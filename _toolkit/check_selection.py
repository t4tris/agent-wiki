#!/usr/bin/env python3
"""Dry-run проверка выгрузки выбора: какие записи взяты, какие отброшены.

Имена списков берутся из объявления домена экземпляра (`_staging/domain.local.tsv`, ключ `corpora`):
механизм не знает, как владелец назвал свой корпус, отбор и отброшенное. Порядок имён — основной корпус,
отбор, отброшенное, литература.

    python3 _toolkit/check_selection.py --staging ./_staging/telegram/incoming \
                              --selection <файл выгрузки выбора> --wiki <корень проекта>

Ничего не переносит и не создаёт: ни файлов в raw/, ни страниц в wiki, ни записей в корпус.
Только читает, сверяет и печатает отчёт. Единственная запись — сам отчёт `selection-dry-run.md`
в служебной папке накопителя (её можно отключить флагом --no-report).

Что проверяется:
  1. форма файла: списки из объявления домена, постоянная роль литературы из `owner-markup.local.json` и карта context;
  2. все id — целые числа из 333 записей накопителя;
  3. согласованность: нет id сразу в двух списках, нет дублей, отброшенное не пересекается с отмеченными,
     литература входит в основной корпус (метка — подмножество, а не отдельный список);
  4. покрытие: сколько записей размечено, сколько ещё нет (неполный файл — это нормально, не ошибка);
  5. контекст: id существует, текст непустой, контекст не выдан выброшенным записям;
  6. расхождения с моими рекомендациями: что ты снял и что добавил;
  7. понятность: выбранные записи, которые без ручного контекста непонятны (обрубок, подпись к скрину).

Коды возврата: 0 — ошибок нет (неполнота допустима); 1 — есть ошибки, в прод такой файл не пойдёт;
2 — файл не найден или не разобран.
"""
import argparse
import json
import os
import sys

ID_HINT = "id записи"


