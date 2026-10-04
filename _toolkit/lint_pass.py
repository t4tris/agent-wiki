#!/usr/bin/env python3
"""Проход lint по вики: собирает объём, снимает машинные числа, сводит находки глазных проверок.

Процедура целиком — `_toolkit/lint-pass.md`; канон режима — `_toolkit/SCHEMA.md`, «Режим lint: LLM проходит по вики».

Что делает команда:
  1. считает объём прохода: страницы, изменившиеся с даты прошлого отчёта (или все — с `--all`);
  2. гоняет машинную половину (линтер) и замер стиля (`style_audit.py`), сохраняет числа;
  3. собирает отчёты подагентов: `lint-eye-*.json` (четыре проверки здоровья), `style-read-*.json`
     (проверка языка), `fix-out-*.json` / `style-fix-out-*.json` (вердикты проверяющих);
  4. пишет отчёт `_staging/audit/lint-pass-<дата>.md` — с числами, находками по проверкам и тремя
     исходами; ничего не правит в вики.

Запуск: python3 _toolkit/lint_pass.py --wiki . [--all] [--write]
"""
import argparse
import glob
import io
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import toolkit

AUDIT = "audit"
TAIL_MARK = "<!-- ниже — дописано вручную, сохраняется при пересборке -->"
CHECKS = {
    "противоречие_страниц": "1. Противоречия между страницами (вне оси stance)",
    "устаревшее": "2. Устаревшее: перекрытое более новым источником",
    "концепт_без_страницы": "3. Концепт, упомянутый прозой без страницы",
    "связность": "4. Связность: где читатель не доходит",
    "обрывок": "5а. Язык: обрывок без сказуемого",
    "нет_носителя": "5б. Язык: утверждение без носителя",
    "непонятно_что_меняет": "5в. Язык: назван механизм без «что он меняет»",
    "цепочка_без_фразы": "5г. Язык: цепочка вместо фразы",
    "другое": "5д. Язык: прочее",
}
GENERATED = {"quality-metrics.md", "corpus-lineage.md", "source-registry.md", "topic-map.md",
             "index.md", "wiki-state.md"}


def read(p):
    with io.open(p, encoding="utf-8", newline="") as f:
        return f.read()


def write(p, t):
    with io.open(p, "w", encoding="utf-8", newline="") as f:
        f.write(t)


def last_pass_date(root):
    dates = []
    for p in glob.glob(toolkit.area(root, AUDIT, "lint-pass-*.md")):
        m = re.search(r"(\d{4}-\d{2}-\d{2})", os.path.basename(p))
        if m:
            dates.append(m.group(1))
    return max(dates) if dates else None


