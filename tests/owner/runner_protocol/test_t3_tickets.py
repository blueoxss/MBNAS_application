"""Job tickets: forgery, tampering, other key, expiry and lease binding (C05 D2; DD-A18, WP-2 contract section 1.3,
``mbnas_core.runner_api.tickets``). SYNTHETIC.

* Unit (ported from the WP-2A scratch self-test ``test_ticket_issue_verify_unit``,
  ``docs/internal/evidence/WP2/2A/selftest_test_wp2a.py.txt``, and extended): an authentic ticket verifies only for
  its own (job, attempt, runner) and only before ``exp``; a forged or truncated MAC, another key, a tampered payload,
  another version or garbage does not decode; every refusal is the same 403 ``TICKET_INVALID`` (no oracle on which
  field differed).
* API (ported from the C05 evaluator probe ``test_c05_ticket_probe.py``): on the real core app, every bad ticket x
  {start, heartbeat} is 403 ``TICKET_INVALID``, first on the LEASED job and again on the RUNNING job after the genuine
  start (a bad ticket never renews a lease). The ticket is never echoed, no table changes (``principals.last_used_at``
  aside: the bearer tokens themselves are valid) and the genuine ticket keeps working.
"""
from __future__ import annotations

import base64
import time

import harness
import pytest
import runner_sim as rs

from mbnas_contracts.errors import ApiError
from mbnas_core.runner_api import tickets

pytestmark = pytest.mark.runner_protocol

KEY = b"t3-unit-ticket-key-0123456789abcdef0123"
OTHER_KEY = b"t3-unit-other-key-0123456789abcdef0123"
NOW = 1_800_000_000  # fixed clock of the unit tests (unix seconds)
FENCE = {"job_id": "job-1", "attempt": 2, "runner_id": "R1"}


def _flip_last(text: str) -> str:
    return text[:-1] + ("0" if text[-1] != "0" else "1")


def _with_payload(ticket: str, payload: str) -> str:
    """``ticket`` with its payload replaced (the original MAC kept)."""
    version, _, mac = ticket.split(".")
    body = base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")
    return f"{version}.{body}.{mac}"


def _refusal(key: bytes, ticket: str | None, *, now: float = NOW, **fence: object) -> tuple[int, str, str]:
    with pytest.raises(ApiError) as exc:
        tickets.verify(key, ticket, now=now, **{**FENCE, **fence})  # type: ignore[arg-type]
    return exc.value.status, exc.value.code, exc.value.message


# -- unit ------------------------------------------------------------------------------------------------------------
def test_unit_authentic_ticket_verifies_only_for_its_own_lease_and_before_exp():
    t = tickets.issue(KEY, "job-1", 2, "L0", "R1", NOW + 60)
    fields = {"job_id": "job-1", "attempt": 2, "stage": "L0", "runner_id": "R1", "exp": NOW + 60}
    assert t.startswith("v1.") and t.count(".") == 2
    assert tickets.verify(KEY, t, now=NOW, **FENCE) == fields
    assert tickets.verify(KEY, t, now=NOW + 59, **FENCE) == fields  # last valid second
    assert tickets.decode(KEY, t) == fields
    refusals = {
        "other job": _refusal(KEY, t, job_id="job-2"),
        "other attempt (older)": _refusal(KEY, t, attempt=1),
        "other attempt (newer)": _refusal(KEY, t, attempt=3),
        "other runner": _refusal(KEY, t, runner_id="R2"),
        "expired (exp == now)": _refusal(KEY, t, now=NOW + 60),
        "expired (exp < now)": _refusal(KEY, t, now=NOW + 3600),
        "other key": _refusal(OTHER_KEY, t),
    }
    assert set(refusals.values()) == {(403, "TICKET_INVALID", refusals["other job"][2])}, refusals
    # decode checks the MAC only: an expired authentic ticket still decodes (verify is the expiry gate)
    assert tickets.decode(KEY, tickets.issue(KEY, "job-1", 2, "-", "R1", NOW - 1))["exp"] == NOW - 1


def test_unit_forged_tampered_and_garbage_tickets_never_decode():
    t = tickets.issue(KEY, "job-1", 2, "L0", "R1", NOW + 60)
    bad = {
        "mac flipped": _flip_last(t),
        "mac truncated": t[:-2],
        "mac of another key": tickets.issue(OTHER_KEY, "job-1", 2, "L0", "R1", NOW + 60),
        "payload: attempt raised, MAC kept": _with_payload(t, f"job-1|3|L0|R1|{NOW + 60}"),
        "payload: exp extended, MAC kept": _with_payload(t, f"job-1|2|L0|R1|{NOW + 10**6}"),
        "payload: other runner, MAC kept": _with_payload(t, f"job-1|2|L0|R2|{NOW + 60}"),
        "version swapped": "v2" + t[2:],
        "extra segment": t + ".00",
        "garbage v1.x.y": "v1.x.y",
        "no separators": "garbage-ticket",
        "empty": "",
    }
    for name, ticket in bad.items():
        assert tickets.decode(KEY, ticket) is None, name
    refusals = {name: _refusal(KEY, ticket) for name, ticket in bad.items()}
    refusals["missing (None)"] = _refusal(KEY, None)
    assert set(refusals.values()) == {_refusal(KEY, t, job_id="job-2")}, refusals  # one answer for every failure


