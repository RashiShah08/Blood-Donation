"""The one-time helper that fetches a send-only Gmail API refresh token."""

import base64
import hashlib
import http.server
import importlib.util
import threading
import urllib.parse
import urllib.request
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "google_gmail_token.py"
spec = importlib.util.spec_from_file_location("google_gmail_token", SCRIPT)
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


def test_pkce_challenge_is_the_s256_hash_of_the_verifier():
    verifier, challenge = helper.pkce_pair()
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    assert challenge == expected and 43 <= len(verifier) <= 128


def test_consent_url_asks_only_to_send_email():
    url = helper.authorization_url("client-id", "http://127.0.0.1:5555/", "state-123", "challenge-abc")
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
    assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert query["scope"] == ["https://www.googleapis.com/auth/gmail.send"]  # no read access
    assert query["access_type"] == ["offline"] and query["prompt"] == ["consent"]
    assert query["code_challenge_method"] == ["S256"] and query["code_challenge"] == ["challenge-abc"]
    assert query["state"] == ["state-123"] and query["redirect_uri"] == ["http://127.0.0.1:5555/"]


def test_local_callback_captures_the_reply_from_google():
    server = http.server.HTTPServer(("127.0.0.1", 0), helper._CallbackHandler)
    server.result = None
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()
    with urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/?code=4%2Fabc&state=xyz", timeout=5) as reply:
        page = reply.read().decode()
    thread.join(timeout=5)
    server.server_close()
    assert server.result == {"code": "4/abc", "state": "xyz"}
    assert "close this tab" in page
