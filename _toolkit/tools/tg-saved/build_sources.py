#!/usr/bin/env python3
"""Сборщик слоя 1 для волны инжеста (ступень 2 наряда): отчёты подагентов -> raw/telegram + зеркало.

Живёт в репозитории с 2026-09-18 (решение владельца): раньше скрипт лежал в рабочей выгрузке, вне
версионного контроля, и его дефект — потеря адреса материала — не мог быть ни проверен, ни поймал
канарейкой.

Запуск:
    python3 _toolkit/tools/tg-saved/build_sources.py --batches <каталог> --dry-run
    python3 _toolkit/tools/tg-saved/build_sources.py --batches <каталог> --write

Почему скрипт, а не ручная запись ста тридцатью файлами: шапка источника обязана быть одинаковой,
а тело не переписывается ради машинного хеша: доказательство неизменности хранит git. Скрипт живёт
вместе с Telegram-надстройкой в `_toolkit/tools/tg-saved/`; наряд волны называет его ступенью агента.

Что делает:
  * читает отчёты `report-NN.json` и входные партии `batch-NN.json`, сверяет охват id;
  * шапка — по объявленному локальному шаблону `SCHEMA.local.md` плюс поля Telegram-пайплайна
    (`source_channel`, `source_date`, `source_message_id`, адреса материала, `context_note`/
     `context_source` для пояснений владельца);

  * адреса материала: корень канала (`https://t.me/имя`) не считается адресом материала — на него
    ссылается шапка поста. Один материальный адрес — `source_url`; несколько — `source_links` списком.
    До 2026-09-18 правило считало корень канала ссылкой, поэтому `source_url` почти никогда не писался,
    а адреса гиперссылок («Забираем себе — тут») не попадали в запись нигде: заметка теряла смысл;
  * ворота: если у сообщения есть материальные адреса, которых нет в собранной записи, скрипт
    останавливается и называет их — ни один адрес не теряется молча;
  * тело — ровно текст сообщения; если у записи есть изображение с машинным описанием, к телу
    добавляется раздел «Иллюстрации (машинное описание)» с оговоркой о происхождении;
    сами файлы медиа в репозиторий не копируются (граница записана в `.gitignore`);
  * пишет мастер `raw/telegram/<дата>-<слаг>-<id>.md` и зеркало `wiki/sources/raw/telegram/<то же>`.
"""
import argparse
import datetime
import glob
import json
import os
import re
import sys

TOOLKIT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, TOOLKIT)
import raw_write
import toolkit

ROOT = None
BATCHES = None
MASTER = None
MIRROR = None
TODAY = None
REPORT_KIND = None

# Корень канала: https://t.me/имя — это шапка поста, а не материал, куда ведёт заметка.
# Приглашение в закрытый чат (https://t.me/+hash, /joinchat/...) корнем канала не считается: это адрес.
CHANNEL_ROOT = re.compile(r"^https?://t\.me/(?!\+|joinchat/)[^/]+/?$")

TRANSLIT = {"а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh", "з": "z",
            "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r",
            "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "c", "ч": "ch", "ш": "sh", "щ": "sch",
            "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya"}


def slugify(text, maxlen=60):
    text = (text or "").lower()
    text = "".join(TRANSLIT.get(ch, ch) for ch in text)
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return (text[:maxlen].rstrip("-")) or ""


def q(value, limit=None):
    """Значение YAML в кавычках: экранируем кавычки и переносы, шапку не ломаем.

    Кавычки внутри значения экранируются, а не заменяются на апострофы: пояснение владельца — его
    собственные слова, и переписывать их «похожими» знаками значит менять цитату.
    """
    s = str(value).strip()
    if limit and len(s) > limit:
        s = s[:limit].rsplit(" ", 1)[0].rstrip(" ,;:—-") + " …"
    s = s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "")
    return '"' + s + '"'


def short(value, limit=200):
    """Однострочное описание: обрезаем по границе слова, а не посередине, и помечаем обрез."""
    s = re.sub(r"\s+", " ", str(value)).strip()
    if len(s) <= limit:
        return s
    return s[:limit].rsplit(" ", 1)[0].rstrip(" ,;:—-") + " …"


def media_files(rec):
    return [x for x in (rec.get("media") or []) if x and "File not included" not in x]


