#!/usr/bin/env python3
"""Диспетчер смыслового прохода: наряд читателям и приёмка их отчётов.

Процедура прохода — `_toolkit/lint-pass.md` (пять глазных проверок), канон режима — `_toolkit/SCHEMA.md`. Раньше объём
прохода и раздача читателям собирались руками, и отчёт волны «прошёл lint» подтверждался только тем, что
кто-то видел детей в консоли. Здесь два шага: наряд с точным контрактом и приёмка, которую вызывает раннер
волны (`wave_runner.py`, этап «Смысловой проход»).

    python3 _toolkit/semantic_pass.py --wiki . prepare [--per 11] [--all]   # наряды в _staging/audit/semantic-pass-<дата>/
    python3 _toolkit/semantic_pass.py --wiki . check                        # приёмка: что не предъявлено и что не по контракту

Контракт отчёта (проверяется при приёмке): `{"contract_version": 1.0, "kind": "lint-eye", "part": N,
"pages_read": [...], "findings": [{file, line, kind, quote, why, evidence}], "clean_pages": [...]}`.
У находки вида `концепт_без_страницы` обязателен ещё и `term` — иначе находка не превращается в кандидата.
Пустой отчёт по странице законен: это тоже результат чтения.
"""
import argparse
import datetime
import glob
import io
import json
import os
import re
import sys

import check_contract
import toolkit

CHECKS = [
    ("противоречие_страниц", "противоречия вне оси stance: страница A утверждает одно, страница B — другое"),
    ("устаревшее", "утверждение, перекрытое более новым источником или практикой (смотри даты в шапках)"),
    ("концепт_без_страницы", "термин, инструмент или метод, названный прозой и не заведённый страницей и ссылкой; обязателен `term`"),
    ("связность", "где читатель не доходит: нет входящих ссылок, нет хода из index.md или соседних страниц"),
    ("обрывок | нет_носителя | непонятно_что_меняет | цепочка_без_фразы",
     "язык и пробелы семантики: обрывок без сказуемого, цепочка вместо фразы, утверждение без носителя"),
]
FIELDS = ("file", "line", "kind", "quote", "why", "evidence")


def read(path):
    with io.open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def scope(root, all_pages):
    """Объём прохода берём тем же счётом, что и отчёт прохода: чужая логика тут не нужна."""
    sys.path.insert(0, toolkit.area(root))
    import lint_pass
    pages, why = lint_pass.scope(root, all_pages)
    return pages, why


def prepare(root, per, all_pages):
    pages, why = scope(root, all_pages)
    if not pages:
        print("объём прохода пуст: с прошлого отчёта страницы не менялись (--all возьмёт все)")
        return 1
    date = datetime.date.today().isoformat()
    out = toolkit.area(root, "audit", "semantic-pass-%s" % date)
    os.makedirs(out, exist_ok=True)
    parts = [pages[i:i + per] for i in range(0, len(pages), per)]
    checks = "\n".join("- **%s** — %s" % (k, v) for k, v in CHECKS)
    for n, chunk in enumerate(parts, 1):
        path = os.path.join(out, "part-%d.md" % n)
        body = ["# Проход lint, часть %d из %d" % (n, len(parts)), "",
                "ОБЪЁМ: %s" % why, "",
                "## Страницы части (читать целиком, все %d)" % len(chunk), ""]
        body += ["- %s" % p for p in chunk]
        body += ["", "## Пять проверок", "", checks, "",
                 "Проверку 1 веди по своду тезисов: `_toolkit/stance_context.py` кладёт на каждую страницу её раздел свода;"
                 " расхождение живёт в утверждении, а не в совпадении терминов.", "",
                 "## Контракт отчёта", "",
                 "Пиши файл `_staging/audit/lint-eye-%d.json` (проверка 5 — `style-read-%d.json`) строго такой формы:" % (n, n), "",
                 '```json', '{"contract_version": "1.0", "kind": "lint-eye", "part": %d,' % n,
                 ' "pages_read": ["wiki/…"],',
                 ' "findings": [{"file": "wiki/…", "line": 42, "kind": "противоречие_страниц",',
                 '               "quote": "дословная цитата", "why": "почему это находка",',
                 '               "evidence": "wiki/…:17"}],',
                 ' "clean_pages": ["wiki/…"]}', '```', "",
                 "Требования приёмки: цитата дословна, номер строки обязателен, `evidence` — второй файл и строка,"
                 " у находки «концепт без страницы» обязателен `term`. Пустой отчёт по странице — законный результат:"
                 " это тоже чтение. Язык ответа — русский.", ""]
        with io.open(path, "w", encoding="utf-8", newline="") as f:
            f.write("\n".join(body))
    print("нарядов: %d по %d страниц, объём: %s" % (len(parts), per, why))
    print("папка: " + os.path.relpath(out, root))
    return 0


