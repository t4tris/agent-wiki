#!/usr/bin/env python3
"""Мостик между сессиями: что прочитать первым, в каком окне шла работа, что открыто и чего не делать.

    python3 _toolkit/bridge.py generate --wiki .
    python3 _toolkit/bridge.py check    --wiki .   # отстал ли мостик и все ли отказы в нём названы

Зачем: новая сессия не знает, с чего начать, и первой же ошибкой повторяет отвергнутое или переделывает
сделанное. Рукописный
мостик — и он же мог тихо устареть, потому что его никто не проверял. Здесь мостик **генерируется**:
окно работы, состояние, открытые пункты и отказы берутся из git и реестра долга, а рукописным остаётся
только порядок чтения — то, что нельзя вывести из файлов.

Проверка ловит два класса:
  * мостик отстал — после его конца есть коммиты, которых он не описывает. Допускается отставание
    ровно на один коммит: тот, в котором мостик собран (авто-фиксация запускает проверки до коммита);
  * отказ, записанный в реестре долга, не назван в разделе «Чего не делать» — то есть следующая
    сессия может взяться за то, что уже отвергнуто.
"""
import argparse
import ast
import datetime
import json
import os
import re
import subprocess
import sys

import schema
import toolkit


def task_names():
    """Список задач печатается из tasks.py: перенесённый руками список — это второе место для одного
    факта, и он уже расходился дважды (в мостике не было send и lint)."""
    p = toolkit.script("tasks.py")
    try:
        tree = ast.parse(open(p, encoding="utf-8").read())
    except (OSError, SyntaxError, UnicodeError) as exc:
        return "задачи не прочитаны: %s" % exc
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "TASKS":
            return ", ".join(k.value for k in node.value.keys if getattr(k, "value", None))
    return ""

BRIDGE_MD = "bridge.md"
BRIDGE_MD_DISPLAY = os.path.join(toolkit.AREA, "bridge.md")
DEBT = "debt.tsv"
REFUSED_STATUSES = ("отклонён", "принято ограничение", "отложен")
OPEN_STATUSES = ("открыт", "в работе")

# Порядок чтения: рукописно, потому что это маршрут, а не факт из файлов.
READING_ORDER = [
    ("_toolkit/SCHEMA.md", "метод вики, жизненный цикл, проверки и границы делегирования"),
    ("SCHEMA.local.md", "локальный канон экземпляра: домен, taxonomy, значения и пути"),
    ("_staging/bridge.md", "этот файл: окно работы, состояние, открытое, чего не делать"),
    ("_staging/project-map.md", "структура проекта и зачем она такая (проверяется §29)"),
    ("debt.tsv", "единственный реестр открытого и отклонённого (проверяется §28)"),
    ("_toolkit/tasks.py", "единая точка входа в процедуры: {tasks} — цепочка не читается из bash-хука и не "
     "повторяется руками; `send` пишет состав отправки (не отправляет). Список задач печатается здесь из "
     "`tasks.py`, а не переносится сюда руками: перенос уже дважды расходился с кодом."),
    ("CONTRIBUTING.md", "как начать: установка, зависимости, раздел «Окружение», проверка готовности"),
    ("_staging/split-plan.md", "план разделения на механизм и экземпляр: замеры готовности и остаток списком"),
    ("_toolkit/links-playbook.md", "как забирать внешние ссылки и что остаётся человеку"),
    ("_toolkit/images-playbook.md", "как читать текст с изображений и когда мерить, а не читать"),
    ("_staging/audit/README.md", "что отправляется аудитору, что остаётся машинным доказательством"),
    ("_toolkit/tools/tg-saved/README.md", "порядок этапов извлечения материала и каким интерпретатором запускать"),
]

