# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Home Assistant add-on that manages Nuki Keypad PIN codes via the Nuki Web API
(`https://api.nuki.io`). It runs as a sidebar panel inside Home Assistant using
Ingress, so it needs no exposed port and works behind the user's existing
Cloudflare Access setup.

## Architecture

- `app/main.py` — single-file Flask app. All backend logic lives here:
  `GET /api/locks` (every smartlock on the account) and a `/api/codes`
  resource with `GET` (all keypad codes across all locks, grouped — see
  below), `POST` (create on a chosen set of locks), `PUT` (reconcile an
  existing group's fields and lock membership — creates/updates/deletes
  the underlying per-lock auth as needed), and `DELETE` (remove a group
  from every lock it's on). All of it proxies to the Nuki Web API, plus a
  route serving the static frontend. There is no database — the Nuki API
  is the only source of truth, fetched live on every request. A background
  daemon thread (`_ws_listen_loop`, started in `if __name__ == "__main__"`)
  is the exception: it subscribes to HA's own Core WebSocket API for
  `state_changed` events on the lock entities named in the `lock_entities`
  option, and reactively queries that lock's Nuki activity log and pushes a
  `logbook.log` entry (who/how it was actuated) — see "Logbook attribution"
  below.
- **Cross-lock grouping**: Nuki has no native concept of a code being
  shared across locks — each lock's `/auth` list is completely
  independent. `list_code_groups()` in `main.py` fetches every lock's auth
  list and groups entries by the `(name, code)` tuple, so "Guest" with PIN
  `483920` on two locks shows up as one logical row with two `entries`.
  This is inferred at request time, not persisted — renaming a code
  independently on one lock (bypassing this app) silently breaks the link.
  `sync_code_group()` (the `PUT` handler) takes the desired `lockIds` plus
  the `current` (lockId → authId) membership the frontend already has from
  the last `GET`, and diffs them: locks in both → update in place (Nuki
  `PUT` with the existing `id`, which can change the PIN itself, not just
  the schedule); locks only in the desired set → create; locks only in
  `current` → delete.
- `app/static/index.html` — the entire frontend: HTML, CSS, and vanilla JS
  in one file, no build step or framework. Fetches `api/codes` (relative
  URL, required for Ingress since the add-on can be mounted under an
  arbitrary path prefix) and renders one row per group, with per-lock chips
  showing membership. The add/edit form has a lock checklist (all checked
  by default when adding). Because Nuki's own `GET .../auth` list can lag
  a few seconds behind a `PUT`/`DELETE` write until each lock/bridge syncs,
  the frontend keeps `allGroups` in memory, applies the intended end state
  optimistically right after a save (marking not-yet-confirmed lock
  entries `pending`, which disables Edit/Delete on that group until
  resolved), and polls `GET /api/codes` in the background for up to ~16s to
  reconcile with the real per-lock auth IDs.
- Config (`nuki_api_token`, plus optional `lock_entities` — no
  `smartlock_id`) is supplied by the HA supervisor as add-on options
  (defined in `build.yaml`/`config.yaml`'s `options`/`schema`), not env
  files or repo config. `run.sh` reads them via `bashio::config` and
  exports `NUKI_API_TOKEN`/`NUKI_LOCK_ENTITIES` before starting `main.py`.
  Lock discovery for the panel itself relies on the token alone
  (`GET /smartlock` returns every lock it has access to), so there's no
  separate lock ID to configure there — `lock_entities` only exists to map
  HA entity IDs to Nuki `smartlockId`s for the Logbook attribution feature.
- `Dockerfile` builds from the HA base Python images (`build.yaml` maps
  `aarch64`/`amd64`/`armv7` to `ghcr.io/home-assistant/*-base-python`) and
  runs `run.sh` as the container entrypoint.

## Logbook attribution (who/how unlocked, not just state)

HA's Matter integration (how this add-on's user exposes their Nuki lock as
a `lock.*` entity) only reports locked/unlocked state — it has no concept
of which keypad code, Fob, or app user triggered it. That attribution only
exists in Nuki's own per-lock activity log (`GET /smartlock/{id}/log`),
whose entries' `name`/`authId` fields reference the auth that triggered the
action, and whose `trigger` field (`TRIGGER_LABELS` in `main.py`) says how
(`keypad`, `app`, `button`, `auto-lock`, etc.).

Rather than polling that log on a timer, `_ws_listen_loop()` in `main.py`
opens a long-lived connection to Home Assistant's own Core WebSocket API
(`ws://supervisor/core/websocket`, proxied by the Supervisor — requires
`homeassistant_api: true` in `config.yaml`, which is what grants the
add-on's `SUPERVISOR_TOKEN` permission to speak to Core, not just the
Supervisor's own API) and subscribes to `state_changed`. When one of the
entities named in `lock_entities` flips to `locked`/`unlocked`,
`_handle_lock_state_change()` queries that lock's Nuki log for the newest
lock/unlock entry and pushes a `logbook.log` service call naming who/how.

Nuki's log can lag a couple seconds behind the device's own state push (the
same sync delay noted for `/auth` elsewhere in this file), so the handler
retries for ~6s before falling back to a "no matching entry yet" message —
deliberately visible rather than silently dropped, since a real door event
happened either way. Webhook push from Nuki was considered instead of this
WebSocket-triggered pull, but Nuki's webhooks require applying for their
"Advanced API" integrator program plus a publicly reachable callback URL,
which conflicts with this add-on's whole point of running Ingress-only
with no exposed port.

## Nuki API domain rules (enforced in `main.py`)

- Keypad PIN codes are `type: 13` auths on the smart lock.
- A code must be exactly 6 digits, digits `1-9` only (no `0`), and cannot
  start with `12`. Codes must be unique across the whole Nuki account, not
  just the one lock. See `CODE_RE` in `main.py`.
- `PUT /smartlock/{smartlockId}/auth` creates (no `id` in the body) or
  updates in place (existing `id` included), `GET .../auth` lists,
  `DELETE .../auth/{authId}` deletes. Auth `id` values are opaque strings,
  not necessarily integers — route converters must not assume `int` (see
  `delete_code` history: an `<int:auth_id>` converter silently fell through
  to Flask's static-file catch-all route for hex-style IDs, producing a
  confusing 405 instead of a 404).
- There's a hardware-dependent limit of 100–200 codes per keypad.

## Running / testing locally

There is no test suite or lint config in this repo. To run the Flask app
directly (outside of HA), use `uv` from within `app/`:

```bash
cd app
uv venv
uv pip install -r requirements.txt
cp .env.example .env   # then fill in NUKI_API_TOKEN
uv run main.py
```

`main.py` loads `.env` via `python-dotenv` at import time; `.env` is
gitignored so tokens never get committed. This only sets up local dev — the
HA add-on itself never reads `.env` (there's no such file in the container
image); it gets `NUKI_API_TOKEN` from `run.sh`/`bashio::config` instead.

The real deployment path is installing this as a local HA add-on (see
README.md `## Install`), which is the only way to exercise the Ingress
integration and the `config.yaml`/`bashio` config flow.

## Security note

The Nuki API token lives only in HA's supervisor config (as an add-on
option), never in this repo. Don't hardcode a token anywhere here.
