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
REPO_DIR="/Users/homand/Library/CloudStorage/Dropbox/Research/Clustering_CC/Clustering_Review"
REMOTE="homand@74.140.113.72"
REMOTE_BASE="/home/homand/mojave-review/data"
SSH="ssh -i /Users/homand/.ssh/id_ed25519 -o StrictHostKeyChecking=no -p 2121"
EXCLUDE_FILE="server_sync/server_update_exclude.txt"
UNISON_PROFILE="mojave-recs"

# === Mode ===
MODE="${1:-preview}"
case "$MODE" in
  preview) DRY="--dry-run"; UNISON_FLAGS="" ;;
  run)     DRY="";          UNISON_FLAGS="" ;;
  auto)    DRY="";          UNISON_FLAGS="-batch -auto -prefer newer" ;;
  *) echo "usage: $0 [preview|run|auto]" >&2; exit 1 ;;
esac

echo "=== sync_server.sh ($MODE) ==="
cd "$REPO_DIR"

# === 1) one-way MIRROR (delete OK): notes + Results, workstation -> server ===
mirror() { rsync -az --delete --timeout=300 -e "$SSH" $DRY "$@"; }

echo "--> notes/  ->  server"
mirror ./notes/ "$REMOTE:$REMOTE_BASE/notes/"

echo "--> Results/  ->  server (with exclusions)"
mirror --exclude-from="$EXCLUDE_FILE" \
       ./Results/ "$REMOTE:$REMOTE_BASE/Results/"

# === 2) bidirectional MERGE: recommendations (Unison) ===
echo "--> recommendations/  <->  server (unison)"
if [[ "$MODE" == "preview" ]]; then
  echo "    (interactive Unison: review the listed changes, then accept or quit)"
fi
unison "$UNISON_PROFILE" $UNISON_FLAGS

echo "=== done ($MODE) ==="
