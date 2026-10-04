#!/usr/bin/env python3
"""Сборка одного файла для передачи: весь пакет, кроме объёмного приложения.

Собирает в один markdown-документ: обзор вики, состояние, ответ аудитору, машинное
приложение, машинный вывод, аттестацию чекеров с историей, линтер по разделам,
оценку слепого прогона, эталонные вопросы и сводки реестров.

Объёмное приложение (wiki-appendix) намеренно НЕ включается: это полные тексты страниц,
граф на все рёбра и таблица связей — их передают отдельно по запросу.

Запуск: python3 _toolkit/merge_package.py --wiki .
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import sys

import toolkit

TODAY = datetime.date.today().isoformat()
TITLES = [
    ("Манифест пакета", "wiki-package-{d}.md"),
    ("Обзор вики для внешнего аудита", "wiki-overview-{d}.md"),
    ("Состояние по итогам разборов", "wiki-state-{d}.md"),
    ("Письмо цикла: отчёт или ответ на последний разбор", "auditor-report-{d}.md"),
    ("Машинное приложение: реестры, линтер, канарейки, git", "wiki-machine-appendix-{d}.md"),
    ("Машинный вывод: все счётчики и суммы", "wiki-figures-{d}.md"),
    ("Аттестация чекеров: матрица и история", "canary-report-{d}.md"),
    ("Оценка слепого прогона эталонных вопросов", "blind-run-score-{d}.md"),
    ("Эталонные вопросы", "eval-questions-{d}.md"),
]


def read(p):
    return open(p, encoding="utf-8").read()


def strip_first_heading(text, title):
    """Убирает первый заголовок H1 из вставляемого файла: в сборке заголовок даёт раздел."""
    lines = text.split("\n")
    for i, l in enumerate(lines):
        if l.startswith("# "):
            return "\n".join(lines[:i] + lines[i + 1:]).lstrip("\n")
    return text


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wiki", required=True)
    a = ap.parse_args()
    root = os.path.abspath(a.wiki)
    audit = toolkit.area(root, "audit")

    # снапшот и сводки реестров
    figjson = json.load(open(os.path.join(audit, f"wiki-figures-{TODAY}.json"), encoding="utf-8"))
    snapshot = figjson.get("snapshot_line", "")
    lint_path = toolkit.latest_report(audit, "lint-summary-")
    if not lint_path:
        raise SystemExit("сборка без сводки линтера: сначала прогон lint (сводки привязаны к закладке)")
    print("сводка линтера взята из", os.path.basename(lint_path))
    lint = json.load(open(lint_path, encoding="utf-8"))
    # Аттестация канареек не пересоздаётся автоматически (это отдельная церемония), поэтому
    # если прогона за сегодня нет — берём последний и честно называем его дату в сборке.
    can_path = os.path.join(audit, f"canary-results-{TODAY}.json")
    can_note = ""
    if not os.path.exists(can_path):
        prev = sorted(f for f in os.listdir(audit)
                      if f.startswith("canary-results-") and f.endswith(".json"))
        if prev:
            can_path = os.path.join(audit, prev[-1])
            can_note = (f"Аттестация в этой сборке — от {prev[-1][len('canary-results-'):-5]}: "
                        f"прогона канареек за {TODAY} не было.")
            print("аттестация взята из", prev[-1])
    can = json.load(open(can_path, encoding="utf-8"))
    cards = json.load(open(toolkit.area(root, "cards-registry.json"), encoding="utf-8"))
    claims = toolkit.area(root, "claims-registry.tsv")
    claims_n = sum(1 for _ in open(claims, encoding="utf-8")) - 1 if os.path.exists(claims) else 0

    # индекс пакета: размеры и хеши считает один генератор — и манифест, и содержание сборки
    PURPOSE = [
        ("audit-package-", "ОДИН ФАЙЛ ДЛЯ ПЕРЕДАЧИ: вся сборка, кроме объёмного приложения"),
        ("wiki-overview-", "обзор: метрики, карта тем, типология, фрагменты страниц"),
        ("wiki-appendix-", "приложение: полные страницы, граф Mermaid, таблица связей (объёмное)"),
        ("wiki-state-", "состояние по итогам всех разборов: что закрыто, что открыто"),
        ("wiki-machine-appendix-", "машинное приложение: реестры, линтер по разделам, канарейки, git"),
        ("wiki-figures-", "машинный вывод: все счётчики и суммы"),
        ("wiki-graph-", "список рёбер «страница → источник»"),
        ("canary-report-", "аттестация чекеров: матрица «канарейка → раздел» и история"),
        ("canary-results-", "результаты канареек (машинные)"),
        ("lint-summary-", "линтер по разделам (машинный)"),
        ("number-check-", "автопроверка чисел: подтверждено и спорно"),
        ("blind-run-score-", "оценка слепого прогона эталонных вопросов"),
        ("blind-run-a-", "сырые ответы первого исполнителя (доказательство)"),
        ("blind-run-b-", "сырые ответы второго исполнителя (доказательство)"),
        ("blind-rerun-", "повторный прогон двух вопросов (парная точка)"),
        ("blind-questions-", "вопросы без подсказок: вход слепого прогона"),
        ("eval-questions-", "эталонные вопросы с ожидаемыми страницами"),
        ("exam-history", "траектория экзамена: все прогоны"),
        ("exam-2026", "вердикт экзамена: попадание и сигнал регрессии"),
        ("exam-", "вопросы экзамена без подсказок"),
        ("auditor-response-5-", "архивный ответ (пятый разбор; с 2026-09-14 ответ живёт в письме цикла)"),
        ("auditor-response-", "ответ аудитору (в сборке — последний)"),
        ("package-index-", "машинный индекс пакета: размеры и хеши"),
        ("README.md", "индекс ролей файлов каталога"),
    ]

    def purpose_of(fn):
        for pref, why in PURPOSE:
            if fn.startswith(pref):
                return why
        return "служебный файл"

    index = {}
    for fn in sorted(os.listdir(audit)):
        if not (fn.endswith((".md", ".json", ".tsv"))):
            continue
        # сборка, индекс и сам манифест себя не перечисляют: их размер и хеш менялись бы от собственной
        # записи, и хеш манифеста «плыл» бы на каждом прогоне — пара «письмо ↔ посылка» перестала бы
        # проверяться. Манифест перечисляет груз, а не себя.
        if fn.startswith(("audit-package-", "package-index-", "wiki-package-")):
            continue
        # Реестр отправленного — часть процедуры доставки, а не груз: запись отправки меняет его,
        # и если он входит в перечень, хеш манифеста меняется от самой отправки (письмо подписывается
        # хешем, который тут же становится неверным). Служебная часть доставки: письмо-обложка,
        # реестр отправленного, манифест и сборка.
        if fn == "sent-artifacts.tsv":
            continue
        # Письмо-обложка тоже не входит: оно печатает хеш манифеста, а манифест перечисляет груз.
        # Если письмо попадёт в перечень, хеш манифеста начнёт зависеть от хеша письма, который
        # зависит от хеша манифеста — петля, которую нельзя удовлетворить. Целостность письма
        # фиксирует реестр отправленного (`sent-artifacts.tsv`).
        if re.match(r"auditor-(report|response(?:-\d+[a-z]?)?)-" + re.escape(TODAY) + r"\.md$", fn):
            continue
        pth = os.path.join(audit, fn)
        if os.path.getsize(pth) == 0:
            continue
        digest = hashlib.sha256(open(pth, "rb").read()).hexdigest()
        index[fn] = {"kb": round(os.path.getsize(pth) / 1024, 1),
                     "sha256_12": digest[:12],
                     # Полный хеш печатается рядом с коротким: короткий — удобный идентификатор, но проверить
                     # по нему содержимое нельзя. Разбор 11 просил это шесть циклов: «хеш без содержимого —
                     # обещание, не доказательство».
                     "sha256": digest,
                     "purpose": purpose_of(fn)}
    json.dump({"date": TODAY, "snapshot_line": snapshot, "files": index},
              open(os.path.join(audit, f"package-index-{TODAY}.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)

    # манифест пакета — из того же индекса
    # TL;DR Delta: аудитор читает это первым. Числа берутся из артефактов, не набираются руками.
    def _figs_by_date():
        out = {}
        for fn in sorted(os.listdir(audit)):
            if fn.startswith("wiki-figures-") and fn.endswith(".json"):
                try:
                    out[fn.split("wiki-figures-")[1][:-5]] = json.load(
                        open(os.path.join(audit, fn), encoding="utf-8")).get("figures", {})
                except (OSError, UnicodeError, json.JSONDecodeError, AttributeError) as ex:
                    print(f"предупреждение: снимок чисел {fn} пропущен ({ex})", file=sys.stderr)
        return out

    # Базис дельты — ПРЕДЫДУЩАЯ ОТПРАВКА, а не вчерашний файл по календарю. Точка сравнения обязана быть
    # файлом и хешем (правило шести циклов), иначе дифф пересчитывает чужое изменение как движение цикла.
    # Дефект, который это ловит (разбор 11): таблица письма считала «29 → 39» от 09-13, тогда как предыдущее
    # отправленное состояние — 38 разделов и 380 рёбер, и проверить базис было нечем, потому что он не назван.
    METRIC_LABELS = [("страницы", "pages"), ("источников знаний", "sources"), ("рёбер графа", "edges"),
                     ("карточек извлечения", "cards"), ("эталонных вопросов", "questions"),
                     ("разделов линтера", "lint_sections")]
    _f = _figs_by_date()
    _dates = sorted(_f)
    _cur = _f.get(_dates[-1], {}) if _dates else {}
    _base_file, _base_sha, _base_metrics, _base_date = "", "", {}, ""
    led_path = _domain.register_path(root, "handover")      # реестр передач объявляет экземпляр
    if led_path and os.path.exists(led_path):
        rows_led = [r.split("\t") for r in read(led_path).splitlines()[1:] if r.strip()]
        sent = [r for r in rows_led if len(r) >= 11 and r[10] == "sent"]
        if sent:
            last = sent[-1]
            _base_file, _base_sha, _base_date = last[1], last[2], last[0]
            _base_metrics = {"pages": last[4], "sources": last[5], "edges": last[6],
                             "cards": last[7], "questions": last[8], "lint_sections": last[3]}
    _delta = []
    for label, key in METRIC_LABELS:
        cur_v = _cur.get(label)
        had = _base_metrics.get(key)
        if cur_v is not None and (had or "").strip().isdigit():
            _delta.append((label, int(had), cur_v))
    delta_doc = {"date": TODAY, "baseline": {"file": _base_file, "sha256": _base_sha, "date": _base_date},
                 "current_figures": f"wiki-figures-{TODAY}.json",
                 "rows": [[lbl, a, b] for lbl, a, b in _delta]}
    try:
        json.dump(delta_doc, open(os.path.join(audit, f"delta-{TODAY}.json"), "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
    except (OSError, TypeError, ValueError) as ex:
        print(f"предупреждение: delta-{TODAY}.json не записан ({ex})", file=sys.stderr)
    _can = {}
    for fn in sorted(os.listdir(audit)):
        if fn.startswith("canary-results-") and fn.endswith(".json"):
            try:
                _can = json.load(open(os.path.join(audit, fn), encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as ex:
                print(f"предупреждение: результаты канареек {fn} пропущены ({ex})", file=sys.stderr)
                _can = {}
    _exam = ""
    hist = os.path.join(audit, "exam-history.tsv")
    if os.path.exists(hist):
        rows = [r for r in open(hist, encoding="utf-8").read().strip().split("\n")[1:] if r.strip()]
        if rows:
            last = rows[-1].split("\t")
            _exam = (f"прогон {last[0]}: мягко {last[1]}, строго по основным {last[2]}, "
                     f"по полному набору {last[3]}")
    tldr = ["", "## TL;DR Delta — читать первым", ""]
    if _delta:
        tldr.append(f"- **Что изменилось** (базис: предыдущая отправка `{'handover/' + _base_file if _base_file else 'нет данных'}`"
                    f"{', sha256 `' + _base_sha[:12] + '`' if _base_sha else ''}): " +
                    "; ".join(f"{k}: {a} → {b}" for k, a, b in _delta) + ".")
    if _can:
        _sens, _spec = _can.get("sensitivity", {}), _can.get("specificity", {})
        tldr.append(f"- **Проверки**: канареек {len(_can.get('canaries', []))}, "
                    f"чувствительность {_sens.get('caught', '—')}/{_sens.get('total', '—')}, "
                    f"специфичность {_spec.get('passed', '—')}/{_spec.get('total', '—')}.")
    if _exam:
        tldr.append(f"- **Экзамен**: {_exam}.")
    tldr += ["- **Главный вывод цикла**: наличие ответа в базе не гарантирует его находимость — "
             "страницам, вплетающим новый корпус, нужны явные связи со страницей-голосом и страницей-механизмом.",
             "- **Порядок чтения**: снапшот → это TL;DR → дифф-таблица в письме → таблица дефектов.", ""]
    man = [f"# Пакет отчётности ({TODAY})", "", snapshot, "\n".join(tldr),
           f"Файлов в пакете: {len(index)}. Размеры и хеши — из `package-index-{TODAY}.json`.",
           "",
           f"Вне перечня остаётся служебная часть доставки: письмо-обложка цикла (оно печатает хеш этого "
           f"манифеста), реестр отправленного, сам манифест и машинный индекс — иначе хеш манифеста зависел бы "
           f"от письма и от собственной записи. "
           f"Целостность письма зафиксирована в `sent-artifacts.tsv`.", "",
           "| файл | назначение | КБ | sha256 |", "|---|---|---|---|"]
    for fn, meta in index.items():
        man.append(f"| `{fn}` | {meta['purpose']} | {meta['kb']} | `{meta.get('sha256', meta['sha256_12'])}` |")
    man += ["", "Все числа пакета взяты из одного снапшота; расхождения ловит раздел 14 линтера, ручные числа в прозе — раздел 21.",
            "", "Ответы аудитору (`auditor-response-*.md`) до пятого — исторические документы переписки: числа в них относятся к состоянию на момент ответа."]
    open(os.path.join(audit, f"wiki-package-{TODAY}.md"), "w", encoding="utf-8").write("\n".join(man) + "\n")
    print("манифест пакета:", f"wiki-package-{TODAY}.md", f"({len(index)} файлов)")

    parts, toc = [], []
    body = []
    body.append("# Пакет аудита вики — один файл")
    body.append("")
    body.append(snapshot)
    if can_note:
        body.append("")
        body.append(can_note)
    body.append("")
    body.append(f"Собрано {TODAY} из файлов каталога `_staging/audit`. Объёмное приложение "
                f"(`wiki-appendix-{TODAY}.md`: полные тексты типовых страниц, граф Mermaid на все рёбра, таблица связей) "
                "в сборку не входит — передаётся отдельно. Машинные файлы (`wiki-figures-{d}.json`, `wiki-graph-{d}.tsv`, "
                "`lint-summary-<дата>-<время>-<закладка>.json` (сводка привязана к закладке, взята последняя), "
                "`canary-results-{d}.json`, `cards-registry.json`, `claims-registry.tsv`) "
                "приложены к проекту; их содержимое сводится ниже.".replace("{d}", TODAY))
    body.append("")
    body.append("## Содержание")
    body.append("")

    sent_map = {}
    sent_reg = os.path.join(audit, "sent-artifacts.tsv")
    if os.path.exists(sent_reg):
        for row in read(sent_reg).splitlines()[1:]:
            cols = row.split("\t")
            if len(cols) >= 3:
                sent_map[os.path.basename(cols[1])] = (cols[0], cols[2])
    # Текущее письмо даты — то, которое уходит: не отправленное (отправленное заморожено и живёт
    # отдельным разделом) и, если писем несколько, самое свежее по имени. Без этого новая посылка
    # несла бы в обложке прошлое письмо: оно отвечает не на тот разбор.
    def current_letter():
        cand = [f for f in os.listdir(audit)
                if re.match(r"auditor-(report|response(-\d+[a-z]?)?)-" + re.escape(TODAY) + r"\.md$", f)]
        # Свежесть важнее признака отправки: отфильтровав отправленное, мы вернули бы в обложку прошлое письмо
        # (ответ-9 вместо ответа-10) — молча и правдоподобно. Отправленное просто уходит в замороженные.
        # Свежесть по времени правки, а не по имени: «…-10-…» сортируется раньше «…-9-…» как строка.
        return max(cand, key=lambda f: os.path.getmtime(os.path.join(audit, f))) if cand else ""

    LETTER = current_letter()
    if LETTER:
        print(f"текущее письмо даты: {LETTER}")
    # Отправленное даты перечисляется ВСЕГДА, а не только когда оно попалось среди компонентов: иначе
    # посылка с новым письмом в обложке не упоминала бы прошлое письмо, и §32 справедливо считал бы это
    # потерей — «пакет не ссылается на письмо цикла».
    frozen = []
    for fn_, (date_, h_) in sorted(sent_map.items()):
        if os.path.exists(os.path.join(audit, fn_)) and date_ == TODAY:
            frozen.append((fn_, date_, h_))
    for title, pattern in TITLES:
        fn = pattern.replace("{d}", TODAY)
        if title.startswith("Письмо цикла") and LETTER:
            fn = LETTER
        p = os.path.join(audit, fn)
        if not os.path.exists(p):
            # запасной путь: файл с префиксом не пересоздаётся каждый день (вопросы, история прогонов) —
            # берём самый свежий из имеющихся и помечаем это в сборке, чтобы дата не вводила в заблуждение
            probe, tail = (pattern.split("{d}") + [""])[:2]
            cand = sorted(f for f in os.listdir(audit)
                          if f.startswith(probe) and f.endswith(tail or (".md", ".tsv", ".json"))
                          and f != probe + tail)
            if cand:
                fn = cand[-1]
                p = os.path.join(audit, fn)
                print(f"замена на актуальный файл: {pattern.replace('{d}', TODAY)} → {fn}")
                # ВАЖНО: подставленный файл должен попасть в сборку. Раньше здесь стоял безусловный
                # `continue`, и компонент печатался в оглавлении, но не вставлялся: пакет молча терял
                # ответ аудитору, слепой прогон и наряд вопросов (найдено 2026-09-14).
            else:
                print("нет файла:", fn)
                continue
        # Отправленный документ в сборку не встраивается: его числа заморожены на момент отправки, и в новой
        # сборке выглядели бы как свежие (§21). Ссылаемся именем и хешем, а не содержимым.
        if fn in sent_map and hashlib.sha256(open(p, "rb").read()).hexdigest() == sent_map[fn][1]:
            if not any(f[0] == fn for f in frozen):
                frozen.append((fn, sent_map[fn][0], sent_map[fn][1]))
            continue
        text = strip_first_heading(read(p), title)
        anchor = re.sub(r"[^a-zа-я0-9 -]", "", title.lower()).replace(" ", "-")
        toc.append(f"- {title} — из `{fn}`, {index.get(fn, {}).get('kb', '—')} КБ")
        parts.append((title, text))

    # линтер по разделам: таблица + сырой json, как просил аудитор
    lint_md = ["## Линтер по разделам (полный результат)", "",
               f"Разделов: {lint.get('sections')}; итого проблем: {lint.get('total')}.", "",
               "| раздел | проблем |", "|---|---|"]
    for t, n in lint.get("by_section", {}).items():
        lint_md.append(f"| {t} | {n} |")
    lint_md += ["", "Сырой JSON из `lint-summary-" + TODAY + ".json`:", "", "```json",
                json.dumps(lint, ensure_ascii=False, indent=1), "```"]
    parts.append(("Линтер по разделам (полный результат)", "\n".join(lint_md)))
    toc.append("- Линтер по разделам — таблица и сырой JSON")

    # реестры: сводки
    reg_md = ["## Реестры (сводки)", "",
              f"Карточек: {cards['summary']['cards_total']} = "
              f"{cards['summary']['by_kind'].get('manifest', 0)} манифестов + "
              f"{cards['summary']['by_kind'].get('article', 0)} статей + "
              f"{cards['summary']['by_kind'].get('note', 0)} заметок владельца + "
              f"{cards['summary']['by_kind'].get('template', 0)} служебная карточка шаблона; "
              f"покрытие {cards['summary']['covered_manifests'] + cards['summary']['covered_articles']} "
              f"из {cards['summary']['cardinality']} допустимых к карточке источников.", "",
              f"`lineage_tools` заполнен у {cards['summary']['lineage_filled']}, полемика зафиксирована у {cards['summary']['debates']} из них.", "",
              f"Утверждений с владельцем и типом подтверждения: {claims_n} (`_staging/claims-registry.tsv`).", "",
              f"Карточек в машинной аттестации: чувствительность {can.get('sensitivity', {}).get('caught')}/"
              f"{can.get('sensitivity', {}).get('total')}, специфичность {can.get('specificity', {}).get('passed')}/"
              f"{can.get('specificity', {}).get('total')}.", ""]
    parts.append(("Реестры (сводки)", "\n".join(reg_md)))
    toc.append("- Реестры (сводки) — карточки, утверждения, lineage")

    for fn_, date_, h_ in frozen:
        toc.append(f"- Отправленный документ — отправлено {date_}, не встраивается: числа заморожены "
                   f"(`{fn_}`, хеш `{h_[:12]}`)")

    if frozen:
        fr = ["## Отправленные документы (не встраиваются в сборку)", "",
              "Числа этих документов заморожены на момент отправки и сверяются со слепком (линтер §27). "
              "В сборку они не встраиваются: в новом прогоне их числа читались бы как свежие (§21).", "",
              "| документ | отправлено | sha256 |", "|---|---|---|"]
        for fn_, date_, h_ in frozen:
            fr.append(f"| `{fn_}` | {date_} | `{h_[:16]}` |")
        parts.append(("Отправленные документы (не встраиваются в сборку)", "\n".join(fr)))
    body += toc
    body.append("")
    for title, text in parts:
        body += [f"# {title}", "", text.rstrip(), "", "---", ""]

    out = "\n".join(body).rstrip() + "\n"
    # безопасные для любых просмотрщиков символы: только кириллица, латиница и обычная пунктуация
    for bad, good in (("≠", "не равно "), ("≥", "не меньше "), ("≤", "не больше "),
                      ("⚑", "[!]"), ("→", "->"), ("←", "<-"), ("•", "*")):
        out = out.replace(bad, good)
    target = os.path.join(audit, f"audit-package-{TODAY}.md")
    # CRLF и BOM: Windows-редакторы иначе читают файл как ANSI или показывают его одной строкой
    out = out.replace("\r\n", "\n").replace("\n", "\r\n")
    open(target, "w", encoding="utf-8-sig", newline="").write(out)
    print("сборка:", target)
    print(f"размер: {round(len(out.encode())/1024, 1)} КБ, строк {len(out.split(chr(10)))}, разделов {len(parts)}")



import os as _os
import sys as _sys

_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import domain as _domain  # домен экземпляра: имена его страниц и реестров знает только он

if __name__ == "__main__":
    main()
