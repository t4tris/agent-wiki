#!/usr/bin/env python3
"""Приёмка поставки: та же проверка, но на копии того, что получит посторонний.

    python3 _toolkit/delivery_check.py --wiki . --from-worktree

Зачем. Линтер в рабочем дереве видит пять находок из известной очереди, а в копии поставки видел тридцать девять:
проверки не отличали «нет, потому что не поставляется» от «нет, потому что сломано», а карта адресов читалась только
в форме рабочей выгрузки. Посторонний, скачав проект, первым делом запускает проверку и получает список чужих бед.

Что делает: выгружает HEAD в временный каталог без `.git` (так же, как это делает скачанный архив), прогоняет там
линтер и сверяет разделы с рабочим деревом. Расхождение — находка в поставке; отпечаток карты структуры считается
нормой: он описывает дерево, из которого карта собрана.
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile

import supply
import toolkit

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

SECTIONS = re.compile(r"^## (\d+)\. (.+?): (\d+)$", re.MULTILINE)
TOLERATED = {"14", "29", "30", "36", "43", "67", "89", "91"}


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


def _archive_failure_expected(root, result):
    if not os.path.exists(os.path.join(root, ".git")):
        return True
    detail = _result_text(result).lower()
    return any(marker in detail for marker in (
        "not a valid object name: head",
        "unknown revision or path not in the working tree",
        "needed a single revision",
        "bad revision",
        "ambiguous argument 'head'",
    ))


# 29 — отпечаток карты описывает дерево, из которого собрана; 67 — проверка читает файл протокола волны, которого
# в поставке нет (он про ход работы, а не про проект); 30 и 36 — смотрят в историю коммитов и в реестр отправок
# владельца, а в распакованном архиве у скачавшего их нет; 14 и 43 — сверяют машинные выводы и решения по
# материалу экземпляра, которых в положительном allowlist поставки нет; 89 и 91 — про объявление домена
# экземпляра: в поставке его нет вовсе, поэтому в копии §89 говорит «объявление не заполнено», а §91 нечего считать.


def lint_totals(root):
    try:
        r = subprocess.run([sys.executable, "-X", "utf8", toolkit.script("lint_wiki.py", root), "--wiki", "."],
                           cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    except OSError as exc:
        raise CheckError("линтер не запустился: %s" % exc) from exc
    if r.returncode not in (0, 1):
        raise CheckError(_failure("линтер не завершился", r))
    out = r.stdout or ""
    totals = {m.group(1): (m.group(2).strip(), int(m.group(3))) for m in SECTIONS.finditer(out)}
    if not totals:
        raise CheckError("линтер завершился, но не вывел разделы")
    return totals


def open_debt(root):
    """Открытые и отложенные пункты долга — состояние проекта, его печатает и приёмка, и задача start.

    Печатается до сверки с деревом: в распакованном архиве сверки не будет, а состояние нужно всегда."""
    debt_path = os.path.join(root, "debt.tsv")
    if os.path.exists(debt_path):
        open_rows, delayed = [], []
        for i, line in enumerate(open(debt_path, encoding="utf-8").read().split("\n")):
            if not line.strip() or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) > 4 and parts[4].strip() == "открыт":
                open_rows.append(parts[0])
            elif len(parts) > 4 and parts[4].strip() == "отложен":
                delayed.append(parts[0])
        # Печатаем счёт и, если пунктов немного, их имена; полный перечень живёт в реестре долга.
        tail = ""
        if open_rows:
            shown = open_rows if len(open_rows) <= 5 else open_rows[:5]
            tail = ", ".join(shown)
            if len(open_rows) > len(shown):
                tail += f" и ещё {len(open_rows) - len(shown)}"
        line = f"открытых пунктов долга: {len(open_rows)}" + (f" ({tail})" if tail else "")
        if delayed:
            line += f"; отложено: {len(delayed)}"
        print(line + "; перечень — debt.tsv")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wiki", required=True)
    ap.add_argument("--from-worktree", action="store_true",
                    help="проверить текущую рабочую копию поставки, а не старый HEAD")
    a = ap.parse_args()
    root = os.path.abspath(a.wiki)
    try:
        here = lint_totals(root)
    except CheckError as exc:
        print("ПОСТАВКА: проверка не выполнена: %s" % exc)
        return 1
    tmp = tempfile.mkdtemp(prefix="delivery-")
    try:
        if a.from_worktree:
            supply.copy_worktree(root, tmp)
        else:
            import tarfile
            try:
                r = subprocess.run(["git", "-C", root, "archive", "--format=tar", "HEAD"], capture_output=True, check=False)
            except OSError as exc:
                print("ПОСТАВКА: git archive не запустилась: %s" % exc)
                return 1
            if r.returncode != 0:
                if _archive_failure_expected(root, r):
                    if os.path.exists(os.path.join(root, ".git")):
                        print("git есть, истории в нём нет: сверка с рабочим деревом невозможна — сделайте в копии начальный")
                        print("коммит (`add:`), иначе проверки видят всё дерево новым, а закрытие волны запишет пустое окно")
                    else:
                        print("git нет (распакованный архив): сверка с рабочим деревом невозможна, проверяю этот каталог")
                    open_debt(root)
                    here = lint_totals(root)
                    counted = {sec: n for sec, (_, n) in here.items() if sec not in TOLERATED}
                    total = sum(counted.values())
                    for sec, (name, n) in sorted(here.items(), key=lambda kv: int(kv[0])):
                        if n and sec not in TOLERATED:
                            print(f"- §{sec} {name}: {n}")
                    print(f"находок: {total}" + (f" (допустимые {', '.join('§' + t for t in sorted(TOLERATED))} "
                                                f"не считаются)" if TOLERATED else ""))
                    print("ПОСТАВКА: проверено в этом каталоге (без сверки с деревом владельца)")
                    return 1 if total else 0
                print("ПОСТАВКА: git archive не выполнена: %s" % _failure("сбой git", r))
                return 1
            archive = _text(r.stdout)
            with open(os.path.join(tmp, "head.tar"), "wb") as f:
                f.write(archive.encode() if isinstance(archive, str) else archive)
            with tarfile.open(os.path.join(tmp, "head.tar")) as tf:
                tf.extractall(tmp)
            os.remove(os.path.join(tmp, "head.tar"))
            supply.prune(tmp)

        files = sum(len(fs) for _, _, fs in os.walk(tmp))
        print(f"в копии файлов: {files}")
        # Производные артефакты в копии пересобираются, прежде чем её проверять: свод тезисов, карта, мостик,
        # панели. Иначе копия показывает находки вроде «свод не называет страницу» (§86) или «карта устарела»
        # (§29) на собственных же устаревших выводах, и расхождение с рабочим деревом получается мнимым —
        # ровно то, что мы ловим. Решение владельца: «проверки в копии дают то же, что в рабочем дереве».
        for step in (["build_index.py"], ["audit_report.py"], ["stance_context.py", "--write"],
                     ["project_map.py", "generate"], ["bridge.py", "generate"],
                     ["panel_freshness.py", "--rebuild"]):
            try:
                r = subprocess.run([sys.executable, "-X", "utf8", toolkit.script(step[0], tmp)] + step[1:]
                                   + ["--wiki", tmp], cwd=tmp,
                                   capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
            except OSError as exc:
                print("пересборка %s не запустилась: %s" % (step[0], exc))
                return 1
            if r.returncode != 0:
                print("пересборка %s не удалась: %s" % (step[0], _failure("сбой", r)))
                return 1
        there = lint_totals(tmp)
    except CheckError as exc:
        print("ПОСТАВКА: проверка не выполнена: %s" % exc)
        return 1
    except OSError as exc:
        print("ПОСТАВКА: проверка не выполнена: %s" % exc)
        return 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # §84 в копии: артефакт слоя проходов, который держит объявление домена экземпляра, теряет держателя —
    # объявления в поставке нет. Это не расхождение проверок, а отсутствие файла экземпляра: считаем такие
    # находки объявленными и говорим о них вслух (решение 2026-09-22, тот же случай, что §89 и §91).
    declared_here = set()
    dom_path = toolkit.area(root, "domain.local.tsv")
    if os.path.exists(dom_path):
        for line in open(dom_path, encoding="utf-8"):
            for chunk in line.split("\t")[1:]:
                for item in chunk.split(","):
                    rel = item.split("=")[-1].strip()
                    if rel.endswith((".tsv", ".json", ".md")):
                        declared_here.add(rel)
    declared_84 = 0
    if declared_here:
        for sec, (name, n) in there.items():
            if sec == "84" and n:
                declared_84 = n
                print(f"§84: {n} артефакт(ов) держит объявление домена экземпляра — в поставке его нет: "
                      + ", ".join(sorted(declared_here)[:4]))

    bad = []
    for sec, (name, n) in sorted(there.items(), key=lambda kv: int(kv[0])):
        if sec == "84" and n and n <= declared_84:
            continue
        if sec not in here:
            if sec not in TOLERATED:
                bad.append(f"§{sec} {name}: раздел отсутствует в выводе рабочего дерева")
            continue
        if n and here[sec][1] == 0 and sec not in TOLERATED:
            bad.append(f"§{sec} {name}: в поставке {n}, в рабочем дереве чисто")
    for sec, (name, n) in sorted(here.items(), key=lambda kv: int(kv[0])):
        if sec not in there:
            if sec not in TOLERATED:
                bad.append(f"§{sec} {name}: раздел отсутствует в выводе поставки")
            continue
        if n and there[sec][1] == 0 and sec not in TOLERATED:
            bad.append(f"§{sec} {name}: в рабочем дереве {n}, а в копии чисто — проверка в копии не сработала")
    open_debt(root)
    # Считаем без допустимых разделов: иначе счёт не сходится с вердиктом, и читатель видит «5 против 6» там,
    # где расхождения только в отпечатке карты и в проверке волны.
    def counted(secs):
        return sum(0 if (sec == "84" and n <= declared_84) else n
                   for sec, (_, n) in secs.items() if sec not in TOLERATED)

    print(f"находок: рабочее дерево {counted(here)}, поставка {counted(there)}"
          f" (допустимые {', '.join('§' + t for t in sorted(TOLERATED))} не считаются)")
    for line in bad:
        print("- " + line)
    if bad:
        print("ПОСТАВКА: разошлась с рабочим деревом — см. список выше")
        return 1
    print("ПОСТАВКА: проверки в копии дают то же, что в рабочем дереве")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
