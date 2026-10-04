---
name: subagent-batch-extraction
description: "Use when fanning out bulk work to many parallel subagents. Extraction, classification, or weaving new material into existing documents, all under one written spec."
version: 1.0.0
license: MIT
platforms: [linux, macos, windows]
---

# Batch work by parallel subagents, under a schema contract

Use when N items (tens to hundreds) each need the same structured treatment — extraction of concepts,
classification, per-item verdicts, per-source cards, or merging a cluster of new items into an existing
document — and reading all of it in the parent context is the wrong trade. The parent keeps the schema and the
merge; children keep the raw text.

Shape: **build batch inputs → write one SPEC file → dispatch short tasks → children validate themselves →
parent verifies coverage and spot-checks → parent merges.** For document-weaving batches the shape is the same,
except each child receives the target document's current full text plus only its own items' material, and returns
the full new text as a file plus a companion report.

## Procedure

1. **Split the input into batch files, one per child.** ~13 items per batch for 10 parallel children.
   Keep the grouping coherent (chronological chunks keep a conversation together; per-source grouping keeps
   a topic together), and balance by weight rather than by item count when items differ in size by an order of
   magnitude — a document with twenty sources and one with two are not equal work, and the slowest child sets the
   wall clock. Each batch file carries, per item, the real identifier plus everything the child needs:
   text, links, media description, and any human annotation — children read no other project file. For a
   *restatement* task — rewrite a cell or a paragraph from the note it came from — the opposite holds: the existing
   compressed text is not the source, and a child that sees only it returns the same compression in longer words.
   Carry the resolved paths of the sources the item cites in the batch record, instruct the child to read them, and
   make the batch builder fail when a citation resolves to no file (an unresolved key means the item cites something
   the corpus does not have). Derive each item's
   associations from the current document (the sources it declares today), not from an earlier record of the phase: a
   stale association sends a child comparing a pair that no longer exists, and its correct "this pair is stale" note is
   your input defect, not its sloppiness.
2. **Write the SPEC as a file, not into each task.** One `SPEC.md` in the working directory holding: the input
   field table, the exact output envelope, the item field list with types, a filled example, the allowed value
   lists (taxonomies, target slugs), what is forbidden, and the validator command. Ten copies of the same
   instructions drift; a file does not. The file also carries the two lists that hold a large wave together: the files
   children must not touch (aggregate indexes, logs, panels, backlinks, counters — the parent closes those) and the
   parent's own closing checklist, so the order survives the session that wrote it.
3. **Dispatch short tasks that point at the spec:** "read SPEC.md fully, read batch-NN.json, write
   report-NN.json, run the validator until it prints ok, reply with path + count + one line only."
4. **Children write their report to a FILE and return a short confirmation**, not the JSON in chat.
   The parent merges from disk: the tool result stays small, merging is mechanical, and a re-run overwrites
   one file instead of re-typing. Ask for the file even in a one-shot survey: the live transcript of a child elides
   long payloads, so the result message is the only complete copy, and hand-copying it into files is where a
   one-character path typo enters the artifact — a typo the parent's own key-existence check should then catch.
5. **Parent validates before using anything:** run the schema validator over every report, then check coverage
   as a set equality of identifiers (missing and extra both listed), then spot-check 2–3 claims per child
   against the input (verbatim quotes and numbers by substring search, not by eye).
6. **Parent writes the target artifacts.** Children never write into the destination tree (repo, wiki, `raw/`),
   never edit the batch input, and never fix each other's reports. The one exception is a *revision* wave, where the
   mandate is to edit the documents themselves and the review model is "uncommitted changes until the owner approves,
   a named commit as the rollback point": then children edit the destination directly, the parent keeps the tree
   uncommitted, acceptance is the owner's read of the diff, and only tooling is committed until the verdict.

## Always-on rules

- **The brief must name every required field.** A schema that lives only in the child's imagination produces
  one schema per child: the array is called `items` by some, `records`/`data` by others, the per-item data
  ends up inside a single summary object, and required envelope fields go missing. Content quality does not
  save this — the parent then transcribes by hand, which is the exact failure the contract exists to prevent.
- **Copy the envelope's key names out of the project's own validator, not from memory.** Where the project keeps a
  contract file and a validator, the brief you write inherits whichever key *you* remembered: instruct children to
  return `records` while the validator demands `items` and every report fails on the envelope alone, after the work is
  done. Open the validator first and copy the array key, the required fields and the enums into the brief verbatim — and
  run that validator over the artifact you merge as well, not only over the children's files. When the wave introduces
  a *new* report kind, add it to the validator **before** dispatch: a kind the validator has never heard of fails
  perfectly valid reports, and every child spends its budget reporting the same parent defect instead of finishing.
