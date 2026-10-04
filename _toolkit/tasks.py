#!/usr/bin/env python3
"""Единая точка входа в процедуры проекта: имя задачи, шаги, порядок.

Зачем этот файл. Цепочка проверок жила только внутри bash-хука `pre-commit`: её нельзя было
ни запустить осознанно, ни запустить частично, ни увидеть списком. Порядок в ней смысловой
(сводки обязаны быть свежими до сборки пакета), и одна ошибка порядка уже дала пакет
с устаревшими числами. Плюс цепочку приходилось повторять руками: за один день сборка пакета
запускалась шесть раз, заморозка слепка — семь, и дважды в неверном порядке.

Здесь только ЗАПУСК: имена, шаги, порядок. Правила и «почему» остаются в `SCHEMA.md`, плейбуках
и навыках — в этот файл они не переезжают, иначе появится третье место для одного правила.

Запуск:
    python3 _toolkit/tasks.py list            — что есть и из чего состоит
    python3 _toolkit/tasks.py check           — то же, что делает pre-commit.audit
    python3 _toolkit/tasks.py package         — карта, мостик, реестры, числа, линтер, пакет
    python3 _toolkit/tasks.py report          — package + заморозка отправляемой версии
    python3 _toolkit/tasks.py lint            — линтер и автопроверка чисел (гейты)
    python3 _toolkit/tasks.py python-checks   — обязательные проверки Python и информационные отчёты Ruff
    python3 _toolkit/tasks.py canary          — аттестация канареек
    python3 _toolkit/tasks.py map             — карта структуры
    python3 _toolkit/tasks.py bridge          — мостик между сессиями
    python3 _toolkit/tasks.py candidates      — кандидаты в страницы и очередь решений
    python3 _toolkit/tasks.py candidates-review — HTML-лист решений по кандидатам (черновик + галочки)
    python3 _toolkit/tasks.py intents         — замыслы владельца и решения по ним
    python3 _toolkit/tasks.py intents-review  — HTML-лист проверки решений по замыслам
    python3 _toolkit/tasks.py dashboard       — дашборд контроля проекта (контент + мета)
    python3 _toolkit/tasks.py candidates-status — рост упоминаний по кандидатам (что пересмотреть)
    python3 _toolkit/tasks.py wave-map        — карта волны инжеста с живым состоянием страниц
    python3 _toolkit/tasks.py offsite         — отправка удалённой копии с обратной проверкой
    python3 _toolkit/tasks.py status          — состояние: числа, долг, удалённая копия
    python3 _toolkit/tasks.py wipe --confirm  — вычистить экземпляр до состояния поставки
    python3 _toolkit/tasks.py telegram --all [--force] | --review — приём телеграм-выгрузки: всё или с отбором
    python3 _toolkit/tasks.py unlink <слаг>   — следы перед удалением страницы (только чтение)
"""
import argparse
import datetime
import json
import os
import re
import shutil
import subprocess
import sys

import toolkit

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AUDIT = os.path.join(toolkit.AREA, "audit")
PACKAGE = os.path.join(AUDIT, "audit-package-{d}.md")
# то, что пересобирается по ходу и обязано попасть в тот же коммит
ARTIFACTS = [AUDIT, "_staging/cards-registry.json", "_staging/claims-registry.tsv",
             "_staging/project-map.md", "_staging/project-map.html", "_staging/bridge.md",
             "_staging/audit/offsite-log.tsv", "_staging/audit/sent-artifacts.tsv"]


def py(script, *args, quiet=True, check=True):
    """Шаг цепочки: один скрипт проекта с общим ключом --wiki."""
    # `--wiki .` идёт последним: подкоманды вроде `extracts.py check` не принимают ключ перед собой.
    cmd = [sys.executable, "-X", "utf8", toolkit.script(script), *args, "--wiki", "."]
    r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, errors="replace", check=False)
    if not quiet and r.stdout.strip():
        print(r.stdout.rstrip())                  # задача, которую запускают ради вывода, обязана его показать
    if check and r.returncode != 0:
        print(f"  сбой шага {script}:")
        print("\n".join((r.stdout + r.stderr).strip().splitlines()[-8:]))
        sys.exit(1)
    return r


