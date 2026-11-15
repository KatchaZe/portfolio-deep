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
import hashlib
import io
import re
import json
import socket
import logging
import datetime as _dt

log = logging.getLogger("portfolio.gdrive")

# Live status for /api/persist and the UI badge, so a silent backup failure is
# visible. store_sync also records its guard decisions here.
STATUS = {
    "last_pull": None,        # ISO timestamp of last pull attempt
    "pull_result": None,      # "pulled" | "absent" | "error" | "local-kept"
    "last_push": None,        # ISO timestamp of last push attempt
    "push_result": None,      # "pushed" | "skipped" | "error" | "conflict"
    "last_error": None,       # str(last exception) from pull or push
    "remote_version": None,   # Drive `version` of portfolio.json we last pulled/pushed
    "remote_md5": None,       # md5 of the bytes we last pulled/pushed (content lock)
    "local_sidecar": None,    # local copy kept when a pull replaced a different file
    "conflict_file": None,    # local sidecar holding the remote copy after a conflict
    "conflict_remote": None,  # Drive name of this instance's copy during a conflict
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


def remote_version(svc, fid):
    """Drive's `version` of file `fid` (a monotonically increasing string). Raises."""
    return remote_state(svc, fid)[0]


def remote_state(svc, fid):
    """(version, md5Checksum or None) of file `fid`. Raises."""
    meta = svc.files().get(fileId=fid, fields="version,md5Checksum").execute()
    return str(meta["version"]), meta.get("md5Checksum")


def _md5(data):
    return hashlib.md5(data).hexdigest()


def _create(svc, name, data, retry_any_failure):
    """Create `name`; returns the response dict (id, version). A hand-made folder may be invisible under the
    drive.file scope, so a failed create retries in "My Drive" root. Raises."""
    meta = {"name": name}
    if _folder():
        meta["parents"] = [_folder()]
    try:
        return svc.files().create(body=meta, media_body=_upload_media(data),
                                  fields="id,version").execute()
    except Exception as e:
        if not (_folder() or retry_any_failure):
            raise
        log.warning("Drive: create of %s failed (%s); retrying in My Drive root", name, e)
        meta.pop("parents", None)
        return svc.files().create(body=meta, media_body=_upload_media(data),
                                  fields="id,version").execute()


def _upload(name, local_path, retry_any_failure=False):
    """Create or update the remote `name` from local_path. Returns the new Drive
    version (str) or None when the response carried none, and the md5 of the bytes
    sent. Raises."""
    with open(local_path, "rb") as fh:      # read + close before the network
        data = fh.read()
    return _send(name, data, retry_any_failure), _md5(data)


def _send(name, data, retry_any_failure):
    svc = _client()
    fid = _find_id(svc, name)
    if fid:
        try:
            return _version_of(svc.files().update(
                fileId=fid, media_body=_upload_media(data),
                fields="id,version").execute())
        except Exception as e:
            # The cached id goes stale when the file is deleted/moved in Drive.
            if "404" not in str(e):
                raise
            log.warning("Drive: cached file id for %s stale (%s); re-resolving", name, e)
            _ids[name] = None
            fid = _find_id(svc, name)
            if fid:
                return _version_of(svc.files().update(
                    fileId=fid, media_body=_upload_media(data),
                    fields="id,version").execute())
    made = _create(svc, name, data, retry_any_failure)
    _ids[name] = made["id"]
    return _version_of(made)


def _version_of(resp):
    v = (resp or {}).get("version")
    return str(v) if v is not None else None


# --- portfolio.json (status-tracked; store_sync applies the data-loss guard) --- #
def _keep_differing_local(local_path, remote_bytes):
    """About to overwrite an existing local file with different bytes: keep it."""
    try:
        with open(local_path, "rb") as fh:
            local = fh.read()
    except FileNotFoundError:
        return
    if local == remote_bytes:
        return
    side = f"{local_path}.local-{_dt.datetime.now().strftime('%Y%m%d-%H%M')}"
    with open(side, "wb") as fh:
        fh.write(local)
    STATUS["local_sidecar"] = os.path.basename(side)
    log.warning("Drive pull is replacing a different local %s; the old local copy "
                "was saved to %s", local_path, side)


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
        # The version BEFORE the bytes: a write landing mid-download then reads as a
        # moved remote at the next push (conflict), never as the version we hold.
        version = remote_version(svc, fid)
        data = _download(svc, fid)
        json.loads(data.decode("utf-8"))            # validate before overwriting
        os.makedirs(os.path.dirname(local_path), exist_ok=True)
        if not local_path.endswith(".remote"):      # the local-wins sidecar is a scratch copy
            _keep_differing_local(local_path, data)
        # Atomic replace: readers load() lock-free and must never see a half file.
        tmp = f"{local_path}.pull.{os.getpid()}.tmp"
        with open(tmp, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, local_path)
        log.info("Drive: pulled portfolio.json (%d bytes)", len(data))
        STATUS["pull_result"] = "pulled"
        STATUS["remote_version"] = version
        STATUS["remote_md5"] = _md5(data)
        STATUS["last_error"] = None
        return "pulled"
    except Exception as e:
        log.error("Drive pull FAILED — push disabled until a pull succeeds "
                  "(protects the remote backup): %s", e)
        STATUS["pull_result"] = "error"
        STATUS["last_error"] = f"pull: {e}"
        return "error"


def _stash_remote(svc, fid, local_path):
    """Keep the remote copy beside the local file so neither version is lost."""
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M")
    side = f"{local_path}.remote-{stamp}"
    try:
        with open(side, "wb") as fh:
            fh.write(_download(svc, fid))
    except Exception as e:
        log.warning("Drive: could not save the remote copy on conflict: %s", e)
        return None
    return side


def _conflict_name():
    """portfolio.conflict-<host>-<YYYYMMDD-HHMM>.json: never the guarded name."""
    host = os.environ.get("RENDER_SERVICE_NAME") or socket.gethostname() or "host"
    host = re.sub(r"[^A-Za-z0-9-]+", "-", host).strip("-")[:24].strip("-") or "host"
    return f"portfolio.conflict-{host}-{_dt.datetime.now().strftime('%Y%m%d-%H%M')}.json"


def conflict_remote_name():
    """This conflict episode's Drive name, fixed at first use (one per episode)."""
    STATUS["conflict_remote"] = STATUS.get("conflict_remote") or _conflict_name()
    return STATUS["conflict_remote"]


def push_conflict_copy(local_path):
    """Upload the local file under this conflict episode's own Drive name. Render's
    disk is ephemeral: without this, edits made after a conflict die with the
    instance. store_sync re-pushes it on every save. True on success."""
    if not enabled():
        return False
    return drive_push_json(conflict_remote_name(), local_path)


def enter_conflict(local_path, why, remote_side):
    """Block the guarded push for the rest of this process, keep the remote copy
    (`remote_side`, already on local disk) and put the local copy on Drive."""
    STATUS["push_result"] = "conflict"
    STATUS["conflict_file"] = os.path.basename(remote_side) if remote_side else None
    STATUS["last_error"] = f"push: conflict - {why}"
    push_conflict_copy(local_path)
    log.error("Drive push BLOCKED (%s); remote copy saved to %s, local copy uploaded "
              "as %s", why, remote_side, STATUS["conflict_remote"])


def drive_push(local_path):
    """Upload local_path as the remote portfolio.json. True on success.

    Optimistic lock: the push goes ahead only when Drive's version still equals the
    one this process last pulled/pushed. A moved (or unreadable) version means
    another writer changed the remote, so it is saved beside the local file, the local
    file goes to Drive under a conflict name, and the push is refused with
    push_result == "conflict", which stays until restart."""
    if not enabled():
        return False
    if STATUS["push_result"] == "conflict":
        return False
    STATUS["last_push"] = _now()
    try:
        svc = _client()
        fid = _find_id(svc, REMOTE_NAME)
        if fid:
            try:
                current, md5 = remote_state(svc, fid)
                # Drive also bumps `version` without new bytes (seen live right after
                # our own upload): a moved version over the same content is ours.
                moved = (current != STATUS["remote_version"]
                         and not (md5 and md5 == STATUS.get("remote_md5")))
                why = f"remote version {current} != last seen {STATUS['remote_version']}"
            except Exception as e:
                moved, why = True, f"remote version unreadable: {e}"
            if moved:
                enter_conflict(local_path, why, _stash_remote(svc, fid, local_path))
                return False
        version, md5 = _upload(REMOTE_NAME, local_path)
    except Exception as e:
        log.warning("Drive push failed (kept local copy only): %s", e)
        STATUS["push_result"] = "error"
        STATUS["last_error"] = f"push: {e}"
        return False
    log.info("Drive: pushed portfolio.json")
    STATUS["push_result"] = "pushed"
    STATUS["remote_version"] = version
    STATUS["remote_md5"] = md5
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
