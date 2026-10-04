#!/usr/bin/env python3
"""Приём источников: разложить, поставить шапку, построить зеркало.

Шаги, которые механизм делает сам:

  1. **раскладка.** Файлы из `--from` уезжают в `raw/<корпус>/` — это мастер, архив забранного; задним числом
     он не перезаписывается, и уже принятый источник второй раз не трогается.
  2. **шапка.** Минимальная шапка записи корпуса: `title`, `type`, `status`, `created`, `tags`, `summary`.
     Выжимка и теги помечены как ожидающие: их пишет тот, кто прочитал источник, а раскладчик его не читает.
  3. **зеркало.** `sync_sources.py` кладёт копию для чтения в `wiki/sources/raw/` — источник становится
     частью вики.

Всё принятое сразу идёт в работу: следующий шаг — карточки. Отбор материала — ворота только для
выгруженной базы Telegram: там владелец размечает свой отбор (selection JSON), и волна без него не
начинается (порядок — `_toolkit/ingest-wave.md`). Обычный приём (статьи, файлы, выгрузки из браузера)
ворот не имеет.

Почему это сказано вслух, а не подразумевается: механизм честнее молчаливого «сделал, что мог». После
приёма «источник без карточки» (§64) значит «ещё не написано», а не «ждём отбора»: карточки начинаются
сразу, без решения об отборе.

    python3 _toolkit/ingest.py --corpus <имя корпуса> --from <папка или файлы> [--wiki .]
    python3 _toolkit/ingest.py --corpus <имя> --from <папка> --dry-run     # показать, что будет, и не писать
    python3 _toolkit/ingest.py --selection <квитанция> --from <папка> [--wiki .]
        # строгий приём волны: только перечисленное в квитанции; лишнее и недостающее — отказ
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import raw_write
import toolkit

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE_EXT = (".md", ".txt")
TODAY = datetime.date.today().isoformat()


def sha_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def slugify(name):
    """Имя файла латиницей из заголовка или имени файла: приём не зависит от темы и языка корпуса."""
    base = os.path.splitext(os.path.basename(name))[0]
    tr = {"а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh", "з": "z", "и": "i",
          "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t",
          "у": "u", "ф": "f", "х": "h", "ц": "c", "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "", "ы": "y", "ь": "",
          "э": "e", "ю": "yu", "я": "ya"}
    low = base.lower()
    out = "".join(tr.get(ch, ch) for ch in low)
    out = re.sub(r"[^a-z0-9]+", "-", out).strip("-")
    return out or "source"


def title_of(text, fallback):
    """Заголовок: первая строка-заголовок, иначе имя файла. Читать источник целиком для шапки не нужно."""
    for line in text.splitlines()[:40]:
        if line.startswith("# "):
            return line[2:].strip()
    return fallback


def split_head(text):
    """Своя шапка источника, если она уже есть: `---` сверху с полями `ключ: значение`.

    Корпуса приходят со своей шапкой (у выгрузок — `title` и `source`), и приклеивать нашу поверх чужой значит
    получить запись с двумя шапками, где вторая мертва. Поэтому свою вынимаем, знакомые поля переносим в схему
    вики, остальное тело остаётся нетронутым.
    """
    m = re.match(r"^---\r?\n(.*?)\r?\n---\r?\n?", text, re.DOTALL)
    if not m:
        return {}, text
    head = {}
    for line in m.group(1).splitlines():
        mm = re.match(r"^([A-Za-z_][\w-]*)\s*:\s*(.*)$", line.strip())
        if mm:
            head[mm.group(1).lower()] = mm.group(2).strip().strip('"')
    return head, text[m.end():]


def frontmatter(title, corpus, source_url=""):
    """Минимальная шапка записи корпуса. Выжимка и теги — помечены как ожидающие, а не выдуманы.
    Набор полей сверен с набором типа `article` (§54 линтера), лишнего не пишем."""
    lines = ["---", f'title: "{title}"', "type: article", f"created: {TODAY}", "status: active", "tags: []",
             'summary: "ожидает выжимки: источник разложен, но не прочитан"']
    if source_url:
        lines.append(f'source_url: "{source_url}"')
    return "\n".join(lines) + "\n---\n\n"


def collect(src):
    """Что приносит `--from`: папку обходим по дереву, файлы берём как есть. Только текстовые форматы."""
    if os.path.isfile(src):
        return [src]
    out = []
    for r, dirs, files in os.walk(src):
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        for f in sorted(files):
            if f.lower().endswith(SOURCE_EXT):
                out.append(os.path.join(r, f))
    return out


def main():
    ap = argparse.ArgumentParser(description="Приём источников: раскладка, шапка, зеркало")
    ap.add_argument("--corpus", required=False, help="имя корпуса: папка под raw/ (например, фамилия автора или тема)")
    ap.add_argument("--from", dest="src", required=True, help="папка с источниками или отдельные файлы")
    ap.add_argument("--selection", default="",
                    help="квитанция волны (_staging/selection/<волна>.json): принять строго по списку")
    ap.add_argument("--wiki", default=".", help="корень проекта")
    ap.add_argument("--dry-run", action="store_true", help="показать раскладку и не писать ничего")
    a = ap.parse_args()
    root = os.path.abspath(a.wiki)
    src = os.path.abspath(a.src)

    if not os.path.exists(src):
        sys.exit(f"нет источников по пути {src}")
    files = collect(src)
    if not files:
        sys.exit(f"в {src} нет файлов {', '.join(SOURCE_EXT)} — приём нечего раскладывать")

    receipt = None
    if a.selection:
        # Строгий приём: вход сверяется с квитанцией до раскладки. Лишнее во входе и недостающее
        # из списка — отказ: иначе отбор расширяется молча, без решения владельца.
        # Квитанция — только из открытой волны: рукописная в обход begin не принимается.
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import wave_runner as _wr
        receipt_abs = os.path.abspath(a.selection)
        problem = _wr.check_receipt_open(root, receipt_abs)
        if problem:
            sys.exit(problem)
        try:
            with open(receipt_abs, encoding="utf-8") as f:
                receipt = json.load(f)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            sys.exit(f"квитанция не читается: {exc}")
        inbox_ok = os.path.abspath(os.path.join(root, receipt.get("inbox", "\0")))
        if inbox_ok != src:
            sys.exit(f"приём идёт не из входа квитанции: ждали {inbox_ok}, дали {src}")
        listed = {s.get("id") for s in receipt.get("sources", []) if isinstance(s, dict) and s.get("id")}
        have = {os.path.relpath(f, root).replace("\\", "/") for f in files}
        extra = sorted(have - listed)
        missing = sorted(listed - have)
        if extra:
            sys.exit("во входе лишнее сверх квитанции (%d): %s" % (len(extra), ", ".join(extra[:8])))
        if missing:
            sys.exit("из квитанции нет во входе (%d): %s" % (len(missing), ", ".join(missing[:8])))
        for s in receipt.get("sources", []):
            path = os.path.join(root, *str(s.get("id", "")).split("/"))
            if os.path.isfile(path) and sha_file(path) != s.get("sha256"):
                sys.exit("источник изменён после begin: %s" % s.get("id"))
        a.corpus = receipt.get("corpus") or a.corpus
        print(f"квитанция {os.path.basename(a.selection)}: вход сошёлся ({len(listed)} источников)")
    if not a.corpus:
        sys.exit("нужен --corpus (без --selection имя корпуса взять негде)")

    dest = toolkit.raw(root, a.corpus)
    print(f"корпус: {a.corpus} | источников к приёму: {len(files)} → raw/{a.corpus}/")
    if a.dry_run:
        for f in files:
            print(f"  [проба] {os.path.basename(f)} → {slugify(f)}.md")
        print("проба: ничего не записано")
        return 0

    os.makedirs(dest, exist_ok=True)
    placed, skipped, plans, issues = [], [], [], []
    for f in files:
        target = os.path.join(dest, slugify(f) + ".md")
        if os.path.exists(target):
            skipped.append(os.path.relpath(target, root).replace("\\", "/"))
            continue
        text = open(f, encoding="utf-8", errors="replace").read()
        own, body = split_head(text)
        # Тело иногда само начинается с `---` (горизонтальная черта выгрузки): в записи это читалось бы как
        # пустая шапка, поэтому ведущие черты и пустые строки снимаем.
        body = re.sub(r"^(?:\s*---\s*\r?\n)+", "", body)
        title = own.get("title") or title_of(body, os.path.splitext(os.path.basename(f))[0])
        src_url = own.get("source_url") or own.get("source") or own.get("url") or ""
        raw_write.plan(target, frontmatter(title, a.corpus, src_url) + body.strip() + "\n",
                       False, "", "новый источник", plans, issues)
    for issue in issues:
        print(issue)
    if issues:
        return 1
    raw_write.apply(plans)
    placed = [os.path.relpath(path, root).replace("\\", "/") for path, _old, _new, _label in plans]

    print(f"шаг 1, раскладка: разложено {len(placed)}, пропущено как уже принятые {len(skipped)}")
    for p in placed:
        print(f"  + {p}")
    for p in skipped:
        print(f"  = {p} (уже в корпусе, мастер задним числом не переписывается)")

    sync = toolkit.script("sync_sources.py")
    if placed and os.path.exists(sync):
        try:
            proc = subprocess.run([sys.executable, "-X", "utf8", sync, "--wiki", root], capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", check=False)
        except OSError as ex:
            print("шаг 2, зеркало: sync_sources.py не запустился: %s" % ex, file=sys.stderr)
            return 1
        tail = [l for l in (proc.stdout or "").splitlines() if l.strip()][-3:]
        print("шаг 2, зеркало: " + ("; ".join(tail) if tail else "без сообщений"))
        if proc.returncode != 0:
            print("шаг 2, зеркало: sync_sources.py завершился с кодом %d" % proc.returncode, file=sys.stderr)
            return 1
    else:
        message = "шаг 2, зеркало: новых источников нет" if not placed else "шаг 2, зеркало: sync_sources.py не найден"
        print(message)
        if placed:
            return 1

    print("")
    print("ДАЛЬШЕ: всё принятое идёт в работу без отбора — следующий шаг карточки.")
    print(f"  принято в корпус: {len(placed) + len(skipped)}, из них новых {len(placed)}")
    print("  Отбор материала решает владелец только при обработке выгруженной базы Telegram")
    print("  (порядок — `_toolkit/ingest-wave.md`); обычный приём ворот отбора не имеет.")
    print("  «Источник без карточки» (§64) после приёма значит «ещё не написано», а не «ждём отбора».")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
