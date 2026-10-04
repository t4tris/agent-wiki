#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Мутационная аттестация чекеров вики на канарейках.

Что делает
----------
1. Копирует корень проекта во временный каталог (shutil.copytree), игнорируя
   .git и .obsidian. Все мутации вносятся ТОЛЬКО в копию; живой проект не меняется.
2. По очереди вносит одну мутацию-«канарейку», запускает чекер с --wiki <копия>,
   определяет, поймана ли мутация именно тем чекером, который должен был её
   поймать, и восстанавливает файл из исходного содержимого.
3. Печатает таблицу «канарейка → ожидаемый чекер → поймана/нет» и Mutation Score.
4. Пишет отчёт _staging/audit/canary-report-<дата>-<ЧЧММ>.md и возвращает код 1,
   если поймано меньше 8 из 10 канареек.

Запуск: python3 _toolkit/canary_test.py [--wiki .] [--keep]

Кроме 10 канареек задания есть 3 безвредных правки (№11–13): они проверяют
специфичность — хороший чекер обязан на них молчать. Если канарейка не ловится,
скрипт НЕ подгоняет чекер: это честно фиксируется как дыра в отчёте.
"""
import argparse
import atexit
import collections
import datetime
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import sys as _sys
import tempfile

_sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fmparse as _fmparse

# Граница аттестации: копия делается без .git, поэтому ветка «мостик отстал на N коммитов» канарейкой
# не проверяется (проверку окна в такой копии честнее пропустить, чем выдумывать дефект). Аттестуется
# ветка «окно не записано»; ветка отставания проверена руками на живом репозитории.
# Метка восстановления для канареек, меняющих права файла, а не содержимое: None здесь означал бы
# «удалить файл» (так восстанавливаются канарейки, создававшие файл), а это сломало бы следующие канарейки.
RESTORE_WRITE = "__restore_write__"
RESTORE_MOVE = "<move>"           # канарейка переместила каталог: вернуть на место, а не перезаписывать

TOOLKIT = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(TOOLKIT)
REPORT_DIR = os.path.join(PROJECT_ROOT, "_staging", "audit")
LINT = os.path.join(TOOLKIT, "lint_wiki.py")
VERIFY = os.path.join(TOOLKIT, "verify_numbers.py")
MIN_CAUGHT = 8

class MutationNotApplied(Exception):
    """Целевая строка не найдена — канарейку нельзя считать аттестованной."""

# --------------------------------------------------------------------------- #
# утилиты
# --------------------------------------------------------------------------- #
def read(path):
    # newline="" — сохраняем исходные переводы строк для точного восстановления
    with open(path, encoding="utf-8", newline="") as f:
        return f.read()

def write(path, text):
    try:
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(text)
    except PermissionError:
        # в raw/ и в копиях источников стоит атрибут «только чтение» — в копии проекта это не преграда
        os.chmod(path, 0o666)
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(text)

def rmtree(path):
    """rmtree, снимающий read-only (копии raw/-файлов приходят только для чтения)."""
    def onerr(func, p, exc):
        try:
            os.chmod(p, 0o700)
            func(p)
        except OSError as error:
            print("canary: не удалось удалить %s: %s" % (p, error), file=sys.stderr)
    shutil.rmtree(path, onerror=onerr)

def _cmap_page(tmp):
    """Страница карты конфликтов в пробе: путь объявляет экземпляр (`registers.conflicts`).

    Не объявил — возвращаем None: канарейка этой цели не применима, и об этом говорит её собственный отчёт,
    а не выдуманный путь. Механизм не знает, как у владельца называется карта конфликтов."""
    return _domain.register_path(tmp, "conflicts")


def _ledger_path(tmp):
    """Реестр передач в пробе: путь объявляет экземпляр (`registers.handover`); не объявил — None."""
    return _domain.register_path(tmp, "handover")


def page(tmp, rel):
    return os.path.join(tmp, *rel.split("/"))

def run_runner(wiki):
    """Состояние волны глазами раннера: по нему видно, остановился ли он на дефекте."""
    target = os.path.join(wiki, "_toolkit", "wave_runner.py")
    if not os.path.exists(target):
        target = os.path.join(PROJECT_ROOT, "_toolkit", "wave_runner.py")
    p = subprocess.run([sys.executable, "-X", "utf8", target, "--wiki", ".", "status"], cwd=wiki,
                       capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    return (p.stdout or "") + "\n" + (p.stderr or "")


def run_script(script, wiki, extra=()):
    """Запускает скрипт ИЗ КОПИИ, а не из живого дерева.

    Почему: до 2026-09-14 исполнялся живой `_toolkit/lint_wiki.py` по данным копии — прогон не был
    герметичным, и правки живой ветки посреди аттестации меняли её результат. Так уже терялись
    канарейки: чувствительность упала до 19/53, потому что линтер в живом дереве был в этот момент
    сломан мной же, а специфичность при этом осталась зелёной — тишина, потому что ничего не запускалось.
    """
    local = os.path.join(wiki, "_toolkit", os.path.basename(script))
    target = local if os.path.exists(local) else script
    # --all-issues: линтер человеку режет раздел до 40 строк, и на красном разделе снятая мутация
    # вытаскивает в печать скрытую находку — безвредная канарейка объявляется ложным срабатыванием
    # (а настоящая может спрятаться). Сравнение базлайна и мутации обязано видеть все находки.
    args = list(extra)
    if os.path.basename(script).startswith("lint_") and "--all-issues" not in args:
        args.append("--all-issues")
    p = subprocess.run([sys.executable, "-X", "utf8", target, *args, "--wiki", wiki],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", check=False)
    return (p.stdout or "") + "\n" + (p.stderr or "")

def lint_sections(out):
    """Разбор «## N. Заголовок: K» → {N: K}."""
    sections = {}
    for m in re.finditer(r"(?m)^##\s+(\d+)\.[^\n]*?:\s*(\d+)\s*$", out):
        sections[int(m.group(1))] = int(m.group(2))
    return sections

def verify_counts(out):
    """Разбор «подтверждено X, требует взгляда Y» → (X, Y)."""
    m = re.search(r"подтверждено\s+(\d+),\s+требует взгляда\s+(\d+)", out)
    return (int(m.group(1)), int(m.group(2))) if m else (None, None)

def replace_once(path, text, needle, repl, originals):
    """Регистрирует откат и применяет ровно одну замену; иначе MutationNotApplied."""
    if needle not in text:
        raise MutationNotApplied(f"строка «{needle[:50]}» не найдена в {os.path.basename(path)}")
    originals.append((path, text))
    write(path, text.replace(needle, repl, 1))

def lint_issues(out):
    """Сообщения линтера по разделам: номер раздела -> множество строк-проблем.

    Считаем по сообщениям, а не по счётчику: если раздел был грязен ещё до канарейки
    (например, внешний автор правит навык прямо во время прогона), рост счётчика объявит
    виноватой канарейку. Новое сообщение — это действительно новая проблема.
    """
    sections, cur = {}, None
    for line in out.split("\n"):
        m = re.match(r"^## (\d+)\.", line)
        if m:
            cur = int(m.group(1))
            sections.setdefault(cur, set())
            continue
        if cur is not None and line.startswith("- ") and "… ещё" not in line:
            sections[cur].add(line.strip())
    return sections

def rebuild_derived(tmp):
    """Пересобрать производные артефакты в копии: карту, мостик, панели.

    Зачем: безвредная мутация меняет дерево, и производные начинают описывать дерево до неё — §29 и §50
    срабатывают законно, а специфичность падает на ровном месте. Решение 2026-09-21 (вторая сессия, по разбору
    полного прогона): чинить базу, а не измерение — обновляем производные ДО чекеров, тогда любое срабатывание
    объясняется мутацией. Терпеть разделы в харнессе нельзя: безвредные канарейки — единственное, что измеряет
    МОЛЧАНИЕ §29 и §50.
    """
    for target, extra in (("project_map.py", ["generate"]), ("bridge.py", ["generate"]),
                          ("panel_freshness.py", ["--rebuild"])):
        try:
            run_script(os.path.join("_toolkit", target), tmp, extra)
        except OSError as exc:
            print("canary: производный артефакт %s не пересобран: %s" % (target, exc), file=sys.stderr)


def verdict(r):
    """Человекочитаемый итог: для безвредных канареек «поймана» = сработала тишина."""
    if r.get("skipped"):
        return "—"          # цель устарела или отсутствует в этом экземпляре: не промах и не доказательство
    if r["expected"].startswith("тишина"):
        return "тишина" if r["caught"] else "**ЛОЖНО**"
    return "да" if r["caught"] else "**нет**"

# --------------------------------------------------------------------------- #
# канарейки задания (1–10): каждая возвращает описание, оригиналы пишет в originals
# --------------------------------------------------------------------------- #

def newest_audit_report(tmp, *prefixes):
    """Свежий отчёт нужного класса в копии — или None.

    Закрытые проходы уезжают в архив (решение владельца 2026-09-22), поэтому жёсткая привязка канарейки к отчёту
    конкретной даты ломалась бы при каждом переносе. Берём самый свежий отчёт класса, какой есть в копии; нет
    ни одного — канарейка честно говорит, что цель устарела.
    """
    d = os.path.join(tmp, "_staging", "audit")
    if not os.path.isdir(d):
        return None
    for pref in prefixes:
        cand = sorted(f for f in os.listdir(d) if f.startswith(pref) and f.endswith(".md"))
        if cand:
            return os.path.join(d, cand[-1])
    return None


def c1_broken_link(tmp, originals):        # lint §1 — битая wikilink
    p = page(tmp, "wiki/concepts/harness-architecture.md")
    text = read(p)
    originals.append((p, text))
    write(p, text + "\n\nПроверка ссылок: [[canary-missing-slug-zzz]].\n")
    return "добавлена ссылка [[canary-missing-slug-zzz]] на несуществующий слаг"

def c2_number_change(tmp, originals):      # verify_numbers — подмена числа
    p = page(tmp, "wiki/concepts/harness-architecture.md")
    replace_once(p, read(p), "**~4000 строк**", "**~4100 строк**", originals)
    return "в «Факты и цифры»: ~4000 строк → ~4100 строк (такого числа нет в источнике)"

def c3_drop_attribution(tmp, originals):   # lint §15 — число без владельца
    # цель перенацелена 2026-09-15: «самоотчёт практика» снят каноном, владелец числа теперь
    # ссылка-алиас; канарейка убирает именно её.
    p = page(tmp, "wiki/concepts/spec-driven-development.md")
    replace_once(p, read(p),
                 "1.5–2.5 раза на длинной дистанции ([[Верховский_manifest|Верховский]]).",
                 "1.5–2.5 раза на длинной дистанции.", originals)
    return "у числа в «Факты и цифры» удалена ссылка-владелец"

# Канарейка 4 («own-analysis без маркеров [Синтез вики]») выведена из состава 2026-09-15:
# мытацию не к чему применять — сам маркер снят решением владельца, правило в линтере удалено.

def c86_package_without_snapshot(tmp, originals):   # lint §21 — документ пакета за дату, снапшота на которую нет
    d = page(tmp, "_staging/audit")
    if not os.path.isdir(d):
        raise MutationNotApplied("нет каталога _staging/audit")
    f = os.path.join(d, "wiki-state-2026-12-31-probe.md")
    originals.append((f, None))                 # созданный файл откатывается удалением
    write(f, "# проба\n\nПодтверждено 999 проверенных чисел.\n")
    return "документ пакета от 2026-12-31 без снапшота на эту дату"

