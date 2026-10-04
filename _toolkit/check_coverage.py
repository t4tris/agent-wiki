#!/usr/bin/env python3
"""Сверка покрытия веера в обе стороны: входной список против собранных id.

Пропущенный входной id и выдуманный собранный ловятся одинаково: на выдуманный
потом сошлётся карточка, и расхождение выглядит правдой. Spot-check — только
с записью результата: механическая часть сверяет форму и полноту записи, а не оценивает.
Хвостовой `--wiki` от единой точки входа терпим. Отчёт — парой, не вердиктом.
"""
import argparse
import json
import os
import sys
import tempfile

VERDICTS = ("pass", "fail")


def load_ids(path):
    with open(path, encoding="utf-8") as f:
        return [l.strip() for l in f.read().splitlines() if l.strip()]


def load_spec(path):
    """SPEC веера: задача, бриф, граница, запреты и входные id. Возвращает (ids, проблемы)."""
    try:
        with open(path, encoding="utf-8") as f:
            spec = json.load(f)
    except (OSError, ValueError) as e:
        return None, [f"SPEC не читается: {e}"]
    problems = []
    if not isinstance(spec, dict):
        return None, ["SPEC — не объект"]
    for key in ("task", "brief", "boundary", "ids"):
        if not spec.get(key):
            problems.append(f"SPEC без обязательного поля: {key}")
    ids = spec.get("ids", [])
    if not isinstance(ids, list) or not all(str(i).strip() for i in ids):
        problems.append("SPEC.ids — не список непустых id")
    if problems:
        return None, problems
    return [str(i) for i in ids], []


def collected_ids(reports):
    out = []
    for r in reports:
        with open(r, encoding="utf-8") as f:
            obj = json.load(f)
        items = obj.get("items", [])
        out.extend(str(i.get("id")) for i in items if isinstance(i, dict) and "id" in i)
    return out


def check_spot(path, collected):
    problems = []
    try:
        rows = [l.split("\t") for l in open(path, encoding="utf-8").read().splitlines() if l.strip()]
    except OSError:
        return ["записи spot-check нет — нет и spot-check"]
    if not rows:
        return ["запись spot-check пуста"]
    for row in rows:
        if len(row) < 3 or not row[0] or row[1] not in VERDICTS or not row[2]:
            problems.append(f"битая строка записи: {'|'.join(row)}")
            continue
        if row[0] not in collected:
            problems.append(f"запись ссылается на несобранный id: {row[0]}")
        if row[1] == "fail":
            problems.append(f"spot-check не прошёл id {row[0]}: {row[2]}")
    return problems


def check(expected, reports, spot_path=None):
    collected = collected_ids(reports)
    missing = sorted(set(expected) - set(collected))
    extra = sorted(set(collected) - set(expected))
    lines = []
    ok = True
    if missing:
        lines.append(f"пропущенные входные id: {', '.join(missing)}")
        ok = False
    else:
        lines.append("пропущенные: чисто")
    if extra:
        lines.append(f"лишние собранные id: {', '.join(extra)}")
        ok = False
    else:
        lines.append("лишние: чисто")
    if spot_path is not None:
        problems = check_spot(spot_path, collected)
        if problems:
            lines.extend(problems)
            ok = False
        else:
            lines.append("запись spot-check: в порядке")
    return ok, lines


def self_test():
    tmp = tempfile.mkdtemp(prefix="coverage-")
    exp = os.path.join(tmp, "expect.txt")
    open(exp, "w", encoding="utf-8").write("1\n2\n3\n")
    r1 = os.path.join(tmp, "r1.json")
    r2 = os.path.join(tmp, "r2.json")
    json.dump({"items": [{"id": 1}, {"id": 2}]}, open(r1, "w"))
    json.dump({"items": [{"id": 3}]}, open(r2, "w"))
    ok, _ = check(load_ids(exp), [r1, r2])
    print(f"чистый набор: {'прошёл' if ok else 'УПАЛ'}")
    if not ok:
        return 1
    r3 = os.path.join(tmp, "r3.json")
    json.dump({"items": [{"id": 99}]}, open(r3, "w"))
    ok, _ = check(load_ids(exp), [r1, r2, r3])
    print(f"лишний id: {'пойман' if not ok else 'ПРОПУЩЕН'}")
    if ok:
        return 1
    ok, _ = check(load_ids(exp), [r1])
    print(f"пропущенный id: {'пойман' if not ok else 'ПРОПУЩЕН'}")
    if ok:
        return 1
    rec = os.path.join(tmp, "spot.tsv")
    open(rec, "w", encoding="utf-8").write("2\tpass\tцитата на месте\n9\tpass\tнет такого\n")
    ok, _ = check(load_ids(exp), [r1, r2], rec)
    print(f"битая запись: {'поймана' if not ok else 'ПРОПУЩЕНА'}")
    if ok:
        return 1
    spec = os.path.join(tmp, "spec.json")
    json.dump({"task": "t", "brief": "b", "boundary": ["x"], "forbidden": ["y"],
               "ids": ["1", "2", "3"]}, open(spec, "w"))
    rc = main(["--spec", spec, "--reports", r1, r2])
    print(f"SPEC в порядке: {'принят' if rc == 0 else 'ОТВЕРГНУТ'}")
    if rc != 0:
        return 1
    rc = main(["--expect", exp, "--reports", r1, r2])
    print(f"конверт с id без SPEC: {'отказан' if rc != 0 else 'ПРИНЯТ — гейт дырявый'}")
    if rc == 0:
        return 1
    print("все знаки сошлись")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Сверка покрытия веера в обе стороны.")
    ap.add_argument("--expect", help="файл входных id, по одному на строку (только для отчётов без id)")
    ap.add_argument("--spec", default=None, help="SPEC веера (JSON: task, brief, boundary, forbidden, ids)")
    ap.add_argument("--reports", nargs="*", default=[], help="собранные отчёты-конверты")
    ap.add_argument("--spot-check", default=None, help="запись spot-check (id, вердикт, основание через табуляцию)")
    ap.add_argument("--self-test", action="store_true", help="доказательство в обе стороны на песочнице")
    ap.add_argument("--wiki", default=None, help="ключ цепочки, игнорируется")
    a = ap.parse_args(argv)
    if a.self_test:
        return self_test()
    if not a.reports:
        print("нужен хотя бы один --reports")
        return 2
    collected = collected_ids(a.reports)
    if a.spec:
        expected, spec_problems = load_spec(a.spec)
        if spec_problems:
            print("\n".join(spec_problems))
            return 1
        ok, lines = check(expected, a.reports, a.spot_check)
    else:
        if collected:
            print("конверт с id требует SPEC-файл (--spec): список без документа не принимается. "
                  "Образец: _toolkit/check_coverage.spec.example.json")
            return 1
        if not a.expect:
            print("нужен --expect или --spec")
            return 2
        ok, lines = check(load_ids(a.expect), a.reports, a.spot_check)
    print("\n".join(lines))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
