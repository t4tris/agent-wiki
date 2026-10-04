#!/usr/bin/env python3
"""Форма таблиц страницы болей. Проверка §48 линтера.

Почему появилась. Разделы 1-7 страницы болей стоят канонической таблицей: четыре колонки
«Боль (симптом и пример) | Первопричина | Решение | Источник», одна боль — одна строка. Волна инжеста
14 сентября завела раздел 8 другой формой — три колонки и по строке на класс боли, где вся группа болей
слита в одну ячейку на 1900 знаков. Ни одно правило в SCHEMA форму не задавало, а сторож охвата (§33)
смотрит только на присутствие источника и такую строку считает закрытой. Проверка сторожит форму:

1. у каждой таблицы страницы ровно четыре канонические колонки, и порядок тот же;
2. ячейка длиннее предела (700 знаков) — признак слитых болей: симптом не отделён от соседей,
   искать по нему нельзя;
3. строк на странице не меньше, чем разных заметок архива в ссылках: каждая заметка обязана иметь свою
   строку хотя бы для одной боли, иначе охват закрыт большой строкой, а не разбором;
4. каждая таблица болей стоит под разделом-категорией из списка `SCHEMA.local.md`: боль обязана попасть
    в категорию тем же шагом, которым она заведена.


    python3 _toolkit/pain_shape.py --wiki .
"""
import argparse
import io
import os
import re
import sys

import toolkit
import schema

COLUMNS = ["Боль (симптом и пример)", "Первопричина", "Решение", "Источник"]
PAGE = os.path.join("comparisons", "pain-points-and-fixes.md")
PAGE_DISPLAY = "/".join(("wiki", "comparisons", "pain-points-and-fixes.md"))
# Раздела «Боли заметок владельца архива» больше нет (решение владельца 2026-09-16: расформировать
# и рассортировать боли по категориям), поэтому правило «каждой заметке своя строка» считается по всей
# странице: строк не меньше, чем разных заметок в ссылках.
NOTE_LINK = re.compile(r"\[\[((?:19|20)\d\d-[^\]|]+)\]\]")
CELL_LIMIT = 700


def read(path):
    return io.open(path, encoding="utf-8", errors="replace").read()


def cells_of(line):
    return [c.strip() for c in line.strip().strip("|").split("|")]


def is_separator(line):
    body = line.strip().strip("|")
    return bool(body) and set(body.replace("-", "").replace(":", "").replace(" ", "")) <= set("|")


def issues(root):
    path = toolkit.wiki(root, PAGE)
    if not os.path.exists(path):
        if not _declared(root, "pains"):
            return []
        return ["нет страницы %s — форму болей проверять нечем" % PAGE_DISPLAY]
    categories = schema.list_items(root, "Категории болей")
    if not categories:
        if _declared(root, "pains"):
            return ["в `SCHEMA.local.md` не объявлены категории болей"]
        return []
    lines = read(path).split("\n")
    out = []
    section = ""
    all_notes, total_rows = set(), 0
    # границы таблиц: заголовок — первая строка таблицы после пустой строки; строки данных — до пустой
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("#"):
            section = line.lstrip("# ").strip()
        if not line.startswith("|"):
            i += 1
            continue
        header = cells_of(line)
        rows = []
        j = i + 1
        while j < len(lines) and lines[j].startswith("|"):
            if not is_separator(lines[j]):
                rows.append((j + 1, cells_of(lines[j])))
            j += 1
        if header != COLUMNS:
            out.append("строка %d: колонки таблицы «%s» — а канон требует четыре: %s"
                       % (i + 1, " | ".join(header)[:90], " | ".join(COLUMNS)))
        long_cells = 0
        for ln, cells in rows:
            for k, cell in enumerate(cells):
                if len(cell) > CELL_LIMIT:
                    long_cells += 1
                    if long_cells <= 3:
                        name = header[k] if k < len(header) else ("колонка %d" % (k + 1))
                        out.append("строка %d, колонка «%s»: %d знаков при пределе %d — ячейка слила "
                                   "несколько болей" % (ln, name, len(cell), CELL_LIMIT))
        if long_cells > 3:
            out.append("в таблице на строке %d ещё %d ячеек сверх предела" % (i + 1, long_cells - 3))
        all_notes |= set(NOTE_LINK.findall(" ".join(cell for _, cells in rows for cell in cells)))
        total_rows += len(rows)
        i = j
    # Правило по всей странице: каждой заметке архива — своя строка. Раздел 8 расформирован, боли
    # заметок рассортированы по категориям, поэтому проверка считается на страницу целиком.
    # 4. Таблицы болей стоят под категориями из списка: раздел, которого в списке нет, — это либо
    #    забытая новая категория, либо возврат свалки «боли из источника».
    heads = [(k, l[3:].strip()) for k, l in enumerate(lines) if re.match(r"^## .+", l)]
    for k, title in heads:
        if title in ("Как этим пользоваться", "Связи", "Спорное"):
            continue
        category = re.sub(r"^\d+[.)]\s*", "", title).rstrip(".")
        if category not in {v.rstrip(".") for v in categories}:
            out.append("раздел «%s» (строка %d) не входит в список категорий болей: боль обязана лежать "
                       "в категории, а не в разделе источника" % (title[:50], k + 1))
    if len(all_notes) > total_rows:
        out.append("на странице строк %d, а разных заметок в ссылках %d — заметки слиты в строки, "
                   "каждой нужна своя" % (total_rows, len(all_notes)))
    return out


def main():
    ap = argparse.ArgumentParser(description="Форма таблиц страницы болей (проверка §48)")
    ap.add_argument("--wiki", default=".")
    a = ap.parse_args()
    rows = issues(os.path.abspath(a.wiki))
    for r in rows:
        print(r)
    print("находок: %d" % len(rows))
    return 1 if rows else 0



def _declared(root, key):
    """Объявлен ли этот реестр у экземпляра. Нет объявления домена — проверка не применима, а не «нет файла»:
    у постороннего своя раскладка, и требовать от него наши страницы значит требовать чужих данных."""
    import os as _os
    import sys as _sys
    _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
    import domain
    return key in domain.registers(root)

if __name__ == "__main__":
    sys.exit(main())
