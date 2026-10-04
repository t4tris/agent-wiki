---
name: control-artifacts
description: "Use when a generated map or dashboard must stay honest."
version: 1.0.0
license: MIT
platforms: [linux, macos, windows]
---

# Control artifacts

For any artifact a person reviews by eye instead of by running it: ingest/wave maps, project-structure maps,
review lists with checkboxes, control dashboards assembled from a wiki or repo. Quality gates for the underlying
knowledge base belong to `knowledge-base-quality-gates`; this skill is about the view itself.

## Rules that apply to every one of them

- A reviewed view is a *generated* artifact, not a document. Keep the facts as a data record in the repo
  (`*.json` / `*.tsv`) beside a generator, and render from the record **plus live repo state**. A hand-written
  HTML file kept as the only copy of a decision drifts silently and keeps showing pages that were deleted.
- A generator never copies what it can compute — status, provenance, counts, dates. Copy only claims about the
  past (what an ingest said), and print a warning when the computed value disagrees with the recorded one; the
  recorded claim stays as history, the computed one is what the reader sees.
- Guard every parse of another artifact's markup with a count check (blocks parsed == markers in the source) and
  refuse to write on mismatch. A regex that requires an optional attribute silently drops half the rows.
- Attribute change to the right owner: an inserted line belongs to the section it sits in **in the current file**
  (line numbers from diff hunk headers), not to the last added heading — headings of existing sections never
  appear as added lines, so their insertions slide into a nameless bucket and disappear from the report.
- Separate insertion from replacement: a hunk that also removes lines is an edit, and a replaced line must not
  inflate the count of what was newly added.
- Show the full data once it is available: a "… N more" marker is a defect, not a courtesy. Long lists stay long;
  if that hurts, collapse them behind a control, do not truncate. If truncation is genuinely all that exists
  (the source page is gone), say that in the marker instead of repeating a promise you cannot keep.
- Heavy panels start collapsed. A dashboard whose long panels (review lists, big maps) open by default buries the
  counters and pushes the next panel off-screen. Wrap each heavy panel in `<details>` without `open`, keep its
  state strip and a one-line summary of what is inside **outside** the fold, and leave the short panels open.
- Give every element one honest status — created in this generation / updated by it / older generation / closed /
  source gone — and code it visually, with the legend written into the artifact itself. Reuse one vocabulary:
  frame = generation, border = embedded now, dashed + reduced opacity = declared only by register pages.
- One screen, one purpose: never render the same dataset twice in one view (a summary table above a form listing
  the same rows), and separate sections answering different questions by spacing or a divider.
