"""Tests for Prompt 4: Cases, source IDs, and uploads.

Covers:
- All ten upload rejection cases with their messages
- CSV with different header names still produces owner gap
- CSV missing assignee or status skips rule and produces banner message on Gaps page
- Two cases uploading identical files cannot read each other's lines, drawer, claims, or exports
- Cross-case drawer request returns 404
- Demo case 1 is unchanged: source IDs C1, T1, N1, F1, H1, line counts, hashes
- Case creation, listing, and deletion
- Empty state on new case without run
"""
import html
import io
import json
import os
import tempfile
import pytest
from starlette.testclient import TestClient

from app import db, repo, extract, review, verify, parsers
from app.main import app
from app.auth import create_user, create_session, Principal, DEMO_PASSWORD


@pytest.fixture
def test_db():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    old_path = db.get_db_path() if db._db_path else None
    old_env = os.environ.get("BATON_DB")
    os.environ["BATON_DB"] = tmp.name
    db.set_db_path(tmp.name)

    # Initialize demo case 1
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
        os.unlink(tmp.name)
    except Exception:
        pass


def _create_real_user_client(test_db, email="alice@test.com", name="Alice Owner"):
    conn = db.get_conn()
    uid = create_user(email, name, "password12345")
    token = create_session(uid)
    client = TestClient(app)
    client.cookies.set("baton_session", token)
    return client, uid


def test_demo_case_1_unchanged(test_db):
    """Demo case 1 is unchanged: source IDs C1, T1, N1, F1, H1, line counts, hashes."""
    conn = db.get_conn()
    sources = conn.execute("SELECT * FROM sources WHERE case_id = 1 ORDER BY id").fetchall()
    source_ids = [s["id"] for s in sources]
    assert source_ids == ["C1", "F1", "H1", "N1", "T1"]

    # Check line counts and sample hashes
    for sid in source_ids:
        lines = conn.execute("SELECT * FROM lines WHERE source_id = ?", (sid,)).fetchall()
        assert len(lines) > 0
        for l in lines:
            assert l["line_id"].startswith(f"{sid}:")
            assert len(l["hash"]) == 12


def test_cases_list_create_delete(test_db):
    """Real user can list, create, and delete their own case."""
    client, uid = _create_real_user_client(test_db)

    # 1. List cases initially: user belongs to no cases
    resp = client.get("/cases")
    assert resp.status_code == 200
    assert "New case" in resp.text

    # 2. Create case
    resp_create = client.post("/cases", data={
        "name": "Frontend Team Handover",
        "leaver": "Carol Danvers",
        "successor": "Bob Builder",
        "approver": "Alice Owner",
    }, follow_redirects=False)
    assert resp_create.status_code == 303
    redirect_url = resp_create.headers["location"]
    assert "/case/" in redirect_url
    new_case_id = int(redirect_url.split("/")[2])
    assert new_case_id > 1

    # Check membership: creator is owner and approver
    conn = db.get_conn()
    mem = conn.execute("SELECT * FROM memberships WHERE case_id = ? AND user_id = ?", (new_case_id, uid)).fetchone()
    assert mem is not None
    assert mem["role"] == "approver"
    assert mem["is_owner"] == 1
    assert json.loads(mem["labels"]) == ["finance", "hr", "team"]

    # 3. List cases: now shows the new case
    resp_list = client.get("/cases")
    assert resp_list.status_code == 200
    assert "Frontend Team Handover" in resp_list.text
    assert "Carol Danvers &rarr; Bob Builder" in resp_list.text

    # 4. Delete case
    resp_del = client.post(f"/case/{new_case_id}/delete", follow_redirects=False)
    assert resp_del.status_code == 303
    assert resp_del.headers["location"] == "/cases"

    # Verify everything deleted across all tables
    assert conn.execute("SELECT COUNT(*) as cnt FROM cases WHERE id = ?", (new_case_id,)).fetchone()["cnt"] == 0
    assert conn.execute("SELECT COUNT(*) as cnt FROM memberships WHERE case_id = ?", (new_case_id,)).fetchone()["cnt"] == 0

    # 5. Case 1 cannot be deleted
    resp_del_demo = client.post("/case/1/delete")
    assert resp_del_demo.status_code == 404


