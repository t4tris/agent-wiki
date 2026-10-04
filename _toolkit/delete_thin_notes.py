#!/usr/bin/env python3
"""Удаление тонких записей корпуса по утверждённому владельцем файлу.

Решение владельца 2026-09-17: «просто удалять их после импорта информации в вики, и ссылки на них тоже
удалять». Здесь исполнение, и оно подчинено двум правилам:

1. **Удаления без утверждения не бывает.** Владелец утверждает записи в HTML-обзоре
   (`_staging/audit/delete-thin-notes-review.html`): галочки плюс кнопка «экспорт в JSON». Этот файл и есть
   разрешение — скрипт читает только его (kind: `delete-thin-notes`).
2. **Запись живёт, пока её выжимки не легли в страницы.** Если у записи есть выжимка со `applied: false`
   (`_staging/extracts/<запись>.json`), удаление запрещено — иначе вместе с записью уйдёт содержание, которого
   на страницах ещё нет. Именно эту дыру назвал владелец 2026-09-17.

Ссылки на запись в вики — по решению владельца — удаляются вместе с ней. Замена берётся из плана
(`_staging/delete-thin-notes.tsv`): адрес источника (ссылка на месте упоминания) либо строка
«сообщение id, канал, дата». Из полей `sources:` запись убирается: там живут пути к файлам корпуса, а файла
больше нет.

    python3 _toolkit/delete_thin_notes.py --wiki . --approval <утверждение.json>            # прогон
    python3 _toolkit/delete_thin_notes.py --wiki . --approval <утверждение.json> --write    # исполнение
"""
import argparse
import glob
import io
import json
import os
import re
import sys

import toolkit

PLAN = "delete-thin-notes.tsv"
STORE = "extracts"


def read(path):
    with io.open(path, encoding="utf-8", errors="ignore") as f:
        return f.read()


def write(path, text):
    with io.open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)


def plan_rows(root):
    p = toolkit.area(root, PLAN)
    if not os.path.exists(p):
        sys.exit(f"нет плана удаления {p}")
    rows = {}
    for line in read(p).rstrip("\n").split("\n")[1:]:
        f = line.split("\t")
        if len(f) < 5:
            continue
        rows[f[0]] = {"channel": f[1], "date": f[2], "id": f[3], "replace": f[4]}
    return rows


