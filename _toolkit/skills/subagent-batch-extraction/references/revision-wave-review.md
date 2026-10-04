# Revision waves: the owner's review, repairing the standard, the closing tail

A revision wave is a fan-out whose mandate is to edit existing documents directly, under one written standard
(a style source, a page spec). Children never write from a clean slate, so their failure mode is not an invented
fact — it is **deleting real content while following a ban**. The parent owns the standard; the owner's verdict
repairs the standard, not the child.

## Why the standard gets over-applied

A ban written against one failure mode is executed literally and sweeps the case it was never about. Budget one
rejection per ban: the boundary gets written down only after the owner reads the diff.

Catalogue of over-application, each seen against the same standard:

1. **"A fact already stated elsewhere does not belong in the collected-facts section" deletes the section.** The
   documents where prose and a facts/disputes/boundaries block are written by the same author mirror each other, so
   the ban covers the whole block. Fix in the standard: duplication is the same fact twice **inside one section**; a
   fact stated in the body and collected in the facts block is not duplication, the block is a reference card. Add
   "when in doubt, keep the fact" — a ban with no tie-breaker deletes.
2. **"Do not write about how the document was assembled" removes navigation and scope limits.** In-document
   cross-references ("see the disputed questions below") and the author's own limits ("does not pay off for small
   projects") are content. Separate in the standard: remarks about the assembly (banned), navigation inside the
   document (content), limits of applicability named by the author (content).
3. **"Remove statements about the strength of the evidence" removes the author's boundary too.** Split the two by
   subject: an *evidence caveat* is about the source (no measurement published, vendor-written, methodology
   undisclosed) and is noise; a *limitation* is about the subject (works from a few hundred lines up, redundant for
   prototypes) and stays.
4. **A symbol ban lands where the symbol carries order.** A rule against arrows where words read better gets applied
   to arrow chains that express a pipeline sequence. Say where the ban applies (inside a table cell) and where the
   symbol is doing work (a prose sequence of steps).
5. **A pointer to the project's own source note replaces the address of the material.** When the text sends the
   reader somewhere, the address belongs in the text; the source note is a citation, not a reading destination.
   Pairs with the standing rule that a reader is never sent to look something up in the source tree.
6. **"Drop the coverage reports" removes an item that carries no finding, and trims only the tail when the finding
   sits inside it.** An item whose whole content is that a figure was published without a dataset, that a method was
   not disclosed, or that no measurement exists is noise and goes **whole** — a child that cuts the caveat's tail and
   keeps the sentence has done the mechanical half only. Write the rule positively (what makes an item a finding:
   a figure with an owner, a named divergence, a property of the mechanism the body does not carry) and name the
   exception: where the document's *subject* is the quality of evidence, those phrases are the finding.
7. **A boundary that sits inside an oddly-shaped block is still content.** A limit named by the author but written
   under a sub-heading nested inside a section is not made removable by its packaging: the removed unit is the
   *line*, and a misplaced block is repaired by moving the line into the section that owns it.

**Partial application is its own defect and it is invisible in the reports.** A child that trims the tail of a
filler sentence and leaves the sentence has done the mechanical half: it removed what the rule *named* and kept the
sentence the rule was *about*. After the wave, re-read the sections the brief targeted rather than only the diff
hunks the children described — a section reported as done can still hold the line the rule existed for.

## Before the owner reads the diff: the parent's own two sweeps

The review report is the owner's artifact, so the parent owes it two mechanical sweeps over the children's diff
first. Both are cheap, and both catch a class that the children's own reports call "done":

- **Every deleted line, for a figure together with its owner.** A line carrying a number and a citation of whoever
owes it is the loss the owner notices most reliably, and it disappears quietly inside a caveat, a nested block, or a
"duplicate of the prose" judgement. Sweep the deletions for a digit plus a link and read each hit: if the figure
lived nowhere else, it comes back — as a finding in the section that owns it, not as the caveat it was cut from.
- **Every added line, for talk about the document or the corpus.** A child that removes one meta-remark frequently
writes another in its place, because the ban was on a phrase rather than on the move. Grep the additions for the
document's own name, the corpus, and hold/rest/consist constructions, and rewrite each hit so it speaks about the
subject while keeping the link the sentence carried.

Neither sweep replaces the owner's read: they exist so that what reaches the owner is the judgement calls alone,
not the mechanical misses.

## When the standard has matured: report only the doubtful edits

After a wave or two come back clean, the full diff stops being useful — the owner asks for a report of **only the
edits the parent itself considers doubtful**. Generate it beside the full report from the same base commit and keep
the full one as the archive.

Filter hunks by whether the removed signal **survives the edit**, not by whether text was removed:

- a deleted line carrying a link together with a figure is doubtful only if that figure does not reappear in the added
  lines — otherwise it is a rewording;
- quote, address and removed section heading: the same test, their absence from the additions is the reason;
- length counts only as **net** loss (removed minus added characters), or every rewording lands in the report;
- the header reason excludes the modified-date field, which the standard requires to change on every edit.

Measured on a real wave: without the survival test, 78 hunks from 40 documents; with it, 30 from 21. Print one line of
reason per hunk («deleted a line carrying a link and a figure», «whole section removed: boundaries», «added talk about
the document or the corpus») so the owner reads evidence instead of a diff, and say in the report's header that absent
documents are absent because their edits were verified mechanically — naming the machinery: the figure-with-owner
sweep, the meta-talk sweep, address and link checks, and the header diff.

## Repairing the standard after the review

Classify every place the owner flags, then act on the class:

- **Over-application (rule too strict)** — the rule as written covers a legitimate case. Rewrite the rule so the
  legitimate case is named, inside the rule text, using the owner's own example; after the second or third such
  rejection add a short *what is allowed* block, because bans alone leave the boundary to the child's imagination.
