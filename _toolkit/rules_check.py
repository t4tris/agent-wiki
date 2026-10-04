#!/usr/bin/env python3
"""Реестр правил против файлов: правило существует, только если сходится эта проверка.

Зачем. Владелец 2026-09-17: «тебе кажется что механизм уже вписан в артефакты и репозиторий, потому что он
в твоем контексте, а на самом деле нет. может ты сделать какой-то хук для самопроверки который срабатывает
после таких вот изменений?» Реестр `_toolkit/rules.tsv` перечисляет правила проекта строками; каждая строка
обязана сходиться с файлами:

* маркер встречается в файле, где правило живёт (без маркера правило исчезло, даже если о нём помнят);
* сторож (если назван) существует разделом линтера;
* канарейка (если названа) существует в наборе `canary_test.py`;
* файл правила под git (`git ls-files`), иначе оно не в репозитории, а в рабочем дереве.

Запускается тремя путями: вручную, из линтера (§59) и хуком агентского каркаса после правок файлов и перед `git commit`.

    python3 _toolkit/rules_check.py --wiki .
    python3 _toolkit/rules_check.py --wiki . --brief
"""
import argparse
import io
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import supply
import toolkit

RULES = "rules.tsv"
# документы, которые читает сессия: артефакт, не названный ни в одном из них, недостижим
PROTOCOLS = ("_toolkit/ingest-wave.md", "_toolkit/SCHEMA.md", "_toolkit/style.md")


def read(path):
    with io.open(path, encoding="utf-8", errors="ignore") as f:
        return f.read()


def tracked(root, rel):
    try:
        out = subprocess.run(["git", "ls-files", "--error-unmatch", rel], cwd=root,
                             capture_output=True, text=True, encoding="utf-8", check=False)
        return out.returncode == 0
    except (OSError, UnicodeError):
        return None


def canaries_waiting_for(canary_src, section):
    """Номера канареек, которые по своему ожиданию сторожат этот раздел линтера.

    Ожидание берётся из объявления канарейки (`"lint", <раздел>` или строка «lint §N»), а не из её описания:
    описание — проза, и оно расходится первым.
    """
    out = set()
    # Объявление канарейки — одной строкой: (номер, «что ломаем», «чего ждём», вид проверки, раздел, функция).
    # Раздел берём из пятого поля, а не поиском по тексту: в теле функции лежат докстроки, и поиск по тексту
    # приписывает канарейке чужие разделы (замер 2026-09-24: так нашлось 83 «расхождения» вместо 41).
    for m in re.finditer(r'(?m)^\s*\((\d+),\s*".*?",\s*".*?",\s*"(?:lint|tool|verify|none)",\s*(\d+|None),',
                         canary_src):
        if m.group(2) != "None" and int(m.group(2)) == int(section):
            out.add(int(m.group(1)))
    return out


