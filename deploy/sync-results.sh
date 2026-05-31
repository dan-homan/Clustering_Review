#!/usr/bin/env bash
# mojave-review-sync — push the Drive-mirrored Results/ tree from the
# sender machine to the mojave-review host.
#
# Runs on the SENDER machine (the one with the Google Drive desktop
# mirror). Sync direction is strictly one-way Drive → laptop; the
# laptop's recommendations/, fits_cache/, and logs/ are never touched.
#
# Configuration lives in a shell-sourced file at
#   $MOJAVE_SYNC_CONF       (env override)  or
#   $XDG_CONFIG_HOME/mojave-results-sync.conf  or
#   ~/.config/mojave-results-sync.conf
# Required keys: SOURCE_DIR, TARGET_USER, TARGET_HOST, TARGET_DIR.
# Optional: MAX_DELETE (default 1000), EXTRA_RSYNC_OPTS.
#
# Exit codes
#   0  sync succeeded (including a no-op "nothing to do" run)
#   2  config / preflight problem (config missing, key unset, source
#      gone, SSH unreachable). The systemd timer treats this as a
#      transient failure and tries again on the next schedule.
#   *  rsync's own exit code on transfer failure (see rsync(1)).

set -euo pipefail

# ---------------------------------------------------------------------
# Locate + load config
# ---------------------------------------------------------------------

CONF="${MOJAVE_SYNC_CONF:-${XDG_CONFIG_HOME:-$HOME/.config}/mojave-results-sync.conf}"

if [[ ! -f "$CONF" ]]; then
    echo "mojave-review-sync: config not found at $CONF" >&2
    echo "  Copy deploy/mojave-results-sync.conf.example into place" >&2
    echo "  and edit it before re-running." >&2
    exit 2
fi

# shellcheck disable=SC1090
source "$CONF"

# ---------------------------------------------------------------------
# Validate
# ---------------------------------------------------------------------

for var in SOURCE_DIR TARGET_USER TARGET_HOST TARGET_DIR; do
    if [[ -z "${!var:-}" ]]; then
        echo "mojave-review-sync: $var is required but unset in $CONF" >&2
        exit 2
    fi
done

if [[ ! -d "$SOURCE_DIR" ]]; then
    echo "mojave-review-sync: SOURCE_DIR not a directory: $SOURCE_DIR" >&2
    echo "  Is Google Drive desktop running and mounted?" >&2
    exit 2
fi

# Strip any trailing slash so we can append our own consistently. The
# trailing slash on the rsync source is load-bearing (means "contents
# of"), so we always want exactly one.
SOURCE_DIR="${SOURCE_DIR%/}"
TARGET_DIR="${TARGET_DIR%/}"

MAX_DELETE="${MAX_DELETE:-1000}"

# ---------------------------------------------------------------------
# Preflight SSH — fail fast with a clear error instead of letting rsync
# surface it after we've already partially transferred metadata.
# BatchMode=yes disables interactive prompts so we don't hang under
# systemd; ConnectTimeout caps the wait so a network blackhole doesn't
# stall the timer.
# ---------------------------------------------------------------------

if ! ssh -o BatchMode=yes -o ConnectTimeout=10 \
        "$TARGET_USER@$TARGET_HOST" true 2>/dev/null; then
    echo "mojave-review-sync: SSH to $TARGET_USER@$TARGET_HOST failed." >&2
    echo "  Check that key-based SSH is set up and the host is reachable." >&2
    exit 2
fi

# ---------------------------------------------------------------------
# rsync
# ---------------------------------------------------------------------

RSYNC_OPTS=(
    --archive                  # -rlptgoD: perms, times, symlinks
    --human-readable
    --partial                  # resume an interrupted transfer cleanly
    --info=stats2,progress2    # one line per file + final summary
    --delete                   # mirror upstream deletions
    --exclude='.DS_Store'      # macOS Finder turds
    --exclude='._*'            # macOS resource forks
    --exclude='~$*'            # MS Office lock files
    --exclude='.gdshortcut*'   # Google Drive shortcut placeholders
    --exclude='*.tmp'
    --exclude='*.partial'
)

# MAX_DELETE=0 (or empty after the default) disables the safety brake.
# Any positive integer caps deletions per run; rsync exits non-zero if
# the cap is exceeded and the run leaves the laptop unchanged.
if [[ "$MAX_DELETE" -gt 0 ]]; then
    RSYNC_OPTS+=("--max-delete=$MAX_DELETE")
fi

# Hook for one-off flags from the config without editing the script.
if [[ -n "${EXTRA_RSYNC_OPTS:-}" ]]; then
    # shellcheck disable=SC2206
    RSYNC_OPTS+=( $EXTRA_RSYNC_OPTS )
fi

# Allow callers to pass extra flags through, e.g.
#   sync-results.sh --dry-run
RSYNC_OPTS+=( "$@" )

echo "mojave-review-sync:"
echo "  source: $SOURCE_DIR/"
echo "  target: $TARGET_USER@$TARGET_HOST:$TARGET_DIR/"
echo "  max-delete: $MAX_DELETE"

exec rsync "${RSYNC_OPTS[@]}" \
    -e ssh \
    "$SOURCE_DIR/" \
    "$TARGET_USER@$TARGET_HOST:$TARGET_DIR/"
