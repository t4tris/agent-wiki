#!/usr/bin/env python3
"""Метрики здоровья вики: покрытие таксономии, свежесть, плотность противоречий, реестр источников.

Запуск: python3 ./_toolkit/wiki_metrics.py --wiki .
С флагом --write перезаписывает сгенерированную страницу wiki/_meta/quality-metrics.md.
"""
import argparse
import collections
import glob
import datetime
import hashlib
import os
import re
import sys

import toolkit

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import schema
from fmparse import fold

TODAY = datetime.date.today()
STALE_DAYS = 180


def _cmap_read(root):
    """Текст карты конфликтов: её путь объявляет экземпляр; не объявил — пусто."""
    p = _domain.register_path(root, "conflicts")
    return read(p) if p and os.path.exists(p) else ""


def read(p):
    return open(p, encoding="utf-8").read()


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


def list_of(v):
    return [x.strip() for x in (v or "[]").strip("[]").split(",") if x.strip()]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wiki", required=True)
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()
    root, vault = a.wiki, toolkit.wiki(a.wiki)

    tags_defined = schema.taxonomy(root)

    pages, tags_used, disputed, fresh = {}, collections.Counter(), [], collections.Counter()
    srcs_cited = collections.Counter()
    for d in ("concepts", "comparisons", "entities", "queries", "_meta"):
        full = os.path.join(vault, d)
        if not os.path.isdir(full):
            continue
        for fn in sorted(os.listdir(full)):
            if not fn.endswith(".md"):
                continue
            t = read(os.path.join(full, fn))
            m = fm(t)
            pages[fn[:-3]] = (d, m, t)
            for g in list_of(m.get("tags")):
                tags_used[g] += 1
            if m.get("contradictions") and m["contradictions"] not in ("[]", ""):
                disputed.append(fn[:-3])
            lv = m.get("last-verified", "")
            try:
                age = (TODAY - datetime.date.fromisoformat(lv)).days
            except ValueError:
                age = -1
            fresh[m.get("verification-status", "нет поля")] += 1
            for s in list_of(m.get("sources")):
                srcs_cited[os.path.basename(s)] += 1

    # реестр источников
    rows = []
    raw_root = toolkit.raw(root)
    for p in sorted(glob.glob(os.path.join(raw_root, "**", "*.md"), recursive=True)):
        fn = os.path.basename(p)
        rel_dir = os.path.relpath(os.path.dirname(p), raw_root).replace(os.sep, "/")
        sub = rel_dir.split("/", 1)[0] if rel_dir != "." else ""
        m = fm(read(p))
        rows.append((fn, {"articles": "лонгрид", "GRACE": "статья практика"}.get(sub, "манифест/транскрипция"),
                     m.get("source_author") or m.get("authors", ""), m.get("ingested", ""),
                     hashlib.sha256(open(p, "rb").read()).hexdigest()[:12],
                     srcs_cited.get(fn, 0),
                     "vendor-self-report" if sub == "articles" else "practitioner-opinion"))

    # Баланс голосов: доля автора в источниках и в строках карты конфликтов.
    # По ответу аудитора 9: не штрафовать частых авторов, но делать доминирование видимым читателю.
    def voice(author_field, filename):
        """Голос источника.

        Порядок: явное поле `source_author`/`authors` → имя из имени файла (`Верховский_manifest.md` → «Верховский»).
        Из поля берём первое имя, снимаем вики-скобки и служебные хвосты (даты, arXiv, названия конференций):
        иначе в списке голосов появляются «Anthropic, Applied AI team, 2025-09-29» и «[[Виктор Соломоник]]».
        """
        raw_a = (author_field or "").strip().strip('"')
        raw_a = re.sub(r"[\[\]]", "", raw_a)
        first = raw_a.split(",")[0].strip()
        first = re.sub(r"\s+(arXiv|NeurIPS|ICML|ICLR)\b.*$", "", first, flags=re.IGNORECASE).strip()
        if not first:
            stem = re.sub(r"\.md$", "", filename)
            stem = re.sub(r"_(?:manifest|report)(?:_\d+)?$", "", stem)   # «Matt_Shumer_manifest» → «Matt_Shumer»
            first = stem.strip("_-")
        return first or "голос не указан"

    by_author = collections.Counter()
    for fn, _kind, author, _ing, _sha, _refs, _conf in rows:
        by_author[voice(author, fn)] += 1
    cmap_txt = _cmap_read(root)
    cmap_rows_all = [l for l in cmap_txt.split("\n") if l.strip().startswith("|") and re.match(r"\|\s*\**\d+", l.strip())]
    author_in_rows = collections.Counter()
    for fn, _kind, author, _ing, _sha, _refs, _conf in rows:
        v = voice(author, fn)          # голос берём тем же правилом, что и в таблице источников
        stem = fn[:-3]
        for row in cmap_rows_all:
            if re.search(r"\[\[\s*" + re.escape(stem) + r"\s*(\]|\|)", row):
                author_in_rows[v] += 1
    total_src = sum(by_author.values()) or 1
    total_rows = len(cmap_rows_all) or 1

    unused_tags = sorted(tags_defined - set(tags_used))
    out = []
    out.append("# Метрики здоровья вики")
    out.append("")
    out.append(f"Сгенерировано {TODAY.isoformat()}. Источник данных: frontmatter страниц и файлы `raw/`.")
    out.append("")
    out.append("## Покрытие")
    out.append("")
    out.append(f"- страниц: {len(pages)} (концептов {sum(1 for d, _, _ in pages.values() if d=='concepts')}, "
               f"сравнений {sum(1 for d, _, _ in pages.values() if d=='comparisons')}, "
               f"сущностей {sum(1 for d, _, _ in pages.values() if d=='entities')}, "
               f"ответов {sum(1 for d, _, _ in pages.values() if d=='queries')})")
    out.append(f"- тем в таксономии использовано: {len(tags_defined & set(tags_used))} из {len(tags_defined)} (учитываются все страницы, включая _meta)")
    if unused_tags:
        out.append(f"- темы, объявленные в схеме, но не используемые: {', '.join('`'+x+'`' for x in unused_tags)}")
    out.append(f"- источников в реестре: {len(rows)}; без единой ссылки на страницах: {sum(1 for r in rows if r[5]==0)}")
    out.append("")
    out.append("## Свежесть")
    out.append("")
    for k, v in sorted(fresh.items()):
        out.append(f"- `{k}`: {v}")
    out.append(f"- правило: цифры вендоров и продуктов пересматриваются каждые {STALE_DAYS} дней; страница с просроченным `last-verified` помечается `verification-status: stale`")
    out.append("")
    out.append("## Плотность противоречий")
    out.append("")
    out.append(f"- страниц с непустым `contradictions`: {len(disputed)} из {len(pages)}")
    cmap = _cmap_read(root)
    disputes = len(re.findall(r"(?m)^\| *\*{0,2}\d+", cmap))
    out.append("- споров в карте конфликтов: " + str(disputes))
    out.append("- ориентир: 1–3 спора на концепт — норма; больше пяти на одну страницу означает, что тему пора расщеплять")
    out.append("")
    # петля использования: страницы, не нужные ни одному вопросу
    out.append("")
    out.append("## Баланс голосов")
    out.append("")
    out.append("Доля автора в источниках слоя 1 и в строках карты конфликтов. Это мониторинг, а не штраф: "
               "доминирование должно быть видно читателю, но искусственно ограничивать рост корпуса нельзя. "
               "Одна строка карты может называть несколько голосов, поэтому доли строк в сумме не дают 100%.")
    out.append("")
    out.append("| голос | источников | доля источников | строк карты | доля строк |")
    out.append("|---|---|---|---|---|")
    for author, cnt in by_author.most_common(8):
        rows_n = author_in_rows.get(author, 0)
        out.append(f"| {author} | {cnt} | {round(100 * cnt / total_src)}% | {rows_n} | {round(100 * rows_n / total_rows)}% |")
    out.append("")
    named = [(a, n) for a, n in by_author.most_common() if a != "голос не указан"]
    top_a, top_n = (named[0] if named else ("—", 0))
    out.append(f"- самый представленный голос: {top_a} — {top_n} источников из {total_src} "
               f"({round(100 * top_n / total_src)}%); ориентир для разбора — доля выше четверти")
    out.append("")

    out.append("## Техдолг")
    out.append("")
    debt_path = os.path.join(root, "debt.tsv")
    if os.path.exists(debt_path):
        drows = [l.split("\t") for l in read(debt_path).splitlines() if l.strip()]
        dhead = drows[0]
        dbody = [dict(zip(dhead, r)) for r in drows[1:] if len(r) >= len(dhead)]
        st = collections.Counter(r["статус"] for r in dbody)
        out.append(f"- всего пунктов: {len(dbody)}; " + ", ".join(f"{k}: {v}" for k, v in sorted(st.items())))
        opened = [r for r in dbody if r["статус"] in ("открыт", "в работе")]
        if opened:
            out.append(f"- открытых {len(opened)}; по владельцам: "
                       + ", ".join(f"{k}: {v}" for k, v in sorted(collections.Counter(r["владелец"] for r in opened).items())))
            out.append("")
            out.append("| пункт | класс | владелец | с |")
            out.append("|---|---|---|---|")
            for r in opened:
                out.append(f"| {r['пункт']} | {r['класс']} | {r['владелец']} | {r['открыт']} |")
        accepted = [r for r in dbody if r["статус"] in ("отклонён", "отложен", "принято ограничение")]
        if accepted:
            out.append("")
            out.append(f"- решённых «не делать»: {len(accepted)} (отклонено, отложено, принято как ограничение) — "
                       "переоткрывать без новой причины не следует")
    else:
        out.append("- реестра `debt.tsv` нет: открытые пункты живут в письмах")
    out.append("")

    out.append("## Петля использования")
    out.append("")
    audit = toolkit.area(root, "audit")
    ev = ""
    if os.path.isdir(audit):
        for fn in sorted(os.listdir(audit)):
            if fn.startswith("eval-questions-") and fn.endswith(".md"):
                ev = read(os.path.join(audit, fn))
    asked = set(re.findall(r"`([a-z0-9-]+)`", ev))
    for d in ("queries",):
        full = os.path.join(vault, d)
        if os.path.isdir(full):
            for fn in os.listdir(full):
                if fn.endswith(".md"):
                    asked.add(fn[:-3])
    content = [s for s, (d, m, t) in pages.items() if d in ("concepts", "comparisons")]
    unused = sorted(s for s in content if s not in asked)
    q_count = len(re.findall("(?m)^\\| \\d+ \\|", ev))
    out.append(f"- эталонных вопросов: {q_count}; страниц, упомянутых в вопросах или в queries: {len([s for s in content if s in asked])} из {len(content)}")
    out.append("- страницы, не участвующие ни в одном вопросе (кандидаты на удаление или на новый вопрос): " + (", ".join('`'+x+'`' for x in unused) if unused else "нет"))
    out.append("")
    out.append("## Реестр источников")
    out.append("")
    out.append("| файл | тип | автор | добавлен | sha256 | страниц | подтверждение |")
    out.append("|---|---|---|---|---|---|---|")
    for fn, kind, author, ing, sha, n, ev in rows:
        out.append(f"| `{fn}` | {kind} | {author} | {ing} | `{sha}` | {n} | {ev} |")
    out.append("")
    text = "\n".join(out)
    if a.write:
        target = os.path.join(vault, "_meta", "quality-metrics.md")
        os.makedirs(os.path.dirname(target), exist_ok=True)
        open(target, "w", encoding="utf-8").write("---\n" + "\n".join([
            'title: "Метрики здоровья вики"', "type: summary", f"created: {TODAY.isoformat()}",
            f"updated: {TODAY.isoformat()}", f"last-verified: {TODAY.isoformat()}",
            "verification-status: current", "evidence: mixed", "status: generated",
            "tags: [knowledge/wiki]", "sources: []",
            'summary: "Покрытие таксономии, свежесть проверок, плотность противоречий и реестр источников. Файл генерируется скриптом wiki_metrics.py."']) + "\n---\n\n" + text + "\n")
        # §18: страница больше 150 строк обязана назвать причину, почему не расщеплена. Файл
        # генерируется целиком, поэтому обоснование пишет сам генератор, и число строк = факту.
        _txt = open(target, encoding="utf-8").read()
        if "split-justification" not in _txt:
            _n = len(_txt.rstrip("\n").split("\n")) + 1    # строка обоснования добавится ниже
            _txt = _txt.replace("summary:", 'split-justification: "%d строк — сводка метрик генерируется целиком: расщепление разорвало бы сопоставимость чисел между разделами";\nsummary:' % _n, 1)
            open(target, "w", encoding="utf-8", newline="").write(_txt)
        from align_tables import align_file  # канон: таблицы выровнены пробелами, иначе Obsidian правит страницу сам
        if align_file(target):
            print("выровнено:", target)
        print("записано:", target)
    print(text)



import os as _os
import sys as _sys

_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import domain as _domain  # домен экземпляра: имена его страниц и реестров знает только он

if __name__ == "__main__":
    main()
