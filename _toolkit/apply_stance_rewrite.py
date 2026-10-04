#!/usr/bin/env python3
"""Применяет переписанные позиции к карте конфликтов: ячейки заменяются, таблицы выравниваются.

Вход: `_staging/stance/rewrite-out.json` (свод четырёх партий, kind dispute-rewrite).
Выход: правка страницы карты конфликтов (её путь объявляет экземпляр: `registers` в `_staging/domain.local.tsv`).

Что проверяет до записи (сухой прогон по умолчанию):
  · все 28 споров карты получили новую редакцию, лишних номеров нет;
  · ключи цитирования `[[…]]` не потеряны: множество входа ⊆ множество выхода;
  · в текстах нет «→», «->», markdown-разметки и переводов строк;
  · обновляется поле `split-justification` с новым числом строк.

Запуск: python3 _toolkit/apply_stance_rewrite.py --wiki . [--write]
"""
import argparse
import io
import json
import os
import re
import sys

MAP = ""            # путь карты конфликтов объявляет экземпляр: заполняется в main из `registers`
OUT = "_staging/stance/rewrite-out.json"


def read(p):
    with io.open(p, encoding="utf-8", newline="") as f:
        return f.read()


def write(p, t):
    with io.open(p, "w", encoding="utf-8", newline="") as f:
        f.write(t)


def split_row(line):
    parts = line.strip().strip("|").split("|")
    return [c.strip() for c in parts]


def is_data(cells):
    return bool(cells) and bool(re.match(r"^\**\d+\**$", cells[0]))


def render(cells, widths):
    return "| " + " | ".join(c.ljust(w) for c, w in zip(cells, widths)) + " |"


def keys(t):
    return set(re.findall(r"\[\[([^\]|]+)(?:\|[^\]]*)?\]\]", t))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wiki", default=".")
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()
    root = args.wiki
    global MAP
    MAP = _domain.register(root, "conflicts", "")
    if not MAP:
        print("экземпляр не объявил карту конфликтов (`registers` в `_staging/domain.local.tsv`) — править нечего")
        return 0
    outs = {int(i["number"]): i for i in json.load(io.open(os.path.join(root, OUT), encoding="utf-8"))["items"]}
    text = read(os.path.join(root, MAP))
    lines = text.split("\n")

    # Таблица одна, на 8 колонок (владелец объединил две 2026-09-16): 0 номер, 1 спор, 2 позиция A,
    # 3 позиция B, 4 статус, 5 когда выигрывает A, 6 когда выигрывает B, 7 чем подтверждено.
    section = None
    rows, order = {}, []
    for n, ln in enumerate(lines):
        if ln.startswith("## "):
            section = ln[3:].strip()
            continue
        if not ln.startswith("|") or section != "Таблица конфликтов":
            continue
        cells = split_row(ln)
        if any(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
            continue
        if not is_data(cells):
            continue
        num = int(cells[0].strip("*"))
        rows[num] = (n, cells)
        if num not in order:
            order.append(num)
    disputes, conditions = rows, rows

    problems = []
    if sorted(disputes) != sorted(outs):
        problems.append("номера споров не совпадают: карта %s, редакция %s" % (sorted(disputes), sorted(outs)))
    for num, (_, cells) in sorted(disputes.items()):
        o = outs[num]
        old = " ".join(cells[2:])
        new = " ".join(o[k] for k in ("position_a", "position_b", "status"))
        lost = keys(old) - keys(new)
        if lost:
            problems.append("спор №%d: потеряны ключи %s" % (num, sorted(lost)))
        for k in ("position_a", "position_b", "status", "when_a", "when_b"):
            t = o[k]
            if "→" in t or "->" in t or "**" in t or "`" in t or "\n" in t:
                problems.append("спор №%d: поле %s содержит запрещённое (%s)" % (num, k, t[:40]))
    if len(rows) != 28:
        problems.append("в таблице %d строк споров, а не 28" % len(rows))
    if problems:
        print("НЕ ПРИМЕНЕНО:")
        for p in problems:
            print("  ·", p)
        return 1

    # замена: сначала собираем строки, потом считаем ширины по новому содержимому (иначе
    # выравнивание разъезжается: длинные ячейки шире старой шапки)
    merged = {}
    for num, (n, cells) in rows.items():
        o = outs[num]
        merged[n] = [cells[0], cells[1], o["position_a"], o["position_b"], o["status"],
                     o["when_a"], o["when_b"], cells[7] if len(cells) > 7 else ""]
    widths = [max(len(r[i]) for r in merged.values()) for i in range(8)]
    for n, row in merged.items():
        lines[n] = render(row, widths)

    # разделители таблиц не короче трёх тире: узкая колонка («#») иначе даёт «--»
    for _n, _l in enumerate(lines):
        if _l.startswith("|") and all(re.fullmatch(r":?-{2,}:?", c.strip()) for c in _l.strip().strip("|").split("|")):
            lines[_n] = "| " + " | ".join("-" * max(3, len(c.strip())) for c in _l.strip().strip("|").split("|")) + " |"
    new_text = "\n".join(lines)
    added = keys(" ".join(outs[n][k] for n in outs for k in ("position_a", "position_b", "status"))) - \
        keys(text)
    n_lines = len(new_text.split("\n"))
    new_text = re.sub(r'(?m)^split-justification: ".*"$',
                      'split-justification: "%d строк: карта споров — единый инструмент выбора, одна таблица на спор '
                      '(позиции, статус, условия применимости и база) читается по строке; расщепление '
                      'сломало бы проверку §25"' % n_lines, new_text)
    if not args.write:
        print("сухой прогон: строк %d, новых ключей цитирования %s" % (n_lines, sorted(added) or "нет"))
        return 0
    write(os.path.join(root, MAP), new_text)
    print("применено: строк %d, заменено споров %d, новых ключей %s"
          % (n_lines, len(rows), sorted(added) or "нет"))
    return 0



import os as _os
import sys as _sys

_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import domain as _domain  # домен экземпляра: имена его страниц и реестров знает только он

if __name__ == "__main__":
    sys.exit(main())
