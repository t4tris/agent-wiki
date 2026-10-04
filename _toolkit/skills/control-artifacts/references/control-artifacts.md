# Control artifacts: records, generators, views

Depth for the rules in SKILL.md. Applies to any view a human approves by eye: ingest/wave maps, project
structure maps, review lists with checkboxes, control dashboards built from a repo or a wiki.

## 1. Record + generator + guard

Split the artifact in three, all in the repo:

- **record** — what happened, frozen: `<dir>/<event>.json` (elements touched, sources embedded, line counts, the
  summary line the human read). History is not recomputed.
- **source artifact** — the earlier hand-built version, kept as `<event>.original.html`, so the record stays
  reproducible instead of resting on one agent's parse.
- **generator** — `<name>.py` beside the project's other scripts, wired into its task entry point, with modes:
  `--record` (rebuild the record from the source artifact), default = dry report, `--write` (render).

A generator must be idempotent: run it twice and the second run leaves the file byte-identical. When the project has
its own formatter for the format the generator emits (a table aligner, a front-matter normaliser), run that
formatter over the generated text *before* writing — otherwise the formatter rewrites the whole file on its next
pass, every regeneration shows as a full-file change, and real edits hide inside the churn. Check with a second run
plus the formatter reporting "nothing to align".

Record extraction must refuse partial parses:

```python
chunks = re.findall(r"(?s)<div class='pg([^']*)'>(.*?)(?=<div class='pg|</div>\s*</body>)", src)
slugs = re.findall(r"<code class='pth'>(.*?)</code>", src)
if len(chunks) != len(slugs):
    raise SystemExit(f"partial parse: blocks {len(chunks)}, slugs {len(slugs)} — fix the markup, do not write")
```

Why: requiring a class attribute (`class='pg new'`) misses every block written without one (`class='pg'`), and the
artifact loses half its rows while looking complete. The same guard belongs on any parse of somebody else's
output — a report generator reading a hand-written table, a registry reading a manifest.

## 2. Generations (whose "new" is it)

"New" belongs to a generation, not to an artifact. Keep a register and compute status; never store it:

```json
{"waves": [
  {"id": "2026-09-11", "label": "ingest 2026-09-11", "from": "2026-09-11 00:00", "to": "2026-09-12 00:00"},
  {"id": "2026-09-14", "label": "ingest 2026-09-14", "from": "2026-09-14 18:00", "to": null, "current": true}
]}
```

- element creation: `git log --diff-filter=A --format=%ad --date=format:'%Y-%m-%d %H:%M' --follow -- <file>`
- deletion / rename: `git log --diff-filter=DR --follow --name-status --format=@%h|%ad|%s -- <file>`
- status = the window containing the creation date: current window -> NEW; earlier window and touched now ->
  UPDATED; earlier window and untouched -> its own generation label (dimmed, no frame).
- opening the next generation = add a register entry with `from` and `current: true`; the previous entry gets its
  `to`. Nothing else is edited.
- for elements outside the record, derive status from the creation window only. Never call them "updated" because
  maintenance commits (link canon, table alignment) landed inside an open window — that marks nearly everything
  as touched by the ingest and the view lies.
- render the whole population, not just the touched part: untouched elements go in a dimmed group of their own
  generation, after the current one. A view that shows only what this ingest changed reads as "the rest does not
  exist", and the human asks where the missing elements went instead of reading the change.
- date every older generation you can, or its elements fall into "outside any wave" and the labels stop meaning
  anything.
- deleting an element is a normal operation: the view shows it closed, with the date and reason taken from the
  deleting commit, plus where its material went (look up the source copies' "used in" blocks).

## 3. What was embedded this ingest

For each element touched in the window:

1. base = the file's last commit before the window; head = its last commit inside it.
2. `git diff -U0 base..head -- <file>`; for a file born inside the window treat the whole current content as
   inserted — with no base commit, diffing `head~1..head` counts only the last commit's edits and leaks "not
   embedded" marks onto sources that were in fact embedded.
3. Map added lines to sections by **line number in the new file** from the hunk header (`@@ -a,b +c,d @@`), then
   look up the heading that precedes that line in the current file.
4. A hunk that also removes lines is an edit: count its additions as edits, not as new material.
5. Freshness of a source = its id (or file stem) appears on inserted lines **and** is absent from the base
   version. Per element, never global.

Report the numbers apart: `N new lines · M edits · K removed · J commits` — one "+40 lines" figure that quietly
includes one-word canonical fixes makes the ingest look ten times bigger than it was.

## 4. Provenance pills

- Build the pill list from the element's declared `sources:` field (plus, for a record element, the sources the
  record lists) — not from every number occurring in the text: stray id-shaped numbers become fake sources.