- If the human decides inside the view, the view is the input: a checkbox per row, a decision selector with its
  reason, a filter, and an export whose payload is exactly what the queue's collector accepts (`kind`, contract
  version, `term` / `decision` / `slug` or `reason` / `date`). Verify the export by running that collector against
  a **throwaway root** (a copy of the queue file) and reporting accepted/skipped; count the real queue's rows
  before and after. Merging a returned batch reports three numbers — accepted, skipped, divergences — and the
  divergence list is the deliverable, not noise: a returned row that contradicts the ledger gets one canonical
  formulation kept (state which won and why, in the tool's own output), never a silent overwrite of an earlier
  decision. A returned file is also not automatically newer than the ledger it lands in: a batch exported earlier
  can arrive later, so decide by the ledger's own rules and publish the collisions. Marks kept in browser `localStorage` may not survive `file://` — say so, do not promise
  persistence.
- Match the artifact to the last version the human approved: take its palette, badges and emphasis instead of
  inventing your own. Losing a colour that carried meaning reads as a regression even when the data improved.
- Print accounting-derived numbers as floors, not totals: when a figure is computed only for a subset (the
  recorder skips elements outside this generation's record, no diff is built for older elements), write and say
  "at least N — not computed for X". A floor read as a total converts your own measurement gap into a claim
  about the data, and the owner starts planning work against a corpus that is not empty.
- Prove a new guard fires before trusting its silence: run it against a deliberately perturbed copy of its input
  (lower one baseline value in a temp file, keep the real one untouched, delete the copy afterwards). Zero
  findings on fresh data proves nothing — it is indistinguishable from a guard that cannot fire.
- Attest a new guard from both sides before shipping it: one mutation that creates the defect it watches for (it
  must fire) and one that rewrites the same file with identical content (it must stay silent). The benign half is
  what catches an over-broad guard — when a validation run lights up across unrelated cases, the guard is wrong,
  not the fixture. Judge "changed" over **content** (a hash of the file), never over modification time: any
  rewrite, including a no-op re-save, bumps the timestamp, so a freshness guard keyed to mtime reports drift for
  work that changed nothing. Keep rebuild-and-recheck as one entry point that also records the inputs' hashes, so
the two halves cannot drift apart in the procedure. Protocol: `knowledge-base-quality-gates` (attestation: one firing mutation plus one silent benign control per guard).
- Run the artifact the way its receiver gets it, not the way you have it. A delivered tree is verified by unpacking
  the shipped artifact (the packed archive / `git archive HEAD`) into an empty directory and exercising it there: the
  working copy carries `.git`, ignored folders and local files the receiver does not have, and a tool that falls back
  or crashes only in that shape reads as working until somebody unpacks it. A path-existence test that does not
  normalise a trailing slash, or a state function that returns a partial record when repository history is missing,
  is exactly this class. Verify the fix in the same shape too: a repair proven in a clone proves nothing about the archive.
- A file stays in an assembled delivery because something outside its own generation names it — code, a register, a
  delivery document, or a closed item's evidence. When sweeping orphans, match the name *and* the templates a live
  consumer builds it from (`x-*.json`, `x-%s.json`, `x-{date}.json`): a literal-name sweep moves a guard's own input
  into the archive and the guard goes blind. Say which way each surviving file is held, in the rule and in the check.
- Never record the state of files outside your delivery in your own artifacts — hashes, sizes or counts of a
  neighbouring folder, an externally edited skill set, somebody else's working directory. Their editor's every change
  then becomes a finding in your tree and a commit you did not cause; name the dependency in prose instead of tracking
  its state.
- A state number in prose rots; a contract number does not. Counts of lines, files, rows, commits and any figure that
  changes without you touching it belong in the generated artifact (which recomputes it) or nowhere. A number is
  allowed in prose only when a check reads it back: a threshold with a guard, a closed vocabulary, a hash that names a
  version.
- Mutation-harness arithmetic is part of the measurement. A mutation that does not apply (target moved, format drifted)
  is not applicable — not caught, and not silently dropped: print caught / applicable / not-applicable / false
  positives as separate numbers over one run, or two readers derive two totals from the same log. Never add a noisy
  section to the harness's tolerated list to lift specificity: the benign-canary runs are the only measurement of a
  guard's *silence*, so fix the base instead — rebuild the derived artifacts inside the copy *before* the checkers run,
  and every firing is then attributable to the mutation. Depth: `references/guard-test-harness.md`.
- Correct the reason, not the decision: when a review queue's reasons are generic and the true class is
  computable (an element is covered by an existing one's name plus a generic qualifier word), rewrite only the
  reason field, keep the decision, date the correction, and say that the decisions themselves did not change.
- A row whose *premise* is wrong gets the premise rewritten, never the row dropped. Re-classify the population by
  reading what each element says about itself (a script's own header, an element's own metadata), not by recall;
  keep the classification in the repo as an inventory; then act on the real defect and say plainly that the rest
  is not one. A row like "N of M elements have no caller" is a statement about a class: the defect is a step run
  in practice but absent from the task entry point, and a one-off tool built for a single incident is not a defect
  at all — it is history, and asking it for a caller is asking the wrong question.
- Say whether a proposed mechanism catches a defect on current data before building it. A guard, field or check
  that would find nothing today is insurance, not treatment — say it in those words and expect the owner to
  decline; if they decline, record the refusal with their wording so the next audit does not re-raise it, and
  confirm nothing in the tooling already special-cases that class before promising to "ignore" it.
- Historical rows must not look like live references: print bare names for closed and older elements and reserve
  path-shaped references (`kind/slug`) for live ones, or the deleted-reference guard reports your own history as
  a defect and its count stops meaning "something is stale".
- A viewer the owner treats as the product's face is not build waste: leave it where its generator writes it (moving it breaks the generator and the freshness entry), give it an entry in the structure map stating its purpose in the owner's own terms, and keep it under the same freshness guard as the other panels. An unexplained HTML file sitting next to sources reads as leftover and is the first thing a cleanup pass deletes.
- A viewer whose columns are measured against live corpus pages is stale the moment those pages change, so it belongs in the freshness guard like any other panel; a viewer of a closed pass whose inputs no longer move does not — putting it there would catch nothing, which is insurance, not treatment. Never keep a dated twin beside a rebuildable viewer: the generator overwrites it in place and the earlier states live in git history, so any rule or protocol line that demands a dated copy must be rewritten in the same turn.
- State a guard's coverage from the guard's own table, never from how many artifacts you described. Read the pair list
  (which artifact is watched, and by which section) before writing «N of these stand under the freshness guard»: a
  guard commonly watches fewer items than exist — a map's own freshness may live with the map's check while the panel
  guard knows the map only as an input — and a stated count one too high is read as a promise about the rest.
- A map's staleness fingerprint must exclude everything its own rebuild writes — the map itself, the bridge, the
  panels, and the whole pass-output layer. Any file written *after* the fingerprint is taken lands inside it, so on a
  fresh checkout the map is "stale" the moment it is built and the word stops meaning anything; passing it stops seeing
  real drift. Verify by behaviour, not by reading the exclusion list: rebuild, check (clean), then drop a scratch file
  into the excluded layer and check again — the map must still be clean. Layout rows for the instance's own paths do not
  belong in the mechanism's table at all: they live in a file the instance owns and the delivery does not carry, which
  the map reads beside its own table.
- A structure map's «intent without a file» is a statement about the delivery, not about the working tree. An intent
  for an empty-by-purpose work directory fires as soon as the directory stops shipping (git carries no empty
  directories), and the repair is a README inside that directory — so it travels and explains itself — or teaching the
  map that not-delivered paths are not moved paths. Deleting the intent loses the explanation; relaxing the check for
  every directory loses the guard.
- The intent declaration is a consumer like any other when a path **moves**: the map is regenerated *from* it, so the
  same line comes back after every rebuild until that instance-owned file is corrected. Regenerate, see it again, and go
  fix the declaration — re-running the generator, or relaxing the check, is chasing your own footprint. The same move
  leaves dead exclusions behind: an entry in a checker's or a probe's skip-list for a path that no longer exists is a
  rule kept alive by nothing (the parent layer's exclusion already covers the new location), so delete it in the same
  turn instead of letting the list grow into an inventory of departed folders.
- Regenerate panels first and the aggregate view last, and verify without a browser before reporting: parse the
  HTML (`html.parser`) for tabs/panels and check each embedded document is complete, `node --check` the inline
  script, count rows/pills/tables by regex — then state those counts. Report what was machine-checked, not that
  "it looks fine".
- Order the closing writes by what depends on what: write the **state** the panels are computed from (the wave or
  generation record, registry entries, dates) before re-running the panel generators, and write the report whose
  numbers were computed after all of it. A record written *after* the panels leaves them stale by the closing
  sequence's own last write, and the freshness guard then fires on your own footprint — re-run the generators
  rather than reporting the staleness as a defect of the corpus.
- Show the declared population, and measure the invisible rest: an element appears in the view only because some
  page declares it in provenance, so anything nobody declares is invisible by construction. Scan the source corpus
  against the declarations once and report what is orphaned (usually near zero, with a stray non-source file
  explaining the tail) — "no orphans" is then a measurement instead of an assumption.
- A waiting state the owner has delegated is a defect. When they hand a decision class to the agent and ask for it
  to be written down for next time, act in that same turn, strike the waiting clause out of the record, and put
  the delegation into the repo procedure that owns that step — neither the record row nor session memory is where
  the next run looks.
- Closing an item in the record takes evidence, not a summary: status, close date, evidence path, and — when the
  claim is "the material is fully woven" — an item → location table as the evidence file. Re-count the numbers you
  cite in that table against its own list before committing it as proof.

- A view built for the owner to compare experiment results by eye carries the findings themselves, not their
  headlines: per item the claim of each side with its quotation, what changes in practice, the condition under which
  each side wins, and whether the item came from the mechanical net or from a semantic pass. Name the exact
  artifact each row was produced by (the batch or run file) in its own column, and when two product sets sit in
  one view answer the containment question the reader will ask — is one set part of the other — with a measured
  intersection, not with a judgement: a reader who cannot tell whether six items complement nineteen findings or
  are a subset of them stops trusting the whole table. Then put the methods side by side — counts per outcome for each pass, and the items where the two disagree — because the disagreement is the
  finding and the totals are context. A table of topics makes the reader trust your summary instead of the evidence
  they asked to see.
- Check that every section a report sends the reader to actually exists before it ships: grep the target page for
  the heading, and quote the section that really holds the material. A verdict pointing at the wrong place (a
  generic "disputed" list when the write-up lives in a different section) sends the reader hunting for text that is
  not there and makes the stated reason read as invented.
- Mark your own judgement in the view as judgement. Candidate rows, "needs a decision" marks and editorial readings
  are not measurements: give them a distinct label and state in the footer which parts are measured and which are
  your call. Rendered the same way as facts, they are indistinguishable, and the owner ends up approving as measured
  something you merely read. When the row's *status* itself comes from a reader's verdict, go further: rebuild the status
  as a measurement and demote the verdict to a labelled line beside it — a batch of typed verdicts had most of its harsh
  ones contradicted by the corpus, and the view was approved-on-a-lie until the status stopped being a word.
- Never report a render you have not seen. Verify structure programmatically (balanced tags, no unsubstituted
  placeholders, no external scripts or links), and say plainly that the visual render was not checked whenever no
  browser is available — the owner then knows which defects they are the first to see.

- Give a generated report a **preserved tail**. When the human annotates its output by hand — fates of the
  findings, a list handed back to them — emit a marker line and re-emit everything below it on rebuild, so a
  regeneration keeps their analysis instead of silently deleting it. Verify by rebuilding once and re-reading
  that section, not by trusting the code path.
- A periodic pass that derives its scope from "changed since the previous pass" goes blind when two passes land on
  the same day: the changed list is empty and the report claims "0 pages". Fall back to the coverage the readers
  actually declared, and name in the report which basis was used.
- Feed the queue, do not just report. If a finding class has a queue the owner decides in, the pass itself pushes
  the item there with exactly the field the collector expects, and the view marks which rows arrived by that path
  — entries that no scanner can see (a term living only in prose) look like ordinary decisions until they are
  labelled. A finding that exists only inside a report is already lost.
- **Never put a free-text field in the view for a value the view can compute.** A slug or identifier field in a
  decision list moves naming into the human's hands: rows arrive with names in another script, and stripping the
  non-Latin characters out of them yields an empty string, not a transliteration — so the list shows either an empty
  identifier or the Cyrillic name where a page name belongs. Derive the identifier in code from the row's own data
  (prefer the Latin term the item carries in parentheses, fall back to a transliteration table) and render it
  read-only; the human decides the judgement, never the machine part.
- **Prove a claimed rule is in the repository by reading the committed revision and running it, not by the commit
  message.** Extract the committed file (`git show HEAD:<path>`), run that copy live against a throwaway copy of the
  tree, and list artifacts with `git ls-files` when the claim is "this now exists" — an edit still only on disk, or a
  commit that touched a different file, both read as done in a summary and are not.
- **Derive a gate's condition from the source data, not from the presence of an intermediate artifact.** A check written as
  "read the list file if it exists" passes exactly when the list was never written — and the writing branch may itself be
  broken (a missing stdlib import kept it failing silently for a day). Iterate the records or batches, collect what needs a
  decision, then require each item to appear in the artifact *with* a decision and a reason.
- **A checker must not invoke another checker that invokes it.** Counting sections by running the linter from inside a check
  the linter calls hung the run until timeout; count from the checker's own source text and keep every gate bounded.
- **A count stated in the artifact's own prose is computed from the rendered rows, and a machine compares the stated number
  with the recomputed one.** Hand-typed totals survive the data shrinking and keep lying in the headline and the statistics
  strip; recompute over what is rendered, not over the raw input list.
- **A column filled from data is guarded against invention.** A value derived for a row (a web address, a link) must appear in
  the record that row cites, or be constructible from it (`owner/repo` → the repo URL); where the record has nothing, render
  an explicit empty marker. An invented value in a reference table is worse than a gap — every reader downstream trusts it.
- **A quality signal that fires on generated output is a finding about the generator.** When a check for style, chronicle
  phrasing or duplication lights up in a map, bridge or panel, the text is written once in the template and multiplies
  over every future rebuild: patch the template, or exclude generated artifacts from that check when the phrasing is
  inherent to what the artifact is — and say which you chose. Hand-editing the rebuilt file fixes nothing; the next
  rebuild restores the old text and the finding returns.

Depth on request: `references/control-artifacts.md` — record/generator/guard splitting, generation registers and
status computation, "what was embedded in this ingest" from diffs, provenance pills, srcdoc dashboards, the
staleness guard, interactive review lists, markdown-table rendering rules, and the checks that catch silent loss.
