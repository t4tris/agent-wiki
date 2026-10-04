# Semantic audit of a documentation corpus

Class of task: a corpus of hand-written service documents (entry docs, playbooks, contracts, canons, layout notes) must
be checked for *meaning* — not for line counts, dates or dead paths, which a machine sheet already measured. The owner
usually hands you his own machine sheet plus the list of files he counted; the work is a read-only fan-out plus a
findings report he can act on. Guards and the knowledge-base gates themselves: `knowledge-base-quality-gates`.

## Procedure

1. **Enumerate the corpus yourself before dispatching.** `ls` / `git ls-files` over the places documents live, then
   compare with the list you were handed. A hand-count undercounts: files added in the current session, a README in a
   neighbouring folder, the audit layer's own README. Report the delta ("you named 21, I count 24; three unnamed: …")
   and check the extras too — never silently audit only the handed list.
2. **Separate documents from outputs and journals before auditing.** Dated artifacts of closed passes, generated views
   and logs are records, not documents: say which you excluded and why, one line each.
3. **Triage the ambiguous files by asking what each one IS**, then act:
   - a chronicle of a closed incident (a repair plan, a dry-run note, a draft that has served) leaves the working layer
     for the archive — the working layer holds what is live, the archive keeps the history;
   - a document holding requirements stays where it is;
   - a served draft duplicating a derivative (a page draft beside the ingested source) is double truth to file away, not
     waste to delete — move it, never silently drop it;
   - a file another session may still be writing: flag it, do not move it.
4. **Batch by weight, not by count.** ~4 documents per child, balanced by line count (a 1000-line canon gets a child to
   itself). Read-only scope: children report, they never edit — an audit that edits cannot be trusted to report honestly.
5. **Give every child the same question set** (below), its own files, and the answer format. Children know nothing of the
   conversation: conventions, the guard registry path, the output format and the output language all go into each task.
6. **Verify every finding yourself by running the command it cites.** A child's finding is a claim; the command is the
   evidence. Sweep the whole set with one script that prints a verdict per claim (`OK` / `does not reproduce`) rather
   than reading them by hand — a few dozen commands are minutes of machine time. **When a claim does not reproduce, look
   before you drop it:** re-run the child's own command form and check the artifact's real type and location first,
   because your re-check fails more often than the finding does (a sweep that globbed `*.json` while the objects were
   `*.md` reported zero; a repo-wide grep for a phrase did match it, and the absence was declared anyway; a count
   compared as a string reported the opposite of what it printed). Check too whether the artifact was rebuilt between
   the child's read and yours: a generated file regenerated mid-session legitimately no longer carries the line the
   child cited, and `git show HEAD:<file> | grep <fragment>` settles whether the claim was true when it was read. A claim that still does not reproduce is published
   under its own heading with the command that disproves it — never silently dropped, because the requester compares the
   report against what was claimed, and a vanished finding reads as a lost one.
