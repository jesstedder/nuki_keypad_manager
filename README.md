# Nuki Keypad Manager (Home Assistant add-on)

Lists and creates Nuki Keypad PIN codes via the Nuki Web API, exposed as a
sidebar panel in Home Assistant through Ingress (no extra port exposure,
works fine behind your Cloudflare Access setup).

Codes are shown grouped by name + PIN across every lock the token can see,
not per-lock. Adding or editing a code lets you check which locks it should
exist on; the add-on creates/updates/deletes the underlying Nuki auth on
each lock to match, so one edit keeps a code in sync everywhere it's used.

## Install

1. Copy this whole `nuki_keypad_manager/` folder to `/addons/local/` on your
   Home Assistant host (via Samba share, SSH, or the Studio Code Server
   add-on).
2. In HA: **Settings → Add-ons → Add-on Store → ⋮ (top right) → Check for
   updates**. "Nuki Keypad Manager" will appear under **Local add-ons**.
3. Install it, then open the **Configuration** tab and set:
   - `nuki_api_token`: a token from https://web.nuki.io (Nuki Web must first
     be activated for each lock in the Nuki app, under Features & Configuration)
4. Start the add-on and enable **Show in sidebar**. The panel calls
   `GET /smartlock` itself and lists every lock the token has access to in a
   dropdown — no need to look up smartlock IDs by hand.

## Testing locally (outside Home Assistant)

You don't need HA installed to try the backend and UI:

```bash
cd app
uv venv
uv pip install -r requirements.txt
cp .env.example .env   # then edit .env and paste in your NUKI_API_TOKEN
uv run main.py
```

Then open http://localhost:8099 in a browser. `main.py` loads `.env` via
`python-dotenv` on startup; `.env` is gitignored so the token never gets
committed. This only covers the token — there's no `smartlock_id` to set
since locks are discovered live from the Nuki API (see below).

## Notes on the Nuki API

- Keypad PIN codes are `type: 13` auths on the smart lock.
- A code must be exactly 6 digits, using digits `1-9` only (no `0`), and
  cannot start with `12`. Each code must be unique across your whole Nuki
  account, not just this lock.
- Creating a code is `PUT /smartlock/{smartlockId}/auth`; updating an
  existing one in place (validity window, enabled state, even the PIN
  itself) is the same `PUT` with the existing auth `id` included in the
  body; listing is `GET /smartlock/{smartlockId}/auth`; deleting is
  `DELETE /smartlock/{smartlockId}/auth/{authId}`.
- There's a limit of 100–200 codes per keypad depending on hardware
  generation.
- Nuki has no native concept of "one code shared across locks" — each
  lock's auth list is independent. This add-on infers the link itself by
  matching `name` + PIN across locks' auth lists; there's no separate
  database of linked codes. A corollary: after creating or editing a code,
  Nuki's own list endpoint can lag a few seconds behind before it reflects
  the change on every lock (the UI shows an optimistic "syncing…" state
  and polls quietly until it catches up).

## Security

The API token is stored as an add-on option (in HA's supervisor config,
not in this repo) — don't commit a token if you push this to
code.mltn.net. Consider putting the token in a Sealed Secret if you later
port this to run as a standalone container in k3s instead of as a
supervisor add-on.

## Possible next steps

- Push a corresponding `sensor`/`switch` entity into HA over MQTT so
  codes are also visible outside this panel (mirrors what nuki_hub's
  open feature request #388 was aiming for).
- Auto-generate codes for calendar-based bookings, similar to the
  SuperSaaS webhook pattern, using HA's own calendar integration
  as the trigger instead of a third-party scheduler.
