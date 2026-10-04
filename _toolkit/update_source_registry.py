#!/usr/bin/env python3
"""Обновляет реестр источников: строки корпуса GRACE и счётчики в шапке.

    python3 _toolkit/update_source_registry.py --wiki .

Считает число страниц, которые ссылаются на источник (по вики-ссылкам в телах страниц Layer 2),
дописывает недостающие строки и правит итоговую строку «Источников: N — корпусных X, вендорских Y».
"""
import argparse
import os
import re

import toolkit

GRACE = {
    "grace-framework.md": ("статья", "2025-09-13"),
    "grace-pcam.md": ("статья", "2025-09-11"),
    "grace-contracts.md": ("статья", ""),
    "grace-navigation-graph.md": ("статья", ""),
    "grace-anchors-markup.md": ("статья", ""),
    "grace-self-correction.md": ("статья", ""),
    "grace-autotests-antipattern.md": ("статья", ""),
    "grace-legacy-testing.md": ("статья", ""),
    "grace-swarm-adaptation.md": ("статья", ""),
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wiki", default=".")
    a = ap.parse_args()
    vault = toolkit.wiki(a.wiki)
    reg_path = os.path.join(vault, "_meta", "source-registry.md")

    # ссылки на источники со страниц Layer 2
    refs = {}
    for d in ("concepts", "entities", "comparisons", "queries"):
        p = os.path.join(vault, d)
        if not os.path.isdir(p):
            continue
        for fn in os.listdir(p):
            if not fn.endswith(".md"):
                continue
            t = open(os.path.join(p, fn), encoding="utf-8").read()
            for target in re.findall(r"\[\[([^\]|#]+)", t):
                refs[target.strip().split("/")[-1]] = refs.get(target.strip().split("/")[-1], 0) + 1

    reg = open(reg_path, encoding="utf-8").read()
    added = 0
    rows = []
    for f, (typ, date) in GRACE.items():
        if f"`{f}`" in reg:
            continue
        sha = "—"        # хеша тела в шапке мастера нет с 2026-09-17 (решение владельца)
        pages = refs.get(f[:-3], 0)
        rows.append(f"| `{f}` | {typ} | Владимир Иванов | {date} | 2026-09-13 | `{sha}` | {pages or ''} | practitioner-opinion |")
    if rows:
        # вставляем перед последней пустой строкой таблицы (в конец файла)
        reg = reg.rstrip() + "\n" + "\n".join(rows) + "\n"
        added = len(rows)

    # итоговая строка: считаем по фактическим строкам таблицы
    files = re.findall(r"(?m)^\| `([^`]+)` \|", reg)
    grace_n = sum(1 for f in files if f.startswith("grace-"))
    total = len(files)
    vendor = total - sum(1 for f in files if f.startswith("articles/")) - grace_n
    reg = re.sub(r"(?m)^Источников: \d+ — корпусных \d+, вендорских \d+\.$",
                 f"Источников: {total} — корпусных {grace_n + vendor}, вендорских {total - grace_n - vendor}.",
                 reg)
    reg = re.sub(r"(?m)^updated: .*$", "updated: 2026-09-13", reg, count=1)
    open(reg_path, "w", encoding="utf-8", newline="").write(reg)
    print(f"добавлено строк: {added} | всего источников в реестре: {total} (из них корпус GRACE: {grace_n})")
    print("ссылок на новые источники со страниц:",
          {f[:-3]: refs.get(f[:-3], 0) for f in GRACE if refs.get(f[:-3], 0)})


if __name__ == "__main__":
    main()
