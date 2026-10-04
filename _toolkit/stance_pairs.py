#!/usr/bin/env python3
"""Пары «источник волны × страница» для шага «противоречия».

Зачем. Первая ретроспектива волны 2026-09-14 сравнивала источник только со страницей,
куда его вшили, — и владелец указал на дыру: расхождение может быть между источником и
ДРУГОЙ страницей, куда его не вшивали. Например, заметка «MCP — лишний слой» вшита в реестр
инструментов, а спорна она для страницы про инструменты и для страницы про MCP.

Как строятся пары (критерий воспроизводимый, а не «на глаз»):

1. у источника есть карточка извлечения; её структурные поля (lineage_tools, концепты,
   сущности) дают термины источника;
2. страница считается ЗАТРОНУТОЙ, если термин источника стоит в её тезисных местах:
   заголовке любого уровня либо в разделах «Спорное…», «Факты и цифры», «Числа и замеры»;
   простое упоминание в прозе не считается — иначе пар становится восемьсот и половина из них шум;
3. страница-хозяин (куда источник уже вшит) в пары не попадает: по ней спрашивает обычный шаг.

Использование:

    python3 _toolkit/stance_pairs.py --wave 2026-09-14 --write   # записать пары волны
    python3 _toolkit/stance_pairs.py --wave 2026-09-14           # показать, что получится
"""
import argparse
import collections
import glob
import io
import json
import os
import re
import sys

import toolkit

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import candidates as C  # noqa: E402  — переиспользуем разбор карточек и страниц

THESIS_SECTIONS = re.compile(r"(?ms)^## (?:Спорное[^\n]*|Факты и цифры|Числа и замеры)\s*$(.*?)(?=^## |\Z)")
MIN_TERM = 4
STOP_TERMS = {"json", "cli", "api", "llm", "sdk", "ide", "ui"}


def read(path):
    with io.open(path, encoding="utf-8", errors="ignore") as f:
        return f.read()


def thesis_text(text):
    """Заголовки страницы + её тезисные разделы: там живут утверждения, с которыми можно спорить."""
    heads = "\n".join(l for l in text.split("\n") if l.startswith("#"))
    return heads + "\n" + "\n".join(m.group(1) for m in THESIS_SECTIONS.finditer(text))


def wave_sources(root, wave):
    """Источники волны и их страницы-хозяева: из артефакта вердиктов, иначе из записи волны."""
    v_path = toolkit.area(root, "stance", "wave-%s-verdicts.json" % wave)
    host = collections.defaultdict(set)
    if os.path.exists(v_path):
        for item in json.load(open(v_path, encoding="utf-8")).get("items", []):
            for src in item.get("stance", {}):
                host[src].add(item["slug"])
    else:
        rec = toolkit.area(root, "audit", "ingest-wave-%s.json" % wave)
        if not os.path.exists(rec):
            return host
        for pg in json.load(open(rec, encoding="utf-8")).get("pages", []):
            slug = pg["page"].split("/")[-1]
            for note in pg.get("notes", []):
                for f in glob.glob(toolkit.raw(root, "telegram", "*%s.md" % (note.get("id") or ""))):
                    host["raw/telegram/" + os.path.basename(f)].add(slug)
    return host


def main():
    ap = argparse.ArgumentParser(description="пары «источник волны × чужая страница» для шага «противоречия»")
    ap.add_argument("--wave", required=True)
    ap.add_argument("--wiki", default=".")
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()
    root = os.path.abspath(args.wiki)
    vault = toolkit.wiki(root)

    cterms, _ = C.card_terms(vault)
    term_cards = collections.defaultdict(set)
    for term, cards in cterms.items():
        for card in cards:
            term_cards[card].add(term)
        for sp in [term]:
            pass

    host = wave_sources(root, args.wave)
    pages = C.pages_of(vault)
    thesis = {slug: thesis_text(read(path)) for slug, path in pages.items()}

    pairs = collections.defaultdict(dict)          # страница -> {источник: сколько срабатываний}
    detail = collections.defaultdict(dict)
    for src in sorted(host):
        terms = {t for t in term_cards.get(os.path.basename(src)[:-3], set())
                 if len(t) >= MIN_TERM and t.lower() not in STOP_TERMS}
        if not terms:
            continue
        for slug, text in thesis.items():
            if slug in host[src]:
                continue
            hit = {}
            for t in terms:
                n = len(re.findall(r"(?i)" + re.escape(t), text))
                if n:
                    hit[t] = n
            if hit:
                pairs[slug][src] = sum(hit.values())
                detail[slug][src] = sorted(hit)

    total = sum(len(v) for v in pairs.values())
    print("пар «источник × чужая страница»: " + str(total) + " на " + str(len(pairs)) + " страницах")
    for slug in sorted(pairs, key=lambda s: -len(pairs[s]))[:12]:
        print("   " + slug + ": " + str(len(pairs[slug])) + " источников")
    if args.write:
        out = toolkit.area(root, "stance", "wave-%s-pairs.json" % args.wave)
        json.dump({"wave": args.wave, "built": "stance_pairs.py",
                   "criterion": "термин источника в заголовке или в «Спорном»/«Фактах и цифрах» страницы",
                   "pairs": {slug: sorted(srcs) for slug, srcs in sorted(pairs.items())},
                   "terms": {slug: {src: detail[slug][src] for src in sorted(pairs[slug])}
                             for slug in sorted(pairs)}},
                  io.open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("записано: _staging/stance/wave-%s-pairs.json" % args.wave)
    return 0


if __name__ == "__main__":
    sys.exit(main())
