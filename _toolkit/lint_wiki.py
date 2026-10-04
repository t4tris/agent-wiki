#!/usr/bin/env python3
"""Линтер LLM-вики. Корень проекта и Obsidian-хранилище разделены:

    <корень проекта>\\            <- корень проекта (служебное: _toolkit/, raw/, _staging/)
    <корень проекта>\\wiki\\       <- то, что открывается в Obsidian (entities/, concepts/, comparisons/, queries/, index.md)

    python3 _toolkit/lint_wiki.py --wiki . [--write-log]

    Код возврата — машинная правда об итоге, а не украшение: 0 — проблем нет, 1 — есть находки.
    Печать «ИТОГО проблем: 0» глазами проверяет человек, ворота (хук, задачи) — кодом; свидетельство
    одного без другого — половина проверки (разбор 2026-09-26: код всегда был 0, и красный §30 после
    коммита никто не заметил).

Проверки (13 разделов): битые wikilinks, сироты, index, frontmatter и YAML,
теги вне таксономии, страницы > 200 строк, синхронизация копий источников,
таблицы, пустые файлы, провенанс, ротация лога, свежесть и сила доказательства.
"""
import argparse
import ast
import datetime
import glob
import hashlib
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fmparse
import schema as _schema
import toolkit
from fmparse import fold

PAGE_DIRS = ("entities", "concepts", "comparisons", "queries")
BACKLINK_HEADING = "## Где использован"
REQUIRED = ("title", "created", "updated", "type", "tags", "sources",
            "last-verified", "verification-status", "evidence", "own-analysis")
STATUSES = ("current", "stale", "deprecated")
EVIDENCE = ("practitioner-opinion", "vendor-self-report", "benchmark", "mixed")
STALE_DAYS = 180
# Генерируемые документы: у них нет рукописного текста требований, поэтому проверки пересказа и хроники их не
# судят так же, как рукописные. Один список на проект: его читают линтер (§34) и метрика пересказа.
def _tree_rev(root):
    try:
        return subprocess.run(["git", "-C", root, "rev-parse", "--short", "HEAD"], capture_output=True,
                              text=True, encoding="utf-8", errors="replace", check=False).stdout.strip() or "—"
    except (OSError, subprocess.TimeoutExpired):
        return "—"


def _tree_dirty(root):
    # Нет git — грязь неопределима, а не отсутствует: раньше здесь выходило False («чистое»),
    # и сводка копии без истории выглядела надёжнее, чем она есть (разбор 2026-09-26).
    try:
        p = subprocess.run(["git", "-C", root, "status", "--porcelain"], capture_output=True,
                           text=True, encoding="utf-8", errors="replace", check=False)
        if p.returncode != 0:
            return None
        return bool(p.stdout.strip())
    except (OSError, subprocess.TimeoutExpired):
        return None


REF_GENERATED = re.compile(r"^(wiki-|canary-report-|canary-results-|lint-summary-|audit-package-|package-index-|"
                           r"number-check-|blind-run-score-|eval-questions-|exam-|cards-registry|claims-registry|"
                           r"project-map|package-history|blind-run-|blind-rerun-)")



def _lines(text):
    """Число строк для порогов и полей: без завершающих пустых строк.

    Хвостовая пустая строка — след редактора, а не содержимое: считать её значило объявлять расхождением
    дописанный перевод строки (эту ложь поймала безвредная канарейка №11 после появления сторожа §18).
    """
    body = text.rstrip("\n")
    return len(body.split("\n")) if body else 0


def _child_issues(result, label):
    issues = [line.strip()[2:] for line in (result.stdout or "").splitlines() if line.strip().startswith("-")]
    if result.returncode != 0 and not issues:
        detail = (result.stderr or "").strip()
        suffix = ": " + detail if detail else ""
        return ["%s: проверка завершилась с кодом %d%s" % (label, result.returncode, suffix)]
    return issues


def read(path):
    return open(path, encoding="utf-8").read()


def split_frontmatter(text):
    m = re.match(r"^---\r?\n(.*?)\r?\n---\r?\n?", text, re.DOTALL)
    if not m:
        return None, text
    fm = {}
    key = None
    for line in m.group(1).split("\n"):
        if ":" in line and not line.startswith((" ", "\t", "-")):
            k, v = line.split(":", 1)
            key = k.strip()
            fm[key] = v.strip()
        elif key:                       # списки в блочной форме (так пишет Obsidian)
            fold(fm, line)
    return fm, text[m.end():]


def taxonomy(root):
    return _schema.taxonomy(root)


def parse_tags(value):
    value = (value or "").strip()
    if value.startswith("[") and value.endswith("]"):
        value = value[1:-1]
    return [t.strip().strip('"').strip("'") for t in value.split(",") if t.strip()]


def artifact_exists(root, token):
    """Появился ли артефакт с таким именем: имя файла содержит слова токена (дефис/подчёркивание не важны).
    Сравниваются ИМЕНА файлов, а не содержимое: иначе токен нашёлся бы в самом реестре, где он записан."""
    norm = lambda s: re.sub(r"[-_ ]", "", s.lower())
    want = norm(token)
    for r, dirs, fs in os.walk(root):
        dirs[:] = [d for d in dirs if d not in {".git", "__pycache__", ".venv", ".venv-asr", "models", ".firecrawl",
                                                ".pytest_cache", "site-packages", ".obsidian"}]
        for f in fs:
            if re.search(r"\d{4}-\d{2}-\d{2}", f):
                continue                      # выход генератора за день, а не механизм
            if want and want in norm(f):
                return True
    return False


def metric_value(root, name):
    """Значение метрики для пороговой предпосылки. Неизвестная метрика — это None, и проверка это скажет,
    а не промолчит: молчание в проверке предпосылок означало бы, что отказ никто не пересмотрит."""
    n = name.strip().lower()
    ad = os.path.join(root, "_staging", "audit")
    figs = sorted(f for f in os.listdir(ad) if re.match(r"wiki-figures-\d{4}-\d{2}-\d{2}\.json$", f)) if os.path.isdir(ad) else []
    data = json.load(open(os.path.join(ad, figs[-1]), encoding="utf-8")) if figs else {}
    figures, checks = data.get("figures", {}), data.get("checks", {})
    if n in ("утверждения", "утверждений"):
        p = os.path.join(root, "_staging", "claims-registry.tsv")
        if os.path.exists(p):
            return sum(1 for l in open(p, encoding="utf-8").read().splitlines() if l.strip()) - 1
        return None
    if n in ("страницы", "страниц"):
        return figures.get("страницы")
    if n in ("источники", "источников"):
        return checks.get("источника")
    if n in ("рёбра", "ребра", "рёбер"):
        return checks.get("рёбер")
    return None


def check_moved_rows(root):
    """§55. Строка, убранная из реестра с причиной «переезд в <реестр>», обязана найтись в реестре-адресате.

    Причина: 16.09.2026 строка `DEEP-JLU/Awesome-Graph-Engineering` ушла из реестра инструментов с
    причиной «подборка материалов — её место в реестре материалов», а в материалы не попала: запись
    исчезла молча, причина осталась намерением. Замершей проверки не было.
    """
    import os as _os
    import re as _re
    decisions = _os.path.join(root, "_staging", "registry-rows-decisions.tsv")
    if not _os.path.exists(decisions):
        return []
    registries = {
        "tools-registry": _os.path.join(root, "wiki", "comparisons", "tools-registry.md"),
        "materials-registry": _os.path.join(root, "wiki", "comparisons", "materials-registry.md"),
    }
    findings = []
    for line in open(decisions, encoding="utf-8").read().splitlines()[1:]:
        parts = line.split("\t")
        if len(parts) < 5 or parts[2] != "убран":
            continue
        name, reason, date = parts[0], parts[3], parts[4]
        targets = _re.findall(r"\[\[([a-z-]+-registry)\]\]", reason)
        for tgt in targets:
            path = registries.get(tgt)
            if not path or not _os.path.exists(path):
                continue
            # имя ищем только в строках таблицы, а не в файле целиком: канарейка №116 перестала
            # ловиться 2026-09-17, когда имя подборки появилось в поле `sources:` того же реестра
            rows_text = "\n".join(l for l in open(path, encoding="utf-8").read().splitlines()
                                  if l.lstrip().startswith("|"))
            if name.split("/")[-1].lower() not in rows_text.lower():
                findings.append((tgt, f"строка «{name}» убрана {date} с причиной «{reason}», но в [[{tgt}]] её нет"))
    return findings


def check_corpus_record_life(root):
    """§57. Запись корпуса удаляется только по утверждённому файлу и только когда её выжимки в страницах.

    Решение владельца 2026-09-17: «перед удалением тонких записей должен формироваться отчёт HTML чтобы человек
    утвердил удаление (механика как обычно галочки + кнопка экспорт в json)», и «запись живёт, пока её выжимки
    не легли в страницы». Проверка смотрит на записи плана удаления (`_staging/delete-thin-notes.tsv`): если
    мастер записи исчез, обязан быть журнал удаления `_staging/delete-log-<дата>.tsv` с этой записью, и у
    записи не должно остаться невклеенных выжимок.
    """
    import glob as _glob
    import json as _json
    import os as _os
    plan = _os.path.join(root, "_staging", "delete-thin-notes.tsv")
    if not _os.path.exists(plan):
        return []
    logs = sorted(_glob.glob(_os.path.join(root, "_staging", "delete-log-*.tsv")))
    logged = set()
    for lg in logs:
        for line in open(lg, encoding="utf-8").read().splitlines()[1:]:
            if line.split("\t")[0].strip():
                logged.add(line.split("\t")[0].strip())
    findings = []
    for line in open(plan, encoding="utf-8").read().splitlines()[1:]:
        stem = line.split("\t")[0].strip()
        if not stem:
            continue
        gone = (not _os.path.exists(_os.path.join(root, "raw", "telegram", stem + ".md")) and
                not _os.path.exists(_os.path.join(root, "wiki", "sources", "raw", "telegram", stem + ".md")))
        ext = _os.path.join(root, "_staging", "extracts", stem + ".json")
        pending = 0
        if not _os.path.exists(ext):
            pending = -2
        elif True:
            try:
                pending = sum(1 for it in _json.load(open(ext, encoding="utf-8")).get("items", [])
                              if not it.get("applied"))
            except (OSError, UnicodeError, _json.JSONDecodeError, AttributeError, TypeError):
                pending = -1
        if gone and stem not in logged:
            findings.append((stem, "запись удалена, а журнала удаления с утверждением нет"))
        if gone and pending == -2:
            findings.append((stem, "запись удалена, а разбора содержания не было: файла выжимок нет"))
        elif gone and pending != 0:
            findings.append((stem, f"запись удалена с невклеенными выжимками ({pending})"))
        if pending == -1:
            findings.append((stem, "файл выжимок повреждён — правило «выжимки в страницах» проверить нельзя"))
        if not gone and pending == -2:
            findings.append((stem, "разбора содержания нет: выжимки не собраны и пустота не подтверждена "
                                   "(extracts.py collect или extracts.py empty)"))
    return findings


def check_quotes_declared(root):
    """§58. Цитата в кавычках обязана быть из источника, объявленного в шапке страницы.

    Зачем (первая волна по новым правилам, 2026-09-17): на странице orchestrator цитата
    «нужно думать, разбираться, тюнить» стояла из записи 113489, которой не было в `sources:` — то есть
    страница приводила чужие слова, не называя носителя. Дети проверяют те цитаты, которые сами считают
    цитатами, и потому пропускают ровно этот класс; проверка живёт в приёмке волны и ищет в объявленных
    источниках страницы, а не в файле целиком.

    Фразы в кавычках, которых нет ни в одном источнике, проверкой не считаются: на текущих данных их 133,
    и большинство — слова владельца, названия болей и пересказы. Это работа глаза, а не машины.
    """
    import glob as _glob
    import os as _os
    import re as _re
    def norm(s):
        return _re.sub(r"\s+", " ", _re.sub(r"[*`>\[\]]|\(http[^)]*\)", "", s)).strip()
    src = {}
    for p in _glob.glob(_os.path.join(root, "raw", "**", "*.md"), recursive=True):
        src[_os.path.relpath(p, root).replace("\\", "/")] = norm(open(p, encoding="utf-8", errors="ignore").read())
    findings = []
    for p in _glob.glob(_os.path.join(root, "wiki", "**", "*.md"), recursive=True):
        rel = _os.path.relpath(p, root).replace("\\", "/")
        if "/sources/" in "/" + rel:
            continue
        text = open(p, encoding="utf-8", errors="ignore").read()
        m = _re.match(r"(?s)^---\n(.*?)\n---", text)
        if not m:
            continue
        declared = _re.findall(r"raw/[\w/\.\-\u0400-\u04FF]+\.md", m.group(1))
        declared_text = " || ".join(src.get(s, "") for s in declared)
        for q in _re.findall(r"«([^»]{40,300})»", text):
            nq = norm(q)
            if nq.count(" ") < 4:
                continue
            probe = nq[:55]
            if probe in declared_text:
                continue
            where = [k for k, v in src.items() if probe in v]
            if where:
                findings.append((rel, f"цитата из {where[0]}, которого нет в sources: — {q[:60]}"))
    return findings


def check_staged_copies(root):
    """§61. Отложенная копия страницы, которая уже опубликована, — находка, а не архив.

    Владелец 2026-09-17: «`_staging/new-pages/` держит 22 отложенные копии страниц, и все 22 уже опубликованы
    в вики. Чистить!» Площадка приёмки пуста между волнами; копия живёт ровно до постановки страницы в вики.
    """
    import glob as _glob
    import os as _os
    findings = []
    for s in sorted(_glob.glob(_os.path.join(root, "_staging", "new-pages", "*.md"))):
        slug = _os.path.basename(s)
        pub = [p for p in _glob.glob(_os.path.join(root, "wiki", "**", slug), recursive=True)
               if "/sources/" not in p.replace("\\", "/")]
        if pub:
            findings.append(("_staging/new-pages/" + slug,
                             "копия уже опубликована в " + _os.path.relpath(pub[0], root).replace("\\", "/"), ""))
    return findings


def check_page_wave(root):
    """§94. Страница вики принадлежит волне: наполнение без записи волны — мимо кассы.

    Найдено 2026-09-25 на первой волне AgentWiki: карточки и страницы были собраны скриптами, линтер
    показывал ноль, а волны не существовало вовсе — ни записи, ни вердиктов, ни карты. Сторожа мимо
    кассы не было: каждый этап был зелёным, а пайплайн не проходился. Теперь страница слоя знаний,
    не названная ни в одной записи волны (`_staging/audit/ingest-wave-<дата>.json`), — находка:
    закрой волну (`wave_runner.py close --confirm`), и запись соберётся сама.
    """
    import glob as _glob
    import json as _json
    import os as _os
    covered = set()
    broken = []
    for rec in sorted(_glob.glob(_os.path.join(root, "_staging", "audit", "ingest-wave-*.json"))):
        try:
            data = _json.load(open(rec, encoding="utf-8"))
        except (OSError, UnicodeError, _json.JSONDecodeError) as exc:
            broken.append((_os.path.relpath(rec, root).replace("\\", "/"),
                           "запись волны не читается: %s" % exc, ""))
            continue
        pages = data.get("pages", [])
        if not isinstance(pages, list):
            broken.append((_os.path.relpath(rec, root).replace("\\", "/"),
                           "запись волны без списка pages", ""))
            continue
        for p in pages:
            name = (p.get("page") or "") if isinstance(p, dict) else ""
            if name:
                covered.add(name.split("/")[-1])
    findings = list(broken)
    for d in ("concepts", "entities", "comparisons", "queries"):
        folder = _os.path.join(root, "wiki", d)
        if not _os.path.isdir(folder):
            continue
        for fn in sorted(_os.listdir(folder)):
            if not fn.endswith(".md"):
                continue
            if fn[:-3] not in covered:
                findings.append(("%s/%s" % (d, fn),
                                 "страница вне волн: закрой волну (`wave_runner.py close --confirm`)", ""))
    return findings


def check_page_slugs(root):
    """§62. Имя страницы вики — латиница: `a-z`, цифры и дефис.

    Владелец 2026-09-17: «нейминг должен быть автоматическим без человека» и «в отчет попадают названия
    страницы на кириллице, хотя фактически у нас все названия статей на латинице». Слаг считает `slugify.py`,
    но проверить, что созданная страница названа по правилу, было нечем: страница с кириллическим именем
    прошла бы молча. На текущем дереве находок нет — это страховка, а не лечение, и говорю это прямо.
    """
    import glob as _glob
    import os as _os
    import re as _re
    findings = []
    for p in _glob.glob(_os.path.join(root, "wiki", "**", "*.md"), recursive=True):
        rel = _os.path.relpath(p, root).replace("\\", "/")
        if "/sources/" in "/" + rel:
            continue
        # Владелец 2026-09-19: «исключить папку из проверки имени». `wiki/Clippings/` — его входящий поток
        # выгрузок Obsidian-клиппера, а не страницы вики: имена там даёт источник, а не наш слаг-генератор.
        if "/Clippings/" in "/" + rel:
            continue
        name = _os.path.basename(p)
        if not _re.fullmatch(r"[a-z0-9\-]+\.md", name):
            findings.append((rel, "имя страницы не латинское: ожидается a-z, цифры и дефис", ""))
    return findings


def check_heading_links(root):
    """§63. Заголовок не несёт вики-ссылок.

    Ссылка-алиас внутри заголовка ломает всё, что читает разделы по имени: обратная ссылка в копии источника
    (`_toolkit/source_backlinks.py`) режет имя раздела по «|» и получает `[[anthropic` вместо заголовка, а
    проверка §42 объявляет такого раздела нет. Найдено 2026-09-17 на девяти заголовках пяти страниц —
    §42 показал это косвенно (три находки), прямо же механизма не было.
    """
    import glob as _glob
    import os as _os
    findings = []
    for p in sorted(_glob.glob(_os.path.join(root, "wiki", "**", "*.md"), recursive=True)):
        rel = _os.path.relpath(p, root).replace("\\", "/")
        if "/sources/" in "/" + rel:
            continue
        for line in read(p).split("\n"):
            if line.startswith("#") and "[[" in line:
                findings.append((rel, f"в заголовке вики-ссылка: {line.strip()[:80]}", ""))
    return findings


def check_source_cards(root):
    """§64. У каждого источника корпуса есть карточка.

    Реестр карточек считает покрытие и печатает строку «без карточек: …», но падал на этом только он:
    линтер молчал, и источник, прошедший инжест без карточки, жил так неограниченно долго — ровно это
    случилось с записью голосовых заметок об агентской практике (17 539 знаков, единственная без карточки,
    обнаружена 2026-09-17 вручную). Считаю те же единицы, что и реестр: имена файлов `raw/*.md` и
    `raw/articles/*.md` без служебного шаблона и без транскрипций-докладов (суффикс `_report.md`).
    """
    import json as _json
    import os as _os
    reg = _os.path.join(root, "_staging", "cards-registry.json")
    if not _os.path.exists(reg):
        return []
    # Карточки берём с диска, а не из реестра: реестр перечисляет свои же строки, и удаление файла
    # карточки он не замечает — проверка, читающая только его, слепа ровно к тому случаю, ради которого
    # заведена (канарейка №126 показала это на первой же пробе).
    cards_dir = _os.path.join(root, "_staging", "cards")
    if not _os.path.isdir(cards_dir):
        return []
    try:
        cards = _json.load(open(reg, encoding="utf-8"))["cards"]
        if not isinstance(cards, list):
            raise TypeError("поле cards должно быть списком")
    except (OSError, UnicodeError, _json.JSONDecodeError, KeyError, TypeError, AttributeError) as exc:
        return [("cards-registry.json", "реестр карточек не прочитан: %s" % exc, "")]
    # Соответствие «карточка → источник» берём из реестра (в имени файла его нет: у манифестов карточка
    # называется иначе), но саму карточку требуем на диске — иначе удаление файла проверка не заметит.
    # Сравнение по имени файла: реестр пишет то голое имя, то путь — оба варианта значат один источник.
    carded = {x.get("source", "").split("/")[-1] for x in cards
              if x.get("source") and _os.path.exists(_os.path.join(cards_dir, x.get("file", "")))}
    # 2026-09-25, волна 1 AgentWiki: универсум считал четыре каталога верхнего уровня, и записи корпуса
    # во вложенных папках (raw/fixture/) проверка не видела вовсе — источник без карточки жил бы молча.
    # Теперь универсум — всё дерево raw/; сравнение по имени файла, как раньше.
    service = {"manifest_maker_prompt.md"}
    universe = []
    raw_root = _os.path.join(root, "raw")
    if _os.path.isdir(raw_root):
        for base, _dirs, files in _os.walk(raw_root):
            for f in sorted(files):
                if f.endswith(".md"):
                    universe.append(_os.path.relpath(_os.path.join(base, f), raw_root).replace("\\", "/"))
    findings = []
    for f in universe:
        base = f.split("/")[-1]
        if base in service or base.endswith("_report.md") or base in carded:
            continue
        findings.append((f, "у источника нет карточки: инжест прошёл в обход разбора содержания", ""))
    return findings


def check_registry_link_rows(root):
    """§65. Ссылочная заметка в шапке реестра обязана быть строкой таблицы.

    Владелец 2026-09-17: «2026-09-02-nacl-…-113459 — в статье указано, что она входит в реестр tools, но это
    не так». Так выглядела запись, объявленная источником реестра инструментов и не попавшая ни в одну строку:
    в копии источника раздел «Где использован» честно писал «назван основой страницы», а строки не было.
    Канон реестров другой: ссылка на инструмент становится строкой (`§51`), поэтому заметка, тело которой —
    ссылка, объявленная в шапке реестра, обязана быть названа в таблице. Аналогично и для реестра материалов.
    """
    import os as _os
    import re as _re
    out = []
    for reg in ("tools-registry.md", "materials-registry.md"):
        path = _os.path.join(root, "wiki", "comparisons", reg)
        if not _os.path.exists(path):
            continue
        text = read(path)
        fm = _re.match(r"(?s)^---\n(.*?)\n---", text)
        if not fm:
            continue
        declared = fmparse.items(text, "sources")
        if not declared:
            continue
        table = "\n".join(l for l in text.split("\n") if l.startswith("|"))
        for src in declared:
            note = _os.path.join(root, src)
            if not _os.path.exists(note):
                continue
            stem = _os.path.basename(src)[:-3]
            if stem in table:
                continue
            body = _re.sub(r"(?s)^---.*?---", "", read(note))
            body = _re.sub(r"(?ms)^##\s+Что за ссылкой.*?(?=^##\s|\Z)", "", body)
            links = _re.findall(r"https?://\S+", body)
            words = len(_re.findall(r"[А-Яа-яA-Za-z]{3,}", _re.sub(r"https?://\S+", "", body)))
            if links and words < 40:
                out.append((reg, "%s: ссылочная заметка объявлена источником реестра, но строки в таблице нет" % src, ""))
    return out


def check_card_shape(root):
    """§66. Форма карточки извлечения.

    Карточка — рабочий документ инжеста: из неё растут страницы, и её читают как свидетельство разбора источника.
    Форму не проверял никто: реестр парсит шапку, §33 читает заголовки, но карточка без шапки или без линии
    проходила молча. В проверке ровно то, отсутствие чего дефект: шапка, поля линии, заголовок документа.
    Первая версия проверки требовала ещё раздел понятий и двадцать строк — и обвинила 50 карточек из 167,
    потому что короткая карточка ссылочной заметки такая по замыслу. Правило, придуманное по догадке, а не по
    дефекту, пришлось срезать до измеренного.
    """
    import glob as _glob
    import os as _os
    import re as _re
    out = []
    for path in sorted(_glob.glob(_os.path.join(root, "_staging", "cards", "*.md"))):
        name = _os.path.basename(path)
        text = read(path)
        fm = _re.match(r"(?s)^---\n(.*?)\n---", text)
        head = fm.group(1) if fm else ""
        body = text[fm.end():] if fm else text
        if not fm:
            out.append((name, "нет шапки: карточка без линии и типа — не свидетельство разбора", ""))
            continue
        for field in ("lineage_tools", "lineage_debate"):
            if not _re.search(r"(?m)^" + field + r":", head):
                out.append((name, "в шапке нет поля %s: линия карточки не заполнена" % field, ""))
        if not _re.search(r"(?m)^# \S", body):
            out.append((name, "нет заголовка документа", ""))
        # Раздел «О чём источник» — то, что делает карточку свидетельством разбора, а не набором ячеек.
        # Замер 2026-09-17: у 166 карточек из 168 такой раздел есть; варианты имени («О чём», «О чём источник»)
        # законны, отсутствие — дефект, и нашлись именно два случая (карточка голосовых заметок и старая
        # карточка GRACE). Проверка ставится по измеренному состоянию, а не по догадке.
        # Корпус знает две законные формы: раздел «## О чём источник» и жирный пункт «- **О чём источник**:».
        # Замер 2026-09-17: 133 карточки первым способом, 35 вторым; отсутствует у двух — и те две оказались
        # не дефектом, а третьей формой («О чём источник» жил строкой без жирного начертания). Проверка
        # ловит отсутствие по смыслу, а не по разметке.
        if not _re.search(r"(?m)^(?:##\s+О чём|[-*]\s+\**О чём)", body):
            out.append((name, "нет строки «О чём источник»: карточка не говорит, о чём источник", ""))
    return out


def check_cards_cited(root):
    """§69. У каждой карточки есть адрес в вики.

    Обратной проверки не было: §46 следит за участием источника в наряде прохода, а покрытие карточек
    страницами не смотрел никто, и карточка могла жить в пустоте — разобрана, но ни одной страницей не
    названа. Проверка соединяет реестр карточек с текстом страниц: имя файла источника обязано встретиться
    хотя бы на одной странице вики (или в её шапке в поле `sources`, что тоже ссылка).
    """
    import glob as _glob
    import json as _json
    import os as _os
    reg_path = _os.path.join(root, "_staging", "cards-registry.json")
    if not _os.path.exists(reg_path):
        return []
    items = _json.loads(read(reg_path)).get("cards", [])
    pages = {}
    for path in _glob.glob(_os.path.join(root, "wiki", "**", "*.md"), recursive=True):
        rel = _os.path.relpath(path, root).replace("\\", "/")
        if rel.startswith("wiki/sources/"):
            continue
        pages[rel] = read(path)
    out = []
    for it in items:
        src = str(it.get("source") or "")
        stem = _os.path.basename(src)
        if stem.endswith(".md"):
            stem = stem[:-3]
        if not stem:
            continue
        if not any(stem in text for text in pages.values()):
            out.append((str(it.get("file") or stem), "карточка не названа ни одной страницей вики: %s" % stem, ""))
    return out


