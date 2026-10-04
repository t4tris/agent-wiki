#!/usr/bin/env python3
"""Оценка слепого прогона эталонных вопросов по вики.

Считает машинно: попал ли отвечающий в ожидаемые страницы, привёл ли источники,
признал ли пробел. Пишет отчёт в _staging/audit/blind-run-score-<дата>.md.

Запуск: python3 _toolkit/score_blind_run.py --wiki .
"""
import argparse
import datetime
import glob
import json
import os
import re

import toolkit


def read(p):
    return open(p, encoding="utf-8").read()


def parse_answers(text):
    """Делит ответы по номерам вопросов и достаёт страницы/источники/пробел."""
    # отвечающие оформляют вопросы по-разному: «**Вопрос 11.**» и «## 11. …»
    parts = re.split(r"(?m)^(?:\*\*Вопрос\s+|#{2,4}\s+)(\d+)[.)\s]", text)
    out = {}
    for i in range(1, len(parts), 2):
        n = int(parts[i])
        body = parts[i + 1]
        pages = re.search(r"\*\*Использованы страницы:\*\*\s*([^\n]+)", body)
        srcs = re.search(r"\*\*Источники:\*\*\s*([^\n]+)", body)
        gap = re.search(r"\*\*Пробел:\*\*\s*([^\n]+)", body)
        slugs = set()
        if pages:
            # чистка от знаков препинания: «long-running-agents.» — тот же слаг
            slugs = {s.strip().strip("`[]().,;:«»") for s in re.split(r"[,\s]+", pages.group(1)) if s.strip()}
        out[n] = {
            "pages": slugs,
            "has_sources": bool(srcs),
            "gap": gap.group(1).strip() if gap else "",
            "len": len(body),
        }
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wiki", required=True)
    a = ap.parse_args()
    audit = toolkit.area(a.wiki, "audit")
    evalf = sorted(glob.glob(os.path.join(audit, "eval-questions-*.md")))
    expected, adjacent = {}, {}
    for m in re.finditer(r"(?m)^\| (\d+) \| ([^|]+?) \| ([^|]+?) \| ([^|]+?) \|", read(evalf[-1])):
        n = int(m.group(1))
        expected[n] = set(re.findall(r"`([a-z0-9-]+)`", m.group(3)))
        adjacent[n] = set(re.findall(r"`([a-z0-9-]+)`", m.group(4)))

    known = set()
    vault = toolkit.wiki(a.wiki)
    for d in ("concepts", "comparisons", "entities", "queries"):
        full = os.path.join(vault, d)
        if os.path.isdir(full):
            known |= {f[:-3] for f in os.listdir(full) if f.endswith(".md")}

    # Прогоны считаются ПОРОЗНЬ.  Раньше ответы всех прогонов сливались в один словарь, где
    # побеждал последний файл: числа прошлого прогона на поверку оказывались числами нового,
    # а промахи «растворялись». Каждый прогон оценивается по вопросам, на которые он отвечал.
    runs, sources_files = {}, []
    for f in sorted(glob.glob(os.path.join(audit, "blind-run-*.md"))):
        if "score" in f:
            continue
        t = read(f)
        m_date = re.search(r"blind-run-(?:[a-z]-)?(\d{4}-\d{2}-\d{2})", os.path.basename(f))
        key = m_date.group(1) if m_date else os.path.basename(f)
        runs.setdefault(key, {}).update(parse_answers(t))
        sources_files.append(os.path.basename(f))
    latest = max(runs) if runs else None
    answers = runs.get(latest, {})

    # парная перепроверка: повторный прогон отдельных вопросов после правок
    rerun = {}
    for f in sorted(glob.glob(os.path.join(audit, "blind-rerun-*.md"))):
        rerun.update(parse_answers(read(f)))
    rows, hit, miss, gaps, strict, strict_all, partial, adj_miss = [], 0, [], 0, 0, 0, 0, []
    for n in sorted(expected):
        exp = expected[n]
        got = answers.get(n, {}).get("pages", set())
        got_pages = {g for g in got if g in known}
        overlap = exp & got_pages
        ok = bool(overlap)
        hit += 1 if ok else 0
        if exp <= got_pages:
            strict += 1
        if (exp | adjacent.get(n, set())) <= got_pages:
            strict_all += 1
        else:
            adj_miss.append((n, sorted((exp | adjacent.get(n, set())) - got_pages)))
        g = answers.get(n, {}).get("gap", "")
        if g and not re.match(r"^(нет|—|-|не обнаружено|нет пробела)", g.strip(), re.IGNORECASE):
            partial += 1
        if not ok:
            miss.append((n, sorted(exp), sorted(got_pages)[:6]))
        if answers.get(n, {}).get("gap"):
            gaps += 1
        gap_txt = "да" if (answers.get(n, {}).get("gap", "") and not re.match(r"^(нет|—|-|не обнаружено|нет пробела)", answers[n]["gap"].strip(), re.IGNORECASE)) else "нет"
        rows.append(f"| {n} | {'да' if ok else '**нет**'} | {'да' if ok and exp <= got_pages else 'нет'} | {gap_txt} | "
                    f"{', '.join('`'+x+'`' for x in sorted(exp))} | {', '.join('`'+x+'`' for x in sorted(got_pages)) or '—'} |")
    total = len(expected)
    with_src = sum(1 for n in expected if answers.get(n, {}).get("has_sources"))

    out = [f"# Оценка слепого прогона ({datetime.date.today().isoformat()})", "",
           f"Ответы: {', '.join(sources_files)}. Вопросов: {total}.",
           f"Мягкое попадание (названа хотя бы одна страница — основная или смежная): {hit}/{total}.",
           f"Строгое по основным (названы все страницы, где лежит ответ): {strict}/{total}.",
           f"Строгое по полному набору (плюс смежные: карта споров, экономика, механизмы): {strict_all}/{total}.",
           f"Ответов, где читатель сам назвал пробел или неполноту: {partial}/{total}. Привели источники: {with_src}/{total}.", "",
           "Строгая по основным важнее мягкой: мягкая показывает, что тема находится, строгая — что материал собран в одном месте.",
           "Строгая по полному набору показывает другое: доходит ли читатель до контекстных страниц (карта споров, экономика, механизмы). "
           "До пятого разбора обе вещи считались одной метрикой, поэтому прежний строгий счёт 20/24 совпадает с текущим «полным» — переопределение ничего не улучшило, только объяснило.", "",
           "| № | мягкое попадание | строгое по основным | признали пробел | ожидались (полный список) | назвал отвечающий (полный список) |", "|---|---|---|---|---|---|"]
    out += rows
    # рубрика: что такое «основные» — фиксируется здесь, а не подбирается под результат
    RUBRIC = {
        "мягкое попадание": "названа хотя бы одна страница — основная или смежная (тема найдена)",
        "строгое по основным": "названы ВСЕ страницы из колонки «Основные страницы (ответ)» — там, где ответ и лежит",
        "строгое по полному": "плюс все смежные (карта споров, экономика, механизмы) — вторичная метрика полноты",
    }
    THRESHOLDS = {"мягкое попадание": 1.0, "строгое по основным": 0.9, "строгое по полному": 0.75}

    def stats(ans):
        """Счёт по одному прогону: только те вопросы, на которые он отвечал."""
        soft = core = full = 0
        missed_core, missed_full = [], []
        total = 0
        for n in sorted(expected):
            if n not in ans:
                continue
            total += 1
            exp, adj = expected[n], adjacent.get(n, set())
            got = {g for g in ans[n].get("pages", set()) if g in known}
            if exp & got:
                soft += 1
            if exp <= got:
                core += 1
            else:
                missed_core.append((n, sorted(exp - got)))
            if (exp | adj) <= got:
                full += 1
            else:
                missed_full.append((n, sorted((exp | adj) - got)))
        return total, soft, core, full, missed_core, missed_full

    per_run = {d: stats(a) for d, a in sorted(runs.items())}
    if rerun:
        out += ["", "## Повторный прогон (парная точка)", "",
                "| № | первый прогон | после правок | ожидались |", "|---|---|---|---|"]
        for n in sorted(rerun):
            full = expected.get(n, set()) | adjacent.get(n, set())
            f_got, n_got = answers.get(n, {}).get("pages", set()), rerun[n]["pages"]
            out.append(f"| {n} | {len(full & f_got)}/{len(full)} | {len(full & n_got)}/{len(full)} | "
                       f"{', '.join('`'+x+'`' for x in sorted(expected.get(n, set())))} |")
        fr = sum(len((expected.get(n, set()) | adjacent.get(n, set())) & answers.get(n, {}).get("pages", set())) for n in rerun)
        nr = sum(len((expected.get(n, set()) | adjacent.get(n, set())) & rerun[n]["pages"]) for n in rerun)
        tot = sum(len(expected.get(n, set()) | adjacent.get(n, set())) for n in rerun)
        out += ["", f"Проверено повторно: {len(rerun)} вопроса; метрика — **полнота (recall) по ожидаемым страницам**: "
                    f"сколько ожидаемых страниц назвал читатель, знаменатель — все ожидаемые страницы этих вопросов ({tot}). "
                    f"После правок {nr}/{tot} против {fr}/{tot} в первом прогоне.", ""]
    out += ["", "## Рубрика и пороги", "", "Определение рубрики (фиксируется кодом, не подбирается под результат):", ""]
    for k, v in RUBRIC.items():
        out.append(f"- **{k}** — {v}")
    out += ["", "Пороги приёмки: мягкое попадание 100%, строгое по основным ≥ 90%, "
            "строгое по полному набору ≥ 75% (вторичная метрика полноты).", "",
            "## Траектория по прогонам (каждый прогон считается по своим вопросам)", "",
            "| прогон | вопросов | мягко | строго по основным | строго по полному | вердикт |",
            "|---|---|---|---|---|---|"]
    for d, (total_d, soft_d, core_d, full_d, _mc, _mf) in per_run.items():
        verdict = []
        for short, val, key in (("мягко", soft_d, "мягкое попадание"), ("основные", core_d, "строгое по основным"),
                                ("полный", full_d, "строгое по полному")):
            if total_d and val / total_d < THRESHOLDS[key]:
                verdict.append(f"ниже порога: {short}")
        out.append(f"| {d} | {total_d} | {soft_d}/{total_d} | {core_d}/{total_d} | {full_d}/{total_d} | "
                   f"{'; '.join(verdict) if verdict else 'все пороги выдержаны'} |")
    out += ["", "## Разбор промахов строгого критерия", "",
            "Промах — не названа хотя бы одна страница, где лежит ответ. Классификация механическая: "
            "если пропущенная страница есть и в списке смежных того же вопроса, это дополнительная страница, "
            "иначе — страница-механизм (то есть ответ не сошёлся в месте).", ""]
    for d, (total_d, soft_d, core_d, full_d, mc, mf) in per_run.items():
        if not mc:
            out.append(f"- {d}: промахов строгого по основным нет")
        else:
            out.append(f"- **{d}** (промахов {len(mc)} из {total_d}):")
            for n, pages in mc:
                extra = sorted(adjacent.get(n, set()) & set(pages))
                note = "дополнительная страница" if extra else "страница-механизм не найдена"
                out.append(f"  - вопрос {n}: не названо {', '.join('`'+x+'`' for x in pages)} — {note}")
        if mf:
            out.append(f"  промахи по полному набору — {len(mf)} из {total_d}:")
            for n, pages in mf:
                extra = sorted(adjacent.get(n, set()) & set(pages))
                core_miss = sorted(adjacent.get(n, set()) and expected.get(n, set()) & set(pages))
                kind = "смежная/карта споров" if extra or core_miss else "страница-механизм"
                out.append(f"    - вопрос {n}: не названо {', '.join('`'+x+'`' for x in pages)} ({kind})")
    if adj_miss:
        out += ["", "## Куда читатель не доходит", "",
                "Основной ответ найден, но контекстные страницы остаются непрочитанными:", ""]
        for n, miss_pages in adj_miss:
            out.append(f"- №{n}: не названы " + ", ".join(f"`{x}`" for x in miss_pages))
    if miss:
        out += ["", "## Мимо ожиданий", ""]
        for n, exp, got in miss:
            out.append(f"- №{n}: ожидались {', '.join('`'+x+'`' for x in exp)}; названы {', '.join('`'+x+'`' for x in got) or '—'}")
    out += ["", "## Как читать", "",
            "Попадание считается по пересечению: если отвечающий назвал хотя бы одну ожидаемую страницу, вопрос зачтён. "
            "Промах означает, что либо страница плохо находится из `index.md`, либо отвечающий ушёл в сторону — оба случая требуют разбора. "
            "Столбец «признали пробел» показывает, помечает ли вики свою неуверенность настолько, чтобы это увидел независимый читатель.", ""]
    report = os.path.join(audit, f"blind-run-score-{datetime.date.today().isoformat()}.md")
    open(report, "w", encoding="utf-8").write("\n".join(out) + "\n")
    print(f"попадание: {hit}/{total} | пробел признан: {partial}/{total} | источники: {with_src}/{total}")
    print("отчёт:", report)

    # Машинный источник истины для метрик прогона: журнал экзамена и письма читают ЭТОТ файл,
    # а не прозу отчёта. Причина — расхождение 15/27 против 20/27 (2026-09-14): число, вынутое
    # регуляркой из человекочитаемой фразы, дрейфует вместе с прозой и живёт своей жизнью.
    metrics = os.path.join(audit, f"blind-run-metrics-{datetime.date.today().isoformat()}.json")
    payload = {
        "date": datetime.date.today().isoformat(),
        "generator": "score_blind_run.py",
        "answers_files": sources_files,
        "metrics": {
            "questions": total,
            "soft": hit,
            "strict_main": strict,
            "strict_full": strict_all,
            "named_gap": partial,
            "sources": with_src,
        },
        "per_run": {d: {"questions": t_d, "soft": soft_d, "strict_main": core_d, "strict_full": full_d}
                    for d, (t_d, soft_d, core_d, full_d, _mc, _mf) in per_run.items()},
        "latest_run": latest,
        "adjacent_miss": [[n, p] for n, p in adj_miss],
        "rubric": RUBRIC,
        "thresholds": THRESHOLDS,
    }
    json.dump(payload, open(metrics, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("машинные метрики:", metrics)


if __name__ == "__main__":
    main()
