#!/usr/bin/env python3
"""Источник добавлен — смысловой проход по нему пройден? Проверка §46 линтера.

Канон 2026-09-16: любое добавление источника (одиночного из `raw/` или волной инжеста) завершается
смысловым проходом по вики, и наряд прохода называет каждый прочитанный источник. Сторож ловит случай
«источник завели, а расхождения со страницами никто не искал»: источник добавлен позже даты последнего
наряда и в наряде не назван.

    python3 _toolkit/semantic_coverage.py --wiki .        # список находок, код возврата 1 при находках

Дата добавления берётся из шапки источника: `ingested` — когда он вошёл в вики; если поля нет, берётся
`source_date`, и лишь затем `created` (дата публикации старше добавления, и по ней источник выглядел бы
давно проверенным). Источники вовсе без даты не проверяются — это не находка, а отсутствие данных.
"""
import argparse
import glob
import io
import json
import os
import re
import sys

import toolkit


def read(path):
    with io.open(path, encoding="utf-8") as f:
        return f.read()


def load_report(path):
    try:
        return json.load(io.open(path, encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as ex:
        print(f"предупреждение: отчёт {path} пропущен ({ex})", file=sys.stderr)
        return None


def covered_and_cutoff(root):
    """Источники из нарядов прохода (по стемам) и дата последнего наряда."""
    covered, cutoff = set(), ""
    for rp in sorted(glob.glob(toolkit.area(root, "stance", "*semantic*.json"))):
        data = load_report(rp)
        if data is None:
            continue
        date = str(data.get("date") or data.get("wave") or "")
        if date > cutoff:
            cutoff = date
        for it in (data.get("items") or []):
            src = it.get("source") if isinstance(it, dict) else ""
            if src:
                covered.add(os.path.basename(src)[:-3])
        for src in (data.get("sources") or []):
            if isinstance(src, str):
                covered.add(os.path.basename(src)[:-3])
    return covered, cutoff


def added_date(text):
    """Когда источник вошёл в вики: ingested, иначе source_date, иначе created."""
    fm = dict(re.findall(r"(?m)^(ingested|created|source_date):\s*\"?(\d{4}-\d{2}-\d{2})", text[:1500]))
    return fm.get("ingested") or fm.get("source_date") or fm.get("created") or ""


def missing_page_findings(root):
    """Расхождение, у которого нет страницы, обязано получить адрес (тупик 2026-09-17).

    Смысловой проход идёт по существующим страницам, а найденное расхождение может быть про понятие, у
    которого страницы ещё нет: такому негде жить — ни строки в карте, ни раздела. Правило: в отчёте прохода
    такое расхождение помечается полем `page_missing` (что за расхождение и о каком понятии), а понятие
    обязано стоять в очереди кандидатов или иметь решение — тогда оно не осядет в отчёте молча.
    Сегодня ни один отчёт поля не несёт: проверка заведена как страховка и первым же отчётом с полем
    вступает в силу.
    """
    import glob as _glob
    import os as _os
    queue = ""
    for name in ("_staging/prose-candidates.tsv", "_staging/candidate-decisions.tsv"):
        fp = _os.path.join(root, name)
        if _os.path.exists(fp):
            queue += read(fp)
    out = []
    for rp in sorted(_glob.glob(toolkit.area(root, "stance", "wave-*-semantic.json"))):
        data = load_report(rp)
        if data is None:
            continue
        for it in data.get("items", []):
            missing = (it.get("page_missing") or "").strip()
            if not missing:
                continue
            concept = (it.get("concept") or it.get("term") or "").strip()
            if concept and concept.lower() in queue.lower():
                continue
            out.append("%s: расхождение без страницы («%s»), понятие «%s» не в очереди кандидатов и без решения "
                       "— заведи строку очереди или назови адресата" % (_os.path.basename(rp), missing[:60], concept))
    return out


def issues(root):
    """Находки проверки: источник добавлен после последнего наряда и в наряде не назван."""
    covered, cutoff = covered_and_cutoff(root)
    out = []
    if not cutoff:
        return out
    out.extend(missing_page_findings(root))
    for base, dirs, files in os.walk(toolkit.raw(root)):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for fn in sorted(files):
            if not fn.endswith(".md"):
                continue
            date = added_date(read(os.path.join(base, fn)))
            if date and date > cutoff and fn[:-3] not in covered:
                out.append("%s: источник добавлен %s, смыслового прохода по нему нет — расхождения "
                           "со страницами не искали (шаг 4б, отчёт semantic-pass)" % (fn, date))
    return out


def main():
    ap = argparse.ArgumentParser(description="Источник добавлен — проход пройден?")
    ap.add_argument("--wiki", default=".")
    a = ap.parse_args()
    root = os.path.abspath(a.wiki)
    found = issues(root)
    for f in found:
        print(f)
    covered, cutoff = covered_and_cutoff(root)
    print("наряд последнего прохода: %s · источников в нарядах: %d · находок: %d"
          % (cutoff or "нет", len(covered), len(found)))
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