def check_scripts_reachable(root):
    """§67. Скрипт площадки назван в протоколе, SCHEMA, реестре структуры или наряде.

    Скрипт, которого нет в документах волны, для сессии не существует: его не найдут или найдут позже и не
    поймут, зачем он. Замер 2026-09-17: девятнадцать рабочих скриптов `_staging/` не были названы нигде, среди
    них `lint_pass.py` (проход lint). Проверка машинная: имя файла обязано встретиться в `ingest-wave.md`,
    `SCHEMA.md`, `scripts-layout.md` или `wave-order.md`; если производной описи нет, проверка строит её
    тем же генератором, не требуя instance-артефакт в чистой копии.
    """
    import glob as _glob
    import os as _os
    docs = ""
    for name in (os.path.join("_toolkit", "ingest-wave.md"), os.path.join("_toolkit", "SCHEMA.md"),
                 os.path.join("_staging", "scripts-layout.md"), os.path.join("_toolkit", "wave-order.md")):
        path = _os.path.join(root, name)
        if _os.path.exists(path):
            docs += read(path)
    layout = _os.path.join(root, "_staging", "scripts-layout.md")
    if not _os.path.exists(layout):
        sys.path.insert(0, _os.path.join(root, "_toolkit"))
        import scripts_layout
        docs += "\n".join(row["name"] for row in scripts_layout.rows(root))
        docs += "\nscripts_layout.py\n"
    out = []

    for path in sorted(_glob.glob(_os.path.join(root, "_toolkit", "**", "*.py"), recursive=True)):
        name = _os.path.relpath(path, _os.path.join(root, "_toolkit")).replace(_os.sep, "/")
        # Файл экземпляра (`.local.`) в документах механизма не называется: он не часть поставки, а его
        # содержимое — данные владельца (решение 2026-09-22).
        if ".local." in name:
            continue
        if name not in docs:
            out.append((name, "скрипт не назван ни в протоколе волны, ни в SCHEMA, ни в реестре структуры, ни в наряде", ""))
    return out


def check_media_unclear(root):
    """§68. У каждой непонятной записи изображения принят решение — и список вообще существует.

    Первая версия (2026-09-17, утро) смотрела только файл `media-unclear.tsv` и потому была слепой ровно в том
    случае, ради которого заводилась: партия описаний с записями `note_understandable=false` проходила молча,
    если списка не было. Владелец назвал это вечером того же дня, и замер подтвердил: 30 партий, 522 записи,
    29 без контекста — и ни одного файла списка в проекте. Теперь проверка идёт от данных: сначала из партий
    собираются записи, которым контекста не хватило, затем требуется, чтобы каждая была названа списком
    (`<каталог волны>/media-unclear.tsv`) и получила решение (`media-unclear-decisions.tsv`: ключ, решение,
    причина, дата). Файл описаний задним числом не переписывается — решения живут отдельно.
    """
    import glob as _glob
    import json as _json
    import os as _os
    need = {}
    out = []
    for path in sorted(_glob.glob(_os.path.join(root, "_staging", "**", "descriptions-*.json"), recursive=True)):
        try:
            data = _json.loads(read(path))
        except (OSError, UnicodeError, _json.JSONDecodeError, AttributeError, TypeError) as exc:
            out.append((_os.path.relpath(path, root).replace("\\", "/"),
                        "файл описаний не прочитан: %s" % exc, ""))
            data = {}
        for it in data.get("items", []):
            if it.get("note_understandable", True):
                continue
            key = str(it.get("id", "")).strip()
            if key:
                need.setdefault(key, _os.path.relpath(path, root).replace("\\", "/"))
    if not need:
        return out
    listed, decided = {}, {}
    for path in sorted(_glob.glob(_os.path.join(root, "_staging", "**", "media-unclear.tsv"), recursive=True)):
        for line in read(path).strip().split("\n")[1:]:
            cells = line.split("\t")
            if cells and cells[0].strip():
                listed[cells[0].strip()] = _os.path.relpath(path, root).replace("\\", "/")
    for path in sorted(_glob.glob(_os.path.join(root, "_staging", "**", "media-unclear-decisions.tsv"), recursive=True)):
        for line in read(path).strip().split("\n")[1:]:
            cells = line.split("\t")
            if len(cells) > 2 and cells[0].strip() and cells[1].strip() and cells[2].strip():
                decided[cells[0].strip()] = True
    for key, where in sorted(need.items()):
        if key not in listed:
            out.append((key, "запись без контекста (%s), а списка media-unclear.tsv нет" % where, ""))
        elif key not in decided:
            out.append((key, "непонятная запись изображения без решения (список: %s)" % listed[key], ""))
    return out


def check_audio_transcripts(root):
    """§72. Аудио в сырье обязано быть расшифровано.

    Дыра, названная владельцем 2026-09-17: сцепка внутри `finish_fusion.py` есть, но волну ничто не заставляет
    предъявить расшифровки — она могла закрыться с аудио в сырье и без текста. Проверка соединяет три вещи,
    которые уже есть в проекте: файл аудио в `raw/`, запись корпуса, которая его называет, и раздел расшифровки
    в этой записи. Замер: 10 голосовых файлов, все десять названы записью, у записи есть «## Расшифровки».
    """
    import glob as _glob
    import os as _os
    import re as _re
    exts = (".ogg", ".mp3", ".m4a", ".wav", ".opus", ".flac")
    audio = [p for p in sorted(_glob.glob(_os.path.join(root, "raw", "**", "*"), recursive=True))
             if _os.path.splitext(p)[1].lower() in exts]
    if not audio:
        return []
    records = {}
    for path in sorted(_glob.glob(_os.path.join(root, "raw", "**", "*.md"), recursive=True)):
        records[_os.path.relpath(path, root).replace("\\", "/")] = read(path)
    out = []
    for path in audio:
        base = _os.path.basename(path)
        rel = _os.path.relpath(path, root).replace("\\", "/")
        holders = [k for k, text in records.items() if base in text]
        if not holders:
            out.append((rel, "аудио не названо ни одной записью корпуса: расшифровку некому предъявить", ""))
            continue
        if not any(_re.search(r"(?m)^##\s*Расшифров", records[k]) or "machine-transcript" in records[k]
                   for k in holders):
            out.append((rel, "запись %s называет аудио, но расшифровки не несёт" % holders[0], ""))
    return out

def check_source_required_fields(root):
    """§70. Обязательные поля шапки источника на месте.

    §54 ловит лишние поля (не из набора типа), но пропускал отсутствие: запись корпуса без `title`, `summary`
    или `created` проходила молча. Дыру нашла эмуляция волны 2026-09-17: источник, добавленный без шапки,
    поднимал §8 (нет зеркала), §64 (нет карточки) и очередь карточек, а поля шапки не проверял никто.
    Набор взят измерением: у всех 128 записей корпуса есть `title`, `type`, `tags`, `summary`, `status`,
    `created`; правило по измеренному состоянию не даёт ложных срабатываний.
    """
    import glob as _glob
    import os as _os
    import re as _re
    vault = os.path.join(root, "wiki")
    # Набор взят измерением по 171 записи корпуса: пять полей есть у всех без исключения, `created` —
    # у всех записей типов telegram_saved и article (у манифестов, отчётов и промптов его нет по устройству).
    need = ("title", "type", "tags", "summary", "status")
    need_by_type = {"telegram_saved": ("created",), "article": ("created",)}
    out = []
    files = (sorted(_glob.glob(_os.path.join(root, "raw", "**", "*.md"), recursive=True)) +
             sorted(_glob.glob(_os.path.join(vault, "sources", "raw", "**", "*.md"), recursive=True)))
    for path in files:
        text = read(path)
        fm = _re.match(r"(?s)^---\r?\n(.*?)\r?\n---", text)
        rel = _os.path.relpath(path, root).replace("\\", "/")
        if not fm:
            out.append((rel, "у записи корпуса нет шапки", ""))
            continue
        head = fm.group(1)
        m_type = _re.search(r"(?m)^type:\s*(\S+)", head)
        need_here = list(need) + list(need_by_type.get(m_type.group(1) if m_type else "", ()))
        missing = [f for f in need_here if not _re.search(r"(?m)^" + f + r":", head)]
        if missing:
            out.append((rel, "в шапке нет полей: " + ", ".join(missing), ""))
    return out


def check_mirror_without_master(root):
    """§71. Копия источника в хранилище без мастера в `raw/`.

    §8 смотрит только в одну сторону — есть ли копия у мастера, — и файл, живущий в зеркале без записи в
    `raw/`, проходил молча. Нашлось 2026-09-17 на живом файле (`Learnings & tips with agents.md`): копия
    чужого текста в вики, к которой не ведёт ни одна запись корпуса. Мастер — архив забранного, и зеркало
    без мастера означает, что источник никто не заводил; содержание такой копии ничем не проверяемо.
    """
    import glob as _glob
    import os as _os
    vault_root = _os.path.join(root, "wiki", "sources", "raw")
    out = []
    for path in sorted(_glob.glob(_os.path.join(vault_root, "**", "*.md"), recursive=True)):
        rel = _os.path.relpath(path, vault_root)
        if not _os.path.exists(_os.path.join(root, "raw", rel)):
            out.append((_os.path.relpath(path, root).replace("\\", "/"),
                        "копия источника без мастера в raw/", ""))
    return out


def check_registry_links(root):
    """§73. Веб-адрес в строке реестра инструментов взят из записи, а не придуман.

    Владелец 2026-09-17 назвал принципиальным недочётом, что в реестре нет колонки со ссылкой на сайт: читатель
    видит название инструмента и не может дойти до него. Колонка добавлена, но адрес — то место, где легче всего
    выдумать: проверка требует, чтобы каждый непустой адрес встречался в записи, объявленной в последней колонке
    строки (или был её адресом на GitHub в виде owner/repo). Второе исключение — имя строки, которое само
    является доменом (`mdtask.dev`): тогда адрес выводится из имени, машиной, без суждения. Пустое место
    помечается «—» и дефектом не считается: адрес есть не в каждой записи, и выдумывать его нельзя.
    """
    import glob as _glob
    import os as _os
    path = _os.path.join(root, "wiki", "comparisons", "tools-registry.md")
    if not _os.path.exists(path):
        return []
    out = []
    for line in read(path).split("\n"):
        if not line.strip().startswith("|") or set(line.strip().replace("|", "").replace("-", "").replace(" ", "")) == set():
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) != 5 or cells[0] in ("Инструмент",):
            continue
        name, url, where = cells[0], cells[3], cells[4]
        if url in ("", "—", "-"):
            continue
        if not url.startswith("http"):
            out.append((name, "адрес строки не похож на веб-адрес: %s" % url[:60], ""))
            continue
        records = []
        for slug in re.findall(r"\[\[([^\]\|]+)", where):
            for hit in _glob.glob(_os.path.join(root, "raw", "**", slug + ".md"), recursive=True):
                records.append(read(hit))
        if not records:
            out.append((name, "у строки нет записи, по которой можно проверить адрес", ""))
            continue
        blob = " ".join(records).lower()
        probe = url.lower()
        # Имя строки может само быть адресом: `mdtask.dev` — это и есть домен сервиса, и адрес выводится
        # из имени, а не выдумывается (владелец 2026-09-18: «mdtask.dev это и есть адрес»).
        host_like = re.match(r"^[a-z0-9][a-z0-9\-]*(\.[a-z0-9][a-z0-9\-]*)+$", name.strip().lower())
        from_name = host_like and probe.rstrip("/") in ("https://" + name.strip().lower(),
                                                        "http://" + name.strip().lower())
        if not from_name and probe not in blob and probe.replace("https://github.com/", "") not in blob:
            out.append((name, "адрес строки не найден в её записи: %s" % url[:70], ""))
    return out


def check_materials_links(root):
    """§74. В реестре материалов есть колонка ссылки, и ссылка в ней — настоящий адрес.

    Владелец 2026-09-17 назвал это же принципиальным недочётом и на странице материалов: название есть, а дойти
    до материала нельзя. Ссылка переехала в свою колонку; проверка следит, что колонка на месте, что адрес
    начинается с http и не содержит пробелов, и что у строки либо адрес, либо честное «—».
    """
    import os as _os
    path = _os.path.join(root, "wiki", "comparisons", "materials-registry.md")
    if not _os.path.exists(path):
        return []
    out = []
    head_seen = False
    for line in read(path).split("\n"):
        s = line.strip()
        if not s.startswith("|"):
            continue
        if set(s.replace("|", "").replace("-", "").replace(" ", "")) == set():
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if cells[:2] == ["Материал", "Ссылка"]:
            head_seen = True
            continue
        if not head_seen or len(cells) < 5:
            continue
        url = cells[1]
        if not url or url in ("—", "-"):
            continue
        if not url.startswith("http") or " " in url:
            out.append((cells[0][:40], "адрес в колонке ссылки не похож на веб-адрес: %s" % url[:60], ""))
    if not head_seen:
        out.append(("materials-registry.md", "в таблице нет колонки «Ссылка»: до материала не дойти", ""))
    return out


def check_record_links(root):
    """§75. Адрес из выгрузки доехал до записи.

    Пост ссылается на материал гиперссылкой («Забираем себе — тут»), выгрузка адрес знает, а в записи его не
    было: оставалось «тут» без адреса, и заметка теряла смысл — ни карточка, ни строка реестра, ни страница не
    могли сказать, куда идти. Правило одно и то же с починкой (`links_into_records.py`), чтобы не завести двух
    понятий об одном: каждый адрес материала, который карта привязывает к id сообщения, обязан найтись в тексте
    записи — в шапке (`source_url`/`source_links`) или в теле. Условие «тело говорит о ссылке словами» снято
    2026-09-18: оно было уже заявленного правила и пропускало частичную потерю (четыре записи, 22 адреса).
    Корень канала (t.me/имя) материалом не считается, приглашение в закрытый чат — считается.
    """
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import links_into_records as L
    by_id = L.load_links(root)
    issues = []
    # Карта адресов — часть проверки: без неё проверять нечего, и молчание не должно выглядеть как успех.
    if not by_id:
        if not domain_register_path(root, "links"):
            return []          # карты адресов экземпляр не объявил: проверка не применима (говорит §89)
        return ["карты адресов нет (`_tools/tg-saved/links-by-message.json`): проверить, что адрес "
                "доехал до записи, нечем"]
    for path, mid, text in L.records(root):
        urls = by_id.get(mid)
        if not urls:
            continue
        content = [u for u in urls if not re.match(r"^https?://t\.me/(?!\+|joinchat/)[^/]+/?$", u)] or urls
        # Условие «тело говорит о ссылке словами» снято 2026-09-18: оно было уже заявленного правила и
        # пропускало записи, где один адрес из нескольких уже стоял (четыре записи, 22 адреса). Правило
        # простое: адрес карты обязан найтись в тексте записи — в шапке или в теле.
        for u in content:
            if u not in text:
                issues.append(os.path.relpath(path, root).replace(os.sep, "/") +
                              " — адрес %s есть в карте, но не в записи" % u[:70])
    return issues


def check_registry_address_column(root):
    """§76. Адрес стоит в колонке «Ссылка», а не в имени строки.

    Владелец 2026-09-18: «в реестре материалов в первой же строке недочёт — ссылка на mastra в колонке
    „материал“, а не в колонке „ссылка“». Так выглядит слитая строка: в имени оказались несколько
    материалов и адрес одного из них. Правило: если в имени строки есть веб-адрес (или домен), он обязан
    быть и адресом строки — иначе строка врёт о том, куда ведёт. Имя-домен (`mdtask.dev`) не находка:
    там адрес строки совпадает с именем.
    """
    import os as _os
    host = re.compile(r"(?:https?://\S+)|(?:[a-z0-9][a-z0-9\-]*\.(?:ai|dev|io|com|org|net|sh|ru|app|bot|co|site|xyz|me)(?:/\S*)?)", re.IGNORECASE)
    out = []
    for page, namecol in ((_os.path.join(root, "wiki", "comparisons", "materials-registry.md"), "материалов"),
                          (_os.path.join(root, "wiki", "comparisons", "tools-registry.md"), "инструментов")):
        if not _os.path.exists(page):
            continue
        head = None
        for line in read(page).split("\n"):
            s = line.strip()
            if not s.startswith("|") or set(s.replace("|", "").replace("-", "").replace(" ", "")) == set():
                continue
            cells = [x.strip() for x in s.strip("|").split("|")]
            if cells and cells[0] in ("Материал", "Инструмент"):
                head = cells
                continue
            if not head or "Ссылка" not in head or len(cells) != len(head):
                continue
            url = cells[head.index("Ссылка")]
            for m in host.finditer(cells[0]):
                token = m.group(0).strip().rstrip(".,;:»")
                norm = lambda x: re.sub(r"^(https?://)?(www\.)?", "", x.rstrip("/").lower())
                if norm(token) == norm(url):
                    continue
                out.append((cells[0][:40] or page,
                            "адрес %s стоит в колонке имени (%s): его место — в колонке «Ссылка»" % (token[:60], namecol),
                            ""))
                break
    return out


def check_table_glued(root):
    """§77. Таблица не сломана: перед шапкой пустая строка, между шапкой и разделителем её нет.

    Владелец 2026-09-18: «таблица swiss cheese сломалась» на странице `agent-evals`. Две формы одной
    поломки, обе делают таблицу текстом с палками: (1) строка-шапка стоит сразу после абзаца — в Obsidian
    таблица не начинается; (2) между шапкой и разделителем вставлена пустая строка — шапка отрывается от
    таблицы. Правило проверяет обе.
    """
    out = []
    for path in (sorted(glob.glob(os.path.join(root, "wiki", "**", "*.md"), recursive=True)) +
                 sorted(glob.glob(os.path.join(root, "raw", "**", "*.md"), recursive=True))):
        lines = read(path).split("\n")
        rel = os.path.relpath(path, root).replace(os.sep, "/")
        for i, l in enumerate(lines):
            s = l.strip()
            inner = (s.replace("|", "").replace(" ", "").replace(":", "") if s.startswith("|") else "")
            # разделитель — строка, у которой после удаления палок и пробелов остались только дефисы
            is_sep = bool(inner) and not (set(inner) - set("-"))
            if is_sep and i >= 1:
                if not lines[i - 1].strip().startswith("|"):
                    out.append((rel, "разделитель таблицы стоит строкой %d без шапки над ним" % (i + 1), ""))
                    continue
                if i >= 2:
                    before = lines[i - 2].strip()
                    if before and not before.startswith(("|", "#")):
                        out.append((rel, "шапка таблицы (строка %d) стоит сразу после абзаца: перед ней нужна "
                                         "пустая строка" % i, ""))
    return out


def check_address_delegated_to_record(root):
    """§78. Названный артефакт несёт адрес сам, а не отсылку в запись.

    Владелец 2026-09-18: «Отправка читателя на страницу в /raw за ссылкой вместо того, чтобы поместить
    ссылку прямо в статью, утеря ссылок — это антипаттерн». Ловим узко, чтобы не шуметь: страница
    называет то, на что у объявленного ею источника есть адрес — имя артефакта берётся из самого адреса
    (хвост вида `owner/repo` или длинный слаг), — а самого адреса в тексте страницы нет. Придумать
    находку нельзя: поводом служит адрес, который у нас уже лежит в записи.
    """
    import os as _os
    PAGE_DIRS = ("entities", "concepts", "comparisons", "queries")
    GENERIC = {"index", "main", "master", "readme", "skills", "skill", "docs", "doc", "blog", "papers",
               "posts", "article", "articles", "blob", "tree", "releases", "wiki", "about", "src", "app"}

    # Карта адресов выгрузки — второй дом адреса: у части записей адрес живёт только в ней
    # (`links-by-message.json`), и без карты проверка молчала на странице artifact-driven-ui,
    # где адрес YouTube лежал именно в карте (владелец 2026-09-18).
    import json as _json
    _map = {}
    map_error = None
    _map_path = _os.path.join(root, "_tools", "tg-saved", "links-by-message.json")
    if _os.path.exists(_map_path):
        try:
            _map = _json.loads(read(_map_path))
        except (OSError, UnicodeError, _json.JSONDecodeError, AttributeError, TypeError) as exc:
            _map = {}
            map_error = str(exc)

    def addresses_of(rel):
        """Адреса объявленного источником документа: source_url, source_links и карта выгрузки."""
        path = _os.path.join(root, *rel.split("/"))
        if not _os.path.exists(path):
            return []
        text = read(path)
        out = []
        for key in ("source_url", "source_links"):
            m = re.search(r"(?m)^%s:\s*(.+)$" % key, text)
            if m:
                out += re.findall(r"https?://[^\s\"'\],]+", m.group(1))
        ids = re.findall(r"(\d{6})", rel)
        for i in ids:
            for u in _map.get(i, []) or []:
                out.append(u)
        return out

    def is_link_note(rel):
        """Запись без содержания, кроме ссылки: заголовок «Ссылка на …» или тело из одного адреса."""
        path = _os.path.join(root, *rel.split("/"))
        if not _os.path.exists(path):
            return False
        text = read(path)
        m = re.search(r"(?m)^title:\s*\"?(.+?)\"?\s*$", text)
        if m and m.group(1).strip().lower().startswith("ссылка"):
            return True
        parts = text.split("\n---\n", 1)
        body = (parts[1] if len(parts) > 1 else text).split("## Что за ссылкой")[0].strip()
        return bool(len(body) < 400 and re.match(r"^\s*(https?://\S+|.+\n+\s*https?://\S+)\s*$", body))

    def names_of(url):
        """Имена, по которым видно, что страница называет именно этот адрес."""
        u = re.sub(r"^https?://", "", url).split("#")[0].split("?")[0].rstrip("/")
        seg = [x for x in u.split("/")[1:] if x]
        out = []
        if len(seg) >= 2:
            out.append("/".join(seg[-2:]))
        # Односложное имя без дефиса — обычно обычное слово («filesystem»), а не название артефакта:
        # название либо содержит дефис, либо длиннее 12 знаков.
        if (seg and len(seg[-1]) >= 8 and seg[-1].lower() not in GENERIC
                and ("-" in seg[-1] or len(seg[-1]) > 12)):
            out.append(seg[-1])
        return out

    def present(text, url):
        """Адрес в тексте — в любом из рабочих написаний."""
        bare = re.sub(r"^https?://", "", url).rstrip("/")
        return url in text or bare in text or bare.replace("#", "") in text or url.replace("#", "#") in text

    # Индекс «страница → адреса в её теле»: если адрес живёт на странице вики, на которую эта
    # страница ссылается, отсылка читателя законна — это ход внутри вики, а не отправка в /raw.
    vault = _os.path.join(root, "wiki")
    page_dirs = [d for d in PAGE_DIRS if _os.path.isdir(_os.path.join(vault, d))]
    page_body, page_urls, page_links = {}, {}, {}
    for d in page_dirs:
        for fn in sorted(_os.listdir(_os.path.join(vault, d))):
            if not fn.endswith(".md"):
                continue
            slug = fn[:-3]
            body = split_frontmatter(read(_os.path.join(vault, d, fn)))[1] or ""
            page_body[slug] = body
            page_urls[slug] = set(re.findall(r"https?://[^\s\"'\]\)|]+", body))
            page_links[slug] = set(re.findall(r"\[\[([^\]|#]+)", body))

    def address_reachable(slug, url, seen=None):
        """Адрес либо в этой странице, либо на странице вики, на которую она ссылается."""
        seen = seen or set()
        if slug in seen:
            return False
        seen.add(slug)
        if url in page_urls.get(slug, ()):
            return True
        for nxt in page_links.get(slug, ()):
            nxt = nxt.strip().split("/")[-1]
            if nxt in page_urls and address_reachable(nxt, url, seen):
                return True
        return False

    out = []
    if map_error:
        out.append(("_tools/tg-saved/links-by-message.json", "карта адресов не прочитана: %s" % map_error))
    for d in PAGE_DIRS:
        folder = _os.path.join(root, "wiki", d)
        if not _os.path.isdir(folder):
            continue
        for fn in sorted(_os.listdir(folder)):
            if not fn.endswith(".md"):
                continue
            text = read(_os.path.join(folder, fn))
            fm, body = split_frontmatter(text)
            # Ищем имя артефакта только в прозе страницы: имя внутри ссылки на источник (и в списке
            # sources: шапки) — это нормальная отсылка к записи, а не утеря адреса.
            prose = re.sub(r"\[\[[^\]]*\]\]", " ", body or "")
            prose = re.sub(r"\[[^\]\n]*\]\([^)]*\)", " ", prose)
            # Технические имена с цифрами — это версии и заголовки (`advanced-tool-use-2025-11-20`),
            # а не название артефакта: из прозы их убираем, иначе проверка ловит свой же заголовок.
            prose = re.sub(r"`[^`]*\d[^`]*`", " ", prose)
            if not fm:
                continue
            sources = parse_tags(fm.get("sources"))

            # Запись-ссылка отдаёт адрес прямо в статью. Владелец 2026-09-19: «ссылаться на raw/, когда
            # там просто ссылка — плохой паттерн, вместо этого надо дать саму ссылку и описание».
            # Запись-ссылка — та, у которой, кроме адреса, содержания нет: заголовок «Ссылка на …»
            # или тело из одного адреса. Проверка узкая: имя артефакта тут не нужно, достаточно
            # самого факта отсылки в запись без ссылки.
            for src in sources:
                if not is_link_note(src):
                    continue
                addrs = addresses_of(src)
                if addrs and not any(present(body or "", u) for u in addrs):
                    out.append(f"{fn[:-3]}: источник-ссылка {src.split('/')[-1][:-3][:48]} — "
                               f"в статье нет её адреса: {addrs[0]}")

            urls = []
            for src in sources:
                urls += addresses_of(src)
            if not urls:
                continue
            for url in sorted(set(urls)):
                for nm in names_of(url):
                    key = nm.lower()
                    if len(key) < 5 or (nm.split("/")[-1].lower() in GENERIC and "/" not in nm):
                        continue
                    if (key in prose.lower() and not present(body or "", url)
                            and not address_reachable(fn[:-3], url)):
                        out.append(f"{fn[:-3]}: назван «{nm}», а адреса нет — {url}")
                        break
    return out[:60]