- **State the repairs that are always allowed, or disciplined children refuse them.** A rule such as "do not
  rewrite existing text" protects the document and freezes its existing defects at the same time: a wikilink written
  with a file extension never resolves, and a child that reads the rule literally hands the broken link back and only
  names it in its report. List the mechanical repairs the spec permits (link normalisation, whitespace, stray
  wrappers, placeholders) as repairs rather than rewrites, so the parent does not re-fix the same defect batch after
  batch.
- **For fact-bearing rewrites, the child must be allowed to correct what the sources contradict — and must report every
  correction.** A rewrite "from the primary sources" is also a fact-check, and the compressed cell is where the errors
  live: a quote attributed to the wrong author, a section number that moved, a version the source does not state, a number
  no cited source carries, a threshold credited to the wrong speaker. Say so in the brief: correct the attribution in the
  text, drop a claim no cited source supports, keep every citation key, and list each fix in `failures[]` with the source
  that disproves the old text. Without that permission a disciplined child treats the cell as untouchable and hands back
  the same errors in longer sentences. Then reconcile `failures[]` item by item in the parent and record the decision for
  each (fixed in text / source added to the header / claim dropped) in a file, not in the chat: an unread `retry_by_parent`
  flag is silent debt, and the "dropped for lack of support" list is exactly what the owner needs to see.
- **Require the validator run before the child answers.** "Run `<validator> <your file>` and iterate until it
  prints ok" is the cheapest quality gate in the whole pipeline. Without it, returns arrive in a batch, late,
  when the next stage has already started.
- **Give an example built from a real input item.** Children copy the example literally, including its subject
  matter: an illustrative example from another corpus pulls that corpus's vocabulary into the reports (children
  even flag it). Label it "form only, not the topic".
- **Identifiers come from the input**, never from row numbers — row numbering breaks on merge and re-run.
- **Enumerate the subject list yourself; a handed list undercounts.** When the owner or another session hands you the set
  of files or items to fan out over, derive the set from the filesystem (`ls`, `git ls-files`) before dispatch and report
  the delta: the files nobody listed — one added in the current session, a README in a neighbouring folder, the audit
  layer's own README — are exactly the ones nobody has re-read. Auditing only the handed list silently endorses its gaps.
- **Do not edit a file a running child is reading.** A child's findings cite line numbers and quoted fragments; the
  parent's edit between dispatch and return moves them, and true findings come back unreproducible. Collect the batch
  first, then repair — the same rule that freezes the acceptance harness for the duration of a wave.
- **Owner-gated structural change: the plan goes into the repo as a numbered list, then one stage at a time.**
  Rewriting a pipeline's steps on the strength of a chat proposal alone loses the reasoning the moment the session is
  compacted. Write the plan as a file, wait for the owner's go-ahead, and implement in the order protocols → scripts →
  one measured run on current data → guards and canaries → attestation, so each stage is verifiable before the next
  depends on it.
- **In a revision wave the written standard is the spec, and the owner's verdict repairs the spec, not the child.**
  Expect a standard to be read more strictly than it was written: a ban on "writing about how the document was
  assembled" gets applied to in-page cross-references, and "a repeated fact does not belong here" gets applied to the
  only place the fact lived. Keep the children's edits uncommitted against a named rollback commit so a rejected edit
  costs one restore; restore the text verbatim and rewrite the rule with the owner's own example inside it; after two
  or three rejections add a short *what is allowed* block, because bans alone leave the boundary to the child's
    imagination. Before the owner reads the diff, run your own two sweeps over the children's changes — deletions for a figure plus
 its owner, additions for fresh talk about the document or the corpus (children replace one meta-remark with
 another) — so the review carries judgement calls, not mechanical misses. Then render the review sheet from that filter rather
 than from the raw diff: a full diff over tens of documents is not read by anyone, so the artifact the owner gets shows
 only the hunks the parent cannot vouch for, each with a one-line reason (a removed line that carried a link and a
 number, a block removed whole, a removed quote or address, a removed section heading, fresh talk about the document or
 the corpus, a header field changed beyond the date), and the pages whose edits are mechanical are named as counted and
 absent from it. State the rollback commit with the sheet, since the tree stays uncommitted until his verdict.
 State the tie-breaker the ban lacks, or children delete by the letter of the rule: a figure with an owner stays in the collected-facts section **even when the body states it** (duplication is the same fact twice inside one section), and "when in doubt, keep" belongs in the standard in those words. Keep the standard itself free of chronicle — no dates, no quotes of earlier conversations, no "this file was created from…": it is handed to clean-context children as the task, and history in it invites them to write about the document's past instead of its subject. The modified-date field children set comes from the clock at dispatch time, never from the brief — a date typed from the session's own sense of the day stamps every document wrongly, and the commit history contradicts it.