def check(root, brief=False):
    in_git = os.path.isdir(os.path.join(root, ".git"))
    path = toolkit.script(RULES)
    if not os.path.exists(path):
        return [("rules.tsv", "нет реестра правил — правило, которого нет в реестре, не существует для проекта", "")]
    rows = [l for l in read(path).splitlines() if l.strip() and not l.startswith("#")]
    if not rows or "\t" not in rows[0]:
        return [("rules.tsv", "реестр пуст или без шапки", "")]
    head, body = rows[0].split("\t"), rows[1:]
    # Шапка проверяется по именам колонок: без этого её место молча занимала первая строка правила,
    # и одно правило выпадало из сверки целиком (найдено 2026-09-17 при актуализации реестра).
    expected = ["правило", "где живёт", "род", "маркер", "сторож", "канарейка", "дата"]
    if [h.strip() for h in head[:7]] != expected:
        return [("rules.tsv", "шапка реестра не та: ожидались колонки " + " | ".join(expected)
                 + ", а стоит: " + " | ".join(head[:7]), "")]
    lint = read(toolkit.script("lint_wiki.py"))
    canary = read(toolkit.script("canary_test.py"))
    findings = []
    seen = {}          # раздел → строки, которые его держат, и названные ими канарейки
    for line in body:
        f = line.split("\t")
        while len(f) < 7:
            f.append("")
        rule, where, rod, marker, guard, canary_id, _date = f[:7]
        # Род правила — к кому оно уедет при разделении: колонка обязана быть заполнена одним из двух слов,
        # иначе «механизм против экземпляра» держится на памяти сессии и расходится при первой правке.
        if rod.strip() not in ("механизм", "экземпляр"):
            findings.append((rule, f"род правила не назван: «{rod.strip()[:32]}» (нужно механизм или экземпляр)", ""))
        target = os.path.join(root, where)
        if not os.path.exists(target):
            if supply.included(where):
                findings.append((rule, "файла нет: " + where, ""))
            continue
        if marker and marker not in read(target):
            findings.append((rule, f"маркера нет в {where}: «{marker[:48]}»", ""))
        # Сторож — номер раздела линтера, имя файла, либо слово из набора («канарейка», «совет», см. шапку).
        # Раздел ищем в исходнике линтера, файл — на диске (ниже), слова набора пропускаем: за них отвечает
        # набор канареек и дисциплина, и это записано прямо, чтобы совет не выдавался за проверку.
        if guard.isdigit() and not re.search(r'(?m)^\s*\("' + re.escape(guard) + r'\.', lint):
            findings.append((rule, f"сторожа §{guard} в линтере нет", ""))
        # В колонке канареек бывает несколько номеров через запятую: у рельсов и у правил с несколькими
        # проявлениями их больше одной. Проверяем каждый — иначе запись «78,173» читалась бы как один id
        # и сторож врал бы, что канарейки нет (замечание 2026-09-22).
        named = [x.strip() for x in re.split(r"[,;]", canary_id) if x.strip()]
        for one in named:
            if not re.search(r'(?m)^\s*\(' + re.escape(one) + r',', canary):
                findings.append((rule, f"канарейки №{one} в наборе нет", ""))
        if guard.isdigit():
            seen.setdefault(guard, {"rows": [], "named": set()})
            seen[guard]["rows"].append(rule)
            seen[guard]["named"] |= {int(x) for x in named if x.isdigit()}
        # Словарь сторожа: номер раздела, файл-сторож, «канарейка» (держится только канарейкой) или «совет» (не
        # держится ничем — так и записано, чтобы «правило существует» не выдавало совет за проверку).
        if guard and not (guard.isdigit() or guard in ("канарейка", "совет")):
            if not os.path.exists(os.path.join(root, guard)):
                findings.append((rule, f"сторож «{guard}» не раздел, не файл и не слово из набора "
                                       "(канарейка, совет)", ""))
        reach = " || ".join(read(os.path.join(root, d)) for d in PROTOCOLS if os.path.exists(os.path.join(root, d)))
        if os.path.basename(where) not in reach:
            findings.append((rule, f"{where} не назван ни в протоколе волны, ни в SCHEMA, ни в правилах страниц — артефакт недостижим для сессии", ""))
        # Условие «файл под git» судимо только там, где есть .git. В копии без репозитория (её делает прогон
        # канареек) все правила разом читались как «не под git», и свежий провал реестра было не отличить от
        # постоянного шума: базовая линия аттестации показывала §59 = число правил.
        if in_git and tracked(root, where) is False:
            findings.append((rule, f"{where} не под git — правило живёт в рабочем дереве, а не в репозитории", ""))
    # Полнота колонки канареек считается по РАЗДЕЛУ, а не по строке: один раздел линтера держит иногда два
    # правила, и канарейки делятся между их строками. Проверять надо, что канарейка, сторожáщая раздел, названа
    # хотя бы в одной его строке: безымянная канарейка означает, что её никто не заявляет, и первая правка
    # снимает её как «лишнюю» — так замер 2026-09-24 нашёл 41 канарейку без строки в реестре.
    for sec, data in sorted(seen.items(), key=lambda kv: int(kv[0])):
        absent = sorted(canaries_waiting_for(canary, sec) - data["named"])
        if absent:
            findings.append((data["rows"][0],
                             "раздел §" + sec + ": не названы канарейки, которые его сторожáт — "
                             + ", ".join("№" + str(x) for x in absent), ""))
    if not brief:
        print(f"правил в реестре: {len(body)} | проверено: файл, маркер, сторож, канарейка"
              + (", git" if in_git else " (git недоступен: условие «файл под git» не проверялось)"))
    st = state_line_issue(root, head, body)
    if st:
        findings.append(st)
    return findings


