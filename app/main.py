import os
import re
import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, request, send_from_directory

load_dotenv()

app = Flask(__name__, static_folder="static", static_url_path="")

NUKI_API_TOKEN = os.environ.get("NUKI_API_TOKEN", "")
BASE_URL = "https://api.nuki.io"

HEADERS = {
    "Authorization": f"Bearer {NUKI_API_TOKEN}",
    "Content-Type": "application/json",
    "Accept": "application/json",
}

# Nuki keypad PIN rules: exactly 6 digits, only 1-9 (no zero), not starting with "12"
CODE_RE = re.compile(r"^[1-9]{6}$")

KEYPAD_AUTH_TYPE = 13


def _require_token():
    if not NUKI_API_TOKEN:
        return jsonify({"error": "Add-on is not configured with an API token yet."}), 400
    return None


def _validate_code_fields(name, code):
    if not name or len(name) > 20:
        return "Name is required and must be 20 characters or fewer."
    if not CODE_RE.match(code):
        return "Code must be exactly 6 digits, using only 1-9 (no zero)."
    if code.startswith("12"):
        return "Code cannot start with '12'."
    return None


def _nuki_error_message(resp):
    try:
        payload = resp.json()
    except ValueError:
        return resp.text
    if isinstance(payload, dict) and payload.get("detailMessage"):
        return payload["detailMessage"]
    return resp.text


def _fetch_locks():
    resp = requests.get(f"{BASE_URL}/smartlock", headers=HEADERS, timeout=15)
    if resp.status_code != 200:
        return None, resp
    locks = [{"id": lock.get("smartlockId"), "name": lock.get("name")} for lock in resp.json()]
    return locks, None


def _fetch_lock_keypad_auths(lock_id):
    resp = requests.get(f"{BASE_URL}/smartlock/{lock_id}/auth", headers=HEADERS, timeout=15)
    if resp.status_code != 200:
        return None
    return [a for a in resp.json() if a.get("type") == KEYPAD_AUTH_TYPE]


@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.route("/api/locks", methods=["GET"])
def list_locks():
    err = _require_token()
    if err:
        return err

    locks, error_resp = _fetch_locks()
    if error_resp is not None:
        return jsonify({"error": _nuki_error_message(error_resp)}), error_resp.status_code
    return jsonify(locks)


@app.route("/api/codes", methods=["GET"])
def list_code_groups():
    """Every keypad code across every lock, grouped by (name, PIN) so codes
    shared across locks show up as one entry."""
    err = _require_token()
    if err:
        return err

    locks, error_resp = _fetch_locks()
    if error_resp is not None:
        return jsonify({"error": _nuki_error_message(error_resp)}), error_resp.status_code

    groups = {}
    for lock in locks:
        auths = _fetch_lock_keypad_auths(lock["id"])
        if auths is None:
            continue
        for auth in auths:
            key = (auth.get("name"), auth.get("code"))
            group = groups.setdefault(key, {"name": auth.get("name"), "code": auth.get("code"), "entries": []})
            group["entries"].append(
                {
                    "lockId": lock["id"],
                    "lockName": lock["name"],
                    "authId": auth.get("id"),
                    "enabled": auth.get("enabled"),
                    "allowedFromDate": auth.get("allowedFromDate"),
                    "allowedUntilDate": auth.get("allowedUntilDate"),
                }
            )

    return jsonify({"locks": locks, "groups": list(groups.values())})