Budget one rejection per ban, and re-read the targeted sections yourself once the owner accepts: a child
    that trims the tail of a filler sentence while keeping the sentence was following the rule mechanically, and its report
    still reads "done". Failure taxonomy, repair of the standard, and the parent's closing tail:
    `references/revision-wave-review.md`.
- **Ask every child for the list of places where it was unsure and changed nothing** — that list is the map of the
  spec's ambiguities, and it is worth more than the changes.
- **Empty means an empty array or an explicit "нет"**, never `null` and never a string standing in for a list.
- **Bound the child's prose** (e.g. "≤400 words in the finished note") and state it in the spec; an unbounded
  child returns an essay that no stage consumes.
- **Cost the fan-out before proposing it, and give the owner a budget rather than a round number.** Children are the
  expensive part of the work, not the tools: a child re-sends its growing context on every call, so its price is the
  sum of `in=` and `out=` over its own `API call #N` lines in `logs/agent.log` (group by session id), and that sum is
  stable enough to multiply. Take the median per child from the last wave the project ran, quote the total for the
  proposed count, and let the decision be made against a number — a hundred children is the price of a hundred, and a
  hundred copies of one brief measure variance, not truth: three briefs over the surface that actually changed beat
  them for a fraction of the cost.
- **A brief of the "read it and tell me what is unclear" kind has no floor — require a named defect class and a pass
  criterion, and let the round close on a FACT.** Such a child can always find something to sharpen, so its findings
  extend the work indefinitely and the next round finds roughness inside the previous round's polish. A child that goes
  and DOES the task in a fresh copy finds the defects that matter: a command that no longer runs, a step that exists
  only in the working tree, an instruction its own documentation contradicts. Once a child returns no factual defect,
  that brief's round is closed; re-run it only after the surface it reads has changed, and never to confirm a state you
  did not alter. After a child's work is PORTED, the checks that are due are the project's OWN machine gates over the
  touched surface — its lint, its acceptance run, the canaries of the sections it changed — not another fan-out: children
  answer a question the gates already answer, at the price of a child each. **Pin the configuration the brief tests and name the artifact exactly.** A child told to "follow the
  entry document in a fresh copy" will execute the steps that document recommends — initialise a repository, make the
  first commit, install hooks — and thereby leave the configuration under test without ever testing it, while its report
  reads "gone through end to end". Name the forbidden step in the brief, hand over the artifact as the real reader gets
  it (the packed delivery / `git archive HEAD`, not the working copy), and have the child quote verbatim the instruction
  it read and judge whether that instruction helps or harms the reader it describes. Forbid it the parent's working tree
  as well: a child allowed to look at the live repository "finds" facts the artifact's own reader cannot reach, and the
  the round silently measures nothing.
  - **Verify the repair in the configuration the defect was found in, and treat your own negatives as claims.** A defect
    a child finds in the packed delivery is not repaired by a run in a clone of the working tree: rebuild the same shape
    of copy the child used (unpacked archive, no `.git`, no ignored folders) and re-run the failing step before reporting
    the fix — a repair proven in the wrong shape is the same «works on my machine» the round exists to catch. The same
    applies to negatives you state yourself: «the corpus does not carry this», «the document never mentions it» — each
    ships with the command that produced it and that output, because a literal search for the wrong surface form returns
    empty honestly and turns a missing match into a missing fact (the page writes a figure as `1.5–2.5×`, the source says
    «в 1.5–2.5 раза»). Never read a verdict off a truncated single-line print: open the line, and quote the command with
    the answer.
- **A test round should DO work with an acceptance trail, not a cosmetic edit.** Have the child close one real open item
  from the project's own debt or backlog — with the proof that project demands, the gate of the touched section run, and
  the wave closed — and the round pays for itself even when it finds no structural defect, because it exercises the whole
  circle the brief claims to cover. A synthetic edit teaches nothing about the content rules; its round ends with nothing
  but its own observations. Report the verdict as "the circle works", distinct from "the work is done in the project":
  work performed in a child's copy lands only when the parent ports it, and porting a judgement a child made about real
  material needs the owner's review of that judgement, not a squash of its commit.
- **Failures are content.** A child that could not read something says so in a `failures[]` entry with the
  reason and a retry flag; silence about a partial batch is worse than a gap.
- **Language of the output goes in every task context.** Children behind a fan-out do not inherit the parent's
  language convention; say "answer in <language> only".

## Interruption, steering and resume

A fan-out is a set of independent writers, so it can be stopped and resumed without redoing the work — but only
if you know what each child had already written.

- **Before re-dispatching after a stop, list the output directory.** A child reported as interrupted may have
  written its file seconds before the stop. Verify those files with the same acceptance check and apply the good
  ones; re-dispatch only the children with no file on disk. Treating every interrupted child as lost re-does work
  that is already finished — often a third of the fan-out.
