#!/usr/bin/env python3
"""Приёмка новых страниц перед постановкой в вики.

Требования к тексту страницы — `_toolkit/style.md` (единственный источник правил стиля); здесь проверяется
то, что машина может проверить до постановки.

Страницы-кандидаты лежат в `_staging/new-pages/`. Проверяется то, что машина может проверить
до записи в хранилище: шапка и её допустимые значения, теги из таксономии SCHEMA, объявленные
источники существуют, вики-ссылки ведут на существующие страницы или на мастера в raw/, в ячейках
таблиц нет стрелок, звёздочек и переносов, в ссылках нет расширения `.md`.

    python3 _toolkit/check_new_pages.py --wiki .            # все файлы каталога
    python3 _toolkit/check_new_pages.py --wiki . tracer-bullet
"""
import argparse
import glob
import hashlib
import io
import json
import os
import re
import shutil
import sys
import tempfile

import toolkit

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fmparse
import schema


def head_items(text, key):
    """Поля шапки из фрагмента без разделителей `---` (каталог кандидатов держит шапку отдельно)."""
    return fmparse.items(text if text.lstrip().startswith("---") else "---\n" + text + "\n---\n", key)

PROSE = re.compile(r"(позици|первопричин|статус|решение|что болит|чем расхожден|практики корпуса|вендорские руководства|смысл)", re.IGNORECASE)
REF = re.compile(r"^(файл|ссылка|источник|источники|страница|слаг|slug|id|дата|число|значение|заметок|#|№)$", re.IGNORECASE)
REQ = ["title", "type", "created", "updated", "status", "tags", "sources", "source-stance-default",
       "summary", "confidence", "last-verified", "verification-status", "evidence", "own-analysis"]
TYPES = {"concept", "entity", "comparison", "query", "summary"}
STATUSES = {"active", "draft", "archived"}
CONF_FIELDS = {"high", "medium", "low"}
VSTAT = {"current", "stale", "deprecated"}
EVID = {"practitioner-opinion", "vendor-self-report", "benchmark", "machine-transcript", "mixed"}
TYPE_DIR = {"concept": "concepts", "entity": "entities", "comparison": "comparisons",
            "query": "queries", "summary": "_meta"}


def destination(path, root):
    text = read(path)
    match = re.search(r"(?m)^type:\s*(\S+)", text)
    if not match or match.group(1) not in TYPE_DIR:
        return None
    return toolkit.wiki(root, TYPE_DIR[match.group(1)], os.path.basename(path))


def promote(files, root):
    moves = []
    errors = []
    for source in files:
        target = destination(source, root)
        if not target:
            errors.append("%s: нельзя определить папку назначения" % os.path.basename(source))
        elif os.path.exists(target):
            errors.append("%s: страница уже существует" % os.path.basename(source))
        else:
            moves.append((source, target))
    if errors:
        for error in errors:
            print("  -", error)
        return False
    if not moves:
        print("постановка: нет страниц")
        return False
    temp = tempfile.mkdtemp(prefix=".promote-", dir=toolkit.wiki(root))
    created = []
    try:
        staged = []
        for source, target in moves:
            copy = os.path.join(temp, os.path.basename(source))
            shutil.copy2(source, copy)
            staged.append((copy, target))
        for copy, target in staged:
            shutil.copy2(copy, target)
            created.append(target)
        for source, _target in moves:
            os.remove(source)
    except OSError as exc:
        for target in created:
            try:
                os.remove(target)
            except OSError:
                pass
        print("постановка: сбой: %s" % exc)
        return None
    finally:
        shutil.rmtree(temp, ignore_errors=True)
    print("постановка: страниц %d" % len(created))
    return created


def receipt_rows(files, root):
    rows = []
    for path in files:
        target = destination(path, root)
        if target:
            slug = os.path.relpath(target, toolkit.wiki(root)).replace(os.sep, "/")[:-3]
        else:
            slug = os.path.splitext(os.path.basename(path))[0]
        with open(path, "rb") as stream:
            digest = hashlib.sha256(stream.read()).hexdigest()
        rows.append({"slug": slug, "sha256": digest})
    return sorted(rows, key=lambda row: row["slug"])


