#!/usr/bin/env python3
"""Тематическая карта вики: темы → страницы и кандидаты в страницы по порогу схемы.

Зачем (обещание схемы, пункт долга `topic-map`): при 50+ страницах читателю нужна карта тем,
а вики — сигнал о дырах. Дыра, ради которой карта и заведена: термин, названный многими страницами,
но не получивший своей страницы. Так пропустили AGENTS.md — он набран в 16 страницах и 20 карточках,
а живёт строкой в реестре инструментов, потому что каркас страниц (frame-v1) полки под него не завёл.

Карта — ОТЧЁТ, а не ворота: она ничего не запрещает и ничего не создаёт, она показывает владельцу,
где материал разошёлся по страницам, а страницы под термин нет. Решение о странице принимает человек.

    python3 _toolkit/topic_map.py --wiki .            # показать, что получится
    python3 _toolkit/topic_map.py --wiki . --write     # записать wiki/_meta/topic-map.md

Проверка на стороне линтера: служебная страница `_meta/` (пустой `sources`, не входит в индекс).
"""
import argparse
import collections
import os
import re
import sys

import toolkit

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fmparse
from fmparse import fold

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import align_tables as align  # выравнивание таблиц — один дом: генератор отдаёт ту же форму,
import candidates as cand
import intents as intents_mod  # что пишет `align_tables.py`, иначе генерация и выравнивание спорят

# файлы-конвенции: то, что лежит в проекте как артефакт, а не как источник
ARTIFACT = re.compile(r"\b([A-Z][A-Za-z0-9_\-]*\.(?:md|json|ya?ml|toml))\b")
# Синонимы одного артефакта: у конвенции бывает несколько вендорских имён, и отдельная страница на каждое
# имя — не страница, а дубль. Решение владельца 2026-09-15: «CLAUDE.md это всего лишь вендорский синоним
# AGENTS.md». Ключ — имя файла строчными через дефис, значение — страница, которая описывает конвенцию.
SAME_CONVENTION = cand.SAME_CONVENTION   # единый дом — `candidates.py`, чтобы списки не разъезжались


def canon_tables(text):
    """Выровнять таблицы каноном репозитория (та же функция, что у `align_tables.py`)."""
    new, _changed, _skipped, _structural = align.process(text)
    return new


def read(path):
    return open(path, encoding="utf-8", errors="ignore").read()


def frontmatter(text):
    m = re.match(r"^---\r?\n(.*?)\r?\n---\r?\n?", text, re.DOTALL)
    fm = {}
    if not m:
        return fm, text
    for line in m.group(1).split("\n"):
        if ":" in line and not line.startswith((" ", "\t", "-")):
            k, v = line.split(":", 1)
            fm[k.strip()] = v.strip()
        else:
            fold(fm, line)
    return fm, text[m.end():]


def parse_list(value):
    v = (value or "").strip()
    if v.startswith("[") and v.endswith("]"):
        v = v[1:-1]
    return [x.strip().strip('"').strip("'") for x in v.split(",") if x.strip()]


def pages_of(vault):
    out = {}
    for d in ("entities", "concepts", "comparisons", "queries"):
        full = os.path.join(vault, d)
        if not os.path.isdir(full):
            continue
        for fn in sorted(os.listdir(full)):
            if not fn.endswith(".md"):
                continue
            text = read(os.path.join(full, fn))
            fm, _body = frontmatter(text)
            out[fn[:-3]] = {"dir": d, "tags": parse_list(fm.get("tags")),
                            "summary": fm.get("summary", "").strip('"')}
    return out


def source_basenames(root):
    names = set()
    for base, _dirs, files in os.walk(toolkit.raw(root)):
        for fn in files:
            names.add(fn)
    return names


def convention_synonyms(vault, pages):
    """Имена одного артефакта (вендорские синонимы) — строкой отчёта, а не пропуском."""
    seen = {}
    for slug in pages:
        for d in ("entities", "concepts", "comparisons", "queries"):
            path = os.path.join(vault, d, slug + ".md")
            if not os.path.exists(path):
                continue
            for tok in set(ARTIFACT.findall(read(path))):
                key = cand.norm(tok)
                if key in SAME_CONVENTION:
                    seen.setdefault(key, set()).add(slug)
    out = []
    for key, page in sorted(SAME_CONVENTION.items(), key=lambda kv: -len(seen.get(kv[0], ()))):
        if not seen.get(key):
            continue
        if key in cand.SELF_NAMED:          # страница названа этим же именем: это не синоним, это она и есть
            continue
        name = ".".join(w.upper() if i == 0 else w for i, w in enumerate(key.split(".")))
        out.append(f"- `{name}` — синоним: конвенцию описывает [[{page}]]; назван в {len(seen[key])} страницах")
    return out