- **Late-found material goes to the running child as `steer`, not to a second task.** When a newly found item
  belongs to a target another child is already writing, two tasks writing one output file means last-write-wins
  and one version silently dropped. Send the item inline to the child that owns the file: identifier, provenance,
  and the stance value its report must record.
- **Re-read your own brief before dispatch, and steer the moment you find it broken.** An unsubstituted
  placeholder (`{{POOL_JSON}}`) or an identifier re-typed by hand with one character lost (a file stem ending
  `-dokumentac-` where the file is `-dokumentaci-`) reaches the child as the truth: it reports on paths that do
  not exist and every count it derives is off by whatever drifted. Once a child is running, `steer` is the
  repair — the corrected payload reaches it without a re-dispatch. Afterwards read the child's data-quality
  notes: a child naming a wrong flag or a truncated name in your pool is reporting your input defect, not its
  own sloppiness, and the same note is the signal that its other lookups are reliable.
- **A judgement pass is a proposal, not a change: run it read-only and surface the child's uncertainty.** For
  semantic classification (which item is a voice of the topic, which is material to read later) give the child
  read-only scope plus one report file, and let the owner approve the table before anything lands. Ask
  explicitly for the decisions whose grounds are thin and for the defects its reading turned up — those two
  lists are what the owner actually decides on, and verdicts alone bury them.
- **Snapshot the plan into the project before a context reset, not into the conversation.** Write a status file in
  the repository: one row per task with its state (applied / file ready / not started), where the working inputs
  live, the exact acceptance command, the order apply → lint → commit, and which tasks to re-dispatch. A compacted
  session keeps files but loses the plan; a resume file that names tasks and commands turns the second half of a
  wave into a continuation instead of a rebuild.
- **Regenerate any structure map the project keeps in the same commit as the new document.** A map that asserts
  "every node has a purpose" fails its own freshness check the moment a document appears without an intent line;
  the intent text is the one part a generator cannot infer from the filesystem.
- **Widen the acceptance harness before dispatch, not after the returns land.** The parent's checker carries two
  assumptions that break on a wave larger or different from the last one: the task range (hard-coded to the first
  batch of tasks, so the rest of the wave is reported as "no result — skipped") and the shape of the expected output
  (a checker written for "weave into an existing document" raises on a document that has no before-text, because it
  reads that text to list the headings that must survive). Give it a second path for creating documents — header
  fields present, links resolving, sources declared and given a stance — and prove both paths by running the harness
  once before the fan-out. A harness that crashes does not just miss defects: it blocks the apply step for every
  clean result the children produced, which is the whole wave.

- **When the owner asks to be pinged on completion, the parent sends it — a CLI session cannot.** A fan-out that runs
  while the owner steps away ends with him asking to be notified where he actually reads. In a CLI session nothing is
  delivered back to a chat, and a cron job scheduled from there is local-only (its output is saved, not sent), so promise
  a ping only through a real channel: write the body to a file and send it with `configured send command --to <platform> -f <file>`
  (resolve the target first with `configured send command --list <platform>`), which reuses the gateway credentials with no agent loop.
  Body in a file, not as a shell argument: long Russian text with quotes and newlines mangles in a command line.

## Failure handling and the retry log

When a whole fan-out comes back off-contract, look first at the brief, not at the children: a missing field
list, a missing envelope shape, or no validator step is a **parent defect**. Record it as such in whatever
retry log the project keeps, keep the rejected run aside as evidence of why the retry happened, fix the spec,
and re-dispatch. Content-level violations (value outside an enum, missing required key, duplicate id) are not
repaired by the parent — they go back for a re-run, or the parent's "fix" becomes fudging.

Repairing only the envelope (adding `contract_version`/`kind` around otherwise correct items) is legitimate and
worth logging; repairing content is not.

## Pitfalls

- **A clean result from a checker that never ran looks exactly like a clean result.** A checker that dies on import
  prints no sections, and a parent-side filter over its empty output reads as "nothing found". Before believing a clean
  gate — mid-wave or in your own summary to the owner — run it once and confirm it printed its own total; a harness
  that complains the baseline is empty is right to distrust the silence.
- **A count where the array belongs.** Children asked to "return 13 items" sometimes return `"items": 13`
  with the real data under an invented key. Validate the *type* of every field, not just its presence.
