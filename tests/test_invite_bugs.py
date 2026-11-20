"""Tests for invite links, anonymous access, existing member protection, and sole owner invariants."""
import json
from datetime import datetime, timezone, timedelta
import hashlib
import pytest
from starlette.testclient import TestClient

from app import main, db, repo
from app.auth import (
    create_user,
    create_invite,
    get_invite,
    redeem_invite,
    AlreadyMemberError,
    hash_password,
    seed_demo_accounts,
    Principal,
)


@pytest.fixture(autouse=True)
def setup_db():
    db.load_demo_case()
    seed_demo_accounts()


def test_anonymous_client_invite_flow():
    """An anonymous client views a valid invite twice without burning it,
    signs up through it, gets exact role and clearance, and the link now returns 404.
    """
    standard_404 = main.not_found_response().body.decode("utf-8")
    conn = db.get_conn()

    # 1. Setup a real case with an owner
    owner_id = create_user("owner_case_test@example.com", "Case Owner", "password12345")
    case_id = repo.create_case("Anon Invite Case", "Leaver", "Successor", "Case Owner", owner_id)

    # 2. Create invite for role="viewer", clearance=["team"]
    token = create_invite(case_id, "viewer", ["team"], owner_id)
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()

    # 3. Anonymous client (no cookies)
    anon_client = TestClient(main.app)

    # View once: still valid, unconsumed
    resp1 = anon_client.get(f"/invite/{token}")
    assert resp1.status_code == 200
    assert "Create account" in resp1.text
    assert "Sign in" in resp1.text
    inv_row1 = conn.execute("SELECT used_at FROM invites WHERE token_hash = ?", (token_hash,)).fetchone()
    assert inv_row1["used_at"] is None

    # View twice: still valid, unconsumed
    resp2 = anon_client.get(f"/invite/{token}")
    assert resp2.status_code == 200
    inv_row2 = conn.execute("SELECT used_at FROM invites WHERE token_hash = ?", (token_hash,)).fetchone()
    assert inv_row2["used_at"] is None

    # Sign up through the invite
    resp_signup = anon_client.post(
        "/signup",
        data={
            "name": "New Anon Member",
            "email": "new_anon_member@example.com",
            "password": "password12345",
            "invite": token,
        },
        follow_redirects=False,
    )
    assert resp_signup.status_code in (302, 303)
    assert f"/case/{case_id}/claims" in resp_signup.headers.get("location", "")

    # Confirm the new member has exactly the invite's role and clearance
    new_user = conn.execute("SELECT id FROM users WHERE email = 'new_anon_member@example.com'").fetchone()
    assert new_user is not None
    mem = conn.execute("SELECT * FROM memberships WHERE case_id = ? AND user_id = ?", (case_id, new_user["id"])).fetchone()
    assert mem is not None
    assert mem["role"] == "viewer"
    assert mem["is_owner"] == 0
    assert json.loads(mem["labels"]) == ["team"]

    # Confirm the invite is now consumed
    inv_row_after = conn.execute("SELECT used_at FROM invites WHERE token_hash = ?", (token_hash,)).fetchone()
    assert inv_row_after["used_at"] is not None

    # Confirm the same link now returns the standard 404
    resp3 = anon_client.get(f"/invite/{token}")
    assert resp3.status_code == 404
    assert resp3.text == standard_404


