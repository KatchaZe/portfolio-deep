"""
Google Drive backend for the JSON store (optional, free-tier persistence).

Why: Render's free tier uses an EPHEMERAL disk — data/portfolio.json is wiped on
every redeploy / restart / cold-start. This module mirrors that single JSON file
to a fixed file in Google Drive so the portfolio survives restarts.

IMPORTANT (fixed June 2026): on a PERSONAL Gmail account you must authenticate as
YOURSELF via OAuth. A *service account* has NO Drive storage quota, so it cannot
create files in a normal Drive — push fails with 403 "storageQuotaExceeded", even
inside a folder you shared with it. OAuth user credentials write to YOUR OWN Drive
(using your own quota) and work on free Gmail. See GOOGLE_DRIVE_OAUTH_SETUP.md.

Auth precedence (first one whose env vars are present wins):
  1) OAuth user creds  -> GDRIVE_OAUTH_CLIENT_ID / GDRIVE_OAUTH_CLIENT_SECRET /
                          GDRIVE_OAUTH_REFRESH_TOKEN          <-- use this
  2) Service account   -> GDRIVE_SA_JSON                      <-- legacy; only works
                          with a Google Workspace Shared Drive, NOT personal Gmail
If neither is set, every function is a no-op and the app runs exactly like the
local-only version. The local file is ALWAYS the working copy; Drive is a remote
mirror, so a Drive outage never crashes the app.
"""
import os
import io
import json
import logging
import datetime as _dt

log = logging.getLogger("portfolio.gdrive")

# Live status for the /api/persist endpoint + UI badge. Updated on every
# pull/push attempt so a silent failure becomes VISIBLE on the dashboard.
STATUS = {
    "last_pull": None,        # ISO timestamp of last pull attempt
    "pull_result": None,      # "pulled" | "absent" | "error"
    "last_push": None,        # ISO timestamp of last push attempt
    "push_result": None,      # "pushed" | "skipped" | "error"
    "last_error": None,       # str(last exception) from pull or push
}


def _now():
    return _dt.datetime.now().isoformat(timespec="seconds")

REMOTE_NAME = "portfolio.json"          # fixed filename inside Drive
# drive.file = the app may only touch files it creates/opens itself (narrowest,
# safest scope). Since the app CREATES portfolio.json, it can re-find and update it.
_SCOPES = ["https://www.googleapis.com/auth/drive.file"]

_service = None          # cached Drive client
_file_id = None          # cached id of the remote portfolio.json
_enabled = None          # tri-state cache: None=unknown, True/False once resolved


def _auth_mode():
    """Which credentials are configured? Returns 'oauth', 'sa', or None."""
    if (os.environ.get("GDRIVE_OAUTH_REFRESH_TOKEN")
            and os.environ.get("GDRIVE_OAUTH_CLIENT_ID")
            and os.environ.get("GDRIVE_OAUTH_CLIENT_SECRET")):
        return "oauth"
    if os.environ.get("GDRIVE_SA_JSON"):
        return "sa"
    return None


def enabled():
    """True only when credentials are configured AND the google libs import.
    Cached so we probe once per process."""
    global _enabled
    if _enabled is not None:
        return _enabled
    if _auth_mode() is None:
        _enabled = False
        return False
    try:
        import googleapiclient            # noqa: F401
        import google.auth                # noqa: F401
        _enabled = True
    except Exception as e:                # libs not installed -> stay local-only
        log.warning("Google Drive libs missing, staying local-only: %s", e)
        _enabled = False
    return _enabled


def _client():
    """Build (and cache) an authenticated Drive client. Prefers OAuth user
    credentials (writes to your own Drive); falls back to a service account."""
    global _service
    if _service is not None:
        return _service
    from googleapiclient.discovery import build
    mode = _auth_mode()
    if mode == "oauth":
        from google.oauth2.credentials import Credentials
        creds = Credentials(
            None,                                   # no access token yet -> auto-refresh
            refresh_token=os.environ["GDRIVE_OAUTH_REFRESH_TOKEN"],
            client_id=os.environ["GDRIVE_OAUTH_CLIENT_ID"],
            client_secret=os.environ["GDRIVE_OAUTH_CLIENT_SECRET"],
            token_uri="https://oauth2.googleapis.com/token",
            scopes=_SCOPES,
        )
    else:                                           # 'sa' (legacy)
        from google.oauth2 import service_account
        info = json.loads(os.environ["GDRIVE_SA_JSON"])
        creds = service_account.Credentials.from_service_account_info(info, scopes=_SCOPES)
    # C1 fix: bound every Drive HTTP call — httplib2's default has NO timeout,
    # so a hung socket could stall the push worker (or the first-load pull) forever.
    try:
        import httplib2
        from google_auth_httplib2 import AuthorizedHttp
        _service = build("drive", "v3", cache_discovery=False,
                         http=AuthorizedHttp(creds, http=httplib2.Http(timeout=30)))
    except ImportError:                     # very old client libs: keep old behaviour
        _service = build("drive", "v3", credentials=creds, cache_discovery=False)
    return _service


