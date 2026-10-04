#!/usr/bin/env python3
"""Готовит партии для переписывания позиций карты конфликтов полными предложениями.

Зачем: ячейки карты писались телеграфным стилем («Decision Gate >50 инструментов → skills»),
из-за чего половина смысла терялась. Канон языка — SCHEMA.md, раздел «Язык описания расхождений».

Что делает: разбирает страницу карты конфликтов (её путь объявляет экземпляр) на споры (две позиции, статус) и строки
условий применимости, резолвит ключи цитирования в пути файлов и раскладывает споры по партиям
в `_staging/stance/rewrite-batch-N.json` вместе с текстом источников, чтобы ребёнок переписывал
по первоисточникам, а не по сжатой ячейке.

Запуск: python3 _toolkit/stance_rewrite.py --wiki . --batch-size 7 [--write]
"""
import argparse
import io
import json
import os
import re
import sys

import toolkit

MAP_PATH = ""       # путь карты конфликтов объявляет экземпляр: заполняется в main
TABLE_HEAD = "## Таблица конфликтов"   # одна таблица на 8 колонок (владелец, 2026-09-16)
STYLE_SECTION = "Язык описания расхождений"


def cells(line):
    return [c.strip() for c in line.strip().strip("|").split("|")]


def read_rows(lines, head):
    out = []
    started = False
    for line in lines:
        if line.startswith("## "):
            if line.startswith(head):
                started = True
                continue
            if started:
                break
        if not started or not line.startswith("|"):
            continue
        cs = cells(line)
        if not re.match(r"^\**\d+\**$", cs[0]):      # шапка и разделитель таблицы
            continue
        out.append(cs)
    return out


def resolve(key, root):
    """Ключ wikilinks → путь файла. Ноль неразрешённых — проверено на текущей карте."""
    for d in (toolkit.raw(root), toolkit.wiki(root)):
        for base, _dirs, files in os.walk(d):
            for fn in files:
                if fn.endswith(".md") and os.path.splitext(fn)[0] == key:
                    return os.path.relpath(os.path.join(base, fn), root).replace("\\", "/")
    return None


def build(root, batch_size, write=True):
    global MAP_PATH
    MAP_PATH = _domain.register(root, "conflicts", "")
    if not MAP_PATH:
        print("экземпляр не объявил карту конфликтов (`registers` в `_staging/domain.local.tsv`) — разбирать нечего")
        return None
    text = io.open(os.path.join(root, MAP_PATH), encoding="utf-8").read()
    lines = text.split("\n")
    rows = read_rows(lines, TABLE_HEAD)
    items, unresolved = [], []
    for r in rows:
        num = re.sub(r"\D", "", r[0])
        keys = re.findall(r"\[\[([^\]|]+)(?:\|[^\]]*)?\]\]", " ".join(r[2:]))
        paths = []
        for k in keys:
            p = resolve(k, root)
            if p is None:
                unresolved.append(k)
            elif p not in paths:
                paths.append(p)
        # Колонки объединённой таблицы: 0 номер, 1 спор, 2 позиция A, 3 позиция B, 4 статус,
        # 5 когда выигрывает A, 6 когда выигрывает B, 7 чем подтверждено и куда движется.
        items.append({
            "number": int(num),
            "title": r[1],
            "position_a": r[2],
            "position_b": r[3],
            "status": r[4],
            "when_a": r[5] if len(r) > 5 else "",
            "when_b": r[6] if len(r) > 6 else "",
            "trend": r[7] if len(r) > 7 else "",
            "sources": paths,
        })
    if unresolved:
        print("НЕ РАЗРЕШЕНЫ ключи:", sorted(set(unresolved)))
        return None
    batches = [items[i:i + batch_size] for i in range(0, len(items), batch_size)]
    meta = {"style_section": STYLE_SECTION, "map": MAP_PATH, "total": len(items)}
    written = []
    for n, b in enumerate(batches, 1):
        doc = {"contract_version": "1.0", "kind": "dispute-rewrite", "part": n,
               "parts": len(batches), "meta": meta, "items": b}
        path = toolkit.area(root, "stance", "rewrite-batch-%d.json" % n)
        if write:
            io.open(path, "w", encoding="utf-8", newline="").write(
                json.dumps(doc, ensure_ascii=False, indent=1))
        written.append((os.path.relpath(path, root).replace("\\", "/"), len(b)))
    return written, items


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wiki", default=".")
    ap.add_argument("--batch-size", type=int, default=7)
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()
    res = build(args.wiki, args.batch_size, write=args.write)
    if res is None:
        sys.exit(1)
    written, items = res
    print("споров разобрано: %d" % len(items))
    for path, n in written:
        print("  %s — %d споров" % (path, n))
    no_src = [i["number"] for i in items if not i["sources"]]
    if not args.write:
        print("(сухой прогон: файлы партий не перезаписаны)")
    print("споров без источников: %s" % (no_src or "нет"))
    print("источников всего (с повторами): %d" % sum(len(i["sources"]) for i in items))
    if not args.write:
        print("(сухой прогон; для записи — --write)")



import os as _os
import sys as _sys

_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import domain as _domain  # домен экземпляра: имена его страниц и реестров знает только он

if __name__ == "__main__":
    main()
