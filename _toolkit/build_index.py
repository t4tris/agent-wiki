#!/usr/bin/env python3
"""Вывеска вики: `wiki/index.md` со списком страниц по папкам.

Зачем командой. На свежем экземпляре `wiki/index.md` нет вовсе, и линтер (§3) справедливо говорил «страницы
нет в index.md» — но это не состояние правки, а отсутствие файла, который механизм умеет создать сам.

Чего команда не делает: **не перезаписывает существующую вывеску**. В рабочем экземпляре вывеску ведёт
владелец, у него своя группировка и свой порядок; затирать её генератором значит править за человеком
(решение владельца 2026-09-22 — файл владельца не переписывается).

    python3 _toolkit/build_index.py --wiki .            # создать, если нет
    python3 _toolkit/build_index.py --wiki . --force    # пересобрать (осознанно, затрёт ручную вывеску)
"""
import argparse
import datetime
import os
import sys

import toolkit

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lint_wiki import INDEX_SECTIONS

HEAD = ("# Хранилище: вывеска\n\n"
        "Список страниц по папкам. Файл ведёт владелец вики; генератор создаёт его только там, где его нет.\n")


def pages(vault):
    """Страницы вики по папкам: служебные и зеркала источников в вывеску не идут."""
    skip = ("sources", ".obsidian")
    out = {}
    for r, dirs, files in os.walk(os.path.join(vault)):
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d not in skip)
        rel = os.path.relpath(r, vault).replace("\\", "/")
        if rel == ".":
            continue
        got = sorted(f[:-3] for f in files if f.endswith(".md") and f != "index.md")
        if got:
            out[rel] = got
    return out


def main():
    ap = argparse.ArgumentParser(description="Вывеска вики: создать wiki/index.md, если его нет")
    ap.add_argument("--wiki", default=".")
    ap.add_argument("--force", action="store_true", help="пересобрать существующую вывеску")
    a = ap.parse_args()
    root = os.path.abspath(a.wiki)
    vault = toolkit.wiki(root)
    target = os.path.join(vault, "index.md")
    if os.path.exists(target) and not a.force:
        print("вывеска на месте — не трогаю (пересобрать: --force)")
        return 0
    body = [HEAD]
    found = pages(vault)
    # Итог машинно читаемой строкой: его ждёт §3 линтера — «Total pages: N = concepts A + entities B + ...».
    # Без этой строки вывеска считается нечитаемой, и на свежем экземпляре находка висит зря.
    order = ("concepts", "entities", "comparisons", "queries", "_meta", "Clippings")
    def count(name):
        return len(found.get(name, []))
    total = sum(count(name) for name in order)
    total_line = "Total pages: %d = %s" % (total, " + ".join(f"{n} {count(n)}" for n in order))
    body.append(total_line + "\n")
    body.append(f"> Last updated: {datetime.date.today().isoformat()} | {total_line}\n")
    for folder, names in sorted(found.items()):
        secname = INDEX_SECTIONS.get(folder, folder)
        body.append(f"\n## {secname}\n")
        body.extend(f"- [[{n}]]" for n in names)
    os.makedirs(vault, exist_ok=True)
    with open(target, "w", encoding="utf-8", newline="") as f:
        f.write("\n".join(body).rstrip("\n") + "\n")
    print(f"вывеска собрана: страниц {sum(len(v) for v in pages(vault).values())} в {len(pages(vault))} папках")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