# Ловушки среды и инструмента: то, что стоит часа времени при первой же сессии, если не знать.
# Здесь только название и указатель — полный текст живёт в плейбуках и README, копия была бы вторым местом
# для одного правила, а такие копии у нас уже расходились молча.
ENV_TRAPS = [
    ("Распознавание аудио запускается интерпретатором `.venv-asr`, а не корневым `.venv`",
     "в `.venv-asr` стоят `faster-whisper` и `onnx-asr`; корневой `.venv` оставлен клиенту Telegram",
     "_toolkit/tools/tg-saved/README.md"),
    ("Ключ для забора страниц лежит в переменной `FIRECRAWL_API_KEY` или в локальном `.env` корня проекта",
     "локальные запросы не отличают бот-защиту от пустой страницы; firecrawl ходит со своей стороны",
     "_toolkit/links-playbook.md"),
    ("Telegram и часть внешних адресов доступны только через прокси `http://127.0.0.1:12334`",
     "прямой запрос упирается в блокировку и выглядит как «страницы нет»; по умолчанию прокси уже прописан в клиенте Telegram",
     "_toolkit/tools/tg-saved/README.md"),
    ("Письмо аудитору уходит одной задачей `tasks.py send`, а не руками",
     "письмо-ответ 2026-09-14 ушло вручную и не попало в реестр отправленного: у отправленного числа заморожены "
     "и сверяются со слепком, а не с сегодняшним снапшотом; задачу агент запускает сам — она подписывает письмо "
     "хешем манифеста и записывает состав, отправка остаётся актом владельца",
     "_staging/audit/README.md"),
    ("Письмо даты выбирается по времени правки, а не по имени",
     "`auditor-response-10-…` сортируется раньше `auditor-response-9-…`, и обложкой посылки молча становится "
     "прошлое письмо, а §32 затем справедливо считает письмо цикла потерянным",
     "_staging/audit/README.md"),
    ("Скрипты PowerShell с кириллицей обязаны лежать в UTF-8 с BOM и с CRLF",
     "иначе PowerShell 5.1 падает с невнятной ошибкой про незакрытую скобку, и её ищут в логике, а не в кодировке; консольный вывод держать в ASCII",
     "_toolkit/images-playbook.md"),
    ("В выгрузке Telegram чекбоксы медиа решают, будет ли материал вообще",
     "снятый чекбокс даёт `media_type: voice_message` без файла: тип есть, содержимого нет, лечится только новой выгрузкой",
     "навык telegram-archive-pipeline"),
]

TOOL_TRAPS = [
    ("Не править слепки в `_staging/audit/handover/`",
     "правка слепка уничтожает единственное доказательство того, что именно ушло аудитору; проверяется §31",
     "_toolkit/handover.py"),
    ("Не править источник мастера задним числом — архив забранного из сети неизменяем",
     "копия внутри вики делается из мастера один раз и дальше живёт сама; сверки зеркал и затирания мастером нет (решение владельца 2026-09-15)",
     "_toolkit/sync_sources.py"),
    ("Не переписывать историю git ради вычистки секрета",
     "переписывание обнуляет хеши коммитов, на которых стоят все слепки и утверждения о прошлом; "
     "утечку закрывает ротация ключа, а не filter-repo", "история пакетов закрытых проходов (в архиве, вне поставки)"),
    ("Не запускать цепочку проверок руками по командам из хука",
     "`make` в git-bash нет (нашёлся только в WSL), цепочка живёт в `_toolkit/tasks.py`; "
     "повторение команд руками дважды дало неверный порядок и устаревший пакет", "_toolkit/tasks.py"),
    ("Не верить зелёной аттестации, не проверив, что она вообще запускалась",
     "трижды за сутки она показывала зелёное по неверной причине: сломанный линтер (1/49), внешний дрейф навыков (4/9), "
     "негерметичный прогон (19/53 при зелёной специфичности)", "отчёт аттестации закрытого прохода (в архиве, вне поставки)"),
    ("Не полагаться на число, снятое нерекурсивным обходом слоя источников",
     "этот класс дефекта повторялся трижды (пакет, метрики, раздел линтера) и каждый раз давал правдоподобное занижение; регресс-канарейка стоит в pre-commit.audit и tasks.py check",
     "_toolkit/canary_recursion.py"),
]