def check_process_meta_in_page(root):
    """§79. Пишем о предмете, а не о том, как составлялась страница.

    Владелец 2026-09-18: «надо добавить в правила стиля, что писать мета-замечания в текст статей нельзя.
    В статье надо писать о предмете статьи, а комментариям о сложностях процесса составления этой статьи,
    на чём там её механика держится, в статье не место». Пример, на котором правило поймали
    ([[task-handoff]]): «Материалом страницы эта механика держится на одном машинном описании статьи;
    самой статьи в корпусе нет» — притом что адрес статьи лежал в записи.

    Сила доказательства и его источник живут в шапке (`evidence`, `confidence`, `sources:`) и в разделе
    «Границы»; ремарки о том, как страница собрана, в тексте — дефект.
    """
    import os as _os
    META = re.compile(r"(?i)(материалом (?:этой )?страницы|источником (?:этой )?страницы"
                      r"|держится на машинн\w+ описан\w+|машинно разобранн\w+ описан\w+"
                      r"|добавлено при разборе ссылок|самой статьи в корпусе нет"
                      r"|в корпусе один раз и в пересказе|приводится здесь как"
                      # обороты, которыми страница рассказывает о покрытии источника вместо предмета
                      r"|(?:дана|дан|дано|приведен\w*|приведён\w*|описан\w*) (?:источником|заметкой) без"
                      # сводные дисклеймеры о силе доказательства: пользы не несут, символы раздувают,
                      # силу доказательства называет шапка (`evidence`, `confidence`) — владелец 2026-09-18
                      r"|(?:не измерения|а не измерение),? и приводятся здесь как"
                      r"|объяснения авторов, не измерения"
                      r"|как позиция, а не как факт"
                      # дисклеймер о породе материала: «это самоотчёт практика, а не результат замера»,
                      # «наблюдение практика, а не измерение», «нет независимой репликации» (владелец 2026-09-18:
                      # «типичные бессмысленные фразочки, которые засоряют статьи»)
                      r"|это самоотчёт"
                      r"|а не результат замера"
                      r"|,\s*а не измерение"
                      r"|нет независимой репликации"
                      r"|заметка (?:не объясняет|не раскрывает|не разбирает)"
                      r"|вклад в страницу только указательный"
                      r"|без разбора деталей"
                      # Страница рассказывает, из чего она собрана, а не о предмете (владелец 2026-09-19:
                      # «опять нашел мета-запись, которая описывает не предмет статьи, а то, как статья
                      # устроена… искусственно раздувает объем, затрудняя чтение»). Пример владельца:
                      # «В корпусе понятие держится двумя записями о стандартах…» в generative-ui.
                      # Владелец 2026-09-19, волна 2: дети сняли мета-ремарку «Страница держит роль и её механику»,
                      # но вписали такую же про корпус — «Корпусная рамка держит на этом же месте человеческий гейт».
                      # Активный залог в этой семье не был покрыт: было только «держится».
                      # Владелец 2026-09-19, волна 2: «Определение в корпусе принадлежит вендору и опирается на его
                      # собственные замеры» — «зря оставил, всё верно сработало, эта фраза не „законная", а очередной
                      # филлер». Поэтому залог не различаем: и «держится», и «держит», и «опирается».
                      r"|(?:понятие|термин|тема|слово|определение|материал|страница|раздел|вики|корпусная рамка|соседняя страница)\s[^.]{0,40}(?:держится|держит|опирается|собрана|состоит)"
                      r"|материал страницы\s*—"
                      r"|страница (?:опирается|собрана|состоит)"
                      r"|в корпусе представлен\w+ не одним документом"
                      r"|содержания[^.]{0,40}в корпусе нет"
                      r"|в корпусе (?:понятие|термин|тема)[^.]{0,40}(?:держится|представлен))")
    out = []
    # Страница болей по жанру пересказывает, что говорит заметка: её строки — это «симптом против того,
    # что пишет источник», и обороты вроде «заметка не разбирает» там не ремарка о сборке, а предмет строки.
    GENRE = {"pain-points-and-fixes"}
    for d in ("concepts", "comparisons", "entities", "queries"):
        folder = _os.path.join(root, "wiki", d)
        if not _os.path.isdir(folder):
            continue
        for fn in sorted(_os.listdir(folder)):
            if not fn.endswith(".md") or fn[:-3] in GENRE:
                continue
            body = split_frontmatter(read(_os.path.join(folder, fn)))[1] or ""
            for i, line in enumerate(body.split("\n"), 1):
                m = META.search(line)
                if m:
                    out.append(f"{fn[:-3]}:{i}: «{m.group(0)}» — {line.strip()[:90]}")
                    break
    return out[:60]


def check_section_filler(root):
    """§80. «Факты и цифры», «Спорное», «Границы и грабли» — только значимое, без отчёта о покрытии.

    Проверка идёт по тексту страницы целиком, а не по названным разделам: вода одинаково недопустима в теле.

    Владелец 2026-09-19: «большинство записей в разделах „факты и цифры", „спорное", „границы" — филлеры,
    от которых один вред (замусоривают контекст)… задуманы как разделы со значимыми находками, а стали
    переполнены водой». Пример, названный им: «Второго голоса о генеративном интерфейсе в корпусе нет…
    Числа, кроме названных выше, ни один источник страницы не даёт».

    Ловятся только однозначные обороты: сообщение об отсутствии голоса, о том, что «ни один источник не даёт»,
    о собственной сборке страницы. Сила доказательства живёт в шапке (`evidence`, `confidence`), а не в тексте.
    """
    FILLER = [
        (r"(?:второго|другого|иного|второй)\s+голос\w*[^.]{0,60}?\bнет\b",
         "сообщение об отсутствии второго голоса: сила доказательства живёт в шапке, а не в тексте"),
        (r"ни один[а]?\s+(?:источник|запись|заметк|страниц)\w*[^.]{0,60}?не\s+(?:даёт|дает|называет|приводит|сообщает)",
         "«ни один источник не даёт» — отчёт о покрытии вместо находки"),
        (r"кроме названных выше", "сводка «кроме названных выше» — вода"),
        (r"(?:определение|страница|раздел|материал страницы)[^.]{0,40}держится",
         "рассказ о том, на чём держится страница, а не о предмете"),
        (r"входит в страницу только|входит только адресом", "рассказ о том, как источник вошёл в страницу"),
        # Владелец 2026-09-19 (волна 2): строка «Отдельной строки этот спор в карте конфликтов не имеет:
        # ближайшая по предмету — шестая строка…» в cli — отчёт о покрытии карты вместо находки.
        (r"(?:отдельной|своей)\s+строк\w*[^.]{0,70}?\bнет\b|отдельной строки[^.]{0,40}не имеет",
         "отчёт о том, отдельная ли строка у спора в карте, — вместо находки"),
        (r"проверить нечем|проверить нечего|нечем проверить",
         "«проверить нечем» — отчёт о покрытии вместо находки"),
        (r"(?:её|ее|его)\s+содержание[^.]{0,40}не изложено|в заметках не изложено",
         "отчёт о том, что содержание источника не изложено"),
    ]
    issues = []
    vault = os.path.join(root, "wiki")
    pages = []
    for d in ("entities", "concepts", "comparisons", "queries"):
        full = os.path.join(vault, d)
        if not os.path.isdir(full):
            continue
        for fn in sorted(os.listdir(full)):
            if fn.endswith(".md"):
                pages.append((f"{d}/{fn}", read(os.path.join(full, fn))))
    for rel, text in pages:
        for pat, why in FILLER:
            for m in re.finditer(pat, text, re.IGNORECASE):
                line = text[:m.start()].count("\n") + 1
                issues.append(f"{rel}:{line}: «{m.group(0).strip()[:70]}» — {why}")
    return issues


def check_style_single_source(root):
    """§81. Требования к стилю живут в одном месте — в `_toolkit/style.md`.

    Владелец 2026-09-19: «я выписал все документы и проверки… получил 9… требования разбросаны, что-то здесь,
    что-то там. Хуже того, уже начали наслаиваться и дублировать друг друга. Это бардак, должен быть один
    источник правды, из которого берут требования к стилю все остальные инструменты».

    Ловим дословный повтор: фраза длиной от восьми слов, стоящая и в своде, и в другом служебном документе.
    Ссылаться на свод — можно, пересказывать его — нет.
    """
    import os as _os
    style = toolkit.script("style.md")
    if not _os.path.exists(style):
        return []
    def phrases(text):
        clean = re.sub(r"[`*#>|\-]", " ", text)
        clean = re.sub(r"\s+", " ", clean)
        w = re.findall(r"[А-Яа-яЁёA-Za-z0-9«»]+", clean)
        return {" ".join(w[i:i + 8]).lower() for i in range(len(w) - 7)}
    style_phrases = phrases(read(style))
    others = ["_toolkit/SCHEMA.md", "_toolkit/contract-v1.md", "_toolkit/ingest-wave.md", "_toolkit/lint-pass.md",
              "_toolkit/wave-order.md", "_toolkit/rules-hook.md"]
    out = []
    for rel in others:
        path = _os.path.join(root, *rel.split("/"))
        if not _os.path.exists(path):
            continue
        for p in phrases(read(path)) & style_phrases:
            if len(p) < 45:
                continue
            line = next((n for n, l in enumerate(read(path).split("\n"), 1)
                         if len(p.split()) and p.split()[0] in l.lower() and p.split()[-1] in l.lower()), 0)
            out.append(f"{rel}:{line}: фраза из свода дословно повторяется — ссылайся на `_toolkit/style.md`: «{p[:70]}…»")
    return out[:60]



INDEX_SECTIONS = {"concepts": "Concepts", "entities": "Entities", "comparisons": "Comparisons",
                  "queries": "Queries", "_meta": "Служебное (_meta)"}



_NOT_DELIVERED_CACHE: dict[tuple[str, str], bool] = {}


def not_delivered(root, path):
    # Путь может прийти с завершающей косой (`.venv/` или `.venv-asr/`) — тогда шаблон их не ловил,
    # и служебный документ в поставке объявлялся ссылкой на несуществующее. Косая тут — форма записи, не часть пути.
    path = path.rstrip("/" + os.sep)
    """Путь, которого нет в поставке: архив, игнор или локальные данные владельца.

    Зачем: в свежей копии поставки нет ни архива, ни игнорируемых папок (`_staging/telegram/`, `.venv`, звук,
    `handover`), а служебные документы на них ссылаются — и каждая такая ссылка читалась как обрыв. Разница
    между «нет, потому что не поставляется» и «нет, потому что сломано» обязана быть видна сторожам, иначе
    посторонний, скачав проект, получает тридцать девять находок вместо пяти.
    """
    if path.startswith(("_staging/archive/", "archive/")) or "/archive/" in path:
        return True
    key = (root, path)
    if key in _NOT_DELIVERED_CACHE:
        return _NOT_DELIVERED_CACHE[key]
    verdict = False
    try:
        r = subprocess.run(["git", "-C", root, "check-ignore", "--no-index", "-q", path],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30, check=False)
        verdict = r.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        verdict = False
    if not verdict:
        # Запасной путь: копия поставки может быть распакована из архива и не иметь `.git` вовсе, а вопрос
        # «поставляется ли этот путь» обязан иметь ответ и там. Читаем правила из `.gitignore` руками.
        verdict = _ignored_by_patterns(root, path)
    _NOT_DELIVERED_CACHE[key] = verdict
    return verdict


def _ignored_by_patterns(root, path):
    import fnmatch
    rel = path.replace("\\", "/").lstrip("/")
    verdict = False
    for gi in (os.path.join(root, ".gitignore"), toolkit.script(os.path.join("tools", "tg-saved", ".gitignore"), root)):
        if not os.path.exists(gi):
            continue
        for line in read(gi).split("\n"):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            negate = line.startswith("!")
            pat = line[1:].strip() if negate else line
            pat = pat.lstrip("/")
            if not pat:
                continue
            hit = (fnmatch.fnmatch(rel, pat) or rel == pat.rstrip("/")
                   or fnmatch.fnmatch(rel, pat.rstrip("/") + "/*")
                   or rel.startswith(pat.rstrip("/") + "/")
                   or fnmatch.fnmatch(os.path.basename(rel), pat.rstrip("/")))
            if hit:
                verdict = not negate
    return verdict


def check_index_structure(root):
    """§3 (расширение). Индекс сходится с деревом в обе стороны, по секциям и по итогам.

    Владелец 2026-09-21: «сделай чтоб расхождение индекса с деревом проверялось машинно». Приёмка глазами
    постороннего нашла дрейф, которого не ловил никто: четыре страницы-понятия стояли в разделе Entities,
    двух служебных страниц не было вовсе, а итог обещал 92 содержательных при 90 на диске.

    Прежняя проверка смотрела только одну сторону — «страница есть, а в индексе её нет». Здесь появляются
    вторая сторона (строка без файла), место строки (секция против папки), повтор строки и числа итога.
    """
    issues = []
    vault = os.path.join(root, "wiki")
    idx = os.path.join(vault, "index.md")
    if not os.path.exists(idx):
        return ["wiki/index.md: файла нет"]
    text = read(idx)
    files = {}
    for d in INDEX_SECTIONS:
        folder = os.path.join(vault, d)
        files[d] = {fn[:-3] for fn in os.listdir(folder) if fn.endswith(".md")} if os.path.isdir(folder) else set()
    section = None
    rows = {}
    for i, line in enumerate(text.split("\n"), 1):
        if line.startswith("## "):
            section = line[3:].strip()
            continue
        m = re.match(r"- \[\[([^\]|#]+)", line)
        if m:
            slug = m.group(1).strip().split("/")[-1]
            rows.setdefault(slug, []).append((section, i))
    for d, secname in INDEX_SECTIONS.items():
        for slug in sorted(files[d]):
            if slug not in rows:
                issues.append("wiki/index.md: страницы %s нет в индексе" % slug)
                continue
            places = [sec for sec, _ in rows[slug]]
            if secname not in places:
                issues.append("wiki/index.md:%d: строка %s стоит в разделе «%s», а страница лежит в %s"
                              % (rows[slug][0][1], slug, places[0] or "?", d))
            if len(rows[slug]) > 1:
                issues.append("wiki/index.md:%d: строка %s повторена" % (rows[slug][1][1], slug))
    page_slugs = {s for d in files for s in files[d]}
    for slug, places in rows.items():
        secs = [sec for sec, _ in places]
        if any(sec in [INDEX_SECTIONS[d].split()[0] for d in INDEX_SECTIONS] or
               sec in [INDEX_SECTIONS[d] for d in INDEX_SECTIONS] for sec in secs):
            if slug not in page_slugs:
                issues.append("wiki/index.md:%d: строка %s стоит в разделе страниц, а файла нет"
                              % (places[0][1], slug))
    # Итоги: строка «Total pages: N = concepts A + entities B + comparisons C + queries D + _meta E».
    m = re.search(r"Total pages:\s*(\d+)\s*=\s*concepts\s*(\d+)\s*\+\s*entities\s*(\d+)\s*\+\s*comparisons\s*(\d+)\s*\+\s*queries\s*(\d+)\s*\+\s*_meta\s*(\d+)", text)
    if not m:
        issues.append("wiki/index.md: итог не читается машиной — ждём строку "
                      "«Total pages: N = concepts A + entities B + comparisons C + queries D + _meta E»")
    else:
        want = [int(m.group(i)) for i in range(1, 7)]
        real = [sum(len(files[d]) for d in files), len(files["concepts"]), len(files["entities"]),
                len(files["comparisons"]), len(files["queries"]), len(files["_meta"])]
        names = ["всего страниц", "понятий", "сущностей", "сравнений", "разборов вопросов", "служебных"]
        for n, w, r in zip(names, want, real):
            if w != r:
                issues.append("wiki/index.md: итог говорит %s %d, а на диске %d" % (n, w, r))
    return issues[:40]




SERVICE_DOCS = ("README.md", "CONTRIBUTING.md")
SERVICE_DOC_DIRS = ("_toolkit", "_staging")


def check_service_docs(root):
    """§82. Служебный документ не может быть испорчен записью: ни одна содержательная строка не повторяется десятками раз.

    Владелец 2026-09-21: приёмка глазами постороннего нашла, что README раздут до 2,26 МБ — один пункт про смотрелки
    повторён в нём 4815 раз, а разделы и таблица слоёв при этом пропали. Порча прошла в коммит и не была поймана ничем:
    сторож смотрел на вики, реестры и ссылки, но не на сам служебный документ. Правило ловит именно этот класс —
    дублирование строки, которое и есть следствие сбойной записи, — и молчит на нормальных документах.
    """
    issues = []
    paths = [os.path.join(root, n) for n in SERVICE_DOCS]
    for directory in SERVICE_DOC_DIRS:
        if os.path.isdir(os.path.join(root, directory)):
            paths += sorted(glob.glob(os.path.join(root, directory, "*.md")))
    for path in paths:
        if not os.path.exists(path):
            continue
        lines = [l.strip() for l in read(path).split("\n")]
        counts = {}
        for l in lines:
            if len(l) >= 40:
                counts[l] = counts.get(l, 0) + 1
        hot = sorted(((n, l) for l, n in counts.items() if n > 20), reverse=True)
        for n, l in hot[:3]:
            issues.append("%s: строка повторена %d раз — похоже на сбойную запись: %s"
                          % (os.path.relpath(path, root).replace("\\", "/"), n, l[:80]))
    return issues




def check_ignored_but_tracked(root):
    """§83. Файл, покрытый .gitignore, не отслеживается.

    Владелец 2026-09-21: «надо сделать в .gitignore и разобраться, почему обещалось, что они не отслеживаются,
    а на самом деле отслеживались». Причина — правило git: .gitignore действует только на неотслеживаемые файлы.
    Пять сводок (`lint-summary` 09-11…09-14, `style-audit` 09-16) попали в историю раньше своих правил и остались
    отслеживаемыми, хотя .gitignore обещал обратное: правило появилось 15 и 17 сентября, а файлы — 11…16-го.
    Проверка ловит этот класс: путь в индексе, который попадает под шаблон .gitignore.

    Границы: работает только внутри репозитория git; в копии без .git (аттестация канарейками) молчит, поэтому
    канарейки у правила нет — как и у ветки «мостик отстал на N коммитов».
    """
    if not os.path.exists(os.path.join(root, ".git")):
        return []
    try:
        top = subprocess.run(["git", "-C", root, "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60, check=False).stdout.strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        return ["git: не удалось получить корень: %s" % exc]
    if not top:
        return []
    try:
        tracked = subprocess.run(["git", "-C", top, "ls-files", "-z"],
                                 capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120, check=False).stdout.split("\0")
    except (OSError, subprocess.TimeoutExpired) as exc:
        return ["git: не удалось получить отслеживаемые файлы: %s" % exc]
    tracked = [t for t in tracked if t]
    if not tracked:
        return []
    ignored = {}
    chunk = 400
    for i in range(0, len(tracked), chunk):
        part = tracked[i:i + chunk]
        r = subprocess.run(["git", "-C", top, "check-ignore", "--no-index", "--stdin", "-z"],
                           input="\0".join(part), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120, check=False)
        for path in r.stdout.split("\0"):
            if path:
                ignored[path] = True
    return ["%s: путь покрыт .gitignore, но отслеживается — сними с индекса `git rm --cached`" % p
            for p in sorted(ignored)][:20]




def domain_register_path(root, key):
    """Путь объявленного экземпляром реестра; не объявил — None (проверка не применима)."""
    import os as _os
    import sys as _sys
    _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
    try:
        import domain as _domain
    except ImportError:
        return None
    return _domain.register_path(root, key)


def toolkit_script(name):
    """Файл механизма: лежит рядом с линтером, где бы ни был развёрнут проект (правило §90)."""
    return toolkit.script(name)


def domain_declared(root):
    """Объявлен ли домен у этого экземпляра. Спрашивать экземпляр, а не знать за него, — правило механизма:
    у постороннего своя тема, свои корпуса и свои реестры (разбор инвентаризации 2026-09-22)."""
    import os as _os
    import sys as _sys
    _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
    try:
        import domain as _domain
    except ImportError:
        return False
    return bool(_domain.load(root))


def check_domain_leak(root):
    """§91. Домен экземпляра не живёт в файлах механизма.

    Слова домена объявлены экземпляром (`skill_markers` в `_staging/domain.local.tsv`): у этого экземпляра —
    имя его вики, карта конфликтов, методология практика, реестр передач. Пока они встречаются в скриптах и
    документах механизма, поставка несёт чужую тему: посторонний видит слова, которых не понимает, и не может
    отличить их от обязательных. Число — мера готовности к разделению репозиториев: после разделения ноль.
    Второй ключ запрета — `instance_forbidden`: слова и пути своего экземпляра, которых в поставке быть
    не должно. Оба ключа читаются одинаково — подстрокой в тексте и в имени файла.

    Проверяются файлы механизма: скрипты и служебные документы. Материал экземпляра (`raw/`, `wiki/`,
    `_staging/` вне скриптов, журнал) не проверяется — там домен и должен жить.

    Вторая половина проверки: файлы механизма не называют инструментов экземпляра (`_staging/local/`). Слова
    домена можно вынести из текста, но оставить имя скрипта в протоколе — и посторонний всё равно увидит
    чужой архив в поставке. Найдено 2026-09-24: протокол волны называл два инструмента чужого архива.
    """
    import glob as _glob
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    try:
        import domain as _domain
    except ImportError:
        return []
    # Слова домена — и метки навыков, и темы экземпляра: `topics` объявлены наравне с `skill_markers`, и
    # если их не проверять, объявленное слово живёт в файле механизма незамеченным: тема экземпляра была
    # зашита в скрипт механизма, а сторож этого не видел (найдено 2026-09-24).
    markers = _domain.markers(root) + _domain.topics(root) + _domain.forbidden(root)
    if not markers:
        return []
    hits = []
    # Каталоги и рабочие документы проекта: описывают всё, что есть в проекте, включая инструменты экземпляра
    # и его слова, — иначе перестали бы быть описями. В поставку они не едут, поэтому и из проверки исключены.
    CATALOGUES = ("_toolkit/project_map.py", "_toolkit/scripts-inventory.md", "_staging/scripts-layout.md",
                  "_staging/project-map.md", "_staging/split-plan.md")
    paths = sorted(_glob.glob(os.path.join(os.path.dirname(os.path.abspath(__file__)), "*.py")))
    # Корневые входы — тоже файлы механизма: их читает всякий, кто открыл репозиторий, включая агентские
    # сессии (AGENTS.md читает OpenCode). Список держится поимённо, поэтому новый вход надо вносить сюда же:
    # проверено 2026-09-24 — слово домена в AGENTS.md сторожа не будило, файл жил вне проверки.
    for name in ("CONTRIBUTING.md", "README.md", "AGENTS.md"):
        paths.append(os.path.join(root, name))
    paths += sorted(_glob.glob(os.path.join(root, "_toolkit", "*.md")))
    # Инструменты выгрузки — тоже механизм: без них папка надстройки оставалась вовсе без проверки, и скрипт
    # с именем чужого корпуса жил там незамеченным (найдено 2026-09-24).
    paths += sorted(_glob.glob(os.path.join(root, "_toolkit", "tools", "tg-saved", "*.py")))
    paths += sorted(p for p in _glob.glob(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                            "skills", "**", "*"), recursive=True)
                    if os.path.isfile(p))
    for path in paths:
        if not os.path.exists(path):
            continue
        try:
            rel = os.path.relpath(path, root).replace("\\", "/")
        except ValueError:
            rel = os.path.basename(path)
        # Не считаются: журнал (запись событий), файлы экземпляра (`.local.`), его материал (корпус, страницы,
        # рабочая область) и машинные выводы — мостик, карта, панели: их содержимое приходит из истории и данных
        # экземпляра, а не из текста механизма.
        if (rel in ("log.md",) or ".local." in rel or rel.startswith(
                ("raw/", "wiki/", "_staging/audit/", "_staging/archive/", "_staging/temp/", "_staging/stance/",
                 "_staging/extracts", "_staging/local/"))
                or rel.endswith(("bridge.md", "project-map.md", "project-map.html", "dashboard.html"))
                or rel.endswith("-status.html")):
            continue
        # Каталоги называют всё, что есть в проекте, — и словами тоже (карта, описи). Иначе опись перестала бы
        # быть описью; сам предмет проверки — файлы механизма, а не их перечни.
        if rel in CATALOGUES:
            continue
        try:
            text = read(path)
        except UnicodeDecodeError:
            text = ""
        except OSError as exc:
            hits.append(f"{rel}: файл не прочитан: {exc}")
            text = ""
        # Имя файла механизма — тоже его текст: скрипт с именем чужого корпуса виден постороннему раньше,
        # чем он откроет содержимое.
        in_name = sorted({m for m in markers if m in os.path.basename(rel)})
        if in_name:
            hits.append(f"{rel}: слово домена в имени файла ({', '.join(in_name)})")
        found = sorted({m for m in markers if m in text})
        if found:
            hits.append(f"{rel}: {', '.join(found)}")
        # Имена инструментов экземпляра: папка его области, файлы в ней. Каталоги (карта структуры, описи
        # скриптов) называют всё, что есть в проекте, — иначе они перестали бы быть описями; поэтому из этой
        # половины проверки они исключены, а первая половина (слова домена) их проверяет как обычно.
        tools_dir = toolkit.local(root)
        if rel not in CATALOGUES and os.path.isdir(tools_dir):
            local_names = [n for n in sorted(os.listdir(tools_dir))
                           if n.endswith(".py") and n in text]
            if local_names:
                hits.append(f"{rel}: называет инструмент экземпляра ({', '.join(local_names)})")
    if not hits:
        return []
    return [f"домен экземпляра в файлах механизма ({len(hits)}): " + "; ".join(hits[:6])
            + (" и ещё" if len(hits) > 6 else "")]


def check_toolkit_layout(root):
    """§90. Инструмент ищет свой файл рядом с собой, а не через корень проекта.

    Пока механизм и экземпляр лежат в одной папке (`_staging/`), поиск своего файла через корень проекта
    работает. После разделения инструменты переезжают в `_toolkit/`, а `_staging/` остаётся
    рабочей областью владельца — и такая строка начинает искать свой файл в чужом месте, молча не находить и
    падать по дороге. Правило одно: свой файл — `toolkit.script("имя.py")`, чужая область — `toolkit.area(root, …)`.
    """
    import glob as _glob
    bad = []
    # Мера одна с описью скриптов (`_toolkit/scripts_layout.py`): иначе сторож и опись расходятся, и «ноль»
    # в стороже ничего не значит (замечание второй сессии 2026-09-22).
    import scripts_layout as _sl
    pat = _sl.TOOLKIT_VIA_ROOT
    for path in sorted(_glob.glob(os.path.join(os.path.dirname(os.path.abspath(__file__)), "**", "*.py"), recursive=True)):
        name = os.path.relpath(path, os.path.dirname(os.path.abspath(__file__))).replace(os.sep, "/")
        if name in ("scripts_layout.py",):
            continue          # сама опись: её собственный образец правила не в счёте
        try:
            text = read(path)
        except (OSError, UnicodeError) as exc:
            bad.append(f"{name}: файл не прочитан: {exc}")
            text = ""
        hits = len(pat.findall(text))
        if hits:
            bad.append(f"{name}: {hits} мест(о) ищут свой .py через корень проекта — "
                       f"нужен `toolkit.script(...)` (правило в `_toolkit/toolkit.py`)")
    return bad


def check_instance_paths(root):
    """§92. Пути областей экземпляра строятся только через toolkit."""
    import glob as _glob
    areas = {"_staging", "wiki", "raw", "local"}
    exempt = {"toolkit.py", "scripts_layout.py", "lint_wiki.py", "canary_test.py"}
    hits = []

    class Visitor(ast.NodeVisitor):
        def __init__(self, rel):
            self.rel = rel
            self.found = []

        def visit_Call(self, node):
            if (isinstance(node.func, ast.Attribute) and node.func.attr == "join"
                    and isinstance(node.func.value, ast.Attribute)
                    and node.func.value.attr == "path"):
                literals = [x.value for x in node.args if isinstance(x, ast.Constant) and isinstance(x.value, str)]
                found = [x for x in literals if x in areas]
                if found:
                    self.found.append((node.lineno, ", ".join(found)))
            if (isinstance(node.func, ast.Attribute) and node.func.attr == "area"
                    and isinstance(node.func.value, ast.Name) and node.func.value.id == "toolkit"):
                literals = [x.value for x in node.args if isinstance(x, ast.Constant) and isinstance(x.value, str)]
                found = [x for x in literals if x.endswith(".py") and ".local." not in x]
                if found:
                    self.found.append((node.lineno, ", ".join(found)))
            self.generic_visit(node)

    paths = sorted(_glob.glob(os.path.join(os.path.dirname(os.path.abspath(__file__)), "**", "*.py"), recursive=True))
    for path in paths:
        name = os.path.basename(path)
        if name in exempt or ".local." in name:
            continue
        try:
            rel = os.path.relpath(path, root).replace("\\", "/")
        except ValueError:
            rel = os.path.basename(path)
        try:
            visitor = Visitor(rel)
            visitor.visit(ast.parse(read(path), filename=path))
        except (OSError, UnicodeError, SyntaxError) as ex:
            hits.append(f"{rel}: проверка §92 не прочитала или разобрала файл: {ex}")
            continue
        hits.extend(f"{visitor.rel}:{line}: {areas_text} — нужен toolkit API" for line, areas_text in visitor.found)
    return hits


def _without_comments(text):
    """Текст без комментариев: комментарий не выключает ни образец, ни освобождение.

    Слово в комментарии — тот же выключатель, что имя канарейки: проверка обязана видеть код,
    а не типографский шум вокруг него. Разбирается токенизатором, а не срезом по первому знаку:
    знак внутри строкового литерала комментарием не является. Номера строк сохраняются.
    """
    import io as _io
    import tokenize as _tok
    try:
        toks = list(_tok.generate_tokens(_io.StringIO(text).readline))
    except (SyntaxError, _tok.TokenError, IndentationError):
        return None
    lines = text.splitlines()
    for tok in toks:
        if tok.type != _tok.COMMENT:
            continue
        (srow, scol), (erow, ecol) = tok.start, tok.end
        if srow == erow and 1 <= srow <= len(lines):
            line = lines[srow - 1]
            lines[srow - 1] = line[:scol] + " " * max(0, ecol - scol) + line[ecol:]
    return lines


def check_external_reads(root):
    """§95. Каждое чтение вне поставки объявлено, и ни одно из них не является измерением.

    Датчик границы: сканирует весь код механизма на чтения домашнего каталога и сверяет с таблицей
    объявлений `_toolkit/external-reads.tsv` — в обе стороны. Чтение без объявления — находка;
    объявление без чтения в файле — протухшее разрешение, тоже находка. Новое неразрешённое чтение
    станет красным само, без новой строки в этой функции.
    Образцы покрывают четыре написания: домашний путь в вызове, дом через pathlib, переменные
    окружения с путём и корень из переменной окружения. Абсолютные литералы путей специально не ловятся:
    они дали бы ложняки на адресах в комментариях. Временные каталоги отдельной скидки не имеют:
    строка с образцом идёт к объявлениям, как все, — с пометкой, что это царапка, а не чтение данных.
    """
    import glob as _glob
    table = toolkit_script("external-reads.tsv")
    rows = []
    try:
        lines = read(table).splitlines()
    except OSError as exc:
        return [f"нет таблицы объявлений external-reads.tsv: {exc}"]
    for line in lines[1:]:
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) >= 2 and parts[0].strip():
            rows.append((parts[0].strip(), parts[1].strip(),
                         parts[2].strip() if len(parts) > 2 else ""))
    base = os.path.dirname(os.path.abspath(__file__))
    pats = [re.compile(r"expanduser\s*\(\s*[\"']~/"),
            re.compile(r"Path\.home\s*\(\s*\)"),
            re.compile(r"expandvars\s*\("),
            re.compile(r"os\.environ\.get\(\s*[\"'](?:LOCALAPPDATA|APPDATA|HOMEPATH|USERPROFILE|HOME)[\"']")]
    hits = []
    for path in sorted(_glob.glob(os.path.join(base, "**", "*.py"), recursive=True)):
        try:
            rel = os.path.relpath(path, root).replace("\\", "/")
        except ValueError:
            rel = os.path.basename(path)
        try:
            text = read(path)
        except OSError as exc:
            hits.append(f"{rel}: файл не прочитан: {exc}")
            continue
        stripped = _without_comments(text)
        for n, line in enumerate(stripped if stripped is not None else text.splitlines(), 1):
            if not any(p.search(line) for p in pats):
                continue
            if not any(f == rel and sample in line for f, sample, _why in rows):
                hits.append(f"{rel}:{n}: чтение вне корня без объявления: {line.strip()[:100]}")
    for f, sample, _why in rows:
        target = os.path.join(root, *f.split("/")) if not os.path.isabs(f) else f
        try:
            text = read(target)
        except OSError:
            hits.append(f"{f}: объявление есть, а файла нет")
            continue
        if sample not in text:
            hits.append(f"{f}: объявление без кода: образец отсутствует в файле")
    return hits


