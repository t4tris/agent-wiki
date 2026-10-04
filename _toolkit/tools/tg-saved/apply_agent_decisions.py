#!/usr/bin/env python3
"""Применяет решения агента по спорным регионам фьюзинга.

    python3 _toolkit/tools/tg-saved/apply_agent_decisions.py --out <папка фьюзинга> --decisions decisions-1.json decisions-2.json

Решения приходят файлами (агент их пишет, а не возвращает в чат): каждая запись
{"id": 12, "choose": "gigaam|canon|custom", "text": "...", "reason": "..."}.
Скрипт подставляет выбранный текст вместо плейсхолдера ⟦F0NN⟧, дописывает журнал решений
и проверяет, что нерешённых плейсхолдеров не осталось.
"""
import argparse
import json
import os
import re
import sys


def transcript_path(out, name):
    path = os.path.join(out, name + ".md")
    return path if os.path.exists(path) else os.path.join(out, name + ".txt")


def transcript_files(out):
    names = os.listdir(out)
    markdown = {
        os.path.splitext(name)[0]: name
        for name in names
        if name.endswith(".md") and name != "fusion-report.md"
    }
    paths = [os.path.join(out, name) for name in markdown.values()]
    paths.extend(
        os.path.join(out, name)
        for name in names
        if name.endswith(".txt") and os.path.splitext(name)[0] not in markdown
    )
    return sorted(paths)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--decisions", nargs="+", required=True)
    a = ap.parse_args()

    flagged = json.load(open(os.path.join(a.out, "flagged.json"), encoding="utf-8"))
    by_id = {x["id"]: x for x in flagged}
    decisions = {}
    for p in a.decisions:
        if not os.path.exists(p):
            print("нет файла решений:", p)
            continue
        data = json.load(open(p, encoding="utf-8"))
        items = data["items"] if isinstance(data, dict) and "items" in data else data
        for it in items:
            decisions[int(it["id"])] = it
    print(f"спорных регионов: {len(by_id)}, решений получено: {len(decisions)}")

    applied, journal = 0, []
    for fid, it in sorted(decisions.items()):
        x = by_id.get(fid)
        if not x:
            print("неизвестный id:", fid)
            continue
        choose, text = it.get("choose", "canon"), (it.get("text") or "").strip()
        if choose == "gigaam":
            text = x["gigaam"]
        elif choose == "canon":
            text = x["canon"]
        path = transcript_path(a.out, x["file"])
        body = open(path, encoding="utf-8").read()
        ph = f"⟦F{fid:03d}⟧"
        if ph not in body:
            print(f"плейсхолдер {ph} не найден в {x['file']}")
            continue
        body = body.replace(ph, text)
        open(path, "w", encoding="utf-8").write(body)
        applied += 1
        journal.append(f"| {fid} | {x['file'][:32]} | {choose} | {x['gigaam']} ↔ {x['canon']} | {text} | {it.get('reason','')} |")

    rep = os.path.join(a.out, "fusion-report.md")
    if os.path.exists(rep):
        txt = open(rep, encoding="utf-8").read().rstrip()
        txt += ("\n\n## Решения агента по спорным регионам (HITL отсутствует: решает LLM)\n\n"
                "| № | файл | источник выбора | что было у моделей | что в тексте | обоснование |\n"
                "|---|---|---|---|---|---|\n" + "\n".join(journal) + "\n")
        open(rep, "w", encoding="utf-8").write(txt)

    left = 0
    for path in transcript_files(a.out):
        left += len(re.findall(r"⟦F\d{3}⟧", open(path, encoding="utf-8").read()))
    print(f"применено решений: {applied}; нерешённых плейсхолдеров осталось: {left}")
    if left:
        sys.exit(2)


if __name__ == "__main__":
    main()
