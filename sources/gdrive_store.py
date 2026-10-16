"""
Google Drive I/O for the JSON store (optional, free-tier persistence).

Render's free tier has an EPHEMERAL disk, so data/portfolio.json is wiped on every
redeploy. This module copies named JSON files to and from Drive. It knows nothing
about WHEN that is safe; the pull/push guard lives in store_sync.py.

On a PERSONAL Gmail account authenticate as YOURSELF via OAuth: a service account
has no Drive storage quota, so its pushes fail with 403 "storageQuotaExceeded" even
inside a folder shared with it. See GOOGLE_DRIVE_OAUTH_SETUP.md.

Auth precedence (first one whose env vars are present wins):
  1) OAuth user creds  -> GDRIVE_OAUTH_CLIENT_ID / GDRIVE_OAUTH_CLIENT_SECRET /
                          GDRIVE_OAUTH_REFRESH_TOKEN
  2) Service account   -> GDRIVE_SA_JSON (legacy; Workspace Shared Drive only)
With neither set every function is a no-op. No function here ever raises.
"""
import os
import io
import json
import logging
import datetime as _dt

log = logging.getLogger("portfolio.gdrive")

# Live status for /api/persist and the UI badge, so a silent backup failure is
# visible. store_sync also records its guard decisions here.
STATUS = {
    "last_pull": None,        # ISO timestamp of last pull attempt
    "pull_result": None,      # "pulled" | "absent" | "error" | "local-kept"
    "last_push": None,        # ISO timestamp of last push attempt
    "push_result": None,      # "pushed" | "skipped" | "error"
    "last_error": None,       # str(last exception) from pull or push
}

REMOTE_NAME = "portfolio.json"
# drive.file: the app may only touch files it created or opened itself.
_SCOPES = ["https://www.googleapis.com/auth/drive.file"]

_service = None          # cached Drive client
_ids = {}                # remote name -> cached Drive file id (None = not found)
_enabled = None          # None = not probed yet


def _now():
    return _dt.datetime.now().isoformat(timespec="seconds")


def _auth_mode():
    """'oauth', 'sa', or None."""
    if (os.environ.get("GDRIVE_OAUTH_REFRESH_TOKEN")
            and os.environ.get("GDRIVE_OAUTH_CLIENT_ID")
            and os.environ.get("GDRIVE_OAUTH_CLIENT_SECRET")):
        return "oauth"
    if os.environ.get("GDRIVE_SA_JSON"):
        return "sa"
    return None


def enabled():
    """True only when credentials are configured AND the google libs import.
    Probed once per process."""
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
    except Exception as e:
        log.warning("Google Drive libs missing, staying local-only: %s", e)
        _enabled = False
    return _enabled


def _credentials():
    if _auth_mode() == "oauth":
        from google.oauth2.credentials import Credentials
        return Credentials(
            None,                                   # no access token yet -> auto-refresh
            refresh_token=os.environ["GDRIVE_OAUTH_REFRESH_TOKEN"],
            client_id=os.environ["GDRIVE_OAUTH_CLIENT_ID"],
            client_secret=os.environ["GDRIVE_OAUTH_CLIENT_SECRET"],
            token_uri="https://oauth2.googleapis.com/token",
            scopes=_SCOPES,
        )
    from google.oauth2 import service_account
    info = json.loads(os.environ["GDRIVE_SA_JSON"])
    return service_account.Credentials.from_service_account_info(info, scopes=_SCOPES)


def _client():
    """Build (and cache) an authenticated Drive client."""
    global _service
    if _service is not None:
        return _service
    from googleapiclient.discovery import build
    creds = _credentials()
    # httplib2's default has NO timeout: a hung socket would stall the push worker
    # (or the first-load pull) forever.
    try:
        import httplib2
        from google_auth_httplib2 import AuthorizedHttp
        _service = build("drive", "v3", cache_discovery=False,
                         http=AuthorizedHttp(creds, http=httplib2.Http(timeout=30)))
    except ImportError:                     # very old client libs
        _service = build("drive", "v3", credentials=creds, cache_discovery=False)
    return _service


def _folder():
    return os.environ.get("GDRIVE_FOLDER_ID")


def _find_id(svc, name):
    """Locate `name` (inside GDRIVE_FOLDER_ID when set); cache the id."""
    if _ids.get(name):
        return _ids[name]
    q = f"name = '{name}' and trashed = false"
    if _folder():
        q += f" and '{_folder()}' in parents"
    res = svc.files().list(q=q, spaces="drive", fields="files(id, name)",
                           pageSize=1).execute()
    files = res.get("files", [])
    _ids[name] = files[0]["id"] if files else None
    return _ids[name]


def _download(svc, fid):
    from googleapiclient.http import MediaIoBaseDownload
    buf = io.BytesIO()
    dl = MediaIoBaseDownload(buf, svc.files().get_media(fileId=fid))
    done = False
    while not done:
        _, done = dl.next_chunk()
    return buf.getvalue()


