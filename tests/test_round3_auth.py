"""Tests for Round 3 authentication, sessions, invites, and CSRF protection."""
from datetime import datetime, timezone, timedelta
import json
import os
import sys
import tempfile
import pytest
from starlette.testclient import TestClient

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app import db, repo, extract, review, main
from app.auth import (
    seed_demo_accounts,
    create_session,
    create_user,
    create_invite,
    hash_password,
    login_as,
    DEMO_PASSWORD,
)


@pytest.fixture(autouse=True)
def isolated_db():
    """Run each test with an isolated database."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    db.set_db_path(tmp.name)
    yield tmp.name
    db.close()
    try:
        os.unlink(tmp.name)
    except Exception:
        pass


def test_anonymous_get_every_route_redirect_or_404_no_case_text():
    """Anonymous GET of every route returns a redirect or the standard 404
    and never contains case text.
    """
    db.load_demo_case()
    seed_demo_accounts()
    extract.extract_for_case(1, mode="cache_only")
    review.seed_demo_decisions(1)
    review.create_version(1, approved_by="Divya Menon (seeded)", seeded=True)

    client = TestClient(main.app)
    client.cookies.clear()

    page_routes = [
        "/",
        "/trust",
        "/case/1/sources",
        "/case/1/claims",
        "/case/1/gaps",
        "/case/1/conflicts",
        "/case/1/timeline",
        "/case/1/handover",
        "/case/1/handover/print",
        "/case/1/approvals",
        "/case/1/members",
    ]

    for route in page_routes:
        resp = client.get(route, follow_redirects=False)
        assert resp.status_code in (302, 303, 307), (
            f"HTML page route {route} did not redirect to /login: {resp.status_code}"
        )
        assert "/login" in resp.headers.get("location", "")
        assert "PW-CONTRACT-7731" not in resp.text
        assert "Dispatch notifications" not in resp.text
        assert "Tanvi Shetty" not in resp.text

    # Drawer / API / Export route returns standard 404
    for non_html_route in ["/case/1/claims/1/source", "/case/1/handover.md"]:
        r = client.get(non_html_route, follow_redirects=False)
        assert r.status_code == 404
        assert "PW-CONTRACT-7731" not in r.text
        assert "Dispatch notifications" not in r.text
        assert "Tanvi Shetty" not in r.text


def test_tampered_expired_and_deleted_sessions_behave_as_anonymous():
    """A tampered cookie, an expired session and a deleted session behave as anonymous."""
    db.load_demo_case()
    seed_demo_accounts()
    extract.extract_for_case(1, mode="cache_only")
    review.seed_demo_decisions(1)
    review.create_version(1, approved_by="Divya Menon (seeded)", seeded=True)

    client = TestClient(main.app)

    # 1. Tampered cookie
    client.cookies["baton_session"] = "tampered_session_token_1234567890"
    resp_tampered = client.get("/case/1/claims", follow_redirects=False)
    assert resp_tampered.status_code in (302, 303, 307)
    assert "/login" in resp_tampered.headers.get("location", "")
    assert "Dispatch notifications" not in resp_tampered.text

    # 2. Expired session
    conn = db.get_conn()
    user = conn.execute("SELECT id FROM users WHERE email = 'divya@example.com'").fetchone()
    expired_token = "expired_session_token_1234567890"
    import hashlib
    token_hash = hashlib.sha256(expired_token.encode("utf-8")).hexdigest()
    past_iso = (datetime.now(timezone.utc) - timedelta(hours=13)).isoformat()
    conn.execute(
        "INSERT INTO sessions (token_hash, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
        (token_hash, user["id"], past_iso, past_iso)
    )
    conn.commit()

    client.cookies["baton_session"] = expired_token
    resp_expired = client.get("/case/1/claims", follow_redirects=False)
    assert resp_expired.status_code in (302, 303, 307)
    assert "/login" in resp_expired.headers.get("location", "")
    assert "Dispatch notifications" not in resp_expired.text

    # 3. Deleted session
    real_token = create_session(user["id"])
    real_hash = hashlib.sha256(real_token.encode("utf-8")).hexdigest()
    conn.execute("DELETE FROM sessions WHERE token_hash = ?", (real_hash,))
    conn.commit()

    client.cookies["baton_session"] = real_token
    resp_deleted = client.get("/case/1/claims", follow_redirects=False)
    assert resp_deleted.status_code in (302, 303, 307)
    assert "/login" in resp_deleted.headers.get("location", "")
    assert "Dispatch notifications" not in resp_deleted.text


def test_member_of_case_x_requesting_case_y_gets_byte_identical_404():
    """A member of case X only requesting any /case/Y/... route gets the byte-identical 404."""
    db.load_demo_case()
    seed_demo_accounts()

    conn = db.get_conn()
    conn.execute(
        "INSERT INTO cases (id, name, leaver, successor, approver, status, created_at) "
        "VALUES (2, 'Case Two', 'Bob', 'Alice', 'Charlie', 'open', ?)",
        (db.now_iso(),)
    )
    user_id = create_user("case2only@example.com", "Case Two Member", "securepassword123")
    conn.execute(
        "INSERT INTO memberships (case_id, user_id, role, is_owner, labels, added_at) VALUES (?, ?, 'viewer', 0, '[\"team\"]', ?)",
        (2, user_id, db.now_iso())
    )
    conn.commit()

    client = TestClient(main.app)
    # Login as case2only user
    resp = client.post("/login", data={"email": "case2only@example.com", "password": "securepassword123"})
    assert resp.status_code in (200, 302, 303)

    # Request route from Case 1
    resp_c1 = client.get("/case/1/claims")
    assert resp_c1.status_code == 404

    # Compare with canonical not_found_response
    standard_404 = main.not_found_response()
    assert resp_c1.text == standard_404.body.decode("utf-8")


def test_clearance_change_takes_effect_on_next_request():
    """A viewer with [team] never receives a finance or HR item;
    after the owner adds finance, the next request does.
    """
    db.load_demo_case()
    seed_demo_accounts()
    extract.extract_for_case(1, mode="cache_only")
    review.seed_demo_decisions(1)
    review.create_version(1, approved_by="Divya Menon (seeded)", seeded=True)

    user_id = create_user("viewer_member@example.com", "Viewer Member", "password12345")
    conn = db.get_conn()
    conn.execute(
        "INSERT INTO memberships (case_id, user_id, role, is_owner, labels, added_at) "
        "VALUES (1, ?, 'viewer', 0, '[\"team\"]', ?)",
        (user_id, db.now_iso())
    )
    conn.commit()

    client = TestClient(main.app)
    login_resp = client.post("/login", data={"email": "viewer_member@example.com", "password": "password12345"})
    assert login_resp.status_code in (200, 302, 303)

    # 1. First request with [team] only: finance and HR items are hidden/redacted
    resp1 = client.get("/case/1/claims")
    assert resp1.status_code == 200
    assert "Divya approved signing the Pingwire contract" not in resp1.text
    assert "Ritu Bhandari" not in resp1.text

    # 2. Owner updates clearance to include finance
    conn.execute(
        "UPDATE memberships SET labels = ? WHERE case_id = 1 AND user_id = ?",
        (json.dumps(["finance", "team"]), user_id)
    )
    conn.commit()

    # 3. Next request with same session cookie receives finance items, but not HR items
    resp2 = client.get("/case/1/claims")
    assert resp2.status_code == 200
    assert "Divya approved signing the Pingwire contract" in resp2.text
    assert "Ritu Bhandari" not in resp2.text


def test_invites_single_use_expiry_wrong_token_gives_standard_404():
    """Invites: single use, expiry, wrong token gives the standard 404."""
    db.load_demo_case()
    seed_demo_accounts()

    standard_404 = main.not_found_response().body.decode("utf-8")
    client = TestClient(main.app)

    # 1. Invalid / made up token
    resp_invalid = client.get("/invite/made_up_token_that_does_not_exist")
    assert resp_invalid.status_code == 404
    assert resp_invalid.text == standard_404

    # 2. Expired invite
    expired_token = create_invite(1, "viewer", ["team"], 1)
    import hashlib
    token_hash = hashlib.sha256(expired_token.encode("utf-8")).hexdigest()
    conn = db.get_conn()
    past_iso = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
    conn.execute("UPDATE invites SET expires_at = ? WHERE token_hash = ?", (past_iso, token_hash))
    conn.commit()

    resp_expired = client.get(f"/invite/{expired_token}")
    assert resp_expired.status_code == 404
    assert resp_expired.text == standard_404

    # 3. Valid invite redeemed once
    valid_token = create_invite(1, "viewer", ["team", "finance"], 1)
    user_id = create_user("invited_user@example.com", "Invited User", "password12345")
    client.post("/login", data={"email": "invited_user@example.com", "password": "password12345"})

    # Viewing invite does not consume it
    resp_view = client.get(f"/invite/{valid_token}")
    assert resp_view.status_code == 200

    # Redeeming invite while logged in redirects to case claims
    resp_redeem = client.post(f"/invite/{valid_token}/accept", follow_redirects=False)
    assert resp_redeem.status_code in (302, 303)
    assert "/case/1/claims" in resp_redeem.headers.get("location", "")

    # Check that membership was created with specified clearance
    mem = conn.execute("SELECT * FROM memberships WHERE case_id = 1 AND user_id = ?", (user_id,)).fetchone()
    assert mem is not None
    assert json.loads(mem["labels"]) == ["finance", "team"]

    # 4. Same invite token used a second time gives standard 404
    resp_reused = client.get(f"/invite/{valid_token}")
    assert resp_reused.status_code == 404
    assert resp_reused.text == standard_404


def test_login_throttle_demo_not_throttled_and_real_per_ip_and_pair():
    """Login throttle:
    - Demo accounts (is_demo=1) are not throttled at all.
    - Five wrong passwords for a demo email from one IP, then the correct password still signs in.
    - Five wrong passwords for a real account from one IP locks that (email, IP) pair.
    - Five wrong passwords for a real account from one IP does not block another IP.
    - Separate per-IP cap of 20 failures in 15 minutes blocks that IP.
    """
    db.load_demo_case()
    seed_demo_accounts()

    client = TestClient(main.app)

    # 1. Five wrong passwords for demo email from one IP
    for _ in range(5):
        resp = client.post("/login", data={"email": "divya@example.com", "password": "wrongpassword"})
        assert resp.status_code == 401
        assert "Invalid email or password" in resp.text

    # Then the correct password still signs in immediately
    succ_resp = client.post(
        "/login",
        data={"email": "divya@example.com", "password": DEMO_PASSWORD},
        follow_redirects=False,
    )
    assert succ_resp.status_code in (200, 302, 303)
    assert "/case/1/claims" in succ_resp.headers.get("location", "")

    # 2. Real account: five wrong passwords from IP A locks that (email, IP A) pair
    create_user("realuser@example.com", "Real User", "realpassword123")
    ip_a_headers = {"x-forwarded-for": "forged_client, 203.0.113.10"}
    for _ in range(4):
        r = client.post(
            "/login",
            data={"email": "realuser@example.com", "password": "badpassword"},
            headers=ip_a_headers,
        )
        assert r.status_code == 401

    # 5th failure from IP A locks out (realuser@example.com, IP A)
    r5 = client.post(
        "/login",
        data={"email": "realuser@example.com", "password": "badpassword"},
        headers=ip_a_headers,
    )
    assert r5.status_code == 429
    assert "Try again later" in r5.text

    # 3. Five wrong passwords for a real account from one IP does NOT block another IP
    ip_b_headers = {"x-forwarded-for": "forged_client, 203.0.113.20"}
    r_ip_b = client.post(
        "/login",
        data={"email": "realuser@example.com", "password": "realpassword123"},
        headers=ip_b_headers,
        follow_redirects=False,
    )
    assert r_ip_b.status_code in (200, 302, 303)

    # 4. Separate per-IP cap of 20 failures in 15 minutes
    ip_c_headers = {"x-forwarded-for": "forged_client, 203.0.113.30"}
    for i in range(19):
        client.post(
            "/login",
            data={"email": f"attacker{i}@example.com", "password": "badpassword"},
            headers=ip_c_headers,
        )
    # 20th failure triggers the per-IP cap
    r20 = client.post(
        "/login",
        data={"email": "attacker19@example.com", "password": "badpassword"},
        headers=ip_c_headers,
    )
    assert r20.status_code == 429
    assert "Try again later" in r20.text

    # Any other account from IP C is also throttled
    r_blocked = client.post(
        "/login",
        data={"email": "anyone@example.com", "password": "anypassword"},
        headers=ip_c_headers,
    )
    assert r_blocked.status_code == 429

    # But IP D is unaffected
    ip_d_headers = {"x-forwarded-for": "forged_client, 203.0.113.40"}
    r_ip_d = client.post(
        "/login",
        data={"email": "anyone@example.com", "password": "anypassword"},
        headers=ip_d_headers,
    )
    assert r_ip_d.status_code == 401


def test_demo_accounts_cannot_change_shared_state():
    """Demo accounts cannot change shared state:
    - The Members page is read-only for them.
    - Member edits and invite creation return the standard 404 for is_demo users.
    - seed_demo_accounts re-asserts the four demo memberships (role and labels)
      from code on every boot and inside Reset demo.
    """
    db.load_demo_case()
    seed_demo_accounts()

    client = TestClient(main.app)

    # Sign in as demo Divya (lead / approver, owner of case 1)
    login_as(client, "lead")

    # 1. Members page is read-only for demo accounts
    resp_members = client.get("/case/1/members")
    assert resp_members.status_code == 200
    assert "read-only" in resp_members.text.lower()
    # No edit/save buttons
    assert 'class="btn btn--quiet">Save</button>' not in resp_members.text

    # 2. As demo Divya, POST a clearance change for member 3 (Arjun) -> returns 404
    standard_404 = main.not_found_response().body.decode("utf-8")
    resp_edit = client.post(
        "/case/1/members/3",
        data={"role": "viewer", "labels": ["hr", "finance"]},
    )
    assert resp_edit.status_code == 404
    assert resp_edit.text == standard_404

    # Assert membership is unchanged
    conn = db.get_conn()
    arjun_mem = conn.execute("SELECT * FROM memberships WHERE case_id = 1 AND user_id = 3").fetchone()
    assert arjun_mem["role"] == "successor"
    assert json.loads(arjun_mem["labels"]) == ["team"]

    # 3. As demo Divya, POST an invite creation -> returns 404
    resp_inv = client.post(
        "/case/1/invites",
        data={"role": "viewer", "labels": ["team"]},
    )
    assert resp_inv.status_code == 404
    assert resp_inv.text == standard_404
    # Assert no invites exist
    inv_count = conn.execute("SELECT COUNT(*) as cnt FROM invites").fetchone()["cnt"]
    assert inv_count == 0

    # 4. Alter a membership directly in the database
    conn.execute(
        "UPDATE memberships SET role = 'viewer', labels = '[\"finance\"]' WHERE case_id = 1 AND user_id = 1"
    )
    conn.commit()
    altered_mem = conn.execute("SELECT * FROM memberships WHERE case_id = 1 AND user_id = 1").fetchone()
    assert altered_mem["role"] == "viewer"
    assert json.loads(altered_mem["labels"]) == ["finance"]

    # 5. Call Reset demo (POST /demo/reset)
    resp_reset = client.post("/demo/reset", follow_redirects=False)
    assert resp_reset.status_code in (200, 302, 303)

    # Assert Reset demo restores the membership altered directly in the database
    restored_mem = conn.execute("SELECT * FROM memberships WHERE case_id = 1 AND user_id = 1").fetchone()
    assert restored_mem["role"] == "approver"
    assert restored_mem["is_owner"] == 1
    assert json.loads(restored_mem["labels"]) == ["finance", "hr", "team"]


def test_client_ip_trusted_proxy_hops_and_forged_first_value():
    """Client IP helper:
    - Reads TRUSTED_PROXY_HOPS places from the right (env, default 1).
    - Never uses the first entry (which can be forged by the client).
    - Falls back to socket address.
    """
    from starlette.requests import Request
    from app.auth import get_client_ip

    def make_req(headers=None, client_host="10.20.30.40"):
        raw_headers = []
        if headers:
            for k, v in headers.items():
                raw_headers.append((k.lower().encode("latin-1"), v.encode("latin-1")))
        scope = {
            "type": "http",
            "headers": raw_headers,
            "client": (client_host, 54321),
        }
        return Request(scope)

    # 1. Default TRUSTED_PROXY_HOPS = 1
    os.environ["TRUSTED_PROXY_HOPS"] = "1"

    # Forged first value: "1.1.1.1, 203.0.113.195"
    # Entry 1 from right is "203.0.113.195" (not the first entry)
    req1 = make_req(headers={"x-forwarded-for": "1.1.1.1, 203.0.113.195"})
    assert get_client_ip(req1) == "203.0.113.195"

    # Single entry: "1.1.1.1" -> This IS the first entry! Never use it; fall back to socket
    req_single = make_req(headers={"x-forwarded-for": "1.1.1.1"}, client_host="192.168.1.50")
    assert get_client_ip(req_single) == "192.168.1.50"

    # 2. TRUSTED_PROXY_HOPS = 2
    os.environ["TRUSTED_PROXY_HOPS"] = "2"

    # "1.1.1.1, 203.0.113.195, 10.0.0.1" -> Entry 2 from right is "203.0.113.195"
    req2 = make_req(headers={"x-forwarded-for": "1.1.1.1, 203.0.113.195, 10.0.0.1"})
    assert get_client_ip(req2) == "203.0.113.195"

    # 2 entries with TRUSTED_PROXY_HOPS=2: "1.1.1.1, 10.0.0.1"
    # Entry 2 from right would be index 0 (the first entry!) -> Never use it; fall back to socket
    req_short = make_req(headers={"x-forwarded-for": "1.1.1.1, 10.0.0.1"}, client_host="192.168.1.60")
    assert get_client_ip(req_short) == "192.168.1.60"

    # 3. No header -> fallback to socket
    req_none = make_req(headers={}, client_host="192.168.1.70")
    assert get_client_ip(req_none) == "192.168.1.70"

    # Clean up env
    os.environ["TRUSTED_PROXY_HOPS"] = "1"


def test_signup_respects_max_users_and_password_length():
    """Sign-up respects MAX_USERS and rejects passwords shorter than 10 chars."""
    db.load_demo_case()
    seed_demo_accounts()

    client = TestClient(main.app)

    # 1. Short password (< 10 chars)
    resp_short = client.post(
        "/signup",
        data={"name": "Alice", "email": "alice@example.com", "password": "short"}
    )
    assert resp_short.status_code == 400
    assert "Password must be at least 10 characters" in resp_short.text

    # 2. MAX_USERS limit
    os.environ["MAX_USERS"] = "5"
    conn = db.get_conn()
    # Demo accounts already take 4 users, let's create a 5th
    create_user("user5@example.com", "User Five", "longpassword123")

    # 6th user signup should be rejected
    resp_capped = client.post(
        "/signup",
        data={"name": "User Six", "email": "user6@example.com", "password": "longpassword123"}
    )
    assert resp_capped.status_code == 400
    assert "Registration limit reached" in resp_capped.text
    os.environ["MAX_USERS"] = "200"


def test_csrf_origin_and_referer_rejection():
    """A POST with a foreign Origin or Referer is rejected; matching or absent is allowed."""
    db.load_demo_case()
    seed_demo_accounts()

    client = TestClient(main.app)

    # 1. Foreign origin is rejected (403)
    resp_foreign = client.post(
        "/login",
        data={"email": "divya@example.com", "password": DEMO_PASSWORD},
        headers={"origin": "http://evil.com", "host": "testserver"},
    )
    assert resp_foreign.status_code == 403
    assert "Cross-origin request rejected" in resp_foreign.text

    # 2. Foreign referer without origin is rejected (403)
    resp_foreign_ref = client.post(
        "/login",
        data={"email": "divya@example.com", "password": DEMO_PASSWORD},
        headers={"referer": "http://evil.com/phish", "host": "testserver"},
    )
    assert resp_foreign_ref.status_code == 403

    # 3. Same-host origin is allowed
    resp_same_host = client.post(
        "/login",
        data={"email": "divya@example.com", "password": DEMO_PASSWORD},
        headers={"origin": "http://testserver", "host": "testserver"},
        follow_redirects=False,
    )
    assert resp_same_host.status_code in (200, 302, 303)

    # 4. Neither origin nor referer present is allowed
    resp_no_csrf_hdr = client.post(
        "/login",
        data={"email": "divya@example.com", "password": DEMO_PASSWORD},
        headers={"host": "testserver"},
        follow_redirects=False,
    )
    assert resp_no_csrf_hdr.status_code in (200, 302, 303)