- **A survey asked to find something needs an anti-fabrication bar and explicit permission to find nothing.** A child
told to look for divergences, gaps or conflicts will produce some — diligence looks like a finding. Require every
positive verdict to carry the named parts that make it real (for a disagreement: both positions with their carriers,
what changes in practice, and when each side wins), let the child downgrade a claim that is missing any part, and say
in the brief that an empty list is the expected outcome. Then hold the same bar in the parent: a detector that
manufactures rows costs as much trust as one that misses them, so the acceptance check over the merged artifact should
reject a positive row that lacks one of its parts.
- **Separate the verdict from the finding: require coverage, never require rows.** A checker that demands "a finding
  per item" bribes children into inventing them. Make the mandatory artifact a *verdict per pair* (supports / partial /
  contradicts / not-relevant) and let "nothing contradicts" be a green result, while the positive row keeps the barrier
  above. The owner's test for a real divergence is practical: would two people in the same project act differently?
- **Pairs built from the host document alone are a blind spot.** When material is woven into one document, the tension
  it carries is often with a *different* document — a note saying "this layer is redundant" belongs to the register it
  was woven into and contradicts the page about the technology itself. Derive the pair set from the whole target set
  (each item's structural terms against the *thesis-bearing* places of every document: headings, "disputed" and
  "facts" sections — plain prose mentions give hundreds of noise pairs), then ask a verdict per pair. A host-only pass
  reports zero confusions while the cross axis was never asked.
- **Measure the corpus against the context window before choosing the method.** A per-pair checklist is coverage, not
  cognition; if the whole target digest plus a share of the material fits one context (roughly chars/4 tokens for
  Russian), run a *holistic* pass as well: hand the child every document's theses and let it notice tensions by
  reading, with no pair list at all, and compare what it reports with the pair results. When the semantic pass finds a
tension outside the pairs, the pair criterion is too narrow — that is data, and the fix goes into the criterion.
  Two independent methods agreeing is the strongest result this fan-out can produce.
- **Child self-reports are not evidence.** "13 ids present, validator ok" is a claim; the parent re-derives the
  id set from disk and greps one or two quotes against the input before trusting the batch. Verify each claim **where the claim lives**, not in the file as a whole: a child reporting "row X is in the register" was right that the token occurs — inside the file's frontmatter `sources:` list, while the table has no such row, and a whole-file grep agrees with the false claim. Search the table rows, the section, the field the claim is about. Parse every produced file yourself before merging: a child's summary can read as success
  while its write was refused — a hand-written JSON document failing syntax validation is the common case, so tell
  children to serialise output through a script (`json.dump`) instead of typing the document, and treat the file on
  disk as the only claim that counts. For claims about the
  outside world — links the child says it fetched, versions and statuses it says it checked — probe it yourself: a
  plausible URL is the easiest thing to invent, and a status code costs one request.
- **An input field that arrives shifted is the parent's defect, and it distorts every batch silently.** Splitting a
  table by cell index — column 2 handed over as "field A" when a column was inserted before it — parses cleanly,
  validates, and feeds the wrong value to every child, while each child's output still looks well-formed. Check the
  mapping against the header names once, and after repairing it re-derive any count you already published from the
  wrong field: a correction published as a correction is cheap, a stale number in a report is not.
- **For tasks that restate existing text, the acceptance check is an identifier superset per item.** Require that
  every citation, link or identifier of the original survives in the restatement (input ⊆ output, checked per item by
  script), on top of the shape checks: a rewrite that drops a source reference while reading beautifully passes every
  other gate, and the dropped reference is exactly the meaning that was at risk.
- **Разнотипное задание в одном вызове `dispatch` может не доехать: отправляй по одному.** Три попытки отправить
  четыре задачи с блоками `output_schema` вернули «tasks must be a JSON array of task objects; received a string that
  could not be parsed as JSON» — то есть разъехалась полезная нагрузка самого вызова, а не ответы детей. Одна задача из
  того же текста ушла с первой попытки, три следующие — по одной на вызов. Признак именно этой поломки: ошибка приходит
  мгновенно и одинаково на любом составе задач, дети не запускались вовсе. Не повторяй тот же вызов: сначала отправь
  одну задачу, убедись, что канал жив, и дальше шли по одной — четыре быстрых вызова дешевле трёх мёртвых.

- **One item, one address.** An item that belongs to two targets must be assigned in the plan before dispatch, and
  the other child told to skip it and name it in its report — otherwise it is written twice, or dropped by both.
  Keep it on both pages only where the material genuinely supports each thesis, and record on both that it does.
- **Do not let a child choose the destination page/slug set.** The canonical list of targets is fixed by the
  parent and handed to every child, otherwise two children write the same target in different words.
- **One batch file per child, no shared mutable state.** Children that read the same batch and write the same
  report file race silently.
- **Do not diagnose a failed fan-out from task counts.** Ten "completed" tasks with nine invalid reports is a
  normal-looking result; only running the validator shows it.
- **A verifier that flags known-good work is a verifier defect.** Before acting on reported defects, enumerate every
  namespace a legitimate link or target can come from — canonical pages, source stems, and the mirror copies of
  those sources. A checker that resolves only one namespace reports hundreds of false breaks and buries the real
  ones.