- Source with a message id -> label is the id; source without one (vendor docs, articles, manifests, tool texts)
  -> label is the file stem, tooltip from `source_channel` + `source_date`, `title` as fallback.
- Missing label in the record: fall back to the file stem before showing a bare number.
- Source file gone from the corpus -> mark the pill "source removed from the corpus" (dashed, struck through),
  never as an ordinary source.
- Source declared **only** by register-type elements -> its own mark (dashed + reduced opacity), computed from
  provenance, with the rule stated in the legend.
- Colour marks are per element: "this source was embedded into *this* page in the current ingest". A source that
  exists only in the shared provenance of untouched pages is not new there.
- A dimmed mark must stay readable: restore full opacity on hover. A pill the human cannot read costs more than
  the distinction it draws.

## 5. The dashboard (self-contained HTML, no server)

- One file, dark theme, tabs as buttons toggling views; embed each panel as
  `<iframe srcdoc="{html.escape(document)}">`, so the dashboard reads no sibling files and survives being moved.
  Inner styles and scripts keep working.
- Above the panels: counters computed at build time (elements, sources, cards, open items, findings per rule,
  debt, unpushed commits) — numbers taken from artifacts, never typed in.
- Per panel print a state strip: file, when built, when the source tree was last edited, and a warning when the
  panel references a page that no longer exists (regex its HTML for `kind/slug` paths and check the tree). That
  is the mechanism that catches a stale panel still advertising a deleted page.
- Timestamp "when built" from the file's **mtime**, not from `git log -1 --format=%cd -- <file>`: the git date is
  the last commit, so a panel rebuilt but not yet committed compares as older than the tree and warns about
  staleness that does not exist.
- Keep that guard honest about history: closed rows print bare names, live rows keep the `kind/slug` form. A
  historical row that keeps the path shape makes the guard report the artifact's own past as a defect. Timestamp
  the source tree the same way (newest mtime under the wiki), so the comparison is build-time against edit-time.
- A panel genuinely older than the last edit says so on the strip instead of looking current; a rebuild of a
  gated artifact waits for the owner's word, and the yellow strip is how they see it is waiting.
- Regenerate the panels first and the dashboard last: the repo is the source of truth, the dashboard a rendering
  of it.

## 6. Rendering tables back into markdown

- Emit plain links inside table cells. An alias link with an escaped pipe breaks the row, the link canon rejects
  it, and the table check reports a link-with-pipe plus a broken wikilink at once.
- Keep a bullet that carries a marker (owner link, machine-read note) on one physical line — wrapping it puts the
  marker on a line the checks no longer read.
- Match path case exactly when a generator writes source paths into front matter; a lowercased directory name
  reads as an undeclared source, and the hunt for "undeclared source" ends up being one character.

## 8. Review queues: decisions and reasons

A queue of human decisions (which candidate earns a page, which source is worth weaving in) is a control
artifact too: the view must show the decision, its reason, and what changed since.

- Correct the reason, not the decision. When many rows carry a generic reason and the real class is computable —
  the term is fully covered by an existing element whose name is the term plus a generic qualifier (`method`,
  `methodology`, `architecture`, `framework`, `agent`, `cli`, `code`, `sdk`, `api`, `pattern`) — rewrite only the
  reason field, keep the decision, date the correction, and state that the decisions did not change.
- Add the computed class to the vocabulary the queue offers (the reason list the decision form proposes), so the
  next round picks it instead of relearning it.
- Keep a "reason to ask" counter on the queue view: rows where the computed class disagrees with the stored
  reason get highlighted. Right after the correction it reads zero — that is the point of keeping it.
- Show what changed since the decision: count now, count at decision time, delta. Take a baseline snapshot of the
  counts at decision time (the queue rows usually hold no numbers), state its date on the view, and say that
  deltas are zero until the next ingest. Rows that grew rise to the top as "reconsider".
- Keep the threshold a question, not an order: the machine counts, keeps the queue visible and refuses to forget;
  the human decides, and a refusal is a row in the queue rather than silence.
- State the limit of the computed class plainly: containment plus a generic word catches "harness" against
  "harness-architecture" and misses a synonym carrying an entirely different name — that stays with the human.