@app.route("/api/codes", methods=["POST"])
def create_code_group():
    err = _require_token()
    if err:
        return err

    data = request.get_json(force=True) or {}
    name = (data.get("name") or "").strip()
    code = str(data.get("code") or "").strip()
    lock_ids = data.get("lockIds") or []

    validation_error = _validate_code_fields(name, code)
    if validation_error:
        return jsonify({"error": validation_error}), 400
    if not lock_ids:
        return jsonify({"error": "Select at least one lock."}), 400

    payload = {"name": name, "type": KEYPAD_AUTH_TYPE, "code": int(code)}
    if data.get("allowedFromDate"):
        payload["allowedFromDate"] = data["allowedFromDate"]
    if data.get("allowedUntilDate"):
        payload["allowedUntilDate"] = data["allowedUntilDate"]

    results = []
    for lock_id in lock_ids:
        resp = requests.put(f"{BASE_URL}/smartlock/{lock_id}/auth", headers=HEADERS, json=payload, timeout=15)
        ok = resp.status_code in (200, 204)
        results.append({"lockId": lock_id, "ok": ok, "error": None if ok else _nuki_error_message(resp)})

    if not any(r["ok"] for r in results):
        return jsonify({"error": "Failed to create the code on any lock.", "results": results}), 502

    return jsonify({"status": "created", "results": results}), 201


@app.route("/api/codes", methods=["PUT"])
def sync_code_group():
    """Reconcile a code's name/PIN/schedule/enabled state and lock membership:
    updates locks that already have it, creates it on newly-added locks, and
    deletes it from locks that were unchecked."""
    err = _require_token()
    if err:
        return err

    data = request.get_json(force=True) or {}
    name = (data.get("name") or "").strip()
    code = str(data.get("code") or "").strip()
    enabled = bool(data.get("enabled", True))
    lock_ids = {lid for lid in (data.get("lockIds") or [])}
    current = data.get("current") or []  # [{"lockId":, "authId":}]

    validation_error = _validate_code_fields(name, code)
    if validation_error:
        return jsonify({"error": validation_error}), 400

    current_by_lock = {c["lockId"]: c["authId"] for c in current}

    payload_base = {"name": name, "type": KEYPAD_AUTH_TYPE, "code": int(code), "enabled": enabled}
    if data.get("allowedFromDate"):
        payload_base["allowedFromDate"] = data["allowedFromDate"]
    if data.get("allowedUntilDate"):
        payload_base["allowedUntilDate"] = data["allowedUntilDate"]

    results = []
    for lock_id in lock_ids | set(current_by_lock.keys()):
        if lock_id in lock_ids and lock_id in current_by_lock:
            payload = {**payload_base, "id": current_by_lock[lock_id]}
            resp = requests.put(f"{BASE_URL}/smartlock/{lock_id}/auth", headers=HEADERS, json=payload, timeout=15)
        elif lock_id in lock_ids:
            resp = requests.put(f"{BASE_URL}/smartlock/{lock_id}/auth", headers=HEADERS, json=payload_base, timeout=15)
        else:
            auth_id = current_by_lock[lock_id]
            resp = requests.delete(f"{BASE_URL}/smartlock/{lock_id}/auth/{auth_id}", headers=HEADERS, timeout=15)
        ok = resp.status_code in (200, 204)
        results.append({"lockId": lock_id, "ok": ok, "error": None if ok else _nuki_error_message(resp)})

    # Partial failure is normal here (e.g. Nuki's account-wide PIN-uniqueness
    # check can reject one lock's create while others already hold that PIN
    # legitimately) - report per-lock results instead of failing the whole
    # request, so the caller can show what succeeded and retry the rest.
    return jsonify({"status": "synced", "results": results})


@app.route("/api/codes", methods=["DELETE"])
def delete_code_group():
    err = _require_token()
    if err:
        return err

    data = request.get_json(force=True) or {}
    current = data.get("current") or []  # [{"lockId":, "authId":}]

    results = []
    for c in current:
        resp = requests.delete(
            f"{BASE_URL}/smartlock/{c['lockId']}/auth/{c['authId']}", headers=HEADERS, timeout=15
        )
        ok = resp.status_code in (200, 204)
        results.append({"lockId": c["lockId"], "ok": ok, "error": None if ok else _nuki_error_message(resp)})

    return jsonify({"status": "deleted", "results": results})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8099)
