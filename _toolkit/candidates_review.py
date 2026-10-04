#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""HTML-лист решений по кандидатам в страницы: черновик + галочки + выгрузка JSON.

Зачем. У порога создания страницы теперь есть счёт (`_toolkit/candidates.py`) и очередь решений
(`_staging/candidate-decisions.tsv`), но решения принимает человек — по каждому кандидату: страница
(с одной строкой определения) или отказ с причиной. Черновик решений готовится здесь, а лист нужен,
чтобы пройти его глазами и отметить нужные: те же радио-кнопки, что в других листах отбора, и та же
выгрузка JSON (`kind: page-candidates`), которую принимает `candidates.py collect`.

Запуск:
    python3 _toolkit/candidates_review.py --wiki .            # показать, что попадёт в лист
    python3 _toolkit/candidates_review.py --wiki . --write    # записать _staging/candidates-review.html

Черновик решений — три файла в `_staging/audit/`: candidates-draft-a.json (страницы), -b1 и -b2 (отказы).
Лист отказывается собираться, если покрытие не сходится: у каждого висящего кандидата обязан быть
черновик, а лишних решений быть не должно — иначе часть списка молча выпадет из глаз.
"""
import argparse
import json
import os
import re
import sys

import toolkit

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import candidates as cand

DRAFTS = ("candidates-draft-a.json", "candidates-draft-b1.json", "candidates-draft-b2.json")
REASONS = cand.REASONS


def load_drafts(root):
    items = []
    for name in DRAFTS:
        path = toolkit.area(root, "audit", name)
        if not os.path.exists(path):
            sys.exit(f"нет файла черновика: {os.path.relpath(path, root)}")
        with open(path, encoding="utf-8") as f:
            items += json.load(f).get("items", [])
    return items


def build(root, vault):
    rows = cand.candidates(vault, root)
    live = [c for c in rows if not c["decision"]]
    drafts = {cand.norm(i["term"]): i for i in load_drafts(root)}
    problems, notes = [], []
    for c in live:
        if c["key"] not in drafts:
            problems.append(f"нет черновика решения: {c['name']}")
    for key in drafts:
        if key not in {c["key"] for c in live}:
            # висящих нет — это не ошибка покрытия, а состояние «всё решено»: черновик стал историей
            (notes if not live else problems).append(
                f"черновик без кандидата (закрыт или ниже порога): {key}")
    return live, drafts, problems


def plain(s):
    """Ссылки вики в листе читаются как имена: `[[слаг|Имя]]` -> Имя."""
    return re.sub(r"\[\[([^\]|]+)\|([^\]]+)\]\]", r"\2", re.sub(r"\[\[([^\]]+)\]\]", r"\1", str(s)))


def esc(s):
    return esc0(plain(s))


def esc0(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;"))


def render(root, vault):
    live, drafts, problems = build(root, vault)
    pages = sum(1 for d in drafts.values() if d["decision"] == "page")
    refusals = len(drafts) - pages
    body = []
    for c in live:
        d = drafts[c["key"]]
        is_page = d["decision"] == "page"
        near = f" · ближайшая страница: {c['near']}" if c["near"] else ""
        draft_line = (f"<b>страница</b> · <code>{esc(d.get('slug',''))}</code> — {esc(d.get('definition',''))}"
                      if is_page else f"<b>отказ</b> · {esc(d.get('reason',''))}")
        body.append(f"""
  <tr data-key="{esc(c['key'])}">
    <td class="cand"><b>{esc(c['name'])}</b><div class="muted">источников {c['sources']} · страниц {c['pages_mention']}{esc(near)}</div></td>
    <td class="draft">{draft_line}<div class="muted">{esc(d.get('note',''))}</div></td>
    <td class="ctl">
      <label><input type="radio" name="d-{esc(c['key'])}" value="page" {'checked' if is_page else ''}> страница</label>
      <label><input type="radio" name="d-{esc(c['key'])}" value="refusal" {'checked' if not is_page else ''}> отказ</label>
      <div class="fields">
        <input class="slug" type="text" placeholder="слаг" value="{esc(d.get('slug',''))}" {'disabled' if not is_page else ''}>
        <select class="reason" {'disabled' if is_page else ''}>
          {''.join(f'<option value="{esc(r)}" {"selected" if r == d.get("reason") else ""}>{esc(r)}</option>' for r in REASONS)}
        </select>
      </div>
    </td>
  </tr>""")
    payload = json.dumps([{"key": c["key"], "name": c["name"], "sources": c["sources"],
                           "pages": c["pages_mention"], "near": c["near"]} for c in live],
                         ensure_ascii=False)
    warn = ""
    if problems:
        warn = "<div class='warn'><b>Покрытие не сходится — лист не собираю:</b><ul>" + \
               "".join(f"<li>{esc(p)}</li>" for p in problems) + "</ul></div>"
    html = f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><title>Кандидаты в страницы: черновик решений</title>
<style>
:root {{ color-scheme:dark; --bg:#0f1116; --panel:#161a22; --panel2:#1b2029; --line:#2a3040;
        --ink:#e7eaf1; --muted:#9aa4b8; --acc:#7aa7ff; --acc2:#5b8cf0; --ok:#48c07d; --warn:#e0a33a; --err:#ef6a6a; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; font:14px/1.5 -apple-system,Segoe UI,Roboto,Arial,sans-serif; color:var(--ink); background:var(--bg); }}
header {{ position:sticky; top:0; z-index:5; background:var(--panel); border-bottom:1px solid var(--line); padding:12px 18px; }}
h1 {{ margin:0 0 4px; font-size:17px; }}
.sub {{ color:var(--muted); font-size:13px; }}
.bar {{ display:flex; gap:10px; flex-wrap:wrap; align-items:center; margin-top:10px; }}
button {{ border:1px solid var(--line); background:var(--panel2); color:var(--ink); border-radius:8px; padding:6px 12px; cursor:pointer; font-size:13px; }}
button:hover {{ border-color:var(--acc); color:var(--acc); }}
button.primary {{ background:var(--acc2); color:#0b0e14; border-color:var(--acc2); font-weight:600; }}
input[type=search], input[type=text], select {{ padding:5px 9px; border:1px solid var(--line); border-radius:7px; background:var(--panel2); color:var(--ink); font-size:13px; }}
input[disabled], select[disabled] {{ opacity:.35; }}
main {{ padding:14px 18px 60px; }}
table {{ width:100%; border-collapse:collapse; }}
th {{ text-align:left; color:var(--muted); font-weight:600; font-size:12px; text-transform:uppercase; letter-spacing:.03em; }}
th, td {{ border-bottom:1px solid var(--line); padding:10px 8px; vertical-align:top; }}
td.cand {{ width:26%; }}
td.draft {{ width:44%; color:#cfd6e6; }}
td.ctl {{ width:30%; }}
.muted {{ color:var(--muted); font-size:12px; margin-top:3px; }}
.fields {{ display:flex; gap:6px; margin-top:6px; }}
.slug {{ width:44%; }}
.reason {{ width:56%; }}
label {{ margin-right:12px; font-size:13px; }}
tr.hidden {{ display:none; }}
.warn {{ background:#2a2213; border:1px solid var(--warn); padding:10px 12px; border-radius:8px; margin:10px 0; color:#f0d9a8; }}
.counter {{ color:var(--muted); font-size:13px; }}
code {{ background:var(--panel2); padding:1px 5px; border-radius:5px; }}
</style></head>
<body>
<header>
  <h1>Кандидаты в страницы: черновик решений</h1>
  <div class="sub">Каждая строка — термин, прошедший порог схемы (`SCHEMA.md`, Page Thresholds): <b>2+ источника</b>
  в структурных полях карточек или <b>5+ страниц</b>. Различает человек: отметьте, что оставить, и поправьте черновик,
  где он врёт. Отказ закрывает кандидата — он больше не всплывёт ни в карте, ни в линтере.</div>
  <div class="bar">
    <input type="search" id="q" placeholder="фильтр по имени">
    <button onclick="allAsDraft()">вернуть мой черновик</button>
    <button onclick="allPages()">всё страницами</button>
    <button onclick="allRefusals()">всё отказы</button>
    <button class="primary" onclick="exportJson()">Выгрузить JSON</button>
    <button onclick="resetMarks()">Сбросить метки</button>
    <span class="counter" id="cnt"></span>
  </div>
</header>
<main>
{warn}
<table>
<thead><tr><th>кандидат</th><th>мой черновик</th><th>ваше решение</th></tr></thead>
<tbody>
{''.join(body)}
</tbody></table>
</main>
<script>
const CAND = {payload};
const KEY = 'wiki-candidates-v1-' + CAND.length;
function rows() {{ return [...document.querySelectorAll('tbody tr')]; }}
function mark(r) {{
  const key = r.dataset.key;
  const page = r.querySelector('input[value=page]').checked;
  r.querySelector('.slug').disabled = !page;
  r.querySelector('.reason').disabled = page;
  r.classList.toggle('asrefusal', !page);
}}
function upd() {{
  rows().forEach(mark);
  const pg = rows().filter(r => r.querySelector('input[value=page]').checked).length;
  document.getElementById('cnt').textContent = `страниц: ${{pg}} · отказов: ${{rows().length - pg}} · всего: ${{rows().length}}`;
  save();
}}
function state() {{
  const st = {{}};
  rows().forEach(r => {{
    const key = r.dataset.key;
    st[key] = {{
      d: r.querySelector('input[value=page]').checked ? 'page' : 'refusal',
      slug: r.querySelector('.slug').value,
      reason: r.querySelector('.reason').value
    }};
  }});
  return st;
}}
function save() {{ try {{ localStorage.setItem(KEY, JSON.stringify(state())); }} catch (e) {{}} }}
function load() {{
  let st = null;
  try {{ st = JSON.parse(localStorage.getItem(KEY) || 'null'); }} catch (e) {{}}
  if (!st) return;
  rows().forEach(r => {{
    const s = st[r.dataset.key];
    if (!s) return;
    r.querySelector('input[value=' + s.d + ']').checked = true;
    if (s.slug) r.querySelector('.slug').value = s.slug;
    if (s.reason) r.querySelector('.reason').value = s.reason;
  }});
}}
function allAsDraft() {{
  rows().forEach(r => {{
    const page = r.querySelector('.draft b').textContent.trim() === 'страница';
    r.querySelector('input[value=' + (page ? 'page' : 'refusal') + ']').checked = true;
    const slug = r.querySelector('.draft code');
    if (page && slug) r.querySelector('.slug').value = slug.textContent.trim();
  }});
  upd();
}}
function allPages() {{ rows().forEach(r => r.querySelector('input[value=page]').checked = true); upd(); }}
function allRefusals() {{ rows().forEach(r => r.querySelector('input[value=refusal]').checked = true); upd(); }}
function resetMarks() {{ try {{ localStorage.removeItem(KEY); }} catch (e) {{}} allAsDraft(); }}
function exportJson() {{
  const data = {{ contract_version: '1.0', kind: 'page-candidates', items: [], failures: [] }};
  rows().forEach(r => {{
    const key = r.dataset.key;
    const c = CAND.find(x => x.key === key) || {{}};
    const page = r.querySelector('input[value=page]').checked;
    const item = {{ term: c.name || key, decision: page ? 'page' : 'refusal' }};
    if (page) item.slug = r.querySelector('.slug').value.trim();
    else item.reason = r.querySelector('.reason').value;
    item.note = (r.querySelector('.draft .muted') || {{}}).textContent || '';
    data.items.push(item);
  }});
  const bad = data.items.filter(i => (i.decision === 'page' && !i.slug) || (i.decision === 'refusal' && !i.reason));
  if (bad.length) {{ alert('Не хватает данных: ' + bad.map(i => i.term).join(', ')); return; }}
  const blob = new Blob([JSON.stringify(data, null, 1)], {{type: 'application/json'}});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'candidates-decisions.json';
  a.click();
}}
document.getElementById('q').addEventListener('input', e => {{
  const q = e.target.value.trim().toLowerCase();
  rows().forEach(r => r.classList.toggle('hidden', !!q && !r.dataset.key.includes(q)));
}});
rows().forEach(r => r.querySelectorAll('input[type=radio]').forEach(b => b.addEventListener('change', upd)));
rows().forEach(r => r.querySelector('.slug').addEventListener('input', save));
rows().forEach(r => r.querySelector('.reason').addEventListener('change', save));
load(); upd();
</script>
</body></html>
"""
    return html, len(live), pages, refusals, problems


def main():
    ap = argparse.ArgumentParser(description="Лист решений по кандидатам в страницы.")
    ap.add_argument("--wiki", default=".")
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()
    root = os.path.abspath(a.wiki)
    vault = toolkit.wiki(root)
    html, n, pages, refusals, problems = render(root, vault)
    print(f"кандидатов {n}: черновик — страниц {pages}, отказов {refusals}, без решения {len(problems)}")
    for p in problems:
        print("  !", p)
    if not a.write:
        print("(без --write лист не записан)")
        return 0
    if problems:
        sys.exit("покрытие не сходится — лист не записан")
    target = toolkit.area(root, "candidates-review.html")
    with open(target, "w", encoding="utf-8", newline="") as f:
        f.write(html)
    print("записано:", os.path.relpath(target, root))
    return 0


if __name__ == "__main__":
    sys.exit(main())