def intent_shelved(root, vault, pages):
    """Источники с замыслом владельца (`context_note`), чей ответ — строка реестра, а не страница.

    Ребёнок всегда куда-то приписывает материал, поэтому «никем не объявлен» — слабый признак; сильный —
    материал отобран владельцем с объяснением «зачем», а в вики ему нашлась только строка реестра
    (решение владельца 2026-09-15: «приписал в бессмысленный реестр, потому что не придумал ничего лучше»).
    """
    inside = {}
    for d in ("entities", "concepts", "comparisons", "queries"):
        full = os.path.join(vault, d)
        if not os.path.isdir(full):
            continue
        for fn in sorted(os.listdir(full)):
            if not fn.endswith(".md"):
                continue
            declared = fmparse.items(read(os.path.join(full, fn)), "sources")
            if not declared:
                continue
            for s in declared:
                inside.setdefault(os.path.basename(s), []).append(fn[:-3])
    out = []
    for base, _dirs, files in os.walk(toolkit.raw(root)):
        for fn in sorted(files):
            if not fn.endswith(".md"):
                continue
            text = read(os.path.join(base, fn))
            m = re.search(r'(?m)^context_note:\s*"(.*)"\s*$', text)
            if not (m and m.group(1).strip()):
                continue
            homes = inside.get(fn, [])
            content = [h for h in homes if not h.endswith("-registry")]
            if homes and not content:
                out.append((os.path.relpath(os.path.join(base, fn), root).replace(os.sep, "/"),
                            m.group(1).strip(), sorted(homes)))
    return out


