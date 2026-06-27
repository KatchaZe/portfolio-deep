"""
get_gdrive_token.py — one-time helper to obtain a Google Drive OAuth refresh token.

WHY: Render's free tier wipes data/portfolio.json on every restart/redeploy. The app
mirrors that file to YOUR Google Drive so it survives. To let the app write to your Drive
it needs a *refresh token* (a long-lived key tied to your own account). This script runs
ONCE on your computer, opens a browser so you approve access, then prints the three values
you paste into Render's Environment settings.

NOTE (2026): Google REMOVED the "Download JSON" button for OAuth clients, so this script
no longer needs client_secret.json. Instead you paste your Client ID + Client Secret when
asked. Get them from: Google Cloud Console -> APIs & Services -> Credentials -> your client.
  * Client ID is shown in full on that page.
  * Client secret is masked (****). If you don't have the full value, click "+ Add secret"
    to generate a new one — it is shown in full once; copy it.

(If you happen to still have an old client_secret.json in this folder, the script uses it
automatically and skips the prompts.)

USAGE (run in the project folder):
    pip install google-auth-oauthlib google-api-python-client
    python get_gdrive_token.py

Then copy the three GDRIVE_OAUTH_* lines it prints into Render -> Environment.
See GOOGLE_DRIVE_OAUTH_SETUP.md for the full walkthrough.
"""
import os
import sys

# Must match the scope the app requests in sources/gdrive_store.py. drive.file = the app
# can only touch files it creates itself (narrowest, safest scope — enough for portfolio.json).
SCOPES = ["https://www.googleapis.com/auth/drive.file"]

CLIENT_SECRET_FILE = "client_secret.json"


def _build_flow():
    """Return an InstalledAppFlow, either from a legacy client_secret.json (if present)
    or from a Client ID + Client Secret typed in by the user (the 2026 way)."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    # Path A (legacy): a downloaded JSON still works if you have one.
    if os.path.exists(CLIENT_SECRET_FILE):
        print(f"Using {CLIENT_SECRET_FILE} found in this folder.")
        return InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET_FILE, scopes=SCOPES)

    # Path B (current): build the config from values you paste in.
    print("No client_secret.json here — that's fine (Google no longer lets you download it).")
    print("Open Google Cloud Console -> APIs & Services -> Credentials -> your Desktop client.\n")
    client_id = input("Paste your Client ID:     ").strip()
    client_secret = input("Paste your Client Secret: ").strip()
    if not client_id or not client_secret:
        print("\nERROR: both Client ID and Client Secret are required. Run the script again.\n")
        sys.exit(1)

    client_config = {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }
    }
    return InstalledAppFlow.from_client_config(client_config, scopes=SCOPES)


def main():
    # Import here so a missing library gives a clear, friendly message.
    try:
        import google_auth_oauthlib  # noqa: F401
    except ImportError:
        print("\nERROR: required library missing. Install it first:")
        print("  pip install google-auth-oauthlib google-api-python-client\n")
        sys.exit(1)

    flow = _build_flow()

    # Run the browser consent flow. access_type='offline' + prompt='consent' FORCE Google to
    # return a refresh token (otherwise a repeat run may return none). port=0 = any free port.
    print("\nOpening your browser to approve Google Drive access...")
    print("If a warning says \"Google hasn't verified this app\", that is expected for your")
    print("own app — click Advanced / Continue, then Allow.\n")
    creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")

    # Verify a refresh token actually came back before celebrating.
    if not creds.refresh_token:
        print("\nERROR: no refresh token returned. This usually means you approved this app")
        print("before. Remove its access at https://myaccount.google.com/permissions, then")
        print("run this script again.\n")
        sys.exit(1)

    # Print the three values to paste into Render -> Environment.
    print("\n" + "=" * 70)
    print("SUCCESS — copy these THREE lines into Render -> Environment:")
    print("(refresh token = a key to your Drive — do NOT commit it or share it)")
    print("=" * 70)
    print(f"GDRIVE_OAUTH_CLIENT_ID     = {creds.client_id}")
    print(f"GDRIVE_OAUTH_CLIENT_SECRET = {creds.client_secret}")
    print(f"GDRIVE_OAUTH_REFRESH_TOKEN = {creds.refresh_token}")
    print("=" * 70 + "\n")


if __name__ == "__main__":
    main()
