"""
Google Drive backend for the JSON store (optional, free-tier persistence).

Why: Render's free tier uses an EPHEMERAL disk — data/portfolio.json is wiped on
every redeploy / restart / cold-start. This module mirrors that single JSON file
to a fixed file in Google Drive so the portfolio survives restarts and is
reachable from anywhere.

How it plugs in: store.py calls drive_pull() on load() and drive_push() on save().
If Drive isn't configured (no GDRIVE_SA_JSON env var) every function is a no-op and
the app behaves exactly like the local-only version. Local file is ALWAYS the
working copy; Drive is a remote mirror, so a Drive outage never crashes the app.

Setup (one time) — see GOOGLE_DRIVE_SETUP.md:
  1) make a Google Cloud project + enable the Drive API
  2) create a Service Account, download its key JSON
  3) make a Drive folder, share it with the service account's email (Editor)
  4) set two env vars on Render:
       GDRIVE_SA_JSON   = (paste the entire key JSON, one line)
       GDRIVE_FOLDER_ID = (the folder id from its Drive URL)
"""
import os
import io
import json
import logging

log = logging.getLogger("portfolio.gdrive")

REMOTE_NAME = "portfolio.json"          # fixed filename inside the Drive folder
_SCOPES = ["https://www.googleapis.com/auth/drive.file"]

_service = None          # cached Drive client
_file_id = None          # cached id of the remote portfolio.json
_enabled = None          # tri-state cache: None=unknown, True/False once resolved


def enabled():
    """True only when both env vars are present AND the google libs import.
    Cached so we probe once per process."""
    global _enabled
    if _enabled is not None:
        return _enabled
    if not os.environ.get("GDRIVE_SA_JSON") or not os.environ.get("GDRIVE_FOLDER_ID"):
        _enabled = False
        return False
    try:
        import googleapiclient            # noqa: F401
        import google.oauth2.service_account  # noqa: F401
        _enabled = True
    except Exception as e:                # libs not installed -> stay local-only
        log.warning("Google Drive libs missing, staying local-only: %s", e)
        _enabled = False
    return _enabled


def _client():
    """Build (and cache) an authenticated Drive client from the SA key JSON."""
    global _service
    if _service is not None:
        return _service
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    info = json.loads(os.environ["GDRIVE_SA_JSON"])
    creds = service_account.Credentials.from_service_account_info(info, scopes=_SCOPES)
    _service = build("drive", "v3", credentials=creds, cache_discovery=False)
    return _service


def _find_file_id(svc):
    """Locate portfolio.json inside the configured folder; cache the id."""
    global _file_id
    if _file_id:
        return _file_id
    folder = os.environ["GDRIVE_FOLDER_ID"]
    q = (f"name = '{REMOTE_NAME}' and '{folder}' in parents and trashed = false")
    res = svc.files().list(q=q, spaces="drive", fields="files(id, name)",
                           pageSize=1).execute()
    files = res.get("files", [])
    _file_id = files[0]["id"] if files else None
    return _file_id


def drive_pull(local_path):
    """Download the remote portfolio.json onto local_path. Returns True if a
    remote copy existed and was written, False otherwise. Never raises."""
    if not enabled():
        return False
    try:
        from googleapiclient.http import MediaIoBaseDownload
        svc = _client()
        fid = _find_file_id(svc)
        if not fid:
            log.info("Drive: no remote portfolio.json yet (first run)")
            return False
        buf = io.BytesIO()
        dl = MediaIoBaseDownload(buf, svc.files().get_media(fileId=fid))
        done = False
        while not done:
            _, done = dl.next_chunk()
        data = buf.getvalue()
        json.loads(data.decode("utf-8"))            # validate before overwriting
        os.makedirs(os.path.dirname(local_path), exist_ok=True)
        with open(local_path, "wb") as fh:
            fh.write(data)
        log.info("Drive: pulled portfolio.json (%d bytes)", len(data))
        return True
    except Exception as e:
        log.warning("Drive pull failed (using local copy): %s", e)
        return False


def drive_push(local_path):
    """Upload local_path to the remote portfolio.json (create or update).
    Returns True on success. Never raises — a Drive outage must not break save()."""
    if not enabled():
        return False
    try:
        from googleapiclient.http import MediaFileUpload
        svc = _client()
        fid = _find_file_id(svc)
        media = MediaFileUpload(local_path, mimetype="application/json", resumable=False)
        if fid:
            svc.files().update(fileId=fid, media_body=media).execute()
        else:
            meta = {"name": REMOTE_NAME, "parents": [os.environ["GDRIVE_FOLDER_ID"]]}
            created = svc.files().create(body=meta, media_body=media,
                                         fields="id").execute()
            global _file_id
            _file_id = created["id"]
        log.info("Drive: pushed portfolio.json")
        return True
    except Exception as e:
        log.warning("Drive push failed (kept local copy only): %s", e)
        return False
