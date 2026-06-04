# Review workflow & per-source notes

How sources move from a first look to a finalized model, and how the
human-readable record is kept alongside the machine-applicable recommendations.
This is the reference for the aggregation / note-keeping feature; it is the
*intended* design — pieces are built incrementally (see "Build order").

## Two layers

- **Structured layer (machine-applicable).** The recommendation JSON files and
  `mojave-apply`. This is what the app produces and what gets applied to
  `Results/`.
- **Narrative layer (human lab-notebook).** `notes/<source>.md`, one file per
  source, alongside `recommendations/`. The durable, human-readable story.

**Core principle: the notes file holds only *durable* content.** In-flight
suggestions are never written into it. The volatile "what are people currently
suggesting" view is rendered live by the app from the submitted JSON; only the
**decision snapshot** (taken when the admin aggregates + applies) is persisted
to the notes ledger. This is what keeps resubmissions from cluttering the record
while still giving a permanent trace of what was decided and why.

## On-disk layout (shared Drive, alongside `recommendations/`)

```
notes/
  <source>.md                         ← human lab-notebook (durable only)
recommendations/<source>/
  current/<slug>.json                 ← private autosaved draft
  submitted/<slug>.json               ← live suggestion (mutable; resubmit overwrites)
  applied/<YYYY-MM-DD>/               ← decision-time archive (one dir per apply)
      aggregated.json                 ← the composed recommendation that was applied
      considered/<slug>.json          ← frozen copy of each full submission considered
Results/<source>_<emin>-<emax>/
  history.txt                         ← terse machine log (existing)
```

`<source>` here is the bare designation (e.g. `0003-066u`), matching the
recommendations tree.

## `notes/<source>.md` template

Thematic on top (current state), append-only ledger at the bottom (history).
Chronology lives **only** in the ledger so it doesn't muddy the top. Sections
are delimited by HTML-comment markers so tooling can update one section without
disturbing hand-written prose:

```markdown
# <source>  (<emin>–<emax>)
Status: <stage|finalized|revisiting> · baseline by <name> <date> · last applied <date>

## Stage 1 — Brief review
<!-- stage1:begin -->
…flags: core ID, jet direction, maxGap…
<!-- stage1:end -->

## Stage 2 — Baseline model
<!-- stage2:begin -->
…method: --complex/--editN, cross-IDs done; robustness recommendations…
<!-- stage2:end -->

## Decisions & applied history
<!-- ledger:begin -->
### <date> — Aggregated & applied by <admin>  (→ backup_NNN)
Considered (frozen): see recommendations/<source>/applied/<date>/considered/
  • <slug>: <one-line summary>
Applied:
  ✓ <edit> — <reason>
Deferred:
  ✗ <edit> (<slug>) — <reason>
<!-- ledger:end -->
```

- **Stage 1 / Stage 2** are human prose (seeded from the Google doc; the builder
  updates Stage 2 going forward).
- The **ledger** is append-only and written by the apply action — never by hand.

## Lifecycle / stages

- **Stage 1 — brief review.** Quick flags. Seeded from the Google doc. (Future:
  a pure-text entry field in the app for brand-new sources — see TODO.)
- **Stage 2 — baseline model.** One person builds the model others will see
  (pipeline run: `--complex` / `--editN` / cross-IDs), writes the Stage 2 prose,
  then in the app makes their *own* recommendation (robustness + any post-fit
  cross-IDs), **Submits**, and **`mojave-apply`s** it (a no-change one uses the
  no-op fast path). That apply finalizes the `current` model and **concludes
  Stage 2**. Seeded for the ~100 done sources; going forward the builder updates
  Stage 2 via the app during their apply round.
- **Stage 3 — aggregation.** Other reviewers submit suggestions via the app
  (→ `submitted/`). An admin reviews them side-by-side, accepts/rejects each
  edit with a one-line reason, previews the aggregated model, and applies →
  final model + ledger entry.
