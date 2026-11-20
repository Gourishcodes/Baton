"""Rate limiting, concurrency, and quota policies for Baton live extraction.

Follows ROUND3_SPEC section 4.4:
- RUNS_PER_USER_PER_DAY (default: 3)
- 1 concurrent run per case
- 4.5 seconds minimum between provider calls (15/min)
- LIVE_DAILY_CALL_CAP (default from env, e.g. 100)
- LIVE_ENABLED=0 as a kill switch
- Fixed list of plain failure messages
"""
import os
import threading
import time
from datetime import datetime, timezone
from . import db

_CONCURRENT_CASES: set[int] = set()
_CONCURRENCY_LOCK = threading.Lock()

_PROVIDER_CALL_LOCK = threading.Lock()
_LAST_PROVIDER_CALL_TIME: float = 0.0

# Fixed plain failure messages (ROUND3_SPEC 4.4)
MSG_KILL_SWITCH = "Live extraction is currently disabled by the kill switch. The demo case still works."
MSG_CONCURRENCY = "A run is already in progress for this case. The demo case still works."
MSG_USER_CAP = "You have reached the daily limit of runs for your account. The demo case still works."
MSG_DAILY_CAP = "The host daily call cap has been reached. The demo case still works."
MSG_NO_KEY = "No API key is configured on the host. The demo case still works."
MSG_TIMEOUT = "The model did not answer in time. Your files are saved. Try again in a minute."
MSG_BAD_OUTPUT = "The model returned invalid output after retry. Your files are saved. Try again."
MSG_PROVIDER_REJECTED = "The model provider rejected the request or quota was exceeded. The demo case still works."
MSG_GENERIC_FAILURE = "Extraction failed. Your files are saved. Try again in a minute. The demo case still works."


def acquire_case_run(case_id: int, user_id: int, conn=None) -> tuple[bool, str | None]:
    """Check limits and acquire execution rights for this case and user.

    Returns (True, None) if permitted, or (False, plain_message) if blocked.
    """
    if conn is None:
        conn = db.get_conn()

    # 1. Kill switch
    if os.environ.get("LIVE_ENABLED", "1") == "0":
        return False, MSG_KILL_SWITCH

    # 2. Concurrency cap (one run per case)
    with _CONCURRENCY_LOCK:
        if case_id in _CONCURRENT_CASES:
            return False, MSG_CONCURRENCY
        _CONCURRENT_CASES.add(case_id)

    # 3. Runs per user per day (default: 3)
    user_limit = int(os.environ.get("RUNS_PER_USER_PER_DAY", "3"))
    today_prefix = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    user_runs = conn.execute(
        "SELECT COUNT(*) as c FROM run_events WHERE user_id = ? AND at >= ?",
        (user_id, today_prefix)
    ).fetchone()["c"]

    if user_runs >= user_limit:
        release_case_run(case_id)
        return False, MSG_USER_CAP

    # 4. Host daily call cap
    daily_cap_str = os.environ.get("LIVE_DAILY_CALL_CAP")
    if daily_cap_str:
        daily_cap = int(daily_cap_str)
        calls_today = conn.execute(
            "SELECT COUNT(*) as c FROM provider_calls WHERE at >= ?",
            (today_prefix,)
        ).fetchone()["c"]
        if calls_today >= daily_cap:
            release_case_run(case_id)
            return False, MSG_DAILY_CAP

    # Record run event
    now_iso = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO run_events (user_id, case_id, at) VALUES (?, ?, ?)",
        (user_id, case_id, now_iso)
    )
    conn.commit()

    return True, None


def release_case_run(case_id: int):
    """Release concurrency lock for a case."""
    with _CONCURRENCY_LOCK:
        _CONCURRENT_CASES.discard(case_id)


def throttle_provider_call():
    """Enforce minimum 4.5 seconds between provider calls (15 per minute)."""
    global _LAST_PROVIDER_CALL_TIME
    min_interval = float(os.environ.get("PROVIDER_MIN_INTERVAL", "4.5"))
    with _PROVIDER_CALL_LOCK:
        now = time.time()
        elapsed = now - _LAST_PROVIDER_CALL_TIME
        if elapsed < min_interval:
            time.sleep(min_interval - elapsed)
        _LAST_PROVIDER_CALL_TIME = time.time()


def record_provider_call(conn=None):
    """Record a provider call against host limits."""
    if conn is None:
        conn = db.get_conn()
    now_iso = datetime.now(timezone.utc).isoformat()
    conn.execute("INSERT INTO provider_calls (at) VALUES (?)", (now_iso,))
    conn.commit()


def check_daily_call_cap(conn=None) -> tuple[bool, str | None]:
    """Check if host daily call cap has been reached."""
    if conn is None:
        conn = db.get_conn()
    if os.environ.get("LIVE_ENABLED", "1") == "0":
        return False, MSG_KILL_SWITCH

    daily_cap_str = os.environ.get("LIVE_DAILY_CALL_CAP")
    if daily_cap_str:
        daily_cap = int(daily_cap_str)
        today_prefix = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        calls_today = conn.execute(
            "SELECT COUNT(*) as c FROM provider_calls WHERE at >= ?",
            (today_prefix,)
        ).fetchone()["c"]
        if calls_today >= daily_cap:
            return False, MSG_DAILY_CAP

    return True, None


def map_exception_to_message(exc: Exception) -> str:
    """Map any exception to a plain user message from the fixed list.
    Never exposes stack traces, exception details, or prompt text.
    """
    raw_str = str(exc)
    for known in (
        MSG_KILL_SWITCH,
        MSG_CONCURRENCY,
        MSG_USER_CAP,
        MSG_DAILY_CAP,
        MSG_NO_KEY,
        MSG_TIMEOUT,
        MSG_BAD_OUTPUT,
        MSG_PROVIDER_REJECTED,
        MSG_GENERIC_FAILURE,
    ):
        if known in raw_str:
            return known

    msg = raw_str.lower()

    if "kill switch" in msg or "live_enabled" in msg:
        return MSG_KILL_SWITCH
    if "concurrent" in msg or "already in progress" in msg:
        return MSG_CONCURRENCY
    if "daily call cap" in msg or "daily_call_cap" in msg or "daily limit of calls" in msg:
        return MSG_DAILY_CAP
    if "runs per user" in msg or "user_cap" in msg or "daily limit of runs" in msg:
        return MSG_USER_CAP
    if "timeout" in msg or "timed out" in msg:
        return MSG_TIMEOUT
    if (
        "invalid json" in msg
        or "validation error" in msg
        or "pydantic" in msg
        or "jsondecodeerror" in msg
        or "bad output" in msg
    ):
        return MSG_BAD_OUTPUT
    if (
        "quota" in msg
        or "429" in msg
        or "403" in msg
        or "401" in msg
        or "permission denied" in msg
        or "rejected" in msg
        or "unauthorized" in msg
        or "api key not valid" in msg
        or "invalid api key" in msg
    ):
        return MSG_PROVIDER_REJECTED
    if "gemini_api_key is not configured" in msg or "no api key" in msg or "not configured" in msg:
        return MSG_NO_KEY

    return MSG_GENERIC_FAILURE
