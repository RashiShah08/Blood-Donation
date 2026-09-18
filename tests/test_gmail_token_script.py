"""The one-time helper that fetches a send-only Gmail API refresh token."""

import base64
import hashlib
import http.server
import importlib.util
import json
import threading
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

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


def write_json(tmp_path, data):
    path = tmp_path / "client_secret.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return str(path)


def test_desktop_client_file_is_read(tmp_path):
    path = write_json(
        tmp_path, {"installed": {"client_id": "1-abc.apps.googleusercontent.com", "client_secret": "GOCSPX-x"}}
    )
    assert helper.load_client_file(path) == ("1-abc.apps.googleusercontent.com", "GOCSPX-x")


def test_web_client_file_is_refused_with_a_fix(tmp_path):
    path = write_json(tmp_path, {"web": {"client_id": "1-abc.apps.googleusercontent.com", "client_secret": "s"}})
    with pytest.raises(helper.ClientError, match="Desktop app"):
        helper.load_client_file(path)


def test_missing_or_broken_files_explain_the_problem(tmp_path):
    with pytest.raises(helper.ClientError, match="Couldn"):
        helper.load_client_file(str(tmp_path / "nope.json"))
    broken = tmp_path / "broken.json"
    broken.write_text("not json", encoding="utf-8")
    with pytest.raises(helper.ClientError, match="isn"):
        helper.load_client_file(str(broken))


def test_common_paste_mistakes_are_caught_before_the_browser_opens():
    good_id = "1-abc.apps.googleusercontent.com"
    assert helper.check_client(good_id, "GOCSPX-secret") == []
    assert helper.check_client("GOCSPX-secret", good_id)  # swapped
    assert helper.check_client(good_id, "GOCSPX-sec\x16ret")  # Ctrl+V arrived as a control character
    assert helper.check_client(good_id, "GOCSPX sec")  # stray space