def media_block(rec):
    """Раздел с машинным описанием: изображение цитируемо, байты — нет."""
    desc = (rec.get("media_description") or "").strip()
    vis = (rec.get("media_visible_text") or "").strip()
    files = media_files(rec)
    lines = ["## Иллюстрации (машинное описание)", "",
             "> Описание получено машинно (зрение), это не слова автора. Файл-оригинал остаётся",
             "> в рабочей выгрузке и в репозиторий не публикуется (граница записана в `.gitignore`).", ""]
    if not files:
        return "\n".join(lines + ["Вложение к записи не выгружено (в выгрузке Telegram снят чекбокс медиа):",
                                  "содержимого нет, есть только тип вложения.", ""])
    for f in files:
        lines += [f"### {os.path.basename(f)}", "", desc or "Машинного описания нет.", ""]
        if vis:
            lines += ["Текст с изображения: " + vis, ""]
    return "\n".join(lines)


def configure(root, batches, raw_corpus):
    global ROOT, BATCHES, MASTER, MIRROR, TODAY
    ROOT = os.path.abspath(root)
    BATCHES = os.path.abspath(os.path.join(ROOT, batches) if not os.path.isabs(batches) else batches)
    MASTER = toolkit.raw(ROOT, raw_corpus)
    MIRROR = toolkit.wiki(ROOT, "sources", "raw", raw_corpus)
    TODAY = datetime.date.today().isoformat()


def extraction_kind(root):
    import check_contract
    candidates = []
    for kind, spec in check_contract.local_kinds(root).items():
        fields = spec.get("item", {})
        if "source" in fields and "concepts" in fields:
            candidates.append(kind)
    if len(candidates) != 1:
        sys.exit(f"в локальном контракте не найден единственный вид карточки извлечения: {candidates}")
    return candidates[0]


def build(rec, item):
    text = (rec.get("text") or "").replace("\r\n", "\n").strip("\n")
    stem = slugify(item.get("title") or "") or slugify(text.split("\n")[0][:80]) or "media"
    name = f"{rec['day']}-{stem}-{rec['id']}.md"
    body = text + "\n"
    if rec.get("media"):
        body += "\n" + media_block(rec) + "\n"
    if not text:
        body = "(Текст сообщения пуст.)\n\n" + body.lstrip("\n")
    tags = [t for t in (item.get("tags") or []) if isinstance(t, str)]
    head = ["---", f"title: {q(item.get('title') or text[:80] or 'Заметка')}",
            "type: telegram_saved",
            "tags: [" + ", ".join(tags) + "]",
            f"summary: {q(short(item.get('summary') or text[:180]))}",
             "status: active",

            f"source_channel: {q(rec['channel'])}",
            f"source_date: {rec['day']}",
            f"source_message_id: {rec['id']}"]
    links = [l for l in (rec.get("links") or []) if l]
    material = [l for l in links if not CHANNEL_ROOT.match(l)]
    if len(material) == 1:
        head.append(f"source_url: {q(material[0])}")
    elif len(material) > 1:
        head.append("source_links: [" + ", ".join(q(u) for u in material) + "]")
    elif len(links) == 1:
        head.append(f"source_url: {q(links[0])}")
    if (rec.get("owner_context") or "").strip():
        head += [f"context_note: {q(rec['owner_context'])}", "context_source: owner"]
    head += [f"created: {rec['day']}", f"ingested: {TODAY}", "---", ""]
    return name, "\n".join(head) + body


def link_problems(rec, content):
    """Материальные адреса сообщения, которых нет в собранной записи."""
    links = [l for l in (rec.get("links") or []) if l]
    material = [l for l in links if not CHANNEL_ROOT.match(l)]
    return [u for u in material if u not in content]


def load():
    global REPORT_KIND
    recs, items = {}, {}
    batch_paths = sorted(glob.glob(os.path.join(BATCHES, "batch-*.json")))
    if not batch_paths:
        sys.exit(f"в каталоге партий нет batch-*.json: {BATCHES}")
    REPORT_KIND = extraction_kind(ROOT)
    for bp in batch_paths:
        suffix = os.path.basename(bp)[len("batch-"):]
        rp = os.path.join(BATCHES, f"report-{suffix}")
        if not os.path.exists(rp):
            sys.exit(f"нет отчёта {rp} для партии {suffix}")
        for r in json.load(open(bp, encoding="utf-8"))["items"]:
            recs[int(r["id"])] = r
        d = json.load(open(rp, encoding="utf-8"))
        if d.get("contract_version") != "1.0" or d.get("kind") != REPORT_KIND:
            sys.exit(f"отчёт {rp} вне контракта (ожидался kind={REPORT_KIND})")
        for it in d["items"]:
            i = int(it["id"])
            if i in items:
                sys.exit(f"id {i} повторяется в отчётах")
            items[i] = it
        if d.get("failures"):
            print(f"! отчёт {suffix}: failures {len(d['failures'])} — смотреть отдельно")
    return recs, items


VOCAB_PARTS = ("tg-vocabulary.local.json",)   # слова экземпляра живут в его области