def write_receipt(files, root, path):
    target = path if os.path.isabs(path) else os.path.join(root, path)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with io.open(target, "w", encoding="utf-8", newline="\n") as f:
        json.dump({"pages": receipt_rows(files, root)}, f, ensure_ascii=False, indent=1)
        f.write("\n")
    print("квитанция страниц:", os.path.relpath(target, root))


def read(path):
    return io.open(path, encoding="utf-8", errors="replace").read()


def taxonomy(root):
    return schema.taxonomy(root)


def known_targets(root):
    slugs = set()
    for d in ("concepts", "entities", "comparisons", "queries", "_meta"):
        for p in glob.glob(toolkit.wiki(root, d, "*.md")):
            slugs.add(os.path.basename(p)[:-3])
    raws = set()
    for p in glob.glob(toolkit.raw(root, "**", "*.md"), recursive=True):
        raws.add(os.path.basename(p)[:-3])
    # Медиа — такая же законная цель вставки, как страница: картинка публикуется в
    # `wiki/sources/raw/images` (и в папках images корпуса), поэтому `![[файл.jpg]]` со страницы
    # не битая ссылка (тот же разбор в линтере, §41). Без этого приёмка браковала любую страницу
    # с иллюстрацией — найдено 2026-09-17 на странице agents-md.
    images = set()
    for d in glob.glob(toolkit.raw(root, "**", "images"), recursive=True) + \
             glob.glob(toolkit.wiki(root, "sources", "raw", "images")):
        for p in glob.glob(os.path.join(d, "*")):
            if os.path.isfile(p):
                images.add(os.path.basename(p))
    return slugs, raws, images


def check(path, root, tags_ok, slugs, raws, images=None):
    images = images or set()
    name = os.path.basename(path)
    text = read(path)
    out = []
    if not text.startswith("---"):
        return ["%s: нет шапки" % name]
    fm = text.split("---")[1]
    # Шапку разбираем как YAML: склеенные ключи («sources: sources: [...]») выглядят правильно для
    # поиска по регулярке, но ломают разбор и молча теряют поле (найдено 2026-09-16 на странице LangGraph).
    try:
        import yaml
        parsed = yaml.safe_load(fm)
        if not isinstance(parsed, dict):
            out.append("%s: шапка не разбирается как YAML-словарь" % name)
        else:
            for key in REQ:
                if key not in parsed:
                    out.append("%s: YAML-разбор не видит поля %s" % (name, key))
    except ImportError:
        pass
    except yaml.YAMLError as ex:
        out.append("%s: шапка не разбирается как YAML: %s" % (name, str(ex)[:60]))
    for key in REQ:
        if not re.search(r"(?m)^%s:" % key, fm):
            out.append("%s: в шапке нет поля %s" % (name, key))
    m = re.search(r"(?m)^type:\s*(\S+)", fm)
    if m and m.group(1) not in TYPES:
        out.append("%s: type вне набора: %s" % (name, m.group(1)))
    m = re.search(r"(?m)^status:\s*(\S+)", fm)
    if m and m.group(1) not in STATUSES:
        out.append("%s: status вне набора: %s" % (name, m.group(1)))
    m = re.search(r"(?m)^confidence:\s*(\S+)", fm)
    if m and m.group(1) not in CONF_FIELDS:
        out.append("%s: confidence вне набора: %s" % (name, m.group(1)))
    m = re.search(r"(?m)^verification-status:\s*(\S+)", fm)
    if m and m.group(1) not in VSTAT:
        out.append("%s: verification-status вне набора: %s" % (name, m.group(1)))
    m = re.search(r"(?m)^evidence:\s*(\S+)", fm)
    if m and m.group(1) not in EVID:
        out.append("%s: evidence вне набора: %s" % (name, m.group(1)))
    mt = re.search(r"(?m)^tags:\s*\[(.*?)\]", fm)
    if not mt:
        out.append("%s: тег-строка не разобралась" % name)
    else:
        for tag in [t.strip() for t in mt.group(1).split(",") if t.strip()]:
            if tags_ok and tag not in tags_ok:
                out.append("%s: тег вне таксономии: %s" % (name, tag))
        if tags_ok and tag not in tags_ok:
            out.append("%s: тег вне таксономии: %s" % (name, tag))
    srcs = head_items(fm, "sources")
    if not srcs:
        out.append("%s: sources пуст или не в скобках" % name)
    else:
        for src in srcs:
            if not os.path.exists(os.path.join(root, src)):
                out.append("%s: объявленный источник не найден: %s" % (name, src))
    for bang, link in sorted({(m[0], m[1]) for m in
                              re.findall(r"(!?)\[\[([^\]|#]+)(?:\|[^\]]*)?\]\]", text)}):
        link = link.strip()
        if bang:
            # Вставка `![[файл]]` адресует медиа, а не страницу: проверяется наличие файла в images.
            if os.path.basename(link) not in images:
                out.append("%s: вставлен файл, которого нет ни в одной папке images: ![[%s]]" % (name, link))
            continue
        if link.endswith(".md"):
            out.append("%s: расширение .md внутри вики-ссылки: [[%s]]" % (name, link))
        elif link not in slugs and link not in raws:
            out.append("%s: ссылка на несуществующую страницу: [[%s]]" % (name, link))
    head = None
    for line in text.split("\n"):
        if not line.startswith("|"):
            head = None
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
            continue
        if head is None:
            head = cells
            continue
        for i, cell in enumerate(cells):
            col = (head[i] if i < len(head) else "").strip("* ")
            # Ячейка смысловой колонки без точки и короче 200 знаков читается как назывная строка —
            # то же правило, что у §45 линтера (найдено 2026-09-16 на странице hitl).
            if col and not REF.match(col) and PROSE.search(col) and 8 < len(cell) < 200 and not re.search(r"[.!?]", cell):
                out.append("%s: назывная строка в колонке «%s»: %s" % (name, col, cell[:60]))
        for cell in cells:
            if "→" in cell or "->" in cell:
                out.append("%s: стрелка в ячейке таблицы: %s" % (name, cell[:60]))
            if "**" in cell:
                out.append("%s: звёздочки в ячейке таблицы: %s" % (name, cell[:60]))
    body = len([l for l in text.split("\n") if l.strip()])
    if body < 20:
        out.append("%s: страница в %d непустых строк — похоже на заготовку" % (name, body))
    return out


