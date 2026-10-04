#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Блок «Что за ссылкой» в записи корпуса — шаг 4в волны (SCHEMA, «Что за ссылкой»).

Резюме цели каждой ссылки пишутся отдельными файлами в `_staging/link-summaries/<имя записи>.md`,
этот скрипт вписывает блок в конец записи мастера. Авторский текст записи не меняется — блок дописывается;
хеша тела в шапке нет с 2026-09-17, поэтому пересчитывать нечего.

    python3 _toolkit/apply_link_summaries.py --wiki .            # показать, что будет вписано
    python3 _toolkit/apply_link_summaries.py --wiki . --write    # вписать
"""
import argparse
import io
import os
import re
import sys

import raw_write
import toolkit

FM = re.compile(rb"^---\r?\n.*?\r?\n---\r?\n?", re.DOTALL)
HEAD = "## Что за ссылкой"


def read(path, mode="r"):
    if mode == "rb":
        return open(path, "rb").read()
    return io.open(path, encoding="utf-8", newline="").read()


def transform(text, block):
    if HEAD in text:
        match = re.search(r"(?m)^" + re.escape(HEAD) + r"\s*$", text)
        rest = text[match.end():]
        next_heading = re.search(r"(?m)^## ", rest)
        tail = rest[next_heading.start():] if next_heading else ""
        return text[:match.start()] + block.strip() + "\n" + ("\n" + tail.lstrip("\n") if tail else "")
    anchor = "## Где использован"
    if anchor in text:
        return text.replace(anchor, block.strip() + "\n\n" + anchor, 1)
    return text.rstrip("\n") + "\n\n" + block.strip() + "\n"


def plan(root):
    out = []
    src = toolkit.area(root, "link-summaries")
    if not os.path.isdir(src):
        return out
    for fn in sorted(os.listdir(src)):
        if not fn.endswith(".md"):
            continue
        note = toolkit.raw(root, "telegram", fn)
        if not os.path.exists(note):
            out.append((fn, "нет записи в raw/telegram", ""))
            continue
        text = read(note)
        block = read(os.path.join(src, fn)).strip()
        out.append((fn, "вписать", block))
    return out


def build_plans(root, rows, force, why):
    plans, issues = [], []
    for fn, state, block in rows:
        if state != "вписать":
            continue
        targets = [toolkit.raw(root, "telegram", fn)]
        mirror = toolkit.wiki(root, "sources", "raw", "telegram", fn)
        if os.path.exists(mirror):
            targets.append(mirror)
        for target in targets:
            old = read(target)
            new = transform(old, block)
            existing = HEAD in old
            raw_write.plan(target, new, force, why, "ссылочный блок", plans, issues,
                           allow_existing=not existing)
    return plans, issues


def main():
    ap = argparse.ArgumentParser(description="Блок «Что за ссылкой» в записи корпуса (шаг 4в)")
    ap.add_argument("--wiki", default=".")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--why", default="")
    a = ap.parse_args()
    if a.force and not a.why.strip():
        ap.error("--force требует --why")
    root = os.path.abspath(a.wiki)
    rows = plan(root)
    plans, issues = build_plans(root, rows, a.force, a.why)
    for fn, state, _b in rows:
        print("  %-72s %s" % (fn[:72], state))
    for issue in issues:
        print(issue)
    if issues:
        return 1
    if a.write:
        print("вписано блоков: %d" % raw_write.apply(plans))
    else:
        print("подготовлено блоков: %d; запись не выполнялась (нужен --write)" % len(plans))
    return 0


if __name__ == "__main__":
    sys.exit(main())
