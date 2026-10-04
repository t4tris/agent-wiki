#!/usr/bin/env python3
"""Раскладка болей заметок по категориям страницы болей.

Владелец расформировал отдельный раздел «Боли заметок владельца архива» (2026-09-16) и велел
рассортировать все боли по существующим категориям страницы. Этот шаг читает выгрузки волны
(`_staging/pain-rows/part-*.json`) и результат сортировки (`_staging/pain-sort-out-*.json`),
дописывает строки в конец таблицы своей категории и убирает раздел 8 целиком.

    python3 _toolkit/apply_pain_sort.py --wiki .            # сухой прогон
    python3 _toolkit/apply_pain_sort.py --wiki . --write    # записать страницу
"""
import argparse
import glob
import io
import json
import os
import re
import sys

import toolkit

PAGE = os.path.join("comparisons", "pain-points-and-fixes.md")
PAGE_DISPLAY = "/".join(("wiki", "comparisons", "pain-points-and-fixes.md"))
SECTION8 = re.compile(r"(?m)^## 8\. ")
COLUMNS = ["Боль (симптом и пример)", "Первопричина", "Решение", "Источник"]
# Категория, которой не хватило семи: чтение вернуло «НЕТ» на строках про модель данных, состояние
# приложения и природу самого вывода модели. Раздел заводится здесь и называется так же.
NEW_CATEGORY = "8. Модель, данные и состояние"


def read(path):
    return io.open(path, encoding="utf-8", errors="replace").read()


def rows_and_sort(root):
    rows = []
    for path in sorted(glob.glob(toolkit.area(root, "pain-rows", "part-*.json"))):
        data = json.load(io.open(path, encoding="utf-8"))
        for it in data.get("items") or []:
            rows.append(it)
    where = {}
    for path in sorted(glob.glob(toolkit.area(root, "pain-sort-out-*.json"))):
        data = json.load(io.open(path, encoding="utf-8"))
        for it in data.get("items") or []:
            where[int(it["idx"])] = str(it.get("category", ""))
    return rows, where


def render(row):
    return "| %s | %s | %s | [[%s]] |" % (str(row.get("pain", "")).strip(), str(row.get("cause", "")).strip(),
                                          str(row.get("fix", "")).strip(), row.get("stem", ""))


def main():
    ap = argparse.ArgumentParser(description="Раскладка болей заметок по категориям страницы болей")
    ap.add_argument("--wiki", default=".")
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()
    root = os.path.abspath(a.wiki)
    path = toolkit.wiki(root, PAGE)
    text = read(path)
    rows, where = rows_and_sort(root)
    missing = [i for i in range(len(rows)) if i not in where]
    if missing:
        print("нет решения о категории для строк: %s" % missing[:20])
        return 1
    by_cat = {}
    for i, r in enumerate(rows):
        cat = where[i] if where[i] != "НЕТ" else NEW_CATEGORY
        by_cat.setdefault(cat, []).append(r)
    for cat in sorted(by_cat):
        print("  %-40s %d" % (cat, len(by_cat[cat])))
    # раскладка: строки дописываются в конец таблицы своей категории
    lines = text.split("\n")
    heads = [k for k, l in enumerate(lines) if re.match(r"^## \d+\. ", l)]
    heads.append(len(lines))
    inserted = 0
    for si in range(len(heads) - 1):
        start, end = heads[si], heads[si + 1]
        title = lines[start][3:].strip()
        bare = title.split(". ", 1)[-1].strip()
        key = next((c for c in by_cat if c == title or c.split(". ", 1)[-1].strip() == bare), "")
        if not key:
            continue
        last = max(k for k in range(start, end) if lines[k].startswith("|"))
        block = [render(r) for r in by_cat.pop(key)]
        lines = lines[:last + 1] + block + lines[last + 1:]
        inserted += len(block)
        heads = [k for k, l in enumerate(lines) if re.match(r"^## \d+\. ", l)] + [len(lines)]
    if by_cat:
        # Заводим недостающий раздел перед «Как этим пользоваться»: каноническая таблица и строки.
        anchor = next((k for k, l in enumerate(lines) if l.startswith("## Как этим пользоваться")), len(lines))
        head = [c for c in by_cat]
        block = []
        for cat in head:
            width = [max(len(COLUMNS[i]), 0) for i in range(4)]
            block += ["## " + cat, "",
                      "| " + " | ".join(COLUMNS) + " |",
                      "| " + " | ".join("-" * len(COLUMNS[i]) for i in range(4)) + " |"]
            block += [render(r) for r in by_cat[cat]]
            block.append("")
        lines = lines[:anchor] + block + lines[anchor:]
        inserted += sum(len(v) for v in by_cat.values())
        by_cat = {}
    if by_cat:
        print("не разложены строки категорий: %s" % ", ".join(by_cat))
        return 1
    print("разложено строк: %d" % inserted)
    new = "\n".join(lines)
    # раздел 8 убираем целиком
    i = SECTION8.search(new)
    if i:
        j = new.find("\n## ", i.end())
        new = new[:i.start()] + new[j + 1:]
    # обоснование длины и дата
    _lines = new.split("\n")
    while _lines and not _lines[-1].strip():
        _lines.pop()
    total = len(_lines)
    just = ('split-justification: "%d строк: все боли живут на одной странице (решение владельца '
            '2026-09-16), боли заметок архива рассортированы по категориям, а не собраны отдельным '
            'разделом; расщепление разорвало бы поиск по симптому."' % total)
    new = re.sub(r"(?ms)^split-justification:.*?(?=\n[a-z-]+:)", just, new, count=1)
    new = re.sub(r"(?m)^updated:.*$", "updated: 2026-09-16", new, count=1)
    print("строк на странице после раскладки: %d" % total)
    if a.write:
        io.open(path, "w", encoding="utf-8", newline="").write(new)
        print("записано: %s" % PAGE_DISPLAY)
    else:
        print("сухой прогон: страница не тронута")
    return 0


if __name__ == "__main__":
    sys.exit(main())