def load_vocabulary(root=None):
    """Слова экземпляра: имя корпуса, файл отбора, голосовые записи, словарь тем.

    Файл ведёт экземпляр (`.local.` в имени); механизм его не поставляет и не объясняет. Нет файла — словарь
    пуст, и вызывающий сам решает, что делать: молчаливая подстановка чужих слов хуже остановки.
    """
    path = toolkit.area(root or ROOT, *VOCAB_PARTS)
    if not os.path.exists(path):
        return {}
    try:
        return json.load(open(path, encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as ex:
        sys.exit(f"словарь экземпляра не прочитан ({path}): {ex}")


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--write", action="store_true")
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--fix-summary", action="store_true",
                   help="починка обрезки однострочного описания: тело не меняется")
    ap.add_argument("--wiki", default=".")
    ap.add_argument("--batches", help="каталог партий batch/report")
    ap.add_argument("--raw-corpus", default=os.environ.get("WIKI_RAW_CORPUS", "telegram"))
    a = ap.parse_args()

    root = os.path.abspath(a.wiki)
    vocab = load_vocabulary(root)
    corpus = vocab.get("corpus")
    if not corpus:
        sys.exit("нет имени основного корпуса: заполните `corpus` в _staging/tg-vocabulary.local.json")
    batches = a.batches or os.environ.get("WIKI_BATCHES")
    if not batches:
        sys.exit("укажите каталог партий через --batches или WIKI_BATCHES")
    configure(root, batches, a.raw_corpus)
    recs, items = load()
    sel = json.load(open(toolkit.area(ROOT, "ingest-input", "selection",
                                      vocab.get("selection_file") or f"{corpus}-selection.json"), encoding="utf-8"))
    voice = set(vocab.get("voice") or [])
    want = [i for i in sel[corpus] if i not in voice]
    missing = sorted(set(want) - set(items))
    if missing:
        sys.exit(f"охват не сходится: нет разметки для {missing}")

    written, names, lost = [], set(), []
    for i in want:
        name, content = build(recs[i], items[i])
        if name in names:
            sys.exit(f"имя файла повторилось: {name}")
        names.add(name)
        if os.path.exists(os.path.join(MASTER, name)) and not a.fix_summary:
            sys.exit(f"мастер уже есть, raw/ не переписывается: {name}")
        for u in link_problems(recs[i], content):
            lost.append(f"{name}: адрес {u} есть в выгрузке, но не в записи")
        written.append((name, content))

    if lost:
        print("АДРЕСА МАТЕРИАЛОВ НЕ ПОПАЛИ В ЗАПИСИ — " + str(len(lost)))
        for x in lost:
            print("  " + x)
        sys.exit("ворота адресов: сборка остановлена, ни один файл не записан")
    dom = {}
    for i in want:
        k = items[i].get("domain", "?")
        dom[k] = dom.get(k, 0) + 1
    print(f"к публикации: {len(written)} источников (голосовых не выкладываем: {len(voice)})")
    print("домен:", dom)
    print(f"с изображением: {sum(1 for i in want if recs[i].get('media'))}; "
          f"с пояснением владельца: {sum(1 for i in want if (recs[i].get('owner_context') or '').strip())}")
    print("\nпример шапки:\n" + written[0][1][:600])
    if a.fix_summary:
        plans, issues = [], []
        for name, content in written:
            m_new = re.match(r"^---\n(.*?)\n---\n", content, re.DOTALL)
            for base in (MASTER, MIRROR):
                p = os.path.join(base, name)
                old = open(p, encoding="utf-8", newline="").read()
                m_old = re.match(r"^---\n(.*?)\n---\n", old, re.DOTALL)
                if not m_old:
                    issues.append(f"нет шапки: {p}")
                    continue
                if old[m_old.end():] != content[m_new.end():]:
                    issues.append(f"тело расходится — правка raw/ запрещена: {p}")
                    continue
                raw_write.plan(p, content, False, "", "починка summary", plans, issues, allow_existing=True)
        for issue in issues:
            print(issue)
        if issues:
            return 1
        changed = raw_write.apply(plans)
        print(f"\nпочинено шапок (описание обрезано по границе слова): {changed}; "
              f"тела сверены и не менялись")
        return 0
    if a.dry_run:
        print("\nрежим dry-run: ни один файл не записан")
        return
    os.makedirs(MASTER, exist_ok=True)
    os.makedirs(MIRROR, exist_ok=True)
    for name, content in written:
        for base in (MASTER, MIRROR):
            with open(os.path.join(base, name), "w", encoding="utf-8", newline="\n") as f:
                f.write(content)
    print(f"\nзаписано: {len(written)} источников в raw/telegram и столько же в зеркало")
    print("дальше: python3 _toolkit/tasks.py lint")


if __name__ == "__main__":
    raise SystemExit(main())
