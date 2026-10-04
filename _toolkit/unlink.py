#!/usr/bin/env python3
"""Сбор следов перед удалением страницы: только чтение, ничего не меняет.

Шесть пунктов порядка удаления живут в `SCHEMA.md` («Удаление и архивация») и выполняются руками:
удаление — решение владельца, а не действие скрипта. Этот скрипт закрывает шаги 1–2: перечисляет,
что именно уйдёт вместе со страницей, и собирает следы слага по дереву — чтобы «заодно» не случалось,
а забытая строка не осталась висеть.

    python3 _toolkit/unlink.py <слаг> [--wiki .]
    python3 _toolkit/tasks.py unlink <слаг>

Ничего не удаляет, не правит `index.md`, не трогает копии источников и не запускает генераторы.
Датированные слепки и выгрузки в отчёт попадают отдельной строкой: их не правим (шаг 5).
"""
import argparse
import collections
import os
import re
import subprocess
import sys

import toolkit

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fmparse

PAGE_DIRS = ("entities", "concepts", "comparisons", "queries")
SNAPSHOT_PATH = re.compile(r"(_staging/audit/|_staging/handover/)")
DATED_NAME = re.compile(r"\d{4}-\d{2}-\d{2}")
SERVICE_EXT = (".json", ".tsv", ".html", ".csv", ".zip")
# сгенерированное: править руками нельзя, пересобирается (шаг 4)
GENERATED = {
    "wiki/_meta/quality-metrics.md": ["wiki_metrics.py --write"],
    "wiki/_meta/corpus-lineage.md": ["registries.py"],
    "_staging/cards-registry.json": ["registries.py"],
    "_staging/claims-registry.tsv": ["registries.py"],
    "_staging/bridge.md": ["bridge.py generate"],
    "wiki/_meta/source-registry.md": ["update_source_registry.py"],
}