SKILL_POINTERS = [
    ("knowledge-base-quality-gates", "механика проверок и пакета отчётности"),
    ("auditor-report", "цикл отчёта внешнему аудитору и ответы на находки"),
    ("telegram-archive-pipeline", "процедура пайплайна извлечения материала"),
    ("llm-wiki", "правила построения страниц и вплетения корпуса"),
    ("image-text-extraction", "чтение текста и координат слов с изображений, когда OCR нужен как доказательство"),
    ("blocked-page-recovery", "лестница забора страниц, отказывающих в ответе"),
]

def sh(*args, cwd):
    p = subprocess.run(list(args), cwd=cwd, capture_output=True, text=True, errors="replace", check=False)
    return p.stdout.strip()

def git_head(root):
    return sh("git", "log", "-1", "--format=%h %ad %s", "--date=short", cwd=root)

def commits_after(root, rev):
    n = sh("git", "rev-list", "--count", f"{rev}..HEAD", cwd=root)
    return int(n) if n.isdigit() else None

_ABS_PATH = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/]{1,2}[^\s()\[\]]+")


def fold_paths(text):
    """Свернуть абсолютные пути машины в `<путь>`.

    Мостик печатает темы коммитов дословно, а поставка должна быть агностиком по месту установки: чужая буква
    диска в служебном файле — то, о чём скачавший не должен ничего узнавать. История не переписывается:
    сворачивается только то, что мостик печатает.
    """
    return _ABS_PATH.sub("<путь>", text)


def commit_list(root, start, limit=40):
    rng = f"{start}..HEAD" if start else "-40"
    out = sh("git", "log", rng, "--oneline", "--no-decorate", cwd=root)
    lines = [fold_paths(l) for l in out.splitlines() if l.strip()]
    return lines[::-1][-limit:]

def window_counts(root, limit=40):
    """Честные числа окна.

    Дефект, который это ловит (сухой прогон новой сессии, второй заход): мостик печатал «Коммитов в окне: 40»,
    потому что выводил длину УСЕЧЁННОЙ выдачи, а в диапазоне фактически было 83 коммита. Ложное число в первом
    же файле, который читает новая сессия, — худший сорт дефекта: он выглядит как факт.
    """
    n = sh("git", "rev-list", "--count", "HEAD", cwd=root)
    total = int(n) if n.isdigit() else 0
    shown = min(limit, total) if total else 0
    return shown, total

def read_debt(root):
    p = os.path.join(root, DEBT)
    if not os.path.exists(p):
        return []
    rows = []
    lines = [l for l in open(p, encoding="utf-8").read().splitlines() if l.strip()]
    cols = lines[0].split("\t") if lines else []
    for line in lines[1:]:
        parts = line.split("\t")
        if len(parts) >= len(cols):
            rows.append(dict(zip(cols, parts)))
    return rows

def snapshot_line(root):
    ad = toolkit.area(root, "audit")
    # Каталог аудита создают проходы, а не установка: в чистом экземпляре его ещё нет (проба на чужом корпусе,
    # 2026-09-22 — механизм падал здесь с FileNotFoundError). Отсутствие — это «снимка нет», а не обвал.
    files = sorted(f for f in (os.listdir(ad) if os.path.isdir(ad) else [])
                   if re.match(r"wiki-figures-\d{4}-\d{2}-\d{2}\.json$", f))
    if not files:
        return None, None
    d = json.load(open(os.path.join(ad, files[-1]), encoding="utf-8"))
    return files[-1], d.get("snapshot_line", "").strip()

def previous_end(root):
    p = toolkit.area(root, BRIDGE_MD)
    if not os.path.exists(p):
        return None
    m = re.search(r"окно: `([0-9a-f]{6,})\.\.", open(p, encoding="utf-8").read())
    return m.group(1) if m else None

