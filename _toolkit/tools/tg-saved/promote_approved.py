#!/usr/bin/env python3
"""Шаг 2: перенести одобренное из review.csv в raw/ вики.

    python3 _toolkit/tools/tg-saved/promote_approved.py --staging <staging> --wiki "<путь к Agentic Wiki>"
    python3 _toolkit/tools/tg-saved/promote_approved.py --staging <staging> --wiki "<путь>" --all

Режим по умолчанию читает review.csv (колонка approve = yes), режим --all берёт всю выгрузку
без review.csv — тематический канал, фильтровать нечего; дальше волна открывается через
begin --collected. В обоих режимах действуют ворота механизма: медиа без расшифровки
и ссылки без резюме не продвигаются. Сообщения берёт из messages.json,
кладёт их в raw/telegram/<дата>-<slug>.md. Медиа публикуются по раскладке SCHEMA: оригинал — в
raw/telegram/images/, копия для показа — в wiki/sources/raw/images/ (Obsidian находит вставку по имени файла).
Файлы в raw/ считаются неизменяемыми: существующие не перезаписываются.
"""
import argparse
import csv
import json
import os
import re
import shutil
import sys
from datetime import date

TOOLKIT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, TOOLKIT)
import toolkit

ROOT = toolkit.root()

TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh",
    "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o",
    "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "c",
    "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu",
    "я": "ya",
}


def slugify(text, maxlen=60):
    text = text.lower()
    text = "".join(TRANSLIT.get(ch, ch) for ch in text)
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return (text[:maxlen].rstrip("-")) or "message"


# Порог режима «взять всё»: больше — остановка до записи (решение владельца 2026-09-26:
# предупредить о последствиях, чтобы был шанс отказаться). Считаются сообщения, которые запуск
# рассматривает (с текстом, медиа или ссылками), на один запуск; режим с отметками идёт поштучно,
# и предупреждать там не о чем. Это не запрет: продолжение доступно всегда, но требует второго слова.
THRESHOLD = 1000


def _considered(m):
    """Сообщение запуск рассматривает: есть текст, медиа или ссылки. Пустое — шум для порога."""
    return bool((m.get("text") or "").strip() or m.get("media_local") or m.get("media")
                or [u for u in (m.get("links") or []) if u.strip()])


