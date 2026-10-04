#!/usr/bin/env python3
"""Тонкие записи корпуса: план удаления собирается машиной, а не руками.

Правило владельца (2026-09-17): запись, у которой полезного текста меньше 200 знаков, объявляется тонкой и
удаляется — после того как её содержание разобрано, и по утверждённому файлу. План этого удаления
(`_staging/delete-thin-notes.tsv`) был собран разово руками, поэтому порог нигде не проверялся: новая тонкая
запись в корпусе просто не попадала в план, и никто об этом не узнал бы. Скрипт собирает план из корпуса:

* тонкая — запись из `raw/telegram/`, у которой полезного текста (тело без шапки и без блока «Что за ссылкой»)
  меньше порога;
* чем заменить ссылку — адрес из тела, если он там один и явный; иначе строка «сообщение id, канал, дата»;
* где содержание живёт — заполняет разбор (`extracts.py`), а не этот скрипт.

    python3 _toolkit/thin_notes.py --wiki .              # печать плана и расхождений с текущим файлом
    python3 _toolkit/thin_notes.py --wiki . --write      # записать план
"""
import argparse
import glob
import io
import os
import re
import sys

import toolkit

THRESHOLD = 200
PLAN = "delete-thin-notes.tsv"
HEADER = ("запись\tканал\tдата\tid\tчем заменить ссылку\tгде содержание уже живёт "
          "(ссылки из вики)\tблок «что за ссылкой»")


def read(path):
    with io.open(path, encoding="utf-8", errors="ignore") as f:
        return f.read()


def payload(text):
    """Полезный текст записи: тело без шапки, без блока «Что за ссылкой» и без медиа-вставок."""
    body = re.sub(r"(?s)^---.*?---", "", text)
    body = re.sub(r"(?ms)^##\s+Что за ссылкой.*?(?=^##\s|\Z)", "", body)
    body = re.sub(r"!\[\[[^\]]+\]\]", "", body)
    body = re.sub(r"(?m)^#{1,6} .*$", "", body)
    return re.sub(r"\s+", " ", body).strip()


def rows(root):
    out = []
    for p in sorted(glob.glob(toolkit.raw(root, "telegram", "*.md"))):
        text = read(p)
        stem = os.path.basename(p)[:-3]
        body = payload(text)
        if len(body) >= THRESHOLD:
            continue
        fm = re.match(r"(?s)^---\n(.*?)\n---", text)
        head = fm.group(1) if fm else ""
        def field(name, head=head):
            m = re.search(r"(?m)^" + name + r":\s*(.+)$", head)
            return m.group(1).strip().strip('"') if m else ""
        channel = field("source_channel") or field("source_author") or "?"
        date = field("source_date") or field("created") or "?"
        ident = field("source_message_id") or (re.search(r"(\d{6})", stem).group(1) if re.search(r"(\d{6})", stem) else "?")
        urls = re.findall(r"https?://[^\s)\]]+", body)
        replace = urls[0] if len(urls) == 1 else f"сообщение {ident}, {channel}, {date}"
        out.append((stem, channel, date, ident, replace))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wiki", default=".")
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()
    root = os.path.abspath(args.wiki)
    old = {}
    plan_path = toolkit.area(root, PLAN)
    if os.path.exists(plan_path):
        for line in read(plan_path).rstrip("\n").split("\n")[1:]:
            f = line.split("\t")
            if f and f[0].strip():
                old[f[0]] = f
    found = rows(root)
    print(f"тонких записей в корпусе (полезного текста < {THRESHOLD} знаков): {len(found)}")
    new = [r for r in found if r[0] not in old]
    gone = [s for s in old if s not in {r[0] for r in found}]
    print(f"  новых, которых нет в плане: {len(new)}")
    for r in new:
        print("   ", r[0], "|", f"знаков {len(payload(read(toolkit.raw(root, 'telegram', r[0] + '.md'))))}")
    print(f"  в плане, но уже не тонких: {len(gone)}")
    for s in gone:
        print("   ", s)
    if args.write:
        lines = [HEADER]
        for stem, channel, date, ident, replace in found:
            prev = old.get(stem, [])
            keep = prev[5] if len(prev) > 5 else ""
            block = prev[6] if len(prev) > 6 else ""
            lines.append("\t".join([stem, channel, date, ident, replace, keep, block]))
        with io.open(plan_path, "w", encoding="utf-8", newline="") as f:
            f.write("\n".join(lines) + "\n")
        print(f"записано: {plan_path}")
    return 1 if (new or gone) and not args.write else 0


if __name__ == "__main__":
    sys.exit(main())