def resolve_start(root, start):
    """Переводит начало окна картой переписи, если такого коммита больше нет.

    Дефект, который это ловит (сухой прогон новой сессии, 2026-09-14): после чистки истории сохранённое
    в мостике начало окна `4201d22` перестало существовать, `git log 4201d22..HEAD` вернул пусто, и мостик
    напечатал «Коммитов в окне: 0» — то есть окно молча исчезло, а проверка §30 считала это нормой.
    """
    if not start:
        return start, ""
    ok = sh("git", "rev-parse", "--verify", "--quiet", f"{start}^{{commit}}", cwd=root).strip()
    if ok:
        return start, ""
    import glob as _glob
    maps = sorted(_glob.glob(toolkit.area(root, "audit", "history-rewrite-*.tsv")))
    for f in maps[-1:]:
        for line in open(f, encoding="utf-8").read().splitlines()[1:]:
            parts = line.split("\t")
            if len(parts) >= 2 and parts[0].startswith(start[:10]):
                return parts[1], (f"начало окна переведено картой переписи: `{start}` → `{parts[1]}`")
    back = sh("git", "rev-list", "--max-count=41", "HEAD", cwd=root).splitlines()
    fallback = sh("git", "rev-parse", "--short", back[-1], cwd=root).strip() if len(back) > 40 else ""
    return fallback, (f"начала окна `{start}` в истории нет (перепись) — взято ближайшее доступное `{fallback}`")

def reading_order(root):
    order = list(READING_ORDER)
    local = schema.path(root)
    if local:
        relative = os.path.relpath(local, root).replace(os.sep, "/")
        order[1] = (relative, order[1][1])
    return order


def build(root):
    # Окно — это НЕ «работа сессии»: мостик пересобирается на каждом коммите, и любая самоссылка на прошлое
    # начало окна копила бы диапазон (к 09-14 в нём стало 83 коммита при печати «40»). Показываем последние
    # сорок коммитов и говорим об этом прямо; за прошлое отвечают журнал событий и слепки.
    start, start_note = "", ""
    total = window_counts(root)[1]
    _cl = [fold_paths(l) for l in sh("git", "log", "-40", "--oneline", "--no-decorate", cwd=root).splitlines()]
    shown = len(_cl)
    if _cl:
        start = _cl[-1].split()[0]          # самый старый в показанном диапазоне: хеш реальный и разрешимый
    if False:
        # Первый мостик: окно не с начала времён, а ровно то, что показывает список коммитов.
        back = sh("git", "rev-list", "--max-count=41", "HEAD", cwd=root).splitlines()
        start = (sh("git", "rev-parse", "--short", back[-1], cwd=root) if len(back) > 40 else
                 sh("git", "rev-list", "--max-parents=0", "HEAD", cwd=root)[:8])
    head = sh("git", "rev-parse", "--short", "HEAD", cwd=root)
    rows = read_debt(root)
    figs_name, figs_line = snapshot_line(root)
    return {
        "date": datetime.date.today().isoformat(),
        "head_short": head,
        "head_line": git_head(root),
        "start": start,
        "start_note": start_note,
        "shown": shown,
        "total": total,
        # Список и счёт берём из ОДНОГО источника (`git log -40`), иначе счёт и список расходятся на один
        # коммит: диапазон `oldest..HEAD` исключает сам oldest (поймано проверкой §30).
        "commits": [fold_paths(l) for l in sh("git", "log", "-40", "--oneline", "--no-decorate", cwd=root).splitlines()][::-1],
        "debt": rows,
        "figures_name": figs_name,
        "figures_line": figs_line,
    }