def _find_file_id(svc):
    """Locate portfolio.json (optionally inside GDRIVE_FOLDER_ID); cache the id."""
    global _file_id
    if _file_id:
        return _file_id
    q = f"name = '{REMOTE_NAME}' and trashed = false"
    folder = os.environ.get("GDRIVE_FOLDER_ID")
    if folder:
        q += f" and '{folder}' in parents"
    res = svc.files().list(q=q, spaces="drive", fields="files(id, name)",
                           pageSize=1).execute()
    files = res.get("files", [])
    _file_id = files[0]["id"] if files else None
    return _file_id


def drive_pull(local_path):
    """Download the remote portfolio.json onto local_path.

    Returns one of THREE distinct states (the caller MUST treat them differently):
      "pulled" — remote existed and was written locally
      "absent" — Drive reachable but no remote file yet (legit first run)
      "error"  — Drive NOT reachable / auth failed → the caller must NOT push,
                 or an empty local store would overwrite the good remote backup.
    Never raises."""
    if not enabled():
        return "error" if _auth_mode() else "absent"
    STATUS["last_pull"] = _now()
    try:
        from googleapiclient.http import MediaIoBaseDownload
        svc = _client()
        fid = _find_file_id(svc)
        if not fid:
            log.info("Drive: no remote portfolio.json yet (first run)")
            STATUS["pull_result"] = "absent"
            return "absent"
        buf = io.BytesIO()
        dl = MediaIoBaseDownload(buf, svc.files().get_media(fileId=fid))
        done = False
        while not done:
            _, done = dl.next_chunk()
        data = buf.getvalue()
        json.loads(data.decode("utf-8"))            # validate before overwriting
        os.makedirs(os.path.dirname(local_path), exist_ok=True)
        # C2 fix: atomic replace — readers load() lock-free, so they must never
        # be able to see a half-written portfolio.json.
        tmp = f"{local_path}.pull.{os.getpid()}.tmp"
        with open(tmp, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, local_path)
        log.info("Drive: pulled portfolio.json (%d bytes)", len(data))
        STATUS["pull_result"] = "pulled"
        STATUS["last_error"] = None
        return "pulled"
    except Exception as e:
        log.error("Drive pull FAILED — push disabled until a pull succeeds "
                  "(protects the remote backup): %s", e)
        STATUS["pull_result"] = "error"
        STATUS["last_error"] = f"pull: {e}"
        return "error"


def drive_push(local_path):
    """Upload local_path to the remote portfolio.json (create or update).
    Returns True on success. Never raises — a Drive outage must not break save()."""
    global _file_id
    if not enabled():
        return False
    STATUS["last_push"] = _now()
    try:
        from googleapiclient.http import MediaFileUpload
        svc = _client()
        fid = _find_file_id(svc)
        media = MediaFileUpload(local_path, mimetype="application/json", resumable=False)
        if fid:
            try:
                svc.files().update(fileId=fid, media_body=media).execute()
            except Exception as e:
                # H4: the cached file id can go stale (file deleted/moved in
                # Drive). On 404, drop the cache and fall through to re-find /
                # re-create instead of erroring until the next restart.
                if "404" not in str(e):
                    raise
                log.warning("Drive: cached file id stale (%s); re-resolving", e)
                _file_id = None
                fid = _find_file_id(svc)
                media = MediaFileUpload(local_path, mimetype="application/json",
                                        resumable=False)
                if fid:
                    svc.files().update(fileId=fid, media_body=media).execute()
        if not fid:
            meta = {"name": REMOTE_NAME}
            folder = os.environ.get("GDRIVE_FOLDER_ID")
            if folder:
                meta["parents"] = [folder]
            try:
                created = svc.files().create(body=meta, media_body=media,
                                             fields="id").execute()
            except Exception as e:
                # A folder you created by hand may not be visible under the narrow
                # drive.file scope. Fall back to saving in "My Drive" root (still
                # your own account) so the backup always succeeds.
                if folder:
                    log.warning("Drive: folder '%s' unusable (%s); "
                                "saving portfolio.json to My Drive root instead",
                                folder, e)
                    meta.pop("parents", None)
                    media = MediaFileUpload(local_path, mimetype="application/json",
                                            resumable=False)
                    created = svc.files().create(body=meta, media_body=media,
                                                 fields="id").execute()
                else:
                    raise
            _file_id = created["id"]        # (global declared at function top)
        log.info("Drive: pushed portfolio.json")
        STATUS["push_result"] = "pushed"
        STATUS["last_error"] = None
        return True
    except Exception as e:
        log.warning("Drive push failed (kept local copy only): %s", e)
        STATUS["push_result"] = "error"
        STATUS["last_error"] = f"push: {e}"
        return False


def status():
    """Snapshot for the /api/persist endpoint — never raises."""
    mode = _auth_mode()
    return {"configured": mode is not None, "auth_mode": mode,
            "enabled": enabled(), **STATUS}
