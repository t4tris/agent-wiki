#!/usr/bin/env python3
"""Прогон волны инжеста: одна точка входа от сырья до страниц.

Замысел владельца (2026-09-17): «ТЫ не забыл про единый скрипт, который прогоняет волну целиком от сырья до
страниц? первая половина сейчас выполняется по протоколу агентом». Текст карточек и страниц машина не напишет —
пишет модель. Но она может не дать волне двигаться дальше, пока артефакт не предъявлен и не прошёл приёмку:
у каждого этапа есть список требований, и состояние каждого требования либо машинное (раздел линтера, очередь,
отчёт), либо явно помечено как «нужен владелец».

    python3 _toolkit/wave_runner.py --wiki . status        # состояние всех этапов: что готово, чего не хватает
    python3 _toolkit/wave_runner.py --wiki . step 3        # приёмка одного этапа
    python3 _toolkit/wave_runner.py --wiki . begin --selection <квитанция> --inbox <папка> --corpus <имя>
    python3 _toolkit/wave_runner.py --wiki . begin --collected --corpus <имя> --inbox <папка> --by <имя>    python3 _toolkit/wave_runner.py --wiki . close --confirm --label "инжест 2026-09-17"

Возвращает 1, если хоть одно требование не выполнено: это ворота, а не отчёт для чтения.
Вход волны объявляется в `begin` — двумя способами (квитанция отбора либо «собрано вручную»);
необъявленный вход отказывается. Решение объявляет владелец, состояние входа фиксирует инструмент.
"""
# Требования к тексту страниц — `_toolkit/style.md`; ворота ниже проверяют разделы 75, 78, 79, 80.
import argparse
import datetime
import hashlib
import io
import json
import os
import re
import subprocess
import sys

import toolkit

HERE = os.path.dirname(os.path.abspath(__file__))
WAVE_ID = re.compile(r"^[A-Za-z0-9_.-]+$")


class CheckError(RuntimeError):
    pass


def _text(value):
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return value or ""


def _result_text(result):
    parts = [_text(result.stdout).strip(), _text(result.stderr).strip()]
    return "\n".join(part for part in parts if part)


def _failure(label, result):
    detail = _result_text(result)
    return "%s: %s" % (label, detail or "код возврата %d" % result.returncode)


def _last_line(text, fallback):
    lines = [line for line in text.splitlines() if line.strip()]
    return lines[-1] if lines else fallback


# Этапы волны: что требуется и какими машинными признаками это подтверждается.
# «Линтер §N» — раздел линтера обязан быть в нуле; «очередь» — свой список ниже; «слово» — это работа агента,
# и раннер её только предъявляет (печатает, что именно требуется), а не выполняет.
STAGES = [
    ("0. Вход", "источники, зеркала, карточки, адреса материала, площадка приёмки, дерево чтения", ["§8", "§41", "§61", "§64", "§68", "§71", "§72", "§75", "очередь: источники без карточки", "вход: отбор", "вход: entry", "вход: дерево чтения"]),
    ("1. Карточки", "у каждого источника карточка правильной формы", ["§66", "очередь: карточки без линии"]),
    ("2. Смысловой проход", "наряд читателям, отчёты по контракту, адреса находок", ["§46", "проход: наряд и отчёты"]),
    ("3. Страницы", "решения по кандидатам, страницы в индексе, не сироты, форма имён", ["сомнение: авто решения применены", "§43", "§1", "§2", "§3", "§20", "§58", "§62", "§63", "приёмка: check_new_pages", "замыслы: ответ по каждому"]),
    ("4. Боли и расхождения", "охват болей, сцепка карты, форма ячеек", ["§33", "§45", "§47", "§48", "§25"]),
    ("5. Выжимки", "разбор содержания записи до её удаления", ["§57", "extracts: невклеенные"]),
    ("6. Экзамен", "перегон экзамена: числа и расхождения по сегодняшним страницам", ["экзамен: свежий"]),
    ("7. Закрытие", "карта, мостик, панели, реестр правил, адреса в реестрах, отчёт волны", ["§29", "§30", "§50", "§59", "§73", "§74", "§78", "§94", "замок: пусто", "файл: отчёт волны"]),
]