def check_domain_declared(root):
    """§89. Объявление домена экземпляра заполнено — или сказано, что проверки домена не применимы.

    Проверки домена (§33 боли, §36 удалённая копия, §47 карта конфликтов, §48 форма болей, §49 реестр
    инструментов, §75 карта адресов) молчат, когда экземпляр не объявил своих регистров: требовать чужие
    страницы нельзя. Но молчание о самой неприменимости недопустимо — пустое место не должно выглядеть как
    успех, поэтому строка одна и говорит, чего не хватает и где взять форму.
    """
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    try:
        import domain as _domain
    except ImportError:
        return ["нет `_toolkit/domain.py`: объявление домена не читается"]
    if _domain.load(root):
        # Объявление есть, но реестры в нём не названы: проверки домена не применимы, и это состояние
        # называется вслух — «нет реестров» и «нет объявления» разные вещи (карточка пользователя, 2026-09-22).
        # Ключ `registers` есть и пуст — это сказано «своих реестров у меня нет», и проверки домена просто не
        # применимы. Ключа нет вовсе — объявление не заполнено до конца, и об этом стоит сказать вслух.
        if "registers" not in _domain.load(root):
            return ["объявление домена заполнено, но реестры в нём не названы (`registers`): проверки домена "
                    "не применимы — боли (§33), форма болей (§48), карта конфликтов (§47), реестр инструментов "
                    "(§49), карта адресов (§75), удалённая копия (§36). Пустая строка `registers` — это «своих "
                    "реестров нет», и она молчит законно"]
        return []
    return ["объявление домена не заполнено (`_staging/domain.local.tsv`): проверки домена не применимы — "
            "боли (§33), форма болей (§48), карта конфликтов (§47), реестр инструментов (§49), карта адресов "
            "(§75), реестр удалённых копий (§36). Форма и ключи — `_toolkit/domain.local.example.tsv`"]


def check_audit_references(root):
    """§84. Выход в `_staging/audit/` держится ссылкой из поставки.

    Решение владельца 2026-09-21: файл в `_staging/audit/` держится, пока на него ссылается что-то вне выходов
    проходов — код и сторож, реестр (долг, правила), документ поставки (SCHEMA, README, журнал, мостик, карта,
    свод стиля) или доказательство пункта долга. Файл, который называют только другие выходы того же прохода,
    — отработавшее: его место в `_staging/archive/audit/`, переносит его агент при закрытии волны, в том же
    коммите. Проверка называет кандидатов и сама ничего не переносит.

    Ссылкой считается имя файла или его стем (от шести знаков, чтобы не ловить обрывки) в любом файле проекта
    вне `_staging/audit/`, `_staging/archive/`, `_staging/temp/`, `raw/` и `wiki/`. Поиск идёт по дереву, а не
    по индексу git: в распакованной поставке `.git` нет, и правило должно работать там же.
    """
    audit = os.path.join(root, "_staging", "audit")
    if not os.path.isdir(audit):
        return []
    skip_top = ("_staging/audit", "_staging/archive", "_staging/temp", "_staging/telegram",
                "raw", "wiki", ".git", ".obsidian")
    skip_names = ("__pycache__", ".venv", ".venv-asr", "models", "node_modules", "export")
    sources = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in skip_names]
        rel = os.path.relpath(dirpath, root).replace(os.sep, "/")
        rel = "" if rel == "." else rel
        if any(rel == t or rel.startswith(t + "/") for t in skip_top):
            continue
        for fname in filenames:
            sources.append(os.path.join(dirpath, fname))
    texts = []
    read_errors = []
    for src in sources:
        try:
            texts.append(read(src))
        except UnicodeDecodeError:
            texts.append("")
        except OSError as exc:
            read_errors.append("%s: файл не прочитан: %s" % (os.path.relpath(src, root), exc))
    corpus = "\n".join(texts)
    # Сторож часто собирает имя файла образцом: `lint-eye-*.json`, `number-check-%s.json`, `canary-results-{today}.json`.
    # Литерального имени в коде тогда нет, и такой файл — живой вход сторожа, а не отработавшее. Семейство имени
    # (часть до даты) считаем живым, если код обращается к нему с подстановкой.
    code_lines = [ln for text in texts for ln in text.split("\n")]

    def live_template(family):
        if len(family) < 4:
            return False
        pat = re.compile(re.escape(family) + r"[-_]?(\*|\{|\%s|\%d|\%\(\w+\)s)")
        return any(pat.search(ln) for ln in code_lines)

    candidates = []
    for dirpath, dirnames, filenames in os.walk(audit):
        for fname in filenames:
            abs_path = os.path.join(dirpath, fname)
            rel = os.path.relpath(abs_path, root).replace(os.sep, "/")
            if not_delivered(root, rel):
                continue
            stem = os.path.splitext(fname)[0]
            if fname in corpus or (len(stem) >= 6 and stem in corpus):
                continue
            family = re.sub(r"[-_]?(\d{4}(\-\d{2}-\d{2})?|\d+).*$", "", stem)
            if live_template(family):
                continue
            candidates.append(rel)
    return read_errors + ["%s: не назван вне выходов проходов — кандидат в `_staging/archive/audit/`" % c
                          for c in sorted(candidates)]



README_CEILING = 40          # §85: потолок вывески (решение владельца 2026-09-21)
CONTRIBUTING_CEILING = 180   # §85: потолок руководства — туда уезжают процедуры (поднят по замеру 2026-09-22: раздел «Окружение»)


def check_readme_ceiling(root):
    """§85. Входные файлы не растут: у вывески и у руководства есть потолки.

    Решение владельца 2026-09-21: в одном файле сидели два читателя — тот, кто открыл репозиторий, и тот, кто в нём
    работает, — и рост читался как распухание входа. Отсюда разделение: `README.md` — вывеска с указателями
    (предел 40 строк), `CONTRIBUTING.md` — порядок чтения, запуск, круг правки, проверки (предел 120). Знание,
    которому не хватает места, уезжает в свой файл: правила — в SCHEMA, структура — в карту.
    """
    limits = (("README.md", README_CEILING), ("CONTRIBUTING.md", CONTRIBUTING_CEILING))
    issues = []
    for name, limit in limits:
        path = os.path.join(root, name)
        if not os.path.exists(path):
            issues.append("%s: файла нет" % name)
            continue
        lines = len(read(path).split("\n"))
        if lines > limit:
            issues.append("%s: %d строк при потолке %d — уводи знание в свой файл, а сюда оставляй ссылку"
                          % (name, lines, limit))
    return issues

def check_theses_digest(root):
    r"""§86. Свод тезисов полон: каждая страница хранилища названа в нём ровно один раз.

    Зачем: свод — вход смыслового прохода («свод тезисов плюс источник целиком»), и его полнота была рельсами
    только внутри инструмента. Случай 2026-09-21: `stance_context.py` проверял полноту шаблоном `\S+`, а у страниц
    входящего потока владельца имена с пробелами — свод не сходился, `--write` молча отказывался писать, и метод был
    недоступен никому. Инструмент починен, но результата никто не проверял: правка свода руками или его устаревание
    проходили незамеченными. Проверка зеркалит рельсы инструмента: страницы хранилища, кроме зеркала источников и
    служебных, обязаны быть названы в своде — каждая ровно один раз.
    """
    import glob as _glob
    digest = os.path.join(root, "_staging", "stance", "_wiki-theses.md")
    if not os.path.exists(digest):
        # Страниц нет — значит собирать нечего, и проверять нечего: это свежий экземпляр, а не поломка.
        # Команда сборки на пустом хранилище так и говорит («не нашёл ни одной страницы вики»), и повторять
        # её требованием к постороннему нельзя (находка пробы 2026-09-22).
        if not any(f.endswith(".md") and f != "index.md" for f in
                   (os.listdir(os.path.join(root, "wiki")) if os.path.isdir(os.path.join(root, "wiki")) else [])):
            return []
        return ["нет свода тезисов `_staging/stance/_wiki-theses.md` — собери: "
                "`python3 _toolkit/stance_context.py --wiki . --write`"]
    vault = os.path.join(root, "wiki")
    files = []
    for path in sorted(_glob.glob(os.path.join(vault, "**", "*.md"), recursive=True)):
        rel = os.path.relpath(path, root).replace(os.sep, "/")
        if "/sources/" in rel or "/_meta/" in rel:
            continue
        files.append(rel)
    seen = re.findall(r"(?m)^## (wiki/.+\.md)$", read(digest))
    issues = []
    missing = sorted(set(files) - set(seen))
    extra = sorted(set(seen) - set(files))
    dups = sorted({x for x in seen if seen.count(x) > 1})
    if missing:
        head = ", ".join(missing[:5]) + (" и ещё %d" % (len(missing) - 5) if len(missing) > 5 else "")
        issues.append(f"свод тезисов не называет страниц: {head}")
    if extra:
        issues.append("свод тезисов называет страницы, которых нет: " + ", ".join(extra[:5]))
    if dups:
        issues.append("в своде тезисов страницы повторяются: " + ", ".join(dups[:5]))
    return issues



def _service_docs(root):
    """Служебные документы проекта: корневые и лежащие прямо в `_toolkit/`.

    Не служебные по жанру: `log.md` (это хроника по определению), сгенерированные `project-map.md`,
    `bridge.md`, и всё, что лежит в `temp/`, `audit/`, `archive/`, `stance/`.
    """
    docs = []
    for name in ("README.md", "CONTRIBUTING.md"):
        path = os.path.join(root, name)
        if os.path.exists(path):
            docs.append(name)
    mechanism = os.path.join(root, "_toolkit")
    if os.path.isdir(mechanism):
        for name in sorted(os.listdir(mechanism)):
            if name.endswith(".md") and name not in ("project-map.md", "bridge.md"):
                docs.append("_toolkit/" + name)
    staging = os.path.join(root, "_staging")
    if os.path.isdir(staging):
        for name in sorted(os.listdir(staging)):
            if name.endswith(".md") and name not in ("project-map.md", "bridge.md"):
                docs.append("_staging/" + name)
    for rel in ("_staging/ingest-input/README.md", "_staging/audit/README.md",
                "_toolkit/tools/tg-saved/README.md", "_toolkit/fixture/README.md"):
        path = os.path.join(root, rel)
        if os.path.exists(path):
            docs.append(rel)
    return docs


# Открыватель разбора случая: маркер состоявшегося события плюс дата. Якоря на начало строки нет и разметка
# не важна — разбор живёт и в строке таблицы, и в жирном начале абзаца, и это ловилось дважды (замер 2026-09-22).
CASE_OPENER = re.compile(r"(?:Снято|Снят|Сняты|Проверено|Проверены|Проверена|Повод|Живая проверка|Урок|Разовая|"
                         r"Прецедент|Так было|Так нашл|Так вышло|Первая волна|Появилась после|Заведён|Переехала|"
                         r"Откуда взялась)"
                         r"[^\n]{0,40}?20\d\d-\d\d-\d\d")
CASE_QUOTE = re.compile(r"(?:владелец|Владелец)[^»«.]{0,40}20\d\d-\d\d-\d\d[^«]{0,60}«")


def check_service_doc_chronicle(root):
    """§87. Служебный документ держит требования, а не хронику.

    Зачем: правило «служебный документ пишется как инструкция» было в реестре без сторожа, и прецеденты дважды
    возвращались в `style.md` после чистки. Находка — строка служебного документа, где правилом служит
    состоявшийся случай: маркер события вместе с датой («Снято …», «Проверено …», «Живая проверка …», «Урок …»,
    «Разовая …», «Прецедент …», «Заведён …», «Переехала …») либо дата вместе с цитатой владельца как
    формулировка. Разборы живут в `log.md`; в документе остаётся требование со ссылкой на запись.

    Окно расширено по замеру смыслового прохода 2026-09-22: якоря на начало строки нет (разбор живёт и в строке
    таблицы), разметка не важна (жирное начало абзаца), маркеров больше — прежнее окно пропускало восемь
    разборов в шести документах, а список документов дополнен теми, что лежат вне `_staging/` первого уровня
    (обвязка выгрузки, слой аудита, папка отбора, фикстура). Канарейки: №168 (прежняя форма), №176 (документ вне
    списка), №177 (жирная разметка и середина строки).
    """
    issues = []
    for rel in _service_docs(root):
        for num, line in enumerate(read(os.path.join(root, rel)).split("\n"), 1):
            if CASE_OPENER.search(line) or CASE_QUOTE.search(line):
                snippet = line.strip()
                if len(snippet) > 90:
                    snippet = snippet[:90] + "…"
                issues.append(f"{rel}:{num} — разбор случая в служебном документе: {snippet}")
    return issues


SCHEMA_CEILING = 400


def check_schema_boundary(root):
    path = toolkit.script("SCHEMA.md", root)
    if not os.path.exists(path):
        return ["_toolkit/SCHEMA.md: файл отсутствует"]
    text = read(path)
    issues = []
    count = len(text.rstrip("\n").split("\n"))
    if count > SCHEMA_CEILING:
        issues.append(f"_toolkit/SCHEMA.md: {count} строк при потолке {SCHEMA_CEILING}")
    dates = sorted(set(re.findall(r"(?<!\d)20\d{2}-\d{2}-\d{2}(?!\d)", text)))
    if dates:
        issues.append("_toolkit/SCHEMA.md: хронологические даты: " + ", ".join(dates))
    ids = sorted(set(re.findall(r"(?<!\d)\d{5,}(?!\d)", text)))
    if ids:
        issues.append("_toolkit/SCHEMA.md: идентификаторы источников или волн: " + ", ".join(ids))
    local = _schema.path(root)
    if local and os.path.exists(local):
        import domain as _domain
        backticks = set(re.findall(r"`([^`\n]+)`", text))
        for token in sorted(_schema.taxonomy(root) & backticks):
            issues.append(f"_toolkit/SCHEMA.md: локальный тег `{token}`")
        for token in sorted(_schema.machine_values(root, "Машинные значения")):
            if token in text:
                issues.append(f"_toolkit/SCHEMA.md: локальное значение `{token}`")
        for value in sorted(set(re.findall(r"`([^`\n]+)`", _schema.section(root, "Локальные пути")))):
            if value in {"raw/", "wiki/", "_staging/", "wiki/sources/raw/"}:
                continue
            if value in text:
                issues.append(f"_toolkit/SCHEMA.md: локальный путь `{value}`")
        for category in _schema.list_items(root, "Категории болей"):
            if category.rstrip(".") in text:
                issues.append(f"_toolkit/SCHEMA.md: локальная категория «{category}»")
        for marker in sorted(set(_domain.markers(root) + _domain.topics(root))):
            if re.search(r"(?<![\w-])" + re.escape(marker) + r"(?![\w-])", text):
                issues.append(f"_toolkit/SCHEMA.md: слово домена `{marker}`")
    return issues


SERVICE_DOC_CEILINGS = {"_toolkit/style.md": 205, "_toolkit/instruction-style.md": 60}
# 205 для свода: скелет `entity` получил вторую форму (человек и продукт), это +9 строк по решению владельца;
# прежняя планка 200 была поставлена на состояние 192 строки после увода прецедентов. Класс дефекта, который
# держит потолок, — возврат хроники: на 2026-09-21 свод был 259 строк и потолок его ловил.