def gate_lint():
    """Линтер обязан быть чист: это ворота коммита, а не украшение."""
    r = py("lint_wiki.py", quiet=False, check=False)
    total = None
    for line in r.stdout.splitlines():
        if line.startswith("ИТОГО проблем:"):
            total = line.split(":", 1)[1].strip()
    if total != "0":
        print("\n".join(r.stdout.splitlines()[-30:]))
        sys.exit(f"линтер нашёл проблемы ({total}) — шаг остановлен")


def gate_numbers():
    r = py("verify_numbers.py", quiet=False, check=False)
    tail = (r.stdout or r.stderr).strip().splitlines()[-1:]
    if tail and "требует взгляда 0" not in tail[0]:
        sys.exit(f"автопроверка чисел нашла спорные: {tail[0]}")


def step_python_checks():
    python = os.environ.get("WIKI_PYTHON") or sys.executable
    checks = [
        (["-m", "ruff", "check", "."], "ruff"),
        (["-m", "mypy", os.path.join("_toolkit", "tools", "tg-saved")], "mypy"),
    ]
    for args, label in checks:
        result = subprocess.run([python, *args], cwd=ROOT, capture_output=True,
                                text=True, encoding="utf-8", errors="replace", check=False)
        if result.stdout.strip():
            print(result.stdout.rstrip())
        if result.stderr.strip():
            print(result.stderr.rstrip(), file=sys.stderr)
        if result.returncode:
            sys.exit(f"{label} вернул код {result.returncode}")

    advisories = [
        (["-m", "ruff", "check", "--select", "UP031", "--output-format", "json", "."], "Ruff UP031", "находок"),
        (["-m", "ruff", "format", "--check", "--output-format", "json", "."], "Ruff format", "файлов для форматирования"),
    ]
    for args, label, unit in advisories:
        result = subprocess.run([python, *args], cwd=ROOT, capture_output=True,
                                text=True, encoding="utf-8", errors="replace", check=False)
        try:
            diagnostics = json.loads(result.stdout)
        except (TypeError, ValueError):
            diagnostics = None
        if isinstance(diagnostics, list):
            print(f"{label}: {len(diagnostics)} {unit}; информационный отчёт, коммит не блокируется.")
        else:
            print(f"{label}: информационный запуск не вернул список находок.")
            if result.stdout.strip():
                print(result.stdout.rstrip())
            if result.stderr.strip():
                print(result.stderr.rstrip(), file=sys.stderr)


def step_recursion():
    py("canary_recursion.py")


def step_autosnapshot():
    """Автослепок: если за сегодня в реестре нет строки — снять слепок с текущего пакета."""
    today = datetime.date.today().isoformat()
    ledger = _domain.register_path(ROOT, "handover")     # реестр передач объявляет экземпляр
    if ledger and os.path.exists(ledger) and re.search(rf"(?m)^{today}\b", open(ledger, encoding="utf-8").read()):
        return
    py("merge_package.py", check=False)
    py("handover.py", "archive", "--package", PACKAGE.format(d=today), "--note", "автослепок из pre-commit.audit",
       check=False)


def step_derivatives():
    py("project_map.py", "generate")
    py("bridge.py", "generate")
    py("registries.py")
    # реестр источников: строки корпуса GRACE и счётчики в шапке — считает отдельный скрипт
    py("update_source_registry.py")    # без --write: пишет сразу
    py("wiki_metrics.py", "--write")   # метрики здоровья вики: покрытие, свежесть, противоречия
    py("topic_map.py", "--write")      # тематическая карта: темы, страницы и термины без страницы
    # генераторы пишут таблицы без выравнивания, а канон требует выровненных (иначе Obsidian
    # при просмотре правит их сам) — выравниваем в конце шага
    py("align_tables.py", "--write")
    # раздел «Где использован» в копии источника собирается из страниц: его нельзя править руками,
    # его надо пересобирать всякий раз, когда менялись sources: страниц или их заголовки (линтер §42)
    py("source_backlinks.py", "--write")


