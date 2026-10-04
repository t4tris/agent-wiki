#!/usr/bin/env python3
"""Строки реестра инструментов. Проверка §49 линтера.

Реестр инструментов собирают из упоминаний, и упоминание легко принять за инструмент: волна инжеста
14 сентября занесла в колонку «Инструмент» людей (Karpathy, Joel Hooks как «авторов»), компанию
(Anthropic), методы (GRPO, DbC), файл-конвенцию (AGENTS.md) и общие слова (CLI, repos). Форма таблицы
при этом была цела, поэтому проверка смотрит на смысл строки:

1. у таблицы реестра есть колонка типа, и значение в ней — из закрытого набора;
2. строка не называется именем человека или компании, у которых есть страница вики;
3. в таблице нет колонок-счётчиков («Заметок») — числа считает машина.

    python3 _toolkit/registry_rows.py --wiki .
"""
import argparse
import glob
import io
import os
import re
import sys

import toolkit

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fmparse

PAGE = os.path.join("comparisons", "tools-registry.md")
PAGE_DISPLAY = "/".join(("wiki", "comparisons", "tools-registry.md"))
KINDS = {"CLI", "библиотека", "фреймворк", "платформа", "сервис", "MCP-сервер", "плагин",
         "приложение", "репозиторий кода"}
TYPE_COL = re.compile(r"(?i)^тип")
DESC_COL = re.compile(r"(?i)^что даёт")
# Формальная справка вместо сути. Владелец 2026-09-17: «в tool-registry обнаружил запись ElKornacio/agent-plugins —
# GitHub-репозиторий с плагинами, куда залит Smoke Break — это описание формальное и не отображает сути/пользы,
# за которую эта запись была добавлена владельцем». Колонка называется «Что даёт», и строка обязана это сказать.
BOILER = re.compile(r"(?i)^(GitHub-репозиторий|содержание записи не пояснено|один из инструментов|источник материалов|репозиторий с )")
MIN_DESC = 45
COUNTER_COL = re.compile(r"(?i)^(заметок|упоминаний|строк|раз)$")
# Страницы-сущности о людях и компаниях: имя строки реестра не может с ними совпадать.
PEOPLE_HINT = re.compile(r"(?i)^(anthropic|openai|google|microsoft|vercel|karpathy|joel hooks)$")


def read(path):
    return io.open(path, encoding="utf-8", errors="replace").read()


def person_and_company_slugs(root):
    """Люди и компании, у которых есть страница: определяются по тегам и заголовкам страниц."""
    names = set()
    for path in glob.glob(toolkit.wiki(root, "entities", "*.md")) + \
            glob.glob(toolkit.wiki(root, "concepts", "*.md")):
        text = read(path)
        head = text[:1200]
        title = (re.search(r"(?m)^title:\s*\"?(.+?)\"?\s*$", head) or [None, ""])[1]
        tags = ", ".join(fmparse.items(text, "tags"))
        if re.search(r"(?i)\b(автор|инженер-практик|практик|мейнтейнер|соавтор)\b", title) or \
                re.search(r"company|opinion|methodology/manifesto", tags):
            names.add(os.path.basename(path)[:-3].replace("-", " "))
            if title:
                names.add(title.split(":")[0].strip().lower())
    return names


def issues(root):
    path = toolkit.wiki(root, PAGE)
    if not os.path.exists(path):
        if not _declared(root, "tools"):
            return []
        return ["нет страницы %s — строки реестра проверять нечем" % PAGE_DISPLAY]
    lines = read(path).split("\n")
    known = person_and_company_slugs(root)
    out = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.startswith("|"):
            i += 1
            continue
        head = [c.strip("* ").lower() for c in line.strip().strip("|").split("|")]
        rows, j = [], i + 1
        while j < len(lines) and lines[j].startswith("|"):
            cells = [c.strip() for c in lines[j].strip().strip("|").split("|")]
            if not all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
                rows.append((j + 1, cells))
            j += 1
        if any(COUNTER_COL.match(h) for h in head):
            out.append("строка %d: в реестре колонка-счётчик «%s» — числа считает машина"
                       % (i + 1, [h for h in head if COUNTER_COL.match(h)][0]))
        tcol = next((k for k, h in enumerate(head) if TYPE_COL.match(h)), None)
        dcol = next((k for k, h in enumerate(head) if DESC_COL.match(h)), None)
        first = next((k for k, h in enumerate(head) if h and not COUNTER_COL.match(h)), 0)
        if tcol is None:
            out.append("строка %d: у таблицы реестра нет колонки типа со словарём: %s"
                       % (i + 1, " | ".join(head)[:80]))
        for ln, cells in rows:
            if not cells or not cells[0]:
                continue
            name = re.sub(r"\[\[[^\]]*\|?([^\]]*)\]\]", r"\1", cells[0]).strip()
            if tcol is not None and tcol < len(cells):
                kind = cells[tcol].strip()
                if kind and kind not in KINDS:
                    out.append("строка %d: «%s» — тип «%s» вне набора реестра"
                               % (ln, name[:40], kind[:30]))
            if dcol is not None and dcol < len(cells):
                desc = cells[dcol].strip()
                if desc and len(desc) < MIN_DESC:
                    out.append("строка %d: «%s» — описание короче %d знаков, сути в нём нет: колонка называется «Что даёт»"
                               % (ln, name[:40], MIN_DESC))
                elif desc and BOILER.match(desc):
                    out.append("строка %d: «%s» — формальная справка вместо сути («%s»): напиши, что инструмент даёт и зачем он в реестре"
                               % (ln, name[:40], desc[:44]))
            low = name.lower()
            if low in known or PEOPLE_HINT.match(low):
                out.append("строка %d: «%s» — человек или компания, а не инструмент: её место — "
                           "страница-сущность" % (ln, name[:40]))
        i = j
    return out


def main():
    ap = argparse.ArgumentParser(description="Строки реестра инструментов (проверка §49)")
    ap.add_argument("--wiki", default=".")
    a = ap.parse_args()
    rows = issues(os.path.abspath(a.wiki))
    for r in rows[:40]:
        print(r)
    print("находок: %d" % len(rows))
    return 1 if rows else 0



def _declared(root, key):
    """Объявлен ли этот реестр у экземпляра. Нет объявления домена — проверка не применима, а не «нет файла»:
    у постороннего своя раскладка, и требовать от него наши страницы значит требовать чужих данных."""
    import os as _os
    import sys as _sys
    _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
    import domain
    return key in domain.registers(root)

if __name__ == "__main__":
    sys.exit(main())