def check_service_doc_ceiling(root):
    """§88. Служебный документ не разрастается без нужды.

    Зачем: без потолка прецеденты возвращаются сами. `style.md` к 2026-09-21 вырос до 259 строк, и 90 из них были
    разборами правок. Потолок — страховка от молчаливого возврата хроники, а не замена требованию §87.
    """
    issues = []
    for rel, ceiling in sorted(SERVICE_DOC_CEILINGS.items()):
        path = os.path.join(root, rel)
        if not os.path.exists(path):
            continue
        count = len(read(path).split("\n"))
        if count > ceiling:
            issues.append(f"{rel} вырос до {count} строк при потолке {ceiling}: разборы уводятся в `log.md`")
    return issues


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wiki", required=True)
    ap.add_argument("--write-log", action="store_true")
    ap.add_argument("--json", help="куда записать машинную сводку (разделы, проблемы); "
                                   "по умолчанию — _staging/audit/lint-summary-<дата>.json")
    ap.add_argument("--no-summary", action="store_true", help="не писать машинную сводку")
    ap.add_argument("--all-issues", action="store_true",
                    help="печатать все находки раздела, без среза в 40 строк (для аттестации)")
    args = ap.parse_args()
    root = args.wiki
    if not os.path.isdir(root):
        sys.exit(f"Нет каталога {root}")
    # Объявление домена экземпляра: имена корпусов, слова темы, реестры. Механизм их не знает и спрашивает
    # у экземпляра (разбор инвентаризации 2026-09-22). Нет объявления — проверки домена не применимы, и об
    # этом говорит отдельная строка (§89), а не молчание: пустое место не должно выглядеть как успех.
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    try:
        import domain as _domain
        DOMAIN = _domain.load(root)
        REGISTERS = _domain.registers(root)
    except ImportError:
        DOMAIN, REGISTERS = {}, {}
    except (OSError, UnicodeError) as exc:
        print("lint: объявление домена не прочитано: %s" % exc, file=sys.stderr)
        DOMAIN, REGISTERS = {}, {}
    # Проверки, которые смотрят в историю коммитов или в удалённую копию, в распакованном архиве (без `.git`)
    # оценить нечем: у скачавшего проект нет ни окна коммитов, ни реестра отправок владельца. Они названы
    # "не проверяется без .git" прямо в заголовке раздела, чтобы молчание не читалось как "всё в порядке".
    HAS_GIT = os.path.isdir(os.path.join(root, ".git"))
    if not HAS_GIT:
        print("git нет: разделы 30 и 36 не проверяются (нет истории коммитов и реестра отправок)")
    vault = os.path.join(root, "wiki") if os.path.isdir(os.path.join(root, "wiki")) else root

    tax = taxonomy(root)
    pages, frontmatters, bodies = {}, {}, {}
    for d in PAGE_DIRS:
        full = os.path.join(vault, d)
        if not os.path.isdir(full):
            continue
        for fn in sorted(os.listdir(full)):
            if not fn.endswith(".md"):
                continue
            slug = fn[:-3]
            text = read(os.path.join(full, fn))
            fm, body = split_frontmatter(text)
            pages[slug] = os.path.join(full, fn)
            frontmatters[slug] = fm
            bodies[slug] = text

    links = {slug: set(re.findall(r"\[\[([^\]|#]+)", t)) for slug, t in bodies.items()}
    # известные цели ссылок: любой .md в хранилище — и как "dir/slug", и как "slug"
    known = set()
    for base, _, files in os.walk(vault):
        if ".obsidian" in base:
            continue
        for fn in files:
            rel = os.path.relpath(os.path.join(base, fn), vault).replace("\\", "/")
            if fn.endswith(".md"):
                known.add(rel[:-3])
                known.add(fn[:-3])
            else:
                # медиа в хранилище — такая же законная цель вставки, как страница: картинка
                # публикуется в sources/raw/images, и `![[файл.jpg]]` со страницы — не битая ссылка
                known.add(fn)
                known.add(rel)

    broken = {s: sorted({t[:-3] if t.endswith(".md") else t for t in ls} - known) for s, ls in links.items()}
    broken = {s: v for s, v in broken.items() if v}
    inbound = {s: 0 for s in pages}
    for s, ls in links.items():
        for target in ls:
            t = target.split("/")[-1]
            if t in inbound and t != s:
                inbound[t] += 1
    orphans = sorted(s for s, n in inbound.items() if n == 0)

    index_path = os.path.join(vault, "index.md")
    index_txt = read(index_path) if os.path.exists(index_path) else ""
    indexed = {t.split("/")[-1].split("|")[0].strip() for t in re.findall(r"\[\[([^\]]+)\]\]", index_txt)}
    indexed = {t.split("|")[0].strip().split("/")[-1] for t in indexed}
    missing_in_index = sorted(s for s in pages if s not in indexed)
    index_structure = check_index_structure(root)
    service_docs = check_service_docs(root)
    ignored_tracked = check_ignored_but_tracked(root)
    audit_refs = check_audit_references(root)
    readme_ceiling = check_readme_ceiling(root)
    theses_digest = check_theses_digest(root)
    chronicle = check_service_doc_chronicle(root)
    ceilings = check_service_doc_ceiling(root)

    fm_issues = []
    raw_fm = {}
    for slug, fm in frontmatters.items():
        if fm is None:
            fm_issues.append((slug, "нет frontmatter"))
            continue
        miss = [f for f in REQUIRED if not fm.get(f)]
        if miss:
            fm_issues.append((slug, "нет полей: " + ", ".join(miss)))
        # даты в шапке — строго ГГГГ-ММ-ДД: склейка вида `2026-09-1513` для YAML остаётся строкой и молчит,
        # а в свойствах Obsidian это мусор. Класс дефекта реальный: так уже склеивалась дата при правке
        # `updated:` (2026-09-15), и линтер этого не видел.
        for _f in ("created", "updated", "last-verified"):
            _v = str(fm.get(_f) or "").strip()
            if _v and not re.match(r"^\d{4}-\d{2}-\d{2}$", _v):
                fm_issues.append((slug, f"{_f}: «{_v[:24]}» — дата пишется как ГГГГ-ММ-ДД"))
        # YAML-валидность: незакавыченное ": " внутри значения ломает свойства Obsidian
        m = re.match('^---' + chr(13) + '?\\n(.*?)' + chr(13) + '?\\n---', bodies[slug], re.DOTALL)
        if m:
            block = m.group(1)
            try:
                import yaml as _yaml
                _yaml.safe_load(block)
            except ImportError:
                if any(re.match(r'^[A-Za-z_]+:\\s+.*:\\s', ln) for ln in block.split('\\n')):
                    fm_issues.append((slug, 'значение с «: » без кавычек — ломает свойства Obsidian'))
            except (_yaml.YAMLError, ValueError, TypeError) as e:
                fm_issues.append((slug, 'YAML не парсится: ' + str(e).splitlines()[0][:70]))

    # 13. политика старения и сила доказательства
    import datetime as _dt
    aging = []
    for slug, fm in frontmatters.items():
        if not fm:
            continue
        st = fm.get("verification-status", "").strip().strip('"')
        ev = fm.get("evidence", "").strip().strip('"')
        if st and st not in STATUSES:
            aging.append((slug, "недопустимый verification-status: " + st))
        if ev and ev not in EVIDENCE:
            aging.append((slug, "недопустимый evidence: " + ev))
        lv = fm.get("last-verified", "").strip().strip('"')
        if lv:
            try:
                age = (_dt.date.today() - _dt.date.fromisoformat(lv)).days
                if age > STALE_DAYS and st != "stale":
                    aging.append((slug, "last-verified старше " + str(STALE_DAYS) + " дней (" + lv + ") — пометить stale"))
            except ValueError:
                aging.append((slug, "last-verified не дата: " + lv))

    tag_issues = []
    for slug, fm in frontmatters.items():
        if not fm:
            continue
        for t in parse_tags(fm.get("tags")):
            if tax and t not in tax and not t.startswith("unknown"):
                tag_issues.append((slug, t))

    long_pages = []
    for d in PAGE_DIRS:
        full = os.path.join(vault, d)
        if not os.path.isdir(full):
            continue
        for fn in sorted(os.listdir(full)):
            p = os.path.join(full, fn)
            if fn.endswith(".md"):
                # Страница болей исключена из порога длины по решению владельца 2026-09-16: «к чёрту мягкий
                # предел, мне нужно видеть все боли на одной странице». Длина там платится формой: за формой
                # следит §48, и заменить её расщеплением нельзя — поиск по симптому живёт на одной странице.
                if fn == "pain-points-and-fixes.md":
                    continue
                n = _lines(read(p))
                if n > 200:
                    long_pages.append((os.path.relpath(p, root), n))

    # Копии источников в хранилище: проверяется только НАЛИЧИЕ. Расхождение копии с мастером —
    # не дефект с 2026-09-15 (решение владельца: «отключить сверку зеркал»): мастер `raw/` — архив
    # забранного из сети, а копия внутри вики живёт своей жизнью и правится как нужно (в ней живёт,
    # например, раздел «Где использован»). Содержание копии сторожит раздел 42, а не байтовая сверка.
    mirror_root = os.path.join(vault, "sources", "raw")
    out_of_sync = []
    for base, dirs, files in os.walk(os.path.join(root, "raw")):
        dirs[:] = [d for d in dirs if d not in ()]  # исключение assets снято: каталог убран как пустой, медиа живут в raw/<корпус>/images/
        for fn in files:
            if not fn.endswith(".md"):
                continue
            rel = os.path.relpath(os.path.join(base, fn), os.path.join(root, "raw"))
            if not os.path.exists(os.path.join(mirror_root, rel)):
                out_of_sync.append(f"{rel} — нет копии в sources/raw")

    # таблицы: ссылки в ячейках, скобки, пустые ячейки, число колонок
    table_issues = []
    for d in PAGE_DIRS:
        full = os.path.join(vault, d)
        if not os.path.isdir(full):
            continue
        for fn in sorted(os.listdir(full)):
            if not fn.endswith('.md'):
                continue
            rel = os.path.relpath(os.path.join(full, fn), vault)
            file_lines = open(os.path.join(full, fn), encoding='utf-8').read().split('\n')
            for i, line in enumerate(file_lines, 1):
                if not line.startswith('|'):
                    continue
                if re.search(r'\[\[[^]]*\|[^]]*\]\]', line):
                    table_issues.append(f'{rel}:{i} — ссылка с пайпом внутри таблицы')
                spans = [(m.start(), m.end()) for m in re.finditer(r'\[\[[^\[\]]*\]\]', line)]
                cov = [any(a <= k < b for a, b in spans) for k in range(len(line))]
                if any(not cov[m.start()] for m in re.finditer(r'\[\[|\]\]', line)):
                    table_issues.append(f'{rel}:{i} — несбалансированные скобки в строке таблицы')
                if re.search(r'\[\[\s|\s\]\]', line):
                    table_issues.append(f'{rel}:{i} — пробел внутри цели ссылки')
            i = 0
            while i < len(file_lines):
                if file_lines[i].startswith('|'):
                    j = i
                    while j < len(file_lines) and file_lines[j].startswith('|'):
                        j += 1
                    blk = file_lines[i:j]
                    if len(blk) >= 2:
                        head = [c.strip() for c in blk[0].strip().strip('|').split('|')]
                        for k, l2 in enumerate(blk[2:], start=i + 3):
                            cells = [c.strip() for c in l2.strip().strip('|').split('|')]
                            if '' in cells:
                                table_issues.append(f'{rel}:{k} — пустая ячейка в таблице')
                            elif len(cells) != len(head):
                                table_issues.append(f'{rel}:{k} — колонок {len(cells)} вместо {len(head)}')
                    i = j
                else:
                    i += 1

    # пустые файлы: след сорвавшегося сохранения в Obsidian
    empty_files = []
    for base, _, files in os.walk(vault):
        if '.obsidian' in base:
            continue
        for fn in files:
            if fn.endswith('.md') and os.path.getsize(os.path.join(base, fn)) == 0:
                empty_files.append(os.path.relpath(os.path.join(base, fn), vault))

    # провенанс: инлайновые сноски по абзацам запрещены (канон 2026-09-15: строка «Источники»
    # в теле страницы отменена — провенанс живёт в поле `sources:` шапки)
    prov_issues = []
    for d in PAGE_DIRS:
        full = os.path.join(vault, d)
        if not os.path.isdir(full):
            continue
        for fn in sorted(os.listdir(full)):
            if not fn.endswith('.md'):
                continue
            rel = os.path.relpath(os.path.join(full, fn), vault)
            body = read(os.path.join(full, fn))
            if "^[[" in body:
                prov_issues.append(rel + ' — инлайновые сноски ^[[…]]')
            # Вторая форма той же болезни: инлайновая сноска с путём источника (^[raw/…]), которую
            # писали сущности-страницы. Ловится по содержимому: путь, имя файла или вики-ссылка внутри ^[…].
            for m in re.finditer(r"\^\[([^\]\n]{0,200})\]", body):
                if re.search(r"(raw/|_staging/|\.md\b|\[\[)", m.group(1)):
                    prov_issues.append(rel + ' — инлайновая сноска-источник ^[…]')
                    break

    # Журнал событий один, и имя у него одно — `log.md`: экземпляр ведёт здесь свои события, механизм
    # поставляет файл с записью-образцом. Двух имён больше нет: держать рядом образец и рабочую историю
    # значило объяснять читателю поставки то, чего в ней нет (решение владельца 2026-09-22: новый проект
    # начинается с чистого листа, старая история за ним не едет). Перед публикацией на месте истории
    # снова оказывается стартовый образец.
    log_path = os.path.join(root, "log.md")
    log_files = [f for f in ("log.md",) if os.path.exists(os.path.join(root, f))]
    log_txt_main = read(os.path.join(root, log_path)) if os.path.exists(log_path) else ""
    log_entries = len(re.findall(r"^## \[", log_txt_main, re.MULTILINE))
    # Контракт записи: `## [YYYY-MM-DD] действие | тема`. Проверяем форму, допустимое действие,
    # отсутствие дат из будущего, повторов «дата+тема» и нарушения порядка. Раньше раздел ловил
    # только объём — канарейку на такой дефект было не построить.
    LOG_ACTIONS = {"create", "ingest", "update", "query", "lint", "verify", "audit", "promote",
                   "fix", "docs", "archive", "delete"}
    log_notes, today = [], datetime.date.today().isoformat()
    for log_rel in log_files:
        seen_entries, prev_date = {}, ""
        log_txt = read(os.path.join(root, log_rel))
        # Контракт записи: `## [YYYY-MM-DD] действие | тема`. Проверяем форму, допустимое действие, даты из
        # будущего, повторы «дата+тема» и порядок. Раньше раздел ловил только объём, и канарейку на дефект
        # формы построить было нельзя; потом запись свободной формы молча выпадала из проверки целиком.
        for ln, line in enumerate(log_txt.split("\n"), 1):
            if not line.startswith("## "):
                continue
            m_log = re.match(r"^## \[(\d{4}-\d{2}-\d{2})\]\s+(\S+)\s*\|\s*(.+?)\s*$", line)
            if not m_log:
                log_notes.append(f"{log_rel}:{ln}: запись без формы «## [дата] действие | тема»: {line[:60]}")
                continue
            date, action, subject = m_log.group(1), m_log.group(2).lower(), m_log.group(3)
            if action not in LOG_ACTIONS:
                log_notes.append(f"{log_rel}:{ln}: действие «{action}» вне набора {sorted(LOG_ACTIONS)}")
            if date > today:
                log_notes.append(f"{log_rel}:{ln}: дата {date} в будущем")
            if date < prev_date:
                log_notes.append(f"{log_rel}:{ln}: дата {date} нарушает порядок (после {prev_date})")
            prev_date = max(prev_date, date)
            key = (date, subject.lower())
            if key in seen_entries:
                log_notes.append(f"{log_rel}:{ln}: повтор записи «{date} — {subject[:50]}» "
                                 f"(первая на строке {seen_entries[key]})")
            else:
                seen_entries[key] = ln
    if log_entries > 500:
        log_notes.append(f"записей в логе: {log_entries} — пора ротировать")

    # 14. отчёт о вики против машинного вывода: числа обязаны сходиться
    report_issues = []
    audit_dir = os.path.join(root, "_staging", "audit")
    figs = sorted(f for f in os.listdir(audit_dir)) if os.path.isdir(audit_dir) else []
    fig_json = sorted(f for f in figs if f.startswith("wiki-figures-") and f.endswith(".json"))
    rep_md = sorted(f for f in figs if f.startswith("wiki-overview-") and f.endswith(".md"))
    if fig_json and rep_md:
        import json as _json
        data = _json.load(open(os.path.join(audit_dir, fig_json[-1]), encoding="utf-8"))
        f = data.get("figures", {})
        rep = read(os.path.join(audit_dir, rep_md[-1]))
        rows = re.findall(r"(?m)^\| `([a-z0-9-]+)` \| [a-z]+ \| [^|]+ \| (\d+) \|", rep)
        if rows:
            if len(rows) != f.get("страницы"):
                report_issues.append(f"строк типологии {len(rows)} против страниц {f.get('страницы')}")
            s = sum(int(n) for _, n in rows)
            if s != f.get("рёбер графа"):
                report_issues.append(f"сумма колонки «источников» {s} против рёбер графа {f.get('рёбер графа')}")
        for label, text in ((f.get("рёбер графа"), f"рёбер графа «страница → источник»: {f.get('рёбер графа')}"),):
            if text and text not in rep:
                report_issues.append(f"в отчёте нет строки «{text}»")
        # 14.2 весь пакет: обзор, приложение, статус-документ, машинный вывод
        package = [x for x in figs if x.startswith(("wiki-overview-", "wiki-appendix-", "wiki-state-", "wiki-figures-", "wiki-package-", "wiki-machine-appendix-", "auditor-response-5-", "audit-package-"))
                   and x.endswith(".md")]
        snapshot = data.get("snapshot_line", "")
        # дата снапшота: пакеты прошлых дат — исторические артефакты, их не сверяем с сегодняшним снимком
        snap_date = ""
        m_sd = re.search(r"(\d{4}-\d{2}-\d{2})", os.path.basename([x for x in figs if x.startswith("wiki-figures-")][-1]))
        if m_sd:
            snap_date = m_sd.group(1)
        for name in package:
            if snap_date and snap_date not in name:
                continue
            text = read(os.path.join(audit_dir, name))
            if snapshot and name != [x for x in figs if x.startswith("wiki-figures-")][-1] and snapshot not in text:
                report_issues.append(f"{name}: нет строки снапшота пакета")
            for pattern, key, human in ((r"(\d+)\s+рёб", "рёбер графа", "рёбер"),
                                        (r"карточек\s*[:=—]\s*(\d+)", "карточек извлечения", "карточек"),
                                        (r"(\d+)\s+карточ\w*\s*=", "карточек извлечения", "карточек"),
                                        (r"эталонных вопросов\s*[:=—]?\s*(\d+)", "вопросов", "вопросов"),
                                        (r"(\d+)\s+эталонн\w+\s+вопрос", "вопросов", "вопросов"),
                                        (r"линтер[^\n]{0,40}?(\d+)\s+раздел", "разделов линтера", "разделов линтера")):
                expect = f.get(key)
                if expect is None:
                    continue
                for m in re.finditer(pattern, text):
                    if int(m.group(1)) != int(expect):
                        report_issues.append(f"{name}: «{human}» названо {m.group(1)}, а в снапшоте {expect}")
        for name, jf in (("wiki-graph", [x for x in figs if x.startswith("wiki-graph-") and x.endswith(".tsv")]),
                         ("figures-json", fig_json)):
            if not jf:
                report_issues.append(f"нет артефакта {name}")
        if len(figs):
            pass
    elif figs:
        report_issues.append("есть файлы отчёта, но нет пары wiki-overview + wiki-figures.json")

    # 15. числа без владельца в разделе «Факты и цифры»
    #     Канон 2026-09-15: владелец — ссылкой, «([[источник|Имя]])» или «([[источник]])»;
    #     слова-типы («самоотчёт практика», «вендорский самоотчёт») владельцем не считаются —
    #     замечание владельца: читателю они ничего не добавляют. Перечень имён здесь стоять
    #     не может: он разошёлся с каноном в первый же день (29 ложных находок).
    OWNER = re.compile(r"\([^()]*\[\[[^\]\[]+\|[^\]\[]+\]\][^()]*\)"          # ссылка-алиас
                       r"|\([^()]*\[\[[^\]\[|]+\]\][^()]*\)"                  # ссылка без алиаса
                       r"|\((?:[^()]*(?:не атрибутирован|машинное чтение|OCR|из схем|бенчмарк|замер)[^()]*)\)"
                       # Владелец 2026-09-19: источник-запись со ссылкой отдаёт адрес прямо в статью,
                       # и адрес в скобках считается владельцем числа наравне со ссылкой на источник.
                       r"|\([^()]*https?://[^()]*\)",
                       re.IGNORECASE)
    number_issues = []
    for slug, body in bodies.items():
        m = re.search(r"(?ms)^## (?:Факты и цифры|Числа и замеры)\s*$(.*?)(?=^## |\Z)", body)
        if not m:
            continue
        # Считаем по пункту целиком, а не по физической строке: перенос строки — след разметки,
        # а владелец числа может стоять на второй строке пункта (владелец 2026-09-18: строка
        # «публикация на GitHub — https://… ([[источник|Имя]])», разорванная переносом, давала
        # ложную находку). Цифры внутри адресов фигурами не считаются: 2608.08311 в ссылке — не число.
        bullets, cur = [], None
        for line in m.group(1).split("\n"):
            if line.strip().startswith(("-", "*")):
                if cur is not None:
                    bullets.append(cur)
                cur = line
            elif cur is not None and line.strip():
                cur += "\n" + line
            elif cur is not None:
                bullets.append(cur)
                cur = None
        if cur is not None:
            bullets.append(cur)
        for bullet in bullets:
            text = re.sub(r"https?://\S+", " ", bullet)
            if not re.search(r"\d", text):
                continue
            if not OWNER.search(bullet):
                number_issues.append(f"{slug}: {bullet.strip().splitlines()[0][:90]}")
    number_issues = number_issues[:60]

    # 17. сила доказательства против уверенности: вендорское самоотчёты не дают высокой уверенности без оговорки
    evidence_issues = []
    for slug, fmv in frontmatters.items():
        if not fmv:
            continue
        ev = fmv.get("evidence", "").strip().strip('"')
        cf = fmv.get("confidence", "").strip().strip('"')
        if ev == "vendor-self-report" and cf == "high" and not (fmv.get("disclaimer") or "").strip():
            evidence_issues.append(f"{slug}: evidence=vendor-self-report и confidence=high без поля disclaimer")

    # 19. эффективный размер выборки: страница только на аффилированном корпусе не может быть «высокой уверенности»
    ess_issues = []
    for slug, fmv in frontmatters.items():
        if not fmv or fmv.get("confidence", "").strip().strip('"') != "high":
            continue
        srcs = parse_tags(fmv.get("sources"))
        if not srcs:
            continue
        independent = [x for x in srcs if "/articles/" in x or x.startswith("raw/articles")]
        corpus_only = [x for x in srcs if x not in independent]
        if corpus_only and not independent:
            ess_issues.append(f"{slug}: confidence: high, но все источники из одного корпуса (нет независимого подтверждения)")

    # 21. целые числа в прозе пакета: разрешён снапшот и объяснённые подмножества
    # Отправленные документы: их числа заморожены в момент отправки и сверяются со слепком (§27),
    # а не с сегодняшним снапшотом. Реестр `sent-artifacts.tsv` ведёт процедура отправки; несовпадение
    # хеша означает, что документ после отправки изменили — это уже дефект, а не расхождение чисел.
    sent_files, sent_issues = {}, []
    sent_path = os.path.join(root, "_staging", "audit", "sent-artifacts.tsv")
    if os.path.exists(sent_path):
        for line in read(sent_path).splitlines()[1:]:
            p = line.split("\t")
            if len(p) >= 3:
                sent_files[os.path.basename(p[1])] = p[2]
    prose_issues = []
    import json as _jsonp

    def _snap_value(prefix, date, key=None):
        """Снапшот ИМЕННО этой даты: пакет сверяется со слепком своего дня, а не с сегодняшним."""
        path = os.path.join(audit_dir, prefix + "-" + date + ".json")
        if not os.path.exists(path):
            return None
        data = _jsonp.load(open(path, encoding="utf-8"))
        return data.get(key) if key else data.get("figures", {})

    audit_docs = [x for x in sorted(os.listdir(audit_dir))
                  if x.endswith(".md") and x.startswith(("wiki-", "audit-package-", "auditor-"))] \
        if os.path.isdir(audit_dir) else []
    for name in audit_docs:
        m_date = re.search(r"(\d{4}-\d{2}-\d{2})", name)
        if not m_date:
            continue                            # без даты в имени документ не наш: сверять не с чем
        doc_date = m_date.group(1)
        if name in sent_files:
            cur_hash = hashlib.sha256(open(os.path.join(audit_dir, name), "rb").read()).hexdigest()
            if cur_hash == sent_files[name]:
                continue                        # отправлено: числа заморожены, сверяются со слепком (§27)
            sent_issues.append(f"{name}: изменён после отправки — это уже не то, что ушло "
                               f"(реестр отправленного хранит другой хеш)")
            continue
        _figs = _snap_value("wiki-figures", doc_date)
        vc_fig = _snap_value("number-check", doc_date, "confirmed")
        if _figs is None and vc_fig is None:
            prose_issues.append(f"{name}: снапшота на {doc_date} нет (ни wiki-figures, ни number-check) — "
                                f"числа документа сверить не с чем; собрать пакет вместе со снапшотом")
            continue
        _figs = _figs or {}
        if True:
            text = read(os.path.join(audit_dir, name))
            text = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)
            text = re.sub(r"`[^`]*`", " ", text)                       # код в инлайне тоже не проверяем
            text = re.sub(r"\[[^]]*\]\([^)]*\)", " ", text)
            # Строка генератора «Последние записи: `хеш` сообщение коммита`» цитирует историю git, а не нашу
            # прозу: число из чужого сообщения коммита («130 карточек» в сообщении волны) относится к тому
            # коммиту, а не к сегодняшнему снапшоту, и сверять его не с чем. Проверка ловит рукописные числа
            # в нашей прозе — цитату истории из неё исключаем, иначе гейт красный на законном поведении.
            text = re.sub(r"(?m)^- Последние записи:.*$", " ", text)
            text = re.sub(r"^\s*\|?[-: ]+\|.*$", " ", text, flags=re.MULTILINE)
            METRICS = [
                (r"(\d{2,})\s+рёб", "рёбер графа", {_figs.get("рёбер по корпусным источникам"), _figs.get("рёбер по вендорским статьям")}),
                (r"(\d{2,})\s+карточ", "карточек извлечения", {30, 27, 20, 11, 10, 1}),
                (r"(\d{2,})\s+эталонн\w+\s+вопрос", "эталонных вопросов", {26}),
                (r"(\d{2,})\s+раздел\w*\s+линтер", "разделов линтера", {19, 12}),
                (r"линтер[^\n]{0,24}?(\d{2,})\s+раздел", "разделов линтера", {19, 12}),
                (r"(\d{2,})\s+подтверждено", "проверенных чисел", {vc_fig}),
                (r"(\d{2,})\s+источник\w*\s+знани", "источников знаний", {_figs.get("файлов слоя 1")}),
                (r"(\d{2,})\s+файл\w*\s+слоя", "файлов слоя 1", set()),
            ]
            for pattern, label, subsets in METRICS:
                expect = _figs.get(label) if label != "проверенных чисел" else vc_fig
                # Нет базы в снапшоте этой даты — метрику пропускаем: без неё «не сходится» не докажешь,
                # а находка на пустом месте (законная правка письма) обесценивает гейт. Нашла проба:
                # безвредные канарейки №54 и №64 давали ложные срабатывания §21 на документах 09-15,
                # у которых фигур пакета ещё нет.
                if expect is None and not {v for v in subsets if v}:
                    continue
                for m in re.finditer(pattern, text):
                    val = int(m.group(1))
                    if val in {v for v in subsets if v} or (expect is not None and val == int(expect)):
                        continue
                    if re.search(r"\d+\s+из\s+" + m.group(1), text[max(0, m.start() - 10):m.start() + 1]):
                        continue
                    prose_issues.append(f"{name}: «{m.group(0).strip()}» — в снапшоте за {doc_date} {label} = {expect}")
            prose_issues = sorted(set(prose_issues))[:40]

    # 20. все упомянутые документы слоя 1 должны быть объявлены в sources:
    raw_names = {}
    _raw_bases = [(os.path.join(root, "raw"), "raw/"), (os.path.join(root, "raw", "articles"), "raw/articles/")]
    # вложенные папки raw/ (корпус практика): без них ссылку на такой документ можно было не объявлять
    if os.path.isdir(os.path.join(root, "raw")):
        for _d in sorted(os.listdir(os.path.join(root, "raw"))):
            _p = os.path.join(root, "raw", _d)
            if os.path.isdir(_p) and _d != "articles":
                _raw_bases.append((_p, f"raw/{_d}/"))
    for base, prefix in _raw_bases:
        if os.path.isdir(base):
            for fn in os.listdir(base):
                if fn.endswith(".md") and "prompt" not in fn:      # служебный шаблон опроса источником не считается
                    raw_names[fn[:-3]] = prefix + fn
    prov_gaps = []
    for slug, body in bodies.items():
        fmv = frontmatters.get(slug) or {}
        declared = set(parse_tags(fmv.get("sources")))
        seen = set()
        for line in body.split("\n"):
            # указатели («см. также», «подробнее») источником не являются — это навигация
            if re.search(r"(см\. также|подробнее|см\. \[\[)", line, re.IGNORECASE):
                continue
            attributive = bool(re.search(r"\d", line)) or ":" in line
            if not attributive:
                continue
            for ref in re.findall(r"\[\[([^\]|#]+)", line):
                name = os.path.basename(ref.strip())
                name = name[:-3] if name.endswith(".md") else name
                if name in raw_names and raw_names[name] not in declared and raw_names[name] not in seen:
                    seen.add(raw_names[name])
                    prov_gaps.append(f"{slug}: упомянут {raw_names[name]}, но его нет в sources:")

    # 18. мягкий предел длины: страница > 150 строк обязана объяснить, почему не расщеплена
    split_issues = []
    for d in PAGE_DIRS:
        full = os.path.join(vault, d)
        if not os.path.isdir(full):
            continue
        for fn in sorted(os.listdir(full)):
            if not fn.endswith(".md"):
                continue
            p = os.path.join(full, fn)
            n = _lines(read(p))
            if n > 150:
                fmv = frontmatters.get(fn[:-3]) or {}
                # Поле обязано не только быть, но и не разойтись с файлом: «179 строк» на странице
                # в 188 строк — это утверждение, которого данные не поддерживают (найдено 2026-09-16).
                m_lines = re.match(r"^\s*(\d+)\s*строк", (fmv.get("split-justification") or "").lstrip('"'))
                if not (fmv.get("split-justification") or "").strip():
                    split_issues.append(f"{fn[:-3]}: {n} строк и нет поля split-justification")
                elif m_lines and int(m_lines.group(1)) != n:
                    split_issues.append(f"{fn[:-3]}: split-justification говорит {m_lines.group(1)} строк, "
                                        f"а в файле {n}")

    # 16 (снята 2026-09-15). Проверка волатильности убрана вместе с полем `volatility`: владелец оценил
    # механизм как нарушение YAGNI — «убрать все это. не нужно это никому». Срок пересмотра цифр ведёт
    # не поле в шапке, а сама работа над страницей.

    # 22. остатки черновика и дубли секций (проверка на хвосты бутстрапа)
    draft_issues = []
    # маркеры-остатки: только строки-заголовки-заглушки и явные обещания, а не слова из содержания
    STUB_LINE = re.compile(r"(?m)^\s*(TODO|TBD|FIXME|XXX|ЗАПОЛНИТЬ|ДОПИСАТЬ)\b")
    STUB_PHRASES = ("появится позже", "будет добавлен позже", "секция будет", "здесь будет", "нет данных по культуре")
    for slug, body in bodies.items():
        comments = re.findall(r"<!--(.*?)-->", body, re.DOTALL)
        if comments:
            draft_issues.append(f"{slug}: HTML-комментарий-заглушка в теле ({comments[0].strip()[:40]})")
        h2 = re.findall(r"(?m)^##\s+(.+)$", body)
        dups = {h for h in h2 if h2.count(h) > 1}
        if dups:
            draft_issues.append(f"{slug}: дубли заголовков ## ({', '.join(sorted(dups))})")
        heads = re.findall(r"(?m)^##\s+(.+)$", body)
        for i, h in enumerate(heads):
            after = body.split("## " + h, 1)[1] if "## " + h in body else ""
            nxt = re.search(r"(?m)^##\s+", after)
            section = after[:nxt.start()] if nxt else after
            if len(section.strip()) < 12:
                draft_issues.append(f"{slug}: пустая секция «{h.strip()}»")
        m_stub = STUB_LINE.search(body)
        if m_stub:
            draft_issues.append(f"{slug}: строка-заглушка «{m_stub.group(1)}»")
        for phrase in STUB_PHRASES:
            if phrase in body.lower():
                draft_issues.append(f"{slug}: обещание вместо факта («{phrase}»)")
                break

    # 23. процесс в контенте: страница — конечные факты, а не отчёт о работе
    process_issues = []
    # только процессная проза о ходе работы; технические термины (fallback, gap в смысле пробела) не трогаем
    PROCESS_PHRASES = (r"по правилам честности", r"искали в [Pp]ubMed", r"мы проверили", r"мы обнаружили",
                       r"запросы?\s*→\s*0", r"только title_only", r"валидировано 20", r"статус:\s*`",
                       r"был добавлен в слой", r"мы сравнили", r"было решено", r"проверка показала",
                       r"в ходе работы над", r"в этой сессии")
    for slug, body in bodies.items():
        for pat in PROCESS_PHRASES:
            m = re.search(pat, body, re.IGNORECASE)
            if m:
                ctx = re.sub(r"\s+", " ", body[max(0, m.start() - 40):m.start() + 60])
                process_issues.append(f"{slug}: «{m.group(0)}» — процесс вместо факта (…{ctx.strip()}…)")
                break

    # 24. ось stance: у каждого источника страницы есть позиция, и противоречия не спрятаны
    #     Канон 2026-09-15: умолчание объявлено строкой `source-stance-default: supports`,
    #     в `source-stance` пишутся только отклонения (`partial`, `contradicts`). Раньше поле
    #     перечисляло все источники с «=supports»: 610 записей из 684 повторяли умолчание и
    #     превращали шапку страницы в простыню (у pain-points — 9325 знаков против 48).
    stance_issues = []
    STANCES = ("supports", "contradicts", "partial")
    for slug, body in bodies.items():
        fmv = frontmatters.get(slug) or {}
        declared = [s.strip() for s in parse_tags(fmv.get("sources")) if s.strip()]
        default = (fmv.get("source-stance-default") or "supports").strip().strip('"').strip("'")
        raw_st = fmv.get("source-stance", "")
        st = {}
        for pair in parse_tags(raw_st):
            if "=" in pair:
                src, val = pair.split("=", 1)
                st[src.strip()] = val.strip().strip('"').strip("'")
        for src in declared:
            val = st.get(src, default)
            if val not in STANCES:
                stance_issues.append(f"{slug}: позиция «{val}» у {src} вне набора {STANCES}")
        for src, val in st.items():
            if src not in declared:
                stance_issues.append(f"{slug}: позиция указана для {src}, которого нет в sources")
        contradicting = [s for s, v in st.items() if v == "contradicts"]
        if contradicting:
            fm_txt = " ".join(f"{k}: {v}" for k, v in fmv.items())
            has_dispute_field = bool(parse_tags(fmv.get("contradictions"))) or "disclaimer" in fmv
            has_dispute_section = bool(re.search(r"(?mi)^#{2,3}\s+.*(спорн|противореч|открытые вопросы)", body))
            if not (has_dispute_field or has_dispute_section):
                stance_issues.append(f"{slug}: есть противоречащие источники ({len(contradicting)}), "
                                     f"но ни поля contradictions, ни раздела спора")
            if fmv.get("confidence", "").strip().strip('"') == "high" and not has_dispute_section and "disclaimer" not in fmv:
                stance_issues.append(f"{slug}: confidence: high при противоречащих источниках без оговорки")

    # 25. ось contradicts обязана быть отражена строкой в карте конфликтов:
    # иначе агент-редактор может пометить противоречие и молча не завести спор.
    cmap_path = domain_register_path(root, "conflicts")   # путь объявляет экземпляр: иначе карты для проверки нет
    # Не объявлена карта — проверять нечего: строка «ось contradicts отражена» остаётся молчащей, а о том, что
    # домен не объявлен, говорит §89 (разбор 2026-09-22).
    cmap_rows = [l for l in (read(cmap_path).split("\n") if cmap_path and os.path.exists(cmap_path) else [])
                 if l.strip().startswith("|") and re.match(r"\|\s*\**\d+", l.strip())]
    unaudited_stance = []
    # Карта конфликтов не объявлена — проверка не применима целиком: иначе каждая страница с осью
    # `contradicts` становится находкой на ровном месте, хотя сверять её не с чем (§89 говорит об этом).
    for slug, body in ([] if not cmap_path else bodies.items()):
        fmv = frontmatters.get(slug) or {}
        for pair in parse_tags(fmv.get("source-stance", "")):
            if not pair.strip().lower().endswith("=contradicts"):
                continue
            src = pair.split("=")[0].strip()
            name = os.path.basename(src)[:-3] if src.endswith(".md") else os.path.basename(src)
            # один голос: отчёт (транскрипция) и манифест одного автора — один источник, поэтому
            # строка карты может ссылаться на манифест, а ось стоять на отчёте.
            name = re.sub(r"_(manifest|report)$", "", name)
            hit = any(re.search(r"\[\[\s*" + re.escape(slug) + r"\s*(\]|\|)", row) or name in row
                      for row in cmap_rows)
            if not hit:
                unaudited_stance.append(f"{slug}: contradicts у {name} не отражён строкой в карте конфликтов")

    # 25б. Спор в карте обязан быть работоспособным: у каждой строки спора есть пара в таблице
    # «Условия применимости» с заполненными ячейками. Правило владельца 2026-09-15: «цель — выявить
    # реальные практичные расхождения», а не найти спор в каждой статье. Спор без строки «когда
    # выигрывает A / когда выигрывает B» не отвечает на вопрос «что делать по-разному» — значит это
    # разные формулировки, и в карте ему места нет. Проверка ловит именно выдуманный спор.
    if cmap_path and os.path.exists(cmap_path):
        # Таблица одна (владелец объединил две в одну с 8 колонками 2026-09-16): строка спора обязана
        # быть заполнена целиком — позиции A и B, статус, «когда выигрывает» каждая сторона и база.
        # Пустая колонка = расхождение, не доведённое до практического вывода; такой спор в карте
        # не отвечает на вопрос «что делать по-разному».
        cmap_rows25 = {}
        cur25 = None
        for line in read(cmap_path).split("\n"):
            if line.startswith("## "):
                cur25 = line[3:].strip()
                continue
            m_row = re.match(r"\|\s*\**(\d+)\**\s*\|", line.strip())
            if cur25 == "Таблица конфликтов" and m_row:
                cells = [c.strip() for c in line.strip().strip("|").split("|")]
                cmap_rows25[int(m_row.group(1))] = [i for i, c in enumerate(cells[1:], 1) if not c]
        for num in sorted(cmap_rows25):
            empty_cols = cmap_rows25[num]
            if empty_cols:
                unaudited_stance.append(f"спор №{num}: пустые ячейки в колонках {empty_cols} — "
                                        f"расхождение не доведено до практического вывода")

    # 25в. Волна обязана спросить про расхождения, а не промолчать. Требуется не спор, а ВЕРДИКТ:
    # «подтверждает / не относится / противоречит» по каждому источнику волны на её страницах.
    # Пустой результат («расхождений, меняющих решение, нет») — законный и зелёный: проверка ловит
    # молчание, а не отсутствие споров. Артефакт вердиктов — `_staging/stance/wave-<id>-verdicts.json`.
    waves_path = os.path.join(root, "_staging", "waves.json")
    if os.path.exists(waves_path):
        try:
            for wave in json.load(open(waves_path, encoding="utf-8")).get("waves", []):
                rec_path = os.path.join(root, "_staging", "audit", "ingest-wave-" + wave["id"] + ".json")
                if not os.path.exists(rec_path):
                    continue                      # у волн до заведения записей списка страниц нет
                rec = json.load(open(rec_path, encoding="utf-8"))
                # в записи волны страница записана как «каталог/слаг»; вердикты ведём по слагу
                # страницы, удалённые после волны, вердикта не требуют: спрашивать не о чем
                pages = [p["page"] for p in rec.get("pages", []) if p.get("page")
                         and os.path.exists(os.path.join(vault, p["page"] + ".md"))]
                pages = [x.split("/")[-1] for x in pages]
                v_path = os.path.join(root, "_staging", "stance", "wave-" + wave["id"] + "-verdicts.json")
                if not os.path.exists(v_path):
                    unaudited_stance.append(f"волна {wave['id']}: вердиктов оси нет ни по одной из "
                                            f"{len(pages)} страниц — шаг «противоречия» не проходил, "
                                            f"молчание читается как согласие")
                    continue
                items = json.load(open(v_path, encoding="utf-8")).get("items", [])
                have = {r.get("slug") for r in items}
                missing = [p for p in pages if p not in have]
                if missing:
                    unaudited_stance.append(f"волна {wave['id']}: нет вердикта по страницам " +
                                            ", ".join(missing[:6]) + (" …" if len(missing) > 6 else ""))
                # Расхождение может быть не только с хозяйкой источника: заметка про лишний слой MCP
                # вшита в реестр инструментов, а спорна она для страницы про инструменты и для страницы MCP.
                # Пары строит `_toolkit/stance_pairs.py` (термин источника в заголовке или в тезисном разделе
                # чужой страницы); по каждой паре тоже обязан быть вердикт.
                pairs_path = os.path.join(root, "_staging", "stance", "wave-" + wave["id"] + "-pairs.json")
                if not os.path.exists(pairs_path):
                    unaudited_stance.append(f"волна {wave['id']}: пары «источник × чужая страница» не построены — "
                                            f"`python3 _toolkit/stance_pairs.py --wave {wave['id']} --write`")
                else:
                    idx = {r.get("slug"): set(r.get("stance", {})) for r in items}
                    cross = json.load(open(pairs_path, encoding="utf-8")).get("pairs", {})
                    gaps = [(slug, src) for slug, srcs in cross.items() for src in srcs
                            if src not in idx.get(slug, set())]
                    if gaps:
                        head = ", ".join(s + "→" + os.path.basename(x)[-24:] for s, x in gaps[:3])
                        unaudited_stance.append(f"волна {wave['id']}: вердиктов нет по {len(gaps)} кросс-страничным "
                                                f"парам (например {head}) — расхождение источника с чужой страницей "
                                                f"никто не спрашивал")
        except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError, AttributeError, IndexError) as _ex:
            unaudited_stance.append("проверка вердиктов волны не запустилась: " + str(_ex)[:120])

    # 26. машинное чтение с изображений — отдельный класс доказательства, а не флаг в прозе
    ocr_issues = []
    OCR_MARK = re.compile(r"(машинное чтение|из схемы|из схем|\bOCR\b|распознанн\w+ текст)", re.IGNORECASE)
    OCR_CONFIRM = re.compile(r"(подтвержд|не подтвержд|вторым способом|второго чтения|второе чтение|сверен)", re.IGNORECASE)
    for slug, body in bodies.items():
        fmv = frontmatters.get(slug) or {}
        m_facts = re.search(r"(?ms)^##\s+Факты и цифры\s*$(.*?)(?=^##\s|\Z)", body)
        facts = m_facts.group(1) if m_facts else ""
        ocr_body = OCR_MARK.search(body)
        if OCR_MARK.search(facts) or ocr_body:
            ev = (fmv.get("evidence") or "").strip().strip('"')
            if ev not in ("derived-ocr", "mixed"):
                ocr_issues.append(f"{slug}: в «Фактах» машинно прочитанные числа, а evidence = «{ev}» — нужен derived-ocr или mixed")
            if (fmv.get("confidence") or "").strip().strip('"') == "high":
                ocr_issues.append(f"{slug}: машинно прочитанные числа при confidence: high")
            for line in body.split("\n"):
                if OCR_MARK.search(line) and re.search(r"\d", line) and not OCR_CONFIRM.search(line):
                    ocr_issues.append(f"{slug}: у числа «{line.strip()[:70]}…» не сказано, подтверждено ли прочтение вторым способом")

    # 27. утверждения о прошлых пакетах: проза о прошлом обязана сверяться со слепком.
    # Логика сверки живёт в `_toolkit/handover.py` (реестр слепков передач),
    # здесь только вызов: один источник правды на проверку.
    past_claims = []
    handover_script = toolkit_script("handover.py")
    if os.path.exists(handover_script):
        proc = subprocess.run([sys.executable, "-X", "utf8", handover_script, "check", "--wiki", root],
                              capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
        past_claims = _child_issues(proc, "сверка прошлых пакетов")

    # 33. карточка называет боль — источник обязан быть на странице болей.
    # Правило появилось после находки владельца: пять карточек корпуса GRACE называли боли (автотесты как
    # антипаттерн, фабрика галлюцинаций, попугай-самоучка, границы применимости, тестовые условия в контракте),
    # и ни одна не доехала до pain-points-and-fixes — ингест шёл по каркасу страниц, а страница болей в каркасе
    # не значилась. Проверка следит за ИСТОЧНИКОМ карточки, а не за формулировкой боли: полнота внутри
    # источника остаётся на ревью, как и у рукописных утверждений. Явный отказ — поле `pains: none` в карточке.
    pain_issues = []
    PAIN_MARK = re.compile(r"(?i)антипаттерн|враг|фабрика галлюцинац|деградац|проблем|боль|ломает|ошибк|провал|"
                           r"шум|перегруз|интерференц|обрыв|иллюз|вредн|опасн|попугай|границ\w* применимост|"
                           r"не работает|падает|теряет")
    cards_json = os.path.join(root, "_staging", "cards-registry.json")
    pain_page = os.path.join(vault, "comparisons", "pain-points-and-fixes.md")
    # Реестры — домен экземпляра: если он их не объявил (`_staging/domain.local.tsv`), проверять нечего, и об
    # этом говорит §89 одной строкой. Так у постороннего нет красного на ровном месте (разбор 2026-09-22).
    if not (domain_register_path(root, "cards") or domain_register_path(root, "pains")):
        pass          # реестров карточек и болей экземпляр не объявил — сверять не с чем (говорит §89)
    elif not os.path.exists(cards_json):
        pain_issues.append("нет объявленного реестра карточек (_staging/cards-registry.json) — охват болей проверить нечем")
    elif not os.path.exists(pain_page):
        pain_issues.append("нет объявленной страницы болей (wiki/comparisons/pain-points-and-fixes.md) — боли корпуса негде собрать")
    else:
        reg = json.load(open(cards_json, encoding="utf-8"))
        page_text = read(pain_page)
        for c in reg.get("cards", []):
            cf = os.path.join(root, "_staging", "cards", c["file"])
            if not os.path.exists(cf):
                continue
            card_text = read(cf)
            # 2026-09-17: охват смотрел только заголовки карточки, а боль живёт и в прозе. Замер: 22 карточки
            # с тремя и более признаками боли в тексте не были названы на странице болей и не помечены
            # `pains: none` — то есть проходили мимо охвата молча.
            if re.search(r"(?m)^pains:\s*none\s*$", card_text):
                continue                                  # явный отказ: болей в источнике нет
            heads = [h.strip("# ").strip() for h in re.findall(r"(?m)^###+ .+$", card_text) if PAIN_MARK.search(h)]
            src = c.get("source", "?")
            if src == "?":
                continue
            if src in page_text or src[:-3] in page_text:
                continue
            if heads:
                pain_issues.append(f"карточка «{c['id']}» называет боль («{heads[0][:70]}»), а источник {src} "
                                   f"не назван на странице болей: добавь строку или пометь карточку `pains: none`")
                continue
            # Боль может быть описана и не заголовком: тогда охват по заголовкам её не видит. Порог — три
            # признака в теле, иначе шум от обычных слов («проблема», «ошибка») в прозе.
            body = re.sub(r"(?s)^---.*?---", "", card_text)
            prose = len(PAIN_MARK.findall(body))
            if prose >= 3:
                pain_issues.append(f"карточка «{c['id']}» описывает боль прозой ({prose} признаков), а источник "
                                   f"{src} не назван на странице болей: добавь строку или пометь `pains: none`")

    # 32. письмо и ответ аудитору самодостаточны: несут frontmatter с окном и снапшотом.
    # Письма до 2026-09-14 его не имеют и остаются историческими: задним числом отправленное не переписывается.
    letter_issues = []
    CONTRACT_FROM = "2026-09-14"
    LETTER_KEYS = ("type:", "cycle:", "created:", "audience:", "snapshot:")
    ad = os.path.join(root, "_staging", "audit")
    for f in sorted(os.listdir(ad)) if os.path.isdir(ad) else []:
        m = re.match(r"auditor-(report|response(?:-\d+[a-z]?)?)-(\d{4}-\d{2}-\d{2})\.md$", f)
        if not m or m.group(2) < CONTRACT_FROM:
            continue
        head = open(os.path.join(ad, f), encoding="utf-8").read()[:600]
        if not head.lstrip().startswith("---"):
            letter_issues.append(f"_staging/audit/{f}: нет frontmatter — письмо не описывает само себя")
            continue
        missing = [k for k in LETTER_KEYS if k not in head]
        if "commits_span:" not in head:
            missing.append("commits_span:")
        if missing:
            letter_issues.append(f"_staging/audit/{f}: во frontmatter нет {', '.join(missing)}")
        for key in ("cycle:", "created:", "commits_span:", "answers_review:", "snapshot:"):
            mk = re.search(rf"(?m)^{re.escape(key)}\s*(.+)$", head)
            if mk and ("<" in mk.group(1) or "{{" in mk.group(1)):
                letter_issues.append(f"_staging/audit/{f}: {key} содержит незаполненный плейсхолдер")
        if m.group(1) == "response" and "answers_review:" not in head:
            letter_issues.append(f"_staging/audit/{f}: ответ не называет, на какой разбор отвечает")
        # Ревизия обязана объявить, что дельта ВЕРИФИЦИРОВАНА: разбор 11 показал, что первая ревизия обновила
        # числа, не обновив прозу, которую они инвалидируют, и не сказала, какие проверки прогнала. Строка
        # требует назвать число проверок — это делает «ревизия = повторный полный прогон» утверждением,
        # которое можно проверить, а не намерением. Отправленная ревизия не переписывается: первая
        # ревизия (10b) ушла без этой строки — факт записан строкой реестра и назван в ответе, а не спрятан.
        if ("revision:" in head and os.path.basename(f) not in sent_files
                and not re.search(r"(?i)дельта верифицирована", open(os.path.join(ad, f), encoding="utf-8").read())):
            letter_issues.append(f"_staging/audit/{f}: ревизия без строки «дельта верифицирована: N проверок» — "
                                 f"нечем проверить, что прогон проверок был повторён")
    # пакет обязан нести письмо текущего цикла: иначе аудитор получает архивный документ под именем «ответ»
    pkgs = sorted(x for x in os.listdir(ad) if re.match(r"audit-package-\d{4}-\d{2}-\d{2}\.md$", x)) if os.path.isdir(ad) else []
    if pkgs:
        newest = pkgs[-1]
        cyc = re.match(r"audit-package-(\d{4}-\d{2}-\d{2})\.md$", newest).group(1)
        ptxt = open(os.path.join(ad, newest), encoding="utf-8").read()[:6000]
        comp = re.findall(r"— из `([^`]+)`", ptxt)
        for cname in comp:
            if cname not in os.listdir(ad):
                letter_issues.append(f"_staging/audit/{newest}: компонент {cname} отсутствует рядом с пакетом")
        if f"auditor-report-{cyc}.md" not in ptxt:
            letter_issues.append(f"_staging/audit/{newest}: пакет не ссылается на письмо цикла "
                                 f"auditor-report-{cyc}.md (ни встроенным, ни замороженным)")

    # 31. следствия утверждений карты структуры: часть рукописных строк обещает наблюдаемое свойство.
    # Проверку делает генератор карты, чтобы правило не жило в двух местах.
    consequence_issues = []
    pm_script = toolkit_script("project_map.py")
    if os.path.exists(pm_script):
        proc = subprocess.run([sys.executable, "-X", "utf8", pm_script, "verify", "--wiki", root],
                              capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
        consequence_issues = _child_issues(proc, "проверка следствий карты")
    else:
        consequence_issues.append("нет _toolkit/project_map.py: следствия утверждений карты не проверяются")

    # 30. мостик между сессиями: окно работы описано верно и отказы названы.
    # Проверку делает сам генератор, чтобы правило не жило в двух местах.
    bridge_issues = []
    # Счёт коммитов в окне обязан совпадать со списком: мостик печатал «40» при фактических 83, потому что
    # выводил длину усечённой выдачи. Ложное число в первом файле, который читает новая сессия, — дефект,
    # а не косметика (найдено сухим прогоном новой сессии).
    bridge_script = toolkit_script("bridge.py")
    if os.path.exists(bridge_script):
        proc = subprocess.run([sys.executable, "-X", "utf8", bridge_script, "check", "--wiki", root],
                              capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
        bridge_issues = _child_issues(proc, "проверка мостика")
    else:
        bridge_issues.append("нет _toolkit/bridge.py: мостик между сессиями не проверяется")

    # Счёт коммитов в окне обязан совпадать со списком под ним: мостик печатал «40» при фактических 83,
    # потому что выводил длину усечённой выдачи (найдено сухим прогоном новой сессии). Сверяем число из
    # строки с числом строк-коммитов — это ловит и обрезку, и ложный счётчик.
    bp = toolkit.area(root, "bridge.md")
    if os.path.exists(bp):
        btxt = read(bp)
        m_cnt = re.search(r"(?m)^Коммитов в окне:\s*(\d+)", btxt)
        listed = len(re.findall(r"(?m)^- `[0-9a-f]{6,}", btxt))
        if m_cnt and int(m_cnt.group(1)) != listed:
            bridge_issues.append(f"мостик: «Коммитов в окне: {m_cnt.group(1)}», а в списке {listed} — счёт расходится со списком")

    # 29. карта структуры проекта: актуальна и объяснена.
    # Карта несёт отпечаток дерева и объяснение каждого значимого узла; проверку делает сам генератор,
    # чтобы правило не жило в двух местах.
    map_issues = []
    map_script = toolkit_script("project_map.py")
    if os.path.exists(map_script):
        proc = subprocess.run([sys.executable, "-X", "utf8", map_script, "check", "--wiki", root],
                              capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
        map_issues = _child_issues(proc, "проверка карты структуры")
    else:
        map_issues.append("нет _toolkit/project_map.py: структура проекта не проверяется")

    # 28. реестр техдолга: единственное место, где перечислено открытое.
    # Проверяем форму строк, допустимые статусы, наличие владельца у открытых пунктов и существование
    # доказательства у закрытых: иначе долг снова расползётся по письмам и останется рукописным.
    debt_issues = []
    debt_path = os.path.join(root, "debt.tsv")
    DEBT_STATUS = ("открыт", "в работе", "закрыт", "отклонён", "отложен", "принято ограничение")
    DEBT_COLS = ("id", "пункт", "класс", "владелец", "статус", "открыт", "закрыт", "доказательство", "примечание", "premise")
    # Предпосылка отказа: причина перестаёт быть верной не «вообще», а когда отпадает то, на чём она стояла.
    # Машинно проверяются два вида (артефакт появился, порог перейдён); решения владельца не проверяются вовсе
    # и намеренно не считаются дефектом — иначе линтер навсегда застрянет на решении человека.
    PREMISE_OWNER = "владелец"
    NEED_PREMISE = ("отклонён", "отложен", "принято ограничение")
    # Реестр долга — часть поставки (решение владельца 2026-09-26, отменяет 2026-09-22): пользователь
    # должен знать долг. Его отсутствие — находка, а не норма.
    if not os.path.exists(debt_path):
        debt_issues.append("нет реестра долга debt.tsv: он едет в поставке")
    else:
        lines = [l for l in read(debt_path).splitlines() if l.strip()]
        head = lines[0].split("\t") if lines else []
        if tuple(head) != DEBT_COLS:
            debt_issues.append(f"шапка реестра не совпадает с контрактом: {head}")
        seen_ids = set()
        for n, line in enumerate(lines[1:], 2):
            parts = line.split("\t")
            if len(parts) != len(DEBT_COLS):
                # Лишняя колонка так же опасна, как недостающая: сдвиг молча скрывал причину у половины строк.
                debt_issues.append(f"строка {n}: колонок {len(parts)}, нужно ровно {len(DEBT_COLS)}")
                continue
            row = dict(zip(DEBT_COLS, parts))
            if row["id"] in seen_ids:
                debt_issues.append(f"строка {n}: повтор id «{row['id']}»")
            seen_ids.add(row["id"])
            if row["статус"] not in DEBT_STATUS:
                debt_issues.append(f"строка {n}: статус «{row['статус']}» вне набора {DEBT_STATUS}")
            if row["статус"] == "открыт" and row["владелец"] not in ("агент", "владелец", "оба"):
                debt_issues.append(f"строка {n}: открытый пункт без владельца")
            ev = row["доказательство"].strip()
            if row["статус"] == "закрыт":
                if not row["закрыт"]:
                    debt_issues.append(f"строка {n}: закрытый пункт без даты закрытия")
                if not ev:
                    debt_issues.append(f"строка {n}: закрытый пункт без доказательства")
                elif not ev.startswith("cmd:") and not os.path.exists(os.path.join(root, ev)) \
                        and not not_delivered(root, ev):
                    # Архив и игнорируемые пути в поставку не входят: доказательство там локальное, и это
                    # признано легендой в шапке реестра (решение владельца: «доказательство может быть
                    # локальным, если строка это признаёт»).
                    debt_issues.append(f"строка {n}: доказательство «{ev}» не существует")
            if row["статус"] in ("отклонён", "отложен", "принято ограничение") and not row["примечание"].strip():
                debt_issues.append(f"строка {n}: «{row['статус']}» без причины")
            if row["статус"] in NEED_PREMISE:
                pr = row["premise"].strip()
                if not pr:
                    debt_issues.append(f"строка {n}: «{row['статус']}» без предпосылки — "
                                       f"нечем проверить, что причина ещё верна")
                elif pr == PREMISE_OWNER:
                    pass                                    # решение владельца: машиной не проверяется
                elif pr.startswith("artifact:"):
                    token = pr.split(":", 1)[1].strip()
                    if artifact_exists(root, token):
                        debt_issues.append(f"строка {n}: у отказа «{row['id']}» отпала предпосылка — "
                                           f"в репозитории появился артефакт «{token}»; отказ надо пересмотреть")
                elif pr.startswith("порог:"):
                    spec = pr.split(":", 1)[1].strip()
                    m = re.match(r"^([^<>]+)([<>])(\d+)$", spec)
                    if not m:
                        debt_issues.append(f"строка {n}: предпосылка «{pr}» не разбирается (нужно порог:метрика>число)")
                        continue
                    metric, op, limit = m.group(1).strip(), m.group(2), int(m.group(3))
                    val = metric_value(root, metric)
                    if val is None:
                        debt_issues.append(f"строка {n}: метрика «{metric}» неизвестна — предпосылку проверить нечем")
                    elif (op == ">" and val > limit) or (op == "<" and val < limit):
                        debt_issues.append(f"строка {n}: у отказа «{row['id']}» отпала предпосылка — "
                                           f"{metric} = {val} {op} {limit}; отказ надо пересмотреть")
                else:
                    debt_issues.append(f"строка {n}: предпосылка «{pr}» вне набора "
                                       f"(владелец | artifact:имя | порог:метрика>число)")

    # 89. объявление домена экземпляра. Проверки домена молчат, когда регистров нет, и это правильно —
    # но молчание о самой неприменимости недопустимо: пустое место не должно выглядеть как успех. Строка
    # говорит, чего не хватает и где взять форму.
    domain_issues = check_domain_declared(root)
    instance_path_issues = check_instance_paths(root)

    out = ["# Lint отчёт", "",
           f"Obsidian-хранилище: {vault} | страниц Layer 2: {len(pages)}",
           f"Тегов в таксономии: {len(tax)} | записей в log.md: {log_entries}", ""]
    # 34. внутренние ссылки служебных документов: имя НАШЕГО пути в обратных кавычках обязано существовать.
    # Класс дефекта: README аудита ссылался на удалённый шаблон и описывал снятый механизм — страницы вики
    # проверяет раздел 1, а служебные документы не проверял никто, поэтому гниль была не видна.
    # Границы правила: (1) проверяются только имена нашего дерева (`_staging/`, `_tools/`, `wiki/`, `raw/`,
    # `handover/`, `log.md`, `SCHEMA.md`) — ссылки на файлы чужих проектов и на литературу не наши;
    # (2) документы, отправленные до контракта 2026-09-14, и машинные выходы генераторов не проверяются:
    # задним числом отправленное не переписывается.
    ref_issues = []
    REF_PREFIX = ("_toolkit/", "_staging/", "_tools/", "wiki/", "raw/", "handover/", "log.md")
    REF_SKIP = ("<", ">", "{", "}", "*", "%", "$", "|", "…", " ", "=", "?", "!")
    # Файлы волны, появляющиеся по ходу: `merge_descriptions.py` пишет список непонятных записей только когда
    # такие есть, решения по ним — только когда появился список. Ссылка на них в протоколе законна до первого
    # появления (появление сторожит §68), поэтому требовать их существования нельзя.
    # Выходы проходов, которые механизм называет в документах, хотя появляются они только когда процедура
    # отработает: списка непонятных записей нет, пока нет таких записей; истории экзамена и слепков отправки нет,
    # пока не было ни прогона, ни отправки. Ссылка на них законна, требовать существования нельзя.
    ON_DEMAND = ("media-unclear.tsv", "media-unclear-decisions.tsv", "delete-thin-notes-review.html",
                 "exam-history.tsv", "sent-artifacts.tsv", "rules-check.log", "dashboard.html",
                 "scripts-layout.md")
    # Пути, которых в поставке нет по замыслу: материал экземпляра (корпус, страницы вики, рабочие папки
    # обвязки) и выходы проходов, появляющиеся по ходу работы. Ссылка служебного документа на такой путь —
    # не обрыв: требовать существования того, что создаёт сам пользователь, значит требовать его данных
    # (находка пробы на чужом корпусе, 2026-09-22). Слой доказательств `_staging/audit/` остаётся под
    # проверкой: пропажа артефакта прохода из поставки — настоящий дефект, ради него правило и заведено.
    REF_INSTANCE = re.compile(
        r"^(wiki/|raw/|handover/|_tools/tg-saved(?:/|$)|"
        r"_staging/(cards|cards-registry|claims-registry|sources-registry|tools-registry|waves\.json|debt\.tsv|"
        r"candidate-decisions|dispute-decisions|intent-decisions|registry-rows|prose-candidates|page-doubt|"
        r"delete-thin-notes|delete-review|new-pages|accepted-pages|link-summaries|ingest-input|stance|extracts|grace|repairs|temp))")
    # REF_GENERATED объявлен на уровне модуля: его читают и другие инструменты (метрика пересказа), а два
    # списка генерируемых разошлись бы при первой правке (замечание владельца 2026-09-22).
    CONTRACT_DAY = "2026-09-14"
    if os.path.isdir(root):
        base_names = set()
        for d_root, d_dirs, d_files in os.walk(root):
            d_dirs[:] = [d for d in d_dirs if d not in (".git", ".obsidian", "__pycache__")]
            for f in d_files:
                base_names.add(f)
        # `log.md` из проверки исключён: это запись о том, что было, и ссылки в ней называют файлы своего
        # времени. Требовать их существования сегодня — значит требовать переписывания истории, а она
        # не переписывается. Ссылки НА log.md из других документов проверяются как прежде.
        doc_paths = [toolkit.script("SCHEMA.md")]
        # `_staging/temp/` — рабочая папка на одну задачу: её файлы живут до конца разбора и в поставку не
        # идут, поэтому ссылки в них ничего не обещают читателю (находка 2026-09-22 на форуме двух сессий).
        for top in ("_toolkit", "_staging", "_tools"):
            for stage, d_dirs, files in os.walk(os.path.join(root, top)):
                # `temp` — рабочая папка на одну задачу: её файлы живут до конца разбора и в поставку не идут,
                # поэтому ссылки в них читателю ничего не обещают (находка 2026-09-22 на форуме двух сессий).
                # `reviews` — тексты разборов дословно: снимок датирован и не правится под проверки ссылок.
                d_dirs[:] = [d for d in d_dirs if d not in ("__pycache__", "archive", "handover", "temp", "reviews")]
                for f in files:
                    if not f.endswith(".md") or REF_GENERATED.match(f):
                        continue
                    m_day = re.search(r"\d{4}-\d{2}-\d{2}", f)
                    if m_day and m_day.group(0) < CONTRACT_DAY:
                        continue                      # документ старше контракта — исторический
                    doc_paths.append(os.path.join(stage, f))
        for doc in sorted(set(doc_paths)):
            if not os.path.exists(doc):
                continue
            rel_doc = os.path.relpath(doc, root).replace("\\", "/")
            for tok in sorted(set(re.findall(r"`([^`\n]+)`", read(doc)))):
                # Датированный отчёт разбора (`_staging/audit/…-2026-09-16.md`) — запись о прошлом состоянии:
                # он называет страницы и файлы, которые с тех пор могли исчезнуть (страница удаляется штатно),
                # и требовать их существования нельзя. Живые служебные документы проверяются как прежде.
                if rel_doc.startswith("_staging/audit/") and not rel_doc.endswith("audit/README.md"):
                    continue          # слой доказательств: его документы — запись закрытых циклов
                if re.search(r"_staging/audit/[^/]*\d{4}-\d{2}-\d{2}[^/]*$", rel_doc or ""):
                    continue
                t = tok.strip().strip(".,;:()")
                if not t or t.startswith(("http://", "https://", "cmd:", "python", "git ")):
                    continue
                if any(b in t for b in REF_SKIP) or not t.startswith(REF_PREFIX):
                    continue
                # Цель, которой нет в поставке (архив, игнор, локальные данные), не обрыв: см. not_delivered.
                if not os.path.exists(os.path.join(root, t.replace("/", os.sep))) and not_delivered(root, t):
                    continue
                if os.path.basename(t) in ON_DEMAND or t.rstrip("/") in ("_staging/audit/handover",):
                    continue                      # выходы проходов: появляются, когда процедура отработает
                if t.rstrip("/") in ("_staging", "_staging/audit"):
                    continue                      # голая рабочая область: появляется в работе, в поставке
                    # её нет — тот же класс, что именованные выходы выше, только без имени файла
                if REF_INSTANCE.match(t):
                    continue                      # материал экземпляра: у постороннего его ещё нет
                # Ссылка «путь:строка» — это сноска на место в файле, а не утверждение о существовании
                # имени: суффикс с номером строки отбрасываем (иначе отчёт прохода валится на своих же
                # сносках, и раздел требует существования несуществующего имени).
                # Хвост вида «:47-49» — это уточнение строк, а не часть имени; тире бывает и коротким, и длинным
                # (2026-09-22: ссылка «README.md:47–49» дала ложную находку из-за длинного тире).
                t = re.sub(r":\d+(?:[-\u2013\u2014]\d+)?$", "", t)
                if re.search(r"(?i)^(wiki/)?\.(obsidian|firecrawl)/|__pycache__/", t):
                    continue                      # объявлено вне версионного контроля: требовать существования нельзя

                if "..." in t or re.search(r"(?i)(^|/)(x|y|z|foo|bar|пример)(\.|/)", t):
                    continue                      # многоточие и однобуквенные имена — заглушки в примерах

                cands = [os.path.join(root, t), os.path.join(root, "_staging", t), os.path.join(root, "_staging", "audit", t),
                         os.path.join(os.path.dirname(doc), t), os.path.join(root, "_tools", "tg-saved", t)]
                if any(os.path.exists(c) for c in cands) or os.path.basename(t) in base_names:
                    continue
                ref_issues.append(f"{rel_doc}: ссылка на несуществующее «{t}»")

    # 35. секреты в файлах проекта: ключ, положенный в репозиторий, уходит и в историю, и в удалённую копию,
    # а оттуда его уже не вычистить без переписывания всей истории — то есть без уничтожения следов аудита.
    # Проверка появилась 2026-09-14: в выгрузке Telegram нашлась строка вида ключа, закоммиченная 12 сентября.
    secret_issues = []
    SECRET_RX = {
        "ключ вида sk-": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"),
        "токен GitHub": re.compile(r"\b(gh[ps]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})"),
        "ключ AWS": re.compile(r"\bAKIA[0-9A-Z]{12,}"),
        "бот-токен Telegram": re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{30,}\b"),
        "приватный ключ": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    }
    SECRET_JUNK = (".git", ".venv", ".venv-asr", "models", "__pycache__", "node_modules")
    SECRET_OK = re.compile(r"(sk-<|sk-\*\*\*|REDACTED|удалено: секрет|<ключ)", re.IGNORECASE)
    scanned = 0
    for sroot, sdirs, sfiles in os.walk(root):
        sdirs[:] = [d for d in sdirs if d not in SECRET_JUNK]
        for f in sfiles:
            p = os.path.join(sroot, f)
            try:
                if os.path.getsize(p) > 3_000_000:
                    continue
                txt = open(p, encoding="utf-8", errors="ignore").read()
            except (OSError, UnicodeError) as exc:
                secret_issues.append(f"{os.path.relpath(p, root)}: файл не прочитан при проверке секретов: {exc}")
                txt = ""
            scanned += 1
            for name, rx in SECRET_RX.items():
                for m in rx.finditer(txt):
                    line = txt[:m.start()].count("\n") + 1
                    if SECRET_OK.search(txt[max(0, m.start() - 40):m.end() + 40]):
                        continue
                    secret_issues.append(f"{os.path.relpath(p, root)}:{line}: похоже на {name} — "
                                         f"вырежи значение и внеси в реестр, история уже содержит его")
    if not secret_issues:
        pass

    # 36. удалённая копия: «копия есть» должно быть проверкой, а не обещанием. Реестр отправок ведёт
    # `_toolkit/offsite.py` с обратным чтением (`git ls-remote`), поэтому строка без verified=ok не считается.
    distant_issues = []
    DISTANT_MAX_DAYS = 7
    off_log = os.path.join(root, "_staging", "audit", "offsite-log.tsv")
    OFF_COLS = ("date", "remote", "branch", "commit", "files", "pack", "verified")
    if not os.path.exists(off_log):
        if domain_register_path(root, "offsite"):
            distant_issues.append("нет реестра удалённых копий `_staging/audit/offsite-log.tsv`: "
                                  "единственная копия проекта — диск, на котором он лежит")
    else:
        orows = [l for l in read(off_log).splitlines() if l.strip()]
        if not orows or tuple(orows[0].split("\t")) != OFF_COLS:
            distant_issues.append(f"шапка реестра копий не по контракту: {orows[0] if orows else 'пусто'}")
        else:
            verified_rows = [dict(zip(OFF_COLS, l.split("\t"))) for l in orows[1:]
                             if len(l.split("\t")) == len(OFF_COLS) and l.split("\t")[-1] == "ok"]
            if not verified_rows:
                distant_issues.append("удалённая копия не подтверждалась ни разу: "
                                      "запусти `python3 _toolkit/offsite.py push`")
            else:
                last = verified_rows[-1]
                try:
                    age = (datetime.date.today() - datetime.date.fromisoformat(last["date"])).days
                except ValueError:
                    distant_issues.append(f"в реестре копий нечитаемая дата: {last['date']}")
                    age = None
                if age is not None and age > DISTANT_MAX_DAYS:
                    distant_issues.append(f"удалённая копия не обновлялась {age} дн. "
                                          f"(порог {DISTANT_MAX_DAYS}): запусти `python3 _toolkit/offsite.py push`")

    # 37. Дифф-арифметика и снапшот письма (требование разбора девятого цикла).
    # Класс дефекта: в письме-9 таблица брала «было» из пакета (26), а примечание было написано от письма (27) —
    # «26 + 7 = 33» при заявленных 34. Примечание о приросте обязано сходиться с обеими ячейками строки,
    # а снапшот письма — приходить из того же прогона, что и пакет.
    diff_issues = []
    ad37 = os.path.join(root, "_staging", "audit")
    fig37 = sorted(glob.glob(os.path.join(ad37, "wiki-figures-*.json")))
    snap_now = None
    if fig37:
        snap_now = json.load(open(fig37[-1], encoding="utf-8")).get("snapshot_line", "").lstrip("> ").strip()
    RU_NUM = {"один": 1, "одна": 1, "два": 2, "две": 2, "три": 3, "четыре": 4, "пять": 5,
              "шесть": 6, "семь": 7, "восемь": 8, "девять": 9, "десять": 10}
    # Снимок сверяем только с ТЕКУЩИМ письмом даты: у прежних писем той же даты снимок своего момента, и подтягивать
    # его к сегодняшнему — это подделка истории. Случай 09-14: черновик ответа-9 краснел, когда пакет сменил снимок
    # с 38 разделов на 39, хотя письмо было написано при 38 и остаётся историческим документом.
    by_date37 = {}
    if os.path.isdir(ad37):
        for f in os.listdir(ad37):
            m = re.match(r"auditor-(?:report|response(?:-\d+[a-z]?)?)-(\d{4}-\d{2}-\d{2})\.md$", f)
            if m:
                by_date37.setdefault(m.group(1), []).append(f)
    current_letters = {max(v, key=lambda f: os.path.getmtime(os.path.join(ad37, f))) for v in by_date37.values()}
    # Дельта письма считается от НАЗВАННОГО базиса: точка сравнения — предыдущая отправка (файл + хеш из реестра
    # слепков). Дефект, который это ловит (разбор 11): таблица письма показывала «29 → 39» и «372 → 380», тогда
    # как предыдущее отправленное состояние — 38 разделов и 380 рёбер, примечание «семь новых разделов» не
    # сходилось с ячейками, а проверить было нечем: базис в диффе не назывался. Строки для сверки печатает
    # генератор пакета (`delta-<дата>.json`), потому что сравнение должно быть машинным, а не глазным.
    import datetime as _dt37
    dpath37 = os.path.join(ad37, f"delta-{_dt37.date.today().isoformat()}.json")
    delta_doc = {}
    if os.path.exists(dpath37):
        try:
            delta_doc = json.load(open(dpath37, encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError, AttributeError, TypeError) as exc:
            diff_issues.append(f"{os.path.relpath(dpath37, root)}: файл дельты не прочитан: {exc}")
            delta_doc = {}
    rows_delta = {r[0]: (r[1], r[2]) for r in delta_doc.get("rows", []) if len(r) == 3}
    base_name = (delta_doc.get("baseline") or {}).get("file", "")

    if os.path.isdir(ad37):
        for f37 in sorted(os.listdir(ad37)):
            m37 = re.match(r"auditor-(report|response(?:-\d+[a-z]?)?)-(\d{4}-\d{2}-\d{2})\.md$", f37)
            if not m37 or f37 in sent_files or f37 not in current_letters:
                continue                    # отправленное не переписываем, прежние письма даты не подтягиваем
            body37 = read(os.path.join(ad37, f37))
            m_snap = re.search(r"(?m)^snapshot:\s*>?\s*(.+)$", body37[:800])
            if snap_now and m_snap and m_snap.group(1).strip() != snap_now:
                diff_issues.append(f"_staging/audit/{f37}: снапшот письма не совпадает со снапшотом пакета")
            # Проверка дельты — только для писем ТЕКУЩЕГО цикла: у исторических писем базис свой и задним
            # числом не переписывается (то же правило, что и для снапшота).
            if delta_doc and f37 in current_letters and m37.group(2) == _dt37.date.today().isoformat():
                for row in body37.split("\n"):
                    if not row.startswith("|"):
                        continue
                    cells = [c.strip() for c in row.strip("|").split("|")]
                    if len(cells) < 3:
                        continue
                    label = re.sub(r"[*`]", "", cells[0]).strip()
                    if label not in rows_delta:
                        continue
                    nums = re.findall(r"\d+", cells[1] + " " + cells[2])
                    if len(nums) >= 2 and (int(nums[0]), int(nums[1])) != rows_delta[label]:
                        diff_issues.append(
                            f"_staging/audit/{f37}: строка «{label}» считает {nums[0]} → {nums[1]}, а от предыдущей "
                            f"отправки ({base_name or 'не названа'}) — {rows_delta[label][0]} → {rows_delta[label][1]}")
                if rows_delta and not re.search(r"(?i)базис", body37):
                    diff_issues.append(f"_staging/audit/{f37}: дельта без названного базиса — точка сравнения "
                                       f"обязана быть файлом и хешем, иначе её нельзя проверить")
                m_new37 = re.search(r"(?i)(\d+|один|одна|два|две|три|четыре|пять|шесть|семь|восемь|девять|десять)"
                                    r"\s+нов\w+\s+(раздел|канаре|источник|ребр|ребёр)", body37)
                if m_new37 and rows_delta:
                    stated = int(m_new37.group(1)) if m_new37.group(1).isdigit() else RU_NUM.get(m_new37.group(1).lower())
                    label = {"раздел": "разделов линтера", "канаре": "канареек",
                             "источник": "источников знаний", "ребр": "рёбер графа", "ребёр": "рёбер графа"}[m_new37.group(2)[:6]]
                    if stated is not None and label in rows_delta and rows_delta[label][0] + stated != rows_delta[label][1]:
                        diff_issues.append(
                            f"_staging/audit/{f37}: примечание «{m_new37.group(0).strip()}» не сходится с ячейками: "
                            f"от базиса {rows_delta[label][0]} до {rows_delta[label][1]} прошло "
                            f"{rows_delta[label][1] - rows_delta[label][0]}, а не {stated}")
            for row in body37.split("\n"):
                if not row.startswith("|") or set(row) <= set("|- "):
                    continue
                cells = [c.strip() for c in row.strip("|").split("|")]
                if len(cells) < 4:
                    continue
                m_was, m_now = re.search(r"\d+", cells[1]), re.search(r"\d+", cells[2])
                if not (m_was and m_now):
                    continue
                was, now = int(m_was.group(0)), int(m_now.group(0))
                note = cells[3]
                m_word = re.search(r"(?i)(\d+|один|одна|два|две|три|четыре|пять|шесть|семь|восемь|девять|десять)"
                                   r"\s+нов\w+\s+(раздел|канаре)", note)
                if not m_word:
                    continue
                stated = int(m_word.group(1)) if m_word.group(1).isdigit() else RU_NUM[m_word.group(1).lower()]
                if now - was != stated:
                    diff_issues.append(f"_staging/audit/{f37}: «{stated} новых» при {was} → {now} — арифметика "
                                       f"не сходится (прирост {now - was})")
                rng = re.search(r"\((\d+)\s*[–—-]\s*(\d+)\)", note)
                if rng:
                    lo, hi = int(rng.group(1)), int(rng.group(2))
                    if hi - lo + 1 != stated:
                        diff_issues.append(f"_staging/audit/{f37}: диапазон {lo}–{hi} не покрывает {stated} новых")
                    if lo != was + 1:
                        diff_issues.append(f"_staging/audit/{f37}: диапазон начинается с {lo}, а предыдущих было "
                                           f"{was} (ожидалось {was + 1}) — «было» и примечание из разных слоёв")

    # 38. Реестр задач (`_toolkit/tasks.py`): шаг не может ссылаться на удалённый скрипт.
    # Класс дефекта известен по README папки аудита, который ссылался на удалённый шаблон (§34);
    # здесь он проверяется у кода, а не у документа. Ссылка может вести во вложенную папку
    # (`tools/tg-saved/promote_approved.py`) — сверяется с тем же корнем механизма.
    task_issues = []
    tasks_py = toolkit_script("tasks.py")
    if not os.path.exists(tasks_py):
        task_issues.append("нет _toolkit/tasks.py: цепочка проверок снова живёт только внутри хука")
    else:
        body_t = read(tasks_py)
        for rel in sorted(set(re.findall(r'"((?:[\w-]+/)*[\w-]+\.py)"', body_t))):
            if not os.path.exists(toolkit.script(rel)):
                task_issues.append(f"_toolkit/tasks.py: шаг ссылается на несуществующий _toolkit/{rel}")

    # 39. границы версионного контроля: решённое не публиковать не должно возвращаться в индекс.
    # Раздел появился после реального дефекта (2026-09-14): решение «рабочие выгрузки Telegram вне контроля»
    # записали сообщением коммита, шаблона в .gitignore не добавили, и 684 файла (89 МБ, включая личные
    # документы) уехали в удалённую копию. Проверяем две вещи: правило объявлено (строкой, не упоминанием
    # в комментарии) и — когда рядом есть рабочее дерево git — ничего из исключённого в индексе не осталось.
    boundary_issues = []
    # Правила интерфейса Obsidian проверяются тем же разделом: класс «решение записали, правила не завели»
    # дал реальный дефект (684 файла уехали в удалённую копию), а закрытый пункт `obsidian-noise` стоял
    # доказанным лишь наличием `.gitignore`. Теперь правило проверяется строкой и индексом, как telegram.
    BOUNDARY_PATHS = ["_staging/telegram/", "wiki/.obsidian/workspace.json", "wiki/.obsidian/graph.json",
                      "wiki/.obsidian/backlink.json", "wiki/.obsidian/cache", "/.obsidian/"]
    gi_path = os.path.join(root, ".gitignore")
    if not os.path.exists(gi_path):
        boundary_issues.append("нет .gitignore — границы версионного контроля не объявлены")
    else:
        rules = [l.strip().rstrip("/") for l in read(gi_path).splitlines()
                 if l.strip() and not l.strip().startswith("#")]
        for bp in BOUNDARY_PATHS:
            if bp.rstrip("/") not in rules:
                boundary_issues.append(f".gitignore: нет строки-правила для «{bp}» — исключённое вернётся "
                                       f"в индекс при `git add -A` (упоминание в комментарии не считается)")
            full = os.path.join(root, bp.rstrip("/"))
            if os.path.isdir(full) and os.path.isdir(os.path.join(root, ".git")):
                tracked = subprocess.run(["git", "-C", root, "ls-files", "--", bp],
                                         capture_output=True, text=True, encoding="utf-8", errors="replace", check=False).stdout.split()
                if tracked:
                    boundary_issues.append(f"{bp}: в индексе {len(tracked)} файлов, а папка объявлена "
                                           f"вне версионного контроля")

    # 40. литература: материал без авторского разбора (анонс, курс, голая ссылка, подборка) помечается
    # в карточке полем `material_role: literature` и должен иметь адрес в списке подобных — иначе он теряется.
    # Раздел появился после реального дефекта (2026-09-15): анонс бесплатного курса стоял пятым пунктом
    # раздела «Как это устроено» в краеугольной статье `harness-architecture`, в ряду подходов практиков.
    # Проверка «литература не объявлена источником страницы» снята решением владельца 2026-09-15:
    # подборка 113402 остаётся в `sources:` статьи mastra — это провенанс, а не признание голосом темы;
    # «не голос раздела» — суждение человека, машинно оно не проверяется.
    literature_issues = []
    cards_dir = os.path.join(root, "_staging", "cards")
    lit_files = set()
    if os.path.isdir(cards_dir):
        for fn in sorted(os.listdir(cards_dir)):
            if not fn.endswith(".md"):
                continue
            if re.search(r"(?m)^material_role:\s*literature\b", read(os.path.join(cards_dir, fn))):
                lit_files.add(fn)
    if lit_files and os.path.isdir(cards_dir):
        mreg = os.path.join(vault, "comparisons", "materials-registry.md")
        reg_text = read(mreg) if os.path.exists(mreg) else ""
        # адрес ищем именно в блоке «Литература»: реестр держит два блока, и строка,
        # случайно оказавшаяся в другом, адресом для метки владельца не является
        m_lit = re.search(r"(?ms)^##\s+Литература\s*$(.*?)(?=^##\s|\Z)", reg_text)
        block = m_lit.group(1) if m_lit else ""
        for fn in sorted(lit_files):
            if fn[:-3] not in block:
                literature_issues.append(f"литература без строки в блоке «Литература» реестра материалов: {fn[:-3]}")

    # 41. иллюстрации: у каждого описанного изображения обязан быть сам файл и вставка в текст.
    # Класс дефекта (2026-09-15): в 28 источниках стояло машинное описание картинки, а самой картинки
    # не было ни в репозитории, ни в хранилище — читатель видел рассказ о том, чего не видно.
    # Правило узкое: проверяются заголовки `### <файл>.jpg|png|…` — форма, которой описания в источниках
    # волны и размечены; у корпуса GRACE вставки идут без таких заголовков и не трогаются.
    ill_issues = []
    have_images = set()
    for _base, _dirs, _files in os.walk(os.path.join(root, "raw")):
        if os.path.basename(_base) == "images":
            for _fn in _files:
                have_images.add(_fn)
    for _base, _dirs, _files in os.walk(os.path.join(root, "raw")):
        for _fn in sorted(_files):
            if not _fn.endswith(".md"):
                continue
            _p = os.path.join(_base, _fn)
            _t = read(_p)
            for _n in re.findall(r"(?m)^### (\S+\.(?:jpg|jpeg|png|webp|gif))\s*$", _t):
                if _n not in have_images:
                    ill_issues.append(f"{os.path.relpath(_p, root)}: описан файл {_n}, а самого файла нет ни в одной папке images")
                elif f"![[{_n}]]" not in _t:
                    ill_issues.append(f"{os.path.relpath(_p, root)}: описание {_n} без вставки ![[{_n}]] — картинка не покажется")

    # 42. Источники корпуса telegram: в КОПИИ внутри хранилища есть раздел «Где использован» со ссылками
    # на участки страниц. Раздел производный (`_toolkit/source_backlinks.py`): мастер `raw/` его не несёт,
    # потому что копия живёт в хранилище самостоятельно (решение владельца 2026-09-15 «отключить сверку
    # зеркал»). Смысл проверки — HITL: по копии видно, где источник использован, и это можно сверить глазами.
    back_issues = []
    pages_by_slug = {}
    for d in PAGE_DIRS:
        full = os.path.join(vault, d)
        if not os.path.isdir(full):
            continue
        for fn in sorted(os.listdir(full)):
            if not fn.endswith(".md"):
                continue
            t = read(os.path.join(full, fn))
            fm = re.match(r"^---\r?\n(.*?)\r?\n---\r?\n?", t, re.DOTALL)
            body = t[fm.end():] if fm else t
            pages_by_slug[fn[:-3]] = (re.findall(r"(?m)^#{2,3}\s+(.+?)\s*$", body),
                                      fmparse.items(t, "sources"))
    tg_dir = os.path.join(root, "raw", "telegram")
    for fn in (sorted(os.listdir(tg_dir)) if os.path.isdir(tg_dir) else []):
        if not fn.endswith(".md"):
            continue
        rel_src = f"raw/telegram/{fn}"
        dst = os.path.join(vault, "sources", "raw", "telegram", fn)
        if not os.path.exists(dst):
            continue                                   # отсутствие копии — дело раздела 8
        text = read(dst)
        i = text.find(BACKLINK_HEADING)
        if i < 0:
            back_issues.append(f"{rel_src}: в копии нет раздела «Где использован» (пересобрать: _toolkit/source_backlinks.py --write)")
            continue
        listed = []
        for m in re.finditer(r"\[\[([^\]\|#]+)(?:#([^\]\|]+))?(?:\|[^\]]*)?\]\]", text[i:]):
            slug, head = m.group(1).strip(), (m.group(2) or "").strip()
            if slug not in listed:
                listed.append(slug)
            if slug not in pages_by_slug:
                back_issues.append(f"{rel_src}: раздел ссылается на несуществующую страницу [[{slug}]]")
            elif head and head not in pages_by_slug[slug][0]:
                back_issues.append(f"{rel_src}: ссылка [[{slug}#{head}]] — такого раздела на странице нет")
        for slug, (heads, declared) in sorted(pages_by_slug.items()):
            declares = any(d.replace(os.sep, "/").lstrip("./") == rel_src for d in declared)
            if declares and slug not in listed:
                back_issues.append(f"{rel_src}: страница {slug} объявляет источник в sources:, а в разделе «Где использован» её нет")
            if slug in listed and not declares:
                back_issues.append(f"{rel_src}: раздел называет {slug}, а страница этот источник не объявляет")

    # 43. Кандидаты в страницы: у каждого обязано быть решение. Порог схемы («2+ источника ИЛИ
    # центральна для одного») считает машина (`_toolkit/candidates.py`), различает человек: решение —
    # страница или строка отказа с причиной в `_staging/candidate-decisions.tsv`. Висящий кандидат
    # (нет ни того, ни другого) — находка. Проверка не про «куда приткнули источник», а про то,
    # что вопрос «нужна ли страница» не остался без ответа; число убывает по мере работы.
    candidate_issues = []
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import candidates as _cand
        for c in _cand.hanging(vault, root):
            candidate_issues.append(
                f"кандидат «{c['name']}» (источников {c['sources']}, страниц {c['pages_mention']}): "
                "нет ни страницы, ни отказа с причиной")
    except (ImportError, OSError, UnicodeError, RuntimeError, ValueError, KeyError, TypeError, IndexError, AttributeError) as _ex:
        candidate_issues.append(f"счёт кандидатов не запустился: {str(_ex)[:120]}")

    # 45. Язык смысловых ячеек и пунктов «Спорного». Канон — SCHEMA.md, «Язык описания расхождений»:
    #     то, что человек читает, чтобы принять решение (позиции, первопричины, статусы, аргументы,
    #     решения), пишется полными предложениями, а не телеграфными цепочками. Исключения объявлены
    #     в SCHEMA и здесь: справочные колонки (адрес, файл, число, дата, имя, шкала) и сгенерированные
    #     страницы — иначе проверка жжёт ложными и перестаёт что-либо значить.
    PROSE_COL = re.compile(r"(позици|первопричин|статус|что утверждает|почему|аргумент|довод|расхожден|"
                           r"что это меняет|следстви|вывод|смысл|отличи|суть|тезис|решение|"
                           r"практики корпуса|вендорские руководства|чем подтверждена|что болит)", re.IGNORECASE)
    REF_COL = re.compile(r"^(файл|ссылка|источник|источники|страница|слаг|slug|id|дата|число|значение|"
                         r"метрика|команда|скрипт|путь|тип|класс|версия|автор|кто|где|когда|прочность|"
                         r"заметок|#|№)$", re.IGNORECASE)
    GENERATED_PAGES = {"quality-metrics", "corpus-lineage", "source-registry", "topic-map", "index",
                       "wiki-state", "tools-registry", "materials-registry"}

    def _cell_why(cell, min_len):
        if "→" in cell or "->" in cell:
            return "цепочка стрелок вместо фразы"
        if not re.search(r"[.!?]", cell) and len(cell) < 200:
            return "назывная строка вместо предложения"
        if len(cell) < min_len:
            return "обрубок: %d знаков при пороге %d" % (len(cell), min_len)
        return ""

    lang_issues = []
    _cmap = domain_register_path(root, "conflicts")      # может быть None: карту объявляет экземпляр
    if _cmap and os.path.exists(_cmap):
        _head = None
        for _ln in read(_cmap).split("\n"):
            if _ln.startswith("## "):
                _head = _ln[3:].strip()
                continue
            if not _ln.startswith("|") or _head != "Таблица конфликтов":
                continue
            _cs = [c.strip() for c in _ln.strip().strip("|").split("|")]
            if not _cs or not re.match(r"^\**\d+\**$", _cs[0]):
                continue
            _num = _cs[0].strip("*")
            # Колонки: 0 номер, 1 спор, 2 позиция A, 3 позиция B, 4 статус, 5 когда A, 6 когда B, 7 чем подтверждено.
            _fields = [("позиция A", _cs[2] if len(_cs) > 2 else "", 160),
                       ("позиция B", _cs[3] if len(_cs) > 3 else "", 160),
                       ("статус", _cs[4] if len(_cs) > 4 else "", 80),
                       ("когда выигрывает A", _cs[5] if len(_cs) > 5 else "", 80),
                       ("когда выигрывает B", _cs[6] if len(_cs) > 6 else "", 80),
                       ("чем подтверждено и куда движется", _cs[7] if len(_cs) > 7 else "", 80)]
            for _name, _cell, _min in _fields:
                _why = _cell_why(_cell, _min)
                if _why:
                    lang_issues.append("карта · спор №%s · %s — %s" % (_num, _name, _why))

    for _slug, _text in sorted(bodies.items()):
        if _slug in GENERATED_PAGES:
            continue
        _sec, _head, _fence = "", None, False
        for _n, _ln in enumerate(_text.split("\n"), 1):
            if _ln.startswith("```"):
                _fence = not _fence
                continue
            if _fence:
                continue
            if _ln.startswith("|"):
                _cs = [x.strip() for x in _ln.strip().strip("|").split("|")]
                if all(re.fullmatch(r":?-{2,}:?", x) for x in _cs if x):
                    continue
                if _head is None:
                    _head = _cs
                    continue
                for _i, _cell in enumerate(_cs):
                    _col = (_head[_i] if _i < len(_head) else "").strip("* ")
                    if not _cell or len(_cell) < 8 or not _col or REF_COL.match(_col) \
                            or not PROSE_COL.search(_col):
                        continue
                    _why = _cell_why(_cell, 120)
                    if _why:
                        # Идентичность ячейки: строка (первая ячейка) + колонка. Без неё сообщение
                        # одно на всю колонку страницы, и канарейка не отличает подменённую ячейку
                        # от соседней.
                        _row_id = (_cs[0][:34] + "…") if len(_cs[0]) > 34 else _cs[0]
                        lang_issues.append("%s · строка «%s» · колонка «%s» — %s"
                                           % (_slug, _row_id, _col, _why))
            elif _ln.startswith("#"):
                _sec, _head = _ln.lstrip("# ").strip(), None
            elif re.match(r"^\s*[-*]\s+\S", _ln) and re.search(r"(?i)спорн|расхожден|конфликт|противореч", _sec):
                _b = re.sub(r"^\s*[-*]\s+", "", _ln).strip()
                if len(_b) > 12 and ("→" in _b or "->" in _b or len(_b) < 60):
                    _why = "цепочка стрелок вместо фразы" if ("→" in _b or "->" in _b) else "обрубок пункта"
                    lang_issues.append("%s · «Спорное» — %s" % (_slug, _why))

    # 46. Источник добавлен — смысловой проход по нему пройден (канон 2026-09-16). Логика живёт
    #     отдельным модулем, как у кандидатов и сторожей реестров: так её можно прогнать в одиночку —
    #     `python3 _toolkit/semantic_coverage.py --wiki .`.
    semantic_issues = []
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import semantic_coverage as _cov
        semantic_issues = _cov.issues(root)
    except (ImportError, OSError, UnicodeError, RuntimeError, ValueError, KeyError, TypeError, IndexError, AttributeError) as _ex:
        semantic_issues.append("проверка покрытия источников проходом не запустилась: " + str(_ex)[:120])

    # 50. Панели владельца не отстают от очереди: вход новее панели — панель собрана из старых данных
    #     (случай 2026-09-16: владелец не увидел в дашборде двадцать два новых кандидата). Логика —
    #     отдельным модулем: `python3 _toolkit/panel_freshness.py --wiki .`.
    panel_issues = []
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import panel_freshness as _pfresh
        panel_issues = _pfresh.issues(root)
    except (ImportError, OSError, UnicodeError, RuntimeError, ValueError, KeyError, TypeError, IndexError, AttributeError) as _ex:
        panel_issues.append("проверка свежести панелей не запустилась: " + str(_ex)[:120])

    # 49. Строки реестра инструментов: тип из закрытого набора, никаких людей и компаний, никаких счётчиков
    #     (канон 2026-09-16). Логика — отдельным модулем: `python3 _toolkit/registry_rows.py --wiki .`.
    registry_row_issues = []
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import registry_rows as _rrows
        registry_row_issues = _rrows.issues(root)
    except (ImportError, OSError, UnicodeError, RuntimeError, ValueError, KeyError, TypeError, IndexError, AttributeError) as _ex:
        registry_row_issues.append("проверка строк реестра не запустилась: " + str(_ex)[:120])

    # 48. Форма таблиц страницы болей: четыре канонические колонки, одна боль — одна строка, без слитых
    #     ячеек (канон 2026-09-16). Появилась после находки владельца: волна инжеста завела раздел болей
    #     заметок формой «строка на класс боли» с ячейками по 1900 знаков. Логика — отдельным модулем:
    #     `python3 _toolkit/pain_shape.py --wiki .`.
    pain_shape_issues = []
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import pain_shape as _pshape
        pain_shape_issues = _pshape.issues(root)
    except (ImportError, OSError, UnicodeError, RuntimeError, ValueError, KeyError, TypeError, IndexError, AttributeError) as _ex:
        pain_shape_issues.append("проверка формы болей не запустилась: " + str(_ex)[:120])

    # 47. Сцепка таблицы карты конфликтов с её разделами («Ключевые конфликты подробнее» и «Как этим
    #     пользоваться»): каждая строка обязана быть названа своим номером ниже таблицы. Логика —
    #     отдельным модулем: `python3 _toolkit/map_narrative_coupling.py --wiki .`.
    narrative_issues = []
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import map_narrative_coupling as _coupl
        narrative_issues = _coupl.issues(root)
    except (ImportError, OSError, UnicodeError, RuntimeError, ValueError, KeyError, TypeError, IndexError, AttributeError) as _ex:
        narrative_issues.append("проверка сцепки разделов карты не запустилась: " + str(_ex)[:120])

    # 51. Заметки, тело которых — ссылка без контекста: в теле обязан быть блок «Что за ссылкой».
    # Владелец 2026-09-16: «если в заметке есть ссылка без контекста, то надо по ней сходить и краткое
    # резюме сохранить в заметке, что за той ссылкой». Разбор прошёл 2026-09-16 (двенадцать заметок),
    # поэтому проверка стоит на настоящем дефекте, а не страхует: до разбора она показывает эти заметки.
    linkless_issues = []
    for _p in sorted(glob.glob(os.path.join(root, "raw", "telegram", "*.md"))):
        _t = read(_p)
        _parts = _t.split("---", 2)
        _body = _parts[2] if _t.startswith("---") and len(_parts) > 2 else _t
        if not re.search(r"https?://", _body):
            continue
        # блок «Что за ссылкой» из тела вычитается: он и есть требуемое, а не авторский текст
        _rest = re.sub(r"(?ms)^##\s+Что за ссылкой\s*$.*?(?=^##\s|\Z)", "", _body)
        _rest = re.sub(r"https?://\S+", "", _rest)
        _rest = re.sub(r"[\s\-–—•*]+", "", _rest)
        if len(_rest) > 10:          # в теле есть авторский текст — это не ссылка без контекста
            continue
        if not re.search(r"(?m)^##\s+Что за ссылкой\b", _body):
            linkless_issues.append(os.path.relpath(_p, root).replace(os.sep, "/") +
                                   ": тело записи — только ссылка, а блока «Что за ссылкой» нет")

    # 52. Реестр материалов: строки-сервисы. Сервис — это инструмент, и его место в реестре инструментов
    # (владелец 2026-09-16: «в реестр материалов для чтения попали сервисы, сервисы должны храниться в
    # реестре инструментов»). Разбор прошёл 2026-09-16: две строки, DeepWiki и Zread; поэтому проверка
    # стоит на настоящем дефекте — до правки она их и показывает.
    service_rows = []
    _mp = os.path.join(vault, "comparisons", "materials-registry.md")
    if os.path.exists(_mp):
        for _i, _line in enumerate(read(_mp).split("\n"), 1):
            if not _line.startswith("|"):
                continue
            _cells = [c.strip() for c in _line.strip().strip("|").split("|")]
            if len(_cells) < 2 or set("".join(_cells)) <= set("-: ") or re.match("(?i)^(материал|#)", _cells[0]):
                continue
            if re.match("(?i)^сервис\\b", _cells[1]):
                service_rows.append("materials-registry.md:%d: «%s» — строка сервиса, её место в реестре инструментов"
                                    % (_i, _cells[0][:60]))

    # 54. Поля шапки источника: только из набора для своего типа (SCHEMA, «Поля шапки источника»).
    # Владелец 2026-09-17: «служебные мета-поля создавались стихийно, нужно сократить их число, оставить
    # только действительно полезное». Сторож поставлен ДО миграции: на тот день он показывал 254 лишних
    # `sources: [telegram]`, 58 `source_author`, 342 `sha256`, 10 `updated` и 2 `source_message_ids`.
    FIELDS = {
        "telegram_saved": {"title", "type", "tags", "summary", "status", "source_channel", "source_date",
                           "source_message_id", "source_message_ids", "source_url", "source_links",
                           "authors", "created",
                           "ingested", "context_note", "context_source"},
        "article": {"title", "type", "tags", "summary", "status", "source_url", "authors", "source_note",
                    "source_channel", "source_date", "created", "ingested"},
        "manifest": {"title", "type", "tags", "summary", "status", "sources", "authors", "source_url",
                     "version", "created"},
        "report": {"title", "type", "tags", "summary", "status", "sources", "authors", "source_url",
                   "version", "created"},
        "transcript": {"title", "type", "tags", "summary", "status", "sources", "authors", "source_url",
                       "version", "created"},
        "paper": {"title", "type", "tags", "summary", "status", "sources", "authors", "source_url",
                  "version", "created"},
        "prompt": {"title", "type", "tags", "summary", "status", "sources", "authors", "source_url",
                   "version", "created"},
    }
    field_issues = []
    _fm_files = (sorted(glob.glob(os.path.join(root, "raw", "**", "*.md"), recursive=True)) +
                 sorted(glob.glob(os.path.join(vault, "sources", "raw", "**", "*.md"), recursive=True)))
    for _p in _fm_files:
        _t = read(_p)
        _m = re.match(r"^---\r?\n(.*?)\r?\n---", _t, re.DOTALL)
        if not _m:
            continue
        _rel = os.path.relpath(_p, root).replace(os.sep, "/")
        _ty = re.search(r"(?m)^type:\s*([^\n#]+)", _m.group(1))
        _ty = _ty.group(1).strip().strip('"') if _ty else ""
        _allowed = FIELDS.get(_ty)
        if not _allowed:
            field_issues.append(_rel + ": тип «" + (_ty or "не назван") + "» вне известных наборов полей")
            continue
        for _k in re.findall(r"(?m)^([A-Za-z0-9_\-]+):", _m.group(1)):
            if _ty == "telegram_saved" and _k == "sources":
                # у записи Telegram `sources` допустим, только если это не тавтология [telegram]:
                # настоящее описание происхождения (голосовая выгрузка, способ распознавания) — данные.
                if re.search(r"(?m)^sources:\s*\[telegram\]\s*$", _m.group(1)):
                    field_issues.append(_rel + ": поле sources вне набора типа telegram_saved "
                                               "(тавтология [telegram]: тип записи уже это говорит)")
                continue
            if _k not in _allowed:
                field_issues.append(_rel + ": поле " + _k + " вне набора типа " + _ty)

    moved_rows = check_moved_rows(root)
    life_rows = check_corpus_record_life(root)
    quote_rows = check_quotes_declared(root)
    staged_rows = check_staged_copies(root)
    slug_rows = check_page_slugs(root)
    heading_rows = check_heading_links(root)
    card_rows = check_source_cards(root)
    reglink_rows = check_registry_link_rows(root)
    card_shape_rows = check_card_shape(root)
    script_rows = check_scripts_reachable(root)
    unclear_rows = check_media_unclear(root)
    audio_rows = check_audio_transcripts(root)
    registry_link_rows = check_registry_links(root)
    materials_link_rows = check_materials_links(root)
    record_link_rows = check_record_links(root)
    card_cited_rows = check_cards_cited(root)
    src_field_rows = check_source_required_fields(root)
    stray_mirror_rows = check_mirror_without_master(root)
    try:
        import rules_check
        rules_rows = rules_check.check(root, brief=True)
    except (ImportError, OSError, UnicodeError, RuntimeError, ValueError, KeyError, TypeError, IndexError, AttributeError) as e:
        rules_rows = [("rules.tsv", "проверку реестра не удалось запустить: %s" % e, "")]
    schema_boundary_issues = check_schema_boundary(root)
    page_wave_rows = check_page_wave(root)
    external_read_issues = check_external_reads(root)
    sections = [
        ("1. Битые wikilinks", [f"{s}: {', '.join(v)}" for s, v in broken.items()]),
        ("2. Сироты (нет входящих ссылок)", orphans),
        ("3. Нет в index.md", missing_in_index + index_structure),
     ("82. Служебный документ: признаки порчи записи", service_docs),
     ("83. Файл под .gitignore не отслеживается", ignored_tracked),
     ("84. Выход в _staging/audit/ держится ссылкой из поставки", audit_refs),
     ("85. Входные файлы не растут: потолки строк", readme_ceiling),
     ("86. Свод тезисов: полнота", theses_digest),
     ("87. Служебный документ держит требования, а не хронику", chronicle),
     ("88. Служебные документы не разрастаются", ceilings),
     ("89. Объявление домена экземпляра", domain_issues),
     ("90. Раскладка механизма: свои файлы рядом с собой", check_toolkit_layout(root)),
      ("91. Домен экземпляра не живёт в файлах механизма", check_domain_leak(root)),
       ("92. Пути областей экземпляра строятся через toolkit", instance_path_issues),
       ("93. Локальный канон и хроника не живут в SCHEMA.md", schema_boundary_issues),
        ("94. Страница принадлежит волне", page_wave_rows),
        ("95. Чтения вне поставки объявлены", external_read_issues),

         ("4. Проблемы frontmatter", [f"{s}: {m}" for s, m in fm_issues]),

        ("5. Теги вне таксономии", [f"{s}: {t}" for s, t in tag_issues]),
        ("7. Страницы > 200 строк", [f"{p} — {n} строк" for p, n in long_pages]),
        ("8. Копии источников: наличие в хранилище", out_of_sync),
        ("9. Таблицы: ссылки, пустые ячейки, колонки", table_issues),
        ("10. Пустые файлы", empty_files),
        ("11. Провенанс: инлайновые сноски запрещены", prov_issues),
        ("12. Лог", log_notes),
        ("13. Свежесть и сила доказательства", [f"{s}: {m}" for s, m in aging]),
        ("14. Отчёт против машинного вывода", report_issues),
        ("15. Числа без владельца в «Фактах и цифрах»", number_issues),
        ("17. Сила доказательства против уверенности", evidence_issues),
        ("18. Мягкий предел длины страницы", split_issues),
        ("19. Эффективный размер выборки (аффилированный корпус)", ess_issues),
        ("20. Провенанс: упомянутые источники объявлены", prov_gaps),
        ("21. Ручные числа в прозе пакета", prose_issues),
        ("22. Остатки черновика и дубли секций", draft_issues),
        ("23. Процесс в контенте вместо фактов", process_issues),
        ("24. Ось stance и открытые противоречия", stance_issues),
        ("25. Ось contradicts отражена в карте конфликтов", unaudited_stance),
        ("26. Машинное чтение как класс доказательства", ocr_issues),
        ("27. Утверждения о прошлых пакетах сверены со слепками", past_claims),
        ("28. Реестр техдолга", debt_issues),
        ("29. Карта структуры: актуальность и замысел", map_issues),
        ("30. Мостик сессии: окно и отказы", bridge_issues),
        ("31. Следствия утверждений карты", consequence_issues),
        ("32. Письмо и ответ аудитору: frontmatter", letter_issues + sent_issues),
        ("33. Охват болей корпуса", pain_issues),
        ("34. Внутренние ссылки служебных документов", ref_issues),
        ("35. Секреты в файлах проекта", secret_issues),
        ("36. Удалённая копия: свежесть и подтверждение", distant_issues),
        ("37. Дифф-арифметика и снапшот письма", diff_issues),
        ("38. Реестр задач: шаги существуют", task_issues),
        ("39. Границы версионного контроля", boundary_issues),
        ("40. Литература: адрес в блоке «Литература» реестра", literature_issues),
        ("42. Источники: раздел «Где использован» в копии", back_issues),
        ("41. Иллюстрации: файл и вставка на месте", ill_issues),
        ("43. Кандидаты в страницы: решение по каждому", candidate_issues),
        ("45. Язык смысловых ячеек и «Спорного»", lang_issues),
        ("46. Источник добавлен — смысловой проход пройден", semantic_issues),
        ("47. Сцепка разделов карты с таблицей", narrative_issues),
        ("48. Форма таблиц страницы болей", pain_shape_issues),
        ("49. Строки реестра инструментов", registry_row_issues),
        ("50. Панели владельца: свежесть", panel_issues),
        ("51. Ссылки без контекста: разбор в заметке", linkless_issues),
        ("52. Реестр материалов: строки-сервисы", service_rows),
        ("54. Поля шапки источника: только из набора типа", field_issues),
        ("55. Реестр: строка, убранная переездом, и её адресат", moved_rows),
        ("57. Срок жизни записи корпуса: утверждение и вклеенные выжимки", life_rows),
        ("58. Цитата из источника, объявленного в шапке", quote_rows),
        ("59. Реестр правил: сходится с файлами", rules_rows),
        ("61. Площадка приёмки: копия опубликованной страницы", staged_rows),
        ("62. Имя страницы: латиница", slug_rows),
        ("63. Заголовок без вики-ссылок", heading_rows),
        ("64. Источник без карточки", card_rows),
        ("65. Ссылочная заметка в шапке реестра — строкой", reglink_rows),
        ("66. Форма карточки извлечения", card_shape_rows),
        ("67. Скрипт площадки назван в документах волны", script_rows),
        ("68. Непонятные записи изображений: решение по каждой", unclear_rows),
        ("69. У каждой карточки есть адрес в вики", card_cited_rows),
        ("70. Обязательные поля шапки источника", src_field_rows),
        ("71. Копия источника без мастера в raw/", stray_mirror_rows),
        ("72. Аудио в сырье расшифровано", audio_rows),
        ("73. Реестр инструментов: адрес взят из записи", registry_link_rows),
        ("74. Реестр материалов: колонка ссылки", materials_link_rows),
        ("75. Адрес из выгрузки доехал до записи", record_link_rows),
        ("76. Адрес стоит в колонке ссылки", check_registry_address_column(root)),
        ("77. Таблица не склеена с абзацем", check_table_glued(root)),
        ("78. Названный артефакт несёт адрес сам", check_address_delegated_to_record(root)),
        ("79. Текст о предмете, а не о сборке страницы", check_process_meta_in_page(root)),
        ("80. Факты, спорное, границы и грабли: находка, а не отчёт о покрытии", check_section_filler(root)),
        ("81. Требования к стилю живут в одном месте", check_style_single_source(root)),
    ]
    total = 0
    for title, items in sections:
        out.append(f"## {title}: {len(items)}")
        # Срез в 40 строк на раздел нужен человеку, но ломает аттестацию: если раздел красный,
        # снятая мутацией находка вытаскивает в печать ранее скрытую — канарейка-«безвредная»
        # объявляется ложным срабатыванием, а настоящая пойманная может спрятаться. Прогон
        # аттестации идёт с --all-issues (см. _toolkit/canary_test.py).
        cap = 10 ** 9 if getattr(args, "all_issues", False) else 40
        out.extend(f"- {i}" for i in items[:cap])
        if len(items) > cap:
            out.append(f"- … ещё {len(items) - cap}")
        out.append("")
        # Разделы 30 и 36 смотрят в историю коммитов и в реестр отправок владельца. В распакованном архиве
        # (без `.git`) их находки верны для этой среды, но скачавшему ничего не говорят, и красный счёт на
        # первом запуске — дефект входа. Счёт идёт без них, сами разделы печатаются как есть, чтобы канарейки
        # по-прежнему видели срабатывание.
        if not HAS_GIT and title.split(".", 1)[0] in ("30", "36"):
            continue
        total += len(items)
    print("\n".join(out))
    print(f"ИТОГО проблем: {total}")
    if not HAS_GIT:
        print("(в архиве без `.git` в счёт не вошли разделы 30 и 36: истории коммитов и реестра отправок "
              "владельца здесь нет)")
    summary_path = args.json
    if not summary_path and not args.no_summary:
        ad = os.path.join(root, "_staging", "audit")
        if os.path.isdir(ad):
            # Имя несёт привязку: дата, время и закладка замера (решение владельца 2026-09-26:
            # «зелёное» поднимается и сверяется по файлу, а не остаётся фразой в чате).
            # Вторая строка привязки — поля rev/tree_dirty внутри: сводка грязного дерева описывает
            # рабочее состояние поверх закладки, а не саму закладку.
            _now = __import__('datetime').datetime.now()
            _rev = _tree_rev(root)
            _pin = ("-%s" % _rev) if _rev and _rev != "—" else ""
            summary_path = os.path.join(
                ad, "lint-summary-%s-%s%s.json" % (_now.date().isoformat(), _now.strftime("%H%M"), _pin))
    if summary_path:
        import json as _json2
        _json2.dump({"sections": len(sections), "total": total,
                     "titles": [t for t, _ in sections],
                     "by_section": {title: len(items) for title, items in sections},
                     # Ревизия и состояние дерева: сводка — замер рабочего дерева, и без этих полей её нельзя
                     # ни воспроизвести, ни отличить от замера на другом коммите (находка владельца 2026-09-22).
                     "rev": _tree_rev(root), "tree_dirty": _tree_dirty(root),
                     "date": __import__("datetime").date.today().isoformat()},
                    open(summary_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(f"машинная сводка: {summary_path}")
    if args.write_log:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"\n## [{__import__('datetime').date.today().isoformat()}] lint | {total} issues found\n")
            for title, items in sections:
                if items:
                    f.write(f"- {title}: {len(items)}\n")
        print(f"Запись добавлена в журнал: {log_path}")
    return total


if __name__ == "__main__":
    sys.exit(1 if main() else 0)