def step_numbers_write():
    py("verify_numbers.py", "--write")


def step_package():
    py("audit_report.py")
    py("merge_package.py", quiet=False)
    subprocess.run(["git", "add", "-A", *ARTIFACTS], cwd=ROOT, capture_output=True, check=True)


# Поверхность экземпляра: всё, что wipe вправе тронуть. Фиксированный allowlist: ключом можно
# передать только --confirm, пути из командной строки не читаются вовсе — удалить чужое нельзя.
WIPE_TARGETS = ("wiki", "raw", "_staging", "SCHEMA.local.md", "contract-v1.local.json")


def step_wipe(*rest):
    """Вычистить экземпляр до состояния поставки: дальше снова ingest, start, probe.

    Без --confirm ничего не трогает, а только показывает, что уйдёт. История git при этом остаётся:
    чистый старт без прошлого — это новый клон или новый git init, а не wipe.
    """
    found = []
    for t in WIPE_TARGETS:
        p = os.path.join(ROOT, t)
        if not os.path.exists(p):
            continue
        n = sum(len(files) for _, _, files in os.walk(p)) if os.path.isdir(p) else 1
        found.append((t, n))
    if "--confirm" not in rest:
        if not found:
            print("экземпляра нет: вычищать нечего (поставка чиста)")
            return
        print("уйдёт без --confirm никуда:")
        for t, n in found:
            print(f"  {t} ({n} файлов)")
        print("повтори с --confirm: python3 _toolkit/tasks.py wipe --confirm")
        sys.exit(1)
    for t, _ in found:
        p = os.path.join(ROOT, t)
        if os.path.isdir(p) and not os.path.islink(p):
            shutil.rmtree(p, ignore_errors=True)
        else:
            os.remove(p)
    print("экземпляр вычищен (%d файлов): дальше ingest, start, probe" % sum(n for _, n in found))


def step_telegram(*rest):
    """Меню приёма телеграм-выгрузки: всё целиком или с процедурой отбора."""
    staging = os.environ.get("WIKI_TG_STAGING", os.path.join(toolkit.AREA, "telegram"))
    promote = "tools/tg-saved/promote_approved.py"
    if "--all" in rest:
        extra = ["--force"] if "--force" in rest else []
        py(promote, "--staging", staging, "--all", *extra, quiet=False)
    elif "--review" in rest:
        py(promote, "--staging", staging, quiet=False)
    else:
        print("приём телеграм-выгрузки: выбери режим одной командой")
        print("  python3 _toolkit/tasks.py telegram --all     — весь канал целиком, без review.csv; "
              "дальше волна через begin --collected")
        print("  python3 _toolkit/tasks.py telegram --all --force — то же после остановки по порогу "
              "(больше 1000 сообщений к разбору: три числа, ничего не записано)")
        print("  python3 _toolkit/tasks.py telegram --review  — разбор строкой review.csv; "
              "дальше волна через begin --selection")
        print(f"накопитель: {staging} (переменная WIKI_TG_STAGING)")
        print("код 2 здесь — «режим не выбран»; код 2 у promote_approved — «прогону нельзя доверять» "
              "(строки ссылаются в никуда); код 3 — «выгрузка больше порога, нужно второе слово»")
        sys.exit(2)


def step_exam_nag():
    """Напоминание, а не запрет: если контентные страницы в коммите, а прогона за сегодня нет."""
    staged = subprocess.run(["git", "diff", "--cached", "--name-only"], cwd=ROOT,
                            capture_output=True, text=True, check=True).stdout
    pages = [l for l in staged.splitlines() if re.match(r"^wiki/(concepts|comparisons|queries|entities)/.*\.md$", l)]
    if not pages:
        return
    today = datetime.date.today().isoformat()
    hist = os.path.join(ROOT, AUDIT, "exam-history.tsv")
    last = ""
    if os.path.exists(hist):
        rows = [l for l in open(hist, encoding="utf-8").read().splitlines() if l.strip()]
        if len(rows) > 1:
            last = rows[-1].split("\t")[0]
    if last != today:
        print(f"экзамен: контентные страницы менялись, прогона за {today} нет — "
              f"`tasks.py exam-prepare`, ответы читателей, затем `exam.py score`")