def git(root, *args):
    r = subprocess.run(["git", "-c", "core.quotepath=false", *args], cwd=root,
                       capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    no_match = args and args[0] == "grep" and r.returncode == 1
    if r.returncode != 0 and not no_match:
        detail = (r.stderr or r.stdout).strip() or f"код {r.returncode}"
        raise RuntimeError(f"git {' '.join(args)} завершился с ошибкой: {detail}")
    return r.stdout


def grep(root, needle):
    """Строки отслеживаемых файлов, где встречается литерал (слепки тоже — их покажем отдельно)."""
    out = git(root, "grep", "-n", "-I", "--fixed-strings", "-e", needle)
    rows = []
    for line in out.split("\n"):
        if not line.strip():
            continue
        parts = line.split(":", 2)
        if len(parts) == 3:
            rows.append((parts[0], parts[1], parts[2].strip()))
    return rows


def bucket_of(path, page_path):
    if path in ("log.md",):
        return "журнал"
    if path.startswith("wiki/sources/"):
        return "копии источников"          # их правит генератор, а не рука (шаг 4)
    if path.startswith("raw/"):
        return "мастер raw/ (задним числом не переписываем)"
    if path == page_path:
        return "сама страница"
    if SNAPSHOT_PATH.search(path) or path.endswith(SERVICE_EXT) or (
            DATED_NAME.search(os.path.basename(path)) and path.startswith("_staging/")):
        return "слепки и выгрузки (не правим)"
    if path.startswith("wiki/_meta/"):
        return "служебные страницы _meta (генерируются)"
    if path.startswith("wiki/"):
        return "страницы вики и index"
    return "служебные документы"


def find_page(root, slug):
    for d in PAGE_DIRS:
        p = toolkit.wiki(root, d, slug + ".md")
        if os.path.exists(p):
            return f"wiki/{d}/{slug}.md"
    return None


def pages_index(root):
    """slug -> объявленные источники."""
    idx = {}
    for d in PAGE_DIRS:
        full = toolkit.wiki(root, d)
        if not os.path.isdir(full):
            continue
        for fn in sorted(os.listdir(full)):
            if not fn.endswith(".md"):
                continue
            text = open(os.path.join(full, fn), encoding="utf-8", errors="ignore").read()
            idx[fn[:-3]] = fmparse.items(text, "sources")
    return idx


def incoming(root, slug):
    """Входящие ссылки на страницу: файл:строка, без датированных слепков."""
    rx = re.compile(r"\[\[" + re.escape(slug) + r"(?:\||#|\]\])")
    hits = []
    for base, dirs, files in os.walk(toolkit.wiki(root)):
        dirs[:] = [d for d in dirs if d not in (".obsidian", "images")]
        for fn in files:
            if not fn.endswith(".md"):
                continue
            p = os.path.join(base, fn)
            rel = os.path.relpath(p, root).replace(os.sep, "/")
            for i, line in enumerate(open(p, encoding="utf-8", errors="ignore"), 1):
                if rx.search(line):
                    hits.append((rel, i, line.strip()))
    return hits


def single_home_lines(root, page_rel):
    """Абзацы страницы, которых нет больше нигде в вики — кандидаты на шаг 3 (единственный носитель).

    Сравниваем по «устойчивому началу» абзаца: текст в вики переносится по-разному, поэтому точное
    совпадение строк не работает. Это подсказка для глаз, а не приговор: список идёт владельцу.
    """
    def norm(s):
        return re.sub(r"[\[\]`*>#|]|https?://\S+|\s+", " ", s).strip().lower()

    text = open(os.path.join(root, page_rel), encoding="utf-8", errors="ignore").read()
    body = re.split(r"(?m)^---\s*$", text, maxsplit=2)[-1]
    paras = [p.strip() for p in re.split(r"\n\s*\n", body)]
    mine = [p for p in paras if len(norm(p)) >= 80 and not p.lstrip().startswith(("#", "|", "-", ">"))]

    others = []
    for base, dirs, files in os.walk(toolkit.wiki(root)):
        dirs[:] = [d for d in dirs if d not in (".obsidian", "images")]
        for fn in files:
            if not fn.endswith(".md"):
                continue
            rel = os.path.relpath(os.path.join(base, fn), root).replace(os.sep, "/")
            if rel == page_rel:
                continue
            others.append(norm(open(os.path.join(base, fn), encoding="utf-8", errors="ignore").read()))
    haystack = " ".join(others)
    out, seen = [], set()
    for p in mine:
        key = norm(p)[:60]
        if key and key not in haystack and key not in seen:
            seen.add(key)
            out.append(re.sub(r"\s+", " ", p))
    return out


def checklist(root):
    """Шесть пунктов порядка — читаем из SCHEMA.md, чтобы не держать вторую копию правила."""
    path = toolkit.script("SCHEMA.md", root)
    if not os.path.exists(path):
        return []
    text = open(path, encoding="utf-8", errors="ignore").read()
    m = re.search(r"## Удаление и архивация(.*?)(?:\n## |\Z)", text, re.DOTALL)
    if not m:
        return []
    sec = m.group(1)
    i = sec.find("Порядок удаления:")
    if i < 0:
        return []
    return [l.strip() for l in sec[i:].split("\n") if re.match(r"^\d+\.\s", l.strip())]


def main():
    ap = argparse.ArgumentParser(description="Сбор следов перед удалением страницы (только чтение).")
    ap.add_argument("slug", help="слаг страницы или источника")
    ap.add_argument("--wiki", default=".")
    a = ap.parse_args()
    root, slug = os.path.abspath(a.wiki), a.slug.strip()
    page = find_page(root, slug)

    print(f"следы слага «{slug}» — сбор, ничего не меняется")
    print("")
    # 1. что убираем
    if page:
        text = open(os.path.join(root, page), encoding="utf-8", errors="ignore").read()
        print(f"1. Страница: {page} ({len(text.splitlines())} строк)")
        last = git(root, "log", "-1", "--format=%h %ad %s", "--date=short", "--", page).strip()
        print(f"   последний коммит: {last or '— (файла нет в истории)'}")
    else:
        print(f"1. Страницы с таким слагом нет в wiki/{'/'.join(PAGE_DIRS)}/ — возможно, это источник:")
        for where in ("raw", "wiki/sources/raw"):
            found = [os.path.relpath(os.path.join(b, f), root).replace(os.sep, "/")
                     for b, _d, fs in os.walk(os.path.join(root, where))
                     for f in fs if f == slug + ".md"]
            print(f"   {'мастер' if where == 'raw' else 'копия в хранилище'}: {found[0] if found else 'не найдено'}")
        print("   (мастер в raw/ и копия в wiki/sources/raw/ удаляются вместе — шаг 6)")
    # файлы, чей ПУТЬ содержит слаг: git grep ищет содержимое и такие не находит
    by_name = [x for x in git(root, "ls-files").split("\n") if slug in x]
    if by_name:
        print("   файлы, у которых слаг в имени (их и убираем):")
        for x in by_name:
            kind = ("мастер raw/" if x.startswith("raw/") else
                    "копия в хранилище" if x.startswith("wiki/sources/") else
                    "карточка" if x.startswith("_staging/cards/") else "прочее")
            print(f"     - {x}  [{kind}]")

    rows = grep(root, slug)
    buckets = collections.defaultdict(list)
    for path, line, text in rows:
        buckets[bucket_of(path, page)].append((path, line, text))
    for name, items in buckets.items():
        print(f"   {name}: {len(items)} упоминаний")

    # 2. строка в index.md
    idx_row = [r for r in buckets.get("страницы вики и index", []) if r[0] == "wiki/index.md"]
    print("")
    print(f"2. index.md: " + (f"строка есть — {idx_row[0][2][:110]}" if idx_row else "строки нет"))

    # 3. входящие ссылки и прочие места
    inc_all = [r for r in incoming(root, slug) if r[0] != page]
    inc = [r for r in inc_all if not r[0].startswith("wiki/sources/")]
    copies_hits = [r for r in inc_all if r[0].startswith("wiki/sources/")]
    print("")
    print(f"3. Входящие ссылки из страниц вики: {len(inc)} (и {len(copies_hits)} из копий источников — см. п. 4)")
    for path, line, text in inc[:25]:
        print(f"   {path}:{line}  {text[:110]}")
    if len(inc) > 25:
        print(f"   … ещё {len(inc) - 25}")

    # 4. копии источников
    copies = buckets.get("копии источников", [])
    print("")
    print(f"4. Копии источников с этим слагом в разделе «Где использован»: {len(copies)}")
    if copies:
        print("   пересобираются целиком: python3 _toolkit/source_backlinks.py --write")

    # 5. сгенерированное
    print("")
    print("5. Сгенерированное (руками не правим, пересобираем):")
    rerun = set()
    for rel, cmds in GENERATED.items():
        p = os.path.join(root, rel)
        if os.path.exists(p) and slug in open(p, encoding="utf-8", errors="ignore").read():
            print(f"   {rel} упоминает слаг → {'; '.join(cmds)}")
            rerun.update(cmds)
    if not any(os.path.exists(os.path.join(root, r)) and slug in open(os.path.join(root, r), encoding="utf-8", errors="ignore").read() for r in GENERATED):
        print("   слаг в сгенерированных файлах не значится")
    rerun.update(["align_tables.py --write"])

    # 6. осиротевшие
    print("")
    if page:
        pidx = pages_index(root)
        mine = pidx.get(slug, [])
        only = [s for s in mine if not any(s in v for k, v in pidx.items() if k != slug)]
        print(f"6. Осиротеют вместе со страницей:")
        print(f"   источников объявлено страницей: {len(mine)}, из них объявлены только ею: {len(only)}")
        for s in only:
            print(f"     ! {s} — копия скажет «ни одна страница не объявляет этот источник»")
        # страницы, чья единственная входящая ссылка — из удаляемой страницы
        out_links = sorted(set(re.findall(r"\[\[([^\]\|#]+)", open(os.path.join(root, page), encoding="utf-8", errors="ignore").read())))
        losing = []
        for target in out_links:
            others = [h for h in incoming(root, target) if h[0] != page]
            if not others and target in pidx:
                losing.append(target)
        print(f"   страниц вики, чья единственная входящая ссылка была отсюда: {len(losing)}")
        for t in losing:
            print(f"     ! {t} — станет сиротой (линтер §2)")
        # шаг 3: кандидаты «факт на единственном носителе»
        solo = single_home_lines(root, page)
        print("")
        print(f"   шаг 3 — абзацы, которых больше нигде в вики нет (кандидаты на вопрос владельцу): {len(solo)}")
        for l in solo[:12]:
            print(f"     · {l[:120]}")
        if len(solo) > 12:
            print(f"     … ещё {len(solo) - 12}")

    print("")
    print("Порядок удаления (SCHEMA.md, «Удаление и архивация»):")
    for line in checklist(root):
        print("  " + line)
    print("")
    print("Что пересобрать после правки: " + ", ".join(sorted(rerun)))
    print("Скрипт ничего не удалял: удаление — решение владельца, шаги 1–6 выполняются руками.")
    return 0 if page else 1


if __name__ == "__main__":
    raise SystemExit(main())