def test_unit_issue_refuses_the_field_separator():
    for kw in ({"job_id": "job|1"}, {"stage": "L|0"}, {"runner_id": "R|1"}):
        args = {"job_id": "job-1", "attempt": 1, "stage": "L0", "runner_id": "R1", "exp": NOW, **kw}
        with pytest.raises(ValueError, match="must not contain"):
            tickets.issue(KEY, args["job_id"], args["attempt"], args["stage"], args["runner_id"], args["exp"])


# -- API -------------------------------------------------------------------------------------------------------------
def _db_state(db) -> dict[str, list[str]]:
    """Every table of the core DB (rows as sorted reprs); ``principals`` without ``last_used_at`` (stamped by every
    authenticated call: the bearer tokens of these requests are valid, only their tickets are not)."""
    names = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE "
                                      "'sqlite_%' ORDER BY name")]
    state = {}
    for name in names:
        sql = (f"SELECT * FROM {name}" if name != "principals" else
               "SELECT id, kind, runner_id, token_prefix, token_hash, scopes_json, created_at, expires_at, revoked_at, "
               "rotated_from FROM principals")
        state[name] = sorted(repr(tuple(row)) for row in db.execute(sql))
    assert "jobs" in state and "runners" in state and "workers" in state
    return state


def _bad_tickets(lease: rs.Lease) -> dict[str, str]:
    key = rs.TICKET_KEY.encode()
    f = tickets.decode(key, lease.ticket)
    assert f is not None and f["job_id"] == lease.job_id and f["attempt"] == lease.attempt and f["runner_id"] == "R1"
    assert int(f["exp"]) > time.time()  # type: ignore[arg-type]
    job, stage = str(f["job_id"]), str(f["stage"])
    attempt, exp = int(f["attempt"]), int(f["exp"])  # type: ignore[arg-type]
    return {
        "forged (garbage)": "v1.x.y",
        "forged (not a ticket)": "garbage-ticket",
        "tampered MAC": _flip_last(lease.ticket),
        "tampered payload (attempt+1, MAC kept)": _with_payload(lease.ticket, f"{job}|{attempt + 1}|{stage}|R1|{exp}"),
        "version swapped": "v2" + lease.ticket[2:],
        "other key": tickets.issue(b"t3-other-ticket-key-0123456789abcdef01", job, attempt, stage, "R1", exp),
        "expired": tickets.issue(key, job, attempt, stage, "R1", int(time.time()) - 1),
        "other job": tickets.issue(key, "job-other", attempt, stage, "R1", exp),
        "other runner": tickets.issue(key, job, attempt, stage, "R2", exp),
        "other attempt": tickets.issue(key, job, attempt + 1, stage, "R1", exp),
    }


def _refused_everywhere(runner_sim, runner_sim2, lease: rs.Lease, phase: str) -> set[str]:
    """Every bad ticket on start and heartbeat, plus the genuine ticket replayed by another runner (its own valid
    token) and on another job's routes; returns the distinct refusal messages."""
    calls = []
    for name, ticket in _bad_tickets(lease).items():
        bad = rs.Lease({**lease.claim, "ticket": ticket}, "R1")
        calls += [(name, "start", ticket, runner_sim.start_raw(bad)),
                  (name, "heartbeat", ticket, runner_sim.heartbeat(bad))]
    stolen = rs.Lease(dict(lease.claim), "R2")  # R2 presents R1's genuine ticket with R2's own valid token
    calls += [("stolen by R2", "start", lease.ticket, runner_sim2.start_raw(stolen)),
              ("stolen by R2", "heartbeat", lease.ticket, runner_sim2.heartbeat(stolen))]
    other = "job-does-not-exist"  # the genuine ticket on another job's routes: same answer, no oracle on job existence
    calls += [("other job route", "start", lease.ticket,
               runner_sim.post(f"/jobs/{other}/start", lease.fence(device_uuid="GPU-sim-R1"))),
              ("other job route", "heartbeat", lease.ticket,
               runner_sim.post(f"/jobs/{other}/heartbeat", lease.fence(progress={}, device_sample={})))]
    messages = set()
    for name, route, ticket, r in calls:
        assert r.status_code == 403 and r.json()["code"] == "TICKET_INVALID", (phase, name, route, r.text)
        assert ticket not in r.text, (phase, name, route)  # the presented ticket is never echoed back
        messages.add(r.json()["message"])
    return messages


def test_bad_tickets_are_403_on_start_and_heartbeat_without_any_write(core, runner_sim, runner_sim2, base_proto, db):
    harness.validate_and_run(core, base_proto())
    runner_sim.register()
    runner_sim2.register()
    lease = runner_sim.claim()
    assert lease is not None
    # phase 1: the job is LEASED (claimed, not started)
    before = _db_state(db)
    assert len(_refused_everywhere(runner_sim, runner_sim2, lease, "LEASED")) == 1  # one message for every failure
    empty = rs.Lease({**lease.claim, "ticket": ""}, "R1")
    for r in (runner_sim.start_raw(empty), runner_sim.heartbeat(empty)):  # schema min_length: refused before the check
        assert r.status_code == 422 and r.json()["code"] == "VALIDATION_FAILED", r.text
    assert _db_state(db) == before
    # the genuine ticket still works afterwards
    runner_sim.start(lease)
    assert runner_sim.heartbeat(lease).status_code == 200
    # phase 2: the job is RUNNING - a bad ticket neither renews the lease nor restarts the attempt
    running = _db_state(db)
    assert len(_refused_everywhere(runner_sim, runner_sim2, lease, "RUNNING")) == 1
    assert _db_state(db) == running
    hb = runner_sim.heartbeat(lease)
    assert hb.status_code == 200, hb.text