def scope(root, all_pages):
    if all_pages:
        pages = []
        for d in ("concepts", "comparisons", "entities", "queries", "_meta"):
            for p in sorted(glob.glob(toolkit.wiki(root, d, "*.md"))):
                if os.path.basename(p) not in GENERATED:
                    pages.append("wiki/%s/%s" % (d, os.path.basename(p)))
        return pages, "все страницы (--all)"
    since = last_pass_date(root)
    cmd = ["git", "log", "--name-only", "--pretty=format:"]
    if since:
        cmd.append("--since=%s" % since)
    cmd += ["--", "wiki"]
    result = subprocess.run(cmd, cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    if result.returncode != 0:
        detail = (result.stderr or "").strip()
        raise RuntimeError("git log не прочитал изменения: " + (detail or "код %d" % result.returncode))
    pages = sorted({l.strip() for l in (result.stdout or "").split("\n") if re.match(r"^wiki/(concepts|comparisons|entities|queries|_meta)/", l.strip())})
    pages = [p for p in pages if os.path.basename(p) not in GENERATED and not p.startswith("wiki/sources/")]
    why = "страницы, изменившиеся с %s" % since if since else "все изменившиеся (прошлых отчётов нет)"
    return pages, why


def machine(root):
    lint_proc = subprocess.run([sys.executable, "-X", "utf8", toolkit.script("lint_wiki.py"),
                                "--wiki", root, "--no-summary"],
                               capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    if lint_proc.returncode not in (0, 1):
        raise RuntimeError("линтер завершился с кодом %d" % lint_proc.returncode)
    lint = lint_proc.stdout
    sections = {}
    for m in re.finditer(r"(?m)^##\s+(\d+)\.(.*?):\s*(\d+)\s*$", lint):
        sections[int(m.group(1))] = int(m.group(3))
    mt = re.search(r"ИТОГО проблем:\s*(\d+)", lint)
    if not mt or not sections:
        raise RuntimeError("вывод линтера не содержит обязательных маркеров разделов и итога")
    total = int(mt.group(1))
    sieve = None
    sieve_path = toolkit.area(root, AUDIT, "style-audit-%s.json" % __import__("datetime").date.today().isoformat())
    r = subprocess.run([sys.executable, "-X", "utf8", toolkit.script("style_audit.py"),
                        "--wiki", root, "--json", sieve_path],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    if r.returncode != 0:
        raise RuntimeError("style_audit.py завершился с кодом %d" % r.returncode)
    try:
        d = json.load(io.open(sieve_path, encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as ex:
        raise RuntimeError("отчёт style_audit не прочитан: %s" % ex)
    if (not d or not isinstance(d, dict) or
            any(not isinstance(v, dict) or not isinstance(v.get("findings"), list) for v in d.values())):
        raise RuntimeError("отчёт style_audit: нет обязательного поля findings")
    sieve = {k: len(v["findings"]) for k, v in d.items()}
    # Гниль внешних адресов живёт отдельным отчётом (`tasks.py links`); числа подтягиваем, если отчёт
    # за сегодня уже есть: иначе читатель отчёта прохода не знает, что адреса вообще проверялись.
    links = None
    links_path = toolkit.area(root, AUDIT, "links-pass-%s.md" % __import__("datetime").date.today().isoformat())
    if os.path.exists(links_path):
        lt = io.open(links_path, encoding="utf-8", errors="replace").read()
        m_alive = re.search(r"Живых:\s*(\d+)", lt)
        m_block = re.search(r"заблокированных[^:]*:\s*(\d+)", lt)
        m_dead = re.search(r"мёртвых:\s*(\d+)", lt)
        if not (m_alive and m_block and m_dead):
            raise RuntimeError("отчёт links не содержит обязательных маркеров чисел")
        links = {"alive": int(m_alive.group(1)),
                 "blocked": int(m_block.group(1)),
                 "dead": int(m_dead.group(1)),
                 "path": os.path.relpath(links_path, root).replace("\\", "/")}
    return {"sections": len(sections), "total": total, "s45": sections.get(45, 0), "sieve": sieve,
            "sieve_path": os.path.relpath(sieve_path, root).replace("\\", "/") if sieve else None,
            "links": links}


def child_reports(root):
    found = []
    for pattern in ("lint-eye-*.json", "style-read-*.json", "read-pass-*.json"):
        for p in sorted(glob.glob(toolkit.area(root, AUDIT, pattern))):
            base = os.path.basename(p)
            # сводные файлы и раскладка страниц — не отчёты подагентов: у них список, а не записи
            if "parts" in base or "merged" in base:
                continue
            found.append(p)
    items, clean = [], []
    for p in found:
        try:
            d = json.load(io.open(p, encoding="utf-8"))
        except (OSError, UnicodeError, ValueError) as ex:
            raise RuntimeError("отчёт %s не прочитан: %s" % (os.path.basename(p), ex))
        if not isinstance(d, dict):
            raise RuntimeError("отчёт %s: ожидается объект JSON" % os.path.basename(p))
        entries = d.get("items", d.get("findings"))
        if not isinstance(entries, list):
            raise RuntimeError("отчёт %s: нет обязательного поля items/findings" % os.path.basename(p))
        for it in entries:
            if not isinstance(it, dict):
                raise RuntimeError("отчёт %s: items/findings должен содержать объекты" % os.path.basename(p))
            it["_from"] = os.path.basename(p)
            items.append(it)
        clean_pages = d.get("clean_pages", [])
        if not isinstance(clean_pages, list):
            raise RuntimeError("отчёт %s: clean_pages должен быть списком" % os.path.basename(p))
        clean += clean_pages
    verdicts = {}
    for pattern in ("fix-out-*.json", "style-fix-out-*.json"):
        for p in sorted(glob.glob(toolkit.area(root, AUDIT, pattern))):
            base = os.path.basename(p)
            if base.endswith("fix-out.json"):
                continue
            try:
                d = json.load(io.open(p, encoding="utf-8"))
            except (OSError, UnicodeError, ValueError) as ex:
                raise RuntimeError("отчёт %s не прочитан: %s" % (base, ex))
            if not isinstance(d, dict) or not isinstance(d.get("items"), list):
                raise RuntimeError("отчёт %s: нет обязательного поля items" % base)
            for it in d["items"]:
                if not isinstance(it, dict):
                    raise RuntimeError("отчёт %s: items должен содержать объекты" % base)
                verdicts[(it.get("file"), it.get("line"))] = it.get("decision")
    return items, sorted(set(clean)), verdicts, found


def build_report(root, date, pages, scope_why, mach, items, clean, verdicts, sources, prose=((), ())):
    L = []
    L.append("# Проход lint, %s" % date)
    L.append("")
    L.append("Режим — `_toolkit/SCHEMA.md`, «Режим lint: LLM проходит по вики»; процедура — `_toolkit/lint-pass.md`.")
    L.append("")
    L.append("## Объём прохода")
    L.append("")
    L.append("- Основание списка: %s — **%d страниц**." % (scope_why, len(pages)))
    L.append("- Копии источников (`wiki/sources/`) и сгенерированные страницы в объём не входят.")
    L.append("")
    L.append("## Машинная половина")
    L.append("")
    L.append("- Линтер: **%d проблем**, разделов %d; §45 (язык смысловых ячеек) — **%d**."
             % (mach["total"], mach["sections"], mach["s45"]))
    if mach.get("links"):
        L.append("- Гниль внешних адресов (`%s`): живых %d, заблокированных для curl %d, мёртвых %d."
                 % (mach["links"]["path"], mach["links"]["alive"], mach["links"]["blocked"], mach["links"]["dead"]))
    if mach["sieve"]:
        L.append("- Замер стиля (`%s`), сырых срабатываний сита до человеческого разбора: %s."
                 % (mach["sieve_path"], ", ".join("%s %d" % (k, v) for k, v in mach["sieve"].items())))
    L.append("")
    L.append("## Находки глазных проверок")
    L.append("")
    if not items:
        L.append("Отчётов подагентов не найдено — глазная часть не выполнена (см. порядок в `_toolkit/lint-pass.md`).")
    by_kind = {}
    for it in items:
        by_kind.setdefault(it.get("kind", "другое"), []).append(it)
    for kind, title in CHECKS.items():
        group = by_kind.get(kind)
        if not group:
            continue
        L.append("### %s — %d" % (title, len(group)))
        L.append("")
        for it in group:
            v = verdicts.get((it.get("file"), it.get("line")))
            mark = {"apply": "→ исправлено", "reject": "→ отклонено проверкой"}.get(v, "→ ждёт вердикта")
            L.append("- `%s:%s` %s — %s" % (it.get("file"), it.get("line"), mark,
                                            (it.get("quote") or "").strip()[:120]))
            if it.get("why"):
                L.append("  почему: %s" % str(it["why"]).strip()[:300])
        L.append("")
    unknown = [k for k in by_kind if k not in CHECKS]
    if unknown:
        L.append("### Прочие находки — %d" % sum(len(by_kind[k]) for k in unknown))
        L.append("")
        for k in unknown:
            for it in by_kind[k]:
                L.append("- `%s:%s` [%s] %s" % (it.get("file"), it.get("line"), k,
                                                (it.get("quote") or "")[:120]))
        L.append("")
    L.append("## Чистые страницы (%d)" % len(clean))
    L.append("")
    L.append(", ".join("`%s`" % c for c in clean) if clean else "не названы")
    L.append("")
    L.append("## Отчёты, из которых собран проход")
    L.append("")
    for p in sources:
        L.append("- `%s`" % os.path.relpath(p, root).replace("\\", "/"))
    L.append("")
    prose_added, prose_skipped = prose
    if prose_added or prose_skipped:
        L.append("## Кандидаты из прозы — в очередь решений владельца")
        L.append("")
        L.append("Находки проверки 3 попадают в очередь автоматически: `_staging/prose-candidates.tsv`, "
                 "решения — в дашборде (`_staging/dashboard.html`, панель «Кандидаты в страницы»).")
        L.append("")
        for key, term, where in prose_added:
            L.append("- добавлен кандидат «%s» (%s)" % (term, where))
        for who, why in prose_skipped:
            L.append("- пропущен %s: %s" % (who, why))
        L.append("")
    L.append("## Чего проход не сделал")
    L.append("")
    L.append("- Не правил отправленное и слепки; история не переписывалась.")
    L.append("- Не заменяет машинный линтер: гейтом в `pre-commit.audit` и `tasks.py check` остаётся он.")
    L.append("- Гниль внешних адресов проверяется отдельно (`tasks.py links`), в гейты не входит.")
    L.append("")
    L.append(TAIL_MARK)
    L.append("")
    return "\n".join(L)


def prose_to_queue(root):
    """Собрать находки проверки 3 (концепт без страницы) в реестр кандидатов, которым владеет владелец."""
    try:
        sys.path.insert(0, toolkit.area(root))
        import candidates as cand
    except ImportError as ex:
        return [], [("кандидаты", "модуль счёта недоступен: %s" % str(ex)[:80])]
    reports = sorted(glob.glob(toolkit.area(root, AUDIT, "lint-eye-*.json"))) + \
              sorted(glob.glob(toolkit.area(root, AUDIT, "prose-concepts-*.json")))
    reports = [p for p in reports if "parts" not in os.path.basename(p) and "merged" not in os.path.basename(p)]
    for report in reports:
        try:
            data = json.load(io.open(report, encoding="utf-8"))
        except (OSError, UnicodeError, ValueError) as ex:
            raise RuntimeError("отчёт %s не прочитан: %s" % (os.path.basename(report), ex))
        if not isinstance(data, dict) or not isinstance(data.get("items", data.get("findings")), list):
            raise RuntimeError("отчёт %s: нет обязательного поля items/findings" % os.path.basename(report))
    vault = toolkit.wiki(root)
    return cand.from_lint(vault, root, reports)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wiki", default=".")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--date", default=__import__("datetime").date.today().isoformat())
    args = ap.parse_args()
    root = args.wiki
    try:
        pages, why = scope(root, args.all)
        mach = machine(root)
        items, clean, verdicts, sources = child_reports(root)
        # Кандидаты, найденные прозой страниц (проверка 3), сразу попадают в очередь решений владельца:
        # их не видит счёт по карточкам, поэтому без этого шага находка проверки 3 умирает в отчёте.
        prose_added, prose_skipped = prose_to_queue(root)
    except (OSError, RuntimeError, ValueError, KeyError, TypeError, IndexError, AttributeError) as ex:
        print("lint-pass остановлен: %s" % ex, file=sys.stderr)
        return 1
    if not pages:
        # Проход может идти в тот же день, что и предыдущий: тогда список изменённых пуст,
        # а объём надо брать из того, что подагенты действительно прочитали.
        seen = []
        for c in glob.glob(toolkit.area(root, AUDIT, "lint-eye-*.json")) + \
                 glob.glob(toolkit.area(root, AUDIT, "style-read-*.json")):
            try:
                d = json.load(io.open(c, encoding="utf-8"))
            except (OSError, UnicodeError, ValueError) as ex:
                print("lint-pass остановлен: отчёт %s не прочитан: %s" % (os.path.basename(c), ex), file=sys.stderr)
                return 1
            for pg in (d.get("pages_read") or []):
                pg = str(pg).strip().strip("`")
                pg = pg[5:] if pg.startswith("wiki/") else pg
                if pg.endswith(".md") and pg not in seen:
                    seen.append(pg)
        if seen:
            pages = sorted(seen)
            why = "по отчётам подагентов (список изменившихся с прошлого прохода пуст — проходы в один день)"
    report = build_report(root, args.date, pages, why, mach, items, clean, verdicts, sources,
                          prose=(prose_added, prose_skipped))
    path = toolkit.area(root, AUDIT, "lint-pass-%s.md" % args.date)
    print("объём: %d страниц (%s)" % (len(pages), why))
    print("машина: линтер %d проблем, §45 %d" % (mach["total"], mach["s45"]))
    print("находок от подагентов: %d, чистых страниц: %d" % (len(items), len(clean)))
    if prose_added:
        print("в очередь кандидатов добавлено из прозы: %d" % len(prose_added))
    for p in pages:
        print("   ·", p)
    if args.write:
        # сохранить ручную часть прежнего отчёта (всё ниже маркера)
        tail = ""
        if os.path.exists(path):
            prev = read(path)
            k = prev.find(TAIL_MARK)
            if k >= 0:
                body = prev[k + len(TAIL_MARK):].strip("\n")
                if body:
                    tail = body + "\n"
        write(path, report + tail)
        print("отчёт: %s" % os.path.relpath(path, root).replace("\\", "/"))
    else:
        print("(сухой прогон; для записи — --write)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