TASKS = {
    "recursion": (step_recursion,),
    "map": (lambda: py("project_map.py", "generate"),),
    "bridge": (lambda: py("bridge.py", "generate"),),
    # Закрытие волны одной командой: то, что раньше делалось «не забыть» — порядок зафиксирован.
    # Входных данных у волны нет: каждая команда сама находит своё (выжимки, план тонких записей, дерево).
    # Прогон волны целиком: очередь работ и приёмка каждого этапа (см. _toolkit/wave_runner.py).
    "page-doubt": (lambda: py("page_doubt.py", "--wiki", ".", "--write-tsv", "--apply-auto", quiet=False),),
    "wave": (lambda: py("wave_runner.py", "status", quiet=False, check=False),),
    "wave-close": (lambda: py("extracts.py", "check", quiet=False),
                   # Отработавшее уезжает в архив на закрытии волны: кандидатов называет §84, переносит
                   # archive_pass.py. Так перечень «актуальное против пережитка» не решается заново руками
                   # каждый раз, и критерий у проверки и у переноса один (решение владельца 2026-09-22).
                   lambda: py("archive_pass.py", "--write", quiet=False),
                   lambda: py("thin_notes.py", quiet=False, check=False),
                   lambda: py("source_backlinks.py", "--write"),
                   lambda: py("align_tables.py", "--wiki", ".", "--write"),
                   # Порядок важен: карта и мостик — до панелей, потому что панели собираются по дереву
                   # и обязаны быть последними (§50), а их состояние — производный выход (§29 его не считает).
                   # Свод тезисов — рельсы смыслового прохода: без пересборки §86 говорит «не полон» на любой
                   # правке вики (найдено 2026-09-22 при проверке проб). Идёт до карты: карта считает дерево,
                   # а свод его меняет.
                   lambda: py("stance_context.py", "--write"),
                   lambda: py("project_map.py", "generate"),
                   lambda: py("bridge.py", "generate"),
                   lambda: py("panel_freshness.py", "--rebuild"),
                   # Линтер здесь — отчёт, а не ворота: код возврата у него теперь честный (1 при находках),
                   # но в режиме без аудита красный итог не останавливает работу — решают явные гейты
                   # (gate_lint в check). Иначе каждый красный раздел ронял бы сборку пакетов.
                   lambda: py("lint_wiki.py", quiet=False, check=False)),
    "lint": (lambda: py("lint_wiki.py", quiet=False, check=False), gate_numbers),
    "python-checks": (step_python_checks,),
    # Проход lint: объём по дате прошлого отчёта, машинные числа, свод глазных находок. Читает отчёты
    # подагентов, если они уже лежат в _staging/audit; вики не правит.
    "lint-pass": (lambda: py("lint_pass.py", "--write", quiet=False),),
    "canary": (lambda: py("canary_test.py", quiet=False, check=False),),
    # Сверка покрытия веера в обе стороны: входной список против собранных id (пропуск и выдумка
    # ловятся одинаково), spot-check только с записью. SPEC веера — JSON (task, brief, boundary,
    # forbidden, ids); конверт с id без SPEC не принимается — веер без конверта без SPEC свободен.
    # Хвосты пробрасываются: --expect, --spec, --reports, --spot-check, --self-test. Канарейка 228
    # следит, чтобы сверка сама не ослепла.
    "coverage": (lambda *rest: py("check_coverage.py", *rest, quiet=False),),
    # Дежурство стража цитат: реестр в обе стороны на временной песочнице — чистый черновик
    # проходит, выдуманный id падает. Канарейка 227 следит, чтобы дежурство само не ослепло.
    "citations": (lambda: py("check_citations.py", quiet=False),),
    # Ссылки: проверка внешних адресов с отчётом. В гейтах её нет — сеть не должна решать, пройдёт ли коммит.
    "links": (lambda: py("check_links.py", "--write"),),
    # Адреса материала: карта адресов против записей (§75). Только отчёт: починка пишет в raw/ и делается
    # осознанно — `python3 _toolkit/links_into_records.py --write` (протокол волны, шаг 1).
    "addresses": (lambda: py("links_into_records.py", quiet=False),),
    "numbers": (lambda: py("verify_numbers.py", "--write"), gate_numbers),
    "package": (step_derivatives, step_numbers_write, lambda: py("lint_wiki.py", check=False), step_package),
    # Порядок «сначала attest, потом package» (ответ аудитора на вопрос 2 разбора 11): посылка обязана нести
    # СВЕЖУЮ аттестацию, иначе она легализует недельный прогон. Цена — около десяти минут на отправку.
    "report": (step_derivatives, step_numbers_write, lambda: py("lint_wiki.py", check=False),
               lambda: py("canary_test.py", quiet=False, check=False), step_package,
               lambda: py("handover.py", "archive", "--package", PACKAGE.format(d=datetime.date.today().isoformat()),
                          "--note", "отправляемая версия из tasks.py report")),
    # Порядок важен: слепок снимается ПОСЛЕ подписи письма, иначе в архиве окажется версия без хеша
    # манифеста, а уйдёт подписанная — ровно тот класс «одно имя, два содержимого», что мы закрываем.
    "send": (step_derivatives, step_numbers_write, lambda: py("lint_wiki.py", check=False),
             lambda: py("canary_test.py", quiet=False, check=False), step_package,
             lambda: py("send.py", quiet=False),
             lambda: py("handover.py", "archive", "--package", PACKAGE.format(d=datetime.date.today().isoformat()),
                        "--kind", "sent", "--note", "отправляемая версия из tasks.py send")),
    # Экзамен: числа описывают состояние на дату ответов. Если содержимое менялось позже, задача
    # отказывается засчитывать старые ответы «свежими» — иначе пропуск прогона выглядит как прогон.
    "exam": (lambda: py("exam.py", "fresh", quiet=False), lambda: py("exam.py", "score"),
             lambda: py("exam.py", "status")),
    # Кандидаты в страницы: счёт по порогу схемы и очередь решений (страница или отказ с причиной).
    # Тот же счёт, что у проверки §43: «--all» показывает и закрытых.
    "candidates": (lambda: py("candidates.py", "list", quiet=False),),
    # Лист решений для владельца: черновик по каждому кандидату в HTML (галочки + выгрузка JSON,
    # которую принимает `candidates.py collect`).
    "candidates-review": (lambda: py("candidates_review.py", "--write", quiet=False),),
    # Кандидаты в страницы: рост упоминаний после решения владельца («хочу видеть это, чтобы пересмотреть
    # решение о создании страниц»). `candidates-baseline` — снимок упоминаний на момент решений, без него
    # дельтам не с чем сравниваться; снимается один раз на круг решений.
    "candidates-status": (lambda: py("candidates_status.py", "--write", quiet=False),),
    "candidates-baseline": (lambda: py("candidates_status.py", "--baseline", quiet=False),),
    # Замыслы владельца (`context_note`): очередь решений — страница, покрыто страницей, отказ с причиной.
    "intents": (lambda: py("intents.py", "list", quiet=False),),
    # Лист проверки решений по замыслам: замысел целиком, вердикт карточки, решение — и выгрузка JSON
    # (`intents.py collect`).
    "intents-review": (lambda: py("intents_review.py", "--write", quiet=False),),
    # Дашборд визуального контроля: вкладка «контент» (лист решений, выгрузка, карта волны) и вкладка «мета»
    # (карта структуры) в одном самодостаточном файле `_staging/dashboard.html`. После сборки — проверка
    # отрисовки: панели встроены, srcdoc читаются, порядок панель→агрегат держится. Канарейка 229.
    "dashboard": (lambda: py("dashboard.py", "--write", quiet=False),
                  lambda: py("check_panels.py", quiet=False),),
    # Карта волны: запись волны (`audit/ingest-wave-2026-09-14.json`) → HTML с живым состоянием страниц.
    # Пересобирать после каждого удаления/переименования страницы, иначе карта снова покажет удалённое.
    "wave-map": (lambda: py("ingest_wave_map.py", "--write", quiet=False),),
    # Проба механизма на чужом корпусе: собирает чистый экземпляр (механизм плюс фикстура, без материала
    # владельца), прогоняет путь и печатает числами, где механизм требует экземпляра. Фикстура задаётся
    # `WIKI_FIXTURE` или лежит в `fixture/sources` — фикстура механизма, она поставляется вместе с ним.
    # Оговорка о правах на её тексты — в `fixture/README.md`: решение владельца, не механизма.
    # Хвостовые ключи задачи (`--domain`, `--from-worktree`) пробрасываются в `probe_instance.py` без правки
    # таблицы: проба с объявлением домена обязана запускаться так же одной командой, как без него (замечание
    # владельца 2026-09-24: приёмка «проба на фикстуре проходит» шла мимо единой точки входа).
    "probe": (lambda *rest: py("probe_instance.py", "--fixture",
                                 os.environ.get("WIKI_FIXTURE", toolkit.script(os.path.join("fixture", "sources"))),
                                 *rest, quiet=False),),
    # Приём источников: раскладка в raw/, шапка записи корпуса, зеркало. Всё принятое идёт в работу
    # без отбора; ворота отбора — только для выгруженной базы Telegram (порядок — ingest-wave.md).
    # Корпус и папку приёма задают переменными WIKI_CORPUS и WIKI_INBOX, как пробу — WIKI_FIXTURE.
    # Показать, что уедет в архив, и ничего не трогать (перенос идёт на закрытии волны).
    "archive-pass": (lambda: py("archive_pass.py", quiet=False),),
    "ingest": (lambda: py("ingest.py", "--corpus", os.environ.get("WIKI_CORPUS", "corpus"),
                           "--from", os.environ.get("WIKI_INBOX", "raw-inbox"), quiet=False),),
    # Приём телеграм-выгрузки — меню из двух режимов: весь канал целиком (--all, без review.csv,
    # дальше волна через begin --collected) или разбор строкой review.csv (--review, дальше волна
    # через begin --selection). Без флага только показывает выбор и ничего не трогает: молчаливый
    # режим по умолчанию здесь — это чужое решение о материале, его принимать некому.
    # Порог --all — 1000 сообщений к разбору: выше останавливается до записи, второе слово --force
    # (решение владельца 2026-09-26: предупредить, чтобы был шанс отказаться).
    # Накопитель задаётся WIKI_TG_STAGING, как корпус — WIKI_CORPUS.
    "telegram": (lambda *rest: step_telegram(*rest),),
    # Вычистить экземпляр до состояния поставки: поверхность — фиксированный список в step_wipe,
    # ключ принимает только --confirm. История git остаётся: чистый старт без прошлого — новый клон.
    "wipe": (step_wipe,),
    "offsite": (lambda: py("offsite.py", "push", quiet=False),),
    "status": (lambda: py("offsite.py", "status", quiet=False),
               lambda: py("exam.py", "status", quiet=False)),
    # check — строгая цепочка pre-commit.audit, в том же порядке
    "check": (step_recursion, step_autosnapshot, step_derivatives, step_numbers_write,
              lambda: py("lint_wiki.py", check=False), step_package, gate_lint, gate_numbers, step_exam_nag),
    # Приёмка поставки: те же проверки на копии того, что получит посторонний (см. delivery_check.py).
    "delivery": (lambda: py("delivery_check.py", "--wiki", ".", quiet=False),),
    "doctor": (lambda: py("doctor.py", "--wiki", ".", quiet=False),),
    # Одна команда для того, кто только что скачал проект: проверь поставку, пересобери карту, покажи состояние.
    # Порядок важен: сначала привести копию в рабочее состояние (карта, мостик, панели), только потом приёмка —
    # иначе она красная на ровном месте и пугает того, кто только что скачал проект (поймал входной тест 2026-09-21).
    # Первый запуск: сперва то, что механизм обязан построить сам, потом то, что читает построенное.
    # Свод тезисов, вывеска вики и отчётная пара — не вход пользователя, а производные выводы: на свежем
    # экземпляре их отсутствие читалось как поломка (§86, §3, §14) вместо «ещё не построено» (2026-09-22).
    "start": (lambda: py("doctor.py", "--wiki", ".", quiet=False, check=False),
              lambda: py("stance_context.py", "--write"),
              lambda: py("build_index.py"),
              lambda: py("audit_report.py"),
              lambda: py("project_map.py", "generate"),
              lambda: py("bridge.py", "generate"),
              lambda: py("panel_freshness.py", "--rebuild", quiet=False),
              lambda: py("delivery_check.py", quiet=False, check=False),
              lambda: py("offsite.py", "status", quiet=False),
              lambda: py("exam.py", "status", quiet=False)),
}


