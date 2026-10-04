#!/usr/bin/env python3
"""Машиночитаемые реестры вики: карточки извлечения и утверждения с атрибуцией.

Пишет два файла, из которых пакет отчётов берёт числа вместо ручного набора:

    _staging/cards-registry.json   — карточки: тип, источник, наличие lineage
    _staging/claims-registry.tsv   — утверждения: идентификатор, страница, владелец, тип подтверждения

Запуск: python3 _toolkit/registries.py --wiki .
"""
import argparse
import json
import os
import re
import sys

import toolkit

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fmparse import fold

CLAIM = re.compile(r"\(([^()]*?(?:Верховский|Шейко|Минин|Соломоник|Крестников|Голощапов|Алексей|Сергей|Шумер|DHH|Hansson|Anthropic|OpenAI|GitHub|Stanford|Stryker|BMAD|OpenSpec|METR|не атрибутирован)[^()]*)\)", re.IGNORECASE)


def read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


def fm(t):
    m = re.match(r"^---\r?\n(.*?)\r?\n---", t, re.DOTALL)
    d = {}
    if m:
        for line in m.group(1).split("\n"):
            if ":" in line and not line.startswith((" ", "\t", "-")):
                k, v = line.split(":", 1)
                d[k.strip()] = v.strip().strip('"')
            else:
                fold(d, line)
    return d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wiki", required=True)
    a = ap.parse_args()
    root = a.wiki
    staging = toolkit.area(root)
    cards_dir = os.path.join(staging, "cards")
    raw_dir = toolkit.raw(root)
    arts_dir = os.path.join(raw_dir, "articles")

    corpus = [f for f in sorted(os.listdir(raw_dir)) if f.endswith(".md")]
    # вложенные папки raw/ (корпус практика): нерекурсивный обход делал карточки нового корпуса
    # невидимыми для покрытия — четвёртый случай этого класса за сутки.
    for _d in sorted(os.listdir(raw_dir)):
        _p = os.path.join(raw_dir, _d)
        if os.path.isdir(_p) and _d != "articles":
            corpus += [f for f in sorted(os.listdir(_p)) if f.endswith(".md")]
    articles = [f for f in sorted(os.listdir(arts_dir)) if f.endswith(".md")] if os.path.isdir(arts_dir) else []
    cards = [f for f in sorted(os.listdir(cards_dir)) if f.endswith(".md")] if os.path.isdir(cards_dir) else []
    texts = {c: read(os.path.join(cards_dir, c)) for c in cards}
    # Заметки владельца (Telegram) — отдельный вид карточки, не «манифест»: раньше они попадали в счёт
    # манифестов, и пакет печатал бы «149 манифестов» вместо девятнадцати. Смешивать виды нельзя:
    # манифест — переработка доклада одним голосом, заметка — сохранённое сообщение с датой и каналом.
    telegram = {f for f in os.listdir(os.path.join(raw_dir, "telegram"))
                if f.endswith(".md")} if os.path.isdir(os.path.join(raw_dir, "telegram")) else set()

    def source_of(card):
        """Источник карточки: frontmatter source, иначе упоминание имени файла в тексте."""
        meta = fm(texts[card])
        if meta.get("source"):
            return os.path.basename(meta["source"])
        body = texts[card]
        m = re.search(r"#\s*Карточка:\s*([^\s]+\.md)", body)   # заголовок карточки называет источник
        if m:
            name = os.path.basename(m.group(1))
            if name in articles + corpus:
                return name
        for f in articles + corpus:                     # затем любое упоминание имени файла
            if f in body:
                return f
        stem = card[:-3].replace("ext-", "").lower()
        for f in articles + corpus:                     # затем сравнение по слагу
            fs = f[:-3].lower()
            if fs == stem or fs.replace("-", "_") == stem.replace("-", "_") or fs in stem or stem in fs:
                return f
        return "?"

    def kind_of(card):
        src = source_of(card)
        if src in articles or (src == "?" and card.startswith("ext-")):
            return "article"
        if "prompt" in src:
            return "template"
        if "report" in src:
            return "report"
        if src in telegram:
            return "note"
        return "manifest"

    registry, by_kind = [], {"manifest": 0, "article": 0, "template": 0, "report": 0, "note": 0}
    for c in cards:
        meta = fm(texts[c])
        k = kind_of(c)
        by_kind[k] += 1
        registry.append({
            "id": c[:-3],
            "file": c,
            "kind": k,
            "source": source_of(c),
            "lineage_tools": bool(meta.get("lineage_tools")),
            "lineage_debate": meta.get("lineage_debate", "[]") not in ("[]", ""),
            # Число строк файла, а не число переводов строки: файл, кончающийся переводом, давал +1 к каждой карточке
    # (167 расхождений из 167 — поле читает `ingest_wave_map.py`, и оно врало ровно на строку).
    "lines": len(texts[c].rstrip("\n").split("\n")),
        })

    # Покрытие считается В ОДНИХ ЕДИНИЦАХ — в источниках, а не в карточках:
    # знаменатель = источники, допустимые к карточке (без служебного шаблона и без транскрипций-докладов,
    # которые манифест перерабатывает: это один голос, а не два).
    # Служебный файл опознаётся по имени, а не по подстроке «prompt»: иначе вендорская статья
    # openai-codex-prompting-guide.md выпадала из знаменателя как шаблон. Транскрипции-доклады — по суффиксу.
    SERVICE_FILES = {"manifest_maker_prompt.md"}
    admissible = [n for n in (corpus + articles)
                  if n not in SERVICE_FILES and not n.endswith("_report.md")]
    covered_sources = [n for n in admissible if any(c["source"] == n for c in registry)]
    uncovered = [n for n in admissible if n not in covered_sources]
    covered = {"manifest": len([n for n in covered_sources if n in corpus]),
               "article": len([n for n in covered_sources if n in articles])}

    summary = {
        "cards_total": len(registry),
        "by_kind": by_kind,
        "lineage_filled": sum(1 for r in registry if r["lineage_tools"]),
        "debates": sum(1 for r in registry if r["lineage_debate"]),
        "covered_manifests": covered["manifest"],
        "covered_articles": covered["article"],
        "cardinality": len(admissible),          # источники, допустимые к карточке (знаменатель)
        "covered_sources": len(covered_sources),  # из них с карточкой (числитель)
        "coverage": f"{len(covered_sources)}/{len(admissible)}",
        "uncovered": uncovered,
    }
    json.dump({"summary": summary, "cards": registry},
              open(os.path.join(staging, "cards-registry.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    # утверждения страниц: строка раздела «Факты и цифры» с владельцем
    claims, cid = [], 0
    vault = toolkit.wiki(root)
    for d in ("concepts", "comparisons", "entities", "queries"):
        full = os.path.join(vault, d)
        if not os.path.isdir(full):
            continue
        for fn in sorted(os.listdir(full)):
            if not fn.endswith(".md"):
                continue
            text = read(os.path.join(full, fn))
            m = re.search(r"(?ms)^## (?:Факты и цифры|Числа и замеры)\s*$(.*?)(?=^## |\Z)", text)
            if not m:
                continue
            slug = fn[:-3]
            for i, line in enumerate(m.group(1).split("\n"), 1):
                if not line.strip().startswith(("-", "*")) or not re.search(r"\d", line):
                    continue
                owner = CLAIM.search(line)
                if not owner:
                    continue
                cid += 1
                parts = [x.strip() for x in owner.group(1).split(",")]
                claims.append({
                    "claim_id": f"CLM-{slug}-{cid:04d}" if False else f"CLM-{slug}-{i:02d}",
                    "page": slug,
                    "line": i,
                    "owner": parts[0] if parts else "",
                    "evidence": parts[1] if len(parts) > 1 else "",
                    "text": line.strip()[:120],
                })
    with open(os.path.join(staging, "claims-registry.tsv"), "w", encoding="utf-8") as f:
        f.write("claim_id\tpage\tline\towner\tevidence\ttext\n")
        for c in claims:
            f.write("\t".join(str(c[k]) for k in ("claim_id", "page", "line", "owner", "evidence", "text")) + "\n")

    # страница происхождения корпуса: кто на какие инструменты опирается и с кем спорит
    tools = {}
    for c in registry:
        for t in re.findall(r"[a-z][a-z0-9-]+", str(fm(texts[c["file"]]).get("lineage_tools", "[]"))):
            tools.setdefault(t, []).append(c["id"])
    vault_meta = toolkit.wiki(root, "_meta")
    os.makedirs(vault_meta, exist_ok=True)
    L = ["---", 'title: "Происхождение корпуса: инструменты и полемика"', "type: summary",
         "created: 2026-09-11", "updated: 2026-09-11", "last-verified: 2026-09-11",
         "verification-status: current", "evidence: mixed",
         "own-analysis: true", "status: generated", "tags: [knowledge/wiki, methodology/manifesto]",
         "sources: []",
         'summary: "Измеренная интеллектуальная зависимость корпуса: какие инструменты упоминают авторы, кто с кем спорит. Генерируется из карточек (_toolkit/registries.py)."',
         "---", "", "Служебная страница: сводка по карточкам извлечения, не документ Layer 1.", "",
         "Корпус — один мильё, и это видно по пересечению инструментов: файловая независимость голосов выше интеллектуальной. "
         "Страницы, опирающиеся только на аффилированный корпус, не могут иметь `confidence: high`.", "",
         f"Карточек в реестре: {summary['cards_total']}; с заполненным `lineage_tools`: {summary['lineage_filled']}; "
         f"с зафиксированной полемикой: {summary['debates']}.", "",
         "| инструмент | сколько карточек |", "|---|---|"]
    for t, cids in sorted(tools.items(), key=lambda kv: -len(kv[1]))[:15]:
        L.append(f"| `{t}` | {len(cids)} |")
    L += ["", "## Кто с кем спорит", "", "| карточка | полемика |", "|---|---|"]
    for c in registry:
        d = fm(texts[c["file"]]).get("lineage_debate", "[]")
        if d not in ("[]", ""):
            L.append(f"| `{c['id']}` | {d.strip('[]')} |")
    L += ["", "## Как читать", "",
          "Пересечение инструментов — признак общей практики, а не независимого подтверждения. "
          "При оценке тезиса источники, читающие друг друга, считаются одним голосом с повышенным весом, а не несколькими независимыми.", ""]
    lineage = os.path.join(vault_meta, "corpus-lineage.md")
    open(lineage, "w", encoding="utf-8").write("\n".join(L) + "\n")
    from align_tables import align_file  # канон: таблицы выровнены пробелами
    align_file(lineage)
    print("страница происхождения корпуса: wiki/_meta/corpus-lineage.md")

    print(f"карточек: {summary['cards_total']} (манифесты {by_kind['manifest']}, статьи {by_kind['article']}, "
          f"заметки {by_kind['note']}, шаблон {by_kind['template']})")
    print(f"покрытие: манифестов {covered['manifest']}, статей {covered['article']}; допустимых к карточке источников: {summary['cardinality']}")
    print("без карточек:", ", ".join(uncovered) if uncovered else "нет")
    print(f"утверждений с атрибуцией: {len(claims)}")
    print("реестры: _staging/cards-registry.json, _staging/claims-registry.tsv")


if __name__ == "__main__":
    main()
