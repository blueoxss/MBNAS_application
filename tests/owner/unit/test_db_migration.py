"""db.core.migrate: v1 databases gain the v2 columns additively; v1/v2 databases get the v3 events.type CHECK
('generation.updated', contract 1.2.9) by a row-preserving rebuild of ``events``."""
from __future__ import annotations

import sqlite3

import pytest

from mbnas_core.db import repo
from mbnas_core.db.core import SCHEMA_VERSION, connect, migrate, schema_sql, tx

V2_RUN_COLUMNS = """,
  kind TEXT NOT NULL DEFAULT 'SEARCH' CHECK (kind IN ('SEARCH', 'FINAL_RETRAIN')),
  parent_run_id TEXT REFERENCES runs(id),
  finalists_json TEXT NOT NULL DEFAULT '[]'
);"""
V2_CHECKPOINT_COLUMNS = """,
  state TEXT NOT NULL DEFAULT 'PRESENT' CHECK (state IN ('PRESENT', 'PRUNED')),
  pruned_at TEXT,
  prune_json TEXT
);"""
V3_EVENT_TYPES = """CHECK (type IN ('candidate.updated','candidate.evaluated','generation.updated',
                                     'generation.completed','run.updated','worker.updated','log',
                                     'snapshot.required'))"""
V2_EVENT_TYPES = """CHECK (type IN ('candidate.updated','candidate.evaluated','generation.completed','run.updated',
                                     'worker.updated','log','snapshot.required'))"""
GEN_ROW = {"arm": "GA", "search_seed": 1, "generation": 0, "rung": "L0", "comparison_group": "g", "planning_closed": False,
           "planned_count": 2, "evaluated_count": 1, "failed_count": 0, "not_admitted_count": 0, "pending_count": 1,
           "complete": False, "representative_candidate_id": None}


def _v2_schema() -> str:
    text = schema_sql()
    assert V3_EVENT_TYPES in text
    return text.replace(V3_EVENT_TYPES, V2_EVENT_TYPES)


def _v1_schema() -> str:
    text = _v2_schema()
    assert V2_RUN_COLUMNS in text and V2_CHECKPOINT_COLUMNS in text
    return text.replace(V2_RUN_COLUMNS, "\n);").replace(V2_CHECKPOINT_COLUMNS, "\n);")


def _old_run(db: sqlite3.Connection) -> None:
    db.execute("INSERT INTO runs (id, namespace, status, protocol_hash, resolved_protocol_json, requested_protocol_json, "
               "execution_mode, idempotency_key, payload_sha256, created_at, updated_at, last_seq) VALUES "
               "('run-old', 'n', 'COMPLETED', 'h', '{}', '{}', 'PILOT', 'k', 'p', 't', 't', 2)")
    db.execute("INSERT INTO events (run_id, seq, type, payload_json, created_at) VALUES "
               "('run-old', 1, 'log', '{\"message\": \"hello\"}', 't1'), "
               "('run-old', 2, 'generation.completed', '{\"arm\": \"GA\"}', 't2')")