def _upload_media(data):
    """A fresh upload body over an IN-MEMORY copy of the file.

    A file-backed upload keeps the local file open for the whole request, and on
    Windows an open handle makes the racing save's `os.replace` fail, losing that
    write. A FRESH BytesIO per attempt: a consumed stream would upload zero bytes on
    the 404 re-resolve and folder-fallback retries.
    """
    from googleapiclient.http import MediaIoBaseUpload
    return MediaIoBaseUpload(io.BytesIO(data), mimetype="application/json",
                             resumable=False)


def _create(svc, name, data, retry_any_failure):
    """Create `name`; returns its id. A hand-made folder may be invisible under the
    drive.file scope, so a failed create retries in "My Drive" root. Raises."""
    meta = {"name": name}
    if _folder():
        meta["parents"] = [_folder()]
    try:
        return svc.files().create(body=meta, media_body=_upload_media(data),
                                  fields="id").execute()["id"]
    except Exception as e:
        if not (_folder() or retry_any_failure):
            raise
        log.warning("Drive: create of %s failed (%s); retrying in My Drive root", name, e)
        meta.pop("parents", None)
        return svc.files().create(body=meta, media_body=_upload_media(data),
                                  fields="id").execute()["id"]


def _upload(name, local_path, retry_any_failure=False):
    """Create or update the remote `name` from local_path. Raises."""
    with open(local_path, "rb") as fh:      # read + close before the network
        data = fh.read()
    svc = _client()
    fid = _find_id(svc, name)
    if fid:
        try:
            svc.files().update(fileId=fid, media_body=_upload_media(data)).execute()
            return
        except Exception as e:
            # The cached id goes stale when the file is deleted/moved in Drive.
            if "404" not in str(e):
                raise
            log.warning("Drive: cached file id for %s stale (%s); re-resolving", name, e)
            _ids[name] = None
            fid = _find_id(svc, name)
            if fid:
                svc.files().update(fileId=fid, media_body=_upload_media(data)).execute()
                return
    _ids[name] = _create(svc, name, data, retry_any_failure)


# --- portfolio.json (status-tracked; store_sync applies the data-loss guard) --- #
def drive_pull(local_path):
    """Download the remote portfolio.json onto local_path. Returns:
      "pulled" -- remote existed and was written locally
      "absent" -- Drive reachable but no remote file yet (legit first run)
      "error"  -- Drive NOT reachable / auth failed; the caller must NOT push,
                  or an empty local store would overwrite the good remote backup."""
    if not enabled():
        if _auth_mode():
            STATUS["pull_result"] = "error"
            STATUS["last_error"] = ("pull: GDRIVE_* is set but Google libs are not "
                                    "importable — pip install google-api-python-client google-auth")
            return "error"
        return "absent"
    STATUS["last_pull"] = _now()
    try:
        svc = _client()
        fid = _find_id(svc, REMOTE_NAME)
        if not fid:
            log.info("Drive: no remote portfolio.json yet (first run)")
            STATUS["pull_result"] = "absent"
            return "absent"
        data = _download(svc, fid)
        json.loads(data.decode("utf-8"))            # validate before overwriting
        os.makedirs(os.path.dirname(local_path), exist_ok=True)
        # Atomic replace: readers load() lock-free and must never see a half file.
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
    """Upload local_path as the remote portfolio.json. True on success."""
    if not enabled():
        return False
    STATUS["last_push"] = _now()
    try:
        _upload(REMOTE_NAME, local_path)
    except Exception as e:
        log.warning("Drive push failed (kept local copy only): %s", e)
        STATUS["push_result"] = "error"
        STATUS["last_error"] = f"push: {e}"
        return False
    log.info("Drive: pushed portfolio.json")
    STATUS["push_result"] = "pushed"
    STATUS["last_error"] = None
    return True


# --- auxiliary JSON files (e.g. risk_cache.json): rebuildable, untracked ------ #
def drive_pull_json(name):
    """The parsed remote `name`, or None (disabled / absent / unreachable / invalid).
    Never touches local disk; callers merge in memory."""
    if not enabled():
        return None
    try:
        svc = _client()
        fid = _find_id(svc, name)
        if not fid:
            return None
        return json.loads(_download(svc, fid).decode("utf-8"))
    except Exception as e:
        log.warning("Drive aux pull '%s' failed (ignored): %s", name, e)
        return None


def drive_push_json(name, local_path):
    """Upload local_path as the remote `name`. True on success."""
    if not enabled():
        return False
    try:
        _upload(name, local_path, retry_any_failure=True)
    except Exception as e:
        log.warning("Drive aux push '%s' failed (kept local only): %s", name, e)
        return False
    log.info("Drive: pushed %s", name)
    return True


def status():
    """Snapshot for /api/persist."""
    mode = _auth_mode()
    return {"configured": mode is not None, "auth_mode": mode,
            "enabled": enabled(), **STATUS}
