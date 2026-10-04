#!/usr/bin/env python3
"""Сборка раздела болей заметок на страницу болей из выгрузок волны.

Читает `_staging/pain-rows/part-*.json` (строки: класс, заметка, боль, первопричина, решение),
проверяет их по канону и собирает раздел «Боли заметок владельца архива» одной таблицей на класс:
четыре канонические колонки, одна боль — одна строка. Форму потом сторожит §48 линтера.

    python3 _toolkit/apply_pain_rows.py --wiki .            # сухой прогон, только отчёт
    python3 _toolkit/apply_pain_rows.py --wiki . --write    # записать страницу

Канон ячейки (то же, что требует §45 линтера): полное предложение, 120-300 знаков, без стрелок,
звёздочек, вертикальной черты и переводов строк.
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
SECTION_FROM = "## 8. Боли заметок владельца архива"
SECTION_TO = "## Как этим пользоваться"
CLASSES = [
    "Контекст, окно и память",
    "Тесты, проверки и техдолг",
    "Навыки и их зрелость",
    "Инструменты, интерфейсы и граф кода",
    "Стоимость, модели и маршрутизация",
    "Автономия, контроль и длинные прогоны",
    "Контракты, спецификации и состояние",
    "Оркестрация и мультиагентность",
    "Прочее: одиночные заметки",
]
COLUMNS = ["Боль (симптом и пример)", "Первопричина", "Решение", "Источник"]
MIN, MAX = 120, 300


def read(path):
    return io.open(path, encoding="utf-8", errors="replace").read()


def rows_of(root):
    out = []
    for path in sorted(glob.glob(toolkit.area(root, "pain-rows", "part-*.json"))):
        data = json.load(io.open(path, encoding="utf-8"))
        for it in data.get("items") or []:
            it["_part"] = data.get("part")
            out.append(it)
    return out


def check(rows, declared):
    """Нарушения канона: их надо починить до записи, иначе страница не пройдёт §45 и §48."""
    bad = []
    seen = set()
    for it in rows:
        stem = it.get("stem", "")
        if not stem:
            bad.append("строка без заметки: %s" % str(it.get("pain"))[:40])
        elif stem not in declared:
            bad.append("%s: заметки нет в sources: страницы" % stem)
        if stem in seen and False:
            pass
        seen.add(stem)
        for field, name in (("pain", "Боль"), ("cause", "Первопричина"), ("fix", "Решение")):
            cell = str(it.get(field) or "")
            if "→" in cell or "->" in cell:
                bad.append("%s · %s: цепочка стрелок" % (stem[:30], name))
            if "**" in cell or "|" in cell or "\n" in cell:
                bad.append("%s · %s: разметка внутри ячейки" % (stem[:30], name))
            if not re.search(r"[.!?]", cell):
                bad.append("%s · %s: нет предложения" % (stem[:30], name))
            if not (MIN <= len(cell) <= MAX):
                bad.append("%s · %s: %d знаков вне коридора %d-%d"
                           % (stem[:30], name, len(cell), MIN, MAX))
    return bad


def render(rows):
    """Таблицы по классам: четыре колонки, выравнивание пробелами, как в остальных разделах страницы."""
    out = []
    known = [c for c in CLASSES if any(r.get("class") == c for r in rows)]
    other = sorted({r.get("class", "") for r in rows} - set(CLASSES))
    for cls in known + other:
        part = [r for r in rows if r.get("class") == cls]
        cells = [(str(r.get("pain", "")).strip(),
                  str(r.get("cause", "")).strip(),
                  str(r.get("fix", "")).strip(),
                  "[[%s]]" % r.get("stem", "")) for r in part]
        width = [max([len(COLUMNS[i])] + [len(c[i]) for c in cells]) for i in range(4)]
        out.append("### %s" % cls)
        out.append("")
        out.append("| " + " | ".join(COLUMNS[i].ljust(width[i]) for i in range(4)) + " |")
        out.append("| " + " | ".join("-" * width[i] for i in range(4)) + " |")
        for c in cells:
            out.append("| " + " | ".join(c[i].ljust(width[i]) for i in range(4)) + " |")
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def main():
    ap = argparse.ArgumentParser(description="Сборка раздела болей заметок на странице болей")
    ap.add_argument("--wiki", default=".")
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()
    root = os.path.abspath(a.wiki)
    path = toolkit.wiki(root, PAGE)
    text = read(path)
    fm = text.split("---")[1] if text.startswith("---") else ""
    declared = set(re.findall(r"raw/telegram/([^\s,\]]+)\.md", fm))
    rows = rows_of(root)
    bad = check(rows, declared)
    stems = sorted({r.get("stem", "") for r in rows})
    print("строк: %d, заметок: %d, классов: %d" % (len(rows), len(stems), len({r.get("class") for r in rows})))
    if bad:
        print("нарушений канона: %d" % len(bad))
        for b in bad[:40]:
            print("  -", b)
        return 1
    body = render(rows)
    i, j = text.find(SECTION_FROM), text.find(SECTION_TO)
    if i < 0 or j < 0 or j < i:
        print("не нашёл границы раздела 8 на странице")
        return 1
    head = ("## 8. Боли заметок владельца архива (май — сентябрь 2026)\n\n"
            "Строки этого раздела — боли из заметок, отобранных владельцем в волне инжеста: одна боль — "
            "одна строка, класс боли стоит заголовком, а не ячейкой. Примеры в первой колонке — дословные "
            "формулировки из заметок (сокращённые по границе слова), не наш пересказ. Все боли живут на "
            "одной странице (решение владельца 2026-09-16), поэтому раздел не расщепляется; форму сторожит "
            "§48 линтера.\n\n")
    new = text[:i] + head + body + "\n" + text[j:]
    # Число строк считается без хвостовых пустых: то же правило, что у §18 линтера.
    _lines = new.split("\n")
    while _lines and not _lines[-1].strip():
        _lines.pop()
    total = len(_lines)
    new = re.sub(r'(?m)^updated:.*$', "updated: 2026-09-16", new, count=1)
    new = re.sub(r'(?m)^last-verified:.*$', "last-verified: 2026-09-16", new, count=1)
    just = ('split-justification: "%d строк: все боли живут на одной странице (решение владельца '
            '2026-09-16: «к чёрту мягкий предел»); расщепление разорвало бы поиск по симптому, ради '
            'которого страница и существует."' % total)
    new = re.sub(r'(?ms)^split-justification:.*?(?=\n[a-z-]+:)', just, new, count=1)
    print("строк на странице после сборки: %d" % total)
    for cls in CLASSES:
        n = len([r for r in rows if r.get("class") == cls])
        if n:
            print("   %-42s %d" % (cls, n))
    if a.write:
        io.open(path, "w", encoding="utf-8", newline="").write(new)
        print("записано: %s" % PAGE_DISPLAY)
    else:
        print("сухой прогон: страница не тронута")
    return 0


if __name__ == "__main__":
    sys.exit(main())