def test_v1_database_is_migrated_additively(tmp_path) -> None:
    path = str(tmp_path / "v1.sqlite3")
    old = sqlite3.connect(path)
    old.executescript(_v1_schema())
    _old_run(old)
    old.execute("INSERT INTO checkpoints (id, run_id, candidate_id, epoch, kind, path, sha256, bytes, created_at, "
                "updated_at) VALUES ('ck1', 'run-old', 'c', 1, 'training_full', '/x', 's', 1, 't', 't')")
    old.commit()
    assert "kind" not in {r[1] for r in old.execute("PRAGMA table_info(runs)")}
    old.close()
    conn = connect(db_path=path)
    try:
        run = repo.get_run(conn, "run-old")
        assert run["kind"] == "SEARCH" and run["parent_run_id"] is None and run["finalists"] == []
        ck = repo.list_checkpoints(conn, "run-old")[0]
        assert ck["state"] == "PRESENT" and ck["pruned_at"] is None and ck["prune"] is None
        assert conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == SCHEMA_VERSION == "6"
        # WP-4 v5 (additive): the revisions table and the public-id salt exist after the forward migration
        assert conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='config_revisions'").fetchone()
        assert conn.execute("SELECT value FROM meta WHERE key='public_id_salt'").fetchone()[0]
        # DD-A66 v6 (additive): the prune round-trip requests table
        assert conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='prune_requests'").fetchone()
        assert [e["seq"] for e in repo.get_events_after(conn, "run-old", 0)] == [1, 2]
        with tx(conn):
            repo.create_run(conn, id="run-child", namespace="n", protocol_hash="h", resolved_protocol={},
                            requested_protocol={}, execution_mode="PILOT", idempotency_key="k2", payload_sha256="p",
                            kind="FINAL_RETRAIN", parent_run_id="run-old",
                            finalists=[{"candidate_id": "c", "selection_rule": "r"}])
            pruned = repo.mark_checkpoint_pruned(conn, "ck1", prune={"policy": "keep_all"})
        assert pruned["state"] == "PRUNED" and pruned["prune"] == {"policy": "keep_all"} and pruned["pruned_at"]
        assert [r["id"] for r in repo.list_child_runs(conn, "run-old")] == ["run-child"]
        assert repo.get_run(conn, "run-child")["finalists"] == [{"candidate_id": "c", "selection_rule": "r"}]
    finally:
        conn.close()
    again = connect(db_path=path)  # idempotent on an already migrated DB
    again.close()


def test_v2_database_events_check_is_widened_without_losing_rows(tmp_path) -> None:
    """S9-F3: a v2 DB (events.type CHECK without generation.updated) is rebuilt forward; old events survive."""
    path = str(tmp_path / "v2.sqlite3")
    old = sqlite3.connect(path)
    old.executescript(_v2_schema())
    old.execute("UPDATE meta SET value = '2' WHERE key = 'schema_version'")
    _old_run(old)
    old.commit()
    with pytest.raises(sqlite3.IntegrityError):  # the old CHECK really rejects the new type
        old.execute("INSERT INTO events (run_id, seq, type, payload_json, created_at) "
                    "VALUES ('run-old', 3, 'generation.updated', '{}', 't')")
    old.rollback()
    before = old.execute("SELECT run_id, seq, type, payload_json, created_at FROM events ORDER BY seq").fetchall()
    old.close()
    conn = connect(db_path=path)
    try:
        after = [tuple(r) for r in conn.execute(
            "SELECT run_id, seq, type, payload_json, created_at FROM events ORDER BY seq")]
        assert after == before and len(after) == 2
        assert conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == SCHEMA_VERSION == "6"
        # WP-4 v5 (additive): the revisions table and the public-id salt exist after the forward migration
        assert conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='config_revisions'").fetchone()
        assert conn.execute("SELECT value FROM meta WHERE key='public_id_salt'").fetchone()[0]
        ddl = conn.execute("SELECT sql FROM sqlite_master WHERE name = 'events'").fetchone()[0]
        assert "'generation.updated'" in ddl
        assert conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE name LIKE 'events__%'").fetchone()[0] == 0
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        with tx(conn):
            seq = repo.append_event(conn, "run-old", "generation.updated", GEN_ROW)
        assert seq == 3
        assert [e["type"] for e in repo.get_events_after(conn, "run-old", 0)] == [
            "log", "generation.completed", "generation.updated"]
        with pytest.raises(sqlite3.IntegrityError):  # the CHECK is still enforced for unknown types
            with tx(conn):
                conn.execute("INSERT INTO events (run_id, seq, type, payload_json, created_at) "
                             "VALUES ('run-old', 9, 'bogus', '{}', 't')")
        assert migrate(conn) == []  # idempotent: an up-to-date DB is not rebuilt again
    finally:
        conn.close()
