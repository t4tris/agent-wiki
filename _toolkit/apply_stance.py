#!/usr/bin/env python3
"""Записывает ось stance в frontmatter страниц из отчётов подагентов (контракт v1: kind=stance).

    python3 _toolkit/apply_stance.py --wiki . --reports <stance-1.json> …

Страницы не перезаписываются целиком: правится только строка `source-stance:` —
она добавляется сразу после `sources:` или заменяется на месте.
Расхождение с `sources:` (лишний или пропущенный источник) печатается как предупреждение.
"""
import argparse
import json
import os
import re
import sys

import toolkit

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fmparse

STANCES = {"supports", "contradicts", "partial"}


def find_page(wiki, slug):
    for d in ("concepts", "entities", "comparisons", "queries"):
        p = toolkit.wiki(wiki, d, slug + ".md")
        if os.path.exists(p):
            return p
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wiki", default=".")
    ap.add_argument("--reports", nargs="+", required=True)
    a = ap.parse_args()

    items = []
    for rep in a.reports:
        data = json.load(open(rep, encoding="utf-8"))
        if data.get("contract_version") != "1.0" or data.get("kind") != "stance":
            sys.exit(f"{rep}: не отчёт kind=stance контракта 1.0")
        items += data["items"]

    written = skipped = 0
    warns = []
    for it in items:
        slug, stance = it["slug"], it["stance"]
        bad = {s: v for s, v in stance.items() if v not in STANCES}
        if bad:
            warns.append(f"{slug}: значения вне набора {bad}")
            continue
        p = find_page(a.wiki, slug)
        if not p:
            warns.append(f"{slug}: страница не найдена")
            continue
        text = open(p, encoding="utf-8").read()
        fm = re.match(r"^---\r?\n(.*?)\r?\n---", text, re.DOTALL)
        if not fm:
            warns.append(f"{slug}: нет frontmatter")
            continue
        declared = fmparse.items(text, "sources")
        miss = [s for s in declared if s not in stance]
        extra = [s for s in stance if s not in declared]
        if miss:
            warns.append(f"{slug}: без позиции остались {miss}")
        if extra:
            warns.append(f"{slug}: позиция для необъявленных {extra}")
        dev = [f"{s}={stance[s]}" for s in declared if s in stance and stance[s] != "supports"]
        line = "source-stance-default: supports\nsource-stance: [" + ", ".join(dev) + "]"
        # идемпотентность: прежнее объявление умолчания снимаем, иначе строк станет две
        text = re.sub(r"(?m)^source-stance-default:.*\n", "", text)
        if re.search(r"(?m)^source-stance:.*$", text):
            text = re.sub(r"(?m)^source-stance:.*$", line.replace("\\", "\\\\"), text, count=1)
        elif fm:
            text = text[:fm.end()] + "\n" + line + text[fm.end():]
        else:
            continue
        open(p, "w", encoding="utf-8", newline="").write(text)
        written += 1
    print(f"страниц со stance записано: {written}, пропущено: {skipped}")
    for w in warns:
        print("  предупреждение:", w)
    sys.exit(1 if warns and written == 0 else 0)


if __name__ == "__main__":
    main()
