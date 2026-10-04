#!/usr/bin/env python3
"""Экзамен вики: слепой прогон эталонных вопросов как регулярная церемония.

Три шага, каждый — одна команда:

    python3 _toolkit/exam.py --wiki . prepare
        готовит вопросы без подсказок (exam-<дата>-questions.md) и печатает наряд читателям.

    python3 _toolkit/exam.py --wiki . score
        оценивает ответы (score_blind_run), дописывает строку в траекторию
        (_staging/audit/exam-history.tsv) и сравнивает с прошлым прогоном:
        падение строгого попадания — сигнал, что новые страницы зашумили граф
        или сломали навигацию.

    python3 _toolkit/exam.py --wiki . status
        показывает траекторию экзамена по всем прогонам.

Церемония: после каждого контентного спринта. Порог тревоги — падение строгого
попадания по основным страницам; падение только «полного набора» означает, что
читатель не доходит до контекстных страниц (навигация, а не содержание).
"""
import argparse
import csv
import datetime
import glob
import json
import os
import re
import subprocess
import sys

import toolkit

TODAY = datetime.date.today().isoformat()


def read(p):
    return open(p, encoding="utf-8").read()


def read_metrics(audit):
    """Метрики прогона — ТОЛЬКО из машинного вывода score_blind_run.py (JSON).

    Почему так: до 2026-09-14 журнал вынимал число регуляркой из человекочитаемой фразы отчёта,
    и в истории появилось «пропуски 15/27» против настоящего «неполноту назвали 20/27» — проза
    дрейфует между отправками, и любое извлечённое из неё число наследует дрейф.
    """
    files = sorted(glob.glob(os.path.join(audit, "blind-run-metrics-*.json")))
    if not files:
        sys.exit("нет машинного вывода метрик: сначала `score_blind_run.py --wiki .` "
                 "(журнал экзамена не читает прозу и не угадывает)")
    m = json.load(open(files[-1], encoding="utf-8"))["metrics"]
    t = m["questions"]
    return {
        "date": TODAY,
        "soft": f"{m['soft']}/{t}",
        "strict_main": f"{m['strict_main']}/{t}",
        "strict_full": f"{m['strict_full']}/{t}",
        "named_gap": f"{m['named_gap']}/{t}",
        "sources": f"{m['sources']}/{t}",
        "adjacent_miss": [[str(n), p] for n, p in json.load(open(files[-1], encoding="utf-8")).get("adjacent_miss", [])],
        "metrics_file": os.path.basename(files[-1]),
    }


def ratio(v):
    if not v or "/" not in v:
        return None
    a, b = v.split("/")
    return int(a) / int(b) if int(b) else None


def history_path(audit):
    return os.path.join(audit, "exam-history.tsv")


def read_history(audit):
    p = history_path(audit)
    if not os.path.exists(p):
        return []
    with open(p, encoding="utf-8") as f:
        return list(csv.DictReader(f, delimiter="\t"))


