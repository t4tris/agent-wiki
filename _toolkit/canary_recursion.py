#!/usr/bin/env python3
"""Регресс-канарейка: обход источников обязан быть рекурсивным.

    python3 _toolkit/canary_recursion.py --wiki .

Кладёт пробный источник во ВЛОЖЕННУЮ папку `raw/zz-recursion-probe/` и проверяет,
что число каждого потребителя слоя источников сдвинулось:

  1. `audit_report.py`   — «файлов слоя 1» в снапшоте пакета;
  2. `registries.py`     — знаменатель покрытия карточек;
  3. `wiki_metrics.py`   — строк в таблице реестра на странице метрик;
  4. `lint_wiki.py` §20  — упомянутый документ из вложенной папки обязан быть объявлен в sources.

Почему отдельным скриптом: нерекурсивный обход случался трижды за одну сессию (пакет, метрики,
раздел 20) и каждый раз давал правдоподобное, но заниженное число. Проверять это вниманием нельзя.

Выход: 0 — все потребители увидели вложенный файл; 1 — хотя бы один не увидел (регрессия).
Живая вики восстанавливается в исходное состояние в finally.
"""
import argparse
import datetime
import json
import os
import re
import subprocess
import sys

import schema
import toolkit

PROBE_DIR = "zz-recursion-probe"
PROBE_NAME = "probe-source.md"
PROBE_PAGE = os.path.join("concepts", "zz-probe-page.md")


def read(p):
    return open(p, encoding="utf-8", errors="replace").read()


def write(p, t):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd = os.open(p, flags)
    with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
        stream.write(t)


def run(script, root, *extra):
    """wiki_metrics без --write печатает в stdout и не трогает страницу метрик."""
    subprocess.run([sys.executable, "-X", "utf8", script, "--wiki", root, *extra],
                   capture_output=True, text=True, encoding="utf-8", errors="replace", check=True)
    return


def figures(root):
    import glob
    import json
    fs = sorted(glob.glob(toolkit.area(root, "audit", "wiki-figures-*.json")))
    if not fs:
        sys.exit("нет снапшота пакета (audit/wiki-figures-*.json): сначала audit_report.py")
    data = json.load(open(fs[-1], encoding="utf-8"))
    if "figures" not in data or "файлов слоя 1" not in data["figures"]:
        sys.exit("снапшот пакета без ключа «файлов слоя 1»: пересобери audit_report.py")
    return data["figures"]


def cards_summary(root):
    import json
    p = toolkit.area(root, "cards-registry.json")
    if not os.path.exists(p):
        sys.exit("нет реестра карточек (_staging/cards-registry.json): сначала registries.py")
    data = json.load(open(p, encoding="utf-8"))
    if "summary" not in data or "cardinality" not in data["summary"]:
        sys.exit("реестр карточек без сводки: пересобери registries.py")
    return data["summary"]


def metrics_registry_count(root):
    """Строк в таблице реестра метрик: это и есть рекурсивный обход raw/ внутри wiki_metrics.
    Счётчик «источников в реестре» здесь не годится — он читает заявленный реестр, а не дерево файлов."""
    page = toolkit.wiki(root, "_meta", "quality-metrics.md")
    if not os.path.exists(page):
        sys.exit("нет страницы метрик (wiki/_meta/quality-metrics.md): сначала wiki_metrics.py --write")
    t = read(page)
    return len(re.findall(r"(?m)^\| `[^`]+`\s*\|", t))


def lint_section(root, num):
    proc = subprocess.run([sys.executable, "-X", "utf8", toolkit.script("lint_wiki.py", root), "--wiki", root],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    if proc.returncode not in (0, 1):
        detail = (proc.stderr or proc.stdout).strip() or "без сообщения"
        raise RuntimeError(f"lint_wiki.py завершился с кодом {proc.returncode}: {detail}")
    m = re.search(rf"^##\s*{num}\.[^\n]*:\s*(\d+)", proc.stdout, re.MULTILINE)
    return int(m.group(1)) if m else -1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wiki", required=True)
    a = ap.parse_args()
    root = os.path.abspath(a.wiki)
    probe = toolkit.raw(root, PROBE_DIR, PROBE_NAME)
    page = toolkit.wiki(root, PROBE_PAGE)
    occupied = [path for path in (probe, page) if os.path.exists(path)]
    if occupied:
        print("отказ: временный raw-путь уже занят: " + ", ".join(occupied))
        return 1
    made = []
    try:
        today = datetime.date.today().isoformat()
        tags = sorted(schema.taxonomy(root))
        tag_field = "[" + ", ".join(json.dumps(tag, ensure_ascii=False) for tag in tags[:1]) + "]"
        base = {
            "пакет": figures(root)["файлов слоя 1"],
            "реестр": cards_summary(root)["cardinality"],
            "метрики": metrics_registry_count(root),
            "§20": lint_section(root, 20),
        }
        body = ("---\ntitle: Пробный источник канарейки рекурсии\ntype: article\ntags: " + tag_field + "\n"
                "status: active\nsummary: пробный файл, создаётся и удаляется канарейкой\n---\n\n"
                "Текст пробного источника для проверки рекурсивного обхода `raw/`.\n")
        write(probe, body)
        made.append(probe)
        # страница, упоминающая пробный источник, но не объявляющая его: §20 обязан сработать
        pfm = ("---\ntitle: Пробная страница канарейки рекурсии\ntype: concept\ntags: " + tag_field + "\n"
               f"created: {today}\nupdated: {today}\nlast-verified: {today}\n"
               "verification-status: current\nevidence: practitioner-opinion\n"
               "own-analysis: false\nstatus: active\nconfidence: medium\n"
                "sources: []\nsummary: пробная страница канарейки\n---\n\n"
                f"# Проба рекурсии\n\n- Смотри также [[{PROBE_NAME[:-3]}]]: там 42 единицы измерения.\n")
        write(page, pfm)
        made.append(page)

        for name, script in (("пакет", "audit_report.py"), ("реестр", "registries.py"), ("метрики", "wiki_metrics.py")):
            run(toolkit.script(script, root), root, *(["--write"] if script == "wiki_metrics.py" else []))
        after = {
            "пакет": figures(root)["файлов слоя 1"],
            "реестр": cards_summary(root)["cardinality"],
            "метрики": metrics_registry_count(root),
            "§20": lint_section(root, 20),
        }
        ok = True
        print(f"{'потребитель':12s} {'до':>5s} {'после':>6s}  вердикт")
        for k in base:
            grew = after[k] > base[k]
            ok &= grew
            print(f"{k:12s} {base[k]:5d} {after[k]:6d}  {'увидел вложенный файл' if grew else 'СЛЕП: регрессия'}")
        print("\nитог:", "рекурсия подтверждена у всех потребителей" if ok else "есть слепые потребители")
        return 0 if ok else 1
    finally:
        for path in made:
            if os.path.exists(path):
                os.remove(path)
        d = toolkit.raw(root, PROBE_DIR)
        if os.path.isdir(d) and not os.listdir(d):
            os.rmdir(d)
        # вернуть артефакты генераторов к состоянию без пробного файла
        for script in ("audit_report.py", "registries.py", "wiki_metrics.py"):
            run(toolkit.script(script, root), root, *(["--write"] if script == "wiki_metrics.py" else []))


if __name__ == "__main__":
    sys.exit(main())