def main():
    ap = argparse.ArgumentParser(description="Приёмка новых страниц перед постановкой в вики")
    ap.add_argument("--wiki", default=".")
    ap.add_argument("--promote", action="store_true", help="после проверки перенести страницы в wiki без перезаписи")
    ap.add_argument("--adopt", action="store_true", help="проверить уже опубликованные страницы и записать квитанцию")
    ap.add_argument("--receipt", default="_staging/accepted-pages.json", help="файл квитанции приёмки")
    ap.add_argument("slugs", nargs="*")
    a = ap.parse_args()
    root = os.path.abspath(a.wiki)
    tags_ok = taxonomy(root)
    slugs, raws, images = known_targets(root)
    files = []
    if a.adopt:
        for slug in a.slugs:
            page = toolkit.wiki(root, (slug[:-3] if slug.endswith(".md") else slug) + ".md")
            if os.path.isfile(page):
                files.append(page)
    else:
        for s in a.slugs:
            files += glob.glob(toolkit.area(root, "new-pages", s + ".md")) or \
                     glob.glob(toolkit.area(root, "new-pages", s))
        if not files:
            files = sorted(glob.glob(toolkit.area(root, "new-pages", "*.md")))
    if a.adopt and len(files) != len(set(s[:-3] if s.endswith(".md") else s for s in a.slugs)):
        print("не все ожидаемые страницы найдены в wiki/")
        return 1
    if not files and (a.promote or a.adopt):
        print("страницы для приёмки не заданы")
        return 1
    bad = []
    for f in files:
        issues = check(f, root, tags_ok, slugs, raws, images)
        print("%-24s %s" % (os.path.basename(f), "ок" if not issues else "%d замечаний" % len(issues)))
        bad += issues
    for b in bad:
        print("  -", b)
    print("страниц: %d, замечаний: %d" % (len(files), len(bad)))
    if bad:
        return 1
    if a.promote:
        created = promote(files, root)
        if not created:
            return 1
        write_receipt(created, root, a.receipt)
    if a.adopt:
        write_receipt(files, root, a.receipt)
    return 0


if __name__ == "__main__":
    sys.exit(main())
