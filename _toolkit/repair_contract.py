#!/usr/bin/env python3
"""Починка нарушений контракта, которые касаются только оболочки отчёта.

    python3 _toolkit/repair_contract.py --envelope --kind stance --reports <файл.json> …

Что делает: если в отчёте нет `contract_version` и/или `kind`, добавляет их сверху, НЕ трогая `items`
и `failures`. Любое содержательное нарушение (значение вне enum, пропущенное поле записи, дубль id)
не чинится — такой отчёт уходит на повторный прогон, иначе починка станет подгонкой.

Каждый факт починки пишется в `retry-log.md`: видно, какой подагент нарушил контракт и на чём.
"""
import argparse
import datetime
import json
import os
import sys

import check_contract
import toolkit

RETRY_LOG = toolkit.area(toolkit.root(), "retry-log.md")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--envelope", action="store_true", help="добавить contract_version и kind")
    ap.add_argument("--kind", required=True)
    ap.add_argument("--reports", nargs="+", required=True)
    a = ap.parse_args()
    spec = check_contract.local_kinds(toolkit.root()).get(a.kind, {})

    fixed = []
    for path in a.reports:
        data = json.load(open(path, encoding="utf-8"))
        miss = [f for f in ("contract_version", "kind") if f not in data]
        if not miss:
            print(f"ок: {path} — оболочка на месте")
            continue
        if not a.envelope:
            print(f"НАРУШЕНИЕ: {path} — нет {miss}")
            continue
        if data.get("kind", a.kind) != a.kind:
            sys.exit(f"{path}: kind в отчёте не совпадает с --kind")
        items, failures = check_contract.report_items(data, spec), data.get("failures", [])
        if not isinstance(items, list) or not items:
            sys.exit(f"{path}: items пуст — починка оболочки не спасёт, нужен повторный прогон")
        items_key = "items" if "items" in data else spec.get("items_alias", "items")
        new = {"contract_version": "1.0", "kind": a.kind, items_key: items, "failures": failures}
        json.dump(new, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        fixed.append((path, miss))
        print(f"оболочка добавлена: {path} (не хватало {', '.join(miss)}; items={len(items)})")

    if fixed:
        mode = "a" if os.path.exists(RETRY_LOG) else "w"
        with open(RETRY_LOG, mode, encoding="utf-8") as f:
            if mode == "w":
                f.write("# Журнал повторов сборки отчётов\n\n")
            for path, miss in fixed:
                f.write(f"- {datetime.date.today().isoformat()} kind={a.kind}: подагент не прислал "
                        f"{', '.join(miss)} — оболочка починена родителем, содержимое не тронуто "
                        f"({os.path.basename(path)})\n")
        print(f"записано в журнал: {len(fixed)}")


if __name__ == "__main__":
    main()
