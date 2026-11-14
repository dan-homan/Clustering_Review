#!/usr/bin/env bash
#
# Sync MOJAVE review data between this workstation and the university server.
#
#   notes/        workstation -> server   (one-way rsync mirror, --delete OK)
#   Results/      workstation -> server   (one-way rsync mirror, --delete OK)
#   recommendations/  workstation <-> server  (bidirectional, Unison)
#
# Unison (not rsync) drives the recommendations leg because it keeps a state
# archive on each side: it can tell "created here" from "deleted there", and
# propagates the Stage-3 rename (submitted/x.json -> considered/<date>/x.json)
# as a real move instead of resurrecting the old file. The Unison profile is
# ~/.unison/mojave-recs.prf.
#
# Usage:
#   ./sync_server.sh PROJECT            # preview: rsync --dry-run + interactive Unison
#   ./sync_server.sh PROJECT run        # apply:   rsync for real  + interactive Unison
#   ./sync_server.sh PROJECT auto       # unattended: rsync real + Unison -batch -auto
#
# PROJECT is the server-side project directory name (e.g. MOJAVE, BK224) under
# REMOTE_DATA. It is MANDATORY and has no default, on purpose: the server now
# keeps one directory per project, and silently falling back to the old flat
# layout would push this project's data into a stale tree -- and, worse, point
# Unison at a root its archive no longer matches. See "Multi-project" below.
#
# In preview/run, Unison lists every proposed change and asks before doing
# anything, so nothing happens you didn't approve. Use `auto` only once you
# trust the sync (it resolves conflicts newer-wins; overwritten files are kept
# under ~/.unison/backups/mojave-recs).

set -euo pipefail

# === Configuration ===
# DATA_DIR is the directory that holds notes/, recommendations/, Results/.
# Defaults to the current working directory, so just run this script from your
# production data dir. Override with:  DATA_DIR=/path/to/data ./sync_server.sh
DATA_DIR="${DATA_DIR:-$PWD}"

# The rsync exclude list ships next to this script, so resolve it relative to
# the script itself — independent of where you run from.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXCLUDE_FILE="$SCRIPT_DIR/server_update_exclude.txt"

REMOTE="${REMOTE:-homand@74.140.113.72}"
# The server's data root — the directory holding one subdirectory per project.
# On webby /home/homand/mojave-review is a symlink to the vhost directory
# under /var/www/html/, so this short path is the stable way to name it.
# Overridable (REMOTE / REMOTE_DATA / SSH) so an alternate host or a test
# harness needs no edit to this file.
REMOTE_DATA="${REMOTE_DATA:-/home/homand/mojave-review/data}"
SSH="${SSH:-ssh -i /Users/homand/.ssh/id_ed25519 -o StrictHostKeyChecking=no -p 2121}"

# === Project (mandatory) ===
# Multi-project: the server keeps <REMOTE_DATA>/<PROJECT>/{Results,
# recommendations,notes}. One workstation project dir maps to one server
# project dir, and both the rsync targets and the Unison profile are derived
# from it below.
PROJECT="${PROJECT:-${1:-}}"
if [[ -z "$PROJECT" || "$PROJECT" == "preview" || "$PROJECT" == "run" \
      || "$PROJECT" == "auto" ]]; then
  cat >&2 <<EOM
usage: $0 PROJECT [preview|run|auto]

PROJECT is the server-side project directory under
  $REMOTE:$REMOTE_DATA
e.g.  MOJAVE  or  BK224

It is mandatory. The server keeps one directory per project, so guessing
would push this project's data into the wrong place -- and would point
Unison at a root its state archive no longer matches, which in 'auto' mode
can propagate deletions.

Run from the workstation project dir (the one holding notes/,
recommendations/, Results/), e.g.:
  cd ~/Dropbox/Research/Clustering_CC/MOJAVE_1996.00-2026.00
  $0 MOJAVE            # preview
  $0 MOJAVE run        # apply
EOM
  exit 1
fi

REMOTE_BASE="$REMOTE_DATA/$PROJECT"

# Unison profile, one per project: its REMOTE root must name this project's
# recommendations dir. The LOCAL root is relative ("recommendations"),
# resolved against DATA_DIR because we cd there below.
UNISON_PROFILE="${UNISON_PROFILE:-mojave-recs-$PROJECT}"

# === Mode ===
MODE="${2:-preview}"
case "$MODE" in
  preview) DRY="--dry-run"; UNISON_FLAGS="" ;;
  run)     DRY="";          UNISON_FLAGS="" ;;
  auto)    DRY="";          UNISON_FLAGS="-batch -auto -prefer newer" ;;
  *) echo "usage: $0 [preview|run|auto]" >&2; exit 1 ;;
esac

echo "=== sync_server.sh ($MODE) ==="
echo "    project:   $PROJECT"
echo "    local:     $DATA_DIR"
echo "    remote:    $REMOTE:$REMOTE_BASE"
echo "    unison:    $UNISON_PROFILE"
cd "$DATA_DIR"

# Fail fast if we're not actually in the data dir (parent of the three trees).
for d in notes recommendations Results; do
  [[ -d "$d" ]] || { echo "ERROR: '$d/' not found in $DATA_DIR" >&2
                     echo "Run this from the dir holding notes/ recommendations/ Results/," >&2
                     echo "or set DATA_DIR=/path/to/data." >&2; exit 1; }
done