7. **Write the report into the repository, not into the chat** — a findings table (file, line, what, command), the triage
   decisions, the files the requester did not name, and a separate section for the claims that did not reproduce.
   Findings the owner must decide on are listed separately from the ones you fixed in the same turn. Where the review
   arrives through a live channel kept in a scratch folder (a two-session forum, a reviewer's file), snapshot it verbatim
   into the location the project declares for review texts and name it in the entry document's reading order: a review
   that exists only in the scratch folder leaves with the next cleanup.

## The question set (the brief)

1. Does the document hold **requirements** or a **chronicle** (how it came to be, what was done)? Chronicle inside a
   requirement document is a finding.
2. **One requirement — one bullet.** A line carrying two rules diverges at the first edit; split it or report it.
3. Does it **retell a neighbouring document in its own words**? A verbatim-overlap guard cannot see paraphrase — name the
   neighbour and the overlapping area, and let the owner decide whether the duplication is intended.
4. Is there an **address that does not exist** — path, file, command, section number, task name? Every one is checked by
   running it, not by reading it.
5. Is there a requirement with **neither a guard nor a stated reason** why there is none?
6. Do the **numbers** match the artifact they come from?

## Answer format and the negation rule

`FILE:LINE — what is wrong — the command that shows it`. A negation ("no such file", "this guard does not exist") ships
with the command that produced it; a claim without a command is not accepted, and a report carrying one is rejected as a
whole. A clean document is reported as clean — explicitly, so silence never reads as "not checked".

## Correct the mechanical signals before believing them

A machine sheet's signals carry their own false positives; re-derive each before publishing findings:

- **Dead addresses: count without placeholder templates.** `<date>`, `<n>`, `<entry>`, `**/*.md` and friends are not
  addresses. Without that filter one canon contributes ~11 phantom dead links and the list is noise; with it, handwritten
  documents commonly come back at zero — which is the real result and worth stating as such.
- **Meta-talk ("self-description"): count without quoted examples.** A style guide that lists forbidden phrases quotes
  them; those lines are examples, not violations, and a naive count flags the style guide itself.
- **Publish the corrected number and the filter that produced it.** A number that came from a criterion you repaired is a
  finding about the criterion as much as about the corpus.

## Generated documents are a separate verdict

When a signal fires on a *generated* document (a structure map, a bridge, a panel), it usually points at the
**generator's template**: the phrasing is written once in code and multiplies over every future rebuild. Decide and say
which — patch the template (the ordinary repair), or exclude generated artifacts from that check (when the phrasing is
inherent to what the artifact is). Never hand-edit the generated file: the next rebuild restores the old text and the
finding returns.

## Parent-side pitfalls

- **Do not edit a document a running child is reading.** Its findings cite line numbers; your edit moves them and turns a
  true finding into an unreproducible one. Wait for the batch, then fix.
- **Run the copy-versus-working-tree acceptance check after committing**, not before: a check that builds its copy from
  `HEAD` and runs before the commit compares the *previous* revision and reports a divergence that does not exist (a
  journal or contract line the working tree already satisfies). Commit, check, report.
- **A registry column parsed as one identifier breaks the moment it holds two.** Appending a second guard or canary id to
  a cell written for one makes the parser read `A,B` as a single unknown id, and the check then reports "not in the set"
  for both. Teach the parser to split on the separator before appending the second value — the false finding otherwise
  looks like a genuinely missing guard.
- **Artifact series belong in the file name, not in a growing file.** When a run's summary is rewritten in place, the
  series lives only in git history and "compare two runs" becomes archaeology. One file per run with a counter label
  (`name-<date>-<NN>.json`), the revision of the thing under test recorded inside, and a `--compare A B` command keep the
  series in the working tree and the master unwritten; an accumulating list rewrites the master every run and conflicts
  on merge. Record inside it the revision **and** whether the tree was dirty, plus a hash of each measured input: a
  revision taken at run time describes `HEAD`, not the working tree the run read, so a summary carrying only the revision
  cannot be reproduced from its own fields once the fixes it measured are committed.

## After the report: one finding or a class

- **Fix single findings by hand; a class that repeats is a guard whose window is narrower than the rule.** Sort the
  findings by shape. One occurrence — a dead address, a stale number, a task name that does not exist — is repaired in
  place in the same pass with no new mechanism, because a guard built for one incident is insurance and the owner rejects
  insurance. The same shape across a dozen documents means the rule is already written and its guard cannot see it: read
  the guard's window before proposing anything (a signature anchored to the start of a line misses a case opener inside a
  table row; a marker accepting one kind of emphasis misses another; the list of documents the guard reads leaves out
  four of the files the class lives in). The repair is then the window, not a new mechanism — and it is the owner's call:
  put the measured numbers in front of him and wait for the word. An extension ships with a canary that plants the new
  shape and demands the hit — an extension without one is a claim, not a guard — and where the class is attested by a
  tool's own refusal rather than by a section, the canary runs the tool with its input removed and requires a non-zero
  exit, so the two states stay apart inside the tool: no data is a lawful fresh state, the container being absent is a
  broken instance.
- **Measure the duplication you report, with a pinned recipe and the kinds of text counted apart.** "This document
  retells its neighbour" becomes actionable only with a number, and a number without a recipe means nothing: the same
  pair measured over the whole file gave 71 shared eight-word phrases and **zero** once prose, table rows and code fences
  were separated — the window had been spanning row boundaries, so the "matches" were concatenations of two different
  lines, not repeats. Count prose by n-grams and the other kinds by whole normalized lines (an eight-word frame finds
  nothing in a command, and that silent zero is what hid the artifact); pin the recipe in the script's docstring, print it
  with every run, and give the metric a self-test over a planted pair — the one written for this pass immediately caught
  its own machinery channels reporting zero. Exclude **generated** documents from the queue (a map or bridge repeats the
  documents it was built from by design, and a queue dominated by them asks to delete what must repeat), announce the
  queue together with recipe, revision and date, and set the threshold only after the recipe is frozen. Re-measure after
  the repair so the claim has a before and an after.
- **Read the measured queue by eye before touching a guard, and be ready for the class to be empty.** A number says
  "these pairs overlap", not "these documents retell each other": of three pairs carrying long prose matches, two were one
  rule written twice — repair: the rule stays in one document and the other points at it — and the third was a shared
  **command**, machinery whose repetition is correct. With the pairs read and repaired, the queue held a single legitimate
  repeat, so the class does not exist on current data and the guard stays as written. Extending a guard for a class whose
  items turn out to be legitimate repeats buys noise, and the owner rejects it as insurance bought without a defect.
- **A pointer written in the source's own words is still a second place.** The first repair of a duplicated rule quoted
  the original formulation, and the pair stayed in the queue; reword the pointer so it does not repeat the sentence it
  points at. Then re-measure — the pair leaving the queue is the evidence the repair landed.
- **A guard that would fire on the day it ships is set as the acceptance criterion of the cleanup, not switched on now.**
  A ceiling for a document already over it turns the gate red from the first run, and a gate that is red on day one is
  read as noise and muted — the same road as extending a guard for a class whose items turn out to be legitimate
  repeats. Write it as the cleanup's own acceptance ("the split is done when this document is under N lines") and arm the
  guard together with the cleanup, so its first red is a real defect.
- **Tie every exclusion to the guard, never to the file that needed it.** "Generated documents are out of this queue"
  written as a private list inside the new tool is a second source of truth: ask the guard for its own predicate (hoist
  the shared pattern to module level so the linter and the metric read one list) and confirm the excluded set is still
  judged somewhere — a document excluded from one check and left under none is a document nobody watches. Exclusion from
  a queue and exclusion from a guard are different decisions and often opposite: a generated map leaves the retelling
  queue because it repeats its sources by design, and stays under the chronicle guard, where the repair is the template.