def reports(root, date):
    d = toolkit.area(root, "audit")
    pats = ("lint-eye-*.json", "style-read-*.json")
    out = {}
    for p in sorted(glob.glob(os.path.join(d, pats[0])) + glob.glob(os.path.join(d, pats[1]))):
        base = os.path.basename(p)
        if "merged" in base or "parts" in base:
            continue
        try:
            data = json.load(io.open(p, encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as ex:
            out[base] = (f"отчёт не читается как JSON: {str(ex)[:120]}", None)
        else:
            out[base] = (None, data)
    return out


def check(root, per, needed=True, report_dir=None):
    """Приёмка: наряд на сегодня есть, отчёт на каждую часть есть, контракт соблюдён."""
    date = datetime.date.today().isoformat()
    d = report_dir or toolkit.area(root, "audit", "semantic-pass-%s" % date)
    problems = []
    parts = sorted(glob.glob(os.path.join(d, "part-*.md")))
    if needed and not parts:
        problems.append("наряда на сегодня нет: `semantic_pass.py prepare` не выполнен")
        return problems, 0, []
    got = reports(root, date)
    kinds = check_contract.local_kinds(root)
    late = []
    # Отчёт принимается только по этому наряду: чужой файл той же части — прошлый проход, а не ответ на наряд
    # (иначе приёмка «проходит» по отчётам вчерашнего дня — так и вышло на первом прогоне).
    for n, part_path in enumerate(parts, 1):
        parts_mtime = os.path.getmtime(part_path)
        d_report = toolkit.area(root, "audit")
        found = []
        for k in got:
            if not re.search(r"(lint-eye|style-read)-%d(\.|-)" % n, k):
                continue
            if os.path.getmtime(os.path.join(d_report, k)) + 1 < parts_mtime:
                problems.append("%s: отчёт старше наряда — это прошлый проход, а не ответ на часть %d" % (k, n))
                continue
            found.append(k)
        if not found:
            problems.append("часть %d: отчёта нет" % n)
            continue
        for base in found:
            err, data = got[base]
            if err:
                problems.append("%s: %s" % (base, err))
                continue
            p_report = toolkit.area(root, "audit", base)
            for field in ("contract_version", "kind", "pages_read"):
                if field not in data:
                    problems.append(f"{base}: нет поля {field}")
            spec = kinds.get(data.get("kind"), {})
            items = check_contract.report_items(data, spec)
            if items is None and "findings" in data:
                items = data["findings"]
            if not isinstance(items, list):
                problems.append(f"{base}: нет массива отчёта")
                items = []
            for it in items:
                for field in FIELDS:
                    if not str(it.get(field, "")).strip():
                        problems.append("%s: находка без %s (%s)" % (base, field, str(it.get("file"))[:40]))
                if it.get("kind") == "концепт_без_страницы" and not str(it.get("term", "")).strip():
                    problems.append("%s: находка «концепт без страницы» без term" % base)
                # Дословность и адрес проверяются машинно: приёмка, которая верит на слово, ловит опечатки,
                # но не выдумку. Цитата обязана быть подстрокой указанной строки, evidence — существовать.
                f = os.path.join(root, str(it.get("file", "")))
                # Страница, поправленная после отчёта, лишает проверку опоры: цитата была верна на момент чтения,
                # а после законной правки адреса её уже нет. Такую находку не считаем нарушением, но и не прячем:
                # в отчёте приёмки она помечена как «страница правилась после прохода».
                if os.path.exists(f) and os.path.getmtime(f) > os.path.getmtime(p_report) + 1:
                    late.append(str(it.get("file")))
                    continue
                if not os.path.exists(f):
                    problems.append("%s: находка ссылается на несуществующий файл %s" % (base, it.get("file")))
                else:
                    lines = read(f).split("\n")
                    try:
                        n = int(it.get("line", 0))
                    except (TypeError, ValueError):
                        n = 0
                    if n < 1 or n > len(lines):
                        problems.append("%s: номер строки %s вне файла %s (%d строк)" % (base, n, it.get("file"), len(lines)))
                    else:
                        line = lines[n - 1]
                        if str(it.get("quote", "")).strip() not in line and str(it.get("quote", "")).strip() not in "\n".join(lines[n - 1:n + 3]):
                            problems.append("%s: цитата не найдена в строке %s файла %s" % (base, n, it.get("file")))
                ev = str(it.get("evidence", ""))
                evfile = ev.split(":")[0]
                if evfile and not os.path.exists(os.path.join(root, evfile)):
                    problems.append("%s: evidence ссылается на несуществующий файл %s" % (base, evfile))
    return problems, len(parts), sorted(set(late))


def main():
    ap = argparse.ArgumentParser(description="Диспетчер смыслового прохода")
    ap.add_argument("command", choices=["prepare", "check"])
    ap.add_argument("--wiki", default=".")
    ap.add_argument("--per", type=int, default=11)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--report-dir", default="")
    a = ap.parse_args()
    root = os.path.abspath(a.wiki)
    if a.command == "prepare":
        return prepare(root, a.per, a.all)
    problems, parts, late = check(root, a.per, report_dir=a.report_dir)
    if late:
        print("примечание: страницы правились после прохода, цитаты по ним не перепроверяются — " +
              ", ".join(late[:6]))
    if problems:
        print("смысловой проход не принят (частей в наряде: %d):" % parts)
        for p in problems[:20]:
            print("  — " + p)
        return 1
    print("смысловой проход принят: частей %d, отчёты по контракту" % parts)
    return 0


if __name__ == "__main__":
    sys.exit(main())
