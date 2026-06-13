# Review workflow & per-source notes

How sources move from a first look to a finalized model, and how the
human-readable record is kept alongside the machine-applicable recommendations.
This is the reference for the aggregation / note-keeping feature. **All four
build-order steps are now implemented** (see "Build order" at the bottom); the
on-disk paths and formats below describe the shipped behavior.

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
  stage3/aggregated.json              ← transient: the composed rec staged for apply
                                        (mojave-apply moves it to applied/)
  applied/<YYYY-MM-DD>__<stem>.json   ← archive of every applied rec (flat; existing
                                        mojave-apply scheme). Stage-3 applies land
                                        here as <date>__aggregated.json
  considered/<YYYY-MM-DD>/<slug>.json ← the reviewer submissions folded into a Stage-3
                                        reconciliation, moved out of submitted/ on apply
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
Status: Stage 3 done · applied <date>

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
### <date> — Stage 3 reconciliation (applied by <admin>) — backup_NNN
Considered: alice (<when>), bob (<when>)

Robustness:
- cl 2 → Non-robust ✓ (changed from Robust) — supported by alice, bob; reason: …
- cl 3 → Robust — (kept) — alice suggested Non-robust ✗; reason: …

Cross-ID / use-in-fit:
- Re-ID 4 → 2 (all epochs) ✓ accepted (alice, bob) — reason: …
- use_in_fit=False — whole epoch 2003.10 ✗ not applied (alice)
<!-- ledger:end -->
```

- **Status** is a free-text line set by the app: `Stage 1 done`,
  `Stage 2 in progress`, `Stage 2 done` (the two Stage-2 save buttons), and
  `Stage 3 done · applied <date>` (the aggregation Apply). Seeded baselines may
  carry a richer `Stage 2 done · baseline by <name> <date>`.
- **Stage 1 / Stage 2** are human prose (seeded from the Google doc; the builder
  updates Stage 2 going forward).
- The **ledger** is append-only, rendered by `aggregate.stage3_ledger_entry`
  (records accepted ✓ and rejected ✗ with reasons + proposers) and written by
  the aggregation Apply — never by hand.

## Lifecycle / stages

- **Stage 1 — brief review.** Quick flags. Seeded from the Google doc. (Future:
  a pure-text entry field in the app for brand-new sources — see TODO.)
- **Stage 2 — baseline model.** One person builds the model others will see
  (pipeline run: `--complex` from `recommend_c.py` / per-window N from the
  Window-N review's `--N_win_file` / cross-IDs), writes the Stage 2 prose,
  then in the app makes their *own* recommendation (robustness + any post-fit
  cross-IDs), **Submits**, and **`mojave-apply`s** it (a no-change one uses the
  no-op fast path). That apply finalizes the `current` model and **concludes
  Stage 2**. Seeded for the ~100 done sources; going forward the builder updates
  Stage 2 via the app during their apply round. Full step-by-step under
  "Stage-2 procedure (builder)" below.
- **Stage 3 — aggregation.** Other reviewers submit suggestions via the app
  (→ `submitted/`). An admin reviews them side-by-side, accepts/rejects each
  edit with a one-line reason, previews the aggregated model, and applies →
  final model + ledger entry.
- **Revisit.** Reopening a finalized source is just another cycle: new
  submissions land in `submitted/`; the next aggregation Apply appends a fresh
  dated ledger block and re-stamps `Status:` to `Stage 3 done · applied <date>`.

## Workflows

- **Seed (one-time).** Export the Google doc as Markdown/text; an importer parses
  the regular per-source structure (`<source>` / `Step 1 …` / `Step 2 …` /
  `Step 3 …` / `DCH <date>` / `uploaded` / `Next…`) and writes
  `notes/<source>.md` with Stages 1–2 populated.
- **Reviewer.** Unchanged: submit via the (hosted) app; their latest submission
  shows live to others. No markdown editing, ever.
- **Builder (Stage 2).** After building a baseline, edits the Stage 2 field in
  the app; saving writes that section of the `.md`. The build itself follows
  the "Stage-2 procedure (builder)" steps below.
- **Admin (aggregation, run from a LOCAL copy of the app where `mojave-apply`
  can write `Results/`).** In the admin-only "🧩 Aggregate reviews (Stage 3)"
  panel: per-cluster **Final** robustness picker (default = majority of reviewer
  votes + current, ties → current) + accept/reject each edit, each with an
  optional reason → tick **"Preview aggregated"** to see the composed model on
  the plots → **Apply aggregated…** (confirm modal → one-click `mojave-apply`).
  Needs `find_clusters.py` reachable (`$MOJAVE_CODE` or `<results-dir>/..`) and
  `$MOJAVE_DATA` for plot regen.

## Stage-2 procedure (builder)

The build steps the Stage-2 baseline owner runs before submitting + applying
their recommendation. As of 2026-06 this replaces the interactive `--editN`
matplotlib session with the app's Window-N review + `find_clusters.py
--N_win_file`, and adds the `recommend_c.py` `--complex` recommender.

1. **Recommend `--complex`.** From the results parent directory, run
   `python ../Nestimate/recommend_c.py <source>` against the existing
   (any-complex) run. The default is a trained classifier; `--rule` prints the
   interpretable slope rule instead. When the recommended `--complex` differs
   from the last run, it prints a ready-to-paste re-run command (old
   `run_string.txt` with `--complex` swapped, `--editN`/`--recalc_IDs`
   stripped, `--show_results` appended).
2. **Re-run if needed.** If the recommended `--complex` differs, re-run
   `find_clusters.py` with the new value (use the printed command). Skip when
   the recommendation matches the current run.
3. **Adjust N per window in the app.** Use the **🔢 Window-N review** panel
   (admin) — the `--editN` replacement — to set Ncluster per window. Choices
   autosave to `<recs>/<source>/nwin_edits/nwin_choices.json`.
4. **Generate the apply command.** Hit **"Generate rerun command"** in the
   panel to get the `find_clusters.py … --N_win_file <…>/nwin_choices.json`
   string (`--recalc_IDs` is added so cross-window labels re-match), and run it
   in the production working directory. Cached `cluster_fits/` make this fast.
5. **Make ID / robustness / use-in-fit edits in the app.** With the model
   rebuilt, make post-fit cross-ID, robustness, and use-in-fit edits in the
   recommendations panel.
6. **Submit.** **Submit** the recommendation (→ `submitted/<slug>.json`).
7. **Apply (Stage-2 baseline apply).** Use **"Generate baseline apply command
   (Stage 2)"** to get the `mojave-apply --recommendation <own JSON>` command
   and run it in a terminal. This finalizes the `current` model, archives the
   submission out of `submitted/`, updates the Stage-2 prose + `Status:`, and
   writes `history.txt`. It writes **no ledger entry** (that is Stage 3 only;
   see below). A no-change apply uses the no-op fast path.

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

How the tooling tells them apart: the **action**, not a status string. A plain
`mojave-apply` of the builder's own submission (CLI, or any non-aggregation
apply) is Stage 2 and writes no ledger entry. The aggregation panel's "Apply
aggregated…" is Stage 3 and is the only path that calls
`stage3_ledger_entry` + `append_ledger`.

## The Stage 3 apply: one event, multiple outputs

A single admin (aggregation) "Apply" (`ui/callbacks._apply_aggregated`) produces:

1. The applied model — via `mojave-apply` run as a synchronous subprocess (new
   CSV backup, regenerated plots, the terse **`history.txt`** entry, and the
   composed rec archived to `applied/<date>__aggregated.json`).
2. The narrative **ledger entry** appended to `notes/<source>.md` (the decision
   snapshot: applied ✓ / deferred ✗ with reasons), and `Status:` bumped to
   `Stage 3 done · applied <date>`.
3. The **considered-submissions archive**: the reviewer submissions folded in
   are moved `submitted/<slug>.json` → `considered/<date>/<slug>.json` — they
   leave "open suggestions" and the `Rec:` dropdown, but the full submissions
   (comments and all) are preserved for later. A later re-submission writes a
   fresh `submitted/<slug>.json` and reappears as open.

`history.txt` and the `.md` ledger are both kept (terse machine log + human
narrative), written from the same Stage 3 apply event. If the `mojave-apply`
subprocess fails, nothing else is changed (it is fail-safe before any
destructive write); the failure output is surfaced in the panel.

## In-app surfacing

The app renders `notes/<source>.md` (Stages 1–2 + ledger) in a panel/tab, plus
the **live open-suggestions** rendered from `submitted/*.json`, so reviewers see
the context and each other's current input while they work. The live
open-suggestions are never written to the file.

## Build order (all implemented)

1. ✅ **Notes substrate** — `notes/` + the `.md` template + a read-only in-app
   notes panel + the Google-doc seed importer (`notes/`, `cli/notes.py`).
2. ✅ **Builder Stage 2 editor** — admin app field → `.md` Stage 2 section, with
   two save buttons setting `Stage 2 in progress` / `Stage 2 done`.
3. ✅ **Aggregation UI** (admin/local) — side-by-side accept/reject + a live
   "Preview aggregated" toggle (`recommendations/aggregate.py`, `ui/aggregation.py`).
4. ✅ **Apply integration** — "Apply aggregated…" → confirm modal → one-click
   `mojave-apply` subprocess → ledger write + considered-submissions archive
   (`ui/callbacks._apply_aggregated`, `aggregate.stage3_ledger_entry`,
   `store.archive_considered_submissions`).

## TODO / future

- **Stage 1 entry for brand-new sources:** a pure-text field in the app to author
  Stage 1 when a source first appears (not needed yet).
- Optionally prefer CSV-borne uncertainty columns over app-computed ones (see
  `docs/uncertainty_estimates.md`) — unrelated, noted for the same future pass.