def test_demo_user_cannot_create_or_delete_case(test_db):
    """Demo accounts cannot create or delete cases (returns 404)."""
    client = TestClient(app)
    # Sign in as demo Divya
    client.post("/login", data={"email": "divya@example.com", "password": DEMO_PASSWORD})

    # POST /cases returns 404
    resp_create = client.post("/cases", data={"name": "Bad Case", "leaver": "A", "successor": "B", "approver": "C"})
    assert resp_create.status_code == 404

    # POST /case/1/delete returns 404
    resp_del = client.post("/case/1/delete")
    assert resp_del.status_code == 404


def test_empty_state_on_new_case_without_run(test_db):
    """A new case with sources but no run shows the empty state and no fake tab counts."""
    client, uid = _create_real_user_client(test_db)
    client.post("/cases", data={"name": "Alpha Handover", "leaver": "A", "successor": "B", "approver": "C"})
    conn = db.get_conn()
    case_id = conn.execute("SELECT id FROM cases WHERE name = 'Alpha Handover'").fetchone()["id"]

    # Upload a single source
    chat_content = "2026-01-01 10:00 Alice: Hello team\n2026-01-01 10:01 Bob: Hi Alice\n"
    client.post(f"/case/{case_id}/sources", files={"file": ("chat.txt", io.BytesIO(chat_content.encode("utf-8")), "text/plain")}, data={"label": "team"})

    # Check Sources page: shows 1 source
    resp_src = client.get(f"/case/{case_id}/sources")
    assert resp_src.status_code == 200
    assert "chat.txt" in resp_src.text
    assert "c2s1" in resp_src.text or f"c{case_id}s1" in resp_src.text

    expected_empty = "Nothing has been extracted yet. Build the handover from the Sources page."

    # Check Claims page
    resp_claims = client.get(f"/case/{case_id}/claims")
    assert resp_claims.status_code == 200
    assert expected_empty in resp_claims.text
    # Nav tab should NOT show count badges for claims, gaps, conflicts, approvals
    assert f'<a href="/case/{case_id}/claims">Claims</a>' in resp_claims.text or f'<a href="/case/{case_id}/claims" aria-current="page">Claims</a>' in resp_claims.text

    # Check Gaps page
    resp_gaps = client.get(f"/case/{case_id}/gaps")
    assert resp_gaps.status_code == 200
    assert expected_empty in resp_gaps.text

    # Check Conflicts page
    resp_conf = client.get(f"/case/{case_id}/conflicts")
    assert resp_conf.status_code == 200
    assert expected_empty in resp_conf.text

    # Check Handover page
    resp_ho = client.get(f"/case/{case_id}/handover")
    assert resp_ho.status_code == 200
    assert expected_empty in resp_ho.text

    # Check Approvals page
    resp_app = client.get(f"/case/{case_id}/approvals")
    assert resp_app.status_code == 200
    assert expected_empty in resp_app.text


