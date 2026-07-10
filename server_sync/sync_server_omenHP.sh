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
#   ./sync_server.sh            # preview: rsync --dry-run + interactive Unison
#   ./sync_server.sh run        # apply:   rsync for real  + interactive Unison
#   ./sync_server.sh auto       # unattended: rsync real + Unison -batch -auto
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

REMOTE="homand@74.140.113.72"
REMOTE_BASE="/home/homand/mojave-review/data"
SSH="ssh -i /Users/homand/.ssh/id_ed25519 -o StrictHostKeyChecking=no -p 2121"
# Unison profile (~/.unison/mojave-recs.prf). Its LOCAL root is relative
# ("recommendations"), resolved against DATA_DIR because we cd there below.
UNISON_PROFILE="mojave-recs"

# === Mode ===
MODE="${1:-preview}"
case "$MODE" in
  preview) DRY="--dry-run"; UNISON_FLAGS="" ;;
  run)     DRY="";          UNISON_FLAGS="" ;;
  auto)    DRY="";          UNISON_FLAGS="-batch -auto -prefer newer" ;;
  *) echo "usage: $0 [preview|run|auto]" >&2; exit 1 ;;
esac

echo "=== sync_server.sh ($MODE) — data dir: $DATA_DIR ==="
cd "$DATA_DIR"

# Fail fast if we're not actually in the data dir (parent of the three trees).
for d in notes recommendations Results; do
  [[ -d "$d" ]] || { echo "ERROR: '$d/' not found in $DATA_DIR" >&2
                     echo "Run this from the dir holding notes/ recommendations/ Results/," >&2
                     echo "or set DATA_DIR=/path/to/data." >&2; exit 1; }
done

# === 1) one-way MIRROR (delete OK): notes + Results, workstation -> server ===
mirror() { rsync -az --delete --timeout=300 -e "$SSH" $DRY "$@"; }

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
  echo "    (interactive Unison: review the listed changes, then accept or quit)"
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
