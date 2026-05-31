# Deploying mojave-review on this laptop (Path A: HTTP-only, no nginx)

This is the laptop-staging deployment. The university-host migration
adds nginx + TLS + URL prefix in one coherent step later; nothing here
needs to anticipate that.

Architecture reference: [`../docs/deployment_phase2.md`](../docs/deployment_phase2.md).

```
LAN reviewer
    │ http://<laptop-ip>:8050/?token=...
    ▼
gunicorn (systemd --user, 2 workers)
    │
    ├── reads:  ~/mojave-review/data/Results/
    ├── writes: ~/mojave-review/data/recommendations/
    ├── caches: ~/mojave-review/data/fits_cache/
    └── logs:   ~/mojave-review/data/logs/
```

No reverse proxy. Gunicorn binds directly to `0.0.0.0:8050`.

## Bring-up sequence

Each step is independently verifiable; do them in order the first time.

### 1. On-disk layout

```bash
mkdir -p ~/mojave-review/config
mkdir -p ~/mojave-review/data/{Results,recommendations,fits_cache,logs}
```

`Results/` will be populated by chunk 9 (rsync from your other machine).
Empty is fine for the gunicorn smoke test — the loader returns an empty
source list and the UI renders with no sources to pick.

### 2. Install the package with the `server` extra

```bash
cd ~/Clustering_Review/mojave_review
pip install -e .[server]
```

The `[server]` extra pulls `gunicorn`. Verify:

```bash
which gunicorn          # should be inside your anaconda3 env
gunicorn --version
```

### 3. Config file

```bash
cp ~/Clustering_Review/deploy/config.yaml.example \
   ~/mojave-review/config/config.yaml
```

Open it, change `admin_contact`, and confirm every path matches the
layout from step 1. `cookie_secure: false` MUST stay false here —
browsers refuse to send `Secure` cookies over plain HTTP.

### 4. Tokens

The `tokens` CLI prints a ready-to-bookmark URL when you pass
`--base-url`. Use the laptop's LAN IP (`ip -4 addr show`) — clients on
other machines need to be able to reach it.

```bash
LAN_URL=http://192.168.1.23:8050/      # ← your laptop's actual LAN IP

mojave-review-tokens \
    --tokens-file ~/mojave-review/config/tokens.yaml \
    add homand --base-url $LAN_URL

mojave-review-tokens \
    --tokens-file ~/mojave-review/config/tokens.yaml \
    list
```

The `add` output ends with `Bookmark URL: http://192.168.1.23:8050/?token=...`
— that's the URL you email to the reviewer. Repeat `add <user>` for
each reviewer once the deploy is verified.

If you ever lose a reviewer's URL: `mojave-review-tokens ... url <user>
--base-url $LAN_URL` reprints it from the on-disk token (no rotation).

### 5. WSGI smoke test (before wiring systemd)

Verify gunicorn comes up cleanly before handing it to systemd. Easier
to diagnose a config typo from a foreground crash than from
`journalctl`.

```bash
MOJAVE_REVIEW_CONFIG_FILE=~/mojave-review/config/config.yaml \
    gunicorn -w 2 -b 127.0.0.1:8050 mojave_review.wsgi:application
```

In another terminal:

```bash
# 1) No credentials → 403 page
curl -sS -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8050/
# → 403

# 2) Token in URL → 302 (middleware sets cookie, redirects to strip ?token=)
curl -sS -o /dev/null -c /tmp/cookie.txt -w "%{http_code}\n" \
    "http://127.0.0.1:8050/?token=<paste-your-token>"
# → 302

# 3) With the cookie just acquired → 200
curl -sS -o /dev/null -b /tmp/cookie.txt -w "%{http_code}\n" \
    http://127.0.0.1:8050/
# → 200
```

Ctrl-C the foreground gunicorn once all three checks pass.

### 6. Install the systemd `--user` unit

```bash
mkdir -p ~/.config/systemd/user
cp ~/Clustering_Review/deploy/mojave-review.service \
   ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now mojave-review
```

Verify:

```bash
systemctl --user status mojave-review
journalctl --user-unit mojave-review -e
```

The startup log line you want to see:

```
INFO mojave_review.wsgi | WSGI startup  reviewer_fallback=homand
    admin=False  auth=token  log_file=/home/homand/.../mojave-review.log
```

### 7. Make the service survive logout

`--user` units stop when the user logs out unless lingering is enabled.

```bash
loginctl enable-linger $USER
```

Verify with `loginctl show-user $USER --property=Linger` → `Linger=yes`.

### 8. Open the firewall (if any)

Linux Mint ships with `ufw` disabled by default. If you've enabled it,
allow LAN access to 8050:

```bash
sudo ufw status
sudo ufw allow from 192.168.1.0/24 to any port 8050   # only if ufw is active
```

### 9. From another machine on the LAN

```
http://192.168.1.23:8050/?token=<paste-your-token>
```

(Use the laptop's actual LAN IP — `ip -4 addr show` to confirm.) The
first hit sets the auth cookie; bookmark the resulting `http://...:8050/`
URL.