- **Syntactic normalization is the parent's job, and it must be re-verified.** When a child's output needs a purely
  mechanical fix (an extension inside a wikilink, a stray wrapper), apply it in the parent, re-run acceptance, then
  lint and commit in one step — do not send the child back for something you can prove mechanically.
- **For "weave N items into an existing document" tasks, hand over the full current text and demand the full new
  text back**, plus a companion report of what went in and what did not fit. Then assert mechanically that every
  original heading survives, every named source is declared in the header, and links resolve; the line count before
  and after is the cheap evidence that no section was dropped silently.
- **A creation fan-out that outruns its targets must return addressed payloads, not prose.** When the material
  belongs on documents that do not exist yet, have each child return, per target, the block text with its target
  document, the section inside it, a verbatim quote and the citation — a payload the parent can apply in a later
  wave without reading the source again — and let the creation wave apply them mechanically. Prose returned "to
  be filed somewhere" costs a second full read of every source, which is the largest expense of the wave. Guard
  the other side too: while such a payload is unapplied, the item it came from is not deletable, and the parent
  must refuse the destructive step rather than trust that the text "is basically in there".
- **When the batch creates documents instead of extending them, the child-side acceptance has no before-text to
  compare, so it passes anything well-formed.** The repository's own lint is what catches the two real defects: a
  document with no inbound link from another content page (an entry in a generator-maintained index does not count as
  an inbound link) and a structure map that went stale because a node appeared without an intent line. Regenerate the
  map, lint, then commit as one step, and when a child returned a table-bearing document wholesale, check the applied
  file for duplicate keys — the snapshot it started from can predate rows that already exist on disk.
- **Hand the child the destination's row definition — a typed column is not a criterion.** A brief that says "add rows to
  the tools registry" while the batch carries one heterogeneous field (people, companies, products, repositories,
  methods, file conventions) produces a row per mention, and a person lands in a tool column: the shape checks pass,
  because columns and links are fine, and the absurdity is visible only to a reader. State what a row is, what is not a
  row and where those items go instead, and require the type from a closed vocabulary in its own column.
- **Put the destination's language gate in the brief *and* in the validator.** Cell-length corridors, "every cell is a
  full sentence", characters banned inside cells, and the convention for an absence ("the source names no fix" written
  as a full sentence, never a three-word stub) are enforced by the destination's own checks. A return that satisfies the
  child's validator and misses these costs a second wave of the same children lengthening cells — and when a property
  reached the returns only because you forgot to specify it, that is a brief defect: fix the brief and the validator
  before dispatching the repair wave, or the repair misses it the same way.
- **Run the fact-bearing checks over the whole return, not a sample.** Verifying that every quote is a verbatim
  substring of its source and that every number in every cell occurs in that source is cheap over a few hundred rows, and
  it is what turns "the child reported ok" into evidence. Sampling two or three claims per child is for prose; a register
  of claims gets swept whole.
- **A document written from a frame is a stub with sections.** When a child creates a document instead of extending one,
  require the material first: the sources naming the subject read whole, the section list following from what they say,
  and a report of what the material did NOT support (a mechanic present in no source, a claim resting on one voice). A
  frame handed over as "fill these sections" returns text that validates and says nothing. Stage the returns in the
  project's staging area, run the acceptance script over them there (fields, enums, tags, links resolve, declared
  sources exist), and only then place them in the destination and lint.
- **Validate a returned header by parsing it, not by matching field names.** A key glued to its own value
  (`sources: sources: [...]`) satisfies a field-name regex, a link check and a declared-source check, while the parse
  reads the value as one string and the field is effectively empty. Parse the header (YAML) in the acceptance script,
  assert the keys the merge depends on, and treat an unparsable header as a failed return.
- **A phrase in quote marks that no declared source contains — or that a source carries while the document fails to
  declare it — is a finding.** Children quote the destination's own
  section titles, its register rows, or their own phrasing as though it were the source's words; the return reads
  perfectly and only a substring sweep of every quoted fragment over the declared sources sees it. Run that sweep in
  the parent's acceptance, not only inside the brief: a child checks the fragments it believes are quotations, so its
  own phrasing inside quote marks and a source it read but never declared are exactly the two cases it cannot see. The repair is to
  reword or to attribute, never to leave the pseudo-quote standing. Guard the fragment's *form* as well as its words: a
  quotation bent into the grammatical shape the sentence needs (the instrumental where the source has the nominative, a
  plural where the source has the singular) matches no source although every word is right, and reads as a citation while
  asserting what nobody said — drop the quote marks and keep the claim as attributed paraphrase.
