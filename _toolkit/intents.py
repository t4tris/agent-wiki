#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Замыслы владельца (`context_note`): у каждого обязано быть решение, а не молчание.

Зачем. Владелец объясняет, зачем взял материал («ответ на вопрос Ильи», «покажите книги по теме»),
и это объяснение лежит в шапке источника. Карточка приводила замысел цитатой, но часто закрывала
материал формальным «понятий нет, сущностей нет, цифр нет» — и по существу вопрос оставался без
ответа: не видно, то ли замысел подтвердился, то ли он отвергнут и почему. Отказ — тоже ответ,
но он должен быть строкой.

Решения живут в `_staging/intent-decisions.tsv`:
    источник | решение (страница / покрыто страницей / отказ) | причина | дата

    python3 _toolkit/intents.py list            # очередь замыслов и их решения
    python3 _toolkit/intents.py check           # замыслы без решения (код 1, если есть)
    python3 _toolkit/intents.py decide <источник> <решение> <причина>
    python3 _toolkit/intents.py collect <выгрузка.json>   # решения из HTML-листа владельца
                                                          # (лист собирает `intents_review.py --write`)
"""
import argparse
import glob
import os
import re
import sys

import toolkit

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fmparse

REGISTRY = "intent-decisions.tsv"
HEADER = "источник\tрешение\tпричина\tдата\n"
DECISIONS = ("страница", "покрыто страницей", "отказ")


def read(path):
    with open(path, encoding="utf-8", errors="ignore") as f:
        return f.read()


def sources_with_intent(root):
    """Источники, у которых владелец объяснил замысел: имя -> текст замысла."""
    out = {}
    for p in sorted(glob.glob(toolkit.raw(root, "**", "*.md"), recursive=True)):
        t = read(p)
        m = re.search(r"(?ms)^context_note:\s*(.+?)(?=^\w|\Z)", t)
        if m:
            out[os.path.basename(p)] = re.sub(r"\s+", " ", m.group(1)).strip().strip('"')
    return out


def declaring_pages(vault):
    """Какие страницы объявляют источник в `sources:`."""
    out = {}
    for d in ("entities", "concepts", "comparisons", "queries"):
        for p in glob.glob(os.path.join(vault, d, "*.md")):
            declared = fmparse.items(read(p), "sources")
            if not declared:
                continue
            for s in declared:
                s = s.strip().lstrip("./")
                if s:
                    out.setdefault(os.path.basename(s), []).append(os.path.basename(p)[:-3])
    return {k: sorted(set(v)) for k, v in out.items()}


def load_registry(root):
    path = toolkit.area(root, REGISTRY)
    rows = {}
    if os.path.exists(path):
        for i, line in enumerate(read(path).split("\n")):
            if not line.strip() or i == 0:
                continue
            parts = line.split("\t")
            if len(parts) >= 3:
                rows[parts[0]] = (parts[1], parts[2], parts[3] if len(parts) > 3 else "")
    return rows


def save_decision(root, source, decision, reason, date):
    path = toolkit.area(root, REGISTRY)
    rows = []
    if os.path.exists(path):
        rows = [l for l in read(path).rstrip("\n").split("\n")[1:] if l.strip()]
    rows = [l for l in rows if l.split("\t")[0] != source]
    rows.append("\t".join([source, decision, reason, date]))
    rows.sort(key=lambda l: l.split("\t")[0])
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(HEADER + "\n".join(rows) + ("\n" if rows else ""))


def queue(root, vault):
    """Список замыслов с решением и без: (источник, замысел, страницы, решение, причина)."""
    reg = load_registry(root)
    decl = declaring_pages(vault)
    out = []
    for name, note in sources_with_intent(root).items():
        d, reason, _date = reg.get(name, ("", "", ""))
        out.append({"source": name, "note": note, "pages": decl.get(name, []),
                    "decision": d, "reason": reason})
    out.sort(key=lambda r: (r["decision"] != "", r["source"]))
    return out


def main():
    ap = argparse.ArgumentParser(description="Замыслы владельца и решения по ним.")
    ap.add_argument("command", choices=["list", "check", "decide", "collect"])
    ap.add_argument("args", nargs="*")
    ap.add_argument("--wiki", default=".")
    ap.add_argument("--date", default="")
    a = ap.parse_args()
    root = os.path.abspath(a.wiki)
    vault = toolkit.wiki(root)
    if a.command == "decide":
        if len(a.args) < 3:
            print("нужно: decide <источник> <%s> <причина>" % "|".join(DECISIONS))
            return 2
        src, dec, reason = a.args[0], a.args[1], " ".join(a.args[2:])
        if dec not in DECISIONS:
            print("решение — одно из:", ", ".join(DECISIONS))
            return 2
        save_decision(root, src, dec, reason, a.date)
        print(f"записано: {src} -> {dec} ({reason})")
        return 0
    if a.command == "collect":
        if not a.args:
            print("нужен путь к выгрузке JSON")
            return 2
        import json as _json
        data = _json.load(open(a.args[0], encoding="utf-8"))
        added, skipped = 0, []
        for i in data.get("items") or []:
            src, dec, reason = i.get("source", ""), i.get("decision", ""), (i.get("reason") or "").strip()
            if dec not in DECISIONS:
                skipped.append((src, f"решение вне набора: {dec!r}"))
                continue
            if dec != "страница" and not reason:
                skipped.append((src, "нет причины"))
                continue
            save_decision(root, src, dec, reason, i.get("date", ""))
            added += 1
        for s, why in skipped:
            print(f"  пропущено: {s} — {why}")
        print(f"принято решений: {added}, пропущено {len(skipped)}")
        return 1 if skipped else 0
    rows = queue(root, vault)
    if a.command == "check":
        hang = [r for r in rows if not r["decision"]]
        for r in hang:
            print(f"замысел без решения: {r['source']} — «{r['note'][:60]}»")
        print(f"замыслов без решения: {len(hang)} из {len(rows)}")
        return 1 if hang else 0
    for r in rows:
        state = f"{r['decision']} ({r['reason']})" if r["decision"] else "БЕЗ РЕШЕНИЯ"
        print(f"{r['source'][:56]:<56} | {state[:60]}")
    print(f"\nвсего замыслов {len(rows)}; без решения {sum(1 for r in rows if not r['decision'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