def c90_cross_pair_without_verdict(tmp, originals):   # lint §25 — кросс-страничная пара без вердикта
    p = page(tmp, "_staging/stance/wave-2026-09-14-pairs.json")
    if not os.path.exists(p):
        raise MutationNotApplied("нет файла пар волны")
    data = json.load(open(p, encoding="utf-8"))
    pairs = data.get("pairs") or {}
    page_slug = next((s for s in sorted(pairs) if pairs[s]), None)
    if not page_slug:
        raise MutationNotApplied("в парах нет непустой страницы")
    originals.append((p, read(p)))
    fake = "raw/telegram/2026-09-01-kakaya-to-chuzhaya-zametka-999999.md"
    pairs[page_slug] = sorted(set(pairs[page_slug]) | {fake})
    json.dump(data, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return "в пары волны добавлена кросс-страничная пара без вердикта: " + page_slug + " ← чужая заметка"

def c88_wave_page_without_verdict(tmp, originals):   # lint §25 — у страницы волны нет вердикта оси
    p = page(tmp, "_staging/stance/wave-2026-09-14-verdicts.json")
    if not os.path.exists(p):
        raise MutationNotApplied("нет артефакта вердиктов волны")
    data = json.load(open(p, encoding="utf-8"))
    items = data.get("items") or data.get("records") or []
    if len(items) < 2:
        raise MutationNotApplied("в артефакте меньше двух страниц")
    originals.append((p, read(p)))
    data["items"] = items[1:]                          # одна страница волны осталась без вердикта
    json.dump(data, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return "у страницы волны «" + items[0]["slug"] + "» удалён вердикт оси"

def b11_wave_verdicts_rewritten(tmp, originals):    # безвредно для §25
    p = page(tmp, "_staging/stance/wave-2026-09-14-verdicts.json")
    if not os.path.exists(p):
        raise MutationNotApplied("нет артефакта вердиктов волны")
    text = read(p)
    originals.append((p, text))
    write(p, text)                                      # перезапись тем же содержимым
    return "артефакт вердиктов перезаписан тем же содержимым (ожидается тишина)"

def c87_dispute_without_conditions(tmp, originals):   # lint §25 — спор без практического вывода
    p = _cmap_page(tmp)
    if not p:
        raise MutationNotApplied("цель не объявлена экземпляром (карта конфликтов): канарейка не применима")
    text = read(p)
    head = text.find("## Таблица конфликтов")
    if head < 0:
        raise MutationNotApplied("в карте нет таблицы конфликтов")
    before, after = text[:head], text[head:]
    rows = [ln for ln in after.split("\n") if re.match(r"\|\s*\**(\d+)\**\s*\|", ln.strip())]
    if not rows:
        raise MutationNotApplied("в таблице конфликтов нет строк споров")
    last = max(rows, key=lambda ln: int(re.match(r"\|\s*\**(\d+)\**", ln.strip()).group(1)))
    cells = [x.strip() for x in last.strip().strip("|").split("|")]
    num = cells[0].strip("*")
    cells[5] = ""                     # «когда выигрывает позиция A»: расхождение без вывода
    out = after.replace(last, "| " + " | ".join(cells) + " |")
    if out == after:
        raise MutationNotApplied("строка спора №" + num + " не найдена")
    originals.append((p, text))
    write(p, before + out)
    return "у спора №" + num + " опустошена колонка «когда выигрывает позиция A» — расхождение без практического вывода"

def c6_empty_table_cell(tmp, originals):   # lint §9 — пустая ячейка
    # Цель ищется по разделителю с ДВУМЯ и более тире: ширина колонки в выровненной таблице зависит от
    # содержимого, и «#» может дать разделитель «--» — на этом канарейка один раз потеряла цель
    # (прогон 2026-09-16: «мутация не применилась»). Строка вставляется по числу колонок шапки, иначе
    # на таблице другой раскладки мутация проверяла бы не пустую ячейку, а несовпадение колонок.
    p = _cmap_page(tmp)
    if not p:
        raise MutationNotApplied("цель не объявлена экземпляром (карта конфликтов): канарейка не применима")
    lines = read(p).split("\n")
    out, done, width = [], False, None
    for ln in lines:
        out.append(ln)
        if width is None and ln.startswith("|"):
            width = len([c for c in ln.strip().strip("|").split("|")])
        if not done and re.match(r"^\|\s*-{2,}", ln):
            cells = ["99", "канарейка-пустая-ячейка"] + [""] * max(0, (width or 3) - 3)
            cells = cells[:max(3, width or 3)]
            cells[2] = ""                      # пустая ячейка в смысловой колонке
            out.append("| " + " | ".join(cells) + " |")
            done = True
    if not done:
        raise MutationNotApplied("в таблице не найдена строка-разделитель")
    originals.append((p, read(p)))
    write(p, "\n".join(out))
    return "в таблицу добавлена строка с пустой ячейкой (%d колонок)" % (width or 0)

def c7_unknown_tag(tmp, originals):        # lint §5 — тег вне таксономии
    p = page(tmp, "wiki/concepts/harness-architecture.md")
    replace_once(p, read(p), "pattern/skills-first]",
                 "pattern/skills-first, canary/outside-taxonomy]", originals)
    return "в tags добавлен тег canary/outside-taxonomy (вне таксономии)"

def c9_report_number(tmp, originals):      # lint §14 — число в опубликованном отчёте
    fig_json = sorted(glob.glob(os.path.join(tmp, "_staging", "audit", "wiki-figures-*.json")))
    reps = sorted(glob.glob(os.path.join(tmp, "_staging", "audit", "wiki-overview-*.md")))
    if not fig_json or not reps:
        raise MutationNotApplied("нет пары wiki-figures.json + wiki-overview.md")
    n = json.load(open(fig_json[-1], encoding="utf-8"))["figures"]["рёбер графа"]
    anchor = f"рёбер графа «страница → источник»: {n}"
    replace_once(reps[-1], read(reps[-1]), anchor,
                 f"рёбер графа «страница → источник»: {n + 1}", originals)
    return f"в опубликованном отчёте число рёбер {n} → {n + 1}"

def c10_unquoted_yaml(tmp, originals):     # lint §4 — незакавыченное «: »
    p = page(tmp, "wiki/concepts/harness-architecture.md")
    text = read(p)
    m = re.match(r"^(---\r?\n)(.*?)(\r?\n---)", text, re.DOTALL)
    if not m:
        raise MutationNotApplied("не найден frontmatter")
    originals.append((p, text))
    write(p, text[:m.end(2)] + "\r\ncanary-value: has: colon" + text[m.end(2):])
    return "в frontmatter добавлено незакавыченное значение с «: »"

# --------------------------------------------------------------------------- #
# безвредные правки (11–13): контроль специфичности — чекеры обязаны молчать
# --------------------------------------------------------------------------- #
def b1_blank_line(tmp, originals):
    p = page(tmp, "wiki/concepts/harness-architecture.md")
    text = read(p)
    originals.append((p, text))
    write(p, text.rstrip("\n") + "\n\n")
    return "добавлена пустая строка в конце страницы"

def b2_new_section(tmp, originals):
    p = page(tmp, "wiki/concepts/loop-engineering.md")
    text = read(p)
    originals.append((p, text))
    write(p, text.rstrip("\n") + "\n\n## Пример из практики\n\n"
          "Никаких чисел и ссылок здесь нет — только пояснение словами.\n")
    return "добавлен раздел с прозой без чисел и ссылок"

def b3_reorder_links(tmp, originals):
    p = page(tmp, "wiki/concepts/task-contracts.md")
    lines = read(p).split("\n")
    originals.append((p, "\n".join(lines)))
    idx = [i for i, l in enumerate(lines) if l.startswith("- [[")]
    if len(idx) < 2:
        raise MutationNotApplied("меньше двух ссылок в списке «Связи»")
    i, j = idx[0], idx[1]
    lines[i], lines[j] = lines[j], lines[i]
    write(p, "\n".join(lines))
    return "переставлены два пункта списка «Связи»"

def c29_log_unknown_action(tmp, originals):   # lint §12 — действие вне набора
    p = os.path.join(tmp, "log.md")
    originals.append((p, read(p)))
    with open(p, "a", encoding="utf-8") as f:
        f.write("\n## [2026-09-13] teleport | канареечная запись с неизвестным действием\n")
    return "добавлена запись лога с действием «teleport»"

def c30_log_future_date(tmp, originals):      # lint §12 — дата из будущего
    p = os.path.join(tmp, "log.md")
    originals.append((p, read(p)))
    with open(p, "a", encoding="utf-8") as f:
        f.write("\n## [2099-01-01] update | канареечная запись с датой из будущего\n")
    return "добавлена запись лога с датой из будущего"

def c31_stance_without_map_row(tmp, originals):   # lint §25 — ось contradicts без строки в карте
    # цель перенацелена 2026-09-15: в поле stance остались только отклонения, пары «=supports» из
    # него ушли вместе с умолчанием. Берём источник, объявленный на странице и НЕ названный в карте.
    p = _cmap_page(tmp)
    if not p:
        raise MutationNotApplied("цель не объявлена экземпляром (карта конфликтов): канарейка не применима")
    text = read(p)
    src = "raw/articles/anthropic-demystifying-evals-for-ai-agents.md"
    if src not in text:
        raise MutationNotApplied("источник не объявлен на странице карты")
    if "source-stance: [" not in text:
        raise MutationNotApplied("нет поля source-stance")
    originals.append((p, text))
    write(p, text.replace("source-stance: [", f"source-stance: [{src}=contradicts, ", 1))
    return "оси источника выставлен contradicts, строки в карте конфликтов нет"

def c32_ocr_high_confidence(tmp, originals):      # lint §26 — машинное чтение при confidence: high
    p = page(tmp, "wiki/concepts/multi-agent-orchestration.md")
    text = read(p)
    m = re.search(r"(?m)^confidence:\s*\S+$", text)
    if not m or "машинное чтение" not in text:
        raise MutationNotApplied("страница не подходит под мутацию")
    originals.append((p, text))
    write(p, text[:m.start()] + "confidence: high" + text[m.end():])
    return "машинно прочитанные числа при confidence: high"

def c33_ocr_without_confirmation(tmp, originals):  # lint §26 — нет отметки о втором прочтении
    p = page(tmp, "wiki/concepts/multi-agent-orchestration.md")
    text = read(p)
    marker = "прочтение подтверждено вторым способом — OCR"
    if marker not in text:
        raise MutationNotApplied("нет отметки о подтверждении")
    originals.append((p, text))
    write(p, text.replace(marker, "", 1))
    return "снята отметка о втором прочтении у чисел из схем"

def b4_ocr_marked_with_confirmation(tmp, originals):   # безвредно для §26
    p = page(tmp, "wiki/concepts/task-contracts.md")
    text = read(p)
    if not re.search(r"(?m)^evidence:", text):
        raise MutationNotApplied("нет поля evidence")
    originals.append((p, text))
    text = re.sub(r"(?m)^evidence:\s*\S+$", "evidence: mixed", text, count=1)
    write(p, text.rstrip() + "\n- Канареечная строка: **42** единицы (машинное чтение, прочтение подтверждено вторым способом — OCR).\n")
    return "добавлена строка с машинным чтением и подтверждением (ожидается тишина)"

def c35_past_claim_undated(tmp, originals):   # lint §27 — утверждение о прошлом без даты
    p = newest_audit_report(tmp, "blind-run-score-", "wiki-state-", "wiki-overview-")
    if not p:
        raise MutationNotApplied("нет отчёта закрытого класса в копии")
    t = read(p)
    originals.append((p, t))
    write(p, t.rstrip() + "\n\nПятый пакет говорил про 33 страницы и был устроен иначе.\n")
    return "добавлено утверждение о прошлом пакете без даты"

def c36_past_claim_wrong_number(tmp, originals):   # lint §27 — число о прошлом пакете не сходится со слепком
    p = newest_audit_report(tmp, "blind-run-score-", "wiki-state-", "wiki-overview-")
    if not p:
        raise MutationNotApplied("нет отчёта закрытого класса в копии")
    t = read(p)
    originals.append((p, t))
    write(p, t.rstrip() + "\n\nВ пакете 2026-09-11 было 99 страниц — так утверждает канарейка.\n")
    return "добавлено утверждение о пакете 2026-09-11 с числом, которого нет в слепке"

def c37_debt_closed_without_evidence(tmp, originals):   # lint §28 — закрытый пункт с несуществующим доказательством
    p = os.path.join(tmp, "debt.tsv")
    if not os.path.exists(p):
        raise MutationNotApplied("нет реестра техдолга")
    t = read(p)
    originals.append((p, t))
    write(p, t.rstrip() + "\nprobe-debt\tПробный пункт\tmeta\tагент\tзакрыт\t2026-09-13\t2026-09-13\t_staging/нет-такого-файла.md\tпроба\n")
    return "добавлен закрытый пункт с несуществующим доказательством"

def c38_debt_bad_status(tmp, originals):                # lint §28 — статус вне набора
    p = os.path.join(tmp, "debt.tsv")
    if not os.path.exists(p):
        raise MutationNotApplied("нет реестра техдолга")
    t = read(p)
    originals.append((p, t))
    write(p, t.rstrip() + "\nprobe-debt2\tПробный пункт\tmeta\tагент\tкогда-нибудь\t2026-09-13\t\t\tпроба\n")
    return "добавлен пункт со статусом вне набора"

def c39_stale_map(tmp, originals):                      # lint §29 — карта структуры устарела
    p = os.path.join(tmp, "_staging", "project-map.md")
    if not os.path.exists(p):
        raise MutationNotApplied("нет карты структуры")
    t = read(p)
    originals.append((p, t))
    import re as _re
    write(p, _re.sub(r"отпечаток: `[0-9a-f]+`", "отпечаток: `" + "0" * 16 + "`", t, count=1))
    return "отпечаток дерева в карте подменён на заведомо неверный"

def c40_unexplained_node(tmp, originals):               # lint §29 — узел структуры без объяснения замысла
    d = os.path.join(tmp, "_staging", "probe-area")
    if os.path.isdir(os.path.join(tmp, "_staging")) is False:
        raise MutationNotApplied("нет _staging")
    os.makedirs(d, exist_ok=True)
    originals.append((d, None))                         # каталог — тоже мутация: без отката он старит копию
    open(os.path.join(d, "probe.py"), "w", encoding="utf-8").write("# проба\n")
    return "создан каталог _staging/probe-area без строки замысла в карте"

def c43_bridge_without_window(tmp, originals):           # lint §30 — у мостика нет записанного окна
    p = os.path.join(tmp, "_staging", "bridge.md")
    if not os.path.exists(p):
        raise MutationNotApplied("нет мостика")
    t = read(p)
    originals.append((p, t))
    new_t, n = re.subn(r"окно: `[^`]*`", "окно: не записано", t, count=1)
    if not n:
        raise MutationNotApplied("в мостике нет строки окна")
    write(p, new_t)
    return "из мостика убрано записанное окно работы"

def c44_refusal_missing_from_bridge(tmp, originals):     # lint §30 — отказ реестра не назван в мостике
    p = os.path.join(tmp, "debt.tsv")
    if not os.path.exists(p):
        raise MutationNotApplied("нет реестра долга")
    t = read(p)
    originals.append((p, t))
    # 10 полей: контракт реестра — ровно 10 колонок (лишняя/недостающая ломает разбор и прячет причину)
    write(p, t.rstrip() + "\nprobe-refusal\tПробный отказ, которого нет в мостике\tmeta\tагент\tотклонён\t2026-09-14\t\t\tпроба\tвладелец\n")
    return "в реестр добавлен отказ, не упомянутый в разделе «Чего не делать»"

def c45_mirror_readonly(tmp, originals):                # lint §31 — зеркало помечено «только чтение»
    import stat as _stat
    base = os.path.join(tmp, "wiki", "sources", "raw")
    if not os.path.isdir(base):
        raise MutationNotApplied("нет зеркала")
    target = None
    for r_, dirs, fs in os.walk(base):
        for f in fs:
            if f.endswith(".md"):
                target = os.path.join(r_, f)
                break
        if target:
            break
    if not target:
        raise MutationNotApplied("в зеркале нет md-файлов")
    os.chmod(target, _stat.S_IREAD)
    originals.append((target, RESTORE_WRITE))            # отдельная метка: вернуть право записи, не удалять файл
    return "у файла зеркала выставлен атрибут «только чтение»"

def c47_service_file_in_vault(tmp, originals):          # lint §31 — служебный файл внутри хранилища
    vault = os.path.join(tmp, "wiki")
    if not os.path.isdir(vault):
        raise MutationNotApplied("нет хранилища")
    target = os.path.join(vault, "log.md")
    originals.append((target, None))
    write(target, "# служебный журнал внутри хранилища\n")
    return "в хранилище положен служебный log.md"

def c48_refusal_premise_broken(tmp, originals):         # lint §28 — предпосылка отказа перестала держаться
    p = os.path.join(tmp, "debt.tsv")
    if not os.path.exists(p):
        raise MutationNotApplied("нет реестра долга")
    t = read(p)
    originals.append((p, t))
    write(p, t.rstrip() + "\nprobe-premise\tПроба предпосылки\tmeta\tагент\tотклонён\t2026-09-14\t\t\t"
                 "причина записана\tпорог:страницы>1\n")
    return "добавлен отказ с заведомо перейдённым порогом в предпосылке"

def c49_letter_without_frontmatter(tmp, originals):     # lint §32 — письмо аудитору без frontmatter
    ad = os.path.join(tmp, "_staging", "audit")
    if not os.path.isdir(ad):
        raise MutationNotApplied("нет папки аудита")
    target = os.path.join(ad, "auditor-report-2026-09-20.md")
    originals.append((target, None))                   # файл создаётся — при восстановлении удаляется
    write(target, "# Отчёт аудитору: цикл 2026-09-20\n\nБез frontmatter — письмо не описывает само себя.\n")
    return "создано письмо аудитору нового контракта без frontmatter"

def c50_bridge_without_traps(tmp, originals):           # lint §30 — из мостика вырезан раздел ловушек
    p = os.path.join(tmp, "_staging", "bridge.md")
    if not os.path.exists(p):
        raise MutationNotApplied("нет мостика")
    t = read(p)
    originals.append((p, t))
    new_t, n = re.subn(r"## Ловушки: среда и инструмент.*?(?=\n## )", "", t, count=1, flags=re.DOTALL)
    if not n:
        raise MutationNotApplied("в мостике нет раздела ловушек")
    write(p, new_t)
    return "из мостика вырезан раздел ловушек среды и инструмента"

def c51_pain_source_missing(tmp, originals):            # lint §33 — карточка называет боль, источника нет на странице
    p = os.path.join(tmp, "wiki", "comparisons", "pain-points-and-fixes.md")
    if not os.path.exists(p):
        raise MutationNotApplied("нет страницы болей")
    t = read(p)
    originals.append((p, t))
    new_t, n = re.subn(r"grace-autotests-antipattern", "grace-somewhere-else", t)
    if not n:
        raise MutationNotApplied("источник не упомянут на странице болей")
    write(p, new_t)
    return "источник карточки вырезан со страницы болей"

def c52_unexplained_doc(tmp, originals):                # lint §29 — документ обвязки без замысла в карте
    d = os.path.join(tmp, "_staging")
    if not os.path.isdir(d):
        raise MutationNotApplied("нет _staging")
    target = os.path.join(d, "probe-doc.md")
    originals.append((target, None))                    # файл создаётся — при восстановлении удаляется
    write(target, "# Пробный документ обвязки\n\nНи в карте, ни в таблице инструментов его нет.\n")
    return "создан документ _staging/probe-doc.md без строки замысла в карте"

# пояснения к отчёту (не правка чекеров, а трактовка результата)
MISS_HINTS = {
    2: "Пример «40–50 → 41–51» verify_numbers не поймал бы: числа <3 цифр без единицы "
       "пропускаются; взято ~4000 → ~4100.",
    11: "№11–13 — безвредные правки: контроль специфичности, чекеры молчат.",
}

# (id, название, ожидаемый чекер, какой чекер запускать, номер раздела lint, функция)

def c11_orphan_page(tmp, originals):
    """lint §2 — страница без входящих ссылок."""
    p = os.path.join(tmp, "wiki", "concepts", "canary-orphan.md")
    originals.append((p, None))
    fm = ("---\ntitle: Канареечная сирота\ntype: concept\ncreated: 2026-09-11\nupdated: 2026-09-11\n"
          "status: active\ntags: [llm/agents]\nsources: [raw/DHH_manifest.md]\nsummary: канарейка\n"
          "confidence: medium\nlast-verified: 2026-09-11\nverification-status: current\nevidence: practitioner-opinion\n"
          "own-analysis: false\n---\n\nТекст канарейки без ссылок.\n")
    write(p, fm)
    return "создана страница-сирота"

def c12_index_miss(tmp, originals):
    """lint §3 — страница есть в графе, но её нет в index.md."""
    p = os.path.join(tmp, "wiki", "concepts", "canary-index-miss.md")
    originals.append((p, None))
    fm = ("---\ntitle: Канарейка вне индекса\ntype: concept\ncreated: 2026-09-11\nupdated: 2026-09-11\n"
          "status: active\ntags: [llm/agents]\nsources: [raw/DHH_manifest.md]\nsummary: канарейка\n"
          "confidence: medium\nlast-verified: 2026-09-11\nverification-status: current\nevidence: practitioner-opinion\n"
          "own-analysis: false\n---\n\nТекст канарейки.\n")
    write(p, fm)
    host = page(tmp, "wiki/concepts/task-contracts.md")
    text = read(host)
    originals.append((host, text))
    write(host, text.rstrip("\n") + "\n- [[canary-index-miss]] — канареечная ссылка.\n")
    return "страница не в индексе, но со входящей ссылкой"

def c14_long_page(tmp, originals):
    """lint §7 — страница длиннее 200 строк."""
    p = page(tmp, "wiki/concepts/loop-engineering.md")
    text = read(p)
    originals.append((p, text))
    write(p, text.rstrip("\n") + "\n" + "\n".join("Канареечная строка без чисел и ссылок." for _ in range(300)) + "\n")
    return "страница раздута до 200+ строк"

def b17_mirror_edit_is_benign(tmp, originals):
    """Безвредно: копия внутри вики живёт самостоятельно (решение владельца 2026-09-15:
    «отключить сверку зеркал; один раз скопировали внутрь вики и там оно само живёт»).

    Прежняя канарейка №18 правила копию и ждала находку §8; теперь такая правка — норма.
    """
    p = page(tmp, "wiki/sources/raw/Шейко_manifest.md")
    text = read(p)
    originals.append((p, text))
    write(p, text.rstrip("\n") + "\n\nПриписка в копии внутри хранилища.\n")
    return "правка копии источника в хранилище (ожидается тишина)"

def c77_mirror_missing(tmp, originals):     # lint §8 — источника нет в хранилище
    p = page(tmp, "wiki/sources/raw/Шейко_manifest.md")
    if not os.path.exists(p):
        raise MutationNotApplied("нет копии источника")
    # Возвращаем СОДЕРЖИМОЕ (не None!): с None харнесс на откате удаляет файл, и он остаётся
    # удалённым до конца прогона — тогда §8/§1/§29 «горят» у всех последующих безвредных канареек
    # (поймано прогоном 2026-09-15: №81 показала ложное срабатывание разделов [1, 8, 29]).
    originals.append((p, read(p)))
    os.remove(p)
    return "копия источника удалена из хранилища — в Obsidian источника не видно"

def c16_empty_file(tmp, originals):
    """lint §10 — пустой файл страницы."""
    p = page(tmp, "wiki/concepts/bdd-and-quality-gates.md")
    originals.append((p, read(p)))
    write(p, "")
    return "файл страницы обнулён"

def c17_bad_status(tmp, originals):
    """lint §13 — недопустимое значение verification-status."""
    p = page(tmp, "wiki/concepts/tool-design.md")
    text = read(p)
    originals.append((p, text))
    write(p, text.replace("verification-status: current", "verification-status: сломано", 1))
    return "недопустимый verification-status"

def c21_evidence_conflict(tmp, originals):
    """lint §17 — вендорский самоотчёт при высокой уверенности без оговорки."""
    p = page(tmp, "wiki/concepts/harness-architecture.md")
    text = read(p)
    originals.append((p, text))
    t = text.replace("evidence: mixed", "evidence: vendor-self-report", 1)
    t = __import__("re").sub(r"(?m)^confidence: \w+", "confidence: high", t)
    write(p, t)
    return "evidence=vendor-self-report при confidence: high"

def c22_over_soft_limit(tmp, originals):
    """lint §18 — страница перевалила 150 строк без обоснования расщепления."""
    p = page(tmp, "wiki/concepts/task-contracts.md")
    text = read(p)
    originals.append((p, text))
    write(p, text.rstrip("\n") + "\n" + "\n".join("Канареечная строка без чисел." for _ in range(90)) + "\n")
    return "страница длиннее 150 строк"

def c23_corpus_only_high(tmp, originals):
    """lint §19 — высокая уверенность на источниках одного корпуса."""
    p = page(tmp, "wiki/concepts/runtime-context-hierarchy.md")   # страница только на корпусных источниках
    text = read(p)
    originals.append((p, text))
    write(p, __import__("re").sub(r"(?m)^confidence: \w+", "confidence: high", text))
    return "confidence: high на аффилированном корпусе"

def c24_undeclared_source(tmp, originals):
    """lint §20 — атрибутивное упоминание источника, которого нет в sources: страницы."""
    p = page(tmp, "wiki/concepts/loop-engineering.md")
    text = read(p)
    originals.append((p, text))
    # Разбор шапки — только через общий парсер: inline-форма `sources: [a, b]` и блочная (так пишет
    # Obsidian и так теперь выглядят многие страницы) дают одно и то же. Прежний срез по «sources: [»
    # падал на странице с блочной формой: полный прогон канареек 2026-09-21 встал на №24.
    declared = " ".join(_fmparse.items(read(p), "sources"))
    for cand in ("Шейко_manifest", "DHH_manifest", "Соломоник_manifest", "Верховский_report"):
        if cand not in declared:
            write(p, text.rstrip("\n") + f"\n\nПо замеру: 40 итераций — [[{cand}]].\n")
            return f"упомянут {cand} вне sources:"
    raise MutationNotApplied("все кандидаты уже объявлены")

def c25_prose_number(tmp, originals):
    """lint §21 — ручное число в прозе пакета, разошедшееся со снапшотом."""
    p = page(tmp, "wiki-overview-2026-09-11.md")
    if not os.path.exists(p):
        cand = [f for f in os.listdir(page(tmp, "_staging/audit")) if f.startswith("wiki-overview-")]
        if not cand:
            raise MutationNotApplied("нет обзора")
        p = page(tmp, "_staging/audit/" + cand[-1])
    text = read(p)
    m = __import__("re").search(r"граф Mermaid на (\d+) рёб", text)
    if not m:
        raise MutationNotApplied("в обзоре нет фразы про рёбра")
    originals.append((p, text))
    write(p, text[:m.start(1)] + "291" + text[m.end(1):])
    return "в прозе обзора подставлено 291 ребро"

def c26_draft_leftover(tmp, originals):
    """lint §22 — остаток черновика: HTML-комментарий-заглушка в теле страницы."""
    p = page(tmp, "wiki/concepts/spec-driven-development.md")
    text = read(p)
    if "<!--" in text:
        raise MutationNotApplied("в странице уже есть комментарий")
    originals.append((p, text))
    write(p, text.rstrip() + "\n\n<!-- TODO: дописать раздел про оценки -->\n")
    return "в тело страницы добавлен HTML-комментарий-заглушка"

def c27_process_prose(tmp, originals):
    """lint §23 — процесс вместо факта: страница рассказывает о ходе работы."""
    p = page(tmp, "wiki/concepts/spec-driven-development.md")
    text = read(p)
    if "мы проверили" in text.lower():
        raise MutationNotApplied("фраза уже есть")
    originals.append((p, text))
    write(p, text.rstrip() + "\n\nМы проверили все источники вручную и убедились в выводах.\n")
    return "в тело страницы добавлена процессная проза «мы проверили»"

def c28_source_without_stance(tmp, originals):
    """lint §24 — позиция stance вне допустимого набора у объявленных источников.

    Перенацелено 2026-09-15: канон «умолчание объявлено строкой `source-stance-default`» сделал
    прежнюю мутацию («источник объявлен без позиции») законной формой — умолчание её закрывает.
    Теперь ломаем само умолчание: тогда у каждого объявленного источника позиция недопустима.
    """
    p = page(tmp, "wiki/comparisons/pain-points-and-fixes.md")
    text = read(p)
    old = "source-stance-default: supports"
    if old not in text:
        raise MutationNotApplied("нет строки умолчания stance")
    originals.append((p, text))
    write(p, text.replace(old, "source-stance-default: поддержка", 1))
    return "умолчание stance заменено значением вне набора (supports/partial/contradicts)"

def c53_letter_placeholder_frontmatter(tmp, originals):  # lint §32 — в заголовке письма остался незаполненный плейсхолдер
    ad = os.path.join(tmp, "_staging", "audit")
    if not os.path.isdir(ad):
        raise MutationNotApplied("нет папки аудита")
    target = os.path.join(ad, "auditor-response-2026-09-20.md")
    originals.append((target, None))
    write(target, "---\ntype: auditor-response\ncycle: 2026-09-20\nanswers_review: <номер или дата разбора>\n"
                  "created: 2026-09-20\naudience: external auditor\nsnapshot: > снапшот\n---\n\n# Ответ\n")
    return "создан ответ с незаполненным плейсхолдером в заголовке"

def b5_letter_frontmatter_filled(tmp, originals):       # безвредно для §32 и §37
    """Черновик письма (отправленный документ править нельзя) с НАСТОЯЩИМ снапшотом пакета:
    иначе §37 честно сработает на подставной строке снапшота."""
    ad = os.path.join(tmp, "_staging", "audit")
    if not os.path.isdir(ad):
        raise MutationNotApplied("нет папки аудита")
    figs = sorted(glob.glob(os.path.join(ad, "wiki-figures-*.json")))
    if not figs:
        raise MutationNotApplied("нет снапшота пакета")
    snap = json.load(open(figs[-1], encoding="utf-8")).get("snapshot_line", "").lstrip("> ").strip()
    m = re.search(r"(\d{4}-\d{2}-\d{2})", os.path.basename(figs[-1]))
    day = m.group(1) if m else "2026-09-15"
    target = os.path.join(ad, "auditor-report-%s.md" % day)
    originals.append((target, None))
    write(target, f"---\ntype: auditor-report\ncycle: {day}\ncreated: {day}\n"
                  f"commits_span: aaaaaaa..bbbbbbb\naudience: external auditor\nsnapshot: {snap}\n---\n\n"
                  f"# Отчёт аудитору: цикл {day}\n\nЧерновик.\n")
    return "создан черновик письма с заполненным заголовком (ожидается тишина)"

def c55_dead_internal_ref(tmp, originals):              # lint §34 — ссылка на несуществующий наш файл
    p = os.path.join(tmp, "_toolkit", "links-playbook.md")
    if not os.path.exists(p):
        raise MutationNotApplied("нет плейбука ссылок")
    t = read(p)
    originals.append((p, t))
    write(p, t.rstrip() + "\n\n- Хелпер лежит в `_staging/no-such-helper.py` и запускается вручную.\n")
    return "в служебный документ добавлена ссылка на несуществующий файл"

def b6_internal_ref_placeholder(tmp, originals):        # безвредно для §34
    p = os.path.join(tmp, "_toolkit", "links-playbook.md")
    if not os.path.exists(p):
        raise MutationNotApplied("нет плейбука ссылок")
    t = read(p)
    originals.append((p, t))
    write(p, t.rstrip() + "\n\n- Письмо цикла называется `_staging/audit/auditor-report-<дата>.md`; шаблоны — `raw/....md`.\n")
    return "добавлена ссылка-заглушка (ожидается тишина)"

def c57_secret_in_tree(tmp, originals):                 # lint §35 — ключ в файле проекта
    p = os.path.join(tmp, "_toolkit", "links-playbook.md")
    if not os.path.exists(p):
        raise MutationNotApplied("нет плейбука ссылок")
    t = read(p)
    originals.append((p, t))
    write(p, t.rstrip() + "\n\n- В чужом отчёте попался ключ вида `sk-" + "A" * 40 + "` — пример вынесен сюда.\n")
    return "в документ положена строка вида ключа"

def b7_secret_already_cut(tmp, originals):              # безвредно для §35
    p = os.path.join(tmp, "_toolkit", "links-playbook.md")
    if not os.path.exists(p):
        raise MutationNotApplied("нет плейбука ссылок")
    t = read(p)
    originals.append((p, t))
    write(p, t.rstrip() + "\n\n- Значение вырезано: `sk-<удалено: секрет вырезан 2026-09-14>` (ожидается тишина).\n")
    return "добавлена строка с уже вырезанным значением (ожидается тишина)"

def c59_stale_offsite_log(tmp, originals):              # lint §36 — реестр копий устарел
    p = os.path.join(tmp, "_staging", "audit", "offsite-log.tsv")
    if not os.path.exists(p):
        raise MutationNotApplied("нет реестра удалённых копий")
    t = read(p)
    originals.append((p, t))
    lines = [l for l in t.splitlines() if l.strip()]
    old = (datetime.date.today() - datetime.timedelta(days=30)).isoformat()
    lines[-1] = "\t".join([old] + lines[-1].split("\t")[1:])
    write(p, "\n".join(lines) + "\n")
    return "последняя подтверждённая копия отодвинута на 30 дней назад"

def b8_offsite_fresh(tmp, originals):                   # безвредно для §36
    p = os.path.join(tmp, "_staging", "audit", "offsite-log.tsv")
    if not os.path.exists(p):
        raise MutationNotApplied("нет реестра удалённых копий")
    t = read(p)
    originals.append((p, t))
    lines = [l for l in t.splitlines() if l.strip()]
    lines[-1] = "\t".join([datetime.date.today().isoformat()] + lines[-1].split("\t")[1:])
    write(p, "\n".join(lines) + "\n")
    return "дата последней подтверждённой копии выставлена на сегодня (ожидается тишина)"

def c61_sent_doc_modified(tmp, originals):              # lint §32 — отправленный документ изменили после отправки
    ad = os.path.join(tmp, "_staging", "audit")
    reg = os.path.join(ad, "sent-artifacts.tsv")
    if not os.path.exists(reg):
        raise MutationNotApplied("нет реестра отправленного")
    row = [l for l in read(reg).splitlines() if l.strip()][1].split("\t")
    p = os.path.join(ad, os.path.basename(row[1]))
    t = read(p)
    originals.append((p, t))
    write(p, t.rstrip() + "\n\n- Правка после отправки: строки в отправленной версии не было.\n")
    return "отправленный документ дописан после отправки"

def b9_sent_doc_rewritten_same(tmp, originals):         # безвредно для §32
    ad = os.path.join(tmp, "_staging", "audit")
    reg = os.path.join(ad, "sent-artifacts.tsv")
    if not os.path.exists(reg):
        raise MutationNotApplied("нет реестра отправленного")
    row = [l for l in read(reg).splitlines() if l.strip()][1].split("\t")
    p = os.path.join(ad, os.path.basename(row[1]))
    t = read(p)
    originals.append((p, t))
    write(p, t)                                          # те же байты: хеш не меняется
    return "отправленный документ перезаписан теми же байтами (ожидается тишина)"

def _draft_letter_with_row(tmp, row_text):
    """Черновик письма со строкой дифф-таблицы: используется канарейками §37."""
    ad = os.path.join(tmp, "_staging", "audit")
    if not os.path.isdir(ad):
        raise MutationNotApplied("нет папки аудита")
    figs = sorted(glob.glob(os.path.join(ad, "wiki-figures-*.json")))
    if not figs:
        raise MutationNotApplied("нет снапшота пакета")
    snap = json.load(open(figs[-1], encoding="utf-8")).get("snapshot_line", "").lstrip("> ").strip()
    # Дата черновика — дата снапшота, а не константа: иначе §21 справедливо отвечает «сверить не с чем»
    # на дереве, где фигур этой даты нет (найдено 2026-09-25 полным прогоном на свежем экземпляре).
    m = re.search(r"(\d{4}-\d{2}-\d{2})", os.path.basename(figs[-1]))
    day = m.group(1) if m else "2026-09-15"
    target = os.path.join(ad, "auditor-report-%s.md" % day)
    text = ("---\ntype: auditor-report\ncycle: %s\ncreated: %s\n"
            f"commits_span: aaaaaaa..bbbbbbb\naudience: external auditor\nsnapshot: {snap}\n---\n\n"
            "# Отчёт аудитору: цикл %s\n\n"
            "| метрика | было | стало | чем вызван сдвиг |\n|---|---|---|---|\n"
            % (day, day, day) + row_text + "\n")
    return target, text

def c63_growth_arithmetic_broken(tmp, originals):       # lint §37 — примечание о приросте не сходится с ячейками
    target, text = _draft_letter_with_row(tmp, "| разделов линтера | 26 | 34 | семь новых разделов (28–34) |")
    originals.append((target, None))
    write(target, text)
    return "в черновике письма прирост описан от другой базовой точки"

def b10_growth_arithmetic_ok(tmp, originals):           # безвредно для §37
    target, text = _draft_letter_with_row(tmp, "| разделов линтера | 26 | 34 | восемь новых разделов (27–34) |")
    originals.append((target, None))
    write(target, text)
    return "в черновике письма прирост сходится с обеими ячейками (ожидается тишина)"

def c69_gitignore_rule_removed(tmp, originals):          # lint §39 — строка-правило исключения снята
    p = os.path.join(tmp, ".gitignore")
    if not os.path.exists(p):
        raise MutationNotApplied("нет .gitignore")
    t = read(p)
    originals.append((p, t))
    # Снимаем именно строку-правило, а не упоминание в комментарии: иначе проверку можно обмануть
    # комментарием, и канарейка проходила бы по неверной причине.
    lines = [l for l in t.splitlines() if l.strip().rstrip("/") != "_staging/telegram"]
    if len(lines) == len(t.splitlines()):
        raise MutationNotApplied("нет строки-правила для рабочих выгрузок")
    write(p, "\n".join(lines) + "\n")
    return "из .gitignore убрана строка-правило для рабочих выгрузок Telegram"

def c188_local_writer_refuses_existing_block(tmp, originals):
    import glob as _glob
    if len(_glob.glob(os.path.join(tmp, "_staging", "local", "*grace*.py"))) != 1:
        # Райтер GRACE — инструмент экземпляра: без него отказ проверять не на чем.
        raise MutationNotApplied("нет райтера GRACE в _staging/local/")
    raw_dir = os.path.join(tmp, "raw", "GRACE")
    mirror_dir = os.path.join(tmp, "wiki", "sources", "raw", "GRACE")
    grace_dir = os.path.join(tmp, "_staging", "local", "grace")
    created_dirs = []
    for path in (raw_dir, mirror_dir, grace_dir):
        if not os.path.isdir(path):
            os.makedirs(path)
            created_dirs.append(path)
    wrapper = os.path.join(tmp, "_toolkit", "raw_writer_probe.py")
    originals.append((wrapper, None))
    write(wrapper, "import glob, os, runpy, sys\n"
                   "root = os.path.dirname(os.path.dirname(__file__))\n"
                   "paths = glob.glob(os.path.join(root, '_staging', 'local', '*grace*.py'))\n"
                   "if len(paths) != 1:\n"
                   "    raise SystemExit('writer target not unique')\n"
                   "sys.argv = [paths[0], '--wiki', os.path.dirname(os.path.dirname(__file__))]\n"
                   "runpy.run_path(paths[0], run_name='__main__')\n")
    for base in (raw_dir, mirror_dir):
        for name in os.listdir(base):
            path = os.path.join(base, name)
            if name.endswith(".md"):
                originals.append((path, read(path)))
    image_texts = os.path.join(grace_dir, "image-texts.json")
    originals.append((image_texts, read(image_texts) if os.path.exists(image_texts) else None))
    report = os.path.join(grace_dir, "images-9999-canary.json")
    originals.append((report, None))
    write(report, json.dumps({
        "contract_version": "1.0",
        "kind": "image-transcription",
        "items": [{
            "file": "canary.png",
            "description": "Машинное описание",
            "visible_text": "Распознанный текст",
            "ocr_verified": True,
        }],
    }, ensure_ascii=False))
    text = ("---\ntitle: Canary\ntype: article\ncreated: 2026-09-24\nupdated: 2026-09-24\n"
            "status: active\ntags: []\nsources: []\nsummary: canary\n---\n"
            "Canary body ![[canary.png]]\n\n"
            "## Иллюстрации (распознанный текст)\n\nстарый блок\n\n"
            "## Хвост\n\nKEEP\n")
    master = os.path.join(raw_dir, "canary.md")
    mirror = os.path.join(mirror_dir, "canary.md")
    originals.extend([(master, None), (mirror, None)])
    write(master, text)
    write(mirror, text)
    originals.extend((path, None) for path in created_dirs)
    return "существующий блок raw GRACE с хвостом без --force"


def c189_link_summary_refuses_existing_block(tmp, originals):
    raw_dir = os.path.join(tmp, "raw", "telegram")
    mirror_dir = os.path.join(tmp, "wiki", "sources", "raw", "telegram")
    summary_dir = os.path.join(tmp, "_staging", "link-summaries")
    created_dirs = []
    for path in (raw_dir, mirror_dir, summary_dir):
        if not os.path.isdir(path):
            os.makedirs(path)
            created_dirs.append(path)
    wrapper = os.path.join(tmp, "_toolkit", "raw_writer_probe.py")
    originals.append((wrapper, None))
    write(wrapper, "import glob, os, runpy, sys\n"
                   "paths = glob.glob(os.path.join(os.path.dirname(__file__), '*link_summaries.py'))\n"
                   "if len(paths) != 1:\n"
                   "    raise SystemExit('writer target not unique')\n"
                   "sys.argv = [paths[0], '--wiki', os.path.dirname(os.path.dirname(__file__)), '--write']\n"
                   "runpy.run_path(paths[0], run_name='__main__')\n")
    name = "canary-link.md"
    raw = os.path.join(raw_dir, name)
    mirror = os.path.join(mirror_dir, name)
    summary = os.path.join(summary_dir, name)
    originals.extend([(raw, None), (mirror, None), (summary, None)])
    old = ("---\ntitle: Canary\ntype: telegram_saved\ncreated: 2026-09-24\nupdated: 2026-09-24\n"
           "status: active\ntags: []\nsummary: canary\n---\n"
           "Текст\n\n## Что за ссылкой\n\nстарый блок\n\n## Хвост\n\nKEEP\n")
    write(raw, old)
    write(mirror, old)
    write(summary, "## Что за ссылкой\n\nновый блок\n")
    originals.extend((path, None) for path in created_dirs)
    return "существующий ссылочный блок raw без --force"


def c190_extracts_refuses_existing_block(tmp, originals):
    raw_dir = os.path.join(tmp, "raw", "telegram")
    mirror_dir = os.path.join(tmp, "wiki", "sources", "raw", "telegram")
    extracts_dir = os.path.join(tmp, "_staging", "extracts")
    created_dirs = []
    for path in (raw_dir, mirror_dir, extracts_dir):
        if not os.path.isdir(path):
            os.makedirs(path)
            created_dirs.append(path)
    wrapper = os.path.join(tmp, "_toolkit", "raw_writer_probe.py")
    originals.append((wrapper, None))
    write(wrapper, "import os, runpy, sys\n"
                   "target = os.path.join(os.path.dirname(__file__), 'extracts.py')\n"
                   "sys.argv = [target, 'annotate', '--wiki', os.path.dirname(os.path.dirname(__file__)),"
                   " '--source', 'canary-extract', '--write']\n"
                   "runpy.run_path(target, run_name='__main__')\n")
    name = "canary-extract.md"
    raw = os.path.join(raw_dir, name)
    mirror = os.path.join(mirror_dir, name)
    data = os.path.join(extracts_dir, "canary-extract.json")
    originals.extend([(raw, None), (mirror, None), (data, None)])
    old = ("---\ntitle: Canary\ntype: telegram_saved\ncreated: 2026-09-24\nupdated: 2026-09-24\n"
           "status: active\ntags: []\nsummary: canary\n---\n"
           "Текст\n\n## Выжимки по страницам\n\n- старый блок\n\n## Хвост\n\nKEEP\n")
    write(raw, old)
    write(mirror, old)
    write(data, json.dumps({"source": "canary-extract", "items": [{
        "concept": "canary", "target": "wiki/concepts/canary.md", "section": "S", "text": "new", "applied": False,
    }]}, ensure_ascii=False))
    originals.extend((path, None) for path in created_dirs)
    return "существующий блок выжимок raw без --force"


def c191_links_writer_refuses_divergent_mirror(tmp, originals):
    raw_root = os.path.join(tmp, "raw")
    mirror_root = os.path.join(tmp, "wiki", "sources", "raw")
    for base in (raw_root, mirror_root):
        for current, _dirs, files in os.walk(base):
            for name in files:
                if name.endswith(".md"):
                    path = os.path.join(current, name)
                    originals.append((path, read(path)))
    links_dir = os.path.join(tmp, "_staging", "telegram", "canary")
    if not os.path.isdir(links_dir):
        os.makedirs(links_dir)
        originals.append((links_dir, None))
    wrapper = os.path.join(tmp, "_toolkit", "raw_writer_probe.py")
    originals.append((wrapper, None))
    write(wrapper, "import glob, os, runpy, sys\n"
                   "paths = glob.glob(os.path.join(os.path.dirname(__file__), 'links_into_records.py'))\n"
                   "if len(paths) != 1:\n"
                   "    raise SystemExit('writer target not unique')\n"
                   "sys.argv = [paths[0], '--wiki', os.path.dirname(os.path.dirname(__file__)), '--write']\n"
                   "runpy.run_path(paths[0], run_name='__main__')\n")
    name = "canary-links.md"
    raw = os.path.join(raw_root, "telegram", name)
    mirror = os.path.join(mirror_root, "telegram", name)
    links = os.path.join(links_dir, "links.json")
    originals.extend([(raw, None), (mirror, None), (links, None)])
    master_text = ("---\ntitle: Canary\ntype: telegram_saved\nsource_message_id: 987654321\n"
                   "status: active\ntags: []\nsummary: canary\n---\nтут.\n")
    mirror_text = master_text.replace("тут.", "тут. независимая копия")
    write(raw, master_text)
    write(mirror, mirror_text)
    write(links, json.dumps({"987654321": ["https://example.com/canary"]}))
    return "независимое зеркало отличается от мастера"


def c192_recursion_refuses_occupied_path(tmp, originals):
    directory = os.path.join(tmp, "raw", "zz-recursion-probe")
    if not os.path.isdir(directory):
        os.makedirs(directory)
        originals.append((directory, None))
    path = os.path.join(directory, "probe-source.md")
    originals.append((path, None))
    write(path, "KEEP\n")
    return "временный raw-путь канарейки уже занят"


def c193_area_path_literal(tmp, originals):
    p = page(tmp, "_toolkit/build_index.py")
    text = read(p)
    originals.append((p, text))
    write(p, text.rstrip("\n") + "\nAREA_PROBE = os.path.join(root, 'raw', 'probe')\n")
    return "в механизме появился прямой literal пути raw"


def c194_area_path_api_benign(tmp, originals):
    p = page(tmp, "_toolkit/build_index.py")
    text = read(p)
    originals.append((p, text))
    write(p, text.rstrip("\n") + "\nAREA_PROBE = toolkit.raw('.', 'probe')\n")
    return "путь raw построен через toolkit"


def c204_area_script_api_literal(tmp, originals):
    p = page(tmp, "_toolkit/canary_recursion.py")
    text = read(p)
    originals.append((p, text))
    call = "toolkit.area(root, " + repr("probe.py") + ")"
    write(p, text.rstrip("\n") + "\nAREA_PROBE = " + call + "\n")
    return "в механизме появился toolkit.area для поиска mechanism-скрипта"


def b205_area_script_api_benign(tmp, originals):
    p = page(tmp, "_toolkit/canary_recursion.py")
    text = read(p)
    originals.append((p, text))
    call = "toolkit.script(" + repr("probe.py") + ", root)"
    write(p, text.rstrip("\n") + "\nSCRIPT_PROBE = " + call + "\n")
    return "меchanism-скрипт построен через toolkit.script"


def c195_schema_local_missing_tag(tmp, originals):
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import schema
    p = page(tmp, "SCHEMA.local.md")
    text = read(p)
    token = next(iter(sorted(schema.taxonomy(tmp))), None)
    if token is None:
        raise MutationNotApplied("в локальной taxonomy нет тегов")
    originals.append((p, text))
    write(p, text.replace(f"`{token}`", "`canary-removed-tag`", 1))
    return "из локальной taxonomy убран используемый тег"


def c196_schema_local_rewritten_benign(tmp, originals):
    p = page(tmp, "SCHEMA.local.md")
    text = read(p)
    originals.append((p, text))
    write(p, text)
    return "локальная taxonomy перезаписана тем же содержимым"


def c197_contract_local_kind_removed(tmp, originals):
    p = page(tmp, "_staging/contract-v1.local.json")
    data = json.loads(read(p))
    report = page(tmp, "_staging/audit/candidates-draft-a.json")
    kind = json.loads(read(report)).get("kind") if os.path.exists(report) else None
    if kind not in data:
        raise MutationNotApplied("в локальном контракте нет вида отчёта из проверяемого файла")
    originals.append((p, read(p)))
    del data[kind]
    write(p, json.dumps(data, ensure_ascii=False, indent=1) + "\n")
    return "из локального контракта убран объявленный вид отчёта"


def c198_owner_markup_removed(tmp, originals):   # постоянная разметка без роли материала
    """Из постоянной разметки владельца убрана роль материала: приёмка обязана отказаться.

    Самодостаточная: не ждёт каталога отбора от экземпляра — объявляет ключ и кладёт в копию разметку
    без роли, требует отказа с текстом про постоянную разметку. Раньше уходила в «цель отсутствует»,
    и строка реестра держалась ничем (разбор 2026-09-26).
    """
    import json as _json
    import re as _re
    decl = page(tmp, "_staging/domain.local.tsv")
    text = read(decl)
    if _re.search(r"(?m)^owner_markup\t", text):
        raise MutationNotApplied("в декларации копии уже объявлен owner_markup — мутировать нечего")
    originals.append((decl, text))
    write(decl, text.rstrip("\n") + "\nowner_markup\t_staging/owner-markup.local.json\n")
    p = page(tmp, "_staging/owner-markup.local.json")
    originals.append((p, None))
    write(p, _json.dumps({"selection": {"context_field": "context"}}, ensure_ascii=False, indent=1) + "\n")
    return "в копии объявлен owner_markup, из разметки убрана роль материала"


def c199_schema_local_value_returned(tmp, originals):
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import schema
    p = page(tmp, "_toolkit/SCHEMA.md")
    text = read(p)
    token = next((x for x in sorted(schema.taxonomy(tmp)) if x not in text), None)
    if token is None:
        raise MutationNotApplied("в локальной taxonomy нет свободного значения для мутации")
    originals.append((p, text))
    write(p, text.rstrip("\n") + f"\n\nЛокальный пример: `{token}`.\n")
    return f"в SCHEMA.md вернулось локальное taxonomy-значение `{token}`"


def c200_schema_rewritten_benign(tmp, originals):
    p = page(tmp, "_toolkit/SCHEMA.md")
    text = read(p)
    originals.append((p, text))
    write(p, text)
    return "SCHEMA.md перезаписан тем же содержимым"


def c201_schema_date_returned(tmp, originals):
    p = page(tmp, "_toolkit/SCHEMA.md")
    text = read(p)
    originals.append((p, text))
    write(p, text.rstrip("\n") + "\n\nДата проверки: 2001-02-03.\n")
    return "в SCHEMA.md вернулась хронологическая дата"


def c202_schema_local_path_returned(tmp, originals):
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import schema
    p = page(tmp, "_toolkit/SCHEMA.md")
    text = read(p)
    paths = re.findall(r"`([^`\n]+)`", schema.section(tmp, "Локальные пути"))
    token = next((x for x in paths if x not in text and x not in {"raw/", "wiki/", "_staging/"}), None)
    if token is None:
        raise MutationNotApplied("в локальной схеме нет свободного пути для мутации")
    originals.append((p, text))
    write(p, text.rstrip("\n") + f"\n\nЛокальный путь: `{token}`.\n")
    return f"в SCHEMA.md вернулся локальный путь `{token}`"


def c203_schema_domain_marker_returned(tmp, originals):
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import domain
    p = page(tmp, "_toolkit/SCHEMA.md")
    text = read(p)
    marker = next((x for x in sorted(set(domain.markers(tmp) + domain.topics(tmp))) if x not in text), None)
    if marker is None:
        raise MutationNotApplied("в объявлении домена нет свободного слова для мутации")
    originals.append((p, text))
    write(p, text.rstrip("\n") + f"\n\nСлово домена: `{marker}`.\n")
    return f"в SCHEMA.md вернулось слово домена `{marker}`"


def c206_page_outside_wave(tmp, originals):   # §94
    """Страница слоя знаний вне записей волн: наполнение пошло мимо кассы.

    Случай волны 1 AgentWiki: карточки и страницы собраны скриптами, линтер в нуле, а волны нет.
    Канарейка кладёт страницу, которой нет ни в одной записи, и ждёт сторожа §94.
    """
    p = page(tmp, "wiki/concepts/canary-outside-wave.md")
    if os.path.exists(p):
        raise MutationNotApplied("страница canary-outside-wave уже есть — мутировать нечего")
    originals.append((p, None))
    write(p, "---\ntitle: \"Канарейка вне волны\"\ntype: concept\ncreated: 2026-09-25\n"
             "updated: 2026-09-25\nstatus: active\ntags: [music/history]\n"
             "sources: [raw/fixture/pyat-kompozitorov-epohi-barokko.md]\n"
             "source-stance-default: supports\nsummary: \"страница мимо кассы\"\nconfidence: low\n"
             "last-verified: 2026-09-25\nverification-status: current\nevidence: practitioner-opinion\n"
             "own-analysis: false\n---\n\n# Канарейка вне волны\n\n## Определение\n\n"
             "Страница, которой нет ни в одной записи волны.\n\n## Связи\n\n"
             "- [[baroque-era]] — сосед по эпохе.\n- [[mozart-effect]] — сосед по смыслу.\n")
    return "в вики положена страница вне записей волн"


def c207_wave_record_rewritten_benign(tmp, originals):   # тишина
    """Запись волны перезаписана тем же содержимым: сторож волн молчит на identical."""
    recs = sorted(glob.glob(os.path.join(tmp, "_staging", "audit", "ingest-wave-*.json")))
    if not recs:
        raise MutationNotApplied("записей волн нет — мутировать нечего")
    p = recs[-1]
    text = read(p)
    originals.append((p, text))
    write(p, text)
    return "запись волны перезаписана без изменений"


def c180_mechanism_doc_names_instance_tool(tmp, originals):   # §91: имя инструмента экземпляра
    """Файл механизма называет инструмент экземпляра — поставка несёт чужой архив.

    Имя берётся из папки экземпляра, а не пишется литералом: иначе этот же сторож сработал бы на самом
    харнессе, чей код лежит в файле механизма (проверено: первая версия канарейки именно так и падала).
    """
    tools = sorted(f for f in os.listdir(os.path.join(tmp, "_staging", "local"))
                   if f.endswith(".py"))
    if not tools:
        raise MutationNotApplied("в _staging/local нет инструментов экземпляра")
    p = page(tmp, "_toolkit/style.md")
    text = read(p)
    if not text.strip():
        raise MutationNotApplied("_toolkit/style.md пуст")
    originals.append((p, text))
    write(p, text.rstrip("\n") + f"\n\nСтупени архива собирает `{tools[0]}` — так работали с этим корпусом.\n")
    return f"документ механизма назвал инструмент экземпляра ({tools[0]})"

def c182_topic_word_in_mechanism(tmp, originals):   # §91: слова тем, а не только метки скиллов
    """В файл механизма вернулось слово темы экземпляра.

    Сторож читал только метки скиллов и молчал, когда в файлах механизма стояли темы экземпляра: раскладка
    кандидатов делила чужой корпус по двум темам владельца, а словарь контрактов нёс его вид отчёта. Слова
    берутся из объявления — канарейка обязана ловить именно тот домен, который объявил этот экземпляр.
    """
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import domain as _domain
    words = _domain.topics(tmp)
    if not words:
        raise MutationNotApplied("объявление домена не называет тем — канарейке нечего вставлять")
    p = page(tmp, "_toolkit/lint-pass.md")
    text = read(p)
    if not text.strip():
        raise MutationNotApplied("_toolkit/lint-pass.md пуст")
    originals.append((p, text))
    write(p, text.rstrip("\n") + f"\n\nТема экземпляра в файле механизма: {words[0]}.\n")
    return f"в служебный документ механизма вписано слово темы «{words[0]}»"


def c183_topic_word_in_filename(tmp, originals):    # §91: слово темы в имени файла механизма
    """Имя файла механизма несёт слово темы экземпляра.

    Название — часть файла: пока имя инструмента говорило «before_may» и прочие слова владельца, инструмент
    механизма выглядел инструментом одного архива. Проверка текста такое имя пропускала.
    """
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import domain as _domain
    words = _domain.topics(tmp)
    if not words:
        raise MutationNotApplied("объявление домена не называет тем — канарейке нечего вставлять")
    p = page(tmp, f"_toolkit/lint-{words[0]}-pass.md")
    if os.path.exists(p):
        raise MutationNotApplied(f"{os.path.basename(p)} уже есть")
    originals.append((p, None))                       # файла не было — снести после прогона
    write(p, "# Служебная страница механизма\n")
    return f"создан файл механизма с именем, несущим слово темы «{words[0]}»"


def c184_case_new_opener(tmp, originals):           # §87: маркеры «Так нашлась», «Так вышло»
    """Разбор случая открыт маркером, которого сторож не знал.

    Половина разборов начинается не с «Урок» и не с «Заведён», а с «Так нашлась ... <дата>»: прежний список
    маркеров их пропускал, и служебный документ продолжал носить хронику.
    """
    p = page(tmp, "_toolkit/lint-pass.md")
    text = read(p)
    if not text.strip():
        raise MutationNotApplied("_toolkit/lint-pass.md пуст")
    originals.append((p, text))
    write(p, text.rstrip("\n") + "\nТак нашлась дыра 2026-09-01: пример переписан задним числом.\n")
    return "разбор случая открыт маркером «Так нашлась»"



def b14_domain_word_inside_instance_tools(tmp, originals):   # безвредно для §91
    """Слово домена внутри области экземпляра: сторож обязан молчать.

    Проверяет не ловлю, а границу: слова домена в `_staging/local/` — это ровно то место, где они и должны
    жить. Если бы сторож считал и эту папку, разделение требовало бы вычищать слова из собственных
    инструментов экземпляра.
    """
    import domain as _domain
    markers = _domain.markers(tmp)
    if not markers:
        raise MutationNotApplied("домен экземпляра не объявлен — слов для подсадки нет")
    path = os.path.join(tmp, "_staging", "local", "README.md")
    if not os.path.exists(path):
        raise MutationNotApplied("нет _staging/local/README.md")
    text = read(path)
    originals.append((path, text))
    write(path, text.rstrip("\n") + f"\n\nЗдесь работали с корпусом {sorted(markers)[0]}.\n")
    return f"в инструменты экземпляра подсажено слово домена ({sorted(markers)[0]}), ожидается тишина"


def b13_gitignore_rewritten_same(tmp, originals):       # безвредно для §39
    p = os.path.join(tmp, ".gitignore")
    if not os.path.exists(p):
        raise MutationNotApplied("нет .gitignore")
    t = read(p)
    originals.append((p, t))
    write(p, t)
    return "gitignore перезаписан теми же байтами (ожидается тишина)"

def c65_task_refers_missing_script(tmp, originals):     # lint §38 — шаг задачи ссылается на удалённый скрипт
    p = os.path.join(tmp, "_toolkit", "tasks.py")
    if not os.path.exists(p):
        raise MutationNotApplied("нет реестра задач")
    t = read(p)
    originals.append((p, t))
    write(p, t.replace('py("registries.py")', 'py("no-such-step.py")', 1))
    return "шаг задачи ссылается на несуществующий скрипт"

def b11_tasks_rewritten_same(tmp, originals):           # безвредно для §38
    p = os.path.join(tmp, "_toolkit", "tasks.py")
    if not os.path.exists(p):
        raise MutationNotApplied("нет реестра задач")
    t = read(p)
    originals.append((p, t))
    write(p, t)
    return "реестр задач перезаписан теми же байтами (ожидается тишина)"

def c67_two_sent_rows(tmp, originals):                  # мемориал снятого правила: две отправки в день законны
    p = _ledger_path(tmp)
    if not p:
        raise MutationNotApplied("цель не объявлена экземпляром (реестр передач): канарейка не применима")
    if not os.path.exists(p):
        raise MutationNotApplied("нет реестра слепков")
    lines = read(p).splitlines()
    head = lines[0].split("\t")
    if "kind" not in head:
        raise MutationNotApplied("в реестре нет колонки kind")
    ki = head.index("kind")
    for i, l in enumerate(lines[1:], 1):
        r = l.split("\t")
        if r[0] == "2026-09-14" and r[ki] != "sent":
            r[ki] = "sent"
            lines[i] = "\t".join(r)
            break
    else:
        raise MutationNotApplied("нет строки за 2026-09-14 с kind != sent")
    originals.append((p, read(p)))
    write(p, "\n".join(lines) + "\n")
    return "за одну дату в реестре стало две отправленных строки"

def b12_ledger_rewritten_same(tmp, originals):          # безвредно для §27
    p = _ledger_path(tmp)
    if not p:
        raise MutationNotApplied("цель не объявлена экземпляром (реестр передач): канарейка не применима")
    if not os.path.exists(p):
        raise MutationNotApplied("нет реестра слепков")
    t = read(p)
    originals.append((p, t))
    write(p, t)
    return "реестр слепков перезаписан теми же байтами (ожидается тишина)"

def c71_literature_without_row(tmp, originals):   # lint §40 — литературе нет строки в блоке «Литература»
    """Упоминание подборки вычищается из блока «Литература» целиком.

    Раньше канарейка снимала одну строку таблицы, но сторож §40 проверяет не строку, а наличие слага в блоке —
    строка была не единственной, и мутация ничего не ломала (разобрано 2026-09-21: слаг встречался в блоке четыре
    раза). Теперь вычищаются все упоминания: тогда адреса у метки владельца действительно нет.
    """
    p = page(tmp, "wiki/comparisons/materials-registry.md")
    if not os.path.exists(p):
        raise MutationNotApplied("нет реестра материалов")
    text = read(p)
    slug = "podborka-materialov-chtoby-bystree"
    m = re.search(r"(?ms)^##\s+Литература\s*$(.*?)(?=^##\s|\Z)", text)
    if not m or slug not in m.group(1):
        raise MutationNotApplied("в блоке «Литература» нет упоминаний подборки 113402")
    originals.append((p, text))
    block = m.group(1)
    write(p, text[:m.start(1)] + block.replace(slug, "podborka-snyata-kanareykoy") + text[m.end(1):])
    return "упоминания подборки вычищены из блока «Литература»"

def b14_literature_row_moved(tmp, originals):     # безвредно для §40: строка осталась в том же блоке
    p = page(tmp, "wiki/comparisons/materials-registry.md")
    text = read(p)
    slug = "podborka-materialov-chtoby-bystree"
    rows = [l for l in text.split("\n") if l.startswith("|") and slug in l]
    tail = "\n## Материалы из голых ссылок"
    if not rows or tail not in text:
        raise MutationNotApplied("реестр не в ожидаемой форме")
    originals.append((p, text))
    row = rows[0]
    moved = text.replace(row + "\n", "", 1)
    write(p, moved.replace(tail, "\n" + row + tail, 1))
    return "строка литературы перенесена в конец того же блока — адрес не потерян"

def c72_illustration_file_missing(tmp, originals):   # lint §41 — описан файл, которого нет в папках images
    p = page(tmp, "raw/telegram/2026-05-13-superpoziciya-gipotez-i-upravlyaemyy-kollaps-vmesto-obychnog-102943.md")
    name = "photo_459@13-05-2026_12-25-21.jpg"
    if not os.path.exists(p):
        raise MutationNotApplied("нет источника с иллюстрацией")
    text = read(p)
    if f"### {name}" not in text:
        raise MutationNotApplied("в источнике нет заголовка описанной картинки")
    originals.append((p, text))
    write(p, text.replace(f"### {name}", "### photo_459-pereimenovan.jpg", 1))
    return "описанный файл переименован в заголовке — такого файла в папках images нет"

def c73_illustration_without_embed(tmp, originals):  # lint §41 — описание без вставки
    rel = "2026-05-13-superpoziciya-gipotez-i-upravlyaemyy-kollaps-vmesto-obychnog-102943.md"
    src = page(tmp, "raw/telegram/" + rel)
    mir = page(tmp, "wiki/sources/raw/telegram/" + rel)
    name = "photo_459@13-05-2026_12-25-21.jpg"
    for p in (src, mir):
        if not os.path.exists(p):
            raise MutationNotApplied(f"нет файла {os.path.basename(p)}")
        t = read(p)
        if f"![[{name}]]" not in t:
            raise MutationNotApplied("в источнике нет вставки картинки")
        originals.append((p, t))
    new = read(src).replace(f"![[{name}]]\n\n", "", 1)
    write(src, new)
    write(mir, new)                                  # зеркало обязано совпадать с мастером байт в байт
    return "вставка ![[…]] снята с описания картинки (мастер и зеркало синхронно)"

def b15_illustration_rewritten_same(tmp, originals):  # безвредно для §41
    p = page(tmp, "raw/telegram/2026-05-13-superpoziciya-gipotez-i-upravlyaemyy-kollaps-vmesto-obychnog-102943.md")
    if not os.path.exists(p):
        raise MutationNotApplied("нет источника с иллюстрацией")
    text = read(p)
    originals.append((p, text))
    write(p, text)
    return "источник с иллюстрацией перезаписан тем же содержимым"

def c76_inline_footnote(tmp, originals):     # lint §11 — инлайновые сноски в теле страницы запрещены
    p = page(tmp, "wiki/concepts/loop-engineering.md")
    if not os.path.exists(p):
        raise MutationNotApplied("нет страницы loop-engineering")
    text = read(p)
    originals.append((p, text))
    write(p, text.rstrip("\n") + "\n\nАбзац со сноской-якорем ^[[Голощапов_manifest]] — так провенанс не пишется.\n")
    return "в тело страницы добавлена инлайновая сноска ^[[…]]"

def c84_broken_date(tmp, originals):     # lint §4 — дата в шапке не в формате ГГГГ-ММ-ДД
    p = page(tmp, "wiki/concepts/loop-engineering.md")
    text = read(p)
    # Дату берём из самой страницы: константа протухла, когда дети подняли updated: (2026-09-21).
    m = re.search(r"(?m)^updated: (\d{4}-\d{2}-\d{2})$", text)
    if not m:
        raise MutationNotApplied("в шапке нет поля updated в формате ГГГГ-ММ-ДД")
    originals.append((p, text))
    write(p, text.replace(m.group(0), "updated: " + m.group(1) + "13", 1))
    return "дата в шапке склеена: updated: %s13" % m.group(1)

def c82_candidate_decision_removed(tmp, originals):     # lint §43 — у кандидата в страницы снято решение
    p = os.path.join(tmp, "_staging", "candidate-decisions.tsv")
    if not os.path.exists(p):
        raise MutationNotApplied("нет очереди решений по кандидатам (candidate-decisions.tsv)")
    text = read(p)
    rows = [l for l in text.split("\n") if l.strip()]
    keep = [l for l in rows if not l.startswith("compaction\t")]
    if len(keep) == len(rows):
        raise MutationNotApplied("в очереди нет строки отказа по «compaction»")
    originals.append((p, text))
    write(p, "\n".join(keep) + "\n")
    return "из очереди решений убрана строка отказа по кандидату «compaction» (кандидат снова висящий)"

def b19_decisions_rewritten_same(tmp, originals):       # безвредно для §43
    p = os.path.join(tmp, "_staging", "candidate-decisions.tsv")
    if not os.path.exists(p):
        raise MutationNotApplied("нет очереди решений по кандидатам")
    text = read(p)
    originals.append((p, text))
    write(p, text)
    return "очередь решений по кандидатам перезаписана тем же содержимым"

def _backlink_mirror(tmp):
    p = page(tmp, "wiki/sources/raw/telegram/2026-09-09-hydrafusion-rantaym-orkestraciya-neskolkih-modeley-v-github-114095.md")
    if not os.path.exists(p):
        raise MutationNotApplied("нет копии источника с разделом «Где использован»")
    return p

def c78_backlink_bad_section(tmp, originals):    # lint §42 — ссылка на участок, которого нет
    p = _backlink_mirror(tmp)
    text = read(p)
    old = "- [[inference-routing-and-cost#Факты и цифры]]"
    if old not in text:
        raise MutationNotApplied("нет ожидаемой ссылки на участок")
    originals.append((p, text))
    write(p, text.replace(old, "- [[inference-routing-and-cost#Раздел, которого нет]]", 1))
    return "ссылка на участок, которого на странице нет"

def c79_backlink_block_missing(tmp, originals):  # lint §42 — раздела нет в копии
    p = _backlink_mirror(tmp)
    text = read(p)
    i = text.find("## Где использован")
    if i < 0:
        raise MutationNotApplied("нет раздела «Где использован»")
    originals.append((p, text))
    write(p, text[:i].rstrip("\n") + "\n")
    return "раздел «Где использован» вырезан из копии источника"

def c80_backlink_page_not_declared(tmp, originals):   # lint §42 — раздел называет страницу-негодяя
    p = _backlink_mirror(tmp)
    text = read(p)
    originals.append((p, text))
    write(p, text.rstrip("\n") + "\n- [[mastra]]\n")
    return "в раздел добавлена страница, которая источник не объявляет"

def b18_backlink_block_rewritten_same(tmp, originals):   # безвредно для §42
    p = _backlink_mirror(tmp)
    text = read(p)
    originals.append((p, text))
    write(p, text)
    return "копия источника с разделом перезаписана тем же содержимым"

def _map_disputes_row(tmp):
    """Первая строка таблицы споров карты конфликтов: строка файла и её ячейки."""
    p = _cmap_page(tmp)
    if not p:
        raise MutationNotApplied("цель не объявлена экземпляром (карта конфликтов): канарейка не применима")
    if not os.path.exists(p):
        raise MutationNotApplied("нет карты конфликтов")
    text = read(p)
    head = text.find("## Таблица конфликтов")
    if head < 0:
        raise MutationNotApplied("в карте нет сводной таблицы")
    tail = text[head:]
    for ln in tail.split("\n"):
        if ln.startswith("|"):
            cs = [x.strip() for x in ln.strip().strip("|").split("|")]
            if cs and re.match(r"^\**\d+\**$", cs[0]) and len(cs) > 3:
                return p, text, ln, cs
    raise MutationNotApplied("в сводной таблице нет строк споров")

def c91_telegraphic_position(tmp, originals):   # lint §45 — позиция спора написана телеграфной строкой
    p, text, ln, cs = _map_disputes_row(tmp)
    telegraph = "Decision Gate >50 инструментов → skills, оптимум 30–40"
    if cs[2] == telegraph:
        raise MutationNotApplied("ячейка уже содержит телеграфную строку")
    cells = list(cs)
    cells[2] = telegraph
    originals.append((p, text))
    write(p, text.replace(ln, "| " + " | ".join(cells) + " |"))
    return "позиция A спора №" + cells[0] + " заменена цепочкой со стрелкой"

def b20_rewritten_position_benign(tmp, originals):   # безвредно для §45 — та же ячейка полным предложением
    p, text, ln, cs = _map_disputes_row(tmp)
    written = ("Крестников требует переносить инструменты в навыки, когда их становится больше пятидесяти, "
               "и держать рабочий оптимум в тридцать — сорок, потому что при приближении к сотне модель "
               "перестаёт выбирать подходящий инструмент ([[Крестников_manifest]]).")
    cells = list(cs)
    cells[2] = written
    originals.append((p, text))
    write(p, text.replace(ln, "| " + " | ".join(cells) + " |"))
    return "позиция A спора №" + cells[0] + " переписана полным предложением"

PROSE_COL_CANARY = re.compile(r"(позици|первопричин|статус|аргумент|решение|чем подтверждена|почему|что утверждает|что болит|практики корпуса|вендорские руководства)", re.IGNORECASE)

def _prose_rows(tmp, rel):
    """Строки таблиц страницы: (индекс строки в файле, ячейки, заголовки колонок)."""
    p = page(tmp, rel)
    if not os.path.exists(p):
        raise MutationNotApplied("нет страницы " + rel)
    lines = read(p).split("\n")
    out, head = [], None
    for n, ln in enumerate(lines):
        if ln.startswith("|"):
            cs = [x.strip() for x in ln.strip().strip("|").split("|")]
            if all(re.fullmatch(r":?-{2,}:?", x) for x in cs if x):
                continue
            if head is None:
                head = cs
                continue
            out.append((n, cs, head))
        elif ln.startswith("## ") or ln.startswith("# "):
            head = None
    if not out:
        raise MutationNotApplied("в таблицах нет строк данных")
    return p, lines, out

def c93_telegraphic_prose_cell(tmp, originals):   # lint §45 — смысловая ячейка написана назывной строкой
    # Страницы перебираются: на части из них все смысловые ячейки уже нарушают правило, и мутировать
    # там нечего (нужна ячейка, которая правило ПРОХОДИТ, — иначе мутация не создаёт новую находку).
    for rel in ("wiki/concepts/prompt-determinism-and-hooks.md",
                "wiki/comparisons/vendor-guidance-vs-corpus.md",
                "wiki/comparisons/pain-points-and-fixes.md"):
        if not os.path.exists(page(tmp, rel)):
            continue
        p, lines, rows = _prose_rows(tmp, rel)
        for n, cs, head in rows:
            for i, cell in enumerate(cs):
                col = (head[i] if i < len(head) else "").strip("* ")
                if not PROSE_COL_CANARY.search(col):
                    continue
                if not re.search(r"[.!?]", cell) or len(cell) < 120 or "→" in cell:
                    continue                 # берём ячейку, которая сейчас правило проходит
                cells = list(cs)
                cells[i] = "лишний слой абстракции, зависимость и поддержка своими руками"
                originals.append((p, "\n".join(lines)))
                lines[n] = "| " + " | ".join(cells) + " |"
                write(p, "\n".join(lines))
                return "ячейка «%s» строки «%.30s…» (%s) заменена назывной строкой" % (col, cs[0], rel.split("/")[-1])
    raise MutationNotApplied("ни на одной из страниц нет смысловой ячейки, проходящей правило")

def _first_prose_cell(tmp, rel, cols, need_compliant):
    """Первая ячейка смысловой колонки страницы: проходящая правило или нарушающая его."""
    p, lines, rows = _prose_rows(tmp, rel)
    for n, cs, head in rows:
        for i, cell in enumerate(cs):
            col = (head[i] if i < len(head) else "").strip("* ")
            if not any(w in col.lower() for w in cols):
                continue
            compliant = re.search(r"[.!?]", cell) and len(cell) >= 120 and "→" not in cell
            if compliant == need_compliant:
                return p, lines, rows, n, cs, i, col
    return None

def b21_prose_cell_rewritten_benign(tmp, originals):   # безвредно для §45 — ячейка переписана иначе
    # Безвредная канарейка НЕ должна зависеть от наличия дефекта: на переписанной вики нарушающих ячеек
    # нет, и прежняя версия объявляла себя ложной («мутация не применилась — цель устарела», прогон
    # 2026-09-16). Поэтому берём ячейку, которая правило проходит, и переписываем её другой полной фразой.
    hit = None
    for rel in ("wiki/comparisons/pain-points-and-fixes.md", "wiki/comparisons/vendor-guidance-vs-corpus.md"):
        hit = _first_prose_cell(tmp, rel, ("первопричина", "решение", "позиция"), True)
        if hit:
            break
    if not hit:
        raise MutationNotApplied("нет смысловой ячейки, проходящей правило")
    p, lines, rows, n, cs, i, col = hit
    keys = re.findall(r"\[\[[^\]]+\]\]", cs[i])
    cells = list(cs)
    cells[i] = ("Причина проста: весь контекст проекта грузится в окно одной порцией, и внимание агента "
                "уходит на шум вместо задачи, поэтому решения дешевеют ещё до первого шага работы."
                + (" " + " ".join(keys) if keys else ""))
    originals.append((p, "\n".join(lines)))
    lines[n] = "| " + " | ".join(cells) + " |"
    write(p, "\n".join(lines))
    return "ячейка «%s» строки «%.30s…» переписана другой полной фразой" % (col, cs[0])

def b22_prose_cell_broken_and_restored(tmp, originals):   # безвредно: ломаем ячейку и возвращаем как было
    # Итог мутации — исходное состояние, поэтому новых находок быть не должно; проверяет, что у проверки
    # нет памяти и побочных эффектов между прогонами. Применима на любой вики, в том числе чистой.
    hit = None
    for rel in ("wiki/comparisons/pain-points-and-fixes.md", "wiki/comparisons/tests-for-agents.md"):
        hit = _first_prose_cell(tmp, rel, ("первопричина", "решение", "позиция", "аргумент"), True)
        if hit:
            break
    if not hit:
        raise MutationNotApplied("нет смысловой ячейки, проходящей правило")
    p, lines, rows, n, cs, i, col = hit
    broken = list(cs)
    broken[i] = "шум в контексте, всё грузится сразу"
    broken_text = "\n".join(lines[:n] + ["| " + " | ".join(broken) + " |"] + lines[n + 1:])
    originals.append((p, "\n".join(lines)))
    write(p, broken_text)                     # сначала ломаем
    write(p, "\n".join(lines))               # и сразу возвращаем исходное
    return "ячейка «%s» строки «%.30s…» сломана и возвращена как была" % (col, cs[0])

def b23_internal_ref_with_line_number(tmp, originals):   # безвредно для §34 — сноска «путь:строка»
    # Отчёт прохода ссылается на места в файлах видом `wiki/…/<страница>.md:97`. Это сноска, а не
    # утверждение о существовании имени: раздел обязан молчать (правка 2026-09-16).
    doc = os.path.join(tmp, "_staging", "audit", "lint-pass-1999-01-01.md")
    os.makedirs(os.path.dirname(doc), exist_ok=True)
    originals.append((doc, None))          # файла не было — удалим после прогона
    write(doc, "# Проход lint, 1999-01-01\n\n"
               "- `wiki/comparisons/example-page.md:97` — сноска на место в существующем файле.\n"
               "- `wiki/concepts/harness-architecture.md:12-15` — сноска диапазоном.\n")
    return "служебный документ ссылается на существующие файлы с номером строки"

def c97_provenance_footnote_raw(tmp, originals):
    """lint §11 — инлайновая сноска-источник ^[raw/…]: вторая форма той же болезни."""
    p = None
    for base in ("wiki/entities", "wiki/concepts", "wiki/comparisons"):
        d = page(tmp, base)
        if os.path.isdir(d):
            cands = sorted(f for f in os.listdir(d) if f.endswith(".md"))
            if cands:
                p = os.path.join(d, cands[0])
                break
    if p is None:
        raise MutationNotApplied("нет страницы для инлайновой сноски")
    text = read(p)
    originals.append((p, text))
    write(p, text.rstrip("\n") + "\n\nПровенанс сноской ^[raw/пример_manifest.md] в теле страницы.\n")
    return "в тело страницы дописана инлайновая сноска-источник ^[raw/…]"

def _split_field_page(tmp):
    """Страница с полем split-justification: первая подходящая."""
    for rel in ("wiki/concepts/context-engineering.md", "wiki/concepts/instruction-overload.md",
                "wiki/concepts/verification-tax-and-debt.md", "wiki/concepts/harness-architecture.md"):
        p = page(tmp, rel)
        if os.path.exists(p) and "split-justification" in read(p):
            return p
    raise MutationNotApplied("нет страницы с полем split-justification")

def c98_split_number_drift(tmp, originals):
    """lint §18 — число строк в split-justification разошлось с файлом."""
    p = _split_field_page(tmp)
    text = read(p)
    originals.append((p, text))
    write(p, re.sub(r'(split-justification: "[^"\d]*)\d+(\s*строк)',
                    lambda m: m.group(1) + "1" + m.group(2), text, count=1))
    return "число строк в split-justification заменено на заведомо неверное"

def b27_split_number_in_sync(tmp, originals):   # безвредно для §18
    p = _split_field_page(tmp)
    text = read(p)
    originals.append((p, text))
    # Считаем как линтер: без завершающих пустых строк — хвостовой перевод строки не содержимое.
    n = len(text.rstrip("\n").split("\n"))
    write(p, re.sub(r'(split-justification: "[^"\d]*)\d+(\s*строк)',
                    lambda m: m.group(1) + str(n) + m.group(2), text, count=1))
    return "поле split-justification переписано с верным числом строк"

def _new_source_file(tmp, date, stem="canary"):
    """Положить в raw/ источник с датой добавления — заготовка для §46."""
    d = os.path.join(tmp, "raw", "telegram")
    if not os.path.isdir(d):
        os.makedirs(d)
    p = os.path.join(d, "2026-09-16-canary-source-%s.md" % stem)
    write(p, "---\ntitle: \"Проверочный источник канарейки\"\ntype: telegram_saved\nsources: [telegram]\n"
             "source_channel: \"canary\"\nsource_date: %s\ncreated: %s\ningested: %s\n---\n\nТекст.\n"
          % (date, date, date))
    return p

def c110_queue_newer_than_panel(tmp, originals):   # §50
    """Очередь кандидатов изменилась, панель не пересобирали — владелец увидит старые данные.

    Мутация меняет СОДЕРЖИМОЕ очереди, а не время правки: сторож сравнивает хеши, потому что первая
    версия на mtime давала двадцать ложных срабатываний на безвредных перезаписях тем же содержимым.
    """
    q = page(tmp, "_staging/prose-candidates.tsv")
    if not os.path.exists(q):
        raise MutationNotApplied("нет очереди кандидатов")
    text = read(q)
    originals.append((q, text))
    write(q, text.rstrip("\n") + "\nkanareyka-paneli\tКанарейка панели\tcomparisons/tools-registry.md:1\t2026-09-16\n")
    return "в очередь кандидатов добавлен термин, панель не пересобрана"

def b33_panels_rebuilt(tmp, originals):   # безвредно для §50
    """безвредно: очередь перезаписана тем же содержимым — данные не изменились, панель не устарела."""
    q = page(tmp, "_staging/prose-candidates.tsv")
    if not os.path.exists(q):
        raise MutationNotApplied("нет очереди кандидатов")
    text = read(q)
    originals.append((q, text))
    write(q, text)          # содержимое то же, меняется только время правки
    return "очередь перезаписана тем же содержимым"
def c108_pain_section_outside_categories(tmp, originals):   # §48
    """Боли заведены отдельным разделом вместо категории — так вернулась бы свалка."""
    src = page(tmp, "wiki/comparisons/pain-points-and-fixes.md")
    text = read(src)
    originals.append((src, text))
    heap = ("## 9. Боли из этого источника\n\n"
            "| Боль (симптом и пример) | Первопричина | Решение | Источник |\n"
            "|---|---|---|---|\n"
            "| Свалка болей источника в отдельном разделе, потому что раскладывать по категориям долго | "
            "Причина в том, что шаг волны не назвал категорию для строки | "
            "Решение — раскладывать боли по категориям тем же шагом, которым они заведены | "
            "[[2026-09-02-llm-wiki-karpatogo-protiv-personal-os-v2-chto-sovpalo-i-chem-113433]] |\n\n")
    # Якорь — «## Связи»: раздел есть в скелете страницы всегда. Прежний якорь «Как этим пользоваться»
    # устарел, когда раздел переименовали (полный прогон 2026-09-21 встал на этом).
    i = text.find("## Связи")
    if i < 0:
        raise MutationNotApplied("нет раздела «Связи» — некуда вставить свалку")
    write(src, text[:i] + heap + text[i:])
    return "боли заведены разделом «9. Боли из этого источника» вместо категории"

def b32_pain_row_in_category(tmp, originals):   # безвредно для §48
    """безвредно: ещё одна боль дописана в таблицу существующей категории.

    Число строк в шапке пересчитывается вместе со строкой: иначе безвредная канарейка валит §18
    (мягкий предел считает точное число строк) и объявляется ложным срабатыванием на ровном месте.
    """
    src = page(tmp, "wiki/comparisons/pain-points-and-fixes.md")
    text = read(src)
    originals.append((src, text))
    lines = text.split("\n")
    i = next((k for k, l in enumerate(lines) if l.startswith("## 1. ")), None)
    if i is None:
        raise MutationNotApplied("нет первого раздела-категории")
    last = max(k for k in range(i, len(lines)) if lines[k].startswith("|"))
    row = ("| Проверочная боль дописана в существующую категорию, чтобы убедиться, что дописывание строки "
           "не ломает форму таблицы болей и не заводит отдельного раздела для источника. | "
           "Причина у этой проверочной боли тоже проверочная: канарейка следит за формой страницы, "
           "а не за существом строки, и не должна ничего утверждать о материале корпуса. | "
           "Решение проверочное: строка остаётся строкой в таблице своей категории, как того требует "
           "канон страницы болей, и никуда не уезжает. | "
           "[[2026-09-02-llm-wiki-karpatogo-protiv-personal-os-v2-chto-sovpalo-i-chem-113433]] |")
    lines = lines[:last + 1] + [row] + lines[last + 1:]
    text = "\n".join(lines)
    body = text.split("\n")
    while body and not body[-1].strip():
        body.pop()
    text = re.sub(r"(?m)^(split-justification:\s*\")(\d+)",
                  lambda m: m.group(1) + str(len(body)), text, count=1)
    write(src, text)
    return "дописана строка в таблицу категории 1, число строк в шапке пересчитано"
def c106_person_in_tools_registry(tmp, originals):   # §49
    """В реестре инструментов заведена строка организации с её страницей — это не инструмент."""
    src = page(tmp, "wiki/comparisons/tools-registry.md")
    text = read(src)
    originals.append((src, text))
    lines = text.split("\n")
    for k, ln in enumerate(lines):
        if ln.startswith("| Codex "):
            cells = [x.strip() for x in ln.strip().strip("|").split("|")]
            cells[0] = "Anthropic"
            cells[1] = "приложение"
            lines[k] = "| " + " | ".join(cells) + " |"
            break
    else:
        raise MutationNotApplied("не нашёл строку реестра для подмены")
    write(src, "\n".join(lines))
    return "в реестре инструментов стоит Anthropic — компания со своей страницей"

def b31_tool_row_with_type(tmp, originals):   # безвредно для §49
    """безвредно: в реестр добавлена строка настоящего инструмента с типом из набора."""
    src = page(tmp, "wiki/comparisons/tools-registry.md")
    text = read(src)
    originals.append((src, text))
    rows = [m for m in re.finditer(r"(?m)^\| Codex .*$", text)]
    if not rows:
        raise MutationNotApplied("не нашёл строку реестра")
    last = [m for m in re.finditer(r"(?m)^\|.*\|\s*$", text)][0]
    row = ("| Zed | приложение | Редактор кода с открытым исходным кодом, названный в подборке инструментов "
           "как среда работы с агентом | [[2026-09-02-podborka-ssylok-ai-friendly-cli-i-universalnye-skilly-113506]] |")
    text = text[:last.end()] + "\n" + row + text[last.end():]
    write(src, text)
    return "добавлена строка инструмента с типом из набора"

def c104_row_merged_into_cell(tmp, originals):   # §48
    """Раздел болей: две боли слиты в одну строку — ячейка уходит за предел формы."""
    src = page(tmp, "wiki/comparisons/pain-points-and-fixes.md")
    text = read(src)
    originals.append((src, text))
    tail = " Слитая ячейка: " + "продолжение прошлой боли, " * 30 + "конец."
    # к первой строке данных первого раздела дописываем хвост, ломающий предел длины ячейки
    lines = text.split("\n")
    for k, ln in enumerate(lines):
        if k == _first_pain_row(text):
            cells = [x.strip() for x in ln.strip().strip("|").split("|")]
            cells[1] = cells[1] + tail
            lines[k] = "| " + " | ".join(cells) + " |"
            break
    else:
        raise MutationNotApplied("не нашёл строку данных в разделе 1")
    write(src, "\n".join(lines))
    return "ячейка «Первопричина» раздута хвостом прошлой боли"

def _first_pain_row(text):
    """Первая строка данных первой таблицы раздела болей (шапка и разделитель пропускаются).

    Почему не по имени боли: строки переставляются и получают ссылки, и привязка к «| Context Rot»
    устарела — две канарейки (104 и 105) перестали применяться и молча считались промахами.
    """
    lines = text.split("\n")
    seen_head = False
    for i, ln in enumerate(lines):
        s = ln.strip()
        if not s.startswith("|"):
            continue
        if set(s) <= set("|-: "):
            seen_head = seen_head or True
            continue
        if "болит" in s.lower() or "первопричина" in s.lower() or "решение" in s.lower():
            continue
        if seen_head or s.count("|") >= 3:
            return i
    return -1


def b30_cell_rewritten_same_shape(tmp, originals):   # безвредно для §48
    """безвредно: ячейка переписана другим полным предложением — форма держится."""
    src = page(tmp, "wiki/comparisons/pain-points-and-fixes.md")
    text = read(src)
    originals.append((src, text))
    lines = text.split("\n")
    for k, ln in enumerate(lines):
        if k == _first_pain_row(text):
            cells = [x.strip() for x in ln.strip().strip("|").split("|")]
            cells[2] = ("Практика лечит это сжатием контекста и выносом правил из файла в навыки: правила, "
                        "которые нужны не всем задачам, переезжают туда, где они уместны, и перестают занимать "
                        "постоянное место в окне модели.")
            lines[k] = "| " + " | ".join(cells) + " |"
            break
    else:
        raise MutationNotApplied("не нашёл строку данных в разделе 1")
    write(src, "\n".join(lines))
    return "ячейка «Решение» переписана другим полным предложением"

def c100_source_without_semantic_pass(tmp, originals):
    """lint §46 — источник добавлен после последнего наряда и в наряде не назван.

    Дата источника считается от порога: зашитое число перестаёт быть «позже последнего наряда», как только
    очередной наряд получает тот же день, и канарейка молча теряет чувствительность (поймано аттестацией).
    """
    sys.path.insert(0, os.path.join(tmp, "_toolkit"))
    import semantic_coverage
    _, cutoff = semantic_coverage.covered_and_cutoff(tmp)
    if not cutoff:
        # Наряда прохода нет вовсе: §46 не с чем сравнивать (молчит без cutoff), а случай
        # «проход не проводился никогда» держат ворота этапа волны, а не этот сторож.
        raise MutationNotApplied("нет наряда смыслового прохода — §46 судить не по чему")
    base = datetime.date.fromisoformat(cutoff) if re.match(r"\d{4}-\d{2}-\d{2}", cutoff or "") \
        else datetime.date.today()
    day = (base + datetime.timedelta(days=1)).isoformat()
    p = _new_source_file(tmp, day, "dirty")
    originals.append((p, None))
    return [(46, "источник добавлен %s, смыслового прохода по нему нет" % day)]

def b28_source_before_last_pass(tmp, originals):   # безвредно для §46
    """безвредно: наряд прохода свежее источника — проверка молчит.

    Ни новых файлов, ни правок мастеров: появление файла меняет отпечаток дерева (§29), а правка
    мастер-источника ловится дрейфом sha256 (§8, §31) — канарейка стала бы ложной из-за способа
    мутации, а не из-за проверки. Правим только запись наряда: она датируется днём источника, то есть
    источник не «позже последнего прохода», и требовать по нему проход не за что.
    """
    rep = page(tmp, "_staging/stance/wave-2026-09-16-semantic-113413.json")
    if not os.path.exists(rep):
        raise MutationNotApplied("нет наряда прохода")
    text = read(rep)
    originals.append((rep, text))
    data = json.loads(text)
    data["date"] = data["wave"] = "2026-09-15"   # день источника: он не позже последнего прохода
    write(rep, json.dumps(data, ensure_ascii=False, indent=1))
    return "наряд датирован днём источника (не позже последнего прохода) — прохода не требуется"

def c102_row_not_named_in_sections(tmp, originals):
    """lint §47 — строка таблицы не названа в разделах карты."""
    p = _cmap_page(tmp)
    if not p:
        raise MutationNotApplied("цель не объявлена экземпляром (карта конфликтов): канарейка не применима")
    text = read(p)
    if "| 31 " not in text and "|31|" not in text:
        raise MutationNotApplied("нет строки 31 в карте")
    originals.append((p, text))
    # переименовываем номер строки в такой, которого в разделах быть не может
    write(p, re.sub(r"(?m)^\|\s*\**31\**\s*\|", "| 41 |", text, count=1))
    return "строка таблицы переномерована в 41, в разделах такого номера нет"

def b29_row_named_in_sections(tmp, originals):   # безвредно для §47
    """безвредно: новая строка таблицы тут же названа в разделах — проверка молчит."""
    p = _cmap_page(tmp)
    if not p:
        raise MutationNotApplied("цель не объявлена экземпляром (карта конфликтов): канарейка не применима")
    text = read(p)
    originals.append((p, text))
    rows = [m for m in re.finditer(r"(?m)^\|\s*\**(\d+)\**\s*\|.*$", text)]
    if not rows:
        raise MutationNotApplied("в карте нет строк таблицы")
    nxt = max(int(m.group(1)) for m in rows) + 1
    last = rows[-1]
    row = ("| %d | Проверочный спор канарейки для сверки разделов | "
           "Позиция A этой проверочной строки утверждает, что сверка разделов с таблицей обязана идти тем же шагом, "
           "которым заводится сама строка, иначе разбор отстаёт от таблицы молча и никто этого не замечает. | "
           "Позиция B этой проверочной строки утверждает, что сверку достаточно делать глазами при следующем проходе, "
           "потому что машина всё равно не отличит обновлённый раздел от устаревшего и будет шуметь на ровном месте. | "
           "Статус этой строки описан словами, потому что канарейка проверяет форму таблицы и разделов, а не существо "
           "спора: строка нужна ровно для того, чтобы сцепка разделов увидела в разборе новый номер. | "
           "Позиция A выигрывает там, где строки карты заводятся часто и волной, и уследить за разбором вручную "
           "становится нечем: тогда сцепку держит проверка линтера, а не память автора и не его внимательность. | "
           "Позиция B выигрывает на редких одиночных правках, где человек и так читает раздел целиком, и лишняя "
           "проверка в отчёте добавляет только шум, который приходится разбирать вручную. | "
           "Подтверждено самой канарейкой: строка добавлена в копию карты и названа номером в разделе правил. |"
           % nxt)
    text = text[:last.end()] + "\n" + row + text[last.end():]
    anchor = "Спорные метрики, на которых стоят эти позиции"
    if anchor in text:
        text = text.replace(anchor, "Проверочное правило канарейки (спор №%d).\n\n%s" % (nxt, anchor), 1)
    write(p, text)
    return "добавлена строка №%d и названа в разделах" % nxt

def c112_decision_row_removed(tmp, originals):   # §43
    """Решение владельца по кандидату убрано из очереди — кандидат снова висит.

    Ловит класс дефекта, найденный дважды: решение в очереди есть, но ключ кандидата записан иначе
    («/workflows» против «workflows», «hmm-вектор» против «hmm вектор»), и §43 продолжал считать
    кандидата нерешённым. Кандидат берётся отказной и без страницы: у такого решения нет другого
    способа закрыть вопрос.
    """
    q = page(tmp, "_staging/candidate-decisions.tsv")
    if not os.path.exists(q):
        raise MutationNotApplied("нет очереди решений")
    text = read(q)
    lines = text.rstrip("\n").split("\n")
    keep = [lines[0]] + [l for l in lines[1:] if l.split("\t")[0] != "biome"]
    if len(keep) == len(lines):
        raise MutationNotApplied("нет решения по кандидату biome")
    originals.append((q, text))
    write(q, "\n".join(keep) + "\n")
    return "из очереди убрано решение по кандидату biome"

def c113_link_block_removed(tmp, originals):   # §51
    """У записи со ссылкой без контекста убран блок «Что за ссылкой» — цель ссылки снова неизвестна.

    Ловит класс, найденный владельцем 2026-09-16: двенадцать сентябрьских записей пришли с телом из одной
    ссылки, ссылки были обработаны машинно и лежали в `_staging`, а в записях так и осталось «пояснений
    источника нет». Проверка §51 требует, чтобы резюме цели жило в самой записи.
    """
    import glob as _glob
    rows = [q for q in _glob.glob(os.path.join(tmp, "raw", "telegram", "*.md")) if "## Что за ссылкой" in read(q)]
    if not rows:
        raise MutationNotApplied("нет записи с блоком «Что за ссылкой»")
    q = sorted(rows)[0]
    text = read(q)
    new = re.sub(r"(?ms)^##\s+Что за ссылкой\s*$.*?(?=^##\s|\Z)", "", text)
    if new == text:
        raise MutationNotApplied("блок не вычленяется")
    originals.append((q, text))
    write(q, new)
    return "из записи убран блок «Что за ссылкой» (%s)" % os.path.basename(q)[-28:]

def c114_service_row_in_materials(tmp, originals):   # §52
    """В реестр материалов возвращена строка сервиса.

    Владелец 2026-09-16: «в реестр материалов для чтения попали сервисы, сервисы должны храниться в реестре
    инструментов». Так стояли DeepWiki (ещё и дублем к реестру инструментов) и Zread. Проверка §52 требует,
    чтобы строка сервиса жила в реестре инструментов.
    """
    p = page(tmp, "wiki/comparisons/materials-registry.md")
    if not os.path.exists(p):
        raise MutationNotApplied("нет реестра материалов")
    text = read(p)
    originals.append((p, text))
    row = ("| [DeepWiki](https://deepwiki.com/) | сервис: сгенерированная вики и вопросы по репозиторию "
           "| открыть чужую репу и задавать вопросы по фреймворку "
           "| [[2026-09-01-k-podborke-blog-langchain-deepwiki-i-dokumentaciya-claude-co-113403]] |\n")
    anchor = "\n## Связи"
    write(p, text.replace(anchor, "\n" + row + anchor, 1) if anchor in text else text.rstrip("\n") + "\n" + row)
    return "в реестр материалов добавлена строка сервиса DeepWiki"

def c115_field_outside_set(tmp, originals):   # §54
    """В шапку записи дописано поле, которого нет в наборе её типа.

    Владелец 2026-09-17: служебные мета-поля создавались стихийно. Так в записях Telegram появилось поле
    `updated` (поле страниц вики), а у статей параллельно `source_author` и `authors`. Наборы полей — в
    SCHEMA, «Поля шапки источника». Сторож §54 требует, чтобы чужих полей в шапке не было.
    """
    import glob as _glob
    rows = sorted(_glob.glob(os.path.join(tmp, "raw", "telegram", "*.md")))
    if not rows:
        raise MutationNotApplied("нет записей Telegram")
    q = rows[0]
    text = read(q)
    m = re.match(r"(?s)^(---\r?\n)(.*?)(\r?\n---)", text)
    if not m:
        raise MutationNotApplied("нет шапки")
    originals.append((q, text))
    write(q, m.group(1) + m.group(2) + "\nupdated: 2026-09-17" + m.group(3) + text[m.end():])
    return "в шапку записи Telegram дописано поле updated"

def c116_moved_row_lost(tmp, originals):   # §55
    """Строка, убранная из реестра «переездом», пропала из реестра-адресата.

    Владелец 2026-09-17 спросил, почему из реестра инструментов исчезли Printing Press и подборка
    Awesome-Graph-Engineering. Причина: 16.09.2026 строка `DEEP-JLU/Awesome-Graph-Engineering` была убрана
    с причиной «подборка материалов — её место [[materials-registry]]», а в материалы не попала — запись
    исчезла молча, причина осталась намерением. Проверка §55 требует, чтобы строка, убранная переездом,
    находилась в реестре-адресате.
    """
    p = page(tmp, "wiki/comparisons/materials-registry.md")
    if not os.path.exists(p):
        raise MutationNotApplied("нет реестра материалов")
    text = read(p)
    lines = [l for l in text.split("\n") if "Awesome-Graph-Engineering" in l]
    if not lines:
        raise MutationNotApplied("нет строки подборки в материалах")
    originals.append((p, text))
    write(p, "\n".join(l for l in text.split("\n") if "Awesome-Graph-Engineering" not in l))
    return "из реестра материалов убрана строка подборки Awesome-Graph-Engineering"

def c117_record_without_approval(tmp, originals):   # §57
    """Запись корпуса из плана удаления исчезла, а журнала удаления с утверждением нет.

    Владелец 2026-09-17: «перед удалением тонких записей должен формироваться отчёт HTML чтобы человек утвердил
    удаление», и отдельно: «запись живёт, пока её выжимки не легли в страницы». Проверка §57 требует журнал
    удаления для исчезнувшей записи и отсутствие невклеенных выжимок.
    """
    plan = os.path.join(tmp, "_staging", "delete-thin-notes.tsv")
    if not os.path.exists(plan):
        raise MutationNotApplied("нет плана удаления")
    stem = [l.split("\t")[0] for l in read(plan).splitlines()[1:] if l.split("\t")[0]][0]
    removed = 0
    for rel in (os.path.join("raw", "telegram", stem + ".md"),
                os.path.join("wiki", "sources", "raw", "telegram", stem + ".md")):
        p = page(tmp, rel)
        if os.path.exists(p):
            originals.append((p, read(p)))
            os.remove(p)
            removed += 1
    if not removed:
        raise MutationNotApplied("записи плана уже нет в дереве")
    return "запись %s удалена без журнала удаления" % stem[-24:]

def c118_quote_from_undeclared(tmp, originals):   # §58
    """Со страницы убран источник, из которого она цитирует: цитата осталась без носителя.

    Первая волна по новым правилам (2026-09-17) дала ровно этот дефект: страница orchestrator цитировала
    запись 113489, а в её `sources:` этой записи не было. Проверка §58 требует, чтобы цитата в кавычках
    находилась в источнике, объявленном в шапке.
    """
    import glob as _glob
    import re as _re2
    pages = sorted(_glob.glob(os.path.join(tmp, "wiki", "concepts", "*.md")))
    for p in pages:
        text = read(p)
        m = re.match(r"(?s)^(---\r?\n)(.*?)(\r?\n---)", text)
        if not m:
            continue
        srcs = re.findall(r"raw/[\w/\.\-\u0400-\u04FF]+\.md", m.group(2))
        if not srcs:
            continue
        bodies = {}
        for s in srcs:
            probe = os.path.join(tmp, s.replace("/", os.sep))
            if os.path.exists(probe):
                bodies[s] = read(probe)
        if not bodies:
            continue
        # Предусловие честной мутации: на странице есть длинная цитата, которую сторож реально
        # сверяет (короткие §58 не смотрит). Нет такой — мутировать нечего, а не «сторож слеп».
        target = None
        for s, body in bodies.items():
            for q in _re2.findall(r"«([^»]{40,300})»", text[m.end():]):
                nq = _re2.sub(r"\s+", " ", _re2.sub(r"[*`>\[\]]|\(http[^)]*\)", "", q)).strip()
                if nq.count(" ") >= 4 and nq[:55] in _re2.sub(r"\s+", " ", body):
                    target = s
                    break
            if target:
                break
        if not target:
            continue
        originals.append((p, text))
        new_fm = m.group(2)
        new_fm = new_fm.replace(target + ", ", "").replace(", " + target, "").replace(target, "")
        write(p, m.group(1) + new_fm + m.group(3) + text[m.end():])
        return "у страницы %s убран источник %s" % (os.path.basename(p), os.path.basename(target)[-22:])
    raise MutationNotApplied("нет страницы с проверяемой цитатой из объявленного источника")

def c119_rule_marker_gone(tmp, originals):   # §59
    """Из файла убран маркер правила, о котором реестр утверждает, что оно там живёт.

    Владелец 2026-09-17: «тебе кажется что механизм уже вписан в артефакты и репозиторий, потому что он
    в твоем контексте, а не потому что он есть в файлах». Реестр `_toolkit/rules.tsv` держит утверждения о правилах, проверка §59
    сверяет их с файлами. Канарейка меняет имя локального порога, который загрузчик обязан объявить.
    """
    p = page(tmp, "_toolkit/page_doubt.py")
    if not os.path.exists(p):
        raise MutationNotApplied("нет page_doubt.py")
    text = read(p)
    marker = 'THRESHOLD_LABEL = "Порог сомнения кандидата"'
    if marker not in text:
        raise MutationNotApplied("нет маркера порога")
    originals.append((p, text))
    write(p, text.replace(marker, 'THRESHOLD_LABEL = "Порог страницы"', 1))
    return "маркер локального порога в page_doubt.py изменён — реестр правил обязан это заметить"


def c120_artifact_unreachable(tmp, originals):   # §59 (достижимость)
    """Из протокола волны убрано имя артефакта: правило живёт, а сессия о нём не узнает.

    Владелец 2026-09-17 спросил про `_toolkit/wave-order.md`: «упоминается в протоколе?» — не упоминался.
    Артефакт, не названный ни в протоколе волны, ни в SCHEMA, ни в правилах страниц, для следующей сессии не
    существует. Проверка реестра это ловит: файл правила обязан быть назван хотя бы в одном из документов.
    """
    p = page(tmp, "_toolkit/ingest-wave.md")
    if not os.path.exists(p):
        raise MutationNotApplied("нет протокола волны")
    text = read(p)
    if "wave-order.md" not in text:
        raise MutationNotApplied("протокол и так не называет шаблон наряда")
    originals.append((p, text))
    write(p, text.replace("wave-order.md", "шаблон-наряда.md"))
    return "из протокола убрано имя артефакта wave-order.md"

def c121_staged_copy_kept(tmp, originals):   # §61
    """На площадке приёмки оставлена копия страницы, которая уже опубликована.

    Владелец 2026-09-17 распорядился чистить площадку: двадцать две копии опубликованных страниц лежали в
    `_staging/new-pages/`. Проверка §61 объявляет такую копию находкой.
    """
    import glob as _glob
    import shutil as _sh
    pages = sorted(_glob.glob(os.path.join(tmp, "wiki", "concepts", "*.md")))
    if not pages:
        raise MutationNotApplied("нет страниц в concepts/")
    d = os.path.join(tmp, "_staging", "new-pages")
    os.makedirs(d, exist_ok=True)
    src = pages[0]
    originals.append((os.path.join(d, os.path.basename(src)), None))
    _sh.copyfile(src, os.path.join(d, os.path.basename(src)))
    return "на площадку приёмки положена копия опубликованной страницы"

def c122_cyrillic_page_name(tmp, originals):   # §62
    """Страница вики названа кириллицей.

    Владелец 2026-09-17: «в отчет попадают названия страницы на кириллице, хотя фактически у нас все названия
    статей на латинице». Слаг считает `slugify.py`, но проверить имя созданной страницы было нечем.
    """
    import glob as _glob
    import shutil as _sh
    pages = sorted(_glob.glob(os.path.join(tmp, "wiki", "concepts", "*.md")))
    if not pages:
        raise MutationNotApplied("нет страниц в concepts/")
    dst = os.path.join(os.path.dirname(pages[0]), "критерий-выбора.md")
    originals.append((dst, None))
    _sh.copyfile(pages[0], dst)
    return "страница названа кириллицей: критерий-выбора.md"


def c123_prose_pain_not_covered(tmp, originals):   # §33
    """Карточка описывает боль прозой, а источник не назван на странице болей.

    Замер 2026-09-17: охват по заголовкам не видел боли, описанной текстом; расширение сторожа дало 11 находок.
    """
    import json as _json
    reg = os.path.join(tmp, "_staging", "cards-registry.json")
    page_p = os.path.join(tmp, "wiki", "comparisons", "pain-points-and-fixes.md")
    if not (os.path.exists(reg) and os.path.exists(page_p)):
        raise MutationNotApplied("нет реестра карточек или страницы болей")
    cards = _json.load(open(reg, encoding="utf-8"))["cards"]
    page = read(page_p)
    for c in cards:
        cf = os.path.join(tmp, "_staging", "cards", c["file"])
        if not os.path.exists(cf):
            continue
        text = read(cf)
        if "pains: none" in text or "боль" in text:
            continue
        # Карточка, чей источник уже назван на странице болей, сторожем не считается вовсе: §33 пропускает её
        # до подсчёта прозы, и подсаженная боль тогда не меняет ничего (аттестации final11 и final12 дали
        # 87/88 именно по этой причине). Берём карточку, которая охватом действительно не покрыта.
        src = c.get("source", "?")
        if src == "?" or src in page or src[:-3] in page:
            continue
        originals.append((cf, text))
        write(cf, text.rstrip("\n") + "\n\nБоль: агенты ломают покрытие, деградация и перегруз растут, "
                                       "провал на проде.\n")
        return "в карточку %s дописана боль прозой" % c["file"]
    raise MutationNotApplied("нет подходящей карточки")


def c124_dispute_without_page(tmp, originals):   # §46
    """Отчёт смыслового прохода несёт расхождение без страницы, а понятия нет ни в очереди, ни в решениях.

    Тупик 2026-09-17: расхождение про понятие, у которого ещё нет страницы, оседало в отчёте прогона —
    ни строки в карте, ни адреса. Проверка требует очередь кандидатов или решение.
    """
    import glob as _glob
    import json as _json
    reps = sorted(_glob.glob(os.path.join(tmp, "_staging", "stance", "wave-*-semantic.json")))
    if not reps:
        raise MutationNotApplied("нет отчётов смыслового прохода")
    rp = reps[0]
    data = _json.load(open(rp, encoding="utf-8"))
    items = data.get("items") or []
    if not items:
        raise MutationNotApplied("в отчёте нет items")
    originals.append((rp, read(rp)))
    items[0]["page_missing"] = "страница говорит «нужен Vision», не называя дерево доступности"
    items[0]["concept"] = "понятие-которого-нет-в-очереди"
    with open(rp, "w", encoding="utf-8") as f:
        _json.dump(data, f, ensure_ascii=False, indent=1)
    return "в отчёт прохода добавлено расхождение без страницы, понятия нет в очереди"

def c125_heading_link(tmp, originals):   # §63
    """Заголовок страницы получил вики-ссылку.

    Найдено 2026-09-17: девять заголовков пяти страниц несли ссылки-алиасы, из-за чего обратная ссылка в копии
    источника резала имя раздела по «|» и §42 объявлял, что такого раздела нет.
    """
    import glob as _glob
    pages = sorted(_glob.glob(os.path.join(tmp, "wiki", "concepts", "*.md")))
    if not pages:
        raise MutationNotApplied("нет страниц в concepts/")
    p = pages[0]
    text = read(p)
    originals.append((p, text))
    write(p, text.rstrip("\n") + "\n\n## Спорное ([[karpathy|Karpathy]])\n\n- Раздел добавлен канарейкой.\n")
    return "в заголовок добавлена вики-ссылка"

def c126_source_without_card(tmp, originals):   # §64
    """У источника корпуса отобрана карточка.

    2026-09-17: запись голосовых заметок прошла инжест без карточки, и это видел только реестр, который
    печатает покрытие, но ничего не запрещает. Находка нашлась руками.
    """
    import glob as _glob
    import os as _os
    cards = sorted(_glob.glob(_os.path.join(tmp, "_staging", "cards", "*.md")))
    reg = _os.path.join(tmp, "_staging", "cards-registry.json")
    if not cards or not _os.path.exists(reg):
        raise MutationNotApplied("нет карточек или реестра карточек")
    c = cards[0]
    originals.append((c, read(c)))
    _os.remove(c)
    return "карточка %s удалена, источник остался без разбора" % _os.path.basename(c)


def c212_promote_missing_message_id(tmp, originals):   # promote_approved.py, отказ
    """Строка ревью ссылается на отсутствующее сообщение: прогону нельзя доверять.

    Законный исход «владелец всё отклонил» — это ноль; ненулевой выход означает только
    недоверяемый прогон. Канарейка подменяет id одобренной строки на несуществующий.
    """
    import csv as _csv
    import io as _io
    import os as _os
    p = page(tmp, "_toolkit/tools/tg-saved/tests/staging/review.csv")
    if not _os.path.exists(p):
        raise MutationNotApplied("нет review.csv фикстуры")
    rows = list(_csv.DictReader(_io.open(p, encoding="utf-8-sig", newline="")))
    if not rows:
        raise MutationNotApplied("review.csv пуст")
    originals.append((p, read(p)))
    rows[0]["approve"] = "yes"
    rows[0]["id"] = "999999"
    fields = ["num", "approve", "date", "source", "verdict", "confidence", "tags", "chars", "id", "preview"]
    with _io.open(p, "w", encoding="utf-8", newline="") as f:
        w = _csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    return "одобренная строка ссылается на сообщение 999999, которого нет"


def c209_begin_refuses_undeclared(tmp, originals):   # wave begin
    """Голый begin без объявления входа: отказывать обязан до всякой работы.

    Мутации не нужно: необъявленный вход виден из самих аргументов, и отказ не зависит
    от состояния дерева. Проверяется текст отказа, а не побочка (её быть не должно).
    """
    return "голый begin без --selection и --collected"


def c210_begin_collected_requires_by(tmp, originals):   # wave begin
    """ collected без --by: чьё решение неизвестно — отказывать.

    Успешный путь (открывает волну, пишет хеши) живым прогоном; здесь — ворота на забытое имя.
    """
    return "collected без имени владельца"


def c211_close_fails_on_tampered_entry(tmp, originals):   # wave close
    """Подмена источника entry после begin роняет закрытие с именем файла.

    Готовит настоящий сценарий в копии: закрывает wave-1, открывает волну через begin,
    подменяет запись. Сам отказ проверяет харнесс командой close.
    """
    import subprocess as _sp
    import sys as _sys
    runner = os.path.join(tmp, "_toolkit", "wave_runner.py")
    waves = os.path.join(tmp, "_staging", "waves.json")
    if not os.path.exists(waves):
        raise MutationNotApplied("нет реестра волн")
    originals.append((waves, read(waves)))
    r = _sp.run([_sys.executable, "-X", "utf8", runner, "--wiki", tmp, "close", "--confirm"],
                capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    if r.returncode != 0:
        tail = ((r.stdout or "") + (r.stderr or "")).strip().splitlines()
        raise MutationNotApplied("волна wave-1 не закрылась: %s" % (tail[-1][:100] if tail else "?"))
    r = _sp.run([_sys.executable, "-X", "utf8", runner, "--wiki", tmp, "begin",
                 "--collected", "--corpus", "fixture", "--inbox", "raw/fixture", "--by", "canary"],
                capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
                cwd=tmp)
    if r.returncode != 0:
        tail = ((r.stdout or "") + (r.stderr or "")).strip().splitlines()
        raise MutationNotApplied("begin не открылся: %s" % (tail[-1][:100] if tail else "?"))
    raws = sorted(glob.glob(os.path.join(tmp, "raw", "fixture", "*.md")))
    if not raws:
        raise MutationNotApplied("нет записей корпуса для подмены")
    p = raws[0].replace("\\", "/")
    originals.append((p, read(p)))
    with open(p, "a", encoding="utf-8") as f:
        f.write("\nПодмена канарейки: текст после begin.\n")
    return "источник entry подменён после begin: %s" % os.path.basename(p)


def c213_close_fails_on_wave_growth(tmp, originals):   # wave close
    """Файл, подложенный во вход после begin, роняет закрытие с именем файла.

    Готовит настоящий сценарий в копии: закрывает wave-1 правкой реестра (симуляция прошлого
    закрытия — полный close здесь не при чём), открывает волну через begin, подкладывает лишний
    файл во вход. Сам отказ проверяет харнесс командой close.
    """
    import subprocess as _sp
    import sys as _sys
    runner = os.path.join(tmp, "_toolkit", "wave_runner.py")
    waves = os.path.join(tmp, "_staging", "waves.json")
    if not os.path.exists(waves):
        raise MutationNotApplied("нет реестра волн")
    originals.append((waves, read(waves)))
    import json as _json
    data = _json.loads(read(waves))
    for w in data.get("waves", []):
        if w.get("current"):
            w["to"] = "2026-09-26T00:00"
            w.pop("current", None)
    with open(waves, "w", encoding="utf-8", newline="") as f:
        _json.dump(data, f, ensure_ascii=False, indent=1)
        f.write("\n")
    r = _sp.run([_sys.executable, "-X", "utf8", runner, "--wiki", tmp, "begin",
                 "--collected", "--corpus", "fixture", "--inbox", "raw/fixture", "--by", "canary"],
                capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
                cwd=tmp)
    if r.returncode != 0:
        tail = ((r.stdout or "") + (r.stderr or "")).strip().splitlines()
        raise MutationNotApplied("begin не открылся: %s" % (tail[-1][:100] if tail else "?"))
    p = os.path.join(tmp, "raw", "fixture", "zz-canary-growth.md")
    originals.append((p, None))
    write(p, "---\ntitle: \"Рост волны\"\ntype: article\nstatus: active\ntags: []\n"
             "summary: \"подложенный файл\"\n---\n\nТекст роста.\n")
    return "во вход подложен файл вне квитанции"


def c214_promote_all_stops_over_threshold(tmp, originals):   # promote_approved.py --all, порог
    """Выгрузка сверх порога в режиме --all: остановка до записи, второе слово — --force.

    Мутации кода не нужно: порог — ворота, а не дефект, и отказ виден из самих чисел. Готовит
    в копии фикстуры 1001 сообщение к разбору; харнесс командой --all проверяет остановку
    с текстом про порог (код 3, ничего не записано).
    """
    import json as _json
    p = page(tmp, "_toolkit/tools/tg-saved/tests/staging/messages.json")
    if not os.path.exists(p):
        raise MutationNotApplied("нет messages.json фикстуры")
    originals.append((p, read(p)))
    msgs = {"messages": [
        {"id": 900000 + i, "day": "2026-09-20", "source": "chan",
         "text": "Пост %d про порог" % i, "tags": [], "media_local": [], "media": [], "links": []}
        for i in range(1001)]}
    with open(p, "w", encoding="utf-8") as f:
        _json.dump(msgs, f, ensure_ascii=False)
    return "в фикстуре 1001 сообщение к разбору"


def c215_probe_verdict_reads_norm(tmp, originals):   # вердикт пробы читает норму
    """Сужение нормы обязано ронять вердикт: иначе вердикт норму не читает, а рисует зелень.

    Одна строка в копии: allowed без §89. Харнесс голой пробой проверяет провал с перечислением
    лишнего. Готовит только код — экземпляр распаковывает сама команда пробы.
    """
    p = page(tmp, "_toolkit/probe_instance.py")
    replace_once(p, read(p), 'allowed = {"89"}', 'allowed = set()', originals)
    return "норма голой пробы сужена до пустого множества"


def c216_probe_verdict_exemption_narrow(tmp, originals):   # освобождение упавшего lint точечное
    """Поломка освобождения обязана ронять вердикт: честный код 1 при сошедшейся норме — не сбой.

    Первая версия освобождения была широкой на любой шаг (настоящий дефект, пойман до прогонов);
    эта подмена ломает его в другую сторону — и голая проба снова красна по закону.
    """
    p = page(tmp, "_toolkit/probe_instance.py")
    replace_once(p, read(p), 'failed_step == "lint" and lint_exit == 1',
                 'failed_step == "lint" and lint_exit == 2', originals)
    return "освобождение требует код 2 вместо честного кода 1"


def c217_telegram_menu_without_flag(tmp, originals):   # меню приёма без флага
    """Меню без режима: показать выбор и выйти с кодом 2, ничего не трогая.

    Мутации не нужно: молчаливый режим по умолчанию — чужое решение о материале, и отказ виден
    из самих аргументов. Проверяется текст выбора, а не побочка (её быть не должно).
    """
    return "задача telegram без --all и --review"


def c218_lint_exit_code_honest(tmp, originals):   # честный код возврата линтера
    """Красный линтер выходит с кодом 1 и печатает итог: вечный ноль скрывал красноту.

    Опустошает одну страницу вики (гарантированная находка «пустой файл»); харнесс проверяет
    ненулевой код и строку итога. Без честного кода отчёты «ИТОГО 0» расходились бы с машиной.
    """
    import glob as _glob
    import os as _os
    pages = sorted(_glob.glob(_os.path.join(tmp, "wiki", "concepts", "*.md")))
    if not pages:
        raise MutationNotApplied("нет страниц слоя знаний для опустошения")
    p = pages[0]
    originals.append((p, read(p)))
    write(p, "")
    return "опустошена страница %s" % _os.path.basename(p)


def c219_forbidden_key_read(tmp, originals):   # второй ключ запрета читается проверкой
    """Новое слово в instance_forbidden обязано ронять §91: иначе ключ объявлен, а не читается.

    Одна строка в копии декларации; харнесс полным линтом проверяет срабатывание раздела. Слова запрета
    функция не называет, а читает из самой декларации: вшивать словарь экземпляра в исходник канарейки —
    значит пачкать поставку тем, от чего она охраняет.
    """
    p = page(tmp, "_staging/domain.local.tsv")
    text = read(p)
    line = next((l for l in text.splitlines() if l.startswith("instance_forbidden\t")), "")
    if not line:
        raise MutationNotApplied("в декларации копии нет ключа instance_forbidden")
    replace_once(p, text, line, line + ",handover", originals)
    return "в запрет добавлено слово handover из файлов механизма"


def c220_undeclared_outside_read(tmp, originals):   # необъявленное чтение вне корня
    """В проверяющий код добавлено чтение домашнего каталога без объявления: §95 обязан сказать.

    Одна строка в копии; харнесс полным линтом проверяет срабатывание раздела. Объявленные чтения
    (таблица external-reads.tsv) проходят молча — это видно каждый зелёный прогон без мутаций.
    """
    p = page(tmp, "_toolkit/dashboard.py")
    text = read(p)
    originals.append((p, text))
    # Литерал собирается по частям: исходник канарейки сканируется тем же датчиком, и цельный литерал
    # ронял бы живое дерево. В копию падает цельная валидная строка — датчик обязан сказать. Обход датчика
    # конкатенацией в боевом коде датчик не видит по построению: он ловит небрежность, а не умысел
    # (как и остальные подстроковые проверки); умысел здесь — сама канарейка.
    write(p, text.rstrip("\n") + '\n_probe_canary = os.path.expanduser(' + '"~/canary-probe")\n')
    return "в проверяющий код добавлено необъявленное чтение домашнего каталога"


def c222_undeclared_home_pathlib(tmp, originals):   # домовый путь через pathlib
    """Необъявленное чтение через Path.home: §95 обязан сказать тем же текстом.

    Литерал собирается по частям, как в соседней канарейке: исходник канарейки сканируется тем же
    датчиком. В копию падает цельная валидная строка.
    """
    p = page(tmp, "_toolkit/dashboard.py")
    text = read(p)
    originals.append((p, text))
    write(p, text.rstrip("\n") + '\n_probe_canary = Path.hom' + 'e() / "canary-probe"\n')
    return "в проверяющий код добавлено необъявленное чтение через Path.home"


def c223_undeclared_home_envvars(tmp, originals):   # домовый путь через переменные окружения
    """Необъявленное чтение через expandvars: §95 обязан сказать тем же текстом."""
    p = page(tmp, "_toolkit/dashboard.py")
    text = read(p)
    originals.append((p, text))
    write(p, text.rstrip("\n") + '\n_probe_canary = os.path.expandvar' + 's("%APPDATA%/canary-probe")\n')
    return "в проверяющий код добавлено необъявленное чтение через expandvars"


def c224_undeclared_home_envjoin(tmp, originals):   # домовый путь через склейку переменной
    """Необъявленное чтение через склейку переменной окружения: §95 обязан сказать тем же текстом."""
    p = page(tmp, "_toolkit/dashboard.py")
    text = read(p)
    originals.append((p, text))
    write(p, text.rstrip("\n") + '\n_probe_canary = os.path.join(os.environ.get("LOCALAPP' + 'DATA", ""), "canary-probe")\n')
    return "в проверяющий код добавлено необъявленное чтение через склейку переменной"


def c225_undeclared_home_comment_toggle(tmp, originals):   # комментарий как выключатель
    """Чтение с комментарием-выключателем: §95 обязан сказать — комментарий не выключает образец.

    Та же подмена, что ловит датчик у себя: строка собирается по частям, в копию падает цельная.
    """
    p = page(tmp, "_toolkit/dashboard.py")
    text = read(p)
    originals.append((p, text))
    write(p, text.rstrip("\n") + '\n_probe_canary = Path.hom' + 'e() / "canary-probe"  # tempfile\n')
    return "в проверяющий код добавлено чтение с комментарием-выключателем"


def c226_undeclared_home_code_toggle(tmp, originals):   # код времянки как выключатель
    """Чтение рядом с времянкой в той же строке: §95 обязан сказать — царапка не покрывает чтение."""
    p = page(tmp, "_toolkit/dashboard.py")
    text = read(p)
    originals.append((p, text))
    write(p, text.rstrip("\n") + '\n_probe_canary = Path.hom' + 'e() / tempfile.gettempdir()\n')
    return "в проверяющий код добавлено чтение рядом с времянкой"


def c221_supply_file_unnamed(tmp, originals):   # файл поставки не назван в карте
    """В поставку добавлен файл без имени в карте: проверка полноты обязана сказать.

    Одна строка в копии дерева (файл есть — имени нет); харнесс проверкой карты требует именования.
    """
    p = page(tmp, "_toolkit/zz-canary-unnamed.py")
    originals.append((p, None))
    write(p, "# проба\n")
    return "в поставку добавлен файл без имени в карте"


def c208_nested_source_without_card(tmp, originals):   # §64
    """Источник во вложенной папке корпуса без карточки: универсум §64 — всё дерево raw/.

    До 2026-09-25 проверка считала четыре каталога верхнего уровня, и записи вроде raw/fixture/
    не видела вовсе. Канарейка кладёт пробную запись во вложенную папку мимо реестра карточек.
    """
    import glob as _glob
    import os as _os
    reg = _os.path.join(tmp, "_staging", "cards-registry.json")
    raws = sorted(_glob.glob(_os.path.join(tmp, "raw", "*", "")))
    raws = [d for d in raws if _os.path.isdir(d)]
    if not raws or not _os.path.exists(reg):
        raise MutationNotApplied("нет вложенного корпуса или реестра карточек")
    p = _os.path.join(raws[0], "zz-canary-nocard.md")
    if _os.path.exists(p):
        raise MutationNotApplied("пробная запись уже есть — мутировать нечего")
    originals.append((p, None))
    write(p, "---\ntitle: \"Проба вложенной записи\"\ntype: article\nstatus: active\n"
             "tags: []\nsummary: \"пробный файл канарейки\"\n---\n\nТекст пробной записи.\n")
    return "во вложенную папку корпуса положена запись без карточки"

def c127_registry_boilerplate(tmp, originals):   # §49
    """Строка реестра инструментов получила формальную справку вместо сути.

    Владелец 2026-09-17 нашёл в реестре запись «GitHub-репозиторий с плагинами, куда залит Smoke Break» —
    описание, которое не говорит, зачем инструмент в реестре. Проверка ловит именно этот класс.
    """
    import os as _os
    p = _os.path.join(tmp, "wiki", "comparisons", "tools-registry.md")
    if not _os.path.exists(p):
        raise MutationNotApplied("нет реестра инструментов")
    text = read(p)
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if not line.startswith("|") or "---" in line or "Что даёт" in line:
            continue
        cells = line.split("|")
        if len(cells) >= 5 and cells[1].strip() and len(cells[3].strip()) >= 45:
            originals.append((p, text))
            cells[3] = " GitHub-репозиторий с плагинами для агента, содержание записи не пояснено"
            lines[i] = "|".join(cells)
            write(p, "\n".join(lines))
            return "в строку реестра «%s» вписана формальная справка" % cells[1].strip()[:30]
    raise MutationNotApplied("нет подходящей строки реестра")

def c128_registry_source_without_row(tmp, originals):   # §65
    """Из реестра удалена строка, а её источник остался объявленным.

    Ровно это владелец нашёл 2026-09-17: заметка про NaCl объявлена источником реестра инструментов, а строки
    в таблице нет — «в статье указано, что она входит в реестр tools, но это не так».
    """
    import os as _os
    import re as _re
    p = _os.path.join(tmp, "wiki", "comparisons", "tools-registry.md")
    if not _os.path.exists(p):
        raise MutationNotApplied("нет реестра инструментов")
    text = read(p)
    srcs = _fmparse.items(text, "sources")
    if not srcs:
        raise MutationNotApplied("в шапке нет sources:")
    for src in srcs:
        stem = _os.path.basename(src)[:-3]
        note = _os.path.join(tmp, src)
        if not _os.path.exists(note):
            continue
        # берём именно ссылочную заметку: проверка смотрит на них, обычная заметка строки не требует
        body = read(note)
        body = _re.sub(r"(?s)^---.*?---", "", body)
        body = _re.sub(r"(?ms)^##\s+Что за ссылкой.*?(?=^##\s|\Z)", "", body)
        links = _re.findall(r"https?://\S+", body)
        words = len(_re.findall(r"[А-Яа-яA-Za-z]{3,}", _re.sub(r"https?://\S+", "", body)))
        if not links or words >= 40:
            continue
        rows = [l for l in text.split("\n") if l.startswith("|") and stem in l and "Что даёт" not in l]
        if rows:                                  # строк может быть несколько: убираем все, иначе заметка остаётся названной
            originals.append((p, text))
            write(p, "\n".join(l for l in text.split("\n") if l not in rows))
            return "строки таблицы со ссылочной заметкой (%d) удалены, объявление осталось" % len(rows)
    raise MutationNotApplied("нет строки, ссылающейся на объявленный источник")

def c129_card_without_frontmatter(tmp, originals):   # §66
    """Карточка извлечения осталась без шапки.

    Карточка без шапки — не свидетельство разбора: линия не заполнена, реестр её не видит. Владелец
    2026-09-17: «единый скрипт, который прогоняет волну целиком от сырья до страниц» — форма карточки стала
    воротами раннера, и её падение обязано быть видно.
    """
    import glob as _glob
    import os as _os
    import re as _re
    cards = sorted(_glob.glob(_os.path.join(tmp, "_staging", "cards", "*.md")))
    for c in cards:
        text = read(c)
        if _re.match(r"(?s)^---\n.*?\n---", text):
            originals.append((c, text))
            write(c, _re.sub(r"(?s)^---\n.*?\n---\n*", "", text, count=1))
            return "у карточки %s снята шапка" % _os.path.basename(c)[:40]
    raise MutationNotApplied("нет карточки с шапкой")

def c130_script_without_address(tmp, originals):   # §67
    """Скрипт площадки появился без адреса в документах волны.

    Замер 2026-09-17: девятнадцать рабочих скриптов не были названы нигде, включая сторожа строк реестров и
    проход lint. Скрипт, которого нет в протоколе, для сессии не существует.
    """
    import os as _os
    p = _os.path.join(tmp, "_toolkit", "script-без-адреса.py")
    originals.append((p, None))
    with open(p, "w", encoding="utf-8") as f:
        f.write("# скрипт, которого нет в документах\n")
    return "добавлен скрипт без адреса в протоколе"

def c131_unclear_without_decision(tmp, originals):   # §68
    """Непонятная запись изображения осталась без решения.

    Прежняя версия канарейки сажала список с выдуманным id `img-999`, которого нет ни в одной партии описаний.
    Это работало, пока §68 смотрел только файл списка; после того как проверка стала считаться от партий
    (вечер 2026-09-17), выдуманный id перестал что-либо значить, и канарейка молча промахнулась. Теперь
    мутация берёт живую запись без контекста и снимает её решение: список на месте, решения нет.
    """
    import glob as _glob
    lists = sorted(_glob.glob(os.path.join(tmp, "_staging", "**", "media-unclear.tsv"), recursive=True))
    if not lists:
        raise MutationNotApplied("списков непонятных записей не нашлось")
    stem = None
    for line in read(lists[0]).strip().split("\n")[1:]:
        cells = line.split("\t")
        if cells and cells[0].strip():
            stem = cells[0].strip()
            break
    if not stem:
        raise MutationNotApplied("список непонятных записей пуст")
    decisions = os.path.join(os.path.dirname(lists[0]), "media-unclear-decisions.tsv")
    if not os.path.exists(decisions):
        raise MutationNotApplied("файла решений нет — нечего снимать")
    text = read(decisions)
    originals.append((decisions, text))
    keep = [text.split("\n")[0]] + [l for l in text.strip().split("\n")[1:] if not l.startswith(stem + "\t")]
    write(decisions, "\n".join(keep) + "\n")
    return "у записи %s снято решение (список на месте)" % stem

def c132_runner_stops_on_queue(tmp, originals):   # этап 0 раннера
    """Волна не закрывается при непустой очереди карточек.

    Раннер, который проходит мимо очереди, — та же добрая воля, только в виде скрипта. Канарейка сажает
    источник без карточки и требует, чтобы этап «Вход» перестал быть готовым.
    """
    import os as _os
    p = _os.path.join(tmp, "raw", "telegram", "zz-canary-source-999999.md")
    originals.append((p, None))
    with open(p, "w", encoding="utf-8") as f:
        f.write("# источник без карточки\n\nзаголовок и текст для проверки очереди.\n")
    return "источник без карточки в очереди"


def c133_card_without_about(tmp, originals):   # §66
    """Карточка без строки «О чём источник»: разбор есть, а сказать о чём — нечего.

    Замер 2026-09-17: корпус знает три формы записи, и проверка ловит отсутствие по смыслу. Канарейка снимает
    строку у живой карточки.
    """
    import io as _io
    import os as _os
    import re as _re
    cards = _os.path.join(tmp, "_staging", "cards")
    for name in sorted(_os.listdir(cards)):
        if not name.endswith(".md"):
            continue
        path = _os.path.join(cards, name)
        text = _io.open(path, encoding="utf-8", errors="replace").read()
        if _re.search(r"(?m)^(?:##\s+О чём|[-*]\s+\**О чём)", text):
            originals.append((path, text))
            text = _re.sub(r"(?m)^(?:##\s+О чём.*|[-*]\s+\**О чём.*)$", "", text, count=1)
            with _io.open(path, "w", encoding="utf-8", newline="") as f:
                f.write(text)
            return "карточка %s без строки «О чём источник»" % name
    raise MutationNotApplied("карточек с «О чём источник» не нашлось")


def c134_card_without_address(tmp, originals):   # §69
    """Карточка, которую не называет ни одна страница вики: разбор живёт в пустоте.

    Обратной проверки не было — §46 следит за нарядом прохода, а покрытие карточек страницами не смотрел
    никто. Канарейка заводит карточку и запись реестра для источника, которого в вики нет.
    """
    import io as _io
    import json as _json
    import os as _os
    reg_path = _os.path.join(tmp, "_staging", "cards-registry.json")
    reg = _json.loads(_io.open(reg_path, encoding="utf-8").read())
    card_name = "zz-canary-без-адреса.md"
    card_path = _os.path.join(tmp, "_staging", "cards", card_name)
    originals.append((card_path, None))
    with _io.open(card_path, "w", encoding="utf-8", newline="") as f:
        f.write("---\nlineage_tools: []\nlineage_debate: []\n---\n\n# Карточка: zz-canary-без-адреса\n\n"
                "- **О чём источник**: источник, которого нет ни на одной странице вики.\n")
    originals.append((reg_path, _io.open(reg_path, encoding="utf-8").read()))
    reg.setdefault("cards", []).append({"id": "zz-canary-без-адреса", "file": card_name,
                                        "kind": "note", "source": "zz-canary-без-адреса.md",
                                        "lineage_tools": [], "lineage_debate": []})
    with _io.open(reg_path, "w", encoding="utf-8", newline="") as f:
        f.write(_json.dumps(reg, ensure_ascii=False, indent=1))
    return "карточка без адреса в вики"


def c135_quote_head_from_other_source(tmp, originals):   # §58
    """У цитаты подменено начало на фразу из источника, которого страница не объявляла.

    Проверка §58 смотрит первые 55 знаков кавычек: если они есть в объявленных источниках — молчит. Значит
    канарейка должна подставить начало из ЧУЖОГО источника, иначе она проверяет не то, что кажется.
    """
    import glob as _glob
    pages = sorted(_glob.glob(os.path.join(tmp, "wiki", "concepts", "*.md")))
    for p in pages:
        text = read(p)
        m = re.match(r"(?s)^(---\r?\n)(.*?)(\r?\n---)", text)
        if not m:
            continue
        declared = re.findall(r"raw/[\w/\.\-\u0400-\u04FF]+\.md", m.group(2))
        if not declared:
            continue
        quotes = [q for q in re.findall(r"«([^»]{80,260})»", text) if q.count(" ") >= 6]
        if not quotes:
            continue
        for src in sorted(_glob.glob(os.path.join(tmp, "raw", "**", "*.md"), recursive=True)):
            rel = os.path.relpath(src, tmp).replace(os.sep, "/")
            if rel in declared:
                continue
            raw = read(src)
            phrases = [q for q in re.findall(r"«([^»]{70,200})»", raw) if q.count(" ") >= 6]
            if not phrases:
                continue
            q = quotes[0]
            new_q = phrases[0][:70] + q[70:]
            originals.append((p, text))
            write(p, text.replace("«" + q + "»", "«" + new_q + "»", 1))
            return "начало цитаты на странице %s подменено фразой из %s" % (os.path.basename(p), rel)
    raise MutationNotApplied("не нашлось пары «страница с цитатой — чужой источник»")


def c136_source_without_required_field(tmp, originals):   # §70
    """У записи корпуса убрано обязательное поле шапки.

    §54 ловит лишние поля, а отсутствие не ловил никто: запись без `summary` или `created` проходила молча.
    """
    import glob as _glob
    import re as _re
    for src in sorted(_glob.glob(os.path.join(tmp, "raw", "**", "*.md"), recursive=True)):
        text = read(src)
        m = _re.match(r"(?s)^(---\r?\n)(.*?)(\r?\n---)", text)
        if not m or not _re.search(r"(?m)^summary:", m.group(2)):
            continue
        originals.append((src, text))
        head = _re.sub(r"(?m)^summary:.*?\r?\n", "", m.group(2), count=1)
        write(src, m.group(1) + head + m.group(3) + text[m.end():])
        return "у записи %s убрано поле summary" % os.path.basename(src)
    raise MutationNotApplied("записи корпуса с полем summary не нашлось")


def c137_mirror_without_master(tmp, originals):   # §71
    """Копия источника в хранилище без мастера: файл живёт в вики, а записи корпуса не было.

    §8 смотрит только «есть ли копия у мастера», поэтому такой файл проходил молча.
    """
    import io as _io
    import os as _io2
    path = _io2.path.join(tmp, "wiki", "sources", "raw", "zz-canary-сирота.md")
    originals.append((path, None))
    with _io.open(path, "w", encoding="utf-8", newline="") as f:
        f.write("---\ntitle: \"Сирота\"\ntype: article\ntags: []\nsummary: \"копия без мастера\"\nstatus: active\ncreated: 2026-09-17\n---\n\n"
                "текст копии, у которой нет мастера в raw/.\n")
    return "копия источника без мастера"


def c138_batch_without_unclear_list(tmp, originals):   # §68
    """Партия описаний с записью без контекста, а списка решений нет.

    Ровно та слепота, которую назвал владелец: прежняя проверка смотрела только файл списка и молчала, когда
    записи без контекста есть, а файла нет. Канарейка добавляет такую запись в партию.
    """
    import glob as _glob
    import io as _io
    import json as _json
    batches = sorted(_glob.glob(os.path.join(tmp, "_staging", "**", "descriptions-*.json"), recursive=True))
    if not batches:
        raise MutationNotApplied("партий описаний не нашлось")
    path = batches[0]
    originals.append((path, _io.open(path, encoding="utf-8").read()))
    data = _json.loads(_io.open(path, encoding="utf-8").read())
    data.setdefault("items", []).append({"id": "zz-canary-без-контекста", "description": "непонятный кадр",
                                         "visible_text": "", "note_understandable": False})
    with _io.open(path, "w", encoding="utf-8", newline="") as f:
        f.write(_json.dumps(data, ensure_ascii=False, indent=1))
    return "в партию добавлена запись без контекста, списка решений для неё нет"


def c139_audio_without_transcript(tmp, originals):   # §72
    """Аудио в сырье, которого не называет ни одна запись корпуса.

    Дыра: волна могла закрыться с аудио и без расшифровок. Канарейка кладёт файл аудио, к которому не ведёт
    ни одна запись.
    """
    import io as _io
    import os as _os
    path = _os.path.join(tmp, "raw", "telegram", "voice_messages", "zz-canary-audio.ogg")
    originals.append((path, None))
    with _io.open(path, "wb") as f:
        f.write(b"OggS")
    return "аудио без записи корпуса и без расшифровки"


def c140_registry_link_invented(tmp, originals):   # §73
    """В строке реестра инструментов стоит адрес, которого нет в её записи."""
    path = os.path.join(tmp, "wiki", "comparisons", "tools-registry.md")
    text = read(path)
    originals.append((path, text))
    lines = text.split("\n")
    for i, l in enumerate(lines):
        if l.startswith("| A2UI"):
            cells = [c.strip() for c in l.strip().strip("|").split("|")]
            cells[3] = "https://выдуманный-адрес.example/"
            lines[i] = "| " + " | ".join(cells) + " |"
            break
    else:
        raise MutationNotApplied("строки A2UI в реестре нет")
    write(path, "\n".join(lines))
    return "выдуманный веб-адрес в строке реестра"


def c141_materials_link_column(tmp, originals):   # §74
    """В реестре материалов убрана колонка ссылки: до материала не дойти.

    Владелец 2026-09-17 назвал это принципиальным недочётом: название есть, ссылки нет. Канарейка снимает
    колонку со страницы.
    """
    path = os.path.join(tmp, "wiki", "comparisons", "materials-registry.md")
    text = read(path)
    originals.append((path, text))
    lines = text.split("\n")
    for i, l in enumerate(lines):
        if l.strip().startswith("| Материал"):
            lines[i] = "| " + " | ".join(["Материал", "Что это", "О чём / зачем читать", "Источник в архиве"]) + " |"
    write(path, "\n".join(lines))
    return "из реестра материалов убрана колонка ссылки"


def c142_record_link_lost(tmp, originals):   # §75
    """Заметка ссылается на материал словами, а адреса в записи нет.

    Канарейка садится туда, где механизм может сработать: §75 судит по карте адресов, поэтому берётся запись,
    чей id сообщения в карте есть, и её адрес заменяется словом «тут». Первая версия канарейки брала любую
    запись с адресом и промахивалась (final25: 104/105) — у выбранной записи адреса в карте не было.
    """
    import glob as _g
    import json as _j
    import os as _o
    import re as _re
    mp_path = _o.path.join(tmp, "_tools", "tg-saved", "links-by-message.json")
    if not _o.path.exists(mp_path):
        raise MutationNotApplied("карты адресов нет: §75 судить не по чему")
    mp = _j.load(open(mp_path, encoding="utf-8"))
    for path in _g.glob(_o.path.join(tmp, "raw", "**", "*.md"), recursive=True):
        text = read(path)
        m = _re.search(r"(?m)^source_message_id:\s*(\d+)", text)
        if not m:
            continue
        for u in (mp.get(m.group(1)) or []):
            if u in text:
                originals.append((path, text))
                # все вхождения: адрес мог стоять и в шапке, и в теле — убираем его целиком
                write(path, text.replace(u, "тут"))
                return "из записи убран адрес из карты, о котором тело говорит словами"
    raise MutationNotApplied("нет записи, несущей адрес из карты")



def c143_address_in_name_column(tmp, originals):   # §76
    """Адрес материала стоит в колонке имени, а не в колонке ссылки.

    Так выглядела первая строка реестра материалов до 2026-09-18: «Раздел обучения Cline, … от Mastra,
    mastra.ai/learn» — имя строки несло адрес одного из материалов.
    """
    path = os.path.join(tmp, "wiki", "comparisons", "materials-registry.md")
    text = read(path)
    lines = text.split("\n")
    for i, l in enumerate(lines):
        if l.startswith("| AI Coding University"):
            cells = [c.strip() for c in l.strip().strip("|").split("|")]
            cells[0] = cells[0] + ", mastra.ai/learn"
            lines[i] = "| " + " | ".join(cells) + " |"
            break
    else:
        raise MutationNotApplied("строки AI Coding University в реестре материалов нет")
    originals.append((path, text))
    write(path, "\n".join(lines))
    return "адрес в колонке имени строки реестра"


def c144_table_glued_to_paragraph(tmp, originals):   # §77
    """Таблица склеена с абзацем: пустой строки перед шапкой нет.

    Так выглядела строка Swiss Cheese на странице agent-evals, когда владелец сказал «таблица сломалась».
    """
    path = os.path.join(tmp, "wiki", "concepts", "agent-evals.md")
    text = read(path)
    lines = text.split("\n")
    for i, l in enumerate(lines):
        if not l.startswith("| Метод") or i < 2:
            continue
        if not lines[i - 1].strip() and lines[i - 2].strip() and not lines[i - 2].startswith("|"):
            originals.append((path, text))
            del lines[i - 1]
            write(path, "\n".join(lines))
            return "убрана пустая строка между абзацем и шапкой таблицы"
        raise MutationNotApplied("перед шапкой таблицы «| Метод» нет пустой строки — состояние уже склеенное")
    raise MutationNotApplied("таблицы с шапкой «| Метод» нет")


def c145_address_delegated_to_record(tmp, originals):   # §78
    """Адрес артефакта убран из страницы: читателя отправляют за ним в запись.

    Владелец 2026-09-18: «Отправка читателя на страницу в /raw за ссылкой вместо того, чтобы поместить
    ссылку прямо в статью, утеря ссылок — это антипаттерн». Так выглядела строка про googleworkspace/cli
    в таблице страницы cli: имя репозитория без адреса.
    """
    path = os.path.join(tmp, "wiki", "concepts", "cli.md")
    text = read(path)
    needle = " — https://github.com/vercel-labs/agent-browser#skills"
    if needle not in text:
        raise MutationNotApplied("адреса репозитория agent-browser в таблице нет")
    originals.append((path, text))
    write(path, text.replace(needle, "", 1))
    return "адрес артефакта убран из ячейки страницы"


def c146_process_meta_in_page(tmp, originals):   # §79
    """В текст страницы дописана ремарка о том, как эта страница собиралась.

    Владелец 2026-09-18 нашёл её в [[task-handoff]]: «Материалом страницы эта механика держится на одном
    машинном описании статьи; самой статьи в корпусе нет» — притом что адрес статьи лежал в записи.
    """
    path = os.path.join(tmp, "wiki", "concepts", "task-handoff.md")
    text = read(path)
    if "Материалом страницы" in text:
        raise MutationNotApplied("ремарка о сборке страницы уже есть в тексте")
    originals.append((path, text))
    write(path, text.rstrip() + "\n\nМатериалом страницы эта механика держится на машинном описании.\n")
    return "ремарка о сборке страницы дописана в текст"


def c147_source_narration_in_page(tmp, originals):   # §79
    """Страница рассказывает о покрытии источника вместо предмета и отправляет за ссылкой в запись.

    Владелец 2026-09-18: «Ссылка на стороннюю реализацию динамических интерфейсов дана источником без
    разбора деталей» — и опять ссылка на заметку в `/raw` вместо прямой ссылки.
    """
    path = os.path.join(tmp, "wiki", "concepts", "artifact-driven-ui.md")
    text = read(path)
    if "дана источником без разбора" in text:
        raise MutationNotApplied("оборот о покрытии источника уже есть в тексте")
    originals.append((path, text))
    write(path, text.rstrip() + "\n\nСсылка на стороннюю реализацию дана источником без разбора деталей.\n")
    return "ремарка о покрытии источника дописана в текст"


def c148_evidence_disclaimer(tmp, originals):   # §79
    """Сводный дисклеймер о силе доказательства в тексте страницы.

    Владелец 2026-09-18: «она не вносит никакой пользы, раздувает количество символов, напоминает
    плохой юридический стиль типа „дисклеймер"» — про фразу «Оба объяснения — объяснения авторов,
    не измерения, и приводятся здесь как позиция, а не как факт». Сила доказательства живёт в шапке.
    """
    path = os.path.join(tmp, "wiki", "concepts", "artifact-driven-ui.md")
    text = read(path)
    if "как позиция, а не как факт" in text:
        raise MutationNotApplied("дисклеймер уже есть в тексте")
    originals.append((path, text))
    write(path, text.rstrip() + "\n\nОба объяснения — объяснения авторов, не измерения, и приводятся здесь как позиция, а не как факт.\n")
    return "сводный дисклеймер о силе доказательства дописан в текст"


def c149_material_grade_disclaimer(tmp, originals):   # §79
    """Дисклеймер о породе материала: «это самоотчёт, а не результат замера».

    Владелец 2026-09-18, про три таких случая: «они не пограничные, а типичные бессмысленные фразочки,
    которые засоряют статьи». Пойманы в tools-registry, context-rot, vendor-guidance-vs-corpus и mastra.
    """
    path = os.path.join(tmp, "wiki", "entities", "mastra.md")
    text = read(path)
    if "это самоотчёт" in text:
        raise MutationNotApplied("дисклеймер уже есть в тексте")
    originals.append((path, text))
    write(path, text.rstrip() + "\n\nОба возражения — это самоотчёт практика, а не результат замера.\n")
    return "дисклеймер о породе материала дописан в текст"


def b34_head_lists_block_form(tmp, originals):   # безвредно: Obsidian переписал шапку
    """Шапка со списками в блочной форме: `tags:` и `sources:` строками «- …».

    Так их пишет Obsidian, когда страницу правит владелец. До 2026-09-18 парсер такие строки пропускал,
    и проверки объявляли страницу без тегов и без источников (§4 и §20 на a2a). Мутация ничего не ломает.
    """
    path = os.path.join(tmp, "wiki", "concepts", "spec-driven-development.md")
    text = read(path)
    m = re.match(r"(?s)^---\n(.*?)\n---\n", text)
    if not m:
        raise MutationNotApplied("шапки нет")
    fm = m.group(1)
    def block(line):
        k, v = line.split(":", 1)
        v = v.strip()
        if not (v.startswith("[") and v.endswith("]")):
            return line
        items = [x.strip() for x in v[1:-1].split(",") if x.strip()]
        return k + ":\n" + "\n".join("  - " + x for x in items)
    new_fm = "\n".join(block(l) for l in fm.split("\n"))
    if new_fm == fm:
        raise MutationNotApplied("списков в шапке нет")
    originals.append((path, text))
    write(path, text[:m.start(1)] + new_fm + text[m.end(1):])
    return "шапка переписана списками в блочной форме"


def c151_coverage_report_instead_of_finding(tmp, originals):   # §80
    """В «Границах» вместо находки — отчёт о покрытии: «второго голоса… нет».

    Владелец 2026-09-19: «большинство записей в разделах „факты и цифры", „спорное", „границы" — филлеры,
    от которых один вред (замусоривают контекст)». Пример назван им же — дословно.
    """
    path = os.path.join(tmp, "wiki", "concepts", "generative-ui.md")
    text = read(path)
    if "Второго голоса" in text:
        raise MutationNotApplied("оборот уже есть в тексте")
    originals.append((path, text))
    write(path, text.rstrip() + "\n- Второго голоса о генеративном интерфейсе в корпусе нет: числа, кроме названных выше, ни один источник страницы не даёт.\n")
    return "в «Границы» дописан отчёт о покрытии вместо находки"


def c152_link_note_without_address(tmp, originals):   # §78
    """Страница оставила только отсылку в запись-ссылку, а адреса в статье нет.

    Владелец 2026-09-19: «ссылаться на raw/, когда там просто ссылка — плохой паттерн, вместо этого надо
    дать саму ссылку и описание. Но вместо этого опять видим ссылки на 2026-09-04-…-113804».
    """
    path = os.path.join(tmp, "wiki", "concepts", "generative-ui.md")
    text = read(path)
    if "https://github.com/thesysdev/openui" not in text:
        raise MutationNotApplied("адреса в статье нет — мутировать нечего")
    originals.append((path, text))
    write(path, text.replace("https://github.com/thesysdev/openui",
                             "([[2026-09-04-ssylka-na-repozitoriy-thesysdev-openui-113804]])"))
    return "адрес записи-ссылки заменён отсылкой в raw/"


def c153_page_composition_sentence(tmp, originals):   # §79
    """Страница рассказывает, из чего она собрана, а не о предмете.

    Владелец 2026-09-19: «опять нашел мета-запись которая описывает не предмет статьи, а то как статья
    устроена… искусственно раздувает объем, затрудняя чтение». Пример его же: «В корпусе понятие держится
    двумя записями о стандартах генеративного интерфейса…» в generative-ui.
    """
    path = os.path.join(tmp, "wiki", "concepts", "generative-ui.md")
    text = read(path)
    if "понятие держится" in text:
        raise MutationNotApplied("оборот уже есть в тексте")
    originals.append((path, text))
    write(path, text.rstrip() + "\n\nВ корпусе понятие держится двумя записями о стандартах генеративного интерфейса.\n")
    return "в текст страницы дописана фраза о том, из чего она собрана"


def c154_style_rule_recopied(tmp, originals):   # §81
    """Требование из свода дословно переписано в другой служебный документ.

    Владелец 2026-09-19: «должен быть один источник правды, из которого берут требования к стилю все
    остальные инструменты». Фраза берётся из нынешнего свода на ходу: зашитая в канарейку формулировка
    устарела после правок свода, и мутация перестала что-либо ломать (разобрано 2026-09-21).
    """
    style = page(tmp, "_toolkit/style.md")
    if not os.path.exists(style):
        raise MutationNotApplied("нет свода стиля")
    words = re.findall(r"[А-Яа-яЁёA-Za-z0-9«»]+", read(style))
    if len(words) < 40:
        raise MutationNotApplied("свод стиля слишком короток для фразы из восьми слов")
    phrase = " ".join(words[20:29])   # окно из девяти слов в начале свода: устойчиво и не зависит от правок ниже
    path = os.path.join(tmp, "_toolkit", "SCHEMA.md")
    text = read(path)
    if phrase in text:
        raise MutationNotApplied("фраза уже есть в SCHEMA")
    originals.append((path, text))
    write(path, text.rstrip() + "\n\n" + phrase + "\n")
    return "в SCHEMA переписана фраза из свода стиля"

def b35_log_stale_reference(tmp, originals):   # безвредно для §34
    """В истории (`log.md`) ссылка на файл, которого уже нет: история не переписывается.

    §34 с 2026-09-19 проверяет служебные документы, но не `log.md`: запись о прошлом называет файлы своего
    времени. Мутация ничего не ломает.
    """
    path = os.path.join(tmp, "log.md")
    text = read(path)
    originals.append((path, text))
    write(path, text.rstrip() + "\n- Ссылка на `_staging/new-pages-spec.md` оставлена как запись о том, что было.\n")
    return "в log.md дописана устаревшая ссылка (ожидается тишина)"


def c156_page_holds_active(tmp, originals):   # §79
    """Мета-ремарка в активном залоге: «Страница держит роль и её механику».

    Владелец 2026-09-19, волна 2: дети сняли эту фразу в `orchestrator`, но в `loop-engineering` вписали
    такую же про корпус («Корпусная рамка держит на этом же месте человеческий гейт»), а в
    `inference-routing-and-cost` оставили «страница держит». Пассивного «держится» в §79 хватало,
    активного залога не было.
    """
    path = os.path.join(tmp, "wiki", "concepts", "orchestrator.md")
    text = read(path)
    if "Страница держит" in text:
        raise MutationNotApplied("оборот уже есть в тексте")
    originals.append((path, text))
    write(path, text.rstrip() + "\n\nСтраница держит роль и её механику, а не практику пула агентов.\n")
    return "в текст страницы дописана мета-ремарка в активном залоге"


def c157_no_separate_row(tmp, originals):   # §80
    """Отчёт о том, есть ли у спора отдельная строка в карте расхождений.

    Найдено 2026-09-19 в волне 2: «Отдельной строки этот спор в карте конфликтов не имеет:
    ближайшая по предмету — шестая строка „Skills против MCP“…». Владелец про такую же строку в
    `harness-architecture` сказал: не возвращать.
    """
    path = os.path.join(tmp, "wiki", "concepts", "loop-engineering.md")
    text = read(path)
    if "Отдельной строки" in text:
        raise MutationNotApplied("оборот уже есть в тексте")
    originals.append((path, text))
    write(path, text.rstrip() + "\n\nОтдельной строки этот спор в карте расхождений не имеет.\n")
    return "в текст страницы дописан отчёт о покрытии карты расхождений"


def c158_concept_holds_active(tmp, originals):   # §79
    """Мета-ремарка о происхождении термина в активном залоге.

    Владелец 2026-09-19: про фразу «Определение в корпусе принадлежит вендору и опирается на его
    собственные замеры» в `context-rot` — «зря оставил, всё верно сработало, эта фраза не „законная",
    а очередной филлер». Проверка должна ловить и такое: подлежащее семейства понятий плюс активный залог.
    """
    path = os.path.join(tmp, "wiki", "concepts", "context-rot.md")
    text = read(path)
    if "принадлежит вендору" in text:
        raise MutationNotApplied("оборот уже есть в тексте")
    originals.append((path, text))
    write(path, text.rstrip() + "\n\nОпределение в корпусе принадлежит вендору и опирается на его собственные замеры.\n")
    return "в текст страницы дописана ремарка о происхождении термина"


def c160_intent_without_file(tmp, originals):   # §29
    """Ключ карты структуры без файла: путь переехал, а строка осталась.

    Проверка добавлена 2026-09-19 при полной актуализации карты: раньше `check` сверял отпечаток дерева,
    хеши навыков и покрытие, но не ловил строку intent, которой больше нечего объяснять.
    """
    path = os.path.join(tmp, "_toolkit", "project_map.py")
    text = read(path)
    marker = 'INTENT = {'
    if marker not in text[:text.find(marker) + 40] if marker in text else True:
        pass
    i = text.find(marker)
    if i == -1:
        raise MutationNotApplied("нет INTENT в project_map.py")
    if "no-such-file-for-canary" in text:
        raise MutationNotApplied("мутация уже применена")
    originals.append((path, text))
    write(path, text[:i + len(marker)] + '\n    "_staging/no-such-file-for-canary.md": ("нарочно", "нарочно"),' + text[i + len(marker):])
    return "в карту структуры дописан intent для несуществующего файла (ожидается §29)"


def c161_index_row_wrong_section(tmp, originals):   # §3 (расширение)
    """Строка индекса стоит не в своей секции: страница-понятие перечислена среди сущностей.

    Владелец 2026-09-21: «сделай чтоб расхождение индекса с деревом проверялось машинно». Такой дрейф
    копился незаметно: четыре страницы-понятия стояли в разделе Entities, две служебные не были названы.
    """
    p = page(tmp, "wiki/index.md")
    text = read(p)
    m = re.search(r"(?m)^- \[\[([^\]]+)\]\].*$", text)
    if not m:
        raise MutationNotApplied("в индексе нет строк со ссылками")
    line = m.group(0)
    i = text.find("## Entities")
    if i < 0:
        raise MutationNotApplied("в индексе нет раздела Entities")
    j = text.find("\n## ", i + 1)
    insert = j if j > 0 else len(text)
    originals.append((p, text))
    without = text.replace(line + "\n", "", 1)          # строка снята со своего места
    write(p, without[:insert] + line + "\n" + without[insert:])   # и поставлена в раздел Entities
    return "строка индекса переехала в раздел Entities"


def c162_index_total_wrong(tmp, originals):   # §3 (расширение)
    """Итог индекса разошёлся с деревом: число страниц поправлено в строке, а не в файлах."""
    p = page(tmp, "wiki/index.md")
    text = read(p)
    m = re.search(r"(?m)^> Last updated:.*Total pages: (\d+)", text)
    if not m:
        raise MutationNotApplied("нет строки итога «Total pages»")
    originals.append((p, text))
    write(p, text.replace("Total pages: " + m.group(1), "Total pages: " + str(int(m.group(1)) + 3), 1))
    return "итог индекса увеличен на три страницы без правки дерева"


def c163_service_doc_repeated_line(tmp, originals):   # §82
    """Служебный документ испорчен записью: содержательная строка размножена десятками раз.

    Владелец 2026-09-21: README раздулся до 2,26 МБ — пункт про смотрелки повторён 4815 раз, разделы при этом пропали.
    Порча прошла в коммит, потому что сторож смотрел на вики и реестры, но не на сам служебный документ.
    """
    p = page(tmp, "README.md")
    text = read(p)
    lines = [l for l in text.split("\n") if len(l.strip()) >= 40]
    if not lines:
        raise MutationNotApplied("в README нет содержательных строк")
    originals.append((p, text))
    write(p, text + ("\n" + lines[0]) * 40 + "\n")
    return "строка README повторена сорок раз"


def c164_log_entry_free_form(tmp, originals):   # §12
    """Запись журнала потеряла форму контракта: заголовок стал свободным («## 2026-09-19 — тема»).

    Владелец 2026-09-21 просил вернуться к первоисточнику: gist Карпатого требует у каждой записи постоянный
    префикс `## [дата] действие | тема`, иначе журнал не читается грепом. Три записи такой формы прошли молча,
    потому что сторож пропускал всё, что не начинается с «## [».
    """
    p = page(tmp, "log.md")
    text = read(p)
    m = re.search(r"(?m)^## \[(\d{4}-\d{2}-\d{2})\]\s+(\S+)\s*\|\s*(.+)$", text)
    if not m:
        raise MutationNotApplied("в журнале нет записи формы «## [дата] действие | тема»")
    originals.append((p, text))
    write(p, text.replace(m.group(0), "## %s — %s" % (m.group(1), m.group(3)), 1))
    return "запись журнала переписана свободной формой"



def c165_audit_orphan(tmp, originals):   # §84
    """В `_staging/audit/` появился выход, которого никто в поставке не называет.

    Решение владельца 2026-09-21: файл держится ссылкой — кодом и сторожем, реестром, документом поставки или
    доказательством долга. Файл, названный только другими выходами того же прохода, — отработавшее, его место
    в `_staging/archive/audit/`.
    """
    d = os.path.join(tmp, "_staging", "audit")
    if not os.path.isdir(d):
        raise MutationNotApplied("нет каталога _staging/audit")
    # Имя собирается из частей, а не лежит литералом: референсный сторож §84 ищет имя по всему
    # дереву, и литерал в исходнике харнесса (или в документе, который его цитирует) гасит находку.
    # Найдено 2026-09-25 полным прогоном: сторож молчал, потому что skill-документ цитировал имя.
    name = "-".join(("zz", "canary", "loose", "artifact")) + ".json"
    p = os.path.join(d, name)
    originals.append((p, None))
    write(p, "{\"" + "canary" + "\": 165}\n")
    return "в _staging/audit/ положен файл, не названный нигде в поставке"



def c166_readme_growth(tmp, originals):   # §85
    """README вырос выше потолка: во входной файл дописали знание, которому место в своём.

    Решение владельца 2026-09-21: потолок объявлен дважды и пробит дважды, поэтому он проверяется, а не
    обещается. Входной файл называет адреса и шаги; знание уходит в SCHEMA, карту или `_staging/`.
    """
    p = page(tmp, "README.md")
    text = read(p)
    if not text.strip():
        raise MutationNotApplied("README.md пуст")
    originals.append((p, text))
    filler = "\n".join("- строка роста %d: знание, которому место в своём файле, а не во входном" % i
                        for i in range(1, 41))
    write(p, text.rstrip("\n") + "\n" + filler + "\n")
    return "в README дописано сорок строк знания"



def c167_theses_digest_gap(tmp, originals):   # §86
    """В хранилище появилась страница, которой нет в своде тезисов.

    Ровно та дыра, из-за которой сегодня молча не работал метод «свод тезисов плюс источник целиком»: полноту
    свода проверял только сам инструмент, а результат никто.
    """
    p = page(tmp, "wiki/concepts/kanareyka-svod.md")
    originals.append((p, None))
    write(p, "---\ntitle: \"Канареечная страница свода\"\ntype: concept\ncreated: 2026-09-21\n"
             "updated: 2026-09-21\nstatus: active\ntags: [harness]\nsources: []\n---\n\nТело канареечной "
             "страницы: нужна только чтобы свод тезисов оказался неполон.\n")
    return "в хранилище добавлена страница, не названная в своде тезисов"



def c178_doctor_names_hidden_engine(tmp, originals):   # раздел «Окружение»: движка нет — доктор называет
    """Канарейка доктора на урезанное окружение: движок спрятан, и об этом надо сказать.

    Прежняя канарейка доктора ловила только пропажу хранилища (ядро). Здесь проверяется вторая половина
    обещания раздела «Окружение»: без движков путь проходит, и доктор перечисляет, чего нет и чем заменить.
    Запуск идёт в подменённом `PATH` — смотри `bare:` в проверке `tool`.
    """
    return ("`PATH` смотрит в пустой каталог: на машине нет ни git, ни ffmpeg, ни firecrawl. "
            "Доктор обязан назвать ffmpeg как отсутствующую надстройку вместе со способом поставить.")


def c179_rule_kind_missing(tmp, originals):   # §59: род правила
    """У правила стёрт род — реестр обязан это заметить.

    Колонка «род» говорит, к кому правило уедет при разделении репозиториев. Пустая клетка читается как
    «не решено»: правило без рода живёт на памяти сессии и разъезжается при первой же правке.
    """
    p = page(tmp, "_toolkit/rules.tsv")
    text = read(p)
    if not text.strip():
        raise MutationNotApplied("_toolkit/rules.tsv пуст")
    originals.append((p, text))
    lines = text.split("\n")
    for i, line in enumerate(lines):
        f = line.split("\t")
        if len(f) > 3 and f[2].strip() in ("механизм", "экземпляр") and not line.startswith("#"):
            f[2] = ""
            lines[i] = "\t".join(f)
            break
    else:
        raise MutationNotApplied("в реестре нет строки с названным родом")
    write(p, "\n".join(lines))
    return "у первого правила реестра стёрта колонка «род»"


def c176_case_in_doc_outside_list(tmp, originals):   # §87: расширение списка документов
    """Разбор случая в документе, которого сторож раньше не видел.

    Смысловой проход 2026-09-22 нашёл разборы в документах вне `_staging/` первого уровня (обвязка выгрузки,
    слой аудита, папка отбора). Список документов расширен — и это надо ловить, иначе расширение остаётся
    заявлением.
    """
    p = page(tmp, "_toolkit/tools/tg-saved/README.md")
    text = read(p)
    if not text.strip():
        raise MutationNotApplied("_toolkit/tools/tg-saved/README.md пуст")
    originals.append((p, text))
    case = ("\nСнято 2026-09-15: скрипт больше не нужен, потому что реестр стал страницей.\n")
    write(p, text.rstrip("\n") + "\n" + case)
    return "разбор случая дописан в документ вне прежнего списка"


def c177_case_mid_line_bold(tmp, originals):   # §87: расширение окна открывателей
    """Разбор случая в новой форме: без начала строки, в жирной разметке, с новым маркером.

    Прежнее окно требовало якоря на начало строки и одного символа разметки, поэтому разборы вида
    «**Проверены 2026-09-15**» и «Урок 2026-09-17» проходили молча — замер 2026-09-22.
    """
    p = page(tmp, "_toolkit/scripts-inventory.md")
    text = read(p)
    if not text.strip():
        raise MutationNotApplied("_toolkit/scripts-inventory.md пуст")
    originals.append((p, text))
    case = "\n- в строке таблицы: инструмент снят, **Урок 2026-09-17: генератор мешал страницам** — правило выше.\n"
    write(p, text.rstrip("\n") + "\n" + case)
    return "разбор случая дописан в жирной разметке и в середине строки"


def c168_service_doc_case(tmp, originals):   # §87
    """В служебный документ вернулся разбор случая: дата и цитата как формулировка правила.

    Ровно то, что дважды не срабатывало прозой: прецеденты возвращались в `style.md`. Теперь разбор случая —
    находка, а разборы живут в `log.md`.
    """
    p = page(tmp, "_toolkit/link-recovery.md")
    text = read(p)
    if not text.strip():
        raise MutationNotApplied("_toolkit/link-recovery.md пуст")
    originals.append((p, text))
    case = ("\nСнято 2026-09-15 по замечанию владельца: «ссылки мы ещё не раз будем обрабатывать, это факт» — "
            "поэтому цепочка зафиксирована здесь, а не в памяти сессии.\n")
    write(p, text.rstrip("\n") + "\n" + case)
    return "в служебный документ дописан разбор случая с датой и цитатой"


def c169_service_doc_growth(tmp, originals):   # §88
    """Служебный документ вырос выше потолка — прецеденты возвращаются сами, если длину ничто не держит."""
    p = page(tmp, "_toolkit/style.md")
    text = read(p)
    if not text.strip():
        raise MutationNotApplied("_toolkit/style.md пуст")
    originals.append((p, text))
    filler = "\n".join("- разбор %d: как это было раньше и почему стало так" % i for i in range(1, 31))
    write(p, text.rstrip("\n") + "\n" + filler + "\n")
    return "в служебный документ дописано тридцать строк разборов"



def c170_domain_declaration_gone(tmp, originals):   # §89
    """Объявление домена экземпляра опустело — проверки домена молчат, и никто об этом не говорит.

    Ровно тот случай, ради которого §89 заведён: молчание о неприменимости выглядит как успех. Канарейка
    стирает объявление (оставляя файл) и ждёт, что сторож скажет вслух.
    """
    p = page(tmp, "_staging/domain.local.tsv")
    text = read(p) if os.path.exists(p) else ""
    if text.strip():
        originals.append((p, text))
    write(p, "# объявление домена стёрто канарейкой\n")
    return "объявление домена экземпляра стёрто"


def c171_toolkit_own_file_via_root(tmp, originals):   # §90
    """Инструмент снова ищет свой файл через корень проекта: после переезда в `_toolkit/` найдёт чужое место.

    Так было в тринадцати местах до развязки; канарейка возвращает привычку и ждёт сторожа.
    """
    p = page(tmp, "_toolkit/rules_check.py")
    text = read(p)
    if not text.strip():
        raise MutationNotApplied("_toolkit/rules_check.py пуст")
    originals.append((p, text))
    # Строка обязана лежать внутри функции: голый модуль-уровень роняет `import rules_check`
    # внутри линтера (§59) — и падает весь прогон вместо раздела §90. Найдено 2026-09-25 полным
    # прогоном: канарейка всегда была красной не потому, что сторож слеп, а потому что мутация
    # убивала чекер. Текстовый паттерн, который ищет §90, сохранён дословно.
    write(p, text.rstrip("\n") + "\n\ndef _canary_self(root):\n"
          "    return os.path.join(root, \"_staging\", \"lint_wiki.py\")  # канарейка §90\n")
    return "в скрипт возвращена привычка искать свой файл через корень проекта"


def c172_domain_word_in_mechanism(tmp, originals):   # §91
    """В файл механизма вернулось слово домена экземпляра: посторонний снова видит чужую тему.

    Слово берётся из объявления домена, а не выдумывается: канарейка обязана ловить именно тот домен,
    который объявил этот экземпляр.
    """
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import domain as _domain
    words = _domain.markers(tmp)
    if not words:
        raise MutationNotApplied("объявление домена не называет слов темы — канарейке нечего вставлять")
    p = page(tmp, "_toolkit/skills/knowledge-base-quality-gates/SKILL.md")
    text = read(p)
    if not text.strip():
        raise MutationNotApplied("method-skill knowledge-base-quality-gates/SKILL.md пуст")
    originals.append((p, text))
    write(p, text.rstrip("\n") + f"\n\nСлово домена экземпляра в method-skill: {words[0]}.\n")
    return f"в method-skill вписано слово домена «{words[0]}»"




def c175_doctor_names_missing_vault(tmp, originals):   # доктор окружения
    """Доктор без хранилища: обязан назвать отсутствующее и выйти с ненулевым кодом.

    Ядро окружения — не «команда есть», а «путь проходит». Убранное хранилище — тот случай, когда доктор
    обязан сказать вслух; молчаливый ноль здесь означал бы, что посторонний узнаёт о недостающем из
    трассировки первого же инструмента.
    """
    vault = page(tmp, "wiki")
    if not os.path.isdir(vault):
        raise MutationNotApplied("каталога wiki/ нет — мутировать нечего")
    moved = vault + ".canary-off"
    os.rename(vault, moved)
    originals.append((moved, RESTORE_MOVE))
    return "хранилище wiki/ убрано: доктор должен назвать его отсутствующим"


def c173_backlinks_without_corpus(tmp, originals):   # рельсы §42: source_backlinks.py
    """Рельсы обратных ссылок без мастера: инструмент обязан отказаться, а не собрать пустое.

    У свежего экземпляра `raw/` нет вовсе — и «Где использован» собирать не из чего. Молчаливый ноль здесь
    опаснее падения: раздел §42 увидел бы пустые копии и промолчал.
    """
    raw = page(tmp, "raw")
    if not os.path.isdir(raw):
        raise MutationNotApplied("каталога raw/ нет — мутировать нечего")
    moved = raw + ".canary-off"
    os.rename(raw, moved)
    originals.append((moved, RESTORE_MOVE))       # вернуть каталог на место после прогона
    return "мастер-каталог raw/ убран: рельсы обратных ссылок должны отказаться"


def c174_digest_without_vault(tmp, originals):   # рельсы §86: stance_context.py
    """Рельсы свода без хранилища: инструмент обязан отказаться, а не отчитаться пустым сводом.

    «Страниц нет» — законное состояние свежего экземпляра, и о нём инструмент говорит и выходит с нулём.
    «Хранилища нет» — сломанный экземпляр, и это уже ошибка: молчание тут выглядело бы как успех.
    """
    vault = page(tmp, "wiki")
    if not os.path.isdir(vault):
        raise MutationNotApplied("каталога wiki/ нет — мутировать нечего")
    moved = vault + ".canary-off"
    os.rename(vault, moved)
    originals.append((moved, RESTORE_MOVE))
    return "каталог wiki/ убран: рельсы свода должны отказаться"



def c185_registry_row_canary_dropped(tmp, originals):   # реестр правил: шапка и строки
    """Из строки реестра снята канарейка, которая сторожит её раздел: реестр обещает покрытие уже, чем есть.

    Замер 2026-09-24 нашёл так 41 канарейку, сторожившую раздел и не названную в его строке: снятая канарейка
    пропадает из реестра молча — и следующая правка убирает её как «лишнюю». Раздел берётся с ОДНОЙ строкой:
    у раздела с двумя строками канарейка названа дважды, и снятие одной ничего не меняет (первый прогон
    2026-09-24 выбрал именно такой раздел и не покраснел).
    """
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import rules_check as _rc
    p = page(tmp, "_toolkit/rules.tsv")
    text = read(p)
    if not text.strip():
        raise MutationNotApplied("_toolkit/rules.tsv пуст")
    canary_src = read(page(tmp, "_toolkit/canary_test.py"))
    lines = text.split("\n")
    per_section = {}
    for i, line in enumerate(lines):
        cells = line.split("\t")
        if len(cells) > 5 and cells[4].strip().isdigit():
            per_section.setdefault(cells[4].strip(), []).append(i)
    for sec, idxs in per_section.items():
        cells = lines[idxs[0]].split("\t")
        if len(idxs) == 1 and cells[5].strip() and _rc.canaries_waiting_for(canary_src, sec):
            originals.append((p, text))
            cells[5] = ""
            lines[idxs[0]] = "\t".join(cells)
            write(p, "\n".join(lines))
            return f"из строки «{cells[0][:40]}» снята канарейка раздела §{sec}"
    raise MutationNotApplied("в реестре нет раздела с одной строкой и своими канарейками")


def c186_registry_guard_not_from_set(tmp, originals):   # реестр правил: шапка и строки
    """Сторож в реестре заменён словом не из набора: строка читается как проверка, а проверки нет.

    Набор — номер раздела, имя файла, «канарейка», «совет». Пустая клетка и слово из набора означают разное,
    и подмена одного другим делает реестр неотличимым от списка пожеланий.
    """
    p = page(tmp, "_toolkit/rules.tsv")
    text = read(p)
    if not text.strip():
        raise MutationNotApplied("_toolkit/rules.tsv пуст")
    lines = text.split("\n")
    for i, line in enumerate(lines):
        cells = line.split("\t")
        if len(cells) > 4 and cells[4].strip().isdigit():
            originals.append((p, text))
            cells[4] = "когда-нибудь"
            lines[i] = "\t".join(cells)
            write(p, "\n".join(lines))
            return f"сторож строки «{cells[0][:40]}» заменён словом не из набора"
    raise MutationNotApplied("в реестре нет строки с разделом")


def c187_domain_word_in_root_entry(tmp, originals):   # §91
    """Слово домена экземпляра вернулось в корневой вход — тот, что читает агентская сессия первой.

    Корневые входы держатся в стороже поимённо, и до 2026-09-24 новый вход (`AGENTS.md`) в этот список не
    попадал: слово домена в нём сторожа не будило, файл жил вне проверки. Канарейка замыкает этот список.
    """
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import domain as _domain
    words = _domain.markers(tmp)
    if not words:
        raise MutationNotApplied("объявление домена не называет слов темы — канарейке нечего вставлять")
    p = page(tmp, "AGENTS.md")
    text = read(p)
    if not text.strip():
        raise MutationNotApplied("AGENTS.md пуст или отсутствует")
    originals.append((p, text))
    write(p, text.rstrip("\n") + f"\n\nПроверка входа: {words[0]}.\n")
    return f"в корневой вход вписано слово домена «{words[0]}»"


def c227_citation_guard_blind(tmp, originals):   # дежурство цитат
    """Страж цитат ослеплён: verify всегда возвращает чистое — дежурство обязано упасть.

    Дежурство (`check_citations.py`) доказывает обе стороны каждый прогон, но само держится
    на честности `verify_draft`: мутация, превращающая его вердикт в константу, обязана ронять
    задачу с «ПРОПУЩЕН», иначе слепой страж дежурит дальше молча.
    """
    p = page(tmp, "_toolkit/citation_ledger/sources.py")
    text = read(p)
    old = "    code = 1 if errors else (1 if (strict and warnings) else 0)"
    if old not in text:
        raise MutationNotApplied("якорь verify_draft изменился — мутация устарела")
    originals.append((p, text))
    write(p, text.replace(old, "    code = 0"))
    return "verify_draft ослеплён: дежурство должно упасть с «ПРОПУЩЕН»"


def c228_coverage_check_blind(tmp, originals):   # сверка покрытия веера
    """Сверка покрытия ослепла на лишнее: выдуманный id проходит — самопроверка обязана упасть.

    Сверка (`check_coverage.py --self-test`) доказывает четыре знака каждый прогон, но держится
    на строке лишнего направления: мутация, обнуляющая её, обязана ронять самопроверку, иначе
    сверка ловит только пропуски, а выдуманные id едут дальше в карточки.
    """
    p = page(tmp, "_toolkit/check_coverage.py")
    text = read(p)
    old = "    extra = sorted(set(collected) - set(expected))"
    if old not in text:
        raise MutationNotApplied("якорь лишнего направления изменился — мутация устарела")
    originals.append((p, text))
    write(p, text.replace(old, "    extra = []"))
    return "лишнее направление обнулено: самопроверка должна упасть с «ПРОПУЩЕН»"


def c229_panels_check_blind(tmp, originals):   # проверка отрисованных панелей
    """Проверка панелей ослепла на порядок: чужой srcdoc проходит — самопроверка обязана упасть.

    Проверка (`check_panels.py --self-test`) доказывает оба знака каждый прогон, но порядок
    панель→агрегат держится на одной строке совпадения: мутация, вечно прощающая чужое
    встроенное, обязана ронять самопроверку, иначе агрегат старше панелей читается как свежий.
    """
    p = page(tmp, "_toolkit/check_panels.py")
    text = read(p)
    old_match = ("        else:\n"
                 "            problems.append(f\"srcdoc #{i} не совпал ни с одной панелью"
                 " — агрегат старше панелей\")")
    old_req = ("    for rel in required:\n"
               "        if rel != AGGR and rel in panels and rel not in matched:\n"
               "            problems.append(f\"панель не встроена в агрегат: {rel}\")")
    if old_match not in text or old_req not in text:
        raise MutationNotApplied("якорь порядка панелей изменился — мутация устарела")
    originals.append((p, text))
    text = text.replace(old_match, "        else:\n            pass  # канарейка ослепила")
    write(p, text.replace(old_req, "    pass  # канарейка ослепила порядок"))
    return "чужой srcdoc прощён: самопроверка должна упасть с «ПРОПУЩЕНА»"


CANARIES = [
    (1, "битая вики-ссылка", "lint §1", "lint", 1, c1_broken_link),
    (2, "подмена числа в «Фактах»", "verify_numbers", "verify", None, c2_number_change),
    (3, "удаление атрибуции у числа", "lint §15", "lint", 15, c3_drop_attribution),
    (6, "пустая ячейка в таблице", "lint §9", "lint", 9, c6_empty_table_cell),
    (7, "тег вне таксономии в frontmatter", "lint §5", "lint", 5, c7_unknown_tag),
    (9, "подмена числа в отчёте", "lint §14", "lint", 14, c9_report_number),
    (10, "незакавыченное YAML с «: »", "lint §4", "lint", 4, c10_unquoted_yaml),
    (11, "безвредно: пустая строка в конце", "тишина", "none", None, b1_blank_line),
    (12, "безвредно: проза без чисел", "тишина", "none", None, b2_new_section),
    (13, "безвредно: перестановка двух пунктов «Связи»", "тишина", "none", None, b3_reorder_links),
    (14, "страница-сирота без входящих ссылок", "lint §2", "lint", 2, c11_orphan_page),
    (15, "страница отсутствует в index.md", "lint §3", "lint", 3, c12_index_miss),
    (17, "страница длиннее 200 строк", "lint §7", "lint", 7, c14_long_page),
    (18, "безвредно: правка копии источника в хранилище", "тишина", "none", None, b17_mirror_edit_is_benign),
    (19, "пустой файл страницы", "lint §10", "lint", 10, c16_empty_file),
    (20, "недопустимый verification-status", "lint §13", "lint", 13, c17_bad_status),
    (21, "вендорский самоотчёт при высокой уверенности", "lint §17", "lint", 17, c21_evidence_conflict),
    (22, "страница длиннее 150 строк без обоснования", "lint §18", "lint", 18, c22_over_soft_limit),
    (23, "высокая уверенность на одном корпусе", "lint §19", "lint", 19, c23_corpus_only_high),
    (24, "упомянут источник без объявления в sources", "lint §20", "lint", 20, c24_undeclared_source),
    (25, "ручное число в прозе пакета (291 ребро)", "lint §21", "lint", 21, c25_prose_number),
    (26, "остаток черновика: комментарий-заглушка", "lint §22", "lint", 22, c26_draft_leftover),
    (27, "процессная проза вместо факта", "lint §23", "lint", 23, c27_process_prose),
    (28, "умолчание stance вне допустимого набора", "lint §24", "lint", 24, c28_source_without_stance),
    (29, "запись лога с неизвестным действием", "lint §12", "lint", 12, c29_log_unknown_action),
    (30, "запись лога с датой из будущего", "lint §12", "lint", 12, c30_log_future_date),
    (31, "ось contradicts без строки в карте конфликтов", "lint §25", "lint", 25, c31_stance_without_map_row),
    (32, "машинное чтение при confidence: high", "lint §26", "lint", 26, c32_ocr_high_confidence),
    (33, "числа из схем без отметки о втором прочтении", "lint §26", "lint", 26, c33_ocr_without_confirmation),
    (34, "безвредно: машинное чтение с подтверждением", "тишина", "none", None, b4_ocr_marked_with_confirmation),
    (35, "утверждение о прошлом пакете без даты", "lint §27", "lint", 27, c35_past_claim_undated),
    (36, "число о прошлом пакете не сходится со слепком", "lint §27", "lint", 27, c36_past_claim_wrong_number),
    (37, "закрытый пункт долга без доказательства", "lint §28", "lint", 28, c37_debt_closed_without_evidence),
    (38, "пункт долга со статусом вне набора", "lint §28", "lint", 28, c38_debt_bad_status),
    (39, "карта структуры устарела", "lint §29", "lint", 29, c39_stale_map),
    (40, "структура без объяснения замысла", "lint §29", "lint", 29, c40_unexplained_node),
    (43, "у мостика нет записанного окна", "lint §30", "lint", 30, c43_bridge_without_window),
    (44, "отказ реестра не назван в мостике", "lint §30", "lint", 30, c44_refusal_missing_from_bridge),
    (45, "зеркало только для чтения", "lint §31", "lint", 31, c45_mirror_readonly),
    (47, "служебный файл внутри хранилища", "lint §31", "lint", 31, c47_service_file_in_vault),
    (48, "предпосылка отказа отпала", "lint §28", "lint", 28, c48_refusal_premise_broken),
    (49, "письмо без frontmatter", "lint §32", "lint", 32, c49_letter_without_frontmatter),
    (50, "из мостика вырезаны ловушки", "lint §30", "lint", 30, c50_bridge_without_traps),
    (51, "боль карточки не отражена на странице болей", "lint §33", "lint", 33, c51_pain_source_missing),
    (52, "документ обвязки без замысла", "lint §29", "lint", 29, c52_unexplained_doc),
    (53, "в заголовке письма незаполненный плейсхолдер", "lint §32", "lint", 32, c53_letter_placeholder_frontmatter),
    (54, "безвредно: заполненный заголовок ответа", "тишина", "none", None, b5_letter_frontmatter_filled),
    (55, "ссылка служебного документа на несуществующий файл", "lint §34", "lint", 34, c55_dead_internal_ref),
    (56, "безвредно: ссылка-заглушка в служебном документе", "тишина", "none", None, b6_internal_ref_placeholder),
    (57, "ключ в файле проекта", "lint §35", "lint", 35, c57_secret_in_tree),
    (58, "безвредно: значение уже вырезано", "тишина", "none", None, b7_secret_already_cut),
    (59, "реестр удалённых копий устарел", "lint §36", "lint", 36, c59_stale_offsite_log),
    (60, "безвредно: копия подтверждена сегодня", "тишина", "none", None, b8_offsite_fresh),
    (61, "отправленный документ изменён после отправки", "lint §32", "lint", 32, c61_sent_doc_modified),
    (62, "безвредно: отправленный документ перезаписан тем же", "тишина", "none", None, b9_sent_doc_rewritten_same),
    (65, "шаг задачи ссылается на удалённый скрипт", "lint §38", "lint", 38, c65_task_refers_missing_script),
    (66, "безвредно: реестр задач перезаписан тем же", "тишина", "none", None, b11_tasks_rewritten_same),
    # 67 переведена в безвредные: правило «ровно одна отправленная строка на дату» снято решением
    # (разбор десятый: повторные отправки в день — штатный случай). Канарейка осталась от снятого правила
    # и после его отмены обвиняла законное поведение: аттестация показывала 55/56 на ровном месте.
    (67, "безвредно: две отправленных строки за дату — штатный случай", "тишина", "none", None, c67_two_sent_rows),
    (68, "безвредно: реестр слепков перезаписан тем же", "тишина", "none", None, b12_ledger_rewritten_same),
    (69, "из .gitignore убрано правило исключения рабочих выгрузок", "lint §39", "lint", 39, c69_gitignore_rule_removed),
    (70, "безвредно: gitignore перезаписан тем же", "тишина", "none", None, b13_gitignore_rewritten_same),
    (63, "примечание о приросте не сходится с ячейками", "lint §37", "lint", 37, c63_growth_arithmetic_broken),
    (64, "безвредно: прирост сходится с ячейками", "тишина", "none", None, b10_growth_arithmetic_ok),
    (71, "литературе нет строки в блоке «Литература»", "lint §40", "lint", 40, c71_literature_without_row),
    (72, "безвредно: строка литературы перенесена внутри блока", "тишина", "none", None, b14_literature_row_moved),
    (73, "описанный файл картинки отсутствует в images", "lint §41", "lint", 41, c72_illustration_file_missing),
    (74, "описание картинки без вставки ![[…]]", "lint §41", "lint", 41, c73_illustration_without_embed),
    (75, "безвредно: источник с иллюстрацией перезаписан тем же", "тишина", "none", None, b15_illustration_rewritten_same),
    (76, "инлайновая сноска ^[[…]] в теле страницы", "lint §11", "lint", 11, c76_inline_footnote),
    (77, "источника нет в хранилище (копия удалена)", "lint §8", "lint", 8, c77_mirror_missing),
    (78, "ссылка на участок, которого на странице нет", "lint §42", "lint", 42, c78_backlink_bad_section),
    (79, "в копии источника нет раздела «Где использован»", "lint §42", "lint", 42, c79_backlink_block_missing),
    (80, "раздел называет страницу, которая источник не объявляет", "lint §42", "lint", 42, c80_backlink_page_not_declared),
    (81, "безвредно: копия источника с разделом перезаписана тем же", "тишина", "none", None, b18_backlink_block_rewritten_same),
    (84, "дата в шапке не в формате ГГГГ-ММ-ДД", "lint §4", "lint", 4, c84_broken_date),
    (82, "у кандидата в страницы снято решение", "lint §43", "lint", 43, c82_candidate_decision_removed),
    (91, "позиция спора телеграфной строкой", "lint §45", "lint", 45, c91_telegraphic_position),
    (93, "смысловая ячейка назывной строкой", "lint §45", "lint", 45, c93_telegraphic_prose_cell),
    (86, "пакет за дату без снапшота", "lint §21", "lint", 21, c86_package_without_snapshot),
    (87, "спор без строки применимости", "lint §25", "lint", 25, c87_dispute_without_conditions),
    (88, "у страницы волны нет вердикта оси", "lint §25", "lint", 25, c88_wave_page_without_verdict),
    (90, "кросс-страничная пара без вердикта", "lint §25", "lint", 25, c90_cross_pair_without_verdict),
    (83, "безвредно: очередь решений перезаписана тем же", "тишина", "none", None, b19_decisions_rewritten_same),
    (92, "безвредно: позиция спора переписана полным предложением", "тишина", "none", None, b20_rewritten_position_benign),
    (94, "безвредно: смысловая ячейка переписана полным предложением", "тишина", "none", None, b21_prose_cell_rewritten_benign),
    (95, "безвредно: смысловая ячейка сломана и возвращена как была", "тишина", "none", None, b22_prose_cell_broken_and_restored),
    (96, "безвредно: сноска «путь:строка» в служебном документе", "тишина", "none", None, b23_internal_ref_with_line_number),
    (89, "безвредно: вердикты волны перезаписаны тем же", "тишина", "none", None, b11_wave_verdicts_rewritten),
    (181, "безвредно: слово домена внутри инструментов экземпляра", "тишина", "none", None, b14_domain_word_inside_instance_tools),
    # 2026-09-16: вторая форма инлайновой сноски (путь источника) и дрейф числа в split-justification
    (97, "инлайновая сноска-источник ^[raw/…]", "lint §11", "lint", 11, c97_provenance_footnote_raw),
    (98, "число строк в split-justification разошлось с файлом", "lint §18", "lint", 18, c98_split_number_drift),
    (99, "безвредно: поле split-justification переписано с верным числом строк", "тишина", "none", None, b27_split_number_in_sync),
    # 2026-09-16: источник добавлен, а смыслового прохода по нему нет (и безвредная: источник до наряда)
    (110, "очередь новее панели: дашборд собран из старых данных", "lint §50", "lint", 50, c110_queue_newer_than_panel),
    (111, "безвредно: очередь перезаписана тем же содержимым", "тишина", "none", None, b33_panels_rebuilt),
    (108, "боли заведены разделом вместо категории", "lint §48", "lint", 48, c108_pain_section_outside_categories),
    (109, "безвредно: строка дописана в существующую категорию", "тишина", "none", None, b32_pain_row_in_category),
    (106, "в реестре инструментов строка организации вместо инструмента", "lint §49", "lint", 49, c106_person_in_tools_registry),
    (107, "безвредно: строка инструмента с типом из набора", "тишина", "none", None, b31_tool_row_with_type),
    (104, "две боли слиты в одну ячейку таблицы болей", "lint §48", "lint", 48, c104_row_merged_into_cell),
    (100, "источник добавлен после последнего наряда и в наряде не назван", "lint §46", "lint", 46, c100_source_without_semantic_pass),
    (105, "безвредно: ячейка таблицы болей переписана другим предложением", "тишина", "none", None, b30_cell_rewritten_same_shape),

    (101, "безвредно: источник добавлен до последнего наряда", "тишина", "none", None, b28_source_before_last_pass),
    # 2026-09-16: сцепка разделов карты с таблицей
    (102, "строка таблицы не названа в разделах карты", "lint §47", "lint", 47, c102_row_not_named_in_sections),
    (103, "безвредно: строка названа в разделах — тишина", "тишина", "none", None, b29_row_named_in_sections),
    # 2026-09-16: решения владельца по кандидатам — ключ термина не должен разъезжаться с очередью
    (112, "решение по кандидату убрано из очереди", "lint §43", "lint", 43, c112_decision_row_removed),
    # 2026-09-16: заметки со ссылкой без контекста — резюме цели живёт в самой записи
    (113, "у записи убран блок «Что за ссылкой»", "lint §51", "lint", 51, c113_link_block_removed),
    # 2026-09-16: реестры — материал читают, сервисом пользуются
    (114, "строка сервиса возвращена в реестр материалов", "lint §52", "lint", 52, c114_service_row_in_materials),
    # 2026-09-17: наборы полей шапки по типу записи
    (115, "в шапку записи дописано чужое поле", "lint §54", "lint", 54, c115_field_outside_set),
    (116, "строка реестра убрана переездом и не найдена в адресате", "lint §55", "lint", 55, c116_moved_row_lost),
    (117, "запись корпуса удалена без утверждения", "lint §57", "lint", 57, c117_record_without_approval),
    (118, "источник цитаты убран из шапки страницы", "lint §20", "lint", 20, c118_quote_from_undeclared),
    (179, "у правила реестра стёрт род", "lint §59", "lint", 59, c179_rule_kind_missing),
    (180, "документ механизма назвал инструмент экземпляра", "lint §91", "lint", 91, c180_mechanism_doc_names_instance_tool),
    (182, "слово темы экземпляра вернулось в файл механизма", "lint §91", "lint", 91, c182_topic_word_in_mechanism),
    (183, "имя файла механизма несёт слово темы экземпляра", "lint §91", "lint", 91, c183_topic_word_in_filename),
    (119, "маркер правила исчез, а реестр о нём помнит", "lint §59", "lint", 59, c119_rule_marker_gone),
    (120, "артефакт правила не назван в протоколе", "lint §59", "lint", 59, c120_artifact_unreachable),
    (121, "отложенная копия опубликованной страницы", "lint §61", "lint", 61, c121_staged_copy_kept),
    (122, "страница названа кириллицей", "lint §62", "lint", 62, c122_cyrillic_page_name),
    (123, "боль описана прозой и не покрыта охватом", "lint §33", "lint", 33, c123_prose_pain_not_covered),
    (124, "расхождение без страницы и без адреса", "lint §46", "lint", 46, c124_dispute_without_page),
    (125, "заголовок страницы несёт вики-ссылку", "lint §63", "lint", 63, c125_heading_link),
    (126, "источник корпуса остался без карточки", "lint §64", "lint", 64, c126_source_without_card),
    (208, "источник во вложенной папке корпуса без карточки", "lint §64", "lint", 64, c208_nested_source_without_card),
    (127, "строка реестра инструментов без сути", "lint §49", "lint", 49, c127_registry_boilerplate),
    (128, "ссылочная заметка в шапке реестра без строки", "lint §65", "lint", 65, c128_registry_source_without_row),
    (129, "карточка извлечения без шапки", "lint §66", "lint", 66, c129_card_without_frontmatter),
    (130, "скрипт площадки без адреса в документах", "lint §67", "lint", 67, c130_script_without_address),
    (131, "непонятная запись изображения без решения", "lint §68", "lint", 68, c131_unclear_without_decision),
    (132, "раннер останавливается на непустой очереди карточек", "этап 0 раннера", "runner", 0, c132_runner_stops_on_queue),
    (133, "карточка без строки «О чём источник»", "lint §66", "lint", 66, c133_card_without_about),
    (134, "карточка, не названная ни одной страницей", "lint §69", "lint", 69, c134_card_without_address),
    (135, "начало цитаты подменено фразой из необъявленного источника", "lint §58", "lint", 58, c135_quote_head_from_other_source),
    (136, "запись корпуса без обязательного поля шапки", "lint §70", "lint", 70, c136_source_without_required_field),
    (137, "копия источника без мастера в raw/", "lint §71", "lint", 71, c137_mirror_without_master),
    (138, "партия описаний без списка непонятных записей", "lint §68", "lint", 68, c138_batch_without_unclear_list),
    (139, "аудио в сырье без расшифровки", "lint §72", "lint", 72, c139_audio_without_transcript),
    (140, "выдуманный адрес в строке реестра инструментов", "lint §73", "lint", 73, c140_registry_link_invented),
    (141, "в реестре материалов нет колонки ссылки", "lint §74", "lint", 74, c141_materials_link_column),
    (142, "в записи нет адреса, о котором она говорит словами", "lint §75", "lint", 75, c142_record_link_lost),
    (143, "адрес в колонке имени строки реестра", "lint §76", "lint", 76, c143_address_in_name_column),
    (144, "таблица склеена с абзацем", "lint §77", "lint", 77, c144_table_glued_to_paragraph),
    (145, "названный артефакт без адреса (адрес делегирован записи)", "lint §78", "lint", 78, c145_address_delegated_to_record),
    (146, "ремарка о сборке страницы в её тексте", "lint §79", "lint", 79, c146_process_meta_in_page),
    (147, "рассказ о покрытии источника вместо предмета", "lint §79", "lint", 79, c147_source_narration_in_page),
    (148, "сводный дисклеймер о силе доказательства", "lint §79", "lint", 79, c148_evidence_disclaimer),
    (149, "дисклеймер о породе материала («это самоотчёт, а не замер»)", "lint §79", "lint", 79, c149_material_grade_disclaimer),
    (150, "безвредно: шапка со списками в блочной форме (как пишет Obsidian)", "тишина", "none", 0, b34_head_lists_block_form),
    (151, "отчёт о покрытии вместо находки («второго голоса… нет»)", "lint §80", "lint", 80, c151_coverage_report_instead_of_finding),
    (152, "запись-ссылка без адреса в статье", "lint §78", "lint", 78, c152_link_note_without_address),
    (153, "фраза о том, из чего собрана страница («понятие держится…»)", "lint §79", "lint", 79, c153_page_composition_sentence),
    (154, "требование из свода стиля переписано в другой документ", "lint §81", "lint", 81, c154_style_rule_recopied),
    (155, "безвредно: устаревшая ссылка в log.md (история не переписывается)", "тишина", "none", 0, b35_log_stale_reference),
    (156, "мета-ремарка в активном залоге («страница держит роль»)", "lint §79", "lint", 79, c156_page_holds_active),
    (157, "отчёт о покрытии карты («отдельной строки… не имеет»)", "lint §80", "lint", 80, c157_no_separate_row),
    (158, "ремарка о происхождении термина («определение принадлежит вендору и опирается…»)", "lint §79", "lint", 79, c158_concept_holds_active),
    (160, "intent карты структуры без файла (путь переехал)", "lint §29", "lint", 29, c160_intent_without_file),
    (161, "строка индекса стоит не в своей секции", "lint §3", "lint", 3, c161_index_row_wrong_section),
    (162, "итог индекса разошёлся с деревом", "lint §3", "lint", 3, c162_index_total_wrong),
    (163, "служебный документ испорчен записью (строка размножена)", "lint §82", "lint", 82, c163_service_doc_repeated_line),
    (164, "запись журнала потеряла форму контракта", "lint §12", "lint", 12, c164_log_entry_free_form),
    (165, "выход в _staging/audit/ без ссылки", "lint §84", "lint", 84, c165_audit_orphan),
    (166, "README вырос выше потолка", "lint §85", "lint", 85, c166_readme_growth),
    (167, "страница не названа в своде тезисов", "lint §86", "lint", 86, c167_theses_digest_gap),
    (168, "в служебный документ вернулся разбор случая", "lint §87", "lint", 87, c168_service_doc_case),
    (178, "доктор называет спрятанный движок", "доктор окружения", "tool",
     "bare: doctor.py :: ffmpeg (надстройка): winget install Gyan.FFmpeg", c178_doctor_names_hidden_engine),
    (185, "канарейка снята из строки реестра правил", "реестр правил", "tool",
     "bare: rules_check.py :: не названы канарейки, которые его сторожáт", c185_registry_row_canary_dropped),
    (187, "слово домена в корневом входе для агентской сессии", "lint §91", "lint", 91,
     c187_domain_word_in_root_entry),
    (188, "writer GRACE отказывает существующему raw-блоку без --force", "инструмент raw_writer_probe.py", "tool",
     "raw_writer_probe.py :: отказ: существующий raw-блок", c188_local_writer_refuses_existing_block),
    (189, "writer ссылочных блоков отказывает существующему raw-блоку без --force", "инструмент raw_writer_probe.py", "tool",
     "raw_writer_probe.py :: отказ: ссылочный блок", c189_link_summary_refuses_existing_block),
    (190, "writer выжимок отказывает существующему raw-блоку без --force", "инструмент raw_writer_probe.py", "tool",
     "raw_writer_probe.py :: отказ: блок выжимок", c190_extracts_refuses_existing_block),
    (191, "writer ссылок отказывает при расхождении зеркала", "инструмент raw_writer_probe.py", "tool",
     "raw_writer_probe.py :: отказ: зеркало расходится", c191_links_writer_refuses_divergent_mirror),
     (192, "рекурсивная канарейка не занимает существующий raw-путь", "инструмент canary_recursion.py", "tool",
      "canary_recursion.py :: отказ: временный raw-путь уже занят", c192_recursion_refuses_occupied_path),
     (193, "прямой literal пути области в механизме", "lint §92", "lint", 92, c193_area_path_literal),
     (194, "безвредно: путь области построен через toolkit", "тишина", "none", None, c194_area_path_api_benign),
     (195, "из локальной taxonomy убран используемый тег", "lint §5", "lint", 5, c195_schema_local_missing_tag),
     (196, "безвредно: локальная taxonomy перезаписана тем же", "тишина", "none", None, c196_schema_local_rewritten_benign),
     (197, "из локального контракта убран объявленный вид отчёта", "check_contract.py :: неизвестный тип", "tool",
      "check_contract.py _staging/audit/candidates-draft-a.json :: неизвестный тип", c197_contract_local_kind_removed),
     (198, "из постоянной разметки владельца убрана роль материала", "check_selection.py :: постоянная разметка", "tool",
       "check_selection.py --staging _staging/telegram/incoming --selection _staging/ingest-input/selection/canary-selection.json --no-report :: постоянная разметка",
       c198_owner_markup_removed),
     (199, "локальное taxonomy-значение вернулось в SCHEMA.md", "lint §93", "lint", 93, c199_schema_local_value_returned),
     (200, "безвредно: SCHEMA.md перезаписан тем же", "тишина", "none", None, c200_schema_rewritten_benign),
     (201, "хронологическая дата вернулась в SCHEMA.md", "lint §93", "lint", 93, c201_schema_date_returned),
     (202, "локальный путь вернулся в SCHEMA.md", "lint §93", "lint", 93, c202_schema_local_path_returned),
     (203, "слово домена вернулось в SCHEMA.md", "lint §93", "lint", 93, c203_schema_domain_marker_returned),
     (212, "строка ревью ссылается на отсутствующее сообщение", "инструмент promote_approved.py", "tool",
      "promote_approved.py --staging _toolkit/tools/tg-saved/tests/staging :: прогону нельзя доверять",
      c212_promote_missing_message_id),
     (204, "toolkit.area ищет mechanism-скрипт", "lint §92", "lint", 92, c204_area_script_api_literal),
     (205, "безвредно: mechanism-скрипт построен через toolkit.script", "тишина", "none", None, b205_area_script_api_benign),
     (206, "страница слоя знаний вне записей волн", "lint §94", "lint", 94, c206_page_outside_wave),
     (207, "безвредно: запись волны перезаписана тем же", "тишина", "none", None, c207_wave_record_rewritten_benign),
     (209, "begin без объявления входа отказывает", "инструмент wave_runner.py", "tool",
      "wave_runner.py begin :: необъявленный вход", c209_begin_refuses_undeclared),
     (210, "begin --collected без --by отказывает", "инструмент wave_runner.py", "tool",
      "wave_runner.py begin --collected --corpus fixture --inbox raw/fixture :: требует --by",
      c210_begin_collected_requires_by),
     (211, "подмена источника entry роняет закрытие", "инструмент wave_runner.py", "tool",
      "wave_runner.py close --confirm :: изменён после begin", c211_close_fails_on_tampered_entry),
     (213, "рост входа мимо квитанции роняет закрытие", "инструмент wave_runner.py", "tool",
      "wave_runner.py close --confirm :: во входе лишнее", c213_close_fails_on_wave_growth),
     (214, "выгрузка сверх порога в режиме --all останавливается до записи", "инструмент promote_approved.py", "tool",
      "promote_approved.py --staging _toolkit/tools/tg-saved/tests/staging --all :: больше порога",
      c214_promote_all_stops_over_threshold),
     (215, "вердикт пробы читает норму: сужение роняет", "инструмент probe_instance.py", "tool",
      "probe_instance.py --dest _staging/probe-c215 --fixture _toolkit/fixture/sources :: вне нормы пробы",
      c215_probe_verdict_reads_norm),
     (216, "вердикт пробы: поломка освобождения роняет", "инструмент probe_instance.py", "tool",
      "probe_instance.py --dest _staging/probe-c216 --fixture _toolkit/fixture/sources :: шаг с ненулевым кодом",
      c216_probe_verdict_exemption_narrow),
     (217, "меню приёма без флага только показывает выбор", "инструмент tasks.py", "tool",
      "tasks.py telegram :: выбери режим", c217_telegram_menu_without_flag),
     (218, "красный линтер выходит с кодом 1 и печатает итог", "инструмент lint_wiki.py", "tool",
      "lint_wiki.py :: ИТОГО проблем:", c218_lint_exit_code_honest),
     (219, "второй ключ запрета читается проверкой §91", "линт §91", "lint", 91,
      c219_forbidden_key_read),
     (220, "необъявленное чтение вне корня роняет §95", "линт §95", "lint", 95,
      c220_undeclared_outside_read),
     (222, "домовый путь через pathlib роняет §95", "линт §95", "lint", 95,
      c222_undeclared_home_pathlib),
     (223, "домовый путь через переменные роняет §95", "линт §95", "lint", 95,
      c223_undeclared_home_envvars),
     (224, "домовый путь через склейку переменной роняет §95", "линт §95", "lint", 95,
      c224_undeclared_home_envjoin),
     (225, "комментарий не выключает образец §95", "линт §95", "lint", 95,
      c225_undeclared_home_comment_toggle),
     (226, "времянка в строке не покрывает чтение §95", "линт §95", "lint", 95,
      c226_undeclared_home_code_toggle),
     (221, "файл поставки без имени в карте роняет §29", "линт §29", "lint", 29,
      c221_supply_file_unnamed),

     (186, "сторож в реестре — слово не из набора", "реестр правил", "tool",

     "bare: rules_check.py :: не раздел, не файл и не слово из набора", c186_registry_guard_not_from_set),
    (176, "разбор случая в документе вне прежнего списка", "lint §87", "lint", 87, c176_case_in_doc_outside_list),
    (177, "разбор случая в жирной разметке и середине строки", "lint §87", "lint", 87, c177_case_mid_line_bold),
    (184, "разбор случая открыт маркером «Так нашлась»", "lint §87", "lint", 87, c184_case_new_opener),
    (169, "служебный документ вырос выше потолка", "lint §88", "lint", 88, c169_service_doc_growth),
    (170, "объявление домена экземпляра стёрто", "lint §89", "lint", 89, c170_domain_declaration_gone),
    (171, "инструмент ищет свой файл через корень проекта", "lint §90", "lint", 90, c171_toolkit_own_file_via_root),
    (172, "слово домена экземпляра вернулось в файл механизма", "lint §91", "lint", 91, c172_domain_word_in_mechanism),
    (173, "рельсы обратных ссылок без мастера", "рельсы §42", "tool", "source_backlinks.py --write", c173_backlinks_without_corpus),
    (174, "рельсы свода без хранилища", "рельсы §86", "tool", "stance_context.py --write", c174_digest_without_vault),
    (175, "доктор называет отсутствующее", "доктор окружения", "tool", "doctor.py :: ЯДРО НЕ ГОТОВО", c175_doctor_names_missing_vault),
    (227, "слепой страж цитат роняет дежурство", "дежурство цитат", "tool", "check_citations.py :: ПРОПУЩЕН", c227_citation_guard_blind),
    (228, "слепая сверка покрытия роняет самопроверку", "сверка покрытия", "tool", "check_coverage.py --self-test :: ПРОПУЩЕН", c228_coverage_check_blind),
    (229, "слепая проверка панелей роняет самопроверку", "проверка панелей", "tool", "check_panels.py --self-test :: ПРОПУЩЕН", c229_panels_check_blind),
]



def tree_fingerprint(root):
    """Слепок живого дерева: путь → sha256. Нужен, чтобы обещание «мутации только в копии»
    проверялось, а не принималось на слово: 2026-09-18 из живого `raw/` пропал файл, пока шёл
    прогон, и отличить это от чужой правки было нечем.
    """
    import hashlib as _h
    skip_dirs = {".git", ".obsidian", "__pycache__", "node_modules", ".mypy_cache", ".ruff_cache", ".venv", ".venv-asr"}
    skip_rel = {os.path.join("_staging", "telegram")}
    out = {}
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in skip_dirs]
        rel_base = os.path.relpath(base, root)
        if rel_base in skip_rel or any(rel_base.startswith(s + os.sep) for s in skip_rel):
            dirs[:] = []
            continue
        for fn in files:
            if fn.endswith(".pyc"):
                continue
            path = os.path.join(base, fn)
            rel = os.path.relpath(path, root)
            try:
                with open(path, "rb") as f:
                    out[rel] = _h.sha256(f.read()).hexdigest()
            except OSError:
                out[rel] = "?"
    return out


def fingerprint_diff(before, after):
    """Что изменилось между двумя слепками живого дерева."""
    changed = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
    return changed


def snapshot_paths(tmp):
    """Множество путей копии для зачистки: что канарейка создала без регистрации — то убираем."""
    current = set()
    for base, dirs, files in os.walk(tmp):
        for d in dirs:
            current.add(os.path.join(base, d))
        for f in files:
            current.add(os.path.join(base, f))
    return current


def sweep_untracked_paths(tmp, before):
    """Убрать из копии пути, созданные канарейкой без регистрации отката.

    Договор харнесса — «мутации только в копии с восстановлением», но восстановление знает
    лишь то, что канарейка записала в `originals`. Пустые родительские каталоги (`_staging/local`,
    `_staging/telegram` после `rmtree` листьев в c188/c191) и забытые файлы оставались в копии
    и роняли все следующие безвредные через §29: карта видит новый путь. Найдено 2026-09-25
    бисекцией полного прогона. Заметаем сами: сначала штатный откат, потом это.
    """
    current = set()
    for base, dirs, files in os.walk(tmp):
        for d in dirs:
            current.add(os.path.join(base, d))
        for f in files:
            current.add(os.path.join(base, f))
    for path in sorted(current - before, key=lambda p: p.count(os.sep), reverse=True):
        try:
            if os.path.isdir(path) and not os.path.islink(path):
                if not os.listdir(path):
                    os.rmdir(path)
            elif os.path.exists(path):
                os.remove(path)
        except OSError:
            pass


def _self_check():
    """Дубли определений в самом харнессе — дефект, который уже ломал аттестацию.

    Питон берёт последнее определение, поэтому «переписанная» канарейка, оставшаяся в файле дважды,
    молча выполняет старую версию, а новая проверяется не тем, чем задумано. Ловится до прогона.
    """
    src = open(os.path.abspath(__file__), encoding="utf-8").read()
    names = re.findall(r"(?m)^def (\w+)", src)
    twice = sorted(n for n, k in collections.Counter(names).items() if k > 1)
    return ["канарейка определена в файле %d раз(а): %s" % (names.count(n), n) for n in twice]

def main():
    ap = argparse.ArgumentParser(description="Мутационная аттестация чекеров вики.")
    ap.add_argument("--wiki", default=PROJECT_ROOT, help="корень проекта для копирования")
    ap.add_argument("--keep", action="store_true", help="не удалять временный каталог")
    ap.add_argument("--only", default="", help="номера канареек через запятую: 78,145 (выборочный прогон)")
    ap.add_argument("--section", default="", help="разделы линтера через запятую: 78 — все их канарейки")
    args = ap.parse_args()
    src_root = os.path.abspath(args.wiki)
    if not os.path.isdir(src_root):
        sys.exit(f"Нет каталога {src_root}")

    only = {int(x) for x in re.findall(r"\d+", args.only)}
    sections = {int(x) for x in re.findall(r"\d+", args.section)}
    picked = [c for c in CANARIES
              if (not only and not sections)
              or c[0] in only
              or (sections and c[4] in sections)]
    if not picked:
        sys.exit(f"выбор пуст: --only {args.only or '—'} --section {args.section or '—'} ничего не нашли")
    selective = bool(only or sections)
    if selective:
        print(f"ВЫБОРОЧНЫЙ ПРОГОН: {len(picked)} канареек из {len(CANARIES)} "
              f"(--only {args.only or '—'}; --section {args.section or '—'})")
    live_before = tree_fingerprint(src_root)

    tmp_root = tempfile.mkdtemp(prefix="canary-wiki-")
    if not args.keep:
        atexit.register(rmtree, tmp_root)   # уберётся даже при падении/исключении
    tmp = os.path.join(tmp_root, os.path.basename(src_root))
    print(f"Копирую {src_root} → {tmp}")

    def _canary_ignore(path, names):
        skip = {".git", ".obsidian", ".venv", ".venv-asr", "__pycache__", ".mypy_cache", ".ruff_cache"}
        out = [n for n in names if n in skip]
        # Веса моделей — машинное состояние вне git: таскать 200+ МБ в каждую копию нет смысла,
        # а на системном диске может не влезть (найдено 2026-09-25: полный прогон лёг на .onnx).
        # Проверкам веса не нужны: ни одна канарейка ASR не запускает.
        if "_tools" in path.replace("\\", "/").split("/") and os.path.basename(path) == "models":
            out += [n for n in names if n not in out]
        return out

    shutil.copytree(src_root, tmp, ignore=_canary_ignore)

    # Базлайн на нетронутой копии. Живой проект могут править параллельно, поэтому
    # канарейка считается пойманной по ПРИРОСТУ счётчика, а не по абсолютному нулю.
    base_out = run_script(LINT, tmp)
    base_sections = lint_sections(base_out)
    base_msgs = lint_issues(base_out)
    _, base_bad = verify_counts(run_script(VERIFY, tmp))
    base_bad = base_bad or 0
    # Мёртвый линтер не должен выглядеть как «канарейки не поймали»: если линтер не отдал ни одного
    # раздела, аттестация бессмысленна (специфичность при этом остаётся зелёной — тишина, потому что
    # ничего не запускалось). Случай дважды портил числа: 1/49 и 19/53.
    if not base_sections:
        sys.exit("базлайн линтера пуст: линтер не отдал ни одного раздела — прогон аттестации "
                 "недействителен (проверь, что lint_wiki.py запускается и печатает «ИТОГО проблем»)")
    print(f"Базлайн: lint {sorted(base_sections.items())}, verify «требует взгляда» {base_bad}")
    print()

    results = []
    for cid, name, expected, checker, section, apply in picked:
        originals = []
        skipped = False
        before_paths = None
        try:
            before_paths = snapshot_paths(tmp)
            note = apply(tmp, originals)
            if checker == "none":
                # Производные обновляем до чекеров: иначе §29 и §50 говорят о дереве, которого уже нет.
                rebuild_derived(tmp)
                out = run_script(LINT, tmp)
                issues = lint_issues(out)
                _, bad = verify_counts(run_script(VERIFY, tmp))
                fired = sorted(n for n, msgs in issues.items() if msgs - base_msgs.get(n, set()))
                if fired or (bad or 0) > base_bad:
                    caught = False
                    detail = f"ЛОЖНОЕ СРАБАТЫВАНИЕ: разделы {fired}, verify {base_bad} → {bad}"
                else:
                    caught = True
                    detail = "тишина, ложных срабатываний нет"
            elif checker == "lint":
                out = run_script(LINT, tmp)
                issues = lint_issues(out)
                fired = sorted(n for n, msgs in issues.items() if msgs - base_msgs.get(n, set()))
                caught = section in fired
                detail = f"сработали разделы {fired}" if fired else "ни один раздел не сработал"
            elif checker == "tool":
                # Канарейка инструмента: он обязан упасть, когда его вход сломан, и — если названо требование
                # после « :: » — сказать в выводе именно это. Первый токен — скрипт, остальные — его ключи как
                # есть: раньше ключи получали префикс каталога и канарейка могла сработать по чужой причине
                # (замечание 2026-09-22).
                spec, _, need = section.partition(" :: ")
                # `bare:` — запуск в урезанном окружении: `PATH` смотрит в пустой каталог, то есть на машине
                # «нет ничего установленного». Так проверяется, что инструмент называет отсутствующее, а не
                # падает трассировкой (требование раздела «Окружение»).
                env = None
                if spec.startswith("bare:"):
                    spec = spec[len("bare:"):].strip()
                    empty = os.path.join(tmp, "_bare_path")
                    os.makedirs(empty, exist_ok=True)
                    env = dict(os.environ, PATH=empty)
                parts = spec.split()
                # Скрипт может лежать во вложенной папке механизма (tools/tg-saved/): basename тогда
                # указывает в никуда — ищем по дереву. Найдено 2026-09-25 канарейкой №212: путь обрезался,
                # и инструмент «не открывался» по причине харнесса, а не цели.
                target = os.path.join(tmp, "_toolkit", parts[0])
                if not os.path.exists(target):
                    import glob as _nglob
                    found = sorted(_nglob.glob(os.path.join(tmp, "_toolkit", "**", os.path.basename(parts[0])),
                                               recursive=True))
                    target = found[0] if found else os.path.join(tmp, "_toolkit", os.path.basename(parts[0]))
                cmd = [sys.executable, "-X", "utf8", target] + parts[1:] + ["--wiki", tmp]
                try:
                    proc = subprocess.run(cmd, cwd=tmp, capture_output=True, text=True, env=env,
                                          encoding="utf-8", errors="replace", timeout=300, check=False)
                finally:
                    # Пустой каталог bare-окружения — мусор прогона, а не состояние экземпляра: без уборки
                    # он остаётся в копии и роняет все следующие безвредные через §29 (карта видит новый путь).
                    # Найдено 2026-09-25 бисекцией полного прогона: тишина ломалась именно здесь.
                    if env is not None:
                        shutil.rmtree(empty, ignore_errors=True)
                proc = subprocess.run(cmd, cwd=tmp, capture_output=True, text=True, env=env,
                                      encoding="utf-8", errors="replace", timeout=300, check=False)
                out = (proc.stderr or "") + (proc.stdout or "")
                caught = proc.returncode != 0 and (need.strip() in out if need.strip() else True)
                detail = (f"код {proc.returncode}" +
                          (f", требует «{need.strip()}»" if need.strip() else "") + ": " +
                          (out.strip().splitlines() or ["тишина"])[-1][:120])
            elif checker == "runner":
                out = run_runner(tmp)
                row = [l for l in out.splitlines() if re.match(r"^\| %d\." % section, l)]
                caught = bool(row) and "готово" not in row[0]
                detail = (row[0][:150] if row else "раннер не отдал строку этапа %d" % section)
            else:
                _, bad = verify_counts(run_script(VERIFY, tmp))
                caught = (bad or 0) > base_bad
                detail = f"«требует взгляда»: {base_bad} → {bad}"
        except MutationNotApplied as e:
            note, caught, detail = str(e), False, "мутация не применилась — цель устарела"
            skipped = True
        except FileNotFoundError as e:
            # Цель мутации отсутствует в этом экземпляре (страница чужой вики): это та же
            # «неприменимость», что и MutationNotApplied, а не падение прогона. Найдено 2026-09-25
            # полным прогоном на вики без агентских страниц: канарейка №1 роняла всю аттестацию.
            # Текст ошибки — в детали, чтобы опечатка в пути мутации была видна, а не спрятана.
            note, caught, detail = str(e), False, "цель отсутствует в этом экземпляре: %s" % str(e)[:120]
            skipped = True
        finally:
            for path, orig in originals:
                if orig is RESTORE_WRITE:             # менялись права, а не содержимое
                    if os.path.exists(path):
                        os.chmod(path, __import__("stat").S_IWRITE)
                elif orig is RESTORE_MOVE:            # канарейка переносила каталог — возвращаем как был
                    if os.path.exists(path) and not os.path.exists(path[:-len(".canary-off")]):
                        os.rename(path, path[:-len(".canary-off")])
                elif orig is None:                    # канарейка создавала файл или каталог — удаляем его
                    if os.path.isdir(path):
                        rmtree(path)
                    elif os.path.exists(path):
                        os.remove(path)
                else:
                    write(path, orig)
            if before_paths is not None:
                sweep_untracked_paths(tmp, before_paths)
        results.append({"id": cid, "name": name, "expected": expected,
                        "note": note, "caught": caught, "detail": detail, "skipped": skipped})
        print(f"[{'OK ' if caught else 'ПРО'} {cid:>2}] {name} — ожидался {expected} → "
              f"{verdict(r := results[-1])} ({detail})")

    # Порядок — по id, а не по порядку добавления в реестр: консольная таблица, отчёт и JSON
    # обязаны совпадать и не шуметь диффами между прогонами (находка десятого разбора).
    results.sort(key=lambda r: int(r["id"]))
    benign = [r for r in results if r["expected"].startswith("тишина") and not r.get("skipped")]
    # Канарейка, чья мутация не применилась (цель устарела), — не промах и не доказательство:
    # она не входит ни в чувствительность, ни в специфичность, но обязана быть ВИДНА в отчёте.
    # Владелец 2026-09-18: «слишком много канареек накопилось?» — первое, что нужно видеть,
    # это сколько из набора вообще отработало.
    not_applied = [r for r in results if r.get("skipped")]
    sense = [r for r in results if not r["expected"].startswith("тишина") and not r.get("skipped")]
    sense_caught = sum(1 for r in sense if r["caught"])
    spec_n = sum(1 for r in benign if r["caught"])
    total = len(sense)                      # Mutation Score — по 10 канарейкам задания
    score = sense_caught / total if total else 0.0

    print("\n# Канарейка → ожидаемый чекер → результат")
    print("| № | Канарейка | Ожидаемый чекер | Поймана |")
    print("|---|---|---|---|")
    for r in results:
        print(f"| {r['id']} | {r['name']} | {r['expected']} | {verdict(r)} |")
    print(f"\nЧувствительность: {sense_caught}/{total}")
    print(f"Mutation Score: {sense_caught}/{total} = {score:.0%} (порог {MIN_CAUGHT}/{total})")
    # Оценка без знаменателя целиком — число, которое можно процитировать и продать: соседняя сессия
    # разбором 2026-09-26 показала «100% по выжившим» при половине набора в пропуске. Пишем всё.
    print(f"Знаменатель целиком: {len(results)} в наборе, неприменимы {len(not_applied)} "
          f"({', '.join(str(r['id']) for r in not_applied[:12])}"
          f"{' и ещё %d' % (len(not_applied) - 12) if len(not_applied) > 12 else ''})")
    # Формулировка обязана следовать числу, а не наоборот: прогон 09-15 дважды подряд печатал
    # «ложных срабатываний не дали» при 17/19 — то есть отчёт утверждал то, чего в нём же не было.
    if not_applied:
        print(f"Не применилось: {len(not_applied)} — " + "; ".join(
            f"№{r['id']} ({r['detail']}: {r['note'][:70]})" for r in not_applied))
    else:
        print("Не применилось: 0 — весь выбранный набор отработал")
    if spec_n == len(benign):
        print(f"Специфичность: {spec_n}/{len(benign)} — безвредные правки ложных срабатываний не дали")
    else:
        false_ones = ", ".join("№" + str(r["id"]) for r in benign if not r["caught"])
        print(f"Специфичность: {spec_n}/{len(benign)} — ЛОЖНЫЕ на безвредных: {false_ones}")

    today = datetime.date.today().isoformat()
    started_at = datetime.datetime.now().replace(microsecond=0).isoformat()
    # Время в имени полного отчёта: два полных прогона в один день иначе затирают друг друга,
    # и дельта сравнивается сама с собой. Разбор соседней сессии: прогон в 05:03 затёр предшественника
    # из той же даты, и «Знаменатель не изменился» соврало. Выборочные отчёты без времени — они не
    # участвуют ни в дельте, ни в каденции.
    stamp = started_at[11:16].replace(":", "")
    os.makedirs(REPORT_DIR, exist_ok=True)
    # Предыдущий полный прогон читаем ДО записи: иначе дельта и каденция сравнятся с самими собой.
    # Находит соседняя сессия разбором отчёта 2026-09-26: счёт 85→78 вырос сужением знаменателя, и без
    # дельты «вошло/вышло» это выглядит как улучшение чекеров. Имена — чтобы было видно, что именно ушло.
    prev_full, prev_started = None, ""
    if os.path.isdir(REPORT_DIR):
        for fn in sorted(os.listdir(REPORT_DIR)):
            if fn.startswith("canary-results-") and fn.endswith(".json") and "-selective" not in fn:
                try:
                    data = json.load(open(os.path.join(REPORT_DIR, fn), encoding="utf-8"))
                except (OSError, UnicodeError, json.JSONDecodeError):
                    continue
                if isinstance(data, dict) and data.get("selection", {}).get("selective") is False:
                    prev_full, prev_started = data, str(data.get("started_at") or "")
    prev_sense = {str(r.get("id")): str(r.get("name", "")) for r in (prev_full or {}).get("canaries", [])
                  if not str(r.get("expected", "")).startswith("тишина") and r.get("verdict") != "skipped"}
    cur_sense = {str(r["id"]): r["name"] for r in results if not r["expected"].startswith("тишина")
                 and not r.get("skipped")}
    entered = sorted(set(cur_sense) - set(prev_sense), key=int)
    exited = sorted(set(prev_sense) - set(cur_sense), key=int)
    base_note = ("Мутации вносились только в копию корня; живой проект не менялся. Внешняя поверхность "
                 "(каталог навыков) заморожена снимком на время прогона: правки куратора посреди "
                 "прогона не влияют на числа.")
    if base_sections.get(14, 0) or base_bad:
        base_note += (f" Базлайн копии не нулевой (lint §14 = {base_sections.get(14, 0)}, "
                      f"verify = {base_bad}) — счёт по приросту.")
    head = []
    if selective:
        head.append("**ВЫБОРОЧНЫЙ ПРОГОН** — %d канареек из %d (--only %s; --section %s). "
                    "Полная аттестация — на ключевой точке, не реже 100 коммитов."
                    % (len(results), len(CANARIES), args.only or "—", args.section or "—"))
    lines = [f"# Мутационная аттестация чекеров — {today}", ""]
    lines += head + [
             f"{total} канареек + {len(benign)} безвредных. "
             f"**Mutation Score = {sense_caught}/{total} = {score:.0%}** (порог {MIN_CAUGHT}/{total}).", "",
             f"Чувствительность: {sense_caught}/{total}. Специфичность: {spec_n}/{len(benign)}. "
             f"Всего канареек с безвредными: {total + len(benign)}. "
             f"Знаменатель целиком: {len(results)} в наборе, неприменимы {len(not_applied)}.", "",
             base_note, "",
             "| № | Канарейка | Ожидался | Поймана |",
             "|---|---|---|---|"]
    for r in results:
        lines.append(f"| {r['id']} | {r['name']} | {r['expected']} | {verdict(r)} |")
    holes = [r for r in results if not r["caught"]]
    lines += ["", "## Почему", ""]
    if holes:
        for r in holes:
            hint = MISS_HINTS.get(r["id"], "")
            lines.append(f"- №{r['id']} «{r['name']}» (ожидался {r['expected']}) — {r['detail']}. {hint}")
    else:
        lines.append("- все канарейки пойманы.")
    if not_applied:
        lines += ["", "## Не применилось", ""]
        for r in not_applied:
            lines.append(f"- №{r['id']} «{r['name']}» (ожидался {r['expected']}) — {r['detail']}: {r['note'][:140]}")
    hole_ids = {r["id"] for r in holes}
    lines += [f"- {n}" for i, n in MISS_HINTS.items() if i not in hole_ids]
    # Дельта знаменателя с предыдущим полным прогоном: рост счёта сужением знаменателя обязан быть
    # виден с именами, иначе метрика вознаграждает выкидывание канареек. Разбор соседней сессии 2026-09-26.
    # Выборочному прогону дельта неприменима: --only/--section сужают знаменатель намеренно, и сравнение
    # двойки с полным прогоном на 92 печатает «вышли 80» — арифметический шум, а не находка (разбор 2026-09-26).
    if selective:
        print("Дельта знаменателя: неприменима к выборочному прогону "
              "(--only/--section сужают знаменатель намеренно)")
    elif prev_full is not None:
        lines += ["", "## Дельта знаменателя", ""]
        if entered:
            lines.append("Вошли: " + ", ".join("№%s «%s»" % (i, cur_sense[i][:50]) for i in entered))
        if exited:
            lines.append("Вышли: " + ", ".join("№%s «%s»" % (i, prev_sense[i][:50]) for i in exited))
        if not entered and not exited:
            lines.append("Знаменатель не изменился.")
        print("Дельта знаменателя: вошли %s; вышли %s"
              % ([str(i) for i in entered] or "—", [str(i) for i in exited] or "—"))
    # Возможности экземпляра, которых нет: пропуски без этой строки читаются как «утраченные»,
    # хотя их не было никогда. Одна строка списком — требование разбора 2026-09-26.
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import domain as _capdom
        _regs = _capdom.registers(PROJECT_ROOT)
    except (ImportError, OSError, UnicodeError, AttributeError):
        _regs = {}
    _capline = ("Экземпляр не объявил ни одного реестра — проверки домена молчат целиком (§89)."
                if not _regs else "Объявленные реестры экземпляра: " + ", ".join(sorted(_regs)))
    print(_capline)
    lines += ["", "## Возможности экземпляра", "", _capline, ""]
    # матрица покрытия: какие разделы линтера аттестованы канарейками
    import json as _jsonm
    attested, summary_titles = {}, []
    for r in results:
        m = __import__("re").match(r"lint §(\d+)", r["expected"])
        if m:
            attested.setdefault(int(m.group(1)), []).append(r["id"])
    # Заголовки разделов берём из ИСХОДНИКА линтера, а не из сводки прогона: сводка
    # `lint-summary-*.json` лежит в игноре и может отсутствовать (её сносят, чтобы не мусорила
    # в дереве) — тогда матрица печатала «Не аттестовано ничем: 0», то есть тишину вместо
    # измерения, и новый раздел выглядел аттестованным. Сводка осталась запасным путём.
    lint_src = os.path.join(tmp, "_toolkit", "lint_wiki.py")
    if os.path.exists(lint_src):
        block = re.search(r"(?ms)^    sections = \[(.*?)^    \]", read(lint_src))
        if block:
            summary_titles = re.findall(r'\("([^"]+)"', block.group(1))
    if not summary_titles:
        for fn in sorted(os.listdir(REPORT_DIR)) if os.path.isdir(REPORT_DIR) else []:
            if fn.startswith("lint-summary-") and fn.endswith(".json"):
                try:
                    summary_titles = _jsonm.load(open(os.path.join(REPORT_DIR, fn), encoding="utf-8")).get("titles", [])
                except (OSError, UnicodeError, _jsonm.JSONDecodeError, AttributeError, TypeError) as exc:
                    summary_titles = []
                    print("canary: сводка линтера не прочитана (%s): %s" % (fn, exc), file=sys.stderr)
    unattested = []
    for t in summary_titles:
        m = __import__("re").match(r"(\d+)\.", t)
        if m and int(m.group(1)) not in attested:
            unattested.append(t)
    lines += ["", "## Матрица «канарейка → раздел»", "",
              "| раздел линтера | канарейки |", "|---|---|"]
    for sec in sorted(attested):
        title = next((t for t in summary_titles if __import__("re").match(rf"{sec}\.", t)), f"раздел {sec}")
        lines.append(f"| {title} | №{', №'.join(map(str, sorted(attested[sec])))} |")
    lines += ["", f"Аттестовано разделов: {len(attested)}. Не аттестовано ничем: {len(unattested)}"
              + (" — " + "; ".join(unattested) if unattested else "") + ".",
              "Разделы без канарейки считаются непринятыми: правило «новый раздел линтера поставляется вместе со своей канарейкой».",
              f"Отдельно: подмена числа (канарейка №2) ловится только `verify_numbers.py`, поэтому он де-факто аттестован этой канарейкой.", ""]

    lines += ["", "## История аттестации", "",
              "- Первый прогон 2026-09-11: 9 из 10. Провалилась канарейка №6 (пустая ячейка таблицы): раздел 9 линтера был мёртвым — "
              "строки читались через split по литеральной паре символов, поэтому файл попадал в одну строку и таблицы не проверялись вовсе.",
              f"- После починки раздела: чувствительность {sense_caught}/{total}, специфичность {spec_n}/{len(benign)}.",
               "- **Ретро-пометка (2026-09-14): специфичность всех прогонов до этого дня недостоверна.** "
               "Канарейка №40 создавала каталог и не убирала его за собой, поэтому все последующие канарейки "
               "судились по грязной копии, а контрольные прогоны давали ложные срабатывания. Значения «3/3» "
               "(2026-09-11, 2026-09-12) и «4/4» (2026-09-13) замерены на загрязнённой копии и не являются "
               "измерением; достоверные значения — с 2026-09-14.",
               "- Второй дефект харнесса, найденный тогда же: «поймана» считалась по росту счётчика раздела. "
               "Пока внешний автор правил навык прямо во время прогона, раздел 29 честно срабатывал, и виноватой "
               "объявляли контрольную канарейку — специфичность ложно падала до 4/9. Теперь сравниваются "
               "множества сообщений: новым считается только сообщение, которого не было в базлайне.",
              "- Канарейки 11–13 (специфичность) добавлены после замечания аудита: результат 100% без проверки на ложные срабатывания ничего не доказывает.", ""]
    lines += ["", f"Поймано {sense_caught} из {total}: " +
              ("порог пройден." if sense_caught >= MIN_CAUGHT else f"порог {MIN_CAUGHT} НЕ пройден.")]
    # --- обещание «мутации только в копии» проверяется, а не принимается на слово -------------
    live_after = tree_fingerprint(src_root)
    touched = fingerprint_diff(live_before, live_after)
    if touched:
        print("\nЖИВОЕ ДЕРЕВО ИЗМЕНИЛОСЬ ЗА ВРЕМЯ ПРОГОНА — аттестация недостоверна.")
        print("   Это может быть и правка владельца: сигнал не в том, кто изменил, а в том, что "
              "условия прогона перестали быть неизменными. Что именно изменилось:")
        for rel in touched[:40]:
            print("   ", rel)
    else:
        print(f"\nЖивое дерево после прогона: без изменений ({len(live_after)} файлов сверено по sha256).")
    # --- каденция: полный прогон — на ключевой точке, не реже 100 коммитов --------------------
    # Считаем от метки времени предыдущего полного прогона, а не от начала дня: подпись обещает
    # «с последнего прогона», и дата в имени файла это обещание ломала (разбор соседней сессии 2026-09-26).
    last_full, full_commits, full_since = "", None, ""
    if prev_started:
        full_since = prev_started
    elif prev_full:
        full_since = prev_full.get("started_at") or ""
    if full_since:
        cp = subprocess.run(["git", "rev-list", "--count", "--since=" + full_since, "HEAD"],
                            cwd=src_root, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
        full_commits = cp.stdout.strip() if cp.returncode == 0 else None
    cadence = (f"Коммитов с предыдущего полного прогона ({full_since or 'неизвестно когда'}): {full_commits}. "
               "Полный прогон — на ключевой точке, ориентир — 100 коммитов; "
               "после правки чекера достаточно выборочного (`--only`/`--section`).") if full_since else \
              "Предыдущего полного прогона со штампом времени нет — каденцию считать не от чего."
    # машиночитаемый результат для пакета и матрицы покрытия разделов
    import json as _jsonc
    results_json = os.path.join(REPORT_DIR, f"canary-results-{today}-{stamp}.json")
    if selective:
        results_json = os.path.join(REPORT_DIR, f"canary-results-{today}-{stamp}-selective.json")
    with open(results_json, "w", encoding="utf-8", newline="\n") as f:
        # `verdict` вычисляется, чтобы одно имя не значило противоположное: у безвредной канарейки
        # `caught: true` означает «тишина подтверждена», а у мутационной — «дефект пойман». Порядок — по id:
        # машинный артефакт обязан быть детерминированным, иначе диффы между прогонами шумят.
        rows_json = []
        for r in sorted(results, key=lambda x: int(x["id"])):
            benign_row = r["expected"].startswith("тишина")
            rows_json.append({"id": r["id"], "name": r["name"],
                              "kind": ("specificity" if benign_row else "sensitivity"),
                              "expected": r["expected"], "caught": r["caught"],
                              "verdict": ("skipped" if r.get("skipped") else
                                          "silent-as-expected" if (benign_row and r["caught"]) else
                                          "detected" if (not benign_row and r["caught"]) else
                                          "false-positive" if benign_row else "not-detected")})
        _jsonc.dump({"date": today,
                     "started_at": started_at,
                     "harness": "герметичный: скрипт исполняется из копии, атрибуция по новым сообщениям",
                     "sensitivity": {"caught": sense_caught, "total": total},
                     "specificity": {"passed": spec_n, "total": len(benign)},
                     "denominator": {"in_set": len(results), "skipped": len(not_applied)},
                     "delta": {"entered": entered, "exited": exited},
                     "capabilities": _capline,
                     "selection": {"selective": selective, "only": args.only, "section": args.section,
                                   "picked": len(results), "in_set": len(CANARIES)},
                     "not_applied": [{"id": r["id"], "name": r["name"], "why": r["note"][:140]}
                                     for r in not_applied],
                     "live_tree_changed": touched,
                     "commits_since_full": full_commits,
                     "canaries": rows_json},
                    f, ensure_ascii=False, indent=1)
    lines += ["", "## Герметичность прогона", ""]
    if touched:
        lines.append(f"**Живое дерево изменилось за время прогона** — {len(touched)} файлов: "
                     + ", ".join(f"`{x}`" for x in touched[:20]))
    else:
        lines.append(f"Живое дерево не менялось: сверено {len(live_after)} файлов по sha256 "
                     "(каталоги `.git`, `.obsidian`, `_staging/telegram` и `__pycache__` исключены).")

    print(cadence)
    lines += ["", "## Каденция", "", cadence]

    # Выборочный прогон не затирает полный: у него своё имя, и каденцию он не сбивает.
    report_name = (f"canary-report-{today}-{stamp}-selective.md" if selective else f"canary-report-{today}-{stamp}.md")
    report = os.path.join(REPORT_DIR, report_name)
    with open(report, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    print(f"\nОтчёт: {report}")

    if not args.keep:
        atexit.unregister(rmtree)
        rmtree(tmp_root)
    else:
        print(f"Временный каталог сохранён: {tmp}")

    if touched:
        sys.exit(2)                      # аттестация недостоверна: дерево изменилось во время прогона
    # Порог «не меньше восьми» относится к полному прогону задания; выборочный обязан поймать
    # всё, что отобрано, иначе он бессмысленен (и всегда возвращал бы 1 — свойство нашлось на первом же
    # прогоне --only 146).
    need = len(sense) if selective else MIN_CAUGHT
    sys.exit(0 if sense_caught >= need else 1)


import os as _os
import sys as _sys

_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import domain as _domain  # домен экземпляра: имена его страниц и реестров знает только он

if __name__ == "__main__":
    main()
