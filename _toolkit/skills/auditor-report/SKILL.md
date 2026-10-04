---
name: auditor-report
description: "Use when writing a report for an external auditor."
version: 1.0.0
license: MIT
platforms: [windows, macos, linux]
---

# Auditor report

A report to an outside reviewer exists for one reason: the reviewer cannot see your machine, your working tree or
your memory. Everything a reviewer will do is **recompute what you asserted**. Write for that, and the review
becomes an asset; write from memory and each round costs a week of arguing about numbers.

Companion skill: `knowledge-base-quality-gates` owns the *package* mechanics — one generator run per package,
prose-number scanning, the canary harness, the handover ledger, the pre-commit gate. This skill covers the *cycle*:
what the letter must carry, how to answer findings, and what to check before pressing send.

## When to write it, and where the window starts

Write it at an explicit request, at the end of a work cycle, and always before a large ingest — a cycle nobody
reported is a cycle the reviewer cannot verify. Resolve the start point by this priority, never by memory:

1. **A review of the previous report** (pasted in, or sitting as a file) → the window starts at *the state after
   that review*, and the letter opens with a section answering it.
2. **The previous report** → the window starts at its last commit.
3. **The session bridge** (`<instance>/bridge.md`) → its recorded window end is the start point,
   and its commit list is the first draft of the letter's own list.
4. Nothing at all → say so explicitly and take the start point as the repository's first commit; a guessed window
   is worse than a declared one.

The window's end is the commit the letter is written at, and it belongs in the letter's frontmatter so a reader
can recompute the list themselves. Everything before the window belongs to earlier cycles: see the honesty rule
about attribution below.

## The honesty contract

- **No hand-typed numbers, anywhere** — including the questions you ask the reviewer. Prose and question sections
  are the last surface a generator does not render, and a stale figure there survives regeneration after
  regeneration. Every count comes from the artifact that computed it (figures file, machine summary, results
  JSON), substituted in, not retyped.
- **Pin comparisons to the frozen sent copy, never to "now".** A dated report file is rewritten on every run, so
the same filename can carry two contents in one day; a diff table whose "previous" column was filled from the
working tree silently compares against the wrong point. Take both endpoints from archived snapshots, and name
(date + hash) which artifact each column came from.
- **Publish tables in full.** A list column clipped to N entries drops exactly the item that decides a borderline
  case. If you must shorten, say so where the reader sees it, not in your own memory of it.
- **Show the failures without cosmetics.** A threshold missed is reported as missed, with the score and the
  threshold side by side. Every cosmetic pass costs the reviewer's trust in the parts they cannot check.
- **Never assert validation of material the reviewer cannot see.** Attach the artifact, or drop the claim. A
  "confirmed by reading" over a blob the reviewer does not have is not evidence, it is a request for belief.
- **Distinguish fixed-by-data from fixed-by-rule.** Widening a rule to fit the data is legitimate; its *result*
  must be visible — say where the field points now, which row appeared, what changed versus what was only
  reinterpreted. A finding closed by prose alone reads as rule-fitting, and the reviewer will say so.
- **Keep rejected recommendations visible.** Every counterpart proposal gets a verdict: accepted, accepted with
  amendment, rejected with a reason, or withdrawn. Rejections are recorded where rules live (the schema), because
  an unrecorded "no" returns next round as unfinished work.
- **Journal publication defects separately from data defects.** A wrong number in a shipped file, a truncated
  column, a report rewritten after sending — these are defects of *publication* and they belong in their own
  register, next to the history of what was already sent. Fixing the file does not fix the record.
- **Claims about the past need a date and a frozen copy.** A phrase naming a package plus a number is checkable
  only against a ledger row; an ordinal ("the fifth package") with no date is unverifiable by construction.
- **Never attribute the previous cycle's work to this one.** Read the window from git and let it decide what is
  yours: taking credit for an earlier cycle's pages, checks or ingest is the same defect as a wrong number, and it
  is what a reviewer detects first, because they hold the earlier letter. When the boundary is genuinely ambiguous,
  state the ambiguity instead of claiming the whole span.
- **Name the versions of the norms you applied.** A letter that follows a schema, contract or rule set must say
  which version of each, and mark any artifact produced under an older version. Rules change between cycles, and a
  report that silently applies last cycle's contract makes its own numbers incomparable with the reviewer's notes.