def unapplied(root, stem):
    """Сколько выжимок записи ещё не закрыто. Отсутствие файла — это не «ноль», а «разбора не было».

    Правило 2026-09-17: запись удаляется, только когда её содержание разобрано — выжимки собраны
    (`extracts.py collect`) и вклеены или закрыты отказом (`extracts.py resolve`). Отсутствие файла
    означает «разбор не делали», и до 2026-09-17 это молча разрешало удаление: из 42 кандидатов 38 были
    удаляемы именно потому, что выжимок у них не было вовсе.
    """
    p = toolkit.area(root, STORE, stem + ".json")
    if not os.path.exists(p):
        return -2
    try:
        d = json.load(io.open(p, encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return -1
    return sum(1 for it in d.get("items", []) if not it.get("applied"))


def replacement(row):
    """Чем заменить ссылку на месте упоминания: адрес или строка «сообщение id, канал, дата»."""
    r = (row.get("replace") or "").strip()
    if r.startswith("http"):
        return r
    return f"сообщение {row['id']}, {row['channel']}, {row['date']}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wiki", required=True)
    ap.add_argument("--approval", required=True)
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()
    root = args.wiki
    if not os.path.exists(args.approval):
        sys.exit(f"нет файла утверждения {args.approval}")
    data = json.load(io.open(args.approval, encoding="utf-8"))
    if data.get("kind") != "delete-thin-notes":
        sys.exit(f"файл не того вида: kind={data.get('kind')!r}")
    approved = [it for it in data.get("items", []) if it.get("approved")]
    plan = plan_rows(root)
    print(f"утверждено записей: {len(approved)} из {len(data.get('items', []))}")

    blocked, unknown = [], []
    for it in approved:
        stem = it["stem"]
        if stem not in plan:
            unknown.append(stem)
            continue
        n = unapplied(root, stem)
        if n == -2:
            blocked.append((stem, "разбора содержания не было: соберите выжимки (extracts.py collect) "
                                   "или подтвердите пустоту (extracts.py empty)"))
        elif n == -1:
            blocked.append((stem, "файл выжимок повреждён — правило «выжимки в страницах» проверить нельзя"))
        elif n > 0:
            blocked.append((stem, f"невклеенных выжимок {n}"))
    if unknown:
        sys.exit("в утверждении есть записи, которых нет в плане: " + ", ".join(unknown))
    if blocked:
        print("\nудаление запрещено по правилу «запись живёт, пока выжимки не в страницах»:")
        for stem, why in blocked:
            print(f"   {stem} — {why}")
        sys.exit("сначала вклейте выжимки (`extracts.py apply --write`) или соберите их (`extracts.py collect`)")

    pages = [p for p in glob.glob(toolkit.wiki(root, "**", "*.md"), recursive=True)]
    links_total, sources_total, deleted = 0, 0, []
    for it in approved:
        stem = it["stem"]
        row = plan[stem]
        repl = replacement(row)
        n_links = n_src = 0
        for p in pages:
            text = read(p)
            orig = text
            # 1) поля sources: — запись убирается целиком, файла корпуса больше нет
            def strip_src(m, stem=stem):
                items = [x.strip() for x in m.group(1).split(",") if x.strip()]
                kept = [x for x in items if f"/{stem}.md" not in x and stem not in os.path.basename(x)]
                return "sources: [" + ", ".join(kept) + "]" if len(kept) != len(items) else m.group(0)
            before = text
            text = re.sub(r"(?m)^sources: \[(.*?)\]$", strip_src, text)
            if text != before:
                n_src += 1
            # 2) ссылки в тексте — заменяются адресом либо строкой «сообщение …»
            def sub_link(m, repl=repl):
                return repl
            before = text
            text = re.sub(r"\[\[" + re.escape(stem) + r"(\|[^\]]*)?\]\]", sub_link, text)
            n_links += len(re.findall(r"\[\[" + re.escape(stem) + r"(\|[^\]]*)?\]\]", before))
            if args.write and text != orig:
                write(p, text)
        links_total += n_links
        sources_total += n_src
        removed = []
        for rel in (f"raw/telegram/{stem}.md", f"wiki/sources/raw/telegram/{stem}.md"):
            p = os.path.join(root, rel)
            if os.path.exists(p):
                if args.write:
                    os.remove(p)
                removed.append(rel)
        deleted.append((stem, n_links, n_src, removed))
        print(f"   {stem}: ссылок заменено {n_links}, полей sources затронуто {n_src}, "
              f"файлов к удалению {len(removed)}")

    # карточка источника в реестре карточек
    reg = toolkit.area(root, "cards-registry.json")
    if os.path.exists(reg):
        d = json.load(io.open(reg, encoding="utf-8"))
        before = len(d.get("cards", []))
        kept = [c for c in d["cards"] if c.get("id") not in {s for s, *_ in deleted}]
        if len(kept) != before:
            if args.write:
                d["cards"] = kept
                d.setdefault("summary", {})["cards_total"] = len(kept)
                write(reg, json.dumps(d, ensure_ascii=False, indent=1))
            print(f"   карточек к удалению: {before - len(kept)}")

    print(f"\nвсего записей: {len(approved)} | ссылок заменено: {links_total} | полей sources затронуто: {sources_total}")
    if not args.write:
        print("это прогон: ни один файл не удалён и не изменён (для исполнения добавьте --write)")
    else:
        log = toolkit.area(root, f"delete-log-{__import__('datetime').date.today().isoformat()}.tsv")
        lines = ["запись\tссылок заменено\tполей sources\tудалено файлов"]
        for stem, nl, ns, rem in deleted:
            lines.append(f"{stem}\t{nl}\t{ns}\t{' + '.join(rem)}")
        write(log, "\n".join(lines) + "\n")
        print(f"журнал удаления: {log}")


if __name__ == "__main__":
    main()
