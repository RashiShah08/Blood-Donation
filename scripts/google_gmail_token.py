"""Get a Gmail API refresh token that can only send email (one-time setup).

Run it in your own terminal, not through a chat or CI log, because it prints a secret:

    python scripts/google_gmail_token.py

It asks for the client ID and secret of a Google Cloud OAuth client of type "Desktop app",
opens Google's consent page in your browser, and prints the refresh token to store as
GMAIL_REFRESH_TOKEN. The only permission requested is gmail.send: the token can send mail
as you but can't read, change or delete anything in the mailbox. Nothing is written to disk.
"""

import base64
import getpass
import hashlib
import http.server
import json
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

SCOPE = "https://www.googleapis.com/auth/gmail.send"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105 (a URL, not a secret)
WAIT_SECONDS = 300


def pkce_pair() -> tuple[str, str]:
    """A PKCE verifier and its S256 challenge (RFC 7636)."""
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=")
    return verifier, challenge.decode("ascii")


def authorization_url(client_id: str, redirect_uri: str, state: str, challenge: str) -> str:
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": SCOPE,
        "access_type": "offline",  # ask for a refresh token
        "prompt": "consent",  # always return one, even if you authorised before
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    return f"{AUTH_URL}?{urllib.parse.urlencode(params)}"


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 (http.server naming)
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        if "code" in query or "error" in query:
            self.server.result = {key: values[0] for key, values in query.items()}
            message = "Done. You can close this tab and go back to the terminal."
        else:
            message = "Waiting for Google..."
        body = f"<!doctype html><title>BloodConnect</title><p style='font:16px sans-serif'>{message}</p>".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # keep the terminal quiet
        pass


def main() -> int:
    print(__doc__.split("\n\n")[0], "\n")
    client_id = input("OAuth client ID: ").strip()
    client_secret = getpass.getpass("OAuth client secret (input hidden): ").strip()
    if not client_id or not client_secret:
        print("Both the client ID and the client secret are needed.")
        return 1

    server = http.server.HTTPServer(("127.0.0.1", 0), _CallbackHandler)
    server.result = None
    server.timeout = 2  # wake up regularly so the overall deadline is honoured
    redirect_uri = f"http://127.0.0.1:{server.server_port}/"
    state = secrets.token_urlsafe(24)
    verifier, challenge = pkce_pair()
    url = authorization_url(client_id, redirect_uri, state, challenge)

    print("\nOpening Google in your browser. If it doesn't open, visit this address:\n")
    print(url, "\n")
    webbrowser.open(url)
    deadline = time.monotonic() + WAIT_SECONDS
    while server.result is None and time.monotonic() < deadline:
        server.handle_request()
    server.server_close()

    result = server.result
    if result is None:
        print(f"No reply from Google within {WAIT_SECONDS // 60} minutes. Please run this again.")
        return 1
    if result.get("error"):
        print(f"Google returned an error: {result['error']}")
        return 1
    if result.get("state") != state:
        print("The reply didn't match this request (state mismatch), so it was ignored. Please run again.")
        return 1

    form = urllib.parse.urlencode(
        {
            "code": result["code"],
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
            "code_verifier": verifier,
        }
    ).encode("ascii")
    request = urllib.request.Request(TOKEN_URL, data=form, method="POST")  # noqa: S310 (fixed https URL)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:  # noqa: S310
            tokens = json.load(response)
    except urllib.error.HTTPError as exc:
        print(f"Google refused the exchange: HTTP {exc.code} {exc.read(300).decode('utf-8', 'replace')}")
        return 1

    refresh_token = tokens.get("refresh_token")
    if not refresh_token:
        print("Google didn't return a refresh token. Remove the app's access at myaccount.google.com/permissions")
        print("and run this again.")
        return 1
    if tokens.get("scope") != SCOPE:
        print(f"Note: Google granted these permissions: {tokens.get('scope')}")
    print("\nSuccess. Add these to your hosting provider's environment variables:\n")
    print(f"  GMAIL_CLIENT_ID={client_id}")
    print("  GMAIL_CLIENT_SECRET=<the secret you just typed>")
    print(f"  GMAIL_REFRESH_TOKEN={refresh_token}")
    print("  GMAIL_SENDER=<the Gmail address you just signed in with>\n")
    print("Treat the refresh token like a password: it can send email as that account.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
