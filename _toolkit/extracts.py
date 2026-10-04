#!/usr/bin/env python3
"""Выжимки источников по страницам: собрать, показать невклеенные, вклеить.

Зачем. Разрыв в пайплайне (назван владельцем 2026-09-17): понятие доводится до решения «страница», но
страница не создаётся сразу, а смысловой проход работает по существующим страницам — значит выжимке из
источника некуда лечь, пока страницы нет, и содержание живёт только в самой записи корпуса. Записи мы
удаляем, поэтому окно между «источник разобран» и «страница создана» обязано быть закреплено.

Правило владельца: выжимка пишется в первом проходе, пока источник открыт (иначе вторая волна перечитывает
источники заново). Здесь лежит её хранилище: `_staging/extracts/<запись>.json` — по блоку на понятие, с
адресом страницы-адресата, разделом и ссылкой на источник. Пока выжимка не вклеена (`applied: false`),
запись корпуса удалять нельзя; это проверяет `check` и сторож §57 линтера.

    python3 _toolkit/extracts.py collect <отчёт.json>     # отчёт ребёнка (kind: extracts) → хранилище
    python3 _toolkit/extracts.py check --wiki .           # что не вклеено и у каких записей
    python3 _toolkit/extracts.py apply --wiki . --write   # вклейка в целевые страницы (без --write — прогон)
    python3 _toolkit/extracts.py annotate --wiki . --source <запись> --write   # блок «Выжимки» в запись
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
import raw_write
import toolkit

STORE = "extracts"
SECTION_TITLE = "Выжимки по страницам"


def read(path):
    with io.open(path, encoding="utf-8", errors="ignore") as f:
        return f.read()


def write(path, text):
    with io.open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)


def resolve_target(root, target):
    """Адрес страницы-адресата: путь или голый слаг (первая волна писала слаг)."""
    target = (target or "").strip()
    if not target:
        return ""
    if target.endswith(".md") and "/" in target:
        return target
    slug = os.path.basename(target).replace(".md", "")
    hits = [p for p in glob.glob(toolkit.wiki(root, "**", slug + ".md"), recursive=True)
            if "/sources/" not in p.replace("\\", "/")]
    if hits:
        return os.path.relpath(hits[0], root).replace("\\", "/")
    return target


def store_path(root, source):
    return toolkit.area(root, STORE, source + ".json")


def load_all(root):
    out = {}
    for p in sorted(glob.glob(toolkit.area(root, STORE, "*.json"))):
        try:
            out[os.path.basename(p)[:-5]] = json.load(io.open(p, encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as e:
            print(f"битый файл выжимок {p}: {e}", file=sys.stderr)
    return out


def cmd_collect(root, report):
    data = json.load(io.open(report, encoding="utf-8"))
    if data.get("kind") != "extracts":
        sys.exit(f"отчёт {report} не того вида: kind={data.get('kind')!r}")
    src = data.get("source")
    if not src:
        sys.exit("в отчёте нет поля source")
    items = []
    for it in data.get("items", []):
        if not (it.get("concept") and it.get("target") and it.get("text")):
            sys.exit(f"выжимка без понятия, адреса или текста: {json.dumps(it, ensure_ascii=False)[:120]}")
        items.append({"concept": it["concept"], "target": it["target"],
                      "section": it.get("section", ""), "text": it["text"],
                      "quote": it.get("quote", ""), "citation": it.get("citation", f"[[{src}]]"),
                      "applied": False})
    os.makedirs(toolkit.area(root, STORE), exist_ok=True)
    write(store_path(root, src), json.dumps({"source": src, "items": items}, ensure_ascii=False, indent=1))
    print(f"выжимок собрано: {len(items)} (источник {src})")


def cmd_check(root):
    """Невклеенные выжимки: ждут адресата. Закрытые (вклеено, отказ с причиной, пустой отчёт) не в счёте."""
    data = load_all(root)
    pending = []
    for src, d in data.items():
        for it in d["items"]:
            if not it.get("applied") and it.get("status") != "refused":
                pending.append((src, it))
    for src, it in pending:
        print(f"  не вклеено: {src} → {it['target']} | понятие «{it['concept']}»")
    print(f"\nисточников с выжимками: {len(data)} | невклеенных выжимок: {len(pending)}")
    return len(pending)


def cmd_apply(root, only, do_write):
    data = load_all(root)
    applied = 0
    for src, d in data.items():
        if only and src != only:
            continue
        for it in d["items"]:
            if it.get("applied"):
                continue
            it["target"] = resolve_target(root, it["target"])
            target = os.path.join(root, it["target"])
            if not os.path.exists(target):
                print(f"  цель ещё не создана: {it['target']} (источник {src}) — ждём фазу создания страниц")
                continue
            if not it.get("section"):
                print(f"  без раздела: {src} → {it['target']} — раздел обязателен, вклейка пропущена")
                continue
            text = read(target)
            bullet = f"- {it['text']} {it['citation']}\n"
            head = re.search(r"(?m)^#{2,4} " + re.escape(it["section"]) + r"\s*$", text)
            if not head:
                print(f"  нет раздела «{it['section']}» в {it['target']} — вклейка пропущена")
                continue
            tail = re.search(r"(?m)^#{2,4} ", text[head.end():])
            at = head.end() + tail.start() if tail else len(text)
            if bullet.strip() in text:
                it["applied"] = True
                continue
            if do_write:
                write(target, text[:at].rstrip("\n") + "\n" + bullet + text[at:].lstrip("\n"))
            it["applied"] = True
            applied += 1
            print(f"  вклеено: {src} → {it['target']} §{it['section']}")
    if do_write and applied:
        subprocess.run([sys.executable, toolkit.script("source_backlinks.py"),
                        "--wiki", root, "--write"], check=False)
        for src, d in data.items():
            if only and src != only:
                continue
            write(store_path(root, src), json.dumps(d, ensure_ascii=False, indent=1))
    print(f"\nвклеек: {applied}{'' if do_write else ' (прогон, без --write ничего не записано)'}")


def annotate_text(text, block):
    match = re.search(r"(?m)^## " + re.escape(SECTION_TITLE) + r"\s*$", text)
    if not match:
        return text.rstrip("\n") + "\n" + block.strip("\n") + "\n"
    rest = text[match.end():]
    next_heading = re.search(r"(?m)^## ", rest)
    tail = rest[next_heading.start():] if next_heading else ""
    result = text[:match.start()] + block.strip("\n") + "\n"
    if tail:
        result += "\n" + tail.lstrip("\n")
    return result


def cmd_annotate(root, source, do_write, force=False, why=""):
    d = load_all(root).get(source)
    if not d:
        sys.exit(f"нет выжимок для {source}")
    body = [f"## {SECTION_TITLE}", ""]
    for it in d["items"]:
        mark = "вклеено" if it.get("applied") else "ждёт страницы"
        body.append(f"- **{it['concept']}** → `{it['target']}`"
                    + (f" §{it['section']}" if it.get("section") else "") + f" — {mark}: {it['text']}")
    block = "\n".join(body) + "\n"
    plans, issues, found = [], [], []
    for rel in (f"raw/telegram/{source}.md", f"wiki/sources/raw/telegram/{source}.md"):
        path = os.path.join(root, rel)
        if not os.path.exists(path):
            continue
        found.append(rel)
        old = read(path)
        new = annotate_text(old, block)
        existing = bool(re.search(r"(?m)^## " + re.escape(SECTION_TITLE) + r"\s*$", old))
        raw_write.plan(path, new, force, why, "блок выжимок", plans, issues,
                       allow_existing=not existing)
    for issue in issues:
        print(issue)
    if issues:
        return 1
    if do_write:
        print(f"блок «{SECTION_TITLE}» для {source}: записано {raw_write.apply(plans)}")
    else:
        print(f"блок «{SECTION_TITLE}» для {source}: " + (", ".join(found) or "запись не найдена")
              + f"; подготовлено {len(plans)} (прогон)")
    return 0


def cmd_mark_applied(root, do_write):
    """Отметить выжимки вклеенными, если их материал уже на целевой странице.

    Проверка идёт по двум вещам, обе машинные: целевая страница существует и называет источник (в поле
    `sources:` или ссылкой). Дословное совпадение формулировки здесь не годится: выжимка — черновая
    формулировка для страницы, а страница к моменту приёмки уже написана своими словами, и первая волна
    (2026-09-17) показала это на восьми понятиях: страницы несли материал, но не буквы выжимки.
    """
    data = load_all(root)
    marked, missing = 0, []
    for src, d in data.items():
        for it in d["items"]:
            if it.get("applied"):
                continue
            target = os.path.join(root, it["target"])
            stem = re.sub(r"[\[\]|]", "", it.get("citation", "")).split("|")[0].strip()
            stem = os.path.basename(stem)[:-3] if stem.endswith(".md") else stem
            if not os.path.exists(target):
                missing.append((src, it["target"], "страница ещё не создана"))
                continue
            page = read(target)
            if stem and (stem in page):
                it["applied"] = True
                marked += 1
            else:
                missing.append((src, it["target"], f"страница не называет источник {stem}"))
    if do_write:
        for src, d in data.items():
            write(store_path(root, src), json.dumps(d, ensure_ascii=False, indent=1))
    print(f"отмечено вклеенными: {marked} | ждут: {len(missing)}")
    for src, target, why in missing[:12]:
        print(f"  {src} → {target}: {why}")


def cmd_index_by_record(root, do_write):
    """Разложить выжимки по записям корпуса: документ каждой записи должен быть вклеен, прежде чем её удалять.

    Выжимки собраны по понятиям (один файл — одно понятие), но удаление идёт по записям корпуса, поэтому
    нужен обратный индекс: здесь каждая выжимка попадает в файл своей записи (адрес берётся из `citation`).
    Его читают `check` и сторож §57 линтера.
    """
    data = load_all(root)
    by_record = {}
    for src, d in data.items():
        for it in d["items"]:
            stem = re.sub(r"[\[\]|]", "", it.get("citation", "")).split("|")[0].strip()
            stem = os.path.basename(stem)[:-3] if stem.endswith(".md") else stem
            if not stem:
                continue
            by_record.setdefault(stem, []).append({"concept": src, "target": it["target"],
                                                   "section": it.get("section", ""),
                                                   "text": it["text"], "applied": bool(it.get("applied"))})
    if do_write:
        out = toolkit.area(root, STORE, "by-record")
        os.makedirs(out, exist_ok=True)
        for stem, items in by_record.items():
            write(os.path.join(out, stem + ".json"), json.dumps({"record": stem, "items": items},
                                                                ensure_ascii=False, indent=1))
    pending = sum(1 for items in by_record.values() for it in items if not it["applied"])
    print(f"записей с выжимками: {len(by_record)} | невклеенных у них: {pending}")


def cmd_resolve(root, source, concept, to, why, do_write):
    """Закрыть выжимку, которой некуда лечь: адрес или отказ.

    Тупик, найденный 2026-09-17: понятие получило решение «отказ», страницы не будет, а выжимка остаётся
    «ждёт страницы» — вклейки нет, удаление записи (§57) заблокировано навсегда. Разрешение фиксируется
    в файле выжимок: `status: applied` (вклеена), `refused` (адресат не появится, причина названа),
    `empty` (запись проверена, переносить нечего).
    """
    data = load_all(root)
    d = data.get(source)
    if not d:
        sys.exit(f"нет выжимок для {source}")
    hit = False
    for it in d["items"]:
        if concept and concept not in it["concept"]:
            continue
        if to == "refusal":
            it["status"] = "refused"
            it["why"] = why or "отказ без причины"
            it["applied"] = True
        elif to == "empty":
            it["status"] = "empty"
            it["applied"] = True
        else:
            it["target"] = resolve_target(root, to)
            it["status"] = "waiting"
            it["applied"] = False
        hit = True
        print(f"  {it['concept']} → {it.get('target','')} ({it.get('status')})")
    if not hit:
        sys.exit(f"в выжимках {source} нет понятия «{concept}»")
    if do_write:
        write(store_path(root, source), json.dumps(d, ensure_ascii=False, indent=1))
    print("выжимок разрешено: 1" if hit else "ничего", "| без --write ничего не записано" if not do_write else "")


def cmd_empty(root, source, why, do_write):
    """Пустой отчёт выжимок: запись проверена, и переносить из неё нечего.

    Зачем отдельная команда, а не отсутствие файла: отсутствие файла означает «разбор не делали», и
    удаление такой записи запрещено (§57). Пустой отчёт означает «смотрели, содержания нет» — и только
    он даёт право удалить запись.
    """
    path = store_path(root, source)
    data = {"source": source, "items": [], "status": "empty", "why": why or "содержания для вики нет"}
    if do_write:
        os.makedirs(toolkit.area(root, STORE), exist_ok=True)
        write(path, json.dumps(data, ensure_ascii=False, indent=1))
    print(f"пустой отчёт выжимок для {source}: {data['why']}" + ("" if do_write else " (прогон)"))


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("collect"); p.add_argument("report"); p.add_argument("--wiki", default=".")
    p = sub.add_parser("check"); p.add_argument("--wiki", default=".")
    p = sub.add_parser("apply"); p.add_argument("--wiki", default="."); p.add_argument("--source")
    p.add_argument("--write", action="store_true")
    p = sub.add_parser("mark-applied"); p.add_argument("--wiki", default="."); p.add_argument("--write", action="store_true")
    p = sub.add_parser("index-by-record"); p.add_argument("--wiki", default="."); p.add_argument("--write", action="store_true")
    p = sub.add_parser("resolve"); p.add_argument("--wiki", default="."); p.add_argument("--source", required=True)
    p.add_argument("--concept", default=""); p.add_argument("--to", required=True); p.add_argument("--why", default="")
    p.add_argument("--write", action="store_true")
    p = sub.add_parser("empty"); p.add_argument("--wiki", default="."); p.add_argument("--source", required=True)
    p.add_argument("--why", default=""); p.add_argument("--write", action="store_true")
    p = sub.add_parser("annotate"); p.add_argument("--wiki", default="."); p.add_argument("--source", required=True)
    p.add_argument("--write", action="store_true")
    p.add_argument("--force", action="store_true")
    p.add_argument("--why", default="")
    args = ap.parse_args()
    if args.cmd == "resolve":
        cmd_resolve(args.wiki, args.source, args.concept, args.to, args.why, args.write)
        return
    if args.cmd == "empty":
        cmd_empty(args.wiki, args.source, args.why, args.write)
        return
    if args.cmd == "mark-applied":
        cmd_mark_applied(args.wiki, args.write)
    elif args.cmd == "index-by-record":
        cmd_index_by_record(args.wiki, args.write)
    if args.cmd == "collect":
        cmd_collect(args.wiki, args.report)
    elif args.cmd == "check":
        cmd_check(args.wiki)
    elif args.cmd == "apply":
        cmd_apply(args.wiki, args.source, args.write)
    elif args.cmd == "annotate":
        if args.force and not args.why.strip():
            ap.error("--force требует --why")
        return cmd_annotate(args.wiki, args.source, args.write, args.force, args.why)


if __name__ == "__main__":
    raise SystemExit(main())
