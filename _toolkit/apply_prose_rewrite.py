#!/usr/bin/env python3
"""Применяет переписанные смысловые ячейки страниц (kind prose-rewrite) к вики.

Вход: `_staging/prose/out-bundle-*.json` (отчёты подагентов), свод — `_staging/prose/out.json`.
Выход: правка страниц вики — ячейки заменяются, таблицы выравниваются по новому содержимому.

Проверки до записи (сухой прогон по умолчанию):
  · файл и строка (по тексту первой ячейки) существуют, колонка есть в шапке;
  · ключи цитирования [[…]] исходной ячейки не потеряны;
  · в новом тексте нет «→», «->», markdown-разметки и переводов строк.

Запуск: python3 _toolkit/apply_prose_rewrite.py --wiki . [--write]
"""
import argparse
import glob
import io
import json
import os
import re
import sys

import toolkit

OUT_GLOB = "prose/out-bundle-*.json"
OUT_MERGED = "prose/out.json"


def read(p):
    with io.open(p, encoding="utf-8", newline="") as f:
        return f.read()


def write(p, t):
    with io.open(p, "w", encoding="utf-8", newline="") as f:
        f.write(t)


def keys(t):
    return set(re.findall(r"\[\[([^\]|]+)(?:\|[^\]]*)?\]\]", t))


def merge(root):
    items, failures = [], []
    for path in sorted(glob.glob(toolkit.area(root, OUT_GLOB))):
        doc = json.load(io.open(path, encoding="utf-8"))
        items += doc.get("items", [])
        for f in doc.get("failures", []):
            f["bundle"] = os.path.basename(path)
            failures.append(f)
    manual = toolkit.area(root, "prose", "manual-edits.json")
    if os.path.exists(manual):
        # правки родителя поверх партий (например, уточнённая атрибуция): держим их отдельным файлом,
        # иначе слияние партий затирало бы их — на этом уже один раз споткнулись
        items += json.load(io.open(manual, encoding="utf-8")).get("items", [])
    doc = {"contract_version": "1.0", "kind": "prose-rewrite", "part": 1, "parts": 1,
           "note": "свод партий переписывания смысловых ячеек + правки родителя", "items": items,
           "failures": failures}
    write(toolkit.area(root, OUT_MERGED), json.dumps(doc, ensure_ascii=False, indent=1))
    return doc


def apply_file(path, items):
    """Заменяет ячейки в одном файле. Возвращает (новый текст, проблемы, сколько заменено).

    Индекс строк строится ОДИН раз по исходному тексту и хранит копии ячеек: иначе вторая правка той же
    строки не находилась (её row_key — первая ячейка — уже переписан первой правкой), а правки накапливались
    бы в живом списке. Правки одной строки применяются последовательно к её текущему тексту.
    """
    lines = read(path).split("\n")
    problems, done = [], 0
    index, head = {}, None
    for n, ln in enumerate(lines):
        if ln.startswith("#"):
            head = None
            continue
        if not ln.startswith("|"):
            continue
        cells = [c.strip() for c in ln.strip().strip("|").split("|")]
        if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
            continue
        if head is None:
            head = cells
            continue
        index.setdefault(cells[0], (n, list(head)))
    grouped = {}
    for it in items:
        grouped.setdefault(it["row_key"], []).append(it)
    already = 0
    for row_key, its in grouped.items():
        if row_key not in index:
            # строку могла изменить предыдущая правка (row_key — первая ячейка той же строки). Если новый
            # текст уже стоит в файле — это идемпотентный повтор, а не потеря.
            if all(it["new_text"] in read(path) for it in its):
                already += len(its)
                continue
            problems.append("%s: строка «%s» не найдена" % (os.path.basename(path), row_key[:50]))
            continue
        n, head = index[row_key]
        for it in its:
            cells = [c.strip() for c in lines[n].strip().strip("|").split("|")]
            cols = [c.strip("* ") for c in head]
            col = it["col"].strip("* ")
            if col not in cols:
                problems.append("%s: в строке «%s» нет колонки «%s»"
                                % (os.path.basename(path), row_key[:40], col))
                continue
            i = cols.index(col)
            old = cells[i]
            lost = keys(old) - keys(it["new_text"])
            if lost:
                problems.append("%s: строка «%s», колонка «%s» — потеряны ключи %s"
                                % (os.path.basename(path), row_key[:40], col, sorted(lost)))
                continue
            t = it["new_text"]
            if "→" in t or "->" in t or "\n" in t:
                problems.append("%s: строка «%s», колонка «%s» — запрещённое содержимое (%s)"
                                % (os.path.basename(path), row_key[:40], col, t[:40]))
                continue
            cells[i] = t
            lines[n] = "| " + " | ".join(cells) + " |"
            done += 1
    # выравнивание: непрерывные блоки строк таблиц, ширины по новому содержимому
    blocks, cur = [], []
    prev = None
    for n, ln in enumerate(lines):
        if ln.startswith("|"):
            if prev is not None and n - prev > 1:
                blocks.append(cur)
                cur = []
            cur.append(n)
            prev = n
        elif prev is not None:
            blocks.append(cur)
            cur = []
            prev = None
    if cur:
        blocks.append(cur)
    for block in blocks:
        rows = [[c.strip() for c in lines[n].strip().strip("|").split("|")] for n in block]
        if len({len(r) for r in rows}) != 1 or not rows:
            continue
        w = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
        for n, r in zip(block, rows):
            if all(re.fullmatch(r":?-{2,}:?", c) for c in r if c):
                # разделитель: тире не короче трёх — иначе узкая колонка («#») даёт «--», и Markdown-парсер
                # (а с ним и канарейка №6) таблицу не признаёт
                lines[n] = "| " + " | ".join("-" * max(3, x) for x in w) + " |"
            else:
                lines[n] = "| " + " | ".join(c.ljust(x) for c, x in zip(r, w)) + " |"
    return "\n".join(lines), problems, done + already * 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wiki", default=".")
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()
    root = args.wiki
    doc = merge(root)
    # Один и тот же адрес ячейки может прийти из партии ребёнка и из правок родителя: побеждает
    # последняя запись (manual-edits.json подмешивается после партий), иначе повторная запись
    # «потерянных ключей» валит прогон на устаревшей версии текста.
    dedup = {}
    for it in doc["items"]:
        dedup[(it["file"], it["row_key"], it["col"].strip("* "))] = it
    by_file = {}
    for it in dedup.values():
        by_file.setdefault(it["file"], []).append(it)
    problems, total = [], 0
    new_texts = {}
    for rel, items in sorted(by_file.items()):
        path = toolkit.wiki(root, rel)
        if not os.path.exists(path):
            problems.append("нет страницы wiki/" + rel)
            continue
        text, probs, done = apply_file(path, items)
        problems += probs
        total += done
        new_texts[path] = text
    print("ячеек переписано: %d из %d, файлов: %d, failures от подагентов: %d"
          % (total, len(doc["items"]), len(by_file), len(doc.get("failures", []))))
    for p in problems[:20]:
        print("  ·", p)
    if problems:
        print("НЕ ПРИМЕНЕНО: сначала устранить проблемы")
        return 1
    if not args.write:
        print("(сухой прогон; для записи — --write)")
        return 0
    for path, text in new_texts.items():
        write(path, text)
    print("записано страниц: %d" % len(new_texts))
    return 0


if __name__ == "__main__":
    sys.exit(main())