- **Revisit.** Reopening a finalized source is just another cycle: new
  submissions land in `submitted/`; the next aggregation appends a fresh dated
  ledger block. `Status:` flips to `revisiting`, then back to `finalized`.

## Workflows

- **Seed (one-time).** Export the Google doc as Markdown/text; an importer parses
  the regular per-source structure (`<source>` / `Step 1 …` / `Step 2 …` /
  `Step 3 …` / `DCH <date>` / `uploaded` / `Next…`) and writes
  `notes/<source>.md` with Stages 1–2 populated.
- **Reviewer.** Unchanged: submit via the (hosted) app; their latest submission
  shows live to others. No markdown editing, ever.
- **Builder (Stage 2).** After building a baseline, edits the Stage 2 field in
  the app; saving writes that section of the `.md`.
- **Admin (aggregation, run from a LOCAL copy of the app where `mojave-apply`
  can write `Results/`).** Side-by-side reviewer recs → accept/reject each edit
  with a reason → preview via the existing "Visualize" path → **Apply**.

## Two kinds of apply — the ledger is Stage 3 only

Both Stage 2 and Stage 3 end in a `mojave-apply`, but they are recorded
**differently** so the "Decisions & applied history" ledger stays a pure log of
the multi-reviewer reconciliation (decision: option A):

- **Stage 2 — baseline apply** (the builder applying their *own* single
  submission to finalize the baseline). Recorded in the `Status:` line
  ("baseline by <name>, applied <date>"), the **Stage 2** prose section, and
  `history.txt`. It does **NOT** write a ledger entry. Apply archives the
  builder's submission out of `submitted/`, so it never resurfaces as a Stage 3
  open suggestion.
- **Stage 3 — aggregation apply** (the admin reconciling *multiple* reviewers'
  submissions, via the aggregation UI). This is the **only** thing that writes
  the ledger.

How the tooling tells them apart: the source's lifecycle stage (the `Status:`
line). A plain `mojave-apply` of the builder's own submission, before the source
is opened for review, is Stage 2. The aggregation UI's Apply, on a source that
is "awaiting review", is Stage 3.

## The Stage 3 apply: one event, multiple outputs

A single admin (aggregation) "Apply" produces:

1. The applied model — via `mojave-apply` (new CSV backup, regenerated plots,
   and the existing terse **`history.txt`** entry).
2. The narrative **ledger entry** appended to `notes/<source>.md` (the decision
   snapshot: applied ✓ / deferred ✗ with reasons).
3. The **decision-time archive** under `recommendations/<source>/applied/<date>/`:
   the composed `aggregated.json` plus a frozen `considered/<slug>.json` copy of
   each full submission (so reviewer comments can be revisited later).

`history.txt` and the `.md` ledger are both kept (terse machine log + human
narrative), written from the same Stage 3 apply event.

## In-app surfacing

The app renders `notes/<source>.md` (Stages 1–2 + ledger) in a panel/tab, plus
the **live open-suggestions** rendered from `submitted/*.json`, so reviewers see
the context and each other's current input while they work. The live
open-suggestions are never written to the file.

## Build order (incremental; each independently useful)

1. **Notes substrate** — `notes/` + the `.md` template + a read-only in-app
   notes panel + the Google-doc seed importer. *(foundational, no risk to the
   apply path)*
2. **Builder Stage 2 editor** — app field → `.md` Stage 2 section.
3. **Aggregation UI** (admin/local) — side-by-side accept/reject + preview.
4. **Apply integration** — aggregation "Apply" → `mojave-apply` + ledger write +
   considered-submissions archive.

## TODO / future

- **Stage 1 entry for brand-new sources:** a pure-text field in the app to author
  Stage 1 when a source first appears (not needed yet).
- Optionally prefer CSV-borne uncertainty columns over app-computed ones (see
  `docs/uncertainty_estimates.md`) — unrelated, noted for the same future pass.