def find_transcript(staging, message):
    """Готовая расшифровка медиа сообщения: `<stem>.md` рядом с медиафайлом."""
    media = (message.get("media_local") or []) + [
        os.path.join("media", os.path.basename(x)) for x in (message.get("media") or [])]
    for rel in media:
        stem = os.path.splitext(os.path.basename(rel))[0]
        cand = os.path.join(staging, os.path.dirname(rel), stem + ".md")
        if os.path.isfile(cand):
            text = open(cand, encoding="utf-8").read().strip()
            if text:
                return text
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--staging", required=True)
    ap.add_argument("--wiki", required=True)
    ap.add_argument("--min-chars", type=int, default=0, help="пропускать слишком короткие тексты")
    ap.add_argument("--all", action="store_true",
                    help="взять всю выгрузку без review.csv: тематический канал, фильтровать нечего. "
                         "Ворота механизма (расшифровка медиа, резюме ссылок) действуют и здесь; "
                         "больше 1000 сообщений к разбору — остановка до записи, второе слово --force; "
                         "дальше волна открывается через begin --collected")
    ap.add_argument("--force", action="store_true",
                    help="второе слово после остановки по порогу: продолжить запись большой выгрузки")
    args = ap.parse_args()

    msgs_path = os.path.join(args.staging, "messages.json")
    if not os.path.exists(msgs_path):
        sys.exit(f"Нет файла {msgs_path}")

    with open(msgs_path, encoding="utf-8") as f:
        msgs = {m["id"]: m for m in json.load(f)["messages"]}

    rejected, undecided, missing = 0, 0, 0
    skipped_empty = 0
    if args.all:
        # Режим «весь канал»: отбора нет, решение — взять всё целиком. Кандидат — каждое сообщение;
        # ворота ниже те же, что в режиме отбора: взять всё не значит взять без расшифровок.
        # Пустые без медиа и ссылок — не рассматриваемые: в число порога и в ворота не идут, иначе
        # порог срабатывает на шуме, а отказ называет «ссылку без резюме» там, где ссылки не было.
        candidates = [msgs[k] for k in sorted(msgs)]
        todo = [m for m in candidates if _considered(m)]
        skipped_empty = len(candidates) - len(todo)
    else:
        csv_path = os.path.join(args.staging, "review.csv")
        if not os.path.exists(csv_path):
            sys.exit(f"Нет файла {csv_path}")
        todo = []
        NEGATIVE = ("no", "n", "нет", "0", "-")
        with open(csv_path, encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                mark = (row.get("approve") or "").strip().lower()
                if mark not in ("yes", "y", "да", "1", "+"):
                    # Пустая отметка — строка ещё не разобрана, а не отклонена: молчим, а не считаем отказом.
                    # Явное «нет» — вердикт владельца: считаем вслух, иначе отклонённое неотличимо от неразобранного.
                    if mark in NEGATIVE:
                        rejected += 1
                    else:
                        undecided += 1
                    continue
                m = msgs.get(int(row["id"])) if row.get("id") else None
                if not m:
                    missing += 1
                    continue
                todo.append(m)

    approved, skipped_short, no_transcript = [], 0, []
    for m in todo:
        if len(m["text"]) < args.min_chars:
            skipped_short += 1
            continue
        # Правило владельца: голосовое/фото без текста идёт в корпус только с расшифровкой.
        # Расшифровка — `<stem>.md` рядом с медиа (пишет transcribe_media.py); без неё строка
        # не продвигается, а отказ называет точную команду вместо молчания.
        if not m["text"].strip() and (m.get("media_local") or m.get("media")):
            transcript = find_transcript(args.staging, m)
            if transcript is None:
                media = (m.get("media_local") or []) + [
                    os.path.join("media", os.path.basename(x)) for x in (m.get("media") or [])]
                first = media[0] if media else "?"
                no_transcript.append(
                    "id %s: медиа без расшифровки — сначала python3 _toolkit/tools/tg-saved/"
                    "transcribe_media.py --media %s" % (m["id"], os.path.join(args.staging, first)))
                continue
            # Шапка происхождения остаётся в теле (провенанс машинного чтения), но слаг,
            # заголовок и выжимка считаются по содержательному тексту, а не по комментарию.
            m = dict(m, text=transcript,
                     _clean=re.sub(r"^(?:\s*<!--[^-]*-->\s*\n)+", "", transcript).strip() or transcript)
        # Запись-ссылка без авторского текста получает блок «Что за ссылкой» сразу при создании:
        # резюме цели пишет агент цепочкой webfetch → firecrawl → человек в
        # `_staging/link-summaries/link-<id>.md`; без него строка не продвигается.
        if not m["text"].strip() and not (m.get("media_local") or m.get("media")):
            links = [u for u in (m.get("links") or []) if u.strip()]
            summary_path = toolkit.area(os.path.abspath(args.wiki), "link-summaries",
                                        "link-%s.md" % m["id"])
            if not links or not os.path.isfile(summary_path):
                no_transcript.append(
                    "id %s: ссылка без резюме — сними резюме цели и положи в %s" % (m["id"], summary_path))
                continue
            summary = open(summary_path, encoding="utf-8").read().strip()
            m = dict(m, text="Ссылка: %s" % links[0],
                     _linkblock="## Что за ссылкой\n\n%s\n\nАдрес: %s" % (summary, links[0]))
        approved.append(m)

    raw_dir = toolkit.raw(args.wiki, "telegram")
    # Раскладка SCHEMA: оригинал медиа — в raw/<корпус>/images/, копия для показа — в wiki/sources/raw/images/.
    # Прежний raw/assets/ остался от ранней раскладки: каталог был пуст, а поставляемый SCHEMA его не знает.
    images_dir = os.path.join(raw_dir, "images")
    display_dir = toolkit.wiki(args.wiki, "sources", "raw", "images")

    # Имена считаются до записи: порог обязан знать, сколько страниц будет записано, а прерванный
    # запуск — не оставить половину. Проверка существования — чтение, а не запись.
    planned = []
    for m in approved:
        content = m.get("_clean") or m["text"]
        base = content.split("\n")[0][:80] or "media"
        name = f"{m['day']}-{slugify(base)}-{m['id']}.md"
        planned.append((m, name, os.path.exists(os.path.join(raw_dir, name))))

    if args.all and not args.force:
        # Порог живёт здесь, а не в меню: прямой вызов останавливается так же. Только режим --all:
        # в режиме с отметками решение уже поштучное. Пустые в todo уже отфильтрованы выше, поэтому
        # число к разбору — длина списка. Код 3 — «нужно второе слово», а не код 2
        # («прогону нельзя доверять»).
        considered = len(todo)
        if considered > THRESHOLD:
            will_write = sum(1 for _, _, exists in planned if not exists)
            print("выгрузка больше порога: сообщений к разбору %d (> %d) | будет записано %d | "
                  "встанет без расшифровки %d" % (considered, THRESHOLD, will_write, len(no_transcript)))
            print("ничего не записано. продолжить: python3 _toolkit/tasks.py telegram --all --force")
            return 3

    os.makedirs(raw_dir, exist_ok=True)
    os.makedirs(images_dir, exist_ok=True)
    os.makedirs(display_dir, exist_ok=True)

    written, kept = [], 0
    for m, name, exists in planned:
        path = os.path.join(raw_dir, name)
        if exists:
            kept += 1
            continue
        media_lines = []
        for rel in m.get("media_local", []) + [
            os.path.join("media", os.path.basename(x)) for x in m.get("media", [])
        ]:
            src = os.path.join(args.staging, rel)
            if os.path.exists(src):
                dst_name = os.path.basename(src)
                dst = os.path.join(images_dir, dst_name)
                if not os.path.exists(dst):
                    shutil.copy2(src, dst)
                shown = os.path.join(display_dir, dst_name)
                if not os.path.exists(shown):
                    shutil.copy2(src, shown)
                if f"![[{dst_name}]]" not in media_lines:
                    media_lines.append(f"![[{dst_name}]]")
        body = m["text"] + ("\n\n" + "\n\n".join(media_lines) if media_lines else "")
        if m.get("_linkblock"):
            body = body.rstrip("\n") + "\n\n" + m["_linkblock"] + "\n"
        title = re.sub(r"\s+", " ", content.split("\n")[0]).strip()[:110] or f"Telegram: {m['source']}"
        summary = re.sub(r"\s+", " ", content).strip()[:180]
        fm = "\n".join([
            "---",
            f'title: "{title}"',
            "type: telegram_saved",
            f"tags: [{', '.join(m.get('tags', []))}]",
            f'summary: "{summary}"',
            "status: active",
            "sources: [telegram]",
            f'source_channel: "{m["source"]}"',
            f"source_date: {m['day']}",
            f"source_message_id: {m['id']}",
            f"created: {m['day']}",
            f"ingested: {date.today().isoformat()}",
            "---",
            "",
        ])
        with open(path, "w", encoding="utf-8") as f:
            f.write(fm + body + "\n")
        written.append(name)

    log_path = os.path.join(args.wiki, "log.md")            # журнал событий экземпляра
    if not os.path.exists(log_path):
        log_path = os.path.join(args.wiki, "log.md")
    mode = "all" if args.all else "review"
    csv_rel = None
    if not args.all:
        csv_rel = os.path.relpath(csv_path, os.path.abspath(args.wiki))
    if written:
        with open(log_path, "a", encoding="utf-8") as f:
            what = ("Все сообщения выгрузки (Telegram, режим --all)"
                    if args.all else "Одобренные сохранённые сообщения (Telegram)")
            f.write(f"\n## [{date.today().isoformat()}] ingest | {what}\n")
            f.write(f"- Перенесено в raw/telegram/: {len(written)} файлов\n")
            for n in written:
                f.write(f"  - raw/telegram/{n}\n")
    took = "Взято строк (режим --all)" if args.all else "Одобрено строк"
    print(f"{took}: {len(approved)} | записано: {len(written)} | "
          f"уже было (raw неизменяем): {kept} | слишком коротких: {skipped_short} | "
          f"пустых пропущено: {skipped_empty} | не найдено: {missing} | "
          f"отклонено вердиктом: {rejected} | не разобрано: {undecided} | "
          f"без текста и материала: {len(no_transcript)}")
    for line in no_transcript:
        print("  !", line)
    for n in written[:20]:
        print(f"  + raw/telegram/{n}")
    # Итог — в артефакт аудита с отпечатками входов: число без входа недоказуемо, через месяц
    # прогон не повторить. Ненулевой выход — только «прогону нельзя доверять» (строки ссылаются
    # в никуда); законный исход «владелец всё отклонил» — это ноль, а не поломка.
    import hashlib as _hashlib
    import json as _json
    import datetime as _datetime

    def _sha(path):
        try:
            with open(path, "rb") as stream:
                return _hashlib.sha256(stream.read()).hexdigest()
        except OSError:
            return ""
    audit_dir = toolkit.area(os.path.abspath(args.wiki), "audit")
    os.makedirs(audit_dir, exist_ok=True)
    day = _datetime.date.today().isoformat()
    taken = {f for f in os.listdir(audit_dir) if f.startswith("promote-")}
    num = next(n for n in range(1, 100) if f"promote-{day}-{n:02d}.json" not in taken)
    summary = {"run": f"promote-{day}-{num:02d}.json", "at": _datetime.datetime.now().isoformat(timespec="seconds"),
               "mode": mode,
               "inputs": {"messages": os.path.relpath(msgs_path, os.path.abspath(args.wiki)),
                          "messages_sha256": _sha(msgs_path),
                          "review": csv_rel,
                          "review_sha256": _sha(csv_path) if csv_rel else ""},
               "counters": {"approved": len(approved), "written": len(written), "kept": kept,
                            "skipped_short": skipped_short, "skipped_empty": skipped_empty,
                            "missing": missing,
                            "rejected": rejected, "undecided": undecided,
                            "no_transcript": len(no_transcript)},
               "records": sorted(written),
               "refused": sorted(no_transcript)}
    with open(os.path.join(audit_dir, summary["run"]), "w", encoding="utf-8", newline="") as f:
        _json.dump(summary, f, ensure_ascii=False, indent=1)
        f.write("\n")
    print("сводка прогона:", os.path.relpath(os.path.join(audit_dir, summary["run"]),
                                            os.path.abspath(args.wiki)))
    if missing:
        print("прогону нельзя доверять: строки ссылаются на отсутствующие сообщения")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