## Outgoing letter: sections that earn their place

Every letter opens with frontmatter — the header that makes it self-describing years later, when the chat is gone:

```yaml
---
type: auditor-report
cycle: <date>            # the cycle this report closes
from_session: <one line: what this cycle was about>
created: <date>
commits_span: <first-commit>..<HEAD>   # the window, recomputable by the reader
audience: external auditor
snapshot: <the snapshot line from the figures artifact, verbatim>
---
```

A response letter carries the same header with `type: auditor-response` plus `answers_review: <date or ordinal of the
review it answers>`. The header is not decoration: `commits_span` is what lets a reviewer check your window, and
`snapshot` is what pins every number in the text.

There is no manual skeleton: the letter is assembled by the send task (`send`) from the contract — manifest hash, defect table, composition record. The parts a reviewer
actually grades:

1. **Snapshot line first** — documents, sources, edges, cards, questions, checks, and the commit the report was
   built from. Every number later in the letter must equal this line.
2. **What changed** — one table, both endpoints from one generator run, then one short paragraph naming the
   single input that produced the movement and — explicitly — what was not touched (nothing deleted, nothing
   rewritten; pages gained sections, disputes were added as rows).
3. **Tooling** — which checker changed and its re-attested numbers; defects found in the tooling separated from
   the work that motivated them.
4. **Tests** — check sections and problems, attestation sensitivity/specificity, which sections no canary
   reaches and why, the number check, the blind-run trajectory and what the misses were.
5. **Defects and closures** — symptom, root cause, closure; plus one line on which were caught mechanically and
   which by eye (a defect found by eye is a missing check).
6. **Open and risk zones** — each with owner, reason and status; name the weakest part of the material plainly.
7. **Next cycle** — ordered by dependency, each step with the check that closes it, plus what will *not* be done
   and the recorded reason.
8. **Questions for the reviewer** — numbered, unanswered, each a genuine limit of your rules (a denominator, a
   class of evidence, a threshold). No rhetorical questions.
9. **Where the evidence is** — file names a reviewer can open, not a promise that it exists.

## Answering findings

Per finding, in order, four things — never three:

| verdict | what must accompany it |
|---|---|
| accepted | what changed, in which file, plus the evidence handle |
| accepted with amendment | the reviewer's point kept, your narrower claim stated, and it to be able to survive the next recomputation |
| rejected | the artifact that supports your number, and the reviewer's stated as theirs the same way |
| withdrawn (your own counter-proposal) | the reason, plus the fact that it is recorded |

Rules for the harder cases:

- **When counts disagree, read the full underlying data before defending or conceding.** The summary table is not
  the data. Both sides have argued from artifacts whose completeness was never established — one unmarked, one
  truncated — and the fix is symmetric: freeze both.
- **If your shipped artifact truncated something, say that first.** A clipped column can be the real root of a
  disagreement about a recount; concede the artifact defect before debating the arithmetic.
- **Answer the finding, not the finding's tone.** A reviewer's sharp sentence is often the most useful line in
  the letter; quote it, close the underlying hole, and repair the *mechanism* (a new check, a snapshot, a
  register) rather than the instance.
- **A correction to a shipped document gets a visible delta** — superseded value, new value, reason. Do not
  silently rewrite what the reviewer holds, and do not leave a known-false number standing either.
- **Report your own checkers' catches in the same letter.** A defect your tooling found before the reviewer did
  is the strongest evidence the tooling exists for a reason.

## Procedure: what the agent does, what the machine does

The half that is mechanical already runs without you — knowing the split is what keeps a letter honest about its
own production.

1. **Agent:** read the bridge first; take the window from it (rules above) and confirm the working tree's state.
2. **Agent:** collect the cycle's facts from artifacts only — `git log <span>`, the figures JSON, the lint summary,
   the attestation results, the exam score, the debt register. Not from memory of the session.
3. **Agent:** answer the previous review finding by finding, with verdicts and resulting states.
4. **Agent:** name the weak spots (floor of five, below) and the questions the rules cannot settle.
5. **Machine (pre-commit):** rebuild the registries, the number check, the lint summary and the package; freeze the
   snapshot of what is being sent; regenerate the structure map and the bridge.
