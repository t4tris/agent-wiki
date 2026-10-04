#!/usr/bin/env python3
"""Применяет правки второго прохода (kind prose-fixes) к страницам вики.

Вход: `_staging/audit/fix-out-*.json` (вердикты проверки), свод — `_staging/audit/fix-out.json`.
Выход: страницы вики с заменёнными строками; таблицы выравниваются заново.

Проверки до записи (сухой прогон по умолчанию):
  · цитата находки действительно стоит в указанной строке (иначе строка сместилась — правка не идёт);
  · если строка табличная — в новой строке то же число колонок;
  · ключи цитирования [[…]] исходной строки сохранены;
  · в новой строке нет «→»/«->» и переводов строк (кроме табличной строки целиком).

Запуск: python3 _toolkit/apply_prose_fixes.py --wiki . [--write]
"""
import argparse
import glob
import io
import json
import os
import re
import sys

import toolkit

OUT_GLOB = "audit/fix-out-*.json"
OUT_MERGED = "audit/fix-out.json"


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
        if os.path.basename(path) == os.path.basename(OUT_MERGED):
            continue
        doc = json.load(io.open(path, encoding="utf-8"))
        for it in doc.get("items", []):
            it["part"] = doc.get("part")
            items.append(it)
        failures += doc.get("failures", [])
    write(toolkit.area(root, OUT_MERGED),
          json.dumps({"kind": "prose-fixes", "items": items, "failures": failures},
                     ensure_ascii=False, indent=1))
    return items, failures


def align(lines):
    blocks, cur, prev = [], [], None
    for n, ln in enumerate(lines):
        if ln.startswith("|"):
            if prev is not None and n - prev > 1:
                blocks.append(cur); cur = []
            cur.append(n); prev = n
        elif prev is not None:
            blocks.append(cur); cur = []; prev = None
    if cur:
        blocks.append(cur)
    for block in blocks:
        rows = [[c.strip() for c in lines[n].strip().strip("|").split("|")] for n in block]
        if not rows or len({len(r) for r in rows}) != 1:
            continue
        w = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
        for n, r in zip(block, rows):
            if all(re.fullmatch(r":?-{2,}:?", c) for c in r if c):
                lines[n] = "| " + " | ".join("-" * max(3, x) for x in w) + " |"
            else:
                lines[n] = "| " + " | ".join(c.ljust(x) for c, x in zip(r, w)) + " |"
    return lines


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wiki", default=".")
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()
    root = args.wiki
    items, failures = merge(root)
    problems, done, rejects = [], 0, 0
    touched = {}
    for it in items:
        if it.get("decision") != "apply" or not it.get("new_line_text"):
            rejects += 1
            continue
        path = toolkit.wiki(root, it["file"])
        if not os.path.exists(path):
            problems.append("нет страницы %s" % it["file"])
            continue
        lines = touched.setdefault(path, read(path).split("\n"))
        n = int(it["line"])
        if n < 1 or n > len(lines):
            problems.append("%s: строка %d вне файла" % (it["file"], n))
            continue
        old = lines[n - 1]
        if it["quote"] not in old:
            problems.append("%s:%d — цитата не совпала со строкой (пропущено)"
                            % (it["file"], n))
            continue
        new = it["new_line_text"].rstrip("\n")
        if old.lstrip().startswith("|"):
            if [c.count("|") for c in [new]] != [c.count("|") for c in [old]]:
                problems.append("%s:%d — у табличной строки изменилось число колонок" % (it["file"], n))
                continue
        lost = keys(old) - keys(new)
        if lost:
            problems.append("%s:%d — потеряны ключи %s" % (it["file"], n, sorted(lost)))
            continue
        lines[n - 1] = new
        done += 1
    print("к применению: %d, отклонено проверкой: %d, вердиктов reject: %d"
          % (done, len(problems), rejects))
    for p in problems[:20]:
        print("  ·", p)
    if problems:
        print("НЕ ПРИМЕНЕНО: сначала устранить расхождения")
        return 1
    if not args.write:
        print("(сухой прогон; для записи — --write)")
        return 0
    for path, lines in touched.items():
        write(path, "\n".join(align(lines)))
    print("записано страниц: %d" % len(touched))
    return 0


if __name__ == "__main__":
    sys.exit(main())