- **A "fits none of the categories" verdict is data about the category set, not a failed batch.** Require the verdict
  field, let the child name the closest candidates and why each fails, collect the no-fit rows across all children, and
  read a cluster as a missing dimension: the new value belongs to the parent's merge and to the owner as a proposal.
  Coercing such a row into the nearest category loses the signal; dropping it loses the item.
- **A term-mention count must read the body of a source, not the whole file.** Grepping a short term over source files
  matches sha256 digests and frontmatter keys, so a term that appears in no argument arrives with several "sources"
  behind it and a document gets written on hashes. Count body occurrences, or confirm each hit by its context, before
  publishing a material inventory.
- **A child that dies on a provider-side refusal is the parent's item, not a re-dispatch.** A task that fails with an
  upstream content filter fails the same way on retry: the payload or the page the child must read is what tripped it,
  and the brief is not the problem. Finish those items in the parent against the same sources — the parent's own fetch
  of the same URLs typically goes through — and spend the second dispatch on work, not on the same wall.
- **Apply each finished child's file as it arrives; do not hold the wave for the slowest sibling.** The consolidated
  batch message arrives last, but the files land one by one: read the output directory after dispatch, run the
  acceptance check on what is already there, merge it, and let the tail finish. A sibling that then fails costs its own
  items only, and a long wave stops being a single point of failure.
- **Normalize the destination's own markup before calling a quoted fragment unfaithful.** A verbatim-quote sweep fires
  on fragments where the child added code formatting or emphasis, or unwrapped a markdown link
  (`[art and science](url)` → `art and science`), and on non-breaking spaces — all legitimate. Normalize whitespace,
  non-breaking spaces and wrappers first; a mismatch that survives normalization is the real finding, and its repair is
  to drop the quote marks and keep the claim as attributed paraphrase.

- **A review whose verdict authorises a destructive step needs its judging criterion in the brief, not just its field
  names.** Children asked "is this already in the target document?" answer literally unless told otherwise: they search
  the item's most distinctive token, usually a proper name, product or repository, find nothing, and return "nothing of
  it is there" — while the item's real subject is a concept already developed under another name, inside a section or a
  register row of a page about a neighbouring topic. Make the child restate each item in 2–5 concepts **before**
  searching, look each concept up by meaning and synonym, check the decision ledger and the candidate queue before
  declaring a concept unhandled, and name the landing place (page + section) the item's data belongs to; a verdict
  without a landing place, or with one that does not exist, is not a verdict. Fixing the criterion means a second pass
  over the same items: write it to a new numbered input set and keep the first, so the parent can publish the before /
  after counts and the list of items whose verdict flipped — that flip list is the measure of what the first criterion
  missed. In this direction the miss is the expensive one: an item wrongly marked "already covered" is deleted.
- **In a judgement fan-out, a missing verdict must render as "not assessed", never as a negative.** When children
  return one verdict per item in a fixed vocabulary, the parent's merge tends to default the absent ones to the most
  common value — and in a loss or coverage review that default is exactly the verdict that authorises the destructive
  step ("nothing to lose here"). Give the vocabulary an explicit unknown, count the returned files against the
  expected item list before rendering, and fail the render when the two disagree.
- **A negative verdict a child supplies is the cheapest thing to produce — re-derive it from the primary source.**
  Asked whether a source carries a given kind of material — a pain, an obligation, a counterexample — a child that finds
  nothing can close the item with the shortest possible answer ("none", "marked as not applicable") and move on, while
  the source names the thing outright in its own table of failure modes. That answer is the one that saves the child
  work and the one the acceptance gate cannot see, because a negative has no fragment to check. Treat every supplied
  negative as a claim: reopen the source, search the places where such material lives by convention (tables of failure
  modes and risks, "common mistakes" lists, the "why this is hard" section), and either find it or record what you
  searched. When the material is there, write the row, not the absence mark; the mark is reserved for items where the
  subject is genuinely out of scope, and it costs a sentence of reasoning each time.

- **When every child must register its artifact in one shared file, re-derive the union from the filesystem after the
  wave.** Children appending a line to the same index or log lose each other's writes: the wave returns complete, every
  child reports its line added, and the file is missing some of them. The post-wave check is the set difference between
  the artifacts on disk and the lines in the shared file, repaired by the parent — or serialise the registration through
  one appender the children call, instead of letting each edit the file.
- **Freeze the acceptance harness for the duration of the wave.** Children that improve the shared validator mid-wave
  break their siblings: a signature widened while another child is running turns every sibling's acceptance run into a
  crash, and each spends its budget re-deriving the same parent defect. Dispatch a frozen harness, let children report
  against it, apply harness edits after the wave — one owner per shared tool.