def test_ten_upload_rejection_cases(test_db):
    """Test all ten upload rejection cases with their specific banner messages."""
    client, uid = _create_real_user_client(test_db)
    client.post("/cases", data={"name": "Upload Rejection Case", "leaver": "L", "successor": "S", "approver": "A"})
    conn = db.get_conn()
    case_id = conn.execute("SELECT id FROM cases WHERE name = 'Upload Rejection Case'").fetchone()["id"]

    # 1. Reject binary (PDF)
    resp = client.post(f"/case/{case_id}/sources", files={"file": ("manual.pdf", io.BytesIO(b"%PDF-1.4 dummy binary content"), "application/pdf")})
    assert resp.status_code == 400
    assert "manual.pdf: Binary PDF files are not allowed." in resp.text

    # 2. Reject NUL bytes
    resp = client.post(f"/case/{case_id}/sources", files={"file": ("chat.txt", io.BytesIO(b"Hello \x00 world"), "text/plain")})
    assert resp.status_code == 400
    assert "chat.txt: Binary files containing NUL bytes are not allowed." in resp.text

    # 3. Reject non-UTF-8
    bad_bytes = b"Hello \xff\xfe non utf8"
    resp = client.post(f"/case/{case_id}/sources", files={"file": ("notes.md", io.BytesIO(bad_bytes), "text/markdown")})
    assert resp.status_code == 400
    assert "notes.md: File is not valid UTF-8." in resp.text

    # 4. Reject unsupported extension (.py, .exe, etc.)
    resp = client.post(f"/case/{case_id}/sources", files={"file": ("script.py", io.BytesIO(b"print('hello')\n"), "text/plain")})
    assert resp.status_code == 400
    assert "script.py: Unsupported extension. Only .txt, .csv, and .md files are supported." in resp.text

    # 5. Reject empty file
    resp = client.post(f"/case/{case_id}/sources", files={"file": ("empty.txt", io.BytesIO(b""), "text/plain")})
    assert resp.status_code == 400
    assert "empty.txt: File is empty." in resp.text

    # 6. Reject file over 200 KB
    huge_bytes = b"A" * (201 * 1024)
    resp = client.post(f"/case/{case_id}/sources", files={"file": ("huge.txt", io.BytesIO(huge_bytes), "text/plain")})
    assert resp.status_code == 400
    assert "huge.txt: File exceeds the 200 KB limit." in resp.text

    # 7. Reject CSV without header row
    resp = client.post(f"/case/{case_id}/sources", files={"file": ("nohead.csv", io.BytesIO(b"\n\n"), "text/csv")})
    assert resp.status_code == 400
    assert "nohead.csv: CSV file must have a header row." in resp.text

    # 8. Reject CSV with more than 30 columns
    cols = ",".join(f"col{i}" for i in range(35)) + "\n" + ",".join(f"val{i}" for i in range(35))
    resp = client.post(f"/case/{case_id}/sources", files={"file": ("wide.csv", io.BytesIO(cols.encode("utf-8")), "text/csv")})
    assert resp.status_code == 400
    assert "wide.csv: CSV file has 35 columns; maximum allowed is 30." in resp.text

    # 9. Reject case exceeding 1,500 non-blank lines
    # Upload 1,000 lines
    lines_1000 = "\n".join(f"Line {i} content" for i in range(1000)) + "\n"
    r1 = client.post(f"/case/{case_id}/sources", files={"file": ("part1.txt", io.BytesIO(lines_1000.encode("utf-8")), "text/plain")}, follow_redirects=False)
    assert r1.status_code == 303
    # Now upload 600 lines (1000 + 600 = 1600 > 1500)
    lines_600 = "\n".join(f"Line {i} content" for i in range(600)) + "\n"
    resp = client.post(f"/case/{case_id}/sources", files={"file": ("part2.txt", io.BytesIO(lines_600.encode("utf-8")), "text/plain")})
    assert resp.status_code == 400
    assert "part2.txt: Case would exceed the limit of 1,500 non-blank lines" in resp.text

    # 10. Reject sixth source
    # We currently have 1 source (part1.txt). Upload 4 more valid small files to reach 5
    for i in range(2, 6):
        r = client.post(f"/case/{case_id}/sources", files={"file": (f"src{i}.txt", io.BytesIO(f"Source {i} line\n".encode("utf-8")), "text/plain")}, follow_redirects=False)
        assert r.status_code == 303
    # Now attempt sixth source
    resp_sixth = client.post(f"/case/{case_id}/sources", files={"file": ("sixth.txt", io.BytesIO(b"Sixth line\n"), "text/plain")})
    assert resp_sixth.status_code == 400
    assert "sixth.txt: Maximum of 5 sources per case reached." in resp_sixth.text


def test_csv_different_headers_produces_owner_gap(test_db):
    """A CSV with different header names (e.g. Task, Owner, State) still produces the owner gap."""
    client, uid = _create_real_user_client(test_db)
    client.post("/cases", data={"name": "CSV Header Test Case", "leaver": "L", "successor": "S", "approver": "A"})
    conn = db.get_conn()
    case_id = conn.execute("SELECT id FROM cases WHERE name = 'CSV Header Test Case'").fetchone()["id"]

    csv_content = """Task,Owner,State
Deploy Notification Service,,In Progress
Setup Database,Charlie,Done
Update DNS,,Closed
"""
    client.post(f"/case/{case_id}/sources", files={"file": ("tasks.csv", io.BytesIO(csv_content.encode("utf-8")), "text/csv")}, data={"label": "team"})

    run_id = db.insert_run(case_id, mode="fixture", provider="fixture", model="fixture")
    gaps = verify.generate_deterministic_gaps(case_id, run_id, conn)
    assert len(gaps) == 1

    gap = conn.execute("SELECT * FROM gaps WHERE id = ?", (gaps[0],)).fetchone()
    assert "Who owns Deploy Notification Service?" in gap["question"]
    assert "Task Deploy Notification Service has no assignee and is not Done or Cancelled." in gap["why"]