def load(path):
    try:
        return json.load(open(path, encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as ex:
        print(f"не разобран JSON {path}: {str(ex)[:160]}")
        sys.exit(2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--staging", required=True)
    ap.add_argument("--selection", required=True)
    ap.add_argument("--no-report", action="store_true")
    ap.add_argument("--wiki", default=".", help="корень проекта: там объявление домена")
    ap.add_argument("--fix-dropped", action="store_true",
                    help="пересчитать список dropped как «не взято никуда» и сохранить копию <файл>.fixed.json")
    a = ap.parse_args()
    # Имена списков — из объявления домена: механизм их не знает. Порядок: корпус, отбор, отброшенное, литература.
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import domain
    import owner_markup
    _root = os.path.abspath(a.wiki)
    _names = domain.corpora(_root)
    if len(_names) < 3:
        sys.exit("нет объявления домена: заполни `_staging/domain.local.tsv` (ключ corpora, три имени: "
                 "корпус, отбор, отброшенное) — пример в `domain.local.example.tsv`")
    try:
        _markup = owner_markup.required(_root)
        _lit = _markup["material_roles"]["literature"]
        _ctx_field = _markup["selection"]["context_field"]
        N_LIT = _lit["selection_field"]
    except (KeyError, TypeError, ValueError) as ex:
        sys.exit(f"не заполнена постоянная разметка владельца: {ex}")
    N_MAIN, N_SEL, N_DROP = _names[:3]

    if not os.path.exists(a.selection):
        print(f"файла нет: {a.selection}")
        print("Выгрузи его кнопкой «Скачать мой выбор» в чеклисте и положи в любую папку — путь передай сюда.")
        sys.exit(2)

    cand = json.load(open(os.path.join(a.staging, "port-candidates.json"), encoding="utf-8"))
    # описания изображений (если их уже разобрали): понятная картинка снимает вопрос о контексте
    descr = {}
    dp = os.path.join(a.staging, "media-descriptions.json")
    if os.path.exists(dp):
        descr = json.load(open(dp, encoding="utf-8"))
    by_id = {r["id"]: r for r in cand}
    valid = set(by_id)
    sel = load(a.selection)

    errors, warns, notes = [], [], []

    # 1. форма
    if not isinstance(sel, dict):
        print(f"ошибка: файл выгрузки должен быть объектом со списками {N_MAIN}/{N_SEL}/{N_DROP}, "
              f"а это {type(sel).__name__}")
        sys.exit(2)
    for key in (N_MAIN, N_SEL):
        if key not in sel:
            errors.append(f"нет обязательного списка «{key}»")
    # «literature» — метка владельца из колонки ly: материал без разбора, идёт строкой в реестр, а не в статью
    for key in (N_MAIN, N_SEL, N_DROP, N_LIT):
        v = sel.get(key, [])
        if not isinstance(v, list):
            errors.append(f"«{key}» должен быть списком, а не {type(v).__name__}")
        elif any(not isinstance(x, int) or isinstance(x, bool) for x in v):
            bad = [x for x in v if not isinstance(x, int) or isinstance(x, bool)][:5]
            errors.append(f"в «{key}» не целые id: {bad}")
    ctx = sel.get(_ctx_field, {})
    if not isinstance(ctx, dict):
        errors.append(f"«{_ctx_field}» должен быть объектом «id → текст»")
        ctx = {}
    # в JSON-объекте ключи всегда строки, а id у нас числа — приводим к одному виду
    ctx_by_id = {int(k): v for k, v in ctx.items() if str(k).isdigit()}

    aw = [x for x in sel.get(N_MAIN, []) if isinstance(x, int)]
    ag = [x for x in sel.get(N_SEL, []) if isinstance(x, int)]
    dr = [x for x in sel.get(N_DROP, []) if isinstance(x, int)]
    lit = [x for x in sel.get(N_LIT, []) if isinstance(x, int)]

    # 2. существование id
    for name, lst in ((N_MAIN, aw), (N_SEL, ag), (N_DROP, dr), (N_LIT, lit)):
        unknown = sorted({x for x in lst if x not in valid})
        if unknown:
            errors.append(f"в «{name}» id, которых нет среди {len(valid)} записей: {unknown[:10]}")

    # 3. согласованность
    both = sorted(set(aw) & set(ag))
    if both:
        errors.append(f"id отмечены и в {N_MAIN}, и в {N_SEL}: {both[:10]}")
    # литература — подмножество основного корпуса: материал остаётся в вики, но не голосом темы
    not_in_aw = sorted(set(lit) - set(aw))
    if not_in_aw:
        errors.append(f"литература отмечена вне {N_MAIN}: {not_in_aw[:10]} — метка ставится только тем записям, "
                      f"которые берутся в вики (в чеклисте это делает сама страница)")
    dup_ly = sorted({x for x in lit if lit.count(x) > 1})
    if dup_ly:
        errors.append(f"дубли внутри literature: {dup_ly[:10]}")
    dup_aw = sorted({x for x in aw if aw.count(x) > 1})
    dup_ag = sorted({x for x in ag if ag.count(x) > 1})
    if dup_aw or dup_ag:
        errors.append(f"дубли внутри списков: {N_MAIN} {dup_aw[:10]}, {N_SEL} {dup_ag[:10]}")
    clash = sorted(set(dr) & (set(aw) | set(ag)))
    fixed_path = None
    if clash:
        # известный дефект выгрузки: dropped считался по одной колонке основного корпуса, поэтому записи отбора попадали и туда
        only_ag = set(clash) <= set(ag) and not (set(dr) & set(aw))
        if a.fix_dropped and only_ag:
            repaired = dict(sel)
            repaired[N_DROP] = sorted(valid - set(aw) - set(ag))
            fixed_path = os.path.splitext(a.selection)[0] + ".fixed.json"
            json.dump(repaired, open(fixed_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            dr = set(repaired[N_DROP])
            print(f"починено: dropped пересчитан как «не взято никуда» — {len(dr)} записей, копия: {fixed_path}")
            warns.append(f"исходный файл содержал {len(clash)} id и в dropped, и в отмеченных — исправлено в копии")
        else:
            errors.append(f"id и в dropped, и в отмеченных: {clash[:10]}"
                          + ("  (это известный дефект выгрузки: пересчитай флагом --fix-dropped)" if only_ag else ""))

    # 4. покрытие
    marked = set(aw) | set(ag)
    unmarked = sorted(valid - marked - set(dr))
    coverage = 100.0 * len(marked) / max(1, len(valid))

    # 5. контекст
    bad_ctx = [k for k in ctx if not str(k).isdigit()]
    if bad_ctx:
        errors.append(f"ключи context не id: {bad_ctx[:5]}")
    for k, v in ctx.items():
        kk = str(k)
        if kk.isdigit() and int(kk) not in valid:
            errors.append(f"context для id {kk}, которого нет в накопителе")
        if not isinstance(v, str) or not v.strip():
            errors.append(f"context[{kk}] пустой — либо впиши текст, либо не заводи запись")
        elif len(v.strip()) < 12:
            warns.append(f"context[{kk}] подозрительно короткий: «{v.strip()}»")
        elif kk.isdigit() and int(kk) in dr:
            warns.append(f"context[{kk}] есть, но запись помечена как выброшенная")

    # 6. расхождения с рекомендациями
    rec_aw = {r["id"] for r in cand if r.get(N_MAIN)}
    rec_ag = {r["id"] for r in cand if r.get(N_SEL)}
    added_aw = sorted(set(aw) - rec_aw)
    removed_aw = sorted(rec_aw - set(aw))
    added_ag = sorted(set(ag) - rec_ag)
    removed_ag = sorted(rec_ag - set(ag))

    # 7. понятность без контекста
    need_ctx = []
    for i in sorted(marked & valid):     # несуществующие id уже в ошибках — здесь их пропускаем
        r = by_id[i]
        t = (r.get("text") or "").strip()
        has_links = bool(r.get("links"))
        kinds = r.get("kinds") or ""
        if i in ctx_by_id:
            continue
        d = descr.get(str(i))
        if d and d.get("note_understandable"):
            continue                      # изображение описано и понятно — контекст не нужен
        if d and not d.get("note_understandable"):
            need_ctx.append((i, r.get("day", ""), r.get("src", ""),
                             ("[изображение] " + (d.get("description") or "")[:80])))
            continue
        # голосовое и файл контекста не требуют: содержание — сама запись или файл, а не подпись к ней
        if "голосов" in kinds or "аудио" in kinds or "файл" in kinds:
            continue
        # ссылка с осмысленным текстом и длинный текст понятны сами по себе
        if has_links and len(t) >= 60:
            continue
        if len(t) < 40 and not has_links:
            need_ctx.append((i, r.get("day", ""), r.get("src", ""), t[:60] or "(без текста)"))
        elif len(t) < 120 and ("фото" in kinds or "видео" in kinds) and not has_links:
            need_ctx.append((i, r.get("day", ""), r.get("src", ""), (t[:60] + " [подпись к " + kinds + "]") if t else "(подпись к " + kinds + ")"))

    # печать
    print("=" * 78)
    print("DRY-RUN проверка выгрузки выбора (ничего не переносится)")
    print("=" * 78)
    print(f"файл: {a.selection}")
    print(f"в накопителе: {len(valid)} записей; в выгрузке: {N_MAIN} {len(set(aw))}, "
          f"{N_SEL} {len(set(ag))}, {N_DROP} {len(set(dr))}, {N_LIT} {len(set(lit))}, контекст {len(ctx)}")
    if lit:
        print(f"литература (материал без разбора — строкой в реестр, не в статью): {sorted(set(lit))[:15]}"
              + (f" … ещё {len(set(lit)) - 15}" if len(set(lit)) > 15 else ""))
    print(f"размечено {len(marked)} из {len(valid)} ({coverage:.0f}%), ещё не размечено {len(unmarked)}")
    print()
    print(f"Расхождения с моими рекомендациями (было {N_MAIN} "
          f"{len(rec_aw)}, {N_SEL} {len(rec_ag)}):")
    print(f"  ты добавил в {N_MAIN}: {len(added_aw)}  {added_aw[:12]}")
    print(f"  ты снял из {N_MAIN}:   {len(removed_aw)}  {removed_aw[:12]}")
    print(f"  ты добавил в {N_SEL}:   {len(added_ag)}  {added_ag[:12]}")
    print(f"  ты снял из {N_SEL}:     {len(removed_ag)}  {removed_ag[:12]}")
    if need_ctx:
        print()
        print(f"Нужен ручной контекст — понятность под вопросом ({len(need_ctx)}):")
        for i, day, src, t in need_ctx[:25]:
            print(f"  {i:5d} {day:11s} {str(src)[:26]:26s} {t}")
        if len(need_ctx) > 25:
            print(f"  … ещё {len(need_ctx) - 25}")
    if warns:
        print()
        print(f"Предупреждения ({len(warns)}):")
        for w in warns[:15]:
            print("  -", w)
    print()
    if errors:
        print(f"ОШИБКИ ({len(errors)}) — такой файл в прод не пойдёт:")
        for e in errors[:20]:
            print("  -", e)
    else:
        print("Ошибок нет. Файл пригоден к переносу; неполнота допустима — размеченное перенесётся, "
              "остальное останется в накопителе.")
    print()
    print("Проверено в режиме dry-run: raw/ не менялся, страниц не создано, в корпус ничего не отправлено.")

    if not a.no_report:
        rep = os.path.join(a.staging, "selection-dry-run.md")
        lines = [f"# Dry-run проверка выгрузки выбора ({os.path.basename(a.selection)})", "",
                 f"- записей в накопителе: {len(valid)}",
                 f"- {N_MAIN}: {len(set(aw))}, {N_SEL}: {len(set(ag))}, {N_DROP}: {len(set(dr))}, контекст: {len(ctx)}",
                 f"- размечено {len(marked)} из {len(valid)} ({coverage:.0f}%)",
                 f"- добавлено тобой: {N_MAIN} {len(added_aw)}, {N_SEL} {len(added_ag)}; снято: {N_MAIN} {len(removed_aw)}, {N_SEL} {len(removed_ag)}",
                 f"- ошибок: {len(errors)}, предупреждений: {len(warns)}, записей без контекста: {len(need_ctx)}", ""]
        if need_ctx:
            lines += ["## Нужен контекст", "", "| id | дата | канал | текст |", "|---|---|---|---|"]
            lines += [f"| {i} | {day} | {src} | {t.replace('|', '/')} |" for i, day, src, t in need_ctx]
            lines += [""]
        if errors:
            lines += ["## Ошибки", ""] + [f"- {e}" for e in errors] + [""]
        if warns:
            lines += ["## Предупреждения", ""] + [f"- {w}" for w in warns] + [""]
        open(rep, "w", encoding="utf-8", newline="").write("\n".join(lines))
        print(f"отчёт: {rep}")

    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