- **A returned payload counts as landed only when the target exists and names the source, not when its wording
  matches.** A draft formulation for a page never survives into the finished text, so text-equality marks every applied
  payload as pending. The landed check is structural — target file exists, declares the item's source, contains the named
  section — and the destructive step that depends on it stays blocked until that check passes, rather than trusting that
  "the text is basically in there".
- **Check the address of a finding, not its field list.** An acceptance that only asserts that `file`, `line` and `quote`
  are present passes an invented finding: the fields exist, the words do not. Resolve the address — the cited file exists,
  the line number is inside it, the quoted fragment is a verbatim substring of that line (a small window of following lines
  is a fair allowance), and the file named as evidence exists. That is the check that separates a typo from fabrication,
  and it is cheap enough to sweep every row of every return.
- **Bind acceptance to this dispatch — a filename pattern also matches the previous run.** A checker that globs
  `<kind>-<n>.json` accepts yesterday's returns and reports a pass while nothing for today has been read; the merge then
  re-opens findings that were already closed. Match against the dispatch file the parent just wrote (its date and mtime),
  and move the previous run's outputs into a dated archive directory so the watched directory holds exactly one run.
- **A target you legitimately repair after the return invalidates the quote check — downgrade it, do not fail it.** Adding
  the link a finding asked for moves the line the quote sits on, and the next acceptance run reports "quote not found" for
  a finding that was true when it was read. Compare the target's mtime with the return's: a target edited later renders as a
  note ("quote true at read time, not re-checked"), while a fresh return is still checked strictly. Without that rule the
  gate punishes honest repairs and pressure builds to weaken the check itself.

## Прострелы // приёмка без пакета, счётчики и инфографика

- **Один артефакт — один источник данных; не копируй файл поверх файла другого типа.** Копирование
  рендера (html) поверх данных (json) уничтожает данные. Собирай данные заново программно из
  первоисточников (подписи — из текста источников, счётчики — через Counter), а перед файловой
  проброса операцией сверяй приёмник, а не только источник.
- **Счётчики пакетов ≠ числу страниц, которое видит человек.** Итог волны и счётчик пакетных файлов
  расходятся: часть страниц делают вне пакетов или генератором, часть материалов получает адрес
  строкой реестра. Перед отрисовкой карты бери факт из git (`--diff-filter=A/M` за окно волны), а не
  из списка пакетов; пользователю нужны и построчный состав (реальные id), и верный знаменатель.
- **Проверяй фильтры самого генератора, а не только его итог.** Правило, выбрасывающее из счётчиков и из
  отпечатка «шум» по шаблону имени файла (например, любую дату в имени как выход генератора), заодно
  выбрасывает датированные источники: карта показывает один файл там, где лежат сто тридцать один, а
  раздел свежести молчит, потому что отпечаток считается тем же фильтром — у проверки и у предмета
  проверки одно слепое пятно. Прежде чем докладывать счётчик, спроси, что именно он исключает.
- **Not-written-into ≠ missing.** Material that carries no authored analysis gets an address in a register (a
  "materials to read" register, rows in a tools register) rather than a chip on a content page, and registers of
  that kind are a class of their own: whole-corpus summaries with no per-item weaving. A coverage count is honest
  only when the "not woven" column states, per item, where it lives and why — otherwise an item with a register
  row reads as a gap, and an item with no row at all reads as woven.

## Related

- `references/document-corpus-audit.md` — the read-only audit fan-out over a documentation corpus: enumerating the
  corpus against a handed list, triaging chronicles and drafts, the question set and the `FILE:LINE — what — command`
  answer format, correcting a machine sheet's signals (placeholders, quoted examples), generated-document verdicts,
  reading a measured class of duplication by eye before extending a guard, and the parent-side pitfalls (post-commit
  acceptance check, a registry cell holding two ids, an exclusion list that belongs to the guard).
- `templates/report-envelope.json` — the envelope shape to hand to children (version, kind, items, failures).
- Role-based clean-context readers that check a repository's entry document and layout (single-file reader, tree
  verifier, symptom navigator, newcomer scenario) — a fan-out whose output is a defect list for the docs, not data (`knowledge-base-quality-gates`, clean-context legibility review).
- When the rounds start feeding the project's own machinery instead of its material — the ratio worth measuring, when to
  freeze the gates, why a finding the owner leaves visible on purpose must not be silenced (`knowledge-base-quality-gates`, gates-vs-content: measure the ratio, freeze green gates).
- `references/revision-wave-review.md` — revision waves where the owner reviews the children's edits against a written
  standard: why bans get over-applied, how to repair the standard (over-application vs miss vs partial application), and
  the mechanical tail the parent owes after acceptance (dates, renumbering and pointer repair, regenerated artifacts,
  wave close, honest numbers).
- Projects with their own contract text keep it in the repo (`contract-v1.md`, validator script, retry log);
  this skill is the generic procedure, the repo file stays authoritative for field names and enums.