def state_line_issue(root, head, body):
    """Числа в шапке реестра должны совпадать с файлами.

    Шапка обещает, что состояние записано честно, и до 2026-09-17 это была непроверяемая проза: строка
    «правил 86, разделов 60» пережила и тридцать новых правил, и семь новых разделов. Правило, записанное
    словами, обязано сходиться с файлами — иначе оно живёт до первой правки.
    """
    text = read(toolkit.script(RULES))
    m = re.search(r"(?m)^# Состояние на (\S+): правил (\d+) \(механизм (\d+), экземпляр (\d+)\), из них со сторожем "
                  r"в линтере (\d+), без сторожа (\d+), с канарейкой (\d+); разделов линтера всего (\d+)", text)
    if not m:
        return ("rules.tsv", "в шапке нет строки состояния с числами — их сверять не с чем", "")
    real_rules = len(body)
    real_mech = sum(1 for r in body if r.split("\t")[2].strip() == "механизм")
    real_inst = sum(1 for r in body if r.split("\t")[2].strip() == "экземпляр")
    real_guarded = sum(1 for r in body if len(r.split("\t")) > 4 and r.split("\t")[4].strip().isdigit())
    real_advice = sum(1 for r in body if len(r.split("\t")) > 4
                      and r.split("\t")[4].strip() in ("совет", "канарейка"))
    real_canned = sum(1 for r in body if len(r.split("\t")) > 5 and r.split("\t")[5].strip())
    # Разделы считаются по ИСХОДНИКУ линтера, а не запуском линтера: линтер сам зовёт этот реестр (§59),
    # и запуск изнутри давал бесконечную рекурсию — прогон повис и оборвался по таймауту (найдено 2026-09-17).
    lint_src = read(toolkit.script("lint_wiki.py"))
    real_sections = len(set(re.findall(r'\(\"(\d+)\. ', lint_src)))
    stated = tuple(int(x) for x in m.groups()[1:])
    real = (real_rules, real_mech, real_inst, real_guarded, real_advice, real_canned, real_sections)
    if stated != real:
        return ("rules.tsv", "числа в шапке разошлись с файлами: записано %s, на деле %s (правил, механизм, "
                             "экземпляр, со сторожем, без сторожа, с канарейкой, разделов)" % (stated, real), "")
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wiki", default=".")
    ap.add_argument("--brief", action="store_true")
    args = ap.parse_args()
    root = os.path.abspath(args.wiki)
    # Состояние git нужно и здесь: строка итога печатается в main, а не в check.
    in_git = os.path.isdir(os.path.join(root, ".git"))
    findings = check(root, args.brief)
    if findings:
        print("реестр правил не сходится с файлами:")
        for rule, why, _ in findings:
            print(f"  — {rule}: {why}")
        return 1
    if not args.brief:
        tail = "файлы под git" if in_git else "состояние git не проверялось (нет .git — так выглядит копия в прогоне канареек)"
        print(f"реестр правил сходится: каждое правило на месте, сторож и канарейка существуют, {tail}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