## 9. Checks that catch silent loss

- After every generator edit, run the generator: a range-replace between two `def` lines can swallow the
  neighbouring helpers, and it only shows up at run time. Regenerating the panel is the cheapest test there is.
- `git show --numstat` and `git show -- <path>` after committing: a commit message can claim a change the diff
  does not contain, and a "cleanup" can drop a whole section from an unrelated file.
- Compare against an older ref for shrink and for vanished headings before calling a rewrite done; normalise
  headings first, because a heading that only gained an owner link is not a lost section.
- When the project's lint or canary harness already covers the kind of claim the artifact makes, register the
  artifact there instead of building a parallel check — and if the new check finds nothing on current data, say
  so plainly, as insurance rather than treatment.
- Prove a new flag fires with a perturbed input before trusting a zero: copy the baseline to a temp file, lower
  one entry, run the generator, confirm the row rises and is marked, then delete the copy. "The code is written"
  is not evidence, and a baseline taken the same day makes every delta zero by construction.
- When a figure covers only a subset, report it as a floor and name the excluded class. A metric like "material
  embedded into this page" is computed per element; elements outside the record get no diff at all, so "29 of 39
  got nothing" is a lower bound, not a count — say it in the artifact and in the report.
- Assert that a string edit landed. An in-place replace whose anchor does not match returns the text unchanged, so
  the file keeps the old code while the artifact on disk is from the previous run — the change looks done and is
  not. Re-run the generator and count a marker only the new code emits; unchanged count means the edit missed.
- Test an interactive export against its consumer in a temp root: build the payload the view's export produces,
  run the collector with the root pointed at a copy of the queue file, and report accepted/skipped — the real
  queue's row count unchanged. A payload shape that only looks right fails at the moment the human uploads it.
- Verify a pipeline step by running that single command, never the aggregate task: an aggregate chain re-runs
  every stage behind it, including side-effecting ones (a gated rebuild the owner asked to leave alone) and long
  stages that outlive the tool timeout. Two commands take seconds; the whole chain both overstates the scope of
  your check and can take the call down with it — and when it rebuilds something gated, report that it did.
- Build a large generator in several small patches, not one oversized write: a single huge edit payload can time
  out and take the whole call with it. Re-run between patches, and leave the file importable after each one.
- Count the population, not only the decorated part: an element is in the view only because a page declares it in
  its provenance field, so a source nobody declares never appears. Scan the corpus against the declarations once
  (`for f in raw/**: name in declared?`) and report the remainder — a measurement of visibility, not a defect hunt.

## 10. Delegated decisions, and closing items in the record

- When the owner delegates a decision class (transcription/fusion adjudication, contradictions, pipeline-internal
  choices) the record must stop saying "waiting for the owner's acceptance". Do the work in the same turn, then
  write the delegation into the procedure that owns that step — the tool README's step table, the wave procedure —
  with what is delegated, the date, and where the decisions themselves are recorded (the canon file, the decisions
  JSON). A debt row or a session memory note is not the place the next run reads.
- Keep the audit trail of the delegation, not only its effect: the canon of adjudicated decisions (`decisions.json`
  plus its human-readable companion) is the artifact that shows what was decided and why, and it is what a later
  reviewer checks instead of re-litigating the fusion.
- Close an item with a claim and a check: status `closed`, close date, evidence path, and a note naming what
  actually happened. "The material is fully woven" needs a table of item → location as its evidence file; put it
  under the project's evidence directory, not inside the record row.
- Re-count the numbers in the evidence before it becomes proof: a table heading claimed 25 while the list under it
  held 22 names, and only the recount surfaced it. State the arithmetic that closes the set (groups summing to the
  population) so a reader can check it without re-deriving the list.
- A row whose premise is wrong is re-classified, not closed. Read each element's own documentation (script
  header, element metadata) and sort the population into: one-off tools built for a single incident (they keep the
  story of that incident, they need no caller — write the story down as an inventory in the repo), steps of a past
  wave (name the condition that reopens them), utilities for a recurring hand-off pattern (children write files,
  parent merges), and steps genuinely run in practice but missing from the task entry point. Only the last class is
  a defect: wire it into the entry point and check the wiring by running those commands before closing anything.
- Finish the work first, close second. Closing while the claim is still aspirational turns the record into a claim
  about the future; closing the same day the work was done, with the artifact path in hand, is what makes the row
  history instead of intent.