- **Miss (rule absent or not applied)** — add the rule, and write it as what makes an item a finding rather than as
  a blacklist of the phrases that happened to trigger it (see the rule-placement reference in
  `knowledge-base-quality-gates`).
- **Partial application** — the rule exists and was half-applied; tighten its wording to name the whole unit
  ("the item goes, not its tail") and check the other sections yourself.

Keep the documents uncommitted against a **named base commit** for the whole review, so a rejection costs one
restore command over the edited tree; restore the rejected text verbatim rather than re-editing it. Commit the
documents once, when the owner accepts, together with the repaired standard.

## The gates that enforce the standard

Repairing a standard usually means touching the checkers behind it — a rule the gate cannot see is a rule the next
wave sweeps again. Full craft notes: `knowledge-base-quality-gates` (linguistic check patterns: cover the grammar banned, not only its commonest form).

- **Cover the grammar you ban, not only its commonest form.** A pattern written for the reflexive/passive verb
  ("is held by", "держится") misses the active one ("holds", "держит") — which is what a child writes when it
  rewrites a sentence to satisfy the ban.
- **Scope the wider form by subject, not by keyword**, then prove it on the live corpus: run it, read the *subject*
  of each hit rather than the keyword, count the legitimate ones, and narrow until only the real defect fires.
- **When the owner says a hit you reclassified as legitimate is a defect, widen the pattern back — the repair goes
  into the text, never into the check.** The narrowing in the bullet above is the parent's own judgement, and it is
  the step that loses a whole class: a phrase ruled "legitimate" because it reads as a claim about the subject ("the
  definition belongs to the vendor and rests on its own measurements") is exactly the meta-talk the rule was written
  for. On his verdict, restore the grammar you had excluded, delete the flagged text to green, and prove the branch
  with a canary that writes his own phrase into a document — a narrowed pattern keeps the class invisible, and the
  next wave sweeps it again.
- **One canary per branch, and prove it fires** — a pattern without a canary is a claim.
- **Fix the text a new pattern uncovers in the same change**, so the gate reads green afterwards, and put the
  owner's example into the standard.
- **Read a register's own checker before reformatting a cell** — a column parsed as a bare number rejects a
  comma-separated list.

## Closing tail after acceptance

Children were told not to touch the shared and generated parts, so these are the parent's own steps:

1. **Raise the modified-date field** in every edited document's header. Children with a clean context do not touch
   the header, so a fresh body keeps a stale date and the project's freshness checks fail. If the standard never
   stated this rule, add it — the review is where such gaps surface. And never hardcode that date in the brief: read
   the clock when dispatching, or the whole wave lands with a date the commit history contradicts.
2. **Renumber, and repair the pointers in the *other* documents.** Inserting a section into a numbered document
   shifts every later number; documents elsewhere reference it as "section N". Grep the pointer sites and fix them
   in the same commit.
3. **Regenerate everything the project generates**: structure map, session bridge, panels, and the backlink blocks
   of the source notes the edited sections cite — a backlink block is derived from the citing text and changes when
   that text changes.
4. **Close the wave through the project's own entry point**, add the log entry, mark the plan file executed,
   lint once, then commit — with the project's accepted commit-message type.
5. **Publish both numbers and name the difference.** The review report's per-document line delta is not the commit's
   `--shortstat`: the difference is the raised dates and the regenerated blocks. Read the staged file list before
   committing (`git add -A` is fine when that is the project's convention) and say what the extra files are. The
   report diffs against the base commit, so every rejected edit you restored counts as an *addition* there — say that
   plainly rather than presenting the restored lines as new work.
6. **Restore only the half the owner named, into the section that owns the fact.** "Bring X back, leave the rest" is
   an instruction about one clause: put that clause back as a proper entry (not into the odd block it lived in),
   attach the attribution its bare form lacked, and re-check the document's own fields (line-count justification,
   dates) after the shape changes. An item he declined is not offered again in the next report.
7. **Commit the tooling, not the documents, until the verdict.** Stage everything, unstage the document tree, and
   commit the standard, the checkers, the canaries and the review report as one tooling change. The owner's rollback
   stays a single command against the named base commit, and the accepted edits land in their own commit afterwards.
8. **Isolate each wave's diff before the next one starts.** Commit the accepted wave first and keep the next wave's
   base as a named commit: the report generator diffs the working tree, so an uncommitted earlier wave mixes into the
   next report. Regenerating an accepted report against the live tree overwrites the artifact the owner already read —
   restore that file from the commit that carried it and say so.
9. **`git add -A` after a wave can sweep the owner's own untracked files** — clipper exports, scratch notes, drafts.
   Where `-A` is the project's accepted convention the guard is reading the staged list, counting it (printed listings
   truncate), and when one of his files is in it: report it and offer the three options — keep it and accept the red
   checks it triggers, exclude that folder from those checks, or ignore it in git. Never repair it by rewriting
   history.
10. **A third-party material quoted inside a declared source: look for its address in that source.** A page citing a
   guide the corpus does not hold has a chain (page → declared source → quoted guide), and the address usually sits in
   the declared source. Grep it there before declaring the address unavailable; the repair is the address in the text,
   not a new header entry (the sources field lists corpus paths, not outside links).
11. **Prefer extending the standard's own text over adding a check.** A defect the owner finds by eye usually has a
   live instance or two and no clean pattern: measure the candidate check on the corpus, read each hit's *subject*, and
   if most hits are legitimate, write the rule into the standard and fix the instances by hand — "insurance, not cure"
   is the honest verdict to report, and a noisy gate devalues the whole set.