def render(root, vault, pages, shelved):
    # группируем по ПОЛНОМУ тегу: теги курируются схемой, их 35 — это и есть темы карты
    themes = collections.defaultdict(list)
    for slug, info in pages.items():
        for tag in info["tags"]:
            themes[tag].append(slug)
    out = ["# Тематическая карта вики", "",
           "Карта собирается из тегов страниц и из того, где называются термины. Файл генерируется",
           "`_toolkit/topic_map.py`; руками не правится. Служебная страница: пустой `sources`, в индекс",
           "не входит. Это отчёт, а не ворота — решение о новой странице принимает владелец.", "",
           f"Всего страниц: {len(pages)}; тем: {len(themes)}.", "",
           "## Темы и страницы", ""]
    for theme, slugs in sorted(themes.items(), key=lambda x: (-len(x[1]), x[0])):
        out.append(f"### {theme} ({len(slugs)})")
        out.append("")
        out.append("| страница | о чём |")
        out.append("|---|---|")
        for slug in sorted(slugs):
            summary = pages[slug]["summary"] or "—"
            out.append(f"| [[{slug}]] | {summary[:160]} |")
        out.append("")
    out.append("## Кандидаты в страницы по порогу схемы")
    out.append("")
    out.append("Термин, названный структурными полями карточек (`lineage_tools`, «Концепты», «Сущности») не менее чем")
    out.append(f"{cand.THRESHOLD} источниками или не менее чем {cand.PAGES_THRESHOLD} страницами и не получивший своей")
    out.append("страницы. Порог из `SCHEMA.md` — вопрос, а не приказ: «ИЛИ центральна для одного» различает человек,")
    out.append("машина только считает и не даёт забыть. Решение живёт в очереди `_staging/candidate-decisions.tsv`:")
    out.append("страница или отказ с причиной («мимолётное упоминание», «материала мало», «центральность не подтверждена»);")
    out.append("отказ закрывает кандидата — он больше не всплывает ни здесь, ни в проверке линтера.")
    out.append("")
    synonyms = convention_synonyms(vault, pages)
    if synonyms:
        out.append("Синонимы одного артефакта — отдельной страницы не требуют, конвенция описывается указанной страницей:")
        out.append("")
        out.extend(synonyms)
        out.append("")
    cands = cand.candidates(vault, root)
    live = [c for c in cands if not c["decision"]]
    closed = [c for c in cands if c["decision"]]
    if live:
        out.append("| термин | источников | страниц | написания | ближайшая страница | решение |")
        out.append("|---|---|---|---|---|---|")
        for c in live:
            spell = ", ".join(c["spellings"]) if len(c["spellings"]) > 1 else "—"
            out.append(f"| {c['name']} | {c['sources']} | {c['pages_mention']} | {spell[:70]} | "
                       + (f"[[{c['near']}]]" if c["near"] else "—") + " | нет решения |")
        out.append("")
        out.append(f"Висящих кандидатов: {len(live)}. У каждого обязано быть решение: страница или строка отказа с причиной.")
        out.append("")
    else:
        out.append("Пусто: у каждого кандидата порога есть решение — страница или отказ с причиной.")
        out.append("")
    if closed:
        pages_n = sum(1 for c in closed if c["decision"] == "страница")
        refusals = collections.Counter(c["reason"] for c in closed if c["decision"] == "отказ")
        parts = ", ".join(f"{k} — {v}" for k, v in sorted(refusals.items(), key=lambda kv: -kv[1]))
        out.append(f"Закрыто решением: {len(closed)} (страницами {pages_n}, отказами {len(closed) - pages_n}"
                   + (f": {parts}" if parts else "") + "). Отказ — тоже решение: кандидат закрыт и не всплывает.")
        out.append("")
    # Решения по замыслам владельца: страница, «покрыто страницей» или отказ с причиной (`_toolkit/intents.py`).
    # Молчание карточки («понятий нет, сущностей нет») ответом не считается — решает человек или агент,
    # но строка обязана быть.
    q = intents_mod.queue(root, vault)
    if q:
        from collections import Counter as _C
        cnt = _C(r["decision"] or "без решения" for r in q)
        parts = ", ".join(f"{k} — {v}" for k, v in sorted(cnt.items(), key=lambda kv: -kv[1]))
        out.append("## Замысел владельца: решения")
        out.append("")
        out.append(f"Источников с замыслом владельца — {len(q)}: {parts}. Решение по замыслу — это ответ на вопрос")
        out.append("«зачем материал взят»: страница, отказ с причиной или «покрыто страницей» (материал разобран")
        out.append("существующей страницей). Очередь — `_staging/intent-decisions.tsv`, проверка — `_toolkit/intents.py check`.")
        out.append("")
        hang = [r for r in q if not r["decision"]]
        if hang:
            out.append("| источник | замысел | без решения |")
            out.append("|---|---|---|")
            for r in hang:
                note = re.sub(r"\s+", " ", r["note"])[:120]
                out.append(f"| `{r['source']}` | {note} | да |")
            out.append("")
    # Подслучай: замысел, у которого домом оказался только реестр (ни одна страница источник не объявляет).
    # Решение по нему уже стоит в очереди выше, здесь остаётся сама полка — чтобы было видно, где материал.
    shelf_only = [r for r in q if r["pages"] and all(p.endswith("-registry") for p in r["pages"])]
    if shelf_only:
        out.append("Дом только у реестра (ни одна страница источник не объявляет): "
                   + ", ".join(f"`{r['source'][:44]}`" for r in shelf_only) + ".")
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wiki", default=".")
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()
    root = os.path.abspath(a.wiki)
    vault = toolkit.wiki(root)
    pages = pages_of(vault)
    shelved = intent_shelved(root, vault, pages)
    body = render(root, vault, pages, shelved)

    header = ['---', 'title: "Тематическая карта вики"', "type: summary",
              "created: 2026-09-15", "updated: 2026-09-15", "last-verified: 2026-09-15",
              "verification-status: current", "evidence: mixed", "status: generated",
              "tags: [knowledge/wiki]", "sources: []",
              'summary: "Темы вики и термины-артефакты, названные многими страницами без своей страницы. Генерируется _toolkit/topic_map.py."',
              "---", ""]
    if a.write:
        target = os.path.join(vault, "_meta", "topic-map.md")
        open(target, "w", encoding="utf-8", newline="").write(canon_tables("\n".join(header) + body))
        # §18: страница больше 150 строк обязана назвать причину, почему не расщеплена. Карта
        # ценна полнотой охвата, поэтому обоснование пишет генератор, а число строк = факту.
        _txt = open(target, encoding="utf-8").read()
        if "split-justification" not in _txt:
            _n = len(_txt.rstrip("\n").split("\n")) + 1    # строка обоснования добавится ниже
            _txt = _txt.replace("summary:", 'split-justification: "%d строк — тематическая карта генерируется целиком: расщепление разорвало бы сопоставление тем между разделами";\nsummary:' % _n, 1)
            open(target, "w", encoding="utf-8", newline="").write(_txt)
        themes_n = len({t for i in pages.values() for t in i["tags"]})
        cands_now = cand.candidates(vault, root)
        live_now = [c for c in cands_now if not c["decision"]]
        print(f"записано: {os.path.relpath(target, root)} — тем {themes_n}, кандидатов в страницы "
              f"{len(cands_now)} (висящих {len(live_now)})")
    else:
        print(body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
