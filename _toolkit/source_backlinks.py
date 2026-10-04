#!/usr/bin/env python3
"""Раздел «Где использован» в копии источника внутри хранилища.

Зачем: по копии источника в Obsidian видно, в каких участках статей вики он использован, и это
проверяется человеком глазами (HITL по работе детей над вшиванием). Мастер `raw/` при этом не
трогается: там архив того, что забрано из сети (решение владельца 2026-09-15: сверка зеркал и
затирание мастером отключены, копия внутри вики живёт своей жизнью).

Раздел ПРОИЗВОДНЫЙ: он собирается из страниц (поле `sources:` + ссылки-владельцы), поэтому его
надо пересобирать, а не править руками — при каждом прогоне раздел переписывается целиком.
Участок выводится там, где источник НАЗВАН в тексте раздела (ссылка-владелец); где он объявлен
только основой страницы, честная строка «назван основой страницы».

    python3 _toolkit/source_backlinks.py --write             # пересобрать разделы в копиях (по умолчанию — корпус telegram)
    python3 _toolkit/source_backlinks.py                     # только доложить, что расходится
    python3 _toolkit/source_backlinks.py --scope all --write # по всем источникам Layer 1

Проверка на стороне линтера — раздел 42.
"""
import argparse
import os
import re
import sys

import toolkit

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fmparse

HEADING = "## Где использован"
PAGE_DIRS = ("entities", "concepts", "comparisons", "queries")


def read(path):
    return open(path, encoding="utf-8", errors="ignore").read()


def write(path, text):
    open(path, "w", encoding="utf-8", newline="").write(text)


def norm_source(value):
    """`raw/telegram/x.md` и `telegram/x.md` — один и тот же источник."""
    value = value.replace(os.sep, "/").strip()
    return value[4:] if value.startswith("raw/") else value


def page_index(vault):
    """slug -> (заголовки разделов в порядке появления, объявленные источники)."""
    idx = {}
    for d in PAGE_DIRS:
        full = os.path.join(vault, d)
        if not os.path.isdir(full):
            continue
        for fn in sorted(os.listdir(full)):
            if not fn.endswith(".md"):
                continue
            rel = os.path.join(d, fn)
            text = read(os.path.join(full, fn))
            fm = re.match(r"^---\r?\n(.*?)\r?\n---\r?\n?", text, re.DOTALL)
            body = text[fm.end():] if fm else text
            heads = re.findall(r"(?m)^#{2,3}\s+(.+?)\s*$", body)
            declared = fmparse.items(text, "sources")
            idx[fn[:-3]] = {"rel": rel.replace(os.sep, "/"), "heads": heads, "declared": declared,
                            "body_lines": body.split("\n")}
    return idx


def sections_of(pinfo, slug):
    """Разделы страницы, где источник назван ссылкой-владельцем (в порядке появления)."""
    rx = re.compile(r"\[\[" + re.escape(slug) + r"(?:\|[^\]]*)?\]\]")
    out, cur = [], None
    for line in pinfo["body_lines"]:
        h = re.match(r"^#{2,3}\s+(.+?)\s*$", line)
        if h:
            cur = h.group(1)
        if rx.search(line) and cur and cur not in out:
            out.append(cur)
    return out


def block_for(source_rel, idx):
    slug = os.path.basename(source_rel)[:-3]
    named, base = [], []
    for slug_page, pinfo in sorted(idx.items()):
        if norm_source(source_rel) not in [norm_source(d) for d in pinfo["declared"]]:
            continue
        secs = sections_of(pinfo, slug)
        for sec in secs:
            named.append(f"- [[{slug_page}#{sec}]]")
        if not secs:
            base.append(f"- [[{slug_page}]] — назван основой страницы")
    lines = [HEADING, ""] + named + base
    if not (named or base):
        lines.append("- ни одна страница не объявляет этот источник")
    return "\n".join(lines) + "\n"


def strip_block(text):
    """Текст копии без раздела «Где использован» (хвостовые переводы строк срезаны)."""
    i = text.find(HEADING)
    prefix = text if i < 0 else text[:i]
    return prefix.rstrip("\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wiki", default=".")
    ap.add_argument("--scope", default="telegram", help="корпус внутри raw/ (например telegram) или all")
    ap.add_argument("--write", action="store_true", help="пересобрать разделы в копиях")
    a = ap.parse_args()
    master_root = toolkit.raw(a.wiki)
    mirror_root = toolkit.wiki(a.wiki, "sources", "raw")
    vault = toolkit.wiki(a.wiki)
    if not os.path.isdir(master_root):
        sys.exit(f"нет каталога {master_root}")
    idx = page_index(vault)

    changed, same, missing = [], 0, []
    for base, dirs, files in os.walk(master_root):
        dirs[:] = [d for d in dirs if d not in ("assets", "images")]
        for fn in sorted(files):
            if not fn.endswith(".md"):
                continue
            src = os.path.join(base, fn)
            rel = os.path.relpath(src, master_root)
            if a.scope != "all" and not rel.replace(os.sep, "/").startswith(a.scope + "/"):
                continue
            dst = os.path.join(mirror_root, rel)
            if not os.path.exists(dst):
                missing.append(rel.replace(os.sep, "/"))
                continue
            text = read(dst)
            want = strip_block(text) + "\n\n" + block_for(rel, idx)
            want = want.rstrip("\n") + "\n"
            if want == text:
                same += 1
                continue
            changed.append(rel.replace(os.sep, "/"))
            if a.write:
                write(dst, want)

    verb = "пересобрано" if a.write else "расходится (--write пересоберёт)"
    print(f"копий с разделом «Где использован»: без изменений {same} | {verb}: {len(changed)}")
    for rel in changed:
        print(f"  ~ {rel}")
    if missing:
        print(f"нет копии в хранилище: {len(missing)} (делает sync_sources.py)")
        for rel in missing:
            print(f"  ! {rel}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