# === 0) remote project root must already exist ===
# Refuse rather than create it. rsync would happily make
# <REMOTE_DATA>/<typo>/Results and report success, leaving a stale tree the
# app never reads and this project's data not where it belongs. And a
# mistyped project means the Unison root below is wrong too, which is the
# dangerous one: its archive would read an empty/absent remote as "everything
# was deleted there" and, in auto mode, mirror those deletions onto the
# workstation.
#
# The app itself logs the project roots it mounted at startup ("mounted
# project <name> at /<name>/ (root=...)"), so that journal line is the
# authority for what PROJECT should be.
# NOTE: stderr is deliberately NOT redirected here. ssh writes its password
# and key-passphrase prompts to stderr, so swallowing it makes an
# authentication prompt invisible and the script merely LOOKS hung while it
# waits for input you cannot see. Noise is the lesser evil.
if $SSH "$REMOTE" "test -d '$REMOTE_BASE'"; then
  :   # remote project root is there — carry on
elif $SSH "$REMOTE" true; then
  # We can reach the host, so it is the path that is wrong.
  cat >&2 <<EOM
ERROR: remote project root does not exist:
  $REMOTE:$REMOTE_BASE

Either PROJECT is wrong, or this project has not been set up on the server
yet. The app's own startup log is the authority for the project name:
  $SSH $REMOTE "journalctl -u mojave -n 30 --no-pager" | grep 'mounted project'

To create it (first sync of a new project):
  $SSH $REMOTE 'mkdir -p $REMOTE_BASE'
and remember the two support files, which are NOT synced by this script:
  scp source_run_param.csv astrometry_mojave.dat $REMOTE:$REMOTE_BASE/
EOM
  exit 1
else
  cat >&2 <<EOM
ERROR: cannot ssh to $REMOTE

The project-root check could not run, so nothing was synced. Check the host
is reachable and your key is available:
  $SSH $REMOTE true
  ssh-add -l                 # is the agent holding your key?
  ssh-add --apple-use-keychain ~/.ssh/id_ed25519
EOM
  exit 1
fi

# === 1) one-way MIRROR (delete OK): notes + Results, workstation -> server ===
# --itemize-changes is what makes `preview` actually preview. Without it a
# --dry-run prints NOTHING (no -v, no --stats), so the rsync legs were silent
# in both modes and you learned nothing from them. -i lists one line per
# CHANGED item only, so it stays quiet on a routine sync and gets loud exactly
# when you want it to -- including "*deleting ..." lines, which are the ones
# worth reading.
# OS junk is excluded on EVERY leg, not just Results/. Only the Results leg
# passed --exclude-from, so notes/ and recommendations/_admin/ had been pushing
# macOS AppleDouble files ("._foo.md") and .DS_Store to the server for months;
# they then showed up as "*deleting ._*" once --itemize-changes made the plan
# visible. Harmless metadata either way, but it has no business on the server.
# The heavy Results-only patterns (PDF/MP4 renders, cluster_fits/) stay in
# server_update_exclude.txt, which only that leg passes.
_JUNK=(--exclude='.DS_Store' --exclude='._*' --exclude='*.swp'
       --exclude='Thumbs.db' --exclude='*.part' --exclude='*.tmp')

mirror() { rsync -az --delete --itemize-changes --timeout=300 \
                 "${_JUNK[@]}" -e "$SSH" $DRY "$@"; }

echo "--> notes/  ->  server"
mirror ./notes/ "$REMOTE:$REMOTE_BASE/notes/"

echo "--> Results/  ->  server (with exclusions)"
mirror --exclude-from="$EXCLUDE_FILE" \
       ./Results/ "$REMOTE:$REMOTE_BASE/Results/"

# === 2) bidirectional MERGE: recommendations (Unison) ===
# Unison ignores recommendations/_admin (see mojave-recs.prf) — it's pushed
# one-way below. Everything else under recommendations/ (reviewer submissions,
# considered/, applied/, notes ledgers) merges bidirectionally as before.
echo "--> recommendations/  <->  server (unison; _admin/ excluded)"
if [[ "$MODE" == "preview" ]]; then
  # Be blunt about this: the rsync legs above were --dry-run, but Unison has
  # no dry-run mode. Its interactive prompt IS the review gate, so this leg
  # is live and WILL apply whatever you approve. Answering 'n' declines.
  echo "    NOTE: the rsync legs above were a dry run; this Unison leg is LIVE."
  echo "    It lists every proposed change and asks first — answer 'n' to"
  echo "    decline, or 'y' to apply. Nothing happens without your answer."
fi
unison "$UNISON_PROFILE" $UNISON_FLAGS

# === 3) one-way push: recommendations/_admin/ workstation -> server ===
# Assignments / roster / target dates / manual credits are authored on the
# workstation and must never be overwritten by the server's copy. backups/ is
# a local-only undo trail, so it's excluded. --delete keeps the server's
# _admin in sync with the workstation (safe: the workstation is the sole
# author of _admin).
echo "--> recommendations/_admin/  ->  server (one-way, excl backups/)"
if [[ -d ./recommendations/_admin ]]; then
  mirror --exclude 'backups/' \
         ./recommendations/_admin/ "$REMOTE:$REMOTE_BASE/recommendations/_admin/"
else
  echo "    (no recommendations/_admin/ yet — skipping)"
fi

echo "=== done ($MODE) ==="
