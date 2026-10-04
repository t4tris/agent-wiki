#!/usr/bin/env python3
"""Валидатор отчётов подагентов: контракт v1.0.

    python3 _toolkit/check_contract.py <файл.json> [<файл2.json> …]
    exit 0 — все отчёты валидны; exit 1 — есть нарушения (печатаются); exit 2 — файл не разобран.

Правила контракта (см. _toolkit/contract-v1.md):
  * отчёт — один JSON-объект с полем contract_version и полем kind;
  * kind определяет набор полей и допустимые значения (enum'ы строгие);
  * полный словарь видов, полей, типов, enum и required объявляет `contract-v1.local.json`;
    встроенного запасного словаря в механизме нет;
  * поэтому проверку зовут с корнем проекта (`--wiki <корень>`): без него объявление экземпляра не читается,
    и годный файл экземпляра отвергается как неизвестный вид;
  * children НЕ пишут файлы вики и не правят источники: они отдают отчёт, родитель сохраняет;
  * пустое значение — это [] или явное «нет», но не null-заглушка и не строка вместо массива;
  * неудачные попытки идут в failures[], а не молча теряются.
"""
import json
import os
import sys

VERSION = "1.0"


def _type(value):
    return {"int": int, "bool": bool, "str": str, "list": list, "dict": dict}.get(value, str)


def _present(value):
    return value is not None and (not isinstance(value, str) or bool(value.strip()))


def _condition_matches(value, condition):
    if not isinstance(condition, dict):
        return False
    if "equals" in condition and value != condition["equals"]:
        return False
    if "in" in condition:
        allowed = condition["in"]
        if not isinstance(allowed, list) or value not in allowed:
            return False
    if "contains_any" in condition:
        tokens = condition["contains_any"]
        if not isinstance(value, str) or not isinstance(tokens, list):
            return False
        if not any(token in value for token in tokens):
            return False
    return any(key in condition for key in ("equals", "in", "contains_any"))


def _rule_problems(item, spec):
    rules = spec.get("rules") or {}
    problems = []
    for field in rules.get("nonempty", []):
        if field in item and not _present(item[field]):
            problems.append(f"items: поле {field} не должно быть пустым")
    for field, limit in (rules.get("min_length") or {}).items():
        value = item.get(field)
        if isinstance(value, str) and len(value.strip()) < int(limit):
            problems.append(f"items: поле {field} короче {limit} символов")
    for rule in rules.get("required_if", []):
        if not isinstance(rule, dict):
            continue
        condition = rule.get("when") or {}
        field = condition.get("field")
        if not field or field not in item or not _condition_matches(item[field], condition):
            continue
        for required in rule.get("fields", []):
            if not _present(item.get(required)):
                problems.append(f"items: поле {required} обязательно при выполненном условии")
    for rule in rules.get("dict_enum", []):
        if not isinstance(rule, dict):
            continue
        field = rule.get("field")
        enum_name = rule.get("enum")
        allowed = spec.get("enums", {}).get(enum_name, set())
        value = item.get(field)
        if isinstance(value, dict):
            for source, entry in value.items():
                if entry not in allowed:
                    problems.append(f"items.{field}[{source}]: значение {entry!r} вне набора {sorted(allowed)}")
    return problems


def report_items(data, spec):
    if isinstance(data.get("items"), list):
        return data["items"]
    alias = spec.get("items_alias")
    if alias and isinstance(data.get(alias), list):
        return data[alias]
    if "items" in data:
        return data["items"]
    if alias and alias in data:
        return data[alias]
    return None


