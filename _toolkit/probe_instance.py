#!/usr/bin/env python3
"""Пустой экземпляр: проба механизма на чужом корпусе.

Зачем. Развязку механизма и экземпляра доказывает поведение, а не признаки: надо убрать материал владельца,
положить чужой корпус и прогнать путь. Проба делает это одной командой и печатает числами, где механизм требует
экземпляра: обвал на старте, находки по разделам линтера, отсутствие артефактов. Для чистого fixture допустим
только ожидаемый §64 до карточек; любой другой результат — провал. Числа сравниваются между запусками, поэтому
«механизм тащит экземпляр» видно по счёту, а не на слух.

Что именно попадает в пробу. Положительный allowlist файлов механизма задан в `supply.py`; instance-данные,
локальные объявления и рабочие результаты в него не входят.

Запуск:
    python3 _toolkit/probe_instance.py --fixture <папка с источниками> [--dest <каталог>] [--keep]
    python3 _toolkit/probe_instance.py --domain fixture/user-domain.local.tsv --schema fixture/user-schema.local.md --contract fixture/user-contract.local.json
"""
import argparse
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tempfile

import supply
import toolkit
import domain

CRASH = re.compile(r"(FileNotFoundError|KeyError|IndexError|AttributeError|TypeError|Traceback)")
SECTION = re.compile(r"^## (\d+)\. (.+?): (\d+)\s*$", re.MULTILINE)
CORPUS = "fixture"