def render(root, info):
    L = []
    L.append("# Мостик между сессиями")
    L.append("")
    L.append(f"Снято: {info['date']} · генератор `_toolkit/bridge.py` · окно: `{info['start']}..{info['head_short']}` "
             f"· проверяется линтером §30")
    L.append("")
    L.append("> Это мостик: прочитай его первым, дальше — по порядку ниже. Всё, что здесь про окно, состояние "
             "и открытые пункты, собрано машиной; руками написан только маршрут чтения.")
    L.append("")
    L.append("## Порядок чтения")
    L.append("")
    # В списке чтения остаётся только то, что в этой копии есть: реестр долга — инструмент разработчика
    # механизма и в поставку не входит, у постороннего его нет, и называть нечего (решение владельца 2026-09-22).
    order = [(p, w) for p, w in reading_order(root) if os.path.exists(os.path.join(root, p))]
    for i, (path, why) in enumerate(order, 1):
        why_txt = why.format(tasks=task_names()) if "{tasks}" in why else why
        L.append(f"{i}. `{path}` — {why_txt}")
    L.append("")
    L.append("## Новая сессия: что прочитать и что не читать")
    L.append("")
    L.append("**Режим работы (с 2026-09-15): без аудита.** Обвязка подготовки к аудиту — `tasks.py check/package/send`, "
             "линтер, карта, мостик, числа, канарейки — запускается только по слову владельца. `pre-commit` отключён, "
             "коммиты локальные, в удалённую копию — только по явному запросу (`_toolkit/offsite.py push`, по явному слову владельца). "
             "Строгая цепочка сохранена в `_toolkit/hooks/pre-commit.audit`. Поэтому «линтер красный» в этом режиме — "
             "не повод останавливаться: замечания копятся и разбираются пачкой в конце.")
    required = [("`_toolkit/SCHEMA.md`", "метод")]
    local = schema.path(root)
    if local:
        local_rel = os.path.relpath(local, root).replace(os.sep, "/")
        required.append((f"`{local_rel}`", "локальный канон"))
    required.extend([
        ("этот мостик", "окно и состояние"),
        ("`_staging/project-map.md`", "структура и зачем она такая"),
        ("`debt.tsv`", "открытое и отклонённое — единственный список"),
        ("`_toolkit/tasks.py`", "единая точка входа"),
    ])
    L.append(f"Обязательный минимум — {len(required)} файлов: "
             + ", ".join(f"{path} ({why})" for path, why in required) + ".")
    L.append("")
    L.append("Под задачу: в волне инжеста — `_toolkit/ingest-wave.md` (порядок волны и что она не делает); "

             "правило атрибуции (владелец ссылкой-алиасом) — `_toolkit/SCHEMA.md`, правила 5–8. "
             "В режиме аудита дополнительно: наряд на письмо (в архиве закрытого прохода, вне поставки) (вердикты, обязательные блоки, "
             "запрещённые формулировки) и `_staging/audit/README.md`.")
    L.append("")
    L.append("**Не читать:** историю сессий и переписку (всё решённое обязано лежать в артефактах, и это "
             "проверяется), письма цикла 1–9 (исторические документы), рабочие выгрузки и медиа Telegram "
             "(вне версии и не нужны для решений), журналы прогонов, кеши, машинные приложения пакета. "
             "Причина простая: шумный контекст дороже чтения пяти файлов и он же порождает повторную работу.")
    L.append("")
    L.append("**Три вопроса, на которые сессия отвечает до любых правок:** (1) сколько открытых пунктов и кто "
              "владелец у каждого — `debt.tsv`; (2) что запускать следующим и с каким входным условием — "
             "`ingest-wave.md` в волне, `tasks.py` вне неё; (3) какие числа письма берутся из артефактов и "
             "какие формулировки запрещены — наряд на письмо (в архиве закрытого прохода) (только когда пишется письмо).")
    L.append("")
    L.append("## Окно работы")
    L.append("")
    L.append(f"Начало окна: `{info['start']}`. Конец: `{info['head_short']}` ({info['head_line']}).")
    if info.get("start_note"):
        L.append("")
        L.append(f"> {info['start_note']}")
    L.append("")
    L.append(f"Коммитов в окне: {info['shown']} — это последние {info['shown']} из {info['total']} в ветке "
             f"(окно не «работа сессии»: за прошлое отвечают журнал событий и слепки `handover/`).")
    L.append("")
    for c in info["commits"]:
        L.append(f"- `{c}`")
    L.append("")
    L.append("Что было ДО окна — в журнале событий и в слепках `_staging/audit/handover/`: о прошлом утверждают только "
             "они, реестр слепков обязателен (линтер §27).")
    L.append("")
    L.append("## Состояние сейчас")
    L.append("")
    if info["figures_line"]:
        L.append(f"{info['figures_line']}")
        L.append("")
        can_files = sorted(f for f in os.listdir(toolkit.area(root, "audit"))
                           if re.match(r"canary-results-\d{4}-\d{2}-\d{2}\.json$", f))
        if can_files:
            try:
                _c = json.load(open(toolkit.area(root, "audit", can_files[-1]), encoding="utf-8"))
                _s, _sp = _c.get("sensitivity", {}), _c.get("specificity", {})
                L.append("")
                L.append(f"Аттестация: канареек {len(_c.get('canaries', []))}, чувствительность "
                         f"{_s.get('caught', '—')}/{_s.get('total', '—')}, специфичность "
                         f"{_sp.get('passed', '—')}/{_sp.get('total', '—')} — источник "
                         f"`_staging/audit/{can_files[-1]}` (единственное место для этого числа: в снапшоте его нет).")
            except (OSError, UnicodeError, json.JSONDecodeError, AttributeError, TypeError) as exc:
                L.append("Аттестация канареек: не прочитана (%s): %s" % (can_files[-1], exc))
        L.append(f"Источник: `_staging/audit/{info['figures_name']}` (один прогон генератора; других чисел в этом "
                 "разделе быть не может — иначе линтер §21 поймает расхождение).")
    else:
        L.append("Сводки чисел нет: собери пакет (`collect_report.py` / `merge_package.py`).")
    L.append("")
    # Внешняя копия: новая сессия обязана знать, что она есть, где и как её обновить (раздел 36 линтера
    # валит коммит, если подтверждённая копия старше семи дней).
    off_log = toolkit.area(root, "audit", "offsite-log.tsv")
    if os.path.exists(off_log):
        rows = [l.split("\t") for l in open(off_log, encoding="utf-8").read().splitlines() if l.strip()]
        ok_rows = [r for r in rows[1:] if len(r) >= 7 and r[6] == "ok"]
        if ok_rows:
            last = ok_rows[-1]
            L.append(f"Внешняя копия: `{last[1]}` {last[2]} @ `{last[3]}` ({last[0]}, проверена обратным чтением). "
                     f"Обновить и подтвердить: `python3 _toolkit/tasks.py offsite`.")
        else:
            L.append("Внешняя копия не подтверждалась ни разу: `python3 _toolkit/tasks.py offsite`.")
        L.append("")
    L.append("## Открытое")
    L.append("")
    for status in OPEN_STATUSES:
        items = [r for r in info["debt"] if r["статус"] == status]
        if not items:
            continue
        L.append(f"**{status}** ({len(items)}):")
        for r in items:
            L.append(f"- `{r['id']}` — {r['пункт']} · владелец: {r['владелец']}")
        L.append("")
    if not any(r["статус"] in OPEN_STATUSES for r in info["debt"]):
        L.append("Открытых пунктов нет.")
        L.append("")
    L.append("Полный реестр со статусами и доказательствами — `debt.tsv`.")
    L.append("")
    L.append("## Ловушки: среда и инструмент")
    L.append("")
    L.append("Название и указатель; полный текст — по ссылке, чтобы не заводить второе место для одного правила.")
    L.append("")
    L.append("**Среда**")
    L.append("")
    for trap, why, where in ENV_TRAPS:
        L.append(f"- {trap}. *Почему важно:* {why}. Подробнее: `{where}`.")
    L.append("")
    L.append("**Инструмент**")
    L.append("")
    for trap, why, where in TOOL_TRAPS:
        L.append(f"- {trap}. *Почему важно:* {why}. Подробнее: `{where}`.")
    L.append("")
    L.append("## Чего не делать")
    L.append("")
    L.append("Здесь только отвергнутое и принятое как ограничение: иначе следующая сессия возьмётся за то, "
             "что уже решено не делать, и цикл повторится.")
    L.append("")
    for r in info["debt"]:
        if r["статус"] in REFUSED_STATUSES:
            L.append(f"- **{r['пункт']}** ({r['статус']}). {r['примечание'].rstrip('.')}. Источник: `debt.tsv` (`{r['id']}`).")
    L.append("")
    L.append("## Где искать процедуры")
    L.append("")
    L.append("Навыки живут вне дерева проекта (см. `_staging/project-map.md`, раздел «Знания вне дерева»):")
    L.append("")
    for name, why in SKILL_POINTERS:
        L.append(f"- `{name}` — {why}")
    L.append("")
    return "\n".join(L)

