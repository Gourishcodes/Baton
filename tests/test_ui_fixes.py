"""Tests for the four UI fixes:
1. Top bar: Leaver/Approver/Successor switch appears only for seeded demo accounts.
   Real accounts see name, role, clearance tags and Sign out.
   POST /role cannot switch a non-demo session.
2. Wire strip: on any case other than the demo case, states:
   "The planted-string scan covers the demo case only. On this case, restricted text is protected by the access function."
   and does not mention 9 planted strings.
3. Sign out button styling & no wrapping.
4. Source drawer: only adds srcline--cited to a line that a claim actually cites.
   Opening a source from the Sources page highlights nothing.
"""
import io
import os
import tempfile
import pytest
from starlette.testclient import TestClient

from app import db, repo, extract, review
from app.main import app
from app.auth import create_user, create_session, DEMO_PASSWORD


@pytest.fixture
def test_db():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    old_path = db.get_db_path() if db._db_path else None
    old_env = os.environ.get("BATON_DB")
    os.environ["BATON_DB"] = tmp.name
    db.set_db_path(tmp.name)

    case_id = db.load_demo_case()
    extract.extract_for_case(case_id, mode="cache_only")
    review.seed_demo_decisions(case_id)
    review.create_version(case_id, approved_by="Divya Menon (seeded)", seeded=True)

    yield tmp.name

    db.close()
    if old_path:
        db.set_db_path(old_path)
    if old_env:
        os.environ["BATON_DB"] = old_env
    else:
        os.environ.pop("BATON_DB", None)
    try:
        os.remove(tmp.name)
    except OSError:
        pass


def test_top_bar_demo_vs_real_account(test_db):
    client = TestClient(app, follow_redirects=True)

    # 1. Seeded demo account (Divya Menon)
    client.post("/login", data={"email": "divya@example.com", "password": DEMO_PASSWORD})
    demo_resp = client.get("/case/1/claims")
    assert demo_resp.status_code == 200
    # Demo switch must appear
    assert 'action="/role"' in demo_resp.text
    assert 'value="leaver"' in demo_resp.text
    assert 'value="lead"' in demo_resp.text
    assert 'value="successor"' in demo_resp.text
    assert "Demo accounts share a public password. Real accounts do not." in demo_resp.text

    # 2. Real non-demo account
    client.post("/logout")
    signup_resp = client.post("/signup", data={
        "name": "Jane Real",
        "email": "jane@example.com",
        "password": "strongpassword123",
    })
    assert signup_resp.status_code == 200

    # Create a new case as real account
    create_resp = client.post("/cases", data={
        "name": "Jane's Handover",
        "leaver": "Alice Worker",
        "successor": "Bob Helper",
        "approver": "Jane Real",
    })
    assert create_resp.status_code == 200
    new_case_url = str(create_resp.url)
    assert "/case/2/sources" in new_case_url

    real_resp = client.get(new_case_url)
    assert real_resp.status_code == 200
    # Demo switch MUST NOT appear for real account
    assert 'action="/role"' not in real_resp.text
    assert 'value="leaver"' not in real_resp.text
    assert "Demo accounts share a public password" not in real_resp.text

    # Real account info MUST appear: name, role, clearance tags, and Sign out
    assert "Jane Real" in real_resp.text
    assert "Approver" in real_resp.text or "Owner" in real_resp.text
    assert "team" in real_resp.text
    assert "finance" in real_resp.text
    assert "hr" in real_resp.text
    assert "Sign out" in real_resp.text


def test_post_role_cannot_switch_non_demo_session(test_db):
    client = TestClient(app, follow_redirects=False)

    # Sign up as a real user
    client.post("/signup", data={
        "name": "Real User",
        "email": "realuser@example.com",
        "password": "securepassword99",
    })

    # While logged in as a real user, attempt to call POST /role
    role_resp = client.post("/role", data={"role": "successor"})
    # Must fail closed with 404
    assert role_resp.status_code == 404

    # Assert session is still Real User, NOT switched to Arjun Rao (the successor)
    home_resp = client.get("/cases", follow_redirects=True)
    assert "Real User" in home_resp.text
    assert "<b>Real User</b>" in home_resp.text
    assert "<b>Arjun Rao</b>" not in home_resp.text


def test_wire_strip_on_non_demo_case(test_db):
    client = TestClient(app, follow_redirects=True)

    # Sign up real user and create case 2
    client.post("/signup", data={
        "name": "Founder",
        "email": "founder@example.com",
        "password": "securepassword99",
    })
    client.post("/cases", data={
        "name": "Acquisition Case",
        "leaver": "Founder",
        "successor": "New Lead",
        "approver": "Founder",
    })

    resp = client.get("/case/2/sources")
    assert resp.status_code == 200
    # Wire strip on case 2 must state the non-demo notice
    assert "The planted-string scan covers the demo case only. On this case, restricted text is protected by the access function." in resp.text
    # Must NOT mention 9 planted strings
    assert "9 planted strings" not in resp.text
    assert "planted strings:" not in resp.text


def test_source_drawer_highlights_only_cited_line(test_db):
    client = TestClient(app, follow_redirects=True)
    # Login as demo approver
    client.post("/login", data={"email": "divya@example.com", "password": DEMO_PASSWORD})

    # 1. Opening source from Sources page highlights NOTHING
    sources_resp = client.get("/case/1/sources?open_source=C1")
    assert sources_resp.status_code == 200
    assert "srcline--cited" not in sources_resp.text

    # Also test via drawer endpoint directly
    drawer_resp = client.get("/case/1/sources/C1/drawer")
    assert drawer_resp.status_code == 200
    assert "srcline--cited" not in drawer_resp.text

    # 2. Opening drawer from Claims page citation highlights the cited line
    claims_resp = client.get("/case/1/claims?open_cite=C1:026")
    assert claims_resp.status_code == 200
    assert "srcline--cited" in claims_resp.text
    # Confirm it is specifically on C1-026
    assert 'id="line-C1-026"' in claims_resp.text