def read(path):
    with io.open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def lint_totals(root, write_summary=False):
    """Числа по разделам линтера и пара закрытия: ({номер: находок}, итого, код, сводка|None).

    Пара берётся из того же прогона, что и разделы: итог перепечатывать по разделам нельзя
    (§30 и §36 в счёт без git не входят), а код возврата с 2026-09-26 честный (0 — чисто, 1 — находки).
    Сводка пишется только по просьбе (закрытие волны): иначе каждая проверка состояния мусорила бы
    в аудит привязанными файлами. Путь сводки — из строки «машинная сводка», а не пересборкой имени.
    """
    cmd = [sys.executable, "-X", "utf8", toolkit.script("lint_wiki.py"), "--wiki", "."]
    if not write_summary:
        cmd.append("--no-summary")
    try:
        r = subprocess.run(cmd, cwd=root,
                           capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    except OSError as exc:
        raise CheckError("линтер не запустился: %s" % exc) from exc
    if r.returncode not in (0, 1):
        raise CheckError(_failure("линтер не завершился", r))
    out, total, summary = {}, None, None
    for line in (r.stdout or "").splitlines():
        m = re.match(r"^## (\d+)\. .*: (\d+)\s*$", line)
        if m:
            out[m.group(1)] = int(m.group(2))
        m2 = re.match(r"^ИТОГО проблем:\s*(\d+)\s*$", line)
        if m2:
            total = int(m2.group(1))
        m3 = re.match(r"^машинная сводка:\s*(\S+)\s*$", line)
        if m3:
            summary = m3.group(1)
    if not out:
        raise CheckError("линтер завершился, но не вывел разделы")
    if total is None:
        raise CheckError("линтер завершился, но не вывел итог")
    return (out, total, r.returncode, summary)


def queues(root):
    """Очереди работ: то, что этап обязан закрыть до перехода к следующему."""
    sys.path.insert(0, toolkit.TOOLKIT)
    import lint_wiki
    return {
        "источники без карточки": lint_wiki.check_source_cards(root),
        "карточки без линии": lint_wiki.check_card_shape(root),
    }


def extracts_pending(root):
    try:
        r = subprocess.run([sys.executable, "-X", "utf8", toolkit.script("extracts.py"), "check", "--wiki", "."],
                           cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    except OSError as exc:
        raise CheckError("проверка выжимок не запустилась: %s" % exc) from exc
    if r.returncode != 0:
        raise CheckError(_failure("проверка выжимок не выполнена", r))
    text = _result_text(r)
    m = re.search(r"невклеенных выжимок: (\d+)", text)
    if not m:
        raise CheckError("проверка выжимок: в выводе нет числа невклеенных выжимок")
    return int(m.group(1))


def pass_report(root):
    d = toolkit.area(root, "stance")
    files = sorted(f for f in os.listdir(d)) if os.path.isdir(d) else []
    return [f for f in files if re.search(r"semantic\.json$", f)]


def check_stage(index, root, totals, q):
    """Что не выполнено на этапе: список строк."""
    name, _what, reqs = STAGES[index]
    out = []
    notes = []
    if index == 4:
        # Боли, конфликты и инструменты — наследие экземпляра про агентную разработку, а не ядро
        # механизма: постороннему они не нужны, пока он их не объявил. Найдено 2026-09-25 разбором
        # агностичности: стадия требовала их безусловно. Теперь требуют только объявленные реестры,
        # остальное — честное «не применимо» (тот же договор, что у входа из отбора и у §89).
        sys.path.insert(0, toolkit.TOOLKIT)
        import domain as _domain
        have = set(_domain.registers(root))
        need = {"§33": ("cards", "pains"), "§48": ("pains",),
                "§47": ("conflicts",), "§49": ("tools",)}
        kept = []
        for r in reqs:
            if r in need and not (set(need[r]) & have):
                notes.append("%s — не применимо: экземпляр не объявил %s (говорит §89)" % (
                    r, " и ".join("реестр " + k for k in need[r])))
                continue
            kept.append(r)
        reqs = kept
    for req in reqs:
        if req.startswith("§"):
            n = req[1:]
            if n not in totals:
                out.append("линтер %s — раздел отсутствует в выводе" % req)
            elif totals[n] > 0:
                out.append("линтер %s — находок %d" % (req, totals[n]))
        elif req.startswith("очередь: "):
            key = req[len("очередь: "):]
            queue_key = "источники без карточки" if "источник" in key else "карточки без линии"
            if queue_key not in q:
                out.append("%s — проверка не вернула результат" % key)
            else:
                items = q[queue_key]
                if items:
                    out.append("%s — %d шт, первый: %s" % (key, len(items), str(items[0][0])[:60]))
        elif req == "проход: наряд и отчёты":
            r = subprocess.run([sys.executable, "-X", "utf8", toolkit.script("semantic_pass.py"), "--wiki", ".", "check"],
                               cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
            if r.returncode != 0:
                text = _result_text(r)
                tail = [l for l in text.splitlines() if l.strip().startswith("—")]
                out.append("смысловой проход не принят: " + ("; ".join(tail)[:200] if tail else _last_line(text, "проверка не вывела причину")))
        elif req == "extracts: невклеенные":
            try:
                n = extracts_pending(root)
            except CheckError as exc:
                out.append(str(exc))
            else:
                if n:
                    out.append("невклеенных выжимок %d: запись нельзя удалять" % n)
        elif req == "приёмка: check_new_pages":
            r = subprocess.run([sys.executable, "-X", "utf8", toolkit.script("check_new_pages.py"), "--wiki", "."],
                               cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
            if r.returncode != 0:
                tail = [l for l in _result_text(r).splitlines() if l.strip()][-3:]
                out.append("приёмка страниц: " + (" / ".join(tail)[:200] if tail else "проверка не вывела причину"))
        elif req == "вход: отбор":
            # Отбор проверяется только там, где волна идёт из выгрузки владельца: скрипту нужны каталог
            # с port-candidates.json и файл отбора. Нет их — эта волна идёт не из отбора, и это не «сошлось
            # по-тихому»: причина печатается в состоянии этапа.
            # Пара «каталог волны — файл отбора» берётся из наряда: в протоколе шаг 1 называет обе стороны
            # явно. Угадывать её по каталогам нельзя — у проекта несколько выгрузок, и чужая пара даёт
            # ложную тревогу (проверено 2026-09-17: архивный отбор против свежего).
            prot = read(toolkit.script("ingest-wave.md", root))
            pair = re.search(r"check_selection\.py --staging (\S+) --selection (\S+)", prot)
            if not pair:
                notes.append("вход: отбор — наряд не называет пару «каталог — отбор», проверять нечего")
            else:
                staging = pair.group(1).rstrip("`.,)")
                selection = pair.group(2).rstrip("`.,)")
                here = os.path.basename(root)
                staging = staging.replace(".", ".").replace(here, ".")
                r = subprocess.run([sys.executable, "-X", "utf8", toolkit.script("check_selection.py"),
                                    "--staging", staging, "--selection", selection],
                                   cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
                if r.returncode != 0:
                    out.append("отбор не сходится: " + _last_line(_result_text(r), "проверка не вывела причину")[:130])
                else:
                    notes.append("вход: отбор — %s сошёлся с %s" % (staging, selection))
        elif req == "вход: entry":
            # Вход объявляется только там, где есть отбор (телеграм-путь): обычная волна идёт
            # без entry и закрывается с пометкой. Здесь только видимость, не ворота.
            waves = load_waves(root)["waves"]
            cur = next((w for w in waves if w.get("current")), None)
            if cur is None:
                notes.append("вход: entry — открытой волны нет")
            elif not cur.get("entry"):
                notes.append("волна без entry (обычный приём): закроется с пометкой")
            else:
                notes.append("вход объявлен: %s, источников %d" % (
                    cur["entry"].get("kind"), len(cur["entry"].get("sources", []))))
        elif req == "вход: дерево чтения":
            r = subprocess.run([sys.executable, "-X", "utf8", toolkit.script("tree_bundle.py"), "--wiki", "."],
                               cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
            if r.returncode != 0:
                out.append("дерево чтения не собрано: " + _last_line(_result_text(r), "проверка не вывела причину")[:120])
        elif req == "замыслы: ответ по каждому":
            r = subprocess.run([sys.executable, "-X", "utf8", toolkit.script("intents.py"), "--wiki", ".", "check"],
                               cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
            text = _result_text(r)
            m = re.search(r"замыслов без решения: (\d+) из (\d+)", text)
            if r.returncode != 0:
                out.append("замыслы владельца: " + _last_line(text, "проверка не вывела причину")[:120])
            elif not m:
                out.append("замыслы владельца: проверка не вернула число нерешённых замыслов")
            elif int(m.group(1)) > 0:
                out.append("замыслы владельца: " + _last_line(text, "есть нерешённые замыслы")[:120])
        elif req == "сомнение: авто решения применены":
            r = subprocess.run([sys.executable, "-X", "utf8", toolkit.script("page_doubt.py"), "--wiki", "."],
                               cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
            text = _result_text(r)
            if r.returncode != 0:
                out.append("шкала сомнения не применена: " + _last_line(text, "проверка не вывела причину")[:120])
                continue
            tail = [l for l in text.splitlines() if "автосоздание" in l]
            mm = re.search(r"автосоздание \(сомнение < (\d+)\): (\d+)", tail[-1]) if tail else None
            if mm is None:
                out.append("шкала сомнения: проверка не вернула число автосозданий")
                continue
            auto = int(mm.group(2))
            if auto:
                out.append("шкала сомнения не применена: %d понятий с сомнением ниже порога ждут решения — "
                           "прогони `tasks.py page-doubt`" % auto)
        elif req == "экзамен: свежий":
            r = subprocess.run([sys.executable, "-X", "utf8", toolkit.script("exam.py"), "--wiki", ".", "fresh"],
                               cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
            if r.returncode != 0:
                out.append("экзамен за сегодня не предъявлен: " + _last_line(_result_text(r), "нет данных")[:120])
        elif req == "замок: пусто":
            r = subprocess.run([sys.executable, "-X", "utf8", toolkit.script("wave_lock.py"), "--wiki", ".", "check"],
                               cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
            if r.returncode != 0:
                out.append("остались заявки замка «один писатель»: " + _last_line(_result_text(r), "проверка не вывела причину")[:90])
        elif req == "файл: отчёт волны":
            d = toolkit.area(root, "audit")
            today = datetime.date.today().isoformat()
            if not os.path.exists(os.path.join(d, "wave-report-%s.md" % today)):
                out.append("отчёта волны за сегодня нет: закрытие не выполнено (`wave_runner.py close --confirm`)")
    return out, notes


def load_waves(root):
    """Реестр волн: отсутствует — пустой список, а не ошибка (первая волна ещё не открыта)."""
    path = toolkit.area(root, "waves.json")
    if not os.path.exists(path):
        return {"waves": []}
    try:
        data = json.load(open(path, encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CheckError("реестр волн не читается: %s" % exc)
    if not isinstance(data, dict) or not isinstance(data.get("waves"), list):
        raise CheckError("реестр волн должен быть объектом со списком waves")
    return data


def save_waves(root, data):
    path = toolkit.area(root, "waves.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with io.open(path, "w", encoding="utf-8", newline="") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
        f.write("\n")


def sha_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _rel_in_root(root, path):
    """Относительный путь через прямой слэш; вне корня — None (запись волны переезжает с вики)."""
    try:
        rel = os.path.relpath(os.path.abspath(path), root).replace(os.sep, "/")
    except ValueError:
        return None
    if rel.startswith(".."):
        return None
    return rel


def begin(root, selection=None, collected=False, corpus="", inbox="", by="", wave_id="", label=""):
    """Открыть волну с объявленным входом. Необъявленный вход отказывается — таковы ворота.

    Два способа: квитанция отбора (формат: {"sources": [путь|<объект с id>...],
    "excluded": [{"id":..., "verdict":...}]}) либо «собрано вручную» (всё в inbox — моё,
    хеши фиксируются). Решение объявляет владелец, состояние входа считает инструмент.
    """
    if bool(selection) == bool(collected):
        print("необъявленный вход: wave_runner.py begin (--selection ФАЙЛ --inbox ПАПКА --corpus ИМЯ"
              " | --collected --corpus ИМЯ --inbox ПАПКА --by ИМЯ) [--id ID] [--label ТЕКСТ]")
        return 2
    if collected and not by.strip():
        print("collected требует --by: чьё решение стоит за входом")
        return 2
    if selection and by.strip():
        print("--by только для --collected: у selection решение лежит в квитанции")
        return 2
    data = load_waves(root)
    if next((w for w in data["waves"] if w.get("current")), None):
        print("волна уже открыта: сначала закрой текущую (wave_runner.py close --confirm)")
        return 1
    today = datetime.date.today().isoformat()
    ident = wave_id or today
    if not WAVE_ID.match(ident):
        print("недопустимое имя волны: %s" % ident)
        return 2
    if any(w.get("id") == ident for w in data["waves"]):
        print("волна %s уже есть в реестре" % ident)
        return 1
    if not corpus.strip():
        print("нужен --corpus: имя корпуса, куда пойдёт приём")
        return 2
    inbox_abs = os.path.abspath(inbox) if inbox else ""
    if not inbox or not os.path.isdir(inbox_abs):
        print("нет папки входа: %s" % (inbox or "не задана"))
        return 2
    inbox_rel = _rel_in_root(root, inbox_abs)
    if inbox_rel is None:
        print("вход вне корня проекта: запись волны не переедет вместе с вики: %s" % inbox_abs)
        return 2
    # Собираем вход и хешируем: решение владельца уже принято, состояние фиксирует инструмент.
    raw_sources, excluded = [], []
    if selection:
        try:
            with io.open(os.path.abspath(selection), encoding="utf-8") as f:
                sel = json.load(f)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            print("квитанция отбора не читается: %s" % exc)
            return 2
        if not isinstance(sel, dict):
            print("квитанция отбора должна быть объектом со списками sources/excluded")
            return 2
        items = sel.get("sources", [])
        if not isinstance(items, list) or not items:
            print("в квитанции пустой список sources — отбирать нечего")
            return 2
        seen = set()
        for ref in items:
            name = ref.get("id") if isinstance(ref, dict) else ref
            if not isinstance(name, str) or not name.strip() or name in seen:
                print("битая запись sources в квитанции: %r" % (ref,))
                return 2
            seen.add(name)
            path = os.path.join(inbox_abs, name.replace("/", os.sep))
            if not os.path.isfile(path):
                print("источник из квитанции не найден во входе: %s" % name)
                return 1
            rid = _rel_in_root(root, path)
            if rid is None:
                print("источник вне корня проекта: %s" % path)
                return 2
            raw_sources.append({"id": rid, "sha256": sha_file(path)})
        for item in sel.get("excluded", []) or []:
            if not isinstance(item, dict) or not str(item.get("id", "")).strip() \
                    or not str(item.get("verdict", "")).strip():
                print("битая запись excluded в квитанции: нужна пара id + verdict")
                return 2
            eid = str(item["id"]).strip()
            epath = os.path.join(root, *eid.split("/")) if "/" in eid or eid.endswith(".md") else None
            ehash = sha_file(epath) if epath and os.path.isfile(epath) else ""
            excluded.append({"id": eid, "sha256": ehash, "verdict": str(item["verdict"]).strip()})
    else:
        found = []
        for base, dirs, files in os.walk(inbox_abs):
            dirs[:] = sorted(d for d in dirs if not d.startswith("."))
            for name in sorted(files):
                if name.lower().endswith((".md", ".txt")):
                    found.append(os.path.join(base, name))
        if not found:
            print("во входе нет источников (.md/.txt): %s" % inbox_abs)
            return 1
        for path in found:
            rid = _rel_in_root(root, path)
            if rid is None:      # не случится: всё дерево входа внутри корня (проверено выше)
                print("источник вне корня проекта: %s" % path)
                return 2
            raw_sources.append({"id": rid, "sha256": sha_file(path)})
    raw_sources.sort(key=lambda s: s["id"])
    now = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M")
    receipt = {"kind": "selection" if selection else "collected",
               "at": now, "inbox": inbox_rel, "corpus": corpus.strip(),
               "sources": raw_sources, "excluded": excluded}
    if not selection:
        receipt["by"] = by.strip()   # у selection решения нет: оно лежит в квитанции
    receipt_path = toolkit.area(root, "selection", ident + ".json")
    os.makedirs(os.path.dirname(receipt_path), exist_ok=True)
    with io.open(receipt_path, "w", encoding="utf-8", newline="") as f:
        json.dump(receipt, f, ensure_ascii=False, indent=1)
        f.write("\n")
    entry = dict(receipt, receipt=os.path.join(toolkit.AREA, "selection", ident + ".json").replace(os.sep, "/"))
    data["waves"].append({"id": ident, "label": label or ("волна " + ident),
                          "from": today + " 00:00", "to": None, "current": True,
                          "entry": entry})
    save_waves(root, data)
    print("волна открыта: %s | вход %s, источников %d, отброшено %d | квитанция %s" % (
        ident, entry["kind"], len(raw_sources), len(excluded), entry["receipt"]))
    return 0


def check_receipt_open(root, receipt_abs):
    """Квитанция принята к приёму, только если её выдал begin открытой волны.

    Иначе квитанцию можно написать руками в обход begin — и весь entry-механизм декорация.
    Возвращает строку отказа или пусто.
    """
    data = load_waves(root)
    cur = next((w for w in data["waves"] if w.get("current")), None)
    if cur is None or not cur.get("entry"):
        return "нет открытой волны с entry: сначала wave_runner.py begin"
    want = os.path.abspath(os.path.join(root, str(cur["entry"].get("receipt") or "\0")))
    if os.path.abspath(receipt_abs) != want:
        return "квитанция не из открытой волны: ждали %s" % want
    return ""


def verify_entry(root):
    """Сверка текущего состояния с entry перед закрытием: находит подмену и рост, а не переписывает.

    Возвращает (проблемы, пометки). Волна без entry закрывается с пометкой всегда: ворота entry
    живут только там, где вход объявлялся (телеграм-путь), обычный приём идёт без объявлений
    (решение владельца 2026-09-26: всеобщая церемония — бюрократизация без заказчика).
    """
    data = load_waves(root)
    waves = data["waves"]
    cur = next((w for w in waves if w.get("current")), None)
    if cur is None:
        return (["нет открытой волны: сначала wave_runner.py begin"], [])
    entry = cur.get("entry") or {}
    if not entry:
        return ([], ["волна без entry (обычный приём): закрывается с пометкой"])
    problems = []
    known = {str(s.get("id")) for s in entry.get("sources", []) if s.get("id")}
    for s in entry.get("sources", []):
        path = os.path.join(root, *str(s.get("id", "")).split("/"))
        if not os.path.isfile(path):
            # Отсутствует — твёрдый отказ, а не пометка: иначе удаление файла после begin
            # слепит обе стороны проверки, и решение владельца размывается одним rm.
            # Инбокс живёт до закрытия волны; чистка — после, вместе с архивацией.
            problems.append("источник entry отсутствует: %s" % s.get("id"))
            continue
        if sha_file(path) != s.get("sha256"):
            problems.append("источник entry изменён после begin: %s" % s.get("id"))
    # Обратный ход: файлы во входе, которых нет в entry, — молчаливый рост волны мимо квитанции.
    # Без него решение владельца размывается добавлением, а закрытие это подписывает.
    inbox_abs = os.path.join(root, *str(entry.get("inbox", "")).split("/")) if entry.get("inbox") else ""
    if inbox_abs and os.path.isdir(inbox_abs):
        for base, dirs, files in os.walk(inbox_abs):
            dirs[:] = sorted(d for d in dirs if not d.startswith("."))
            for name in sorted(files):
                if not name.lower().endswith((".md", ".txt")):
                    continue
                rid = os.path.relpath(os.path.join(base, name), root).replace("\\", "/")
                if rid not in known:
                    problems.append("во входе лишнее сверх entry: %s" % rid)
    # Отброшенное сверяется, только если файл на месте: id сообщений и записей без файла
    # проверить нечем, а писать хеш без проверки — видимость следопысла (разбор 2026-09-26).
    for e in entry.get("excluded", []) or []:
        ep = os.path.join(root, *str(e.get("id", "")).split("/"))
        if e.get("sha256") and os.path.isfile(ep) and sha_file(ep) != e.get("sha256"):
            problems.append("отброшенный источник entry изменён: %s" % e.get("id"))
    return (problems, [])


def status(root, only=None, write_summary=False):
    """Строки этапов и пара линтера: ([(этап, название, что, незакрытое, пометки)], (итого, код, сводка|None))."""
    totals, lint_total, lint_exit, lint_summary = lint_totals(root, write_summary=write_summary)
    q = queues(root)
    res = []
    for i, (name, what, _reqs) in enumerate(STAGES):
        if only is not None and i != only:
            continue
        miss, notes = check_stage(i, root, totals, q)
        res.append((i, name, what, miss, notes))
    return (res, (lint_total, lint_exit, lint_summary))


def close(root, label, confirm):
    """Закрытие волны: сверка входа, снимок записи, wave-close, реестр правил, отчёт, запись состояния."""
    if not confirm:
        print("закрытие меняет состояние волны (`waves.json`) — повтори с `--confirm`")
        return 2
    # Bootstrap первой волны: реестра может не быть вовсе — заводим пустой, дальше всё штатно.
    waves_path = toolkit.area(root, "waves.json")
    if not os.path.exists(waves_path):
        save_waves(root, {"waves": []})
        print("реестр волн заведён: это первая волна")
    # Обычный приём без машины entry: если ни одна волна entry не объявляла, волна открывается
    # здесь же, как раньше, с пометкой. Гейт entry живёт только там, где вход объявлялся, —
    # иначе обычный приём был бы обязан церемонии телеграм-пути (разбор 2026-09-26).
    data = load_waves(root)
    if not next((w for w in data["waves"] if w.get("current")), None) \
            and not any(w.get("entry") for w in data["waves"]):
        today = datetime.date.today().isoformat()
        if any(w.get("id") == today for w in data["waves"]):
            print("волна %s уже закрыта сегодня: следующую открой через begin" % today)
            return 1
        data["waves"].append({"id": today, "label": "волна " + today,
                              "from": today + " 00:00", "to": None, "current": True})
        save_waves(root, data)
        print("волна открыта без entry (обычный приём): %s" % today)
    # Вход сверяется, а не переписывается: подмена источника после begin и рост волны мимо
    # квитанции роняют закрытие с именем файла. Снимок ниже только строит запись, не чинит вход.
    problems, notes = verify_entry(root)
    for line in notes:
        print(line)
    if problems:
        print("вход не сошёлся с entry:")
        for line in problems[:12]:
            print("  -", line)
        return 1
    print("вход сошёлся с entry")
    # Запись волны собирается механизмом из состояния репозитория ДО приёмки: иначе §94
    # («страница принадлежит волне») видит страницы без записи и закрытие не проходит никогда.
    r0 = subprocess.run([sys.executable, "-X", "utf8", toolkit.script("ingest_wave_map.py"),
                         "--wiki", ".", "--snapshot"],
                        cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    print("снимок записи:", "ок" if r0.returncode == 0 else "СБОЙ")
    if r0.returncode != 0:
        print(_failure("сбой снимка записи волны", r0)[-400:])
        return 1
    r = subprocess.run([sys.executable, "-X", "utf8", toolkit.script("tasks.py"), "wave-close"],
                        cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    print("wave-close:", "ок" if r.returncode == 0 else "СБОЙ")
    if r.returncode != 0:
        print(_failure("сбой wave-close", r)[-600:])
        return 1
    r2 = subprocess.run([sys.executable, "-X", "utf8", toolkit.script("rules_check.py"), "--wiki", "."],
                        cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    print("реестр правил:", "сходится" if r2.returncode == 0 else "НЕ СХОДИТСЯ")
    if r2.returncode != 0:
        print(_failure("сбой проверки реестра правил", r2)[-400:])
        return 1
    today = datetime.date.today().isoformat()
    data = load_waves(root)
    cur = next((w for w in data["waves"] if w.get("current")), None)
    if not cur:
        print("закрывать нечего: открытой волны нет (wave_runner.py begin)")
        return 1
    cur["to"] = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M")
    cur.pop("current", None)
    save_waves(root, data)
    print("волна закрыта:", cur.get("id"))
    # Панели владельца считают вики, и запись состояния волны их делает устаревшими (§50): пересобираем
    # панели после `waves.json`, а отчёт пишем последним — тогда его состояния уже итоговые.
    r = subprocess.run([sys.executable, "-X", "utf8", toolkit.script("panel_freshness.py"),
                        "--wiki", ".", "--rebuild"], cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    print("панели:", "собраны" if r.returncode == 0 else "НЕ СОБРАНЫ")
    if r.returncode != 0:
        print(_failure("сбой пересборки панелей", r))
        return 1
    rep = toolkit.area(root, "audit", "wave-report-%s.md" % today)
    lines = ["# Отчёт волны %s" % (label or today), "",
             "Собрано раннером `_toolkit/wave_runner.py close`.", "",
             "| этап | что требовалось | состояние |", "| --- | --- | --- |"]
    try:
        rows, (lint_total, lint_exit, lint_summary) = status(root, write_summary=True)
    except CheckError as exc:
        print("проверка волны не выполнена: %s" % exc)
        return 1
    # Привязка к закладке (решение владельца 2026-09-26): отчёт закрытия ссылается на сводку,
    # а сводка несёт закладку и чистоту дерева. Без второй строки привязка врёт: сводка грязного
    # дерева описывает рабочее состояние поверх закладки, а не саму закладку.
    sum_rel, sum_rev, sum_dirty = "", "—", None
    if lint_summary:
        sp = lint_summary if os.path.isabs(lint_summary) else os.path.join(root, lint_summary)
        try:
            sj = json.load(open(sp, encoding="utf-8"))
            sum_rel = os.path.relpath(sp, root)
            sum_rev = str(sj.get("rev") or "—")
            sum_dirty = sj.get("tree_dirty")
        except (OSError, UnicodeError, ValueError):
            sum_rel = lint_summary
    for i, name, what, miss, _notes in rows:
        lines.append("| %s | %s | %s |" % (name, what, "готово" if not miss else "; ".join(miss)[:160]))
    lines += ["",
              "Линтер на закрытии: ИТОГО проблем: %d, код возврата %d." % (lint_total, lint_exit),
              "Сводка: %s; закладка %s; дерево %s в момент проверки."
              % (sum_rel or "не записана", sum_rev,
                 "грязное" if sum_dirty else ("чистое" if sum_dirty is False else "неизвестно"))]
    print("линтер на закрытии: ИТОГО проблем: %d, код %d" % (lint_total, lint_exit))
    with io.open(rep, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("отчёт волны:", os.path.relpath(rep, root))
    return 0


def main():
    ap = argparse.ArgumentParser(description="Прогон волны инжеста от сырья до страниц")
    ap.add_argument("command", choices=["begin", "status", "step", "close"])
    ap.add_argument("n", nargs="?", type=int)
    ap.add_argument("--wiki", default=".")
    ap.add_argument("--label", default="")
    ap.add_argument("--confirm", action="store_true")
    ap.add_argument("--selection", default="")
    ap.add_argument("--collected", action="store_true")
    ap.add_argument("--corpus", default="")
    ap.add_argument("--inbox", default="")
    ap.add_argument("--by", default="")
    ap.add_argument("--id", dest="wave_id", default="")
    a = ap.parse_args()
    root = os.path.abspath(a.wiki)
    if a.command == "begin":
        return begin(root, selection=a.selection or None, collected=a.collected,
                     corpus=a.corpus, inbox=a.inbox, by=a.by, wave_id=a.wave_id, label=a.label)
    if a.command == "close":
        return close(root, a.label, a.confirm)
    try:
        if a.command == "step":
            if a.n is None or not (0 <= a.n < len(STAGES)):
                print("шаг от 0 до %d" % (len(STAGES) - 1))
                return 2
            res = status(root, only=a.n)
        else:
            res = status(root)
    except CheckError as exc:
        print("проверка волны не выполнена: %s" % exc)
        return 1
    res, (_lint_total, _lint_exit, _lint_summary) = res
    bad = 0
    print("| этап | что требуется | состояние |")
    print("| --- | --- | --- |")
    for i, name, what, miss, notes in res:
        state = "готово" if not miss else "; ".join(miss)[:170]
        bad += 1 if miss else 0
        print("| %s | %s | %s |" % (name, what, state))
        for note in notes:
            print("|   | примечание | %s |" % note[:170])
    if bad:
        print("\nволна не пройдена: этапов с незакрытыми требованиями %d. Текст карточек и страниц пишет модель; "
              "раннер требует предъявить артефакт и не даёт шагу пройти молча." % bad)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