---

## On the SENDER machine — Results/ sync

These steps run on the OTHER machine (the one with the Google Drive
desktop mirror). The sync is strictly one-way: Drive → laptop. The
laptop's `recommendations/`, `fits_cache/`, and `logs/` are never
touched.

### S1. Prereqs

- `rsync` installed (`sudo apt install rsync` or equivalent).
- Key-based SSH from this machine to the laptop, no password prompt:
  ```bash
  ssh-keygen -t ed25519           # if you don't have a key yet
  ssh-copy-id homand@<laptop-ip>  # installs your pubkey on the laptop
  ssh homand@<laptop-ip> true     # must succeed without a prompt
  ```

### S2. Install the script + config

```bash
mkdir -p ~/bin ~/.config
cp ~/Clustering_Review/deploy/sync-results.sh ~/bin/mojave-review-sync
chmod +x ~/bin/mojave-review-sync

cp ~/Clustering_Review/deploy/mojave-results-sync.conf.example \
   ~/.config/mojave-results-sync.conf
```

(Adjust `~/Clustering_Review/` if you cloned the repo elsewhere on the
sender. Or just transfer the four `deploy/` files by hand — they have
no other dependencies.)

Edit `~/.config/mojave-results-sync.conf`:

- `SOURCE_DIR` — local Drive-mirror path on this machine.
- `TARGET_HOST` — laptop's hostname or LAN IP (same value as `LAN_URL`
  on the laptop side).
- The other fields match the example.

### S3. First manual run

Dry-run first to see exactly what will be transferred / deleted:

```bash
mojave-review-sync --dry-run
```

When that looks right:

```bash
mojave-review-sync
```

Verify on the laptop:

```bash
ssh homand@<laptop-ip> ls ~/mojave-review/data/Results/
```

### S4. Schedule the nightly sync

```bash
mkdir -p ~/.config/systemd/user
cp ~/Clustering_Review/deploy/mojave-results-sync.service \
   ~/.config/systemd/user/
cp ~/Clustering_Review/deploy/mojave-results-sync.timer \
   ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now mojave-results-sync.timer
loginctl enable-linger $USER     # so the timer fires after logout
```

Verify the timer is scheduled:

```bash
systemctl --user list-timers mojave-results-sync.timer
```

The "NEXT" column should show the upcoming 03:30 fire time. Inspect
past runs with `journalctl --user-unit mojave-results-sync`.

### S5. Run on demand

The timer is convenient but not required. To force a sync any time —
e.g. after dropping a freshly-rerun source into the Drive mirror:

```bash
systemctl --user start mojave-results-sync      # via systemd
# or
mojave-review-sync                              # directly
```

Both invocations call the same script with the same config.

## Day-to-day ops

| Action | Command |
|---|---|
| Tail live logs | `journalctl --user-unit mojave-review -f` |
| Tail the rotating package log | `tail -F ~/mojave-review/data/logs/mojave-review.log` |
| Tail gunicorn access log | `tail -F ~/mojave-review/data/logs/gunicorn-access.log` |
| Add a reviewer | `mojave-review-tokens --tokens-file ~/mojave-review/config/tokens.yaml add <user>` |
| Revoke a reviewer | `mojave-review-tokens --tokens-file ~/mojave-review/config/tokens.yaml revoke <user>` |
| Restart after a code change | `pip install -e .[server] && systemctl --user restart mojave-review` |
| Stop the service | `systemctl --user stop mojave-review` |
| Disable on boot | `systemctl --user disable mojave-review` |
| **On sender:** run sync now | `mojave-review-sync` |
| **On sender:** dry-run sync | `mojave-review-sync --dry-run` |
| **On sender:** tail sync runs | `journalctl --user-unit mojave-results-sync -e` |
| **On sender:** next sync time | `systemctl --user list-timers mojave-results-sync.timer` |

## Migrating to the university host

A migration runbook for moving the live service off this laptop. The
laptop stays up the whole time; we cut over at the end.

[`../docs/deployment_phase2.md`](../docs/deployment_phase2.md) is still
the IT-conversation reference. Hand it to IT for provisioning; this
section is the operational sequence on top of that.

### M1. Pre-migration code work

Two code changes need to land on a branch before the migration starts:

