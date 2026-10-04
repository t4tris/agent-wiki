#!/usr/bin/env python3
"""Сборщик отчётов подагентов: родитель извлекает JSON и сохраняет артефакт.

    python3 _toolkit/collect_report.py --src <вывод ребёнка…> --dst <куда сохранить.json> [--kind stance]

Почему так, а не иначе: ребёнок НЕ пишет файлы вики и не правит источники —
он возвращает отчёт, а сохраняет его родитель одним детерминированным шагом. Тогда:
  * результат всегда в одном месте и в одном формате;
  * видно, какой отчёт чей и валиден ли он;
  * неудача ребёнка — это код возврата 2 и запись в журнал повторов, а не пустой файл.

Exit codes: 0 = сохранено; 1 = JSON найден, но не прошёл контракт; 2 = JSON не найден (RETRY_NEEDED).
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)          # инструмент механизма зовётся через раскладку, а не по расположению соседа
import check_contract
import toolkit

RETRY_LOG = toolkit.area(toolkit.root(), "retry-log.md")


def find_json(text, kinds=None):
    """Ищем объект контракта: перебор позиций '{' + raw_decode, как в разборе отчётов подагентов."""
    dec = json.JSONDecoder()
    for m in re.finditer(r"\{", text):
        try:
            obj, _ = dec.raw_decode(text[m.start():])
        except json.JSONDecodeError:
            obj = None
        if not isinstance(obj, dict) or "contract_version" not in obj:
            continue
        if "items" in obj:
            return obj
        spec = (kinds or {}).get(obj.get("kind"), {})
        alias = spec.get("items_alias")
        if alias and alias in obj:
            return obj
    return None


def log_retry(kind, attempt, src):
    line = (f"- [ ] RETRY: kind={kind} — успешно с {attempt}-й попытки "
            f"({os.path.basename(src) if src else '—'}, {datetime.date.today().isoformat()})\n")
    mode = "a" if os.path.exists(RETRY_LOG) else "w"
    with open(RETRY_LOG, mode, encoding="utf-8") as f:
        if mode == "w":
            f.write("# Журнал повторов сборки отчётов\n\n")
        f.write(line)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", nargs="+", required=True)
    ap.add_argument("--dst", required=True)
    ap.add_argument("--kind", default=None)
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()
    kinds = check_contract.local_kinds(toolkit.root())

    for attempt, src in enumerate(a.src, 1):
        text = ""
        if os.path.exists(src):
            text = open(src, encoding="utf-8", errors="replace").read()
        else:
            probe = subprocess.run(["git", "-C", os.path.dirname(src) or ".", "log", "-1"],
                                   capture_output=True, check=False)
            if probe.returncode != 0:
                detail = (probe.stderr or b"").decode("utf-8", errors="replace").strip()
                if not a.quiet:
                    print(f"не удалось проверить источник {src}: {detail or 'git завершился с ошибкой'}", file=sys.stderr)
            text = ""
        obj = find_json(text, kinds)
        if obj is None:
            if not a.quiet:
                print(f"JSON контракта не найден: {src}")
            continue
        if a.kind and obj.get("kind") != a.kind:
            print(f"отчёт не того типа: ожидался {a.kind}, получен {obj.get('kind')}")
            continue
        payload = json.dumps(obj, ensure_ascii=False, indent=1)
        checksum = hashlib.md5(payload.encode()).hexdigest()
        if os.path.exists(a.dst):
            old = hashlib.md5(open(a.dst, encoding="utf-8").read().encode()).hexdigest()
            if old == checksum:
                print(f"SKIP: {a.dst} уже собран (checksum {checksum[:8]})")
                sys.exit(0)
            print(f"ОБНОВЛЕНИЕ: {a.dst} отличается (новый checksum {checksum[:8]})")
        os.makedirs(os.path.dirname(a.dst) or ".", exist_ok=True)
        open(a.dst, "w", encoding="utf-8").write(payload)

        chk = subprocess.run(toolkit.python("check_contract.py") + ["--wiki", toolkit.root(), a.dst],
                             capture_output=True, text=True, check=False)
        print(chk.stdout.strip() or chk.stderr.strip())
        if chk.returncode != 0:
            print(f"контракт не пройден: {a.dst} (файл сохранён как есть, для разбора)")
            sys.exit(1)
        if attempt > 1:
            log_retry(obj.get("kind"), attempt, src)
        print(f"сохранено: {a.dst} [{checksum[:8]}]")
        sys.exit(0)

    print("RETRY_NEEDED: JSON контракта не найден ни в одном источнике. "
          "Запустите подагента повторно (до двух попыток) и передайте новые файлы.")
    sys.exit(2)


if __name__ == "__main__":
    main()
