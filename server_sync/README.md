# `server_sync/` — workstation ⇄ server data sync

Tools for keeping the MOJAVE review data in sync between this **workstation**
and the **university server** that hosts the web app. Three data trees are
handled, each with the right sync model:

| Tree | Direction | Tool | `--delete`? |
|---|---|---|---|
| `notes/` | workstation → server | rsync mirror | yes |
| `Results/` | workstation → server | rsync mirror | yes |
| `recommendations/` | workstation ⇄ server | **Unison** | no |

`notes/` and `Results/` are authored on the workstation and pushed out, so a
one-way mirror (with `--delete`) is correct. `recommendations/` is written on
**both** sides — reviewers submit on the server, the admin aggregates/applies
locally — so it needs a true two-way sync.

## Why Unison for `recommendations/` (and not rsync `--delete`)

rsync has no memory of a previous state, so it cannot tell *"this file is new
on side A"* from *"this file was deleted on side B."* Run two-way with
`--delete` and each pass tries to mirror the other, deleting the far side's new
files — it removes recommendations you wanted to keep.

Unison keeps a **state archive on each side**, so it:

- distinguishes *created here* from *deleted there*, and only deletes a file
  when it can prove the other side deleted it;
- propagates the Stage-3 rename `submitted/<slug>.json →
  considered/<date>/<slug>.json` as a real **move**, instead of resurrecting
  the moved-away file the way stateless rsync would;
- **pauses on genuine conflicts** (the same file changed on both sides since
  the last sync) instead of silently picking one.

## Files in this directory

| File | Role |
|---|---|
| `sync_server.sh` | The wrapper that runs all three legs. **This is what you run.** |
| `server_update_exclude.txt` | rsync `--exclude-from` patterns for the `Results/` mirror (junk by default; commented examples for skipping heavy PDF/MP4 renders and `backups/`). |
| `mojave-recs.prf` | **Reference copy** of the Unison profile. The *active* profile must live at `~/.unison/mojave-recs.prf` (see setup). |
| `README.md` | This file. |

## One-time setup

1. **Install Unison on both ends, with compatible versions.** Unison's wire
   protocol is version-tied, so match the major/minor as closely as you can.

   ```bash
   # macOS workstation
   brew install unison
   unison -version

   # server — check it's installed and compatible
   ssh -i ~/.ssh/id_ed25519 -p 2121 homand@74.140.113.72 'unison -version'
   ```

   If the server command says "not found", install it
   (`sudo apt-get install unison`). If `unison` lands somewhere off the login
   PATH, set `servercmd = /full/path/to/unison` in the profile.

2. **Install the active Unison profile.** `mojave-recs.prf` here is only a
   reference copy; Unison loads profiles from `~/.unison/`:

   ```bash
   cp server_sync/mojave-recs.prf ~/.unison/mojave-recs.prf
   mkdir -p ~/.unison/backups/mojave-recs
   ```

   Keep the two copies in step if you edit either (this one is the
   version-controlled record).

## Usage

Run from the **repo root** (the script `cd`s to the repo internally, so it
works regardless of where you invoke it from):

```bash
./server_sync/sync_server.sh            # preview  (default)
./server_sync/sync_server.sh run        # apply
./server_sync/sync_server.sh auto       # unattended
```

| Mode | rsync legs | Unison leg |
|---|---|---|
| `preview` *(default)* | `--dry-run` — shows what *would* change | interactive — lists every proposed change and asks before doing anything |
| `run` | applies for real | interactive |
| `auto` | applies for real | `-batch -auto -prefer newer` — no prompts, newer file wins |

In `preview`/`run`, Unison won't touch anything you don't approve — review the
listed changes and accept, or quit to abort. `auto` is for cron/unattended use
**only once you trust the sync**; it resolves conflicts newer-wins, and every
overwritten or deleted file is still kept under
`~/.unison/backups/mojave-recs/` (last 5 versions).

## First real run

Do the very first sync **interactively** so you can eyeball the plan —
especially any deletions — before committing to it:

```bash
./server_sync/sync_server.sh preview     # rsync dry-run + interactive Unison
# looks right? then:
./server_sync/sync_server.sh run
```

With no archive yet, Unison reconciles both sides from scratch on that first
run; afterwards the archive exists and subsequent runs are fast and quiet.
Only switch to `auto` once you're comfortable with how it behaves.

## Conflict handling & safety net

- With the profile's `batch = false` and `prefer` left commented, a file
  changed on **both** sides since the last sync makes Unison stop and ask which
  way to go — the right behaviour for reviewer JSONs.
- `auto` mode passes `-prefer newer` on the command line, so unattended runs
  resolve conflicts by modification time.
- Either way, `backup = Name *` in the profile means the losing copy is saved
  under `~/.unison/backups/mojave-recs/`, so a wrong conflict call is
  recoverable.

## Gotchas

- **Never reintroduce `--delete` on the `recommendations/` leg.** It is the
  exact bug Unison exists to avoid here. `--delete` belongs only on the
  one-way `notes/` and `Results/` mirrors.
- **Keep `server_update_exclude.txt` paths in mind.** `sync_server.sh`
  references it as `server_sync/server_update_exclude.txt` (relative to the
  repo root it `cd`s into). If you move it, update `EXCLUDE_FILE` in the
  script.
- **Dropbox overlap.** `recommendations/` lives inside a Dropbox CloudStorage
  path, so Dropbox is *also* syncing it to Dropbox's cloud. The Unison ⇄ server
  channel is independent of that. It's fine for a single workstation, but if
  other machines also edit these files via Dropbox you have multiple masters —
  treat the server as the single authority for reviewer submissions to avoid
  surprises.
- **Server-side paths** are configured at the top of `sync_server.sh`
  (`REMOTE`, `REMOTE_BASE`, `SSH`) and in the roots/`sshargs` of the profile.
  Change them in **both** places if the host, port, key, or layout changes.