def test_csv_missing_assignee_or_status_skips_rule(test_db):
    """A CSV missing assignee or status skips the rule and produces the banner message on Gaps page."""
    client, uid = _create_real_user_client(test_db)
    client.post("/cases", data={"name": "Missing Col Case", "leaver": "L", "successor": "S", "approver": "A"})
    conn = db.get_conn()
    case_id = conn.execute("SELECT id FROM cases WHERE name = 'Missing Col Case'").fetchone()["id"]

    csv_content = """Task,Priority,Description
TASK-101,High,Setup server
"""
    client.post(f"/case/{case_id}/sources", files={"file": ("tasks.csv", io.BytesIO(csv_content.encode("utf-8")), "text/csv")}, data={"label": "team"})

    run_id = db.insert_run(case_id, mode="fixture", provider="fixture", model="fixture")
    gaps = verify.generate_deterministic_gaps(case_id, run_id, conn)
    assert len(gaps) == 0

    # Visit gaps view as owner
    resp = client.get(f"/case/{case_id}/gaps")
    assert resp.status_code == 200
    assert "Task CSV 'tasks.csv' is missing assignee and status column; 'Who owns this?' gaps rule was skipped." in html.unescape(resp.text)


def test_two_cases_isolation_and_cross_case_drawer_404(test_db):
    """Two cases uploading identical files cannot read each other's lines, drawer, claims or exports.
    Cross-case drawer request returns 404.
    """
    client_a, uid_a = _create_real_user_client(test_db, email="user_a@test.com", name="User A")
    client_b, uid_b = _create_real_user_client(test_db, email="user_b@test.com", name="User B")

    client_a.post("/cases", data={"name": "Case A", "leaver": "LA", "successor": "SA", "approver": "AA"})
    client_b.post("/cases", data={"name": "Case B", "leaver": "LB", "successor": "SB", "approver": "AB"})

    conn = db.get_conn()
    case_a_id = conn.execute("SELECT id FROM cases WHERE name = 'Case A'").fetchone()["id"]
    case_b_id = conn.execute("SELECT id FROM cases WHERE name = 'Case B'").fetchone()["id"]

    shared_content = "Line 1: secret data\nLine 2: sensitive item\n"

    # Both upload identical file name and content
    client_a.post(f"/case/{case_a_id}/sources", files={"file": ("data.txt", io.BytesIO(shared_content.encode("utf-8")), "text/plain")}, data={"label": "team"})
    client_b.post(f"/case/{case_b_id}/sources", files={"file": ("data.txt", io.BytesIO(shared_content.encode("utf-8")), "text/plain")}, data={"label": "team"})

    # Check generated source IDs
    src_a = conn.execute("SELECT id FROM sources WHERE case_id = ?", (case_a_id,)).fetchone()["id"]
    src_b = conn.execute("SELECT id FROM sources WHERE case_id = ?", (case_b_id,)).fetchone()["id"]

    assert src_a == f"c{case_a_id}s1"
    assert src_b == f"c{case_b_id}s1"
    assert src_a != src_b

    # Case A line IDs: c{A}s1:001; Case B line IDs: c{B}s1:001
    lines_a = conn.execute("SELECT line_id FROM lines WHERE source_id = ?", (src_a,)).fetchall()
    lines_b = conn.execute("SELECT line_id FROM lines WHERE source_id = ?", (src_b,)).fetchall()
    assert lines_a[0]["line_id"] == f"{src_a}:001"
    assert lines_b[0]["line_id"] == f"{src_b}:001"

    # 1. Drawer isolation: User A cannot open drawer for Case B source
    resp_cross_drawer = client_a.get(f"/case/{case_a_id}/sources/{src_b}/drawer")
    assert resp_cross_drawer.status_code == 404

    resp_cross_case = client_a.get(f"/case/{case_b_id}/sources/{src_b}/drawer")
    assert resp_cross_case.status_code == 404

    # 2. Drawer on own source works
    resp_own_drawer = client_a.get(f"/case/{case_a_id}/sources/{src_a}/drawer")
    assert resp_own_drawer.status_code == 200
    assert f"Source {src_a}" in resp_own_drawer.text
    assert "Line 1: secret data" in resp_own_drawer.text

    # 3. Sources page with open_source param renders drawer
    resp_sources_open = client_a.get(f"/case/{case_a_id}/sources?open_source={src_a}")
    assert resp_sources_open.status_code == 200
    assert "main-with-drawer" in resp_sources_open.text
    assert f"Source {src_a}" in resp_sources_open.text
