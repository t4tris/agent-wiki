#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""HTML-лист замыслов владельца: что взято, зачем и что решено — проверить глазами.

Зачем. По каждому из 29 источников с `context_note` решение уже принято (`_toolkit/intents.py`),
но решение надо увидеть рядом с замыслом и с тем, что сказала карточка: иначе проверка превращается
в чтение таблицы. Лист показывает замысел целиком, страницы, которые источник объявляют, вердикт
карточки и моё решение; вы отмечаете, где я неправ, и выгружаете JSON — его принимает
`python3 _toolkit/intents.py collect <файл>`.

    python3 _toolkit/intents_review.py --wiki .            # показать, что попадёт в лист
    python3 _toolkit/intents_review.py --wiki . --write    # записать _staging/intents-review.html
"""
import argparse
import os
import re
import sys

import toolkit

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import intents as it

DECISIONS = it.DECISIONS


def read(p):
    with open(p, encoding="utf-8", errors="ignore") as f:
        return f.read()


def card_verdict(root, source):
    """Что карточка сказала про источник — та самая формальная строка, из-за которой замысел терялся."""
    p = toolkit.area(root, "cards", source)
    if not os.path.exists(p):
        return ""
    t = read(p)
    i = t.find("## О чём источник")
    if i < 0:
        return ""
    body = t[i + len("## О чём источник"):].split("\n## ")[0]
    return re.sub(r"\s+", " ", body).strip()[:400]


def esc(s):
    s = re.sub(r"\[\[([^\]|]+)\|([^\]]+)\]\]", r"\2", str(s))
    s = re.sub(r"\[\[([^\]]+)\]\]", r"\1", s)
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;"))


def render(root, vault):
    q = it.queue(root, vault)
    rows = []
    for r in q:
        pages = ", ".join(r["pages"]) if r["pages"] else "только реестр"
        draft = f"{r['decision']} · {r['reason']}" if r["decision"] else "решения нет"
        rows.append((r, pages, draft, card_verdict(root, r["source"])))
    body = []
    for r, pages, draft, verdict in rows:
        d = r["decision"]
        body.append(f"""
  <tr data-src="{esc(r['source'])}" class="d-{'page' if d == 'страница' else ('covered' if d == 'покрыто страницей' else 'refuse')}">
    <td class="src"><b>{esc(r['source'][:64])}</b>
      <div class="muted">страницы: {esc(pages)}</div>
      <div class="muted">карточка: {esc(verdict) or '—'}</div></td>
    <td class="intent">{esc(r['note'])}</td>
    <td class="ctl">
      <div class="draft"><b>{esc(draft)}</b></div>
      {''.join(f'<label><input type="radio" name="v-{esc(r["source"])}" value="{esc(x)}" {"checked" if x == d else ""}> {esc(x)}</label>' for x in DECISIONS)}
      <input class="reason" type="text" placeholder="причина / слаг страницы" value="{esc(r['reason'])}">
    </td>
  </tr>""")
    payload = [{"source": r["source"], "decision": r["decision"], "reason": r["reason"]} for r, _, _, _ in rows]
    import json as _json
    return f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><title>Замыслы владельца: проверка решений</title>
<style>
:root {{ color-scheme:dark; --bg:#0f1116; --panel:#161a22; --panel2:#1b2029; --line:#2a3040;
        --ink:#e7eaf1; --muted:#9aa4b8; --acc:#7aa7ff; --acc2:#5b8cf0; --ok:#48c07d; --warn:#e0a33a; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; font:14px/1.5 -apple-system,Segoe UI,Roboto,Arial,sans-serif; color:var(--ink); background:var(--bg); }}
header {{ position:sticky; top:0; z-index:5; background:var(--panel); border-bottom:1px solid var(--line); padding:12px 18px; }}
h1 {{ margin:0 0 4px; font-size:17px; }}
.sub {{ color:var(--muted); font-size:13px; }}
.bar {{ display:flex; gap:10px; flex-wrap:wrap; align-items:center; margin-top:10px; }}
button {{ border:1px solid var(--line); background:var(--panel2); color:var(--ink); border-radius:8px; padding:6px 12px; cursor:pointer; font-size:13px; }}
button:hover {{ border-color:var(--acc); color:var(--acc); }}
button.primary {{ background:var(--acc2); color:#0b0e14; border-color:var(--acc2); font-weight:600; }}
input[type=search], input[type=text] {{ padding:5px 9px; border:1px solid var(--line); border-radius:7px; background:var(--panel2); color:var(--ink); font-size:13px; }}
main {{ padding:14px 18px 60px; }}
table {{ width:100%; border-collapse:collapse; }}
th {{ text-align:left; color:var(--muted); font-weight:600; font-size:12px; text-transform:uppercase; letter-spacing:.03em; }}
th, td {{ border-bottom:1px solid var(--line); padding:10px 8px; vertical-align:top; }}
td.src {{ width:26%; }}
td.intent {{ width:40%; color:#cfd6e6; }}
td.ctl {{ width:34%; }}
.muted {{ color:var(--muted); font-size:12px; margin-top:3px; }}
.draft {{ margin-bottom:6px; }}
label {{ margin-right:10px; font-size:13px; white-space:nowrap; }}
input.reason {{ width:100%; margin-top:6px; }}
tr.hidden {{ display:none; }}
tr.d-page td.ctl {{ border-left:3px solid var(--ok); }}
tr.d-covered td.ctl {{ border-left:3px solid var(--acc); }}
tr.d-refuse td.ctl {{ border-left:3px solid var(--warn); }}
.counter {{ color:var(--muted); font-size:13px; }}
</style></head>
<body>
<header>
  <h1>Замыслы владельца: проверка решений (29 источников)</h1>
  <div class="sub">Слева — источник, что про него сказала карточка и какие страницы его объявляют; в центре — замысел
  владельца целиком (из шапки источника); справа — моё решение и причина. Если решение неверное — переключите и
  поправьте причину, потом «Выгрузить JSON»: файл принимает <code>python3 _toolkit/intents.py collect</code>.</div>
  <div class="bar">
    <input type="search" id="q" placeholder="фильтр по имени источника">
    <button onclick="allAsDraft()">вернуть мои решения</button>
    <button class="primary" onclick="exportJson()">Выгрузить JSON</button>
    <button onclick="resetMarks()">Сбросить метки</button>
    <span class="counter" id="cnt"></span>
  </div>
</header>
<main>
<table>
<thead><tr><th>источник</th><th>замысел владельца</th><th>решение (моё — можно изменить)</th></tr></thead>
<tbody>
{''.join(body)}
</tbody></table>
</main>
<script>
const ROWS = {_json.dumps(payload, ensure_ascii=False)};
const KEY = 'wiki-intents-v1';
function rows() {{ return [...document.querySelectorAll('tbody tr')]; }}
function pick(r) {{ return r.querySelector('input[type=radio]:checked').value; }}
function upd() {{
  const c = {{'страница':0, 'покрыто страницей':0, 'отказ':0}};
  rows().forEach(r => {{ c[pick(r)]++; }});
  document.getElementById('cnt').textContent = `страница: ${{c['страница']}} · покрыто: ${{c['покрыто страницей']}} · отказ: ${{c['отказ']}} · всего: ${{rows().length}}`;
  save();
}}
function save() {{
  const st = {{}};
  rows().forEach(r => {{ st[r.dataset.src] = {{d: pick(r), reason: r.querySelector('.reason').value}}; }});
  try {{ localStorage.setItem(KEY, JSON.stringify(st)); }} catch (e) {{}}
}}
function load() {{
  let st = null;
  try {{ st = JSON.parse(localStorage.getItem(KEY) || 'null'); }} catch (e) {{}}
  if (!st) return;
  rows().forEach(r => {{
    const s = st[r.dataset.src];
    if (!s) return;
    const b = r.querySelector('input[value="' + s.d + '"]');
    if (b) b.checked = true;
    if (s.reason) r.querySelector('.reason').value = s.reason;
  }});
}}
function allAsDraft() {{
  rows().forEach(r => {{
    const src = r.dataset.src;
    const rec = ROWS.find(x => x.source === src) || {{}};
    const b = r.querySelector('input[value="' + (rec.decision || 'отказ') + '"]');
    if (b) b.checked = true;
    r.querySelector('.reason').value = rec.reason || '';
  }});
  upd();
}}
function resetMarks() {{ try {{ localStorage.removeItem(KEY); }} catch (e) {{}} allAsDraft(); }}
function exportJson() {{
  const data = {{ contract_version: '1.0', kind: 'intent-decisions', items: [], failures: [] }};
  rows().forEach(r => {{
    const d = pick(r);
    const reason = r.querySelector('.reason').value.trim();
    data.items.push({{ source: r.dataset.src, decision: d, reason: reason }});
  }});
  const bad = data.items.filter(i => i.decision !== 'страница' && !i.reason);
  if (bad.length) {{ alert('Не хватает причины: ' + bad.map(i => i.source).join(', ')); return; }}
  const blob = new Blob([JSON.stringify(data, null, 1)], {{type: 'application/json'}});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'intents-decisions.json';
  a.click();
}}
document.getElementById('q').addEventListener('input', e => {{
  const q = e.target.value.trim().toLowerCase();
  rows().forEach(r => r.classList.toggle('hidden', !!q && !r.dataset.src.toLowerCase().includes(q)));
}});
rows().forEach(r => r.querySelectorAll('input[type=radio]').forEach(b => b.addEventListener('change', upd)));
rows().forEach(r => r.querySelector('.reason').addEventListener('input', save));
load(); upd();
</script>
</body></html>
"""


def main():
    ap = argparse.ArgumentParser(description="Лист проверки решений по замыслам владельца.")
    ap.add_argument("--wiki", default=".")
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()
    root = os.path.abspath(a.wiki)
    vault = toolkit.wiki(root)
    html = render(root, vault)
    print(f"замыслов в листе: {html.count('data-src=')}")
    if not a.write:
        print("(без --write лист не записан)")
        return 0
    target = toolkit.area(root, "intents-review.html")
    with open(target, "w", encoding="utf-8", newline="") as f:
        f.write(html)
    print("записано:", os.path.relpath(target, root))
    return 0


if __name__ == "__main__":
    sys.exit(main())
