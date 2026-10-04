# Guard test harness: mutations that measure something

Depth for the attestation rules in `SKILL.md`. A mutation harness (canaries) proves a guard fires and — through its
benign half — that it stays silent. These are the ways it quietly stops measuring, and the repairs.

## A mutation must write in the format the guard parses today

A mutation that edits an artifact into a shape the guard no longer reads applies cleanly and produces no finding: the
run reports «not applicable — target stale» or, worse, «no section fired», and the guard looks blind when the fixture
is. When a guard's parser, renderer or record shape changes, every mutation aimed at that artifact is suspect in the
same commit: re-read the mutation's own write against the current format, not against the format it was written for.
The same failure appears as a *dead target*: a mutation that mutates a hash, a size or a field that has since been
dropped. When the guard's mechanism is removed on purpose, the mutation is not fixed by retargeting it to nothing —
re-aim it at the guard's remaining live rule, and verify with a single-mutation run before the next full pass.

## The harness's own text is part of the corpus

A file-sweeping guard reads every file in the tree, including the harness. A mutation that creates a file whose name
is written literally in the mutation's own code is therefore *named* by the corpus, and the guard stays silent. Build
such names at run time from parts (`"-".join(("zz", "canary", "orphan")) + ".json"`) so the name exists only on disk.
The same trap applies to any fixture the guard scans: a canary that injects a reference to itself defeats itself.

## Report arithmetic explicitly

Four numbers, all named, over one run: caught / applicable / not applicable / false positives on benign input.
`caught of applicable` is the honest sensitivity, `not applicable` is a harness defect to fix, and benign false
positives are the specificity. A single «N of M = X%» line lets a reader derive a different total than the detail list
shows — the mutation that did not apply is either counted as caught or invisible, and both mislead the owner.
Never let a non-applied mutation stay in the denominator unexplained.

## Do not tolerate noisy sections to raise specificity

When benign mutations light up guards that legitimately react to the tree the mutation produced (a fresh fingerprint,
a panel describing the tree before the mutation), the tempting repair is to add those sections to a tolerated list.
That blinds exactly the measurement: benign mutations are the only place a guard's *silence* is measured, and the
guard's own canary tests firing, never silence. Fix the base instead — inside the copy, after the mutation and before
the checkers, re-run the generators of the derived artifacts (structure map, session bridge, panels) so the copy
describes itself again; then any firing is attributable to the mutation. Cost is one rebuild per benign mutation
(seconds), which is cheap next to the pass. If the rebuild is genuinely too expensive, print specificity twice —
strict and explained, naming the sections whose firings come from derived artifacts — so the number stays visible
rather than quietly inflated.

## Keep the living guard covered

When a guard's rule is removed and replaced, its canary becomes a fixture with no target: retire it, or re-aim it at
the rule that replaced it, in the same commit that changed the guard. A canary left in the list as «not applicable»
reports green while covering nothing, and a guard whose only canary was removed this way has no proof it still fires.
The reverse also holds: a change to the tree that silently drops a branch of a guard (a state function rewritten, an
early return losing a key) is a guard regression — the canary that fires on it is working correctly, and the repair
goes into the guard, not into the fixture.