def build_instance(root, dest, fixture, from_worktree=False):
    """Собрать чистый экземпляр в `dest`: механизм из архива плюс фикстура вместо материала владельца."""
    if os.path.exists(dest):
        shutil.rmtree(dest)
    os.makedirs(dest)
    inside = subprocess.run(["git", "-C", root, "rev-parse", "--is-inside-work-tree"],
                            capture_output=True, text=True, check=False)
    inside_git = inside.returncode == 0 and inside.stdout.strip() == "true"
    if inside_git and not from_worktree:
        arch = subprocess.run(["git", "-C", root, "archive", "--format=tar", "HEAD"], capture_output=True, check=True)
        # Распаковка через `tarfile`, а не внешний `tar`: внешний tar на Windows декодирует UTF-8-имена
        # (кириллица фикстуры) как cp866 и кладёт мохнатую кашу — в свежем экземпляре §29 видит «файл поставки
        # не назван в карте» на четырёх коротких именах (находка пробы 2026-10-04). `tarfile` читает имена из
        # заголовка архива как UTF-8 и сохраняет их как есть.
        try:
            with tarfile.open(fileobj=io.BytesIO(arch.stdout), mode="r:") as tf:
                tf.extractall(dest)
        except (tarfile.TarError, OSError) as exc:
            sys.exit("не удалось распаковать архив механизма: %s" % exc)
        supply.prune(dest)
    else:
        supply.copy_worktree(root, dest)

    # Фикстуру кладём тем же приёмом, что и любой чужой источник: `ingest.py` раскладывает файлы в
    # `raw/<корпус>/`, ставит шапку записи и строит зеркало. Так проба проверяет не только «первую команду»,
    # но и сам путь приёма: источник, положенный голым копированием, линтер справедливо ругает (§70, §8),
    # и это ругань на пробу, а не на механизм (2026-09-22).
    sources = sorted(f for f in os.listdir(fixture) if f.endswith((".md", ".txt"))) if os.path.isdir(fixture) else []
    if sources:
        proc = subprocess.run([sys.executable, "-X", "utf8", toolkit.script("ingest.py", dest),
                               "--corpus", CORPUS, "--from", fixture, "--wiki", dest],
                              capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
        if proc.returncode != 0:
            sys.exit("не удалось принять фикстуру:\n" + ((proc.stdout or "") + (proc.stderr or "")).strip())
    return dest, sources, []


def copy_local_file(source, dest, key):
    if not os.path.isfile(source):
        sys.exit(f"нет локального файла по пути {source}")
    declared = domain.get(dest, key)
    if not declared:
        sys.exit(f"в объявлении домена нет ключа {key} для локального файла {source}")
    target = domain.path(dest, key)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    shutil.copy(source, target)
    print(f"локальный {key}: {os.path.basename(source)}")
    return target


def compare(path_a, path_b):
    """Два прогона рядом: что изменилось по разделам и в целом.

    Сводки — по файлу на прогон, поэтому сравнение это просто чтение двух файлов: без археологии по коммитам
    и без переписывания мастера накопительным списком (решение владельца 2026-09-22).
    """
    a = json.load(open(path_a, encoding="utf-8"))
    b = json.load(open(path_b, encoding="utf-8"))
    print(f"A: {a.get('run', os.path.basename(path_a))} | {a.get('at', '')} | коммит {a.get('mechanism_commit', '—')}"
          f" | источников {a.get('sources', '?')} | находок {a.get('total')}")
    print(f"B: {b.get('run', os.path.basename(path_b))} | {b.get('at', '')} | коммит {b.get('mechanism_commit', '—')}"
          f" | источников {b.get('sources', '?')} | находок {b.get('total')}")
    sa, sb = a.get("sections") or {}, b.get("sections") or {}
    rows = []
    for key in sorted(set(sa) | set(sb)):
        x, y = sa.get(key, 0), sb.get(key, 0)
        if x != y:
            rows.append(f"  {key}: {x} → {y}")
    print("разделы, где счёт изменился:" if rows else "счёт по разделам не изменился")
    for r in rows:
        print(r)
    print(f"итого: {a.get('total')} → {b.get('total')}")
    return 0


def run_probe(dest, timeout=600):
    """Прогнать первую команду и собрать числа: обвал, находки по разделам."""
    steps = []
    for label, cmd in (("start", [sys.executable, "-X", "utf8", toolkit.script("tasks.py", dest), "start"]),
                       ("lint", [sys.executable, "-X", "utf8", toolkit.script("lint_wiki.py", dest), "--wiki", dest])):
        try:
            p = subprocess.run(cmd, cwd=dest, capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=timeout, check=False)
            out = (p.stdout or "") + (p.stderr or "")
            steps.append({"step": label, "exit": p.returncode, "output": out})
        except subprocess.TimeoutExpired:
            steps.append({"step": label, "exit": None, "output": "", "timeout": True})
    return steps


def numbers(steps):
    """Числа пробы: обвал, находки по разделам, итог."""
    res = {"crash": None, "sections": {}, "total": None, "timeout": False}
    for st in steps:
        out = st.get("output", "")
        if st.get("timeout"):
            res["timeout"] = True
        m = CRASH.search(out)
        if m and not res["crash"]:
            line = out[:m.start()].rstrip().split("\n")[-1] if m.start() else ""
            res["crash"] = (m.group(1), line[-200:])
        for num, title, count in SECTION.findall(out):
            if int(count):
                res["sections"][f"§{num} {title}"] = int(count)
    for line in out.split("\n"):
        if line.startswith("ИТОГО проблем:"):
            res["total"] = int(line.split(":")[1].strip())
    return res


def probe_verdict(found, allowed, gate, crash, timeout, failed_step, lint_exit, total):
    """Вердикт пробы — чистая функция от нормы и фактов прогона. Возвращает (код, лишние разделы).

    Вынесена отдельно, чтобы подмены ловились механически, без распаковки экземпляра: сужение нормы
    и поломка освобождения обязаны ронять вердикт (разбор 2026-09-26, канарейки 215, 216). Первая версия
    освобождения упавшего шага была настоящим дефектом — широким на любой шаг; канарейка поймала бы его
    до прогонов.
    """
    allowed = set(allowed) | ({"64"} if gate else set())
    unexpected = sorted(set(found) - allowed)
    lint_expected = (failed_step == "lint" and lint_exit == 1
                     and not unexpected and (total or 0) > 0)
    code = 1 if (crash or timeout or unexpected or (failed_step and not gate and not lint_expected)) else 0
    return (code, unexpected)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wiki", default=".", help="корень механизма (откуда берётся архив)")
    ap.add_argument("--fixture", default=None,
                    help="папка с чужим корпусом (md или txt); по умолчанию — фикстура проверяемого механизма")
    ap.add_argument("--dest", help="куда собрать чистый экземпляр (по умолчанию — во временный каталог)")
    ap.add_argument("--keep", action="store_true", help="не удалять собранный экземпляр после прогона")
    ap.add_argument("--from-worktree", action="store_true",
                    help="брать механизм из рабочего дерева, а не из последнего коммита (для отладки правки)")
    ap.add_argument("--json", help="куда записать машинную сводку")
    ap.add_argument("--compare", nargs=2, metavar=("A", "B"),
                    help="сравнить две машинные сводки прогонов и выйти (без сборки экземпляра)")
    ap.add_argument("--domain", help="объявление домена экземпляра: кладётся в собранный экземпляр перед прогоном "
                                      "(например, `fixture/user-domain.local.tsv` — домен постороннего владельца)")
    ap.add_argument("--schema", help="локальный schema-файл для пробы; путь берётся из ключа `schema`")
    ap.add_argument("--contract", help="локальный contract-файл для пробы; путь берётся из ключа `contract`")
    ap.add_argument("--owner-markup", help="локальная разметка владельца для пробы; путь берётся из ключа `owner_markup`")
    a = ap.parse_args()

    if a.compare:
        return compare(a.compare[0], a.compare[1])

    root = os.path.abspath(a.wiki)
    if not os.path.isfile(toolkit.script("tasks.py")):
        sys.exit(f"не похоже на корень механизма: {root}")
    fixture = a.fixture or os.path.join(root, "_toolkit", "fixture", "sources")
    if not os.path.isabs(fixture):
        fixture = os.path.join(root, fixture)
    fixture = os.path.abspath(fixture)
    if not os.path.isdir(fixture):
        sys.exit(f"нет папки с фикстурой: {fixture}")
    dest = os.path.abspath(a.dest or os.path.join(tempfile.gettempdir(), "mechanism-probe"))
    dest, sources, created = build_instance(root, dest, fixture, a.from_worktree)
    local_files = []
    if a.domain:
        # Объявление домена постороннего владельца: с ним проверки домена применимы, и проба показывает
        # не «чего не хватает», а работу механизма у того, кто уже заполнил своё (карточка — `fixture/user.md`).
        src_dom = os.path.abspath(a.domain)
        if not os.path.isfile(src_dom):
            sys.exit(f"нет объявления домена по пути {src_dom}")
        target_domain = toolkit.area(dest, "domain.local.tsv")
        os.makedirs(os.path.dirname(target_domain), exist_ok=True)
        shutil.copy(src_dom, target_domain)
        print(f"объявление домена: {os.path.basename(src_dom)}")
    for source, key in ((a.schema, "schema"), (a.contract, "contract"), (a.owner_markup, "owner_markup")):
        if source:
            local_files.append(copy_local_file(os.path.abspath(source), dest, key))
    print(f"чистый экземпляр: {dest}")
    print(f"источников из фикстуры: {len(sources)} → raw/{CORPUS}/")
    if created:
        print(f"механизм потребовал каталогов, которых у него нет: {', '.join(created)} (созданы пробой)")
    steps = run_probe(dest)
    res = numbers(steps)
    print(f"обвал: {'нет' if not res['crash'] else res['crash'][0] + ' — ' + res['crash'][1]}")
    if res["sections"]:
        print("находки по разделам:")
        for key, val in sorted(res["sections"].items(), key=lambda kv: -kv[1])[:15]:
            print(f"  {val:4}  {key}")
    print(f"ИТОГО находок: {res['total']}")
    expected_gate = (not os.path.isfile(toolkit.area(dest, "waves.json"))
                     and bool(sources)
                     and set(res["sections"]) == {next((key for key in res["sections"] if key.startswith("§64 ")), "")})
    if expected_gate:
        print("ожидаемая остановка кассы: источники приняты, карточки ещё не предъявлены")
    # Норма пробы записана множеством, а не памятью (решение владельца 2026-09-26): голая проба без
    # объявления домена законно даёт ровно §89 — как в поставке, где §89 в TOLERATED (delivery_check):
    # домена нет вовсе, и находка «объявление не заполнено» законна. С доменом норма — пустое множество.
    # Сходится — код 0, иначе код 1 с перечислением лишнего: постоянно красный сигнал перестают читать
    # так же, как когда-то перестали читать постоянный ноль.
    if a.domain:
        allowed = set()
    else:
        allowed = {"89"}
    found = {key.split(" ", 1)[0].lstrip("§") for key in res["sections"]}
    failed_step = next((st["step"] for st in steps if st.get("exit") not in (0, None)), None)
    lint_exit = next((st.get("exit") for st in steps if st.get("step") == "lint"), None)
    code, extra = probe_verdict(found, allowed, expected_gate, res["crash"], res["timeout"],
                                failed_step, lint_exit, res["total"])
    if extra:
        shown = {k: v for k, v in res["sections"].items() if k.split(" ", 1)[0].lstrip("§") in extra}
        print("вне нормы пробы:", ", ".join(sorted("%s (%d)" % (k, v) for k, v in shown.items())))
    if failed_step and not expected_gate and code != 0:
        print("шаг с ненулевым кодом:", failed_step)
    # Сводка — по файлу на прогон: имя несёт дату и метку прогона, и ни один файл не перезаписывается.
    # Накопительный список пришлось бы переписывать каждым запуском (в проекте мастер задним числом не
    # переписывается), он растёт и даёт конфликты при слиянии; файл на прогон сравнивается напрямую
    # (`--compare`) и живёт в истории git как есть (решение владельца 2026-09-22).
    day = __import__("datetime").date.today().isoformat()
    audit_dir = toolkit.area(root, "audit")
    os.makedirs(audit_dir, exist_ok=True)
    if a.json:
        out_json = a.json
    else:
        n = 1
        while os.path.exists(os.path.join(audit_dir, f"probe-{day}-{n:02d}.json")):
            n += 1
        out_json = os.path.join(audit_dir, f"probe-{day}-{n:02d}.json")

    def _rev():
        try:
            proc = subprocess.run(["git", "-C", root, "rev-parse", "--short", "HEAD"], capture_output=True,
                                  text=True, encoding="utf-8", errors="replace", check=False)
        except OSError as ex:
            print(f"не удалось прочитать ревизию механизма: {ex}", file=sys.stderr)
            return "—"
        if proc.returncode != 0:
            print(f"не удалось прочитать ревизию механизма: {(proc.stderr or proc.stdout).strip()}", file=sys.stderr)
            return "—"
        return proc.stdout.strip() or "—"

    def _dirty():
        """Проба идёт по рабочему дереву: без этого признака «коммит механизма» описывает не то, что измерено."""
        try:
            proc = subprocess.run(["git", "-C", root, "status", "--porcelain"], capture_output=True,
                                  text=True, encoding="utf-8", errors="replace", check=False)
        except OSError as ex:
            print(f"не удалось прочитать состояние рабочего дерева: {ex}", file=sys.stderr)
            return None
        if proc.returncode != 0:
            print(f"не удалось прочитать состояние рабочего дерева: {(proc.stderr or proc.stdout).strip()}",
                  file=sys.stderr)
            return None
        return bool(proc.stdout.strip())

    summary = {"run": os.path.basename(out_json), "at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
               "mechanism_commit": _rev(), "mechanism_dirty": _dirty(), "fixture": fixture,
               "domain": os.path.basename(a.domain) if a.domain else "",
               "local_files": [os.path.relpath(p, dest).replace(os.sep, "/") for p in local_files],
               "instance": dest, "sources": sources, "created_dirs": created,
               "crash": res["crash"], "sections": res["sections"], "total": res["total"],
               "expected_gate": expected_gate,
               "date": __import__("datetime").date.today().isoformat()}
    # Сводка ложится в репозиторий механизма, в `_staging/audit/`: смысл пробы — сравнивать счёт между
    # запусками, а сводка во временном каталоге живёт до первой уборки, и сравнивать становится не с чем
    # (замечание второй сессии 2026-09-22). Внутрь экземпляра её класть нельзя: она станет его файлом и карта
    # справедливо потребует для неё замысел. Живёт по образцу `probe-<дата>-<NN>.json` — то есть как выход прохода.

    with open(out_json, "w", encoding="utf-8", newline="\n") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)
    print(f"машинная сводка: {out_json}")
    if not a.keep:
        pass  # экземпляр оставляем: он и есть материал для разбора следующих шагов
    sys.exit(code)


if __name__ == "__main__":
    main()