def append_history(audit, row):
    p = history_path(audit)
    new = not os.path.exists(p)
    if not new:                                     # пере-оценка в тот же день обновляет строку
        rows = read_history(audit)
        if rows and rows[-1]["date"] == row["date"]:
            rows[-1] = {k: row.get(k, "") for k in rows[-1]}
            with open(p, "w", encoding="utf-8", newline="") as f:
                w = csv.DictWriter(f, delimiter="\t", fieldnames=list(rows[0].keys()))
                w.writeheader()
                w.writerows(rows)
            return
    with open(p, "a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, delimiter="\t",
                           fieldnames=["date", "soft", "strict_main", "strict_full", "named_gap", "sources"])
        if new:
            w.writeheader()
        w.writerow({k: row.get(k, "") for k in w.fieldnames})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wiki", required=True)
    ap.add_argument("cmd", choices=["prepare", "score", "status", "fresh"])
    a = ap.parse_args()
    audit = toolkit.area(a.wiki, "audit")
    os.makedirs(audit, exist_ok=True)

    if a.cmd == "prepare":
        evals = sorted(glob.glob(os.path.join(audit, "eval-questions-*.md")))
        if not evals:
            sys.exit("нет файла эталонных вопросов")
        text = read(evals[-1])
        rows = re.findall(r"(?m)^\| (\d+) \| ([^|]+?) \|", text)
        out = [f"# Экзамен вики: вопросы без подсказок ({TODAY})", "",
               "Отвечай только по вики (`wiki/`) и слою источников (`raw/`), внешними знаниями не пользуйся.",
               "Для каждого вопроса: ответ в 3–6 предложениях, затем «**Использованы страницы:**» (полный список слагов, не обрезай), «**Источники:**» со документами raw, «**Пробел:**» — если ответа нет или он неполный.", ""]
        out += [f"{n}. {q.strip()}" for n, q in rows]
        target = os.path.join(audit, f"exam-{TODAY}-questions.md")
        open(target, "w", encoding="utf-8").write("\n".join(out) + "\n")
        print("вопросов:", len(rows), "| файл:", target)
        print("\nНаряд: раздать вопросы N независимым читателям (не менее двух), с ответами в "
              f"_staging/audit/blind-run-<дата>-<буква>.md, затем `exam.py score`.")
        return

    if a.cmd == "score":
        subprocess.run([sys.executable, "-X", "utf8", toolkit.script("score_blind_run.py", a.wiki),
                        "--wiki", a.wiki], check=True)
        row = read_metrics(audit)
        prev = read_history(audit)
        append_history(audit, row)
        print(f"\nТраектория: {row['soft']} мягко, {row['strict_main']} строго по основным, "
              f"{row['strict_full']} по полному набору")
        verdict = ["", f"# Вердикт экзамена ({TODAY})", "",
                   f"Мягкое попадание: {row['soft']}; строгое по основным: {row['strict_main']}; "
                   f"по полному набору: {row['strict_full']}; свою неполноту назвали: {row['named_gap']}.", ""]
        if prev:
            last = prev[-1]
            print(f"Прошлый прогон ({last['date']}): {last['soft']} / {last['strict_main']} / {last['strict_full']}")
            for key, human in (("strict_main", "строгое по основным"), ("strict_full", "строгое по полному набору")):
                now_r, was_r = ratio(row.get(key)), ratio(last.get(key))
                if now_r is None or was_r is None:
                    continue
                if now_r < was_r:
                    verdict.append(f"**СИГНАЛ РЕГРЕССИИ:** {human} упало с {last[key]} до {row[key]}. "
                                   "Сначала разбор навигации и шума новых страниц, потом новый контент.")
                    print(f"СИГНАЛ РЕГРЕССИИ: {human} {last[key]} -> {row[key]}")
        else:
            verdict.append("Это первый прогон: он задаёт базу для сравнения.")
        if row["adjacent_miss"]:
            verdict += ["", "Читатель не доходит до контекстных страниц (навигация, не содержание):", ""]
            verdict += [f"- №{n}: {p}" for n, p in row["adjacent_miss"]]
        open(os.path.join(audit, f"exam-{TODAY}.md"), "w", encoding="utf-8").write("\n".join(verdict) + "\n")
        print("вердикт:", os.path.join(audit, f"exam-{TODAY}.md"))
        return

    if a.cmd == "fresh":
        # Проверка устаревания: числа экзамена описывают состояние на дату ответов, а не сегодняшнее.
        # Дефект, который она ловит (2026-09-14): ответы от 09-13, содержимое с тех пор менялось, а числа
        # в письмах читаются как свежие. Найденный на текущих данных дефект — механизм заведён не зря.
        # Только файлы ОТВЕТОВ: отчёты о скоринге (`blind-run-score-*`) тоже лежат с датой в имени,
        # и из-за них проверка в первом прогоне объявила свежими ответы трёхдневной давности.
        runs = [f for f in sorted(glob.glob(os.path.join(audit, "blind-run-*.md")))
                if "score" not in os.path.basename(f)]
        dates = [m.group(1) for m in (re.search(r"(\d{4}-\d{2}-\d{2})", os.path.basename(f)) for f in runs) if m]
        if not dates:
            sys.exit("ответов слепого прогона нет — экзамен не проходился")
        last_run = max(dates)
        r = subprocess.run(["git", "-C", a.wiki, "log", "-1", "--format=%ad", "--date=short", "--", "wiki/"],
                           capture_output=True, text=True, errors="replace", check=False)
        if r.returncode != 0:
            has_head = subprocess.run(["git", "-C", a.wiki, "rev-parse", "--verify", "--quiet", "HEAD"],
                                      capture_output=True, text=True, check=False).returncode == 0
            if not has_head:
                last_content = "0000-01-01"
                print("истории git ещё нет: свежесть экзамена проверяется по дате ответов")
            else:
                detail = (r.stderr or r.stdout).strip() or "без сообщения"
                print(f"не удалось прочитать дату последней правки wiki/: {detail}", file=sys.stderr)
                last_content = "9999-12-31"
        else:
            last_content = r.stdout.strip() or "неизвестно"
        print(f"последние ответы: {last_run} | последняя правка содержимого: {last_content}")
        if last_content > last_run:
            print(f"ЧИСЛА ЭКЗАМЕНА УСТАРЕЛИ: содержимое менялось {last_content}, ответы от {last_run}.")
            print("Нужен новый слепой прогон (prepare -> читатели -> score); старые ответы засчитывать нельзя.")
            sys.exit(1)
        print("числа экзамена не устарели: ответы не старше последней правки содержимого")
        return

    hist = read_history(audit)
    if not hist:
        print("траектория пуста: сначала `exam.py score`")
        return
    print("| дата | мягко | строго по основным | строго по полному | неполноту назвали | источники |")
    print("|---|---|---|---|---|---|")
    for r in hist:
        print(f"| {r['date']} | {r['soft']} | {r['strict_main']} | {r['strict_full']} | {r['named_gap']} | {r['sources']} |")


if __name__ == "__main__":
    main()
