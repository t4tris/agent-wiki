#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Сокращение шапок источников: убрать стихийные поля (шаг 3 плана владельца 2026-09-17).

Что делает (мастера `raw/` и зеркала `wiki/sources/raw/`, тело не трогается):

  * удаляет `sha256` — хеш тела перестал быть полем записи (SCHEMA, «Хеш — свойство собранного пакета»);
  * удаляет `updated` — поле страниц вики, в корпусе его никто не читает;
  * удаляет `sources: [telegram]` — тавтология, тип записи и так это говорит;
  * переводит `source_author` в `authors` (одно понятие — одно имя), сливая со списком, если он есть.

Наборы полей по типу — в SCHEMA («Поля шапки источника»), сторож — §54 линтера.

    python3 _toolkit/slim_frontmatter.py --wiki .            # показать, что изменится
    python3 _toolkit/slim_frontmatter.py --wiki . --write    # изменить
"""
import argparse
import glob
import hashlib
import io
import os
import re
import sys

import raw_write
import toolkit

FM = re.compile(r"^(---\r?\n)(.*?)(\r?\n---)", re.DOTALL)
DROPS = (r"sha256", r"updated")
SOURCE_AUTHOR = re.compile(r"(?m)^source_author:\s*(.*?)\s*$")
TELEGRAM_TAUTOLOGY = re.compile(r"(?m)^sources:\s*\[telegram\]\s*\n")


def read(p):
    return io.open(p, encoding="utf-8", newline="").read()


def body_hash(text):
    raw = text.encode("utf-8")
    m = re.match(rb"^---\r?\n.*?\r?\n---\r?\n?", raw, re.DOTALL)
    return hashlib.sha256(raw[m.end():] if m else raw).hexdigest()


def value_of(fm, key):
    m = re.search(r"(?m)^" + key + r":\s*(.*?)\s*$", fm)
    return m.group(1).strip().strip('"') if m else ""


def to_authors(fm):
    """`source_author` → `authors`, с сохранением уже бывшего списка авторов."""
    m = SOURCE_AUTHOR.search(fm)
    if not m:
        return fm, 0
    who = m.group(1).strip().strip('"')
    fm = SOURCE_AUTHOR.sub("", fm, count=1)
    if not who:
        return fm, 1
    if re.search(r"(?m)^authors:", fm):
        block = re.search(r"(?ms)^authors:\s*\n((?:\s+-.*\n)+)", fm)
        if block:                       # блочный список: дописываем строку
            fm = fm[:block.end(1)] + '  - "%s"\n' % who + fm[block.end(1):]
        else:                           # однострочный: добавляем внутрь скобок
            fm = re.sub(r"(?m)^authors:\s*\[(.*?)\]\s*$",
                        lambda mm: 'authors: [%s, "%s"]' % (mm.group(1).strip(), who), fm, count=1)
    else:
        fm = re.sub(r"(?m)^source_author:.*\n", "", fm)
        fm = fm.rstrip("\r\n") + '\nauthors: ["%s"]' % who
    return fm, 1


def slim(text):
    m = FM.match(text)
    if not m:
        return text, {}
    fm, stats = m.group(2), {}
    before = body_hash(text)
    for key in DROPS:
        # последнее поле шапки идёт без перевода строки перед `---`, поэтому \n необязателен
        n = len(re.findall(r"(?m)^" + key + r":", fm))
        if n:
            fm = re.sub(r"(?m)^" + key + r":[^\n]*\r?\n?", "", fm)
            stats[key] = stats.get(key, 0) + n
    n = len(TELEGRAM_TAUTOLOGY.findall(fm))
    if n:
        fm = TELEGRAM_TAUTOLOGY.sub("", fm)
        stats["sources: [telegram]"] = n
    fm, n = to_authors(fm)
    if n:
        stats["source_author → authors"] = n
    out = m.group(1) + fm + m.group(3) + text[m.end():]
    if body_hash(out) != before:
        raise SystemExit("тело изменилось — этого быть не должно")
    return out, stats


def main():
    ap = argparse.ArgumentParser(description="Сокращение шапок источников (шаг 3 плана)")
    ap.add_argument("--wiki", default=".")
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()
    root = os.path.abspath(a.wiki)
    files = (sorted(glob.glob(toolkit.raw(root, "**", "*.md"), recursive=True)) +
             sorted(glob.glob(toolkit.wiki(root, "sources", "raw", "**", "*.md"), recursive=True)))
    total = {}
    changed = 0
    plans, issues = [], []
    for p in files:
        text = read(p)
        new, stats = slim(text)
        if not stats:
            continue
        changed += 1
        for k, v in stats.items():
            total[k] = total.get(k, 0) + v
        raw_write.plan(p, new, False, "", "сокращение шапки", plans, issues, allow_existing=True)
    for issue in issues:
        print(issue)
    if issues:
        return 1
    for k in sorted(total):
        print("  %-24s %d" % (k, total[k]))
    print("файлов затронуто: %d из %d%s" % (changed, len(files), "" if a.write else " (сухой прогон)"))
    if a.write:
        print("записано файлов: %d" % raw_write.apply(plans))
    return 0


if __name__ == "__main__":
    sys.exit(main())
