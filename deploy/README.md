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

## What changes at the university-host migration

A single coherent migration step replaces "Path A" with the full IT
plan:

1. Stand up nginx with the `/mojave-review/` location block reverse-
   proxying to `127.0.0.1:8050`. Add TLS at nginx.
2. Add `url_base_pathname="/mojave-review/"` plumbing through
   `mojave_review.app.create_app` (one constructor arg + the cookie's
   `path` in the auth middleware).
3. Flip `cookie_secure: true` in `config.yaml`.
4. Change the systemd unit's `--bind` from `0.0.0.0:8050` to
   `127.0.0.1:8050` (nginx is now the only thing talking to gunicorn).
5. Swap `ExecStart`'s gunicorn path from the anaconda3 location to the
   dedicated venv built on the new host.
6. Re-issue bookmark URLs to reviewers (the path changes from `/` to
   `/mojave-review/`).

Everything else — tokens, recommendations, config schema, logging,
audit trail — is reused unchanged.
