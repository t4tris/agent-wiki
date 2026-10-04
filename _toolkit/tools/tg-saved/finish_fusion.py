#!/usr/bin/env python3
"""Сцепка постправок после фьюзинга: проверка решений, применение, контроль плейсхолдеров.

До 2026-09-17 эта ступень держалась руками: решения писались файлами, применялись отдельно, а что осталось
после применения — проверялось глазами. На самопроверке 17 сентября это стоило отдельного прохода: в готовом
тексте нашлись три остатка распознавания, которые прошли и правила, и решения агента.

    python3 _toolkit/tools/tg-saved/finish_fusion.py --out export/voice-check/fused                 # проверка: решения покрывают все регионы
    python3 _toolkit/tools/tg-saved/finish_fusion.py --out export/voice-check/fused --apply         # плюс применение и контроль плейсхолдеров

Возвращает 1, если хоть один спорный регион не решён или после применения остался плейсхолдер. Чтение
готового текста глазами и раздел постправок в `fusion-report.md` — работа модели, и скрипт называет её прямо:
это последняя ручная ступень цепочки.
"""
import argparse
import glob
import io
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def read(path):
    with io.open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def fused_files(out):
    markdown = {
        os.path.splitext(os.path.basename(path))[0]: path
        for path in glob.glob(os.path.join(out, "*.md"))
        if os.path.basename(path) != "fusion-report.md"
    }
    paths = list(markdown.values())
    paths.extend(
        path
        for path in glob.glob(os.path.join(out, "*.txt"))
        if os.path.splitext(os.path.basename(path))[0] not in markdown
    )
    return sorted(paths)


def coverage(out):
    """Тихий обрыв расшифровки: длина канона против второго голоса.

    GigaAM режет длинный вход молча, и единственный след — «схожесть текстов 0.01» в отчёте расхождений.
    Правило жило в навыке, скрипта не было. Порог снят с живых данных 2026-09-17: на десяти записях
    канон даёт от 0,75 до 0,89 длины второго голоса, поэтому 0,60 — уже вне наблюдаемого, а для слитого
    текста взят 0,90 от канона: применение решений не имеет права терять текст.
    """
    stt_dir = os.path.join(os.path.dirname(out), "stt")
    sec_dir = os.path.join(stt_dir, "second-voice")
    bad = []
    for path in fused_files(out):
        name = os.path.splitext(os.path.basename(path))[0]
        canon_path = os.path.join(stt_dir, name + ".txt")
        sec_path = os.path.join(sec_dir, name + ".txt")
        fused = len(read(path))
        canon = len(read(canon_path)) if os.path.exists(canon_path) else None
        second = len(read(sec_path)) if os.path.exists(sec_path) else None
        if canon and second and canon < 0.60 * second:
            bad.append("%s: канон %d знаков против второго голоса %d — похоже на тихий обрыв распознавания"
                       % (name, canon, second))
        if canon and fused < 0.90 * canon:
            bad.append("%s: слитый текст %d знаков против канона %d — применение решений потеряло текст"
                       % (name, fused, canon))
    return bad


def journal_path(out):
    return os.path.join(out, "fusion-report.md")


def record_journal(out, entries):
    """Постправки агента после применения решений — разделом в отчёте фьюзинга."""
    path = journal_path(out)
    text = read(path) if os.path.exists(path) else "# Отчёт фьюзинга\n"
    head = "## Постправки агента после применения решений"
    if head not in text:
        text = text.rstrip("\n") + "\n\n" + head + "\n\n"
    for e in entries:
        text = text.rstrip("\n") + "\n- " + e.strip()
    with io.open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text.rstrip("\n") + "\n")
    return path


def journal_state(out):
    """Раздел постправок не отстал от самих правок."""
    path = journal_path(out)
    fused = fused_files(out)
    if not os.path.exists(path):
        return ["нет fusion-report.md: постправки после применения решений не записаны"]
    if "## Постправки агента после применения решений" not in read(path):
        return ["в fusion-report.md нет раздела «Постправки агента после применения решений»"]
    newest = max([os.path.getmtime(p) for p in fused] or [0])
    if newest > os.path.getmtime(path) + 1:
        return ["слитые тексты правились после отчёта: раздел постправок отстал"]
    return []


def main():
    ap = argparse.ArgumentParser(description="Сцепка постправок после фьюзинга")
    ap.add_argument("--out", required=True)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--decisions", nargs="*", default=[])
    ap.add_argument("--journal", nargs="*", default=[],
                    help="записи постправок после применения решений (пишутся разделом в fusion-report.md)")
    ap.add_argument("--check-journal", action="store_true",
                    help="только проверить, что раздел постправок не отстал")
    a = ap.parse_args()
    out = os.path.abspath(a.out)
    flagged_path = os.path.join(out, "flagged.json")
    if not os.path.exists(flagged_path):
        sys.exit("нет flagged.json в %s: фьюзинг не выполнялся" % out)
    if a.journal:
        path = record_journal(out, a.journal)
        print("постправки записаны: %s" % os.path.relpath(path, os.path.dirname(out)))
    if a.check_journal:
        problems = journal_state(out)
        for q in problems:
            print("журнал: " + q)
        return 1 if problems else 0
    # Проверка печатает и пустой результат: молчание не отличимо от «проверка не запускалась» —
    # именно на этом класс ошибок ловился весь день.
    cov = coverage(out)
    if cov:
        for q in cov:
            print("длина и схожесть: " + q)
        if a.apply:
            print("применение остановлено: сначала разберись с обрывом распознавания (--apply не пройдёт)")
            return 1
    else:
        n = len(fused_files(out))
        print("длина и схожесть: подозрений нет (%d записей, канон не короче 60%% второго голоса)" % n)
    flagged = json.load(io.open(flagged_path, encoding="utf-8"))
    ids = {int(x["id"]) for x in flagged}
    files = a.decisions or sorted(glob.glob(os.path.join(out, "decisions-*.json")))
    if not files:
        sys.exit("нет файлов решений (decisions-*.json): спорные регионы никто не решал")
    decided = {}
    for p in files:
        data = json.load(io.open(p, encoding="utf-8"))
        items = data["items"] if isinstance(data, dict) and "items" in data else data
        for it in items:
            decided[int(it["id"])] = it
    missing = sorted(ids - set(decided))
    extra = sorted(set(decided) - ids)
    print("регионов: %d | решений: %d | файлов решений: %d" % (len(ids), len(decided), len(files)))
    if missing:
        print("не решены регионы: " + ", ".join(str(x) for x in missing[:20]) + (" …" if len(missing) > 20 else ""))
    if extra:
        print("решения по несуществующим регионам: " + ", ".join(str(x) for x in extra[:10]))
    if missing and a.apply:
        return 1
    if a.apply:
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        r = subprocess.run([sys.executable, os.path.join(HERE, "apply_agent_decisions.py"),
                            "--out", out, "--decisions"] + files,
                           cwd=HERE, capture_output=True, encoding="utf-8", errors="replace", env=env, check=False)
        tail = ((r.stdout or "") + (r.stderr or "")).strip().splitlines()[-4:]
        print("\n".join(tail))
        if r.returncode != 0:
            return 1
    left = 0
    for p in fused_files(out):
        left += len(re.findall(r"⟦F0\d+⟧", read(p)))
    if left:
        print("плейсхолдеров в готовом тексте: %d — решения применены не полностью" % left)
        return 1
    print("плейсхолдеров нет")
    print("осталась ручная ступень: прочитать готовый текст и записать постправки разделом"
          " «Постправки агента после применения решений» в fusion-report.md")
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