def test_existing_member_cannot_redeem_or_lose_membership():
    """Redeeming an invite never changes an existing membership.
    If the signed-in user is already a member of that case, show an inline banner
    'You are already a member of this case.' and consume nothing.
    """
    conn = db.get_conn()

    # Create case with owner (User A) having all clearances
    owner_id = create_user("owner_existing_test@example.com", "Existing Owner", "password12345")
    case_id = repo.create_case("Existing Member Case", "L", "S", "Existing Owner", owner_id)

    # Verify initial membership
    initial_mem = conn.execute("SELECT * FROM memberships WHERE case_id = ? AND user_id = ?", (case_id, owner_id)).fetchone()
    assert initial_mem["role"] == "approver"
    assert initial_mem["is_owner"] == 1
    assert json.loads(initial_mem["labels"]) == ["finance", "hr", "team"]

    # Create an invite for viewer with only [team]
    token = create_invite(case_id, "viewer", ["team"], owner_id)
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()

    client = TestClient(main.app)
    # Sign in as owner
    client.post("/login", data={"email": "owner_existing_test@example.com", "password": "password12345"})

    # Open the invite link while signed in as existing member
    resp = client.get(f"/invite/{token}")
    assert resp.status_code == 200
    assert "You are already a member of this case." in resp.text

    # Verify membership is UNCHANGED (still approver, owner, all clearances)
    mem_after_get = conn.execute("SELECT * FROM memberships WHERE case_id = ? AND user_id = ?", (case_id, owner_id)).fetchone()
    assert mem_after_get["role"] == "approver"
    assert mem_after_get["is_owner"] == 1
    assert json.loads(mem_after_get["labels"]) == ["finance", "hr", "team"]

    # Verify invite is NOT consumed
    inv_row = conn.execute("SELECT used_at FROM invites WHERE token_hash = ?", (token_hash,)).fetchone()
    assert inv_row["used_at"] is None

    # Attempt to accept via POST /invite/{token}/accept
    resp_post = client.post(f"/invite/{token}/accept", follow_redirects=False)
    # Redirects to case claims without changing membership or consuming invite
    assert resp_post.status_code in (302, 303)
    mem_after_post = conn.execute("SELECT * FROM memberships WHERE case_id = ? AND user_id = ?", (case_id, owner_id)).fetchone()
    assert mem_after_post["role"] == "approver"
    assert mem_after_post["is_owner"] == 1
    assert json.loads(mem_after_post["labels"]) == ["finance", "hr", "team"]

    inv_row_post = conn.execute("SELECT used_at FROM invites WHERE token_hash = ?", (token_hash,)).fetchone()
    assert inv_row_post["used_at"] is None


def test_sole_owner_protection():
    """A case always keeps at least one owner.
    Refuse any role or clearance change, and any invite redemption, that would leave a case with no owner.
    """
    conn = db.get_conn()

    # Create a case with single owner
    owner_id = create_user("sole_owner@example.com", "Sole Owner", "password12345")
    case_id = repo.create_case("Sole Owner Case", "L", "S", "Sole Owner", owner_id)

    client = TestClient(main.app)
    client.post("/login", data={"email": "sole_owner@example.com", "password": "password12345"})

    # 1. Attempt to demote sole owner's role to 'viewer' -> refused (400)
    resp_demote = client.post(
        f"/case/{case_id}/members/{owner_id}",
        data={"role": "viewer", "labels": ["team", "finance", "hr"]},
    )
    assert resp_demote.status_code == 400
    assert "A case must always keep at least one owner." in resp_demote.text

    # Verify role was not changed
    mem = conn.execute("SELECT * FROM memberships WHERE case_id = ? AND user_id = ?", (case_id, owner_id)).fetchone()
    assert mem["role"] == "approver"
    assert mem["is_owner"] == 1

    # 2. Attempt to strip all clearance from sole owner -> refused (400)
    resp_strip = client.post(
        f"/case/{case_id}/members/{owner_id}",
        data={"role": "approver", "labels": []},
    )
    assert resp_strip.status_code == 400
    assert "A case must always keep at least one owner." in resp_strip.text

    # Verify labels were not stripped
    mem2 = conn.execute("SELECT * FROM memberships WHERE case_id = ? AND user_id = ?", (case_id, owner_id)).fetchone()
    assert json.loads(mem2["labels"]) == ["finance", "hr", "team"]

    # 3. Direct call to redeem_invite cannot alter existing membership or leave 0 owners
    token = create_invite(case_id, "viewer", ["team"], owner_id)
    with pytest.raises(AlreadyMemberError):
        redeem_invite(token, owner_id)
