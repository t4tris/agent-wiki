---
name: telegram-archive-pipeline
description: "Telegram dump to wiki pipeline: route pointer, review integrity, intake order."
version: 1.0.0
license: MIT
platforms: [linux, macos, windows]
---

# Telegram archive pipeline

The route decision lives in the telegram-data-export skill and is not re-decided here.
The stage order lives in the tools README stage table and the wave order; this skill owns
only what neither states: the review checkpoint integrity and the intake handoff.

Order of work: export, transcribe, review, promote, wave. The intake menu (`tasks.py telegram`)
chooses whole-dump versus selection procedure; the wave takes over after promotion.

## Review checkpoint integrity

The review checkpoint is a recommendation, not a decision: the exported selection file is the
approval input, and rows may be re-ticked against advice. When the review surface is a page
that a generator re-renders, three silent failures follow unless handled:

- Persist the tick state outside the page markup, because the generator re-renders the file.
  A checklist whose state lives only in the page loses manual ticks on reload, and since
  a script regenerates the file, the loss is silent and reads as vanished work. Save on every
  change, restore on load, and keep one reset that restores recommendations without touching
  what the owner typed.
- Stamp the build with a signature of the recommendation set, or an updated checklist looks
  unchanged. Stored ticks otherwise win over freshly computed recommendations: after new advice
  the owner reopens old marks, reads them as nothing changed, and reviews nothing. Hash the
  recommended state of every row, store it with the ticks, and on mismatch re-apply the new
  defaults while migrating the typed context — then say so in the page header. Silent either
  way is the failure: silent loss of ticks or silent masking of new advice.
- Give every archive its own export filename, passed in at render time: two checklists sharing
  one name means one export silently overwrites the other. Land the exported selection in the
  versioned selection directory, never inside the pipeline working area: ticks and typed context
  are the one product of review that cannot be regenerated.

## What this skill does not own

Transcription engines and their choice, image descriptions, link summaries, the selection
procedure itself, cards and pages — all decided by shipped tools and wave documents, and not
repeated here. Selection stays the owner's decision; this skill never selects material.