def cmd_generate(root):
    info = build(root)
    p = toolkit.area(root, BRIDGE_MD)
    open(p, "w", encoding="utf-8", newline="\n").write(render(root, info))
    print(f"мостик: {BRIDGE_MD_DISPLAY} | окно {info['start']}..{info['head_short']} | коммитов {len(info['commits'])} | "
          f"открыто {sum(1 for r in info['debt'] if r['статус'] in OPEN_STATUSES)} | "
          f"отказов {sum(1 for r in info['debt'] if r['статус'] in REFUSED_STATUSES)}")
    return 0

def cmd_check(root):
    issues = []
    p = toolkit.area(root, BRIDGE_MD)
    if not os.path.exists(p):
        print("- нет _staging/bridge.md: новая сессия не знает, с чего начать")
        return 1
    text = open(p, encoding="utf-8").read()
    m = re.search(r"окно: `([0-9a-f]{6,})\.\.([0-9a-f]{6,})`", text)
    has_git = os.path.isdir(os.path.join(root, ".git"))
    has_history = has_git and bool(sh("git", "rev-parse", "--verify", "--quiet", "HEAD", cwd=root))
    if not m:
        if not has_history and re.search(r"окно: `[^`]*`", text) and "## Окно работы" in text:
            pass          # копия без истории: пустое окно — следствие отсутствия git, а не недосмотр
        else:
            issues.append("мостик без записанного окна: пересобери (bridge.py generate)")
    elif not has_history:
        # Копия без истории (например, канареечный прогон): проверить окно нечем — молчим, а не выдумываем дефект.
        pass
    else:
        end = m.group(2)
        n = commits_after(root, end)
        if n is None:
            issues.append(f"окно мостика ссылается на неизвестный коммит {end}: пересобери")
        elif n > 1:
            issues.append(f"мостик отстал на {n - 1} коммитов после {end}: пересобери "
                          f"(bridge.py generate), иначе окно работы описано неверно")
    if "## Ловушки: среда и инструмент" not in text or not ENV_TRAPS or not TOOL_TRAPS:
        issues.append("в мостике нет раздела ловушек среды и инструмента (или он опустошён): "
                      "новая сессия потратит час на то, что уже известно")
    for r in read_debt(root):
        if r["статус"] in REFUSED_STATUSES and r["пункт"] not in text:
            issues.append(f"отказ «{r['пункт']}» ({r['id']}) не назван в разделе «Чего не делать»: "
                          f"следующая сессия возьмётся за отвергнутое")
    for i in issues:
        print("- " + i)
    return 1 if issues else 0

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", nargs="?", default="generate", choices=["generate", "check"])
    ap.add_argument("--wiki", default=os.environ.get("WIKI_PATH", "."))
    a = ap.parse_args()
    root = os.path.abspath(a.wiki)
    return cmd_generate(root) if a.cmd == "generate" else cmd_check(root)

if __name__ == "__main__":
    sys.exit(main())