def local_kinds(root):
    import domain as _domain

    path = _domain.contract_path(root)
    if not path or not os.path.exists(path):
        return {}
    try:
        data = json.load(open(path, encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as e:
        print(f"предупреждение: словарь контракта не прочитан ({e})")
        return {}
    out = {}
    for kind, spec in data.items():
        if not isinstance(spec, dict):
            continue
        out[kind] = {
            "item": {k: _type(v) for k, v in (spec.get("item") or {}).items()},
            "enums": {k: set(v) for k, v in (spec.get("enums") or {}).items()},
            "required": list(spec.get("required") or []),
            "items_alias": spec.get("items_alias"),
            "rules": spec.get("rules") if isinstance(spec.get("rules"), dict) else {},
        }
    return out


def check(path, kinds=None):
    problems = []
    try:
        data = json.load(open(path, encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as ex:
        return [f"не разобран JSON: {str(ex)[:120]}"], None
    if not isinstance(data, dict):
        return ["отчёт должен быть объектом, а не массивом или строкой"], None
    if data.get("contract_version") != VERSION:
        problems.append(f"contract_version: ожидается {VERSION!r}, получено {data.get('contract_version')!r}")
    kind = data.get("kind")
    kinds = kinds or {}
    if kind not in kinds:
        problems.append(f"kind: неизвестный тип отчёта {kind!r} (допустимы {sorted(kinds)})")
        return problems, kind
    spec = kinds[kind]
    items = report_items(data, spec)
    if not isinstance(items, list):
        problems.append("items: должен быть массивом (пусто — []), не строкой и не null")
        items = []
    ids = []
    for i, it in enumerate(items):
        if not isinstance(it, dict):
            problems.append(f"items[{i}]: элемент не объект")
            continue
        for field in spec["required"]:
            if field not in it:
                problems.append(f"items[{i}]: нет обязательного поля {field}")
                continue
            want = spec["item"].get(field)
            if want and not isinstance(it[field], want):
                problems.append(f"items[{i}].{field}: ожидается {want.__name__}, получено {type(it[field]).__name__}")
        for field, allowed in spec["enums"].items():
            if field in it and it[field] not in allowed:
                problems.append(f"items[{i}].{field}: «{it[field]}» вне набора {sorted(allowed)}")
        problems.extend(_rule_problems(it, spec))
        if "id" in it:
            ids.append(it["id"])
    if ids and len(ids) != len(set(map(str, ids))):
        problems.append("items: повторяющиеся id")
    failures = data.get("failures", [])
    if not isinstance(failures, list):
        problems.append("failures: должен быть массивом (пусто — [])")
    else:
        for i, f in enumerate(failures):
            if not isinstance(f, dict):
                problems.append(f"failures[{i}]: элемент не объект")
                continue
            for field in ("what", "reason"):
                if not f.get(field):
                    problems.append(f"failures[{i}]: нет поля {field}")
            if "retry_by_parent" not in f:
                problems.append(f"failures[{i}]: нет флага retry_by_parent")
    return problems, kind


CODE_BY_MARKER = [                     # код причины -> родитель маршрутизирует, а не парсит текст
    ("contract_version", "envelope.contract_version"),
    ("kind", "envelope.kind"),
    ("items", "shape.items"),
    ("failures", "shape.failures"),
    ("дубл", "content.duplicate_id"),
    ("enum", "content.enum"),
]


def violation_code(message):
    """Код причины нарушения: родитель читает код, а не разбирает формулировку."""
    low = message.lower()
    for marker, code in CODE_BY_MARKER:
        if marker in low:
            return code
    return "other"


def main():
    if len(sys.argv) < 2:
        sys.exit("укажите файл(ы) отчёта")
    as_json = "--json" in sys.argv
    files = [a for a in sys.argv[1:] if not a.startswith("--")]
    root = "."
    if "--wiki" in sys.argv:
        i = sys.argv.index("--wiki")
        if i + 1 < len(sys.argv):
            root = sys.argv[i + 1]
            files = [f for f in files if f != root]
    kinds = local_kinds(root)
    bad, report = 0, []
    for path in files:
        problems, kind = check(path, kinds)
        report.append({"file": os.path.basename(path), "kind": kind, "valid": not problems,
                       "violations": [{"code": violation_code(x), "message": x} for x in problems]})
        if problems:
            bad += 1
            if not as_json:
                print(f"НАРУШЕНИЯ {path} (kind={kind}):")
                for x in problems:
                    print(f"  - [{violation_code(x)}] {x}")
        elif not as_json:
            data = json.load(open(path, encoding="utf-8"))
            spec = kinds.get(kind, {})
            items = report_items(data, spec)
            print(f"ок: {path} (kind={kind}, записей {len(items) if isinstance(items, list) else 0}, "
                  f"failures {len(data.get('failures', []))})")
    if as_json:
        print(json.dumps({"valid": bad == 0, "reports": report}, ensure_ascii=False, indent=1))
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