def _takes_args(func):
    """Принимает ли шаг хвостовые ключи: смотрим подпись, а не ловим `TypeError` (он же ловит и настоящие ошибки)."""
    import inspect
    kinds = (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD,
             inspect.Parameter.VAR_POSITIONAL)
    return any(p.kind in kinds for p in inspect.signature(func).parameters.values())


def main():
    ap = argparse.ArgumentParser(description="Единая точка входа: имя задачи, шаги, порядок.")
    ap.add_argument("task", nargs="?", default="list", choices=sorted(TASKS) + ["list", "unlink"])
    ap.add_argument("rest", nargs="*", help="аргументы задачи: например слаг для unlink")
    # Ключи, начинающиеся с дефиса (`--domain`), обычным `nargs="*"` не ловятся: остаток разбираем сами.
    a, extra = ap.parse_known_args()
    if extra:
        # Порядок ключей и значений берём из командной строки как есть: `parse_known_args` разносит значение
        # ключа в позиционные и теряет порядок (`--domain` оставался без своего аргумента).
        a.rest = [x for x in sys.argv[1:] if x != a.task]
    if a.task == "unlink":
        # удаление — решение владельца: здесь только сбор следов и порядок из SCHEMA (ничего не меняется)
        if not a.rest:
            sys.exit("укажите слаг: python3 _toolkit/tasks.py unlink <слаг>")
        py("unlink.py", *a.rest, quiet=False, check=False)
        return
    if a.rest and a.task not in ("probe", "wipe", "telegram"):
        opts = [x for x in a.rest if x.startswith("-")]
        rest = [x for x in a.rest if not x.startswith("-")]
        sys.exit(f"задача «{a.task}» хвостовых аргументов не принимает: {' '.join(opts + rest)}")
    if a.task == "list":
        print("Задачи и их шаги (порядок исполнения — сверху вниз):")
        for name, steps in TASKS.items():
            print(f"  {name:10s} {len(steps)} шаг(ов)")
        print("\nПолный список ключей: " + ", ".join(sorted(TASKS)))
        return
    steps = TASKS[a.task]
    print(f"задача «{a.task}»: {len(steps)} шаг(ов)")
    started = datetime.datetime.now()
    for i, step in enumerate(steps, 1):
        # Проба, вычистка и телеграм — задачи с хвостовыми ключами: у пробы объявление домена,
        # у вычистки --confirm, у телеграма --all/--review.
        # Их получает только тот шаг, который их объявил: у остальных в подписи нет параметров.
        step(*a.rest) if _takes_args(step) else step()
    print(f"задача «{a.task}» выполнена за {round((datetime.datetime.now() - started).total_seconds(), 1)} с")



import os as _os
import sys as _sys

_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import domain as _domain  # домен экземпляра: имена его страниц и реестров знает только он

if __name__ == "__main__":
    main()