6. **Agent:** write the log entry (`## [date] audit | …`) and commit; verify the tree is clean afterwards.
7. **Agent:** state in the letter where the evidence is — the frozen snapshot's name, not a promise that it exists.

Checklist before pressing send: window taken from an artifact and equal to `commits_span`; every number from the
figures artifact; the review's findings each answered; at least five named weak spots; the exemplar pair opened
once for tone; the frozen snapshot present in the ledger; the tree clean; the letter's own file references all
resolve.

**The delivery act stays with the owner: run no send or publication step without his explicit word.** Whether
the mechanical half — assembling the package, stamping the letter with the manifest hash, freezing the snapshot,
recording the composition — may be run on the agent's own initiative is a decision of the project, not a licence
this skill can grant: in the project the audit chains (`check`, `package`, `report`, `send`) are gated
by the owner's word (`AGENTS.md`). Asking about it is never the defect; sending unasked is. Fix the order inside that task too: the frozen snapshot
must be taken **after** the letter is stamped, or the archived copy differs from what actually left.

## Pre-send checks: walk the chains the reviewer will walk

**Start with the bridge.** A session that resumes without a bridge re-litigates settled questions and redoes
finished work; before writing anything, regenerate the project's session bridge (`bridge.md` in the wiki
project: reading order, work window taken from git, current state from the snapshot, open items from the
register, and the do-not list built from every recorded refusal) and take your start commit from it. A report
written from memory of the session is the same defect class as a hand-typed number.

Before sending, recompute — by machine, not by eye — every chain the review will test:

1. check counts across cycles (sections, canaries, attested sections) — the numbers must add up exactly, with no
   unexplained jump and no leftover phantom from an earlier round;
2. the diff table's both endpoints against archived snapshots of those reports;
3. every headline figure in the letter against the snapshot line and the figures artifact;
4. every threshold against its actual result (score, threshold, pass/fail stated plainly);
5. every open item against the register — owner present, evidence path existing for anything marked closed;
6. every claim about a past report against the ledger (date + frozen copy);
7. every table against its column count — no row shorter than the others, no clipped list;
8. the letters' file references against the filesystem: every "see X" must resolve;
9. attestation naming any check whose rule changed this cycle;
10. the artifact inventory (file, purpose, size, hash) regenerated so a reader cannot find two sizes for one file.

Still to do before sending: the letter's own self-criticism. A report with no weak spots named is not an honest
report — **state at least five limits of the work, including one you would rather not publish** (a heuristic you
suspect, a metric that flatters, a claim you cannot recompute, a category you stopped checking). Borrowed from a
sibling project's session-report convention: the section that names the weak spots is the one that carries the
most review value, and a reader can tell when it was padded.

Then read your own letter once as the reviewer: find the sentence you would attack first, and fix it before they
see it. The sentence you want to hide is the one worth publishing.

## Reference pair

An exemplar beats a description. In a worked project: `<instance>/audit/auditor-report-2026-09-13.md` is the letter
that closed the cycle with the corpus ingest, and `<instance>/audit/auditor-response-8-2026-09-13.md` is a response
that answered findings one by one; both are named in `<instance>/audit/README.md`. Read the pair before writing a new
letter — the tone that survives a hostile recomputation is easier to copy than to derive.

## Pitfalls

- **A comparison labelled with the wrong endpoint disinforms without lying.** The label said one review point,
  the values came from a commit in between; the reviewer recomputed from the published package and was right
  against it. Label columns by artifact, not by memory of the intention.
- **One count, one producer.** Three mutually inconsistent values of the same quantity across a package (matrix,
  prose, response) are what a hand-maintained tally looks like after a section is added; compute once and
  substitute everywhere.
- **A claim that a defect "can no longer happen" is only true for the zone a check covers.** Scope the sentence
  to the zone, or the next recurrence turns your confidence into the finding.
- **The reviewer will see your failure modes through your own artifacts.** If the honest score is below the
  threshold, publish the score; a report that rounds it up is the only one that gets permanently distrusted.
- **Do not let the letter grow into the package.** The letter argues and asks; the package proves. Anything a
  reviewer must recompute belongs in the package with a filename the letter can point to.