- **URL-prefix plumbing.** Add a `url_base_pathname` config field that
  flows through `Config` → `create_app(...)` → `Dash(...)` (and is
  honored by the auth middleware's cookie `path=` so the cookie is
  scoped to `/mojave-review/`, not `/`). On the laptop this stays
  unset; on the university host it's `"/mojave-review/"`.
- **nginx config example.** `deploy/nginx.conf.example` with the
  `/mojave-review/` `location` block, `proxy_pass http://127.0.0.1:8050/`,
  and a 443 ssl server block. Skipped at chunk 8 (Path A) on purpose
  — write it now against a real nginx version on the prod host.

### M2. Decide the on-disk path layout

Two viable layouts on the new host:

| Layout | Pros | Cons |
|---|---|---|
| Home-dir (`~/mojave-review/...`, same as laptop) | Zero sudo, identical to the doc you already wrote | Mixes ops state with the user's home |
| IT-doc (`/data/...` + `/etc/mojave-review/...`) | Cleaner separation, matches what IT will see | One-time sudo for `/etc` perms |

Either works. The config schema doesn't care — it's just path strings.
If you go IT-doc, update `tokens_file`, `results_dir`,
`recommendations_dir`, `cache_dir`, `log_file` in the production
`config.yaml`.

### M3. Stand up the new host (laptop still serving)

Follow the bring-up sequence in
[`../docs/deployment_phase2.md`](../docs/deployment_phase2.md) — Steps
0–3 — with these adjustments from Path A:

| Setting | Laptop (now) | University host |
|---|---|---|
| `cookie_secure` | `false` | `true` |
| gunicorn `--bind` | `0.0.0.0:8050` | `127.0.0.1:8050` |
| `url_base_pathname` | unset (root) | `/mojave-review/` |
| `ExecStart` gunicorn path | `~/anaconda3/bin/gunicorn` | `~/<new-venv>/bin/gunicorn` |
| Reverse proxy | none | nginx, TLS at the edge |

Verify the new host serves a 403 for unauthenticated requests AND a
200 for your own token (the same three-curl sequence as Step 5 above),
all on the new public URL. Do NOT email reviewers yet.

### M4. Migrate the data

`Results/` regenerates itself from the next sender run, so it doesn't
need explicit migration. Two things do:

```bash
# Tokens. Copy the file so existing token strings keep working —
# reviewers still need new bookmark URLs in M6 because the path
# prefix changes, but at least the secrets stay valid.
rsync -av ~/mojave-review/config/tokens.yaml \
    <user>@<new-host>:<new-path>/tokens.yaml

# Recommendations. THE only irreplaceable artifact. Copy with -a so
# mtimes survive (the app shows "updated_at" timestamps).
rsync -av ~/mojave-review/data/recommendations/ \
    <user>@<new-host>:<new-path>/recommendations/
```

The fits_cache/ is fine to skip — it'll repopulate transparently from
NRAO on demand.

### M5. Update the sender

On the sender machine, edit `~/.config/mojave-results-sync.conf`:

- `TARGET_USER` → username on the new host
- `TARGET_HOST` → new host's resolvable name
- `TARGET_DIR` → matches `results_dir` in the new host's `config.yaml`
  (per M2)

Then re-establish key-based SSH and do one manual `mojave-review-sync
--dry-run` to confirm the new target. Real run once it looks right.
The timer keeps firing on its existing schedule — no `.timer` edits
needed.

### M6. Cutover

```bash
# On the laptop: capture the audit log before retirement.
cp ~/mojave-review/data/logs/mojave-review.log \
   ~/mojave-review-laptop-audit-$(date +%F).log

# On the laptop: stop serving.
systemctl --user stop mojave-review
systemctl --user disable mojave-review

# Email reviewers their new bookmark URLs:
mojave-review-tokens --tokens-file <new-path>/tokens.yaml \
    url <user> --base-url https://<new-host>/mojave-review/
```

Note: the URL `path` changes (`/` → `/mojave-review/`), so old
bookmarks fail closed (cookie won't be sent on the new path). That's
correct fail-shut behavior — reviewers get a 403 page and email you.

### M7. Retire the laptop deployment

After ~1 week with no reports of issues:

```bash
loginctl disable-linger $USER          # optional
rm ~/.config/systemd/user/mojave-review.service
systemctl --user daemon-reload

# Keep the captured audit log file and recommendations/ tree.
# Everything else under ~/mojave-review/ is reproducible.
```

Leave the recommendations/ tree on disk a while longer as a belt-and-
braces backup of M4's migration. It's small and ignored by the (now
stopped) app.

### Summary of "what's reused unchanged"

Tokens (the strings — bookmark URLs change), recommendations JSON,
config schema (same keys, different values), the logging /
audit-trail format, the rsync sender script + timer (just the config
file changes), and the systemd unit (just `ExecStart` + `--bind` flip).
