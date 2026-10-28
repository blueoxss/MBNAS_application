-- MBNAS server schema v6 (SQLite, WAL). Applied idempotently by db.core.init_schema(); older databases are migrated
-- forward by db.core.migrate() (v2 adds runs.kind / parent_run_id / finalists and checkpoints.state / pruned_at /
-- prune columns; v3 widens the events.type CHECK with 'generation.updated' by rebuilding events with every row
-- copied; v4/v5/v6 only add tables: runner protocol, configuration revisions, prune requests; nothing is dropped).
-- Timestamps: fixed-width UTC ISO-8601 strings (util.time.utcnow_iso), so lexical order == time order.
-- *_json columns hold canonical-ish JSON text; db.repo decodes them to the key without the _json suffix.

CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS validations (
  id TEXT PRIMARY KEY,
  protocol_hash TEXT NOT NULL,
  resolved_json TEXT NOT NULL,
  requested_sha256 TEXT NOT NULL,
  capabilities_json TEXT NOT NULL DEFAULT '{}',
  dataset_manifest_hashes_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS validations_hash ON validations(protocol_hash);

CREATE TABLE IF NOT EXISTS runs (
  id TEXT PRIMARY KEY,
  namespace TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('QUEUED','RUNNING','PAUSE_REQUESTED','PAUSED','RESUMING',
                                         'CANCEL_REQUESTED','COMPLETED','FAILED','CANCELLED')),
  protocol_hash TEXT NOT NULL,
  resolved_protocol_json TEXT NOT NULL,
  requested_protocol_json TEXT NOT NULL,
  execution_mode TEXT NOT NULL CHECK (execution_mode IN ('PILOT','REGISTERED_SEARCH','HISTORICAL_REPRODUCTION')),
  purpose TEXT,
  preset TEXT,
  idempotency_key TEXT NOT NULL UNIQUE,
  payload_sha256 TEXT NOT NULL,
  validation_id TEXT REFERENCES validations(id),
  reason_code TEXT,
  last_seq INTEGER NOT NULL DEFAULT 0 CHECK (last_seq >= 0),
  costs_json TEXT NOT NULL DEFAULT '{}',
  budget_json TEXT NOT NULL DEFAULT '{}',
  cancel_json TEXT,
  source_commit TEXT,
  terminal INTEGER NOT NULL DEFAULT 0 CHECK (terminal IN (0, 1)),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT 'SEARCH' CHECK (kind IN ('SEARCH', 'FINAL_RETRAIN')),
  parent_run_id TEXT REFERENCES runs(id),
  finalists_json TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS runs_namespace ON runs(namespace, created_at);

CREATE TABLE IF NOT EXISTS candidates (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES runs(id),
  name TEXT NOT NULL,
  arm TEXT NOT NULL CHECK (arm IN ('Baseline','GA','IndependentRandom','Final')),
  search_seed INTEGER,
  training_seed INTEGER,
  generation INTEGER NOT NULL DEFAULT 0 CHECK (generation >= 0),
  family TEXT NOT NULL,
  architecture_hash TEXT,
  architecture_yaml TEXT,
  architecture_path TEXT,
  parents_json TEXT NOT NULL DEFAULT '[]',
  parent_evaluation_ids_json TEXT NOT NULL DEFAULT '[]',
  lineage_json TEXT NOT NULL DEFAULT '{}',
  blocks_json TEXT NOT NULL DEFAULT '[]',
  candidate_status TEXT NOT NULL,
  current_rung TEXT,
  audit_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS candidates_run ON candidates(run_id, arm, search_seed, generation);

CREATE TABLE IF NOT EXISTS evaluations (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES runs(id),
  candidate_id TEXT NOT NULL REFERENCES candidates(id),
  rung TEXT NOT NULL,
  epochs INTEGER NOT NULL CHECK (epochs >= 0),
  status TEXT NOT NULL,
  revision INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
  supersedes_evaluation_id TEXT REFERENCES evaluations(id),
  row_json TEXT NOT NULL,
  immutable INTEGER NOT NULL DEFAULT 0 CHECK (immutable IN (0, 1)),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS evaluations_run ON evaluations(run_id, candidate_id, rung);
CREATE TRIGGER IF NOT EXISTS evaluations_no_update_immutable BEFORE UPDATE ON evaluations
  WHEN OLD.immutable = 1 BEGIN SELECT RAISE(ABORT, 'IMMUTABLE_EVALUATION'); END;
CREATE TRIGGER IF NOT EXISTS evaluations_no_delete_immutable BEFORE DELETE ON evaluations
  WHEN OLD.immutable = 1 BEGIN SELECT RAISE(ABORT, 'IMMUTABLE_EVALUATION'); END;

CREATE TABLE IF NOT EXISTS generations (
  run_id TEXT NOT NULL REFERENCES runs(id),
  arm TEXT NOT NULL CHECK (arm IN ('GA','IndependentRandom')),
  search_seed INTEGER NOT NULL,
  generation INTEGER NOT NULL CHECK (generation >= 0),
  rung TEXT NOT NULL,
  comparison_group TEXT NOT NULL,
  group_key TEXT NOT NULL,
  row_json TEXT NOT NULL,
  planning_closed INTEGER NOT NULL DEFAULT 0 CHECK (planning_closed IN (0, 1)),
  planned_count INTEGER NOT NULL DEFAULT 0 CHECK (planned_count >= 0),
  evaluated_count INTEGER NOT NULL DEFAULT 0 CHECK (evaluated_count >= 0),
  failed_count INTEGER NOT NULL DEFAULT 0 CHECK (failed_count >= 0),
  not_admitted_count INTEGER NOT NULL DEFAULT 0 CHECK (not_admitted_count >= 0),
  pending_count INTEGER NOT NULL DEFAULT 0 CHECK (pending_count >= 0),
  complete INTEGER NOT NULL DEFAULT 0 CHECK (complete IN (0, 1)),
  representative_candidate_id TEXT,
  representative_evaluation_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (run_id, group_key),
  CHECK (evaluated_count + failed_count + not_admitted_count + pending_count = planned_count),
  CHECK (complete = 0 OR (planning_closed = 1 AND pending_count = 0))
);

CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES runs(id),
  kind TEXT NOT NULL CHECK (kind IN ('BASELINE_TRAIN','CANDIDATE_TRAIN','EVALUATE','BENCHMARK','FINAL_RETRAIN',
                                     'PROPOSE_GENERATION')),
  candidate_id TEXT,
  rung TEXT,
  cumulative_target INTEGER,
  fixed_horizon INTEGER,
  status TEXT NOT NULL CHECK (status IN ('QUEUED','LEASED','RUNNING','SUCCEEDED','FAILED','CANCELLED')),
  priority INTEGER NOT NULL DEFAULT 100,
  attempt INTEGER NOT NULL DEFAULT 0 CHECK (attempt >= 0),
  max_attempts INTEGER NOT NULL DEFAULT 3 CHECK (max_attempts >= 1),
  lease_owner TEXT,
  lease_expires_at TEXT,
  heartbeat_at TEXT,
  spec_json TEXT NOT NULL,
  result_json TEXT,
  error_json TEXT,
  idempotency_key TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  started_at TEXT,
  finished_at TEXT
);
CREATE INDEX IF NOT EXISTS jobs_queue ON jobs(status, priority, created_at, id);
CREATE INDEX IF NOT EXISTS jobs_run ON jobs(run_id, status);

CREATE TABLE IF NOT EXISTS workers (
  id TEXT PRIMARY KEY,
  status TEXT NOT NULL,
  device TEXT NOT NULL,
  device_uuid TEXT,
  device_name TEXT,
  job_id TEXT,
  run_id TEXT,
  candidate_id TEXT,
  heartbeat_at TEXT,
  row_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
  run_id TEXT NOT NULL REFERENCES runs(id),
  seq INTEGER NOT NULL CHECK (seq >= 1),
  type TEXT NOT NULL CHECK (type IN ('candidate.updated','candidate.evaluated','generation.updated',
                                     'generation.completed','run.updated','worker.updated','log',
                                     'snapshot.required')),
  payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY (run_id, seq)
);

CREATE TABLE IF NOT EXISTS artifacts (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES runs(id),
  evaluation_id TEXT,
  candidate_id TEXT,
  kind TEXT NOT NULL CHECK (kind IN ('gt','tp','fn','fp','pr_curve','confusion_matrix','architecture','checkpoint',
                                     'onnx','tensorrt_engine','other')),
  relpath TEXT NOT NULL,
  sha256 TEXT,
  mime_type TEXT,
  bytes INTEGER CHECK (bytes IS NULL OR bytes >= 0),
  sample_id TEXT,
  split_hash TEXT,
  state TEXT NOT NULL CHECK (state IN ('READY','NOT_READY','EXPIRED','ACCESS_DENIED','MISSING','LOAD_FAILED')),
  meta_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS artifacts_run ON artifacts(run_id, evaluation_id);

CREATE TABLE IF NOT EXISTS cost_ledger (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL REFERENCES runs(id),
  arm TEXT NOT NULL CHECK (arm IN ('Baseline','GA','IndependentRandom','Final')),
  candidate_id TEXT,
  job_id TEXT,
  attempt INTEGER,
  kind TEXT NOT NULL CHECK (kind IN ('train','val','export','profile','failed','retry')),
  gpu_seconds REAL NOT NULL CHECK (gpu_seconds >= 0),
  physical INTEGER NOT NULL CHECK (physical IN (0, 1)),
  billed_arm TEXT CHECK (billed_arm IS NULL OR billed_arm IN ('GA','IndependentRandom')),
  device_uuid TEXT,
  started_at TEXT,
  finished_at TEXT,
  meta_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS cost_run ON cost_ledger(run_id, arm);

CREATE TABLE IF NOT EXISTS checkpoints (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES runs(id),
  candidate_id TEXT NOT NULL,
  rung TEXT,
  epoch INTEGER NOT NULL CHECK (epoch >= 0),
  kind TEXT NOT NULL CHECK (kind IN ('training_full','eval_ema','weights_only','best')),
  path TEXT NOT NULL,
  sha256 TEXT NOT NULL,
  bytes INTEGER NOT NULL CHECK (bytes >= 0),
  hashes_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  state TEXT NOT NULL DEFAULT 'PRESENT' CHECK (state IN ('PRESENT', 'PRUNED')),
  pruned_at TEXT,
  prune_json TEXT
);
CREATE INDEX IF NOT EXISTS checkpoints_candidate ON checkpoints(candidate_id, kind, epoch);

-- v4 (WP-2 remote runner protocol, design A3 section 12; additive, no public_ids table per DD-A19) ------------------
CREATE TABLE IF NOT EXISTS runners (
  id TEXT PRIMARY KEY,
  status TEXT NOT NULL CHECK (status IN ('REGISTERED','ONLINE','LOST','OFFLINE')),
  host TEXT, device TEXT, device_uuid TEXT, device_name TEXT,
  kinds_json TEXT NOT NULL DEFAULT '[]',
  runtime_json TEXT NOT NULL DEFAULT '{}',
  data_mounts_json TEXT NOT NULL DEFAULT '{}',
  registered_at TEXT NOT NULL, last_seen_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS principals (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL CHECK (kind IN ('server','runner','admin')),
  runner_id TEXT REFERENCES runners(id),
  token_prefix TEXT NOT NULL, token_hash TEXT NOT NULL UNIQUE,
  scopes_json TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL, expires_at TEXT, revoked_at TEXT, rotated_from TEXT, last_used_at TEXT,
  CHECK (kind != 'runner' OR runner_id IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS principals_prefix ON principals(token_prefix);

CREATE TABLE IF NOT EXISTS idempotent_responses (
  job_id TEXT NOT NULL REFERENCES jobs(id),
  attempt INTEGER NOT NULL CHECK (attempt >= 1),
  runner_id TEXT NOT NULL,
  payload_digest TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('PROCESSING','DONE')),
  status_code INTEGER, response_json TEXT, response_digest TEXT,
  processing_since TEXT NOT NULL, committed_at TEXT, acked_at TEXT,
  PRIMARY KEY (job_id, attempt)
);

CREATE TABLE IF NOT EXISTS artifact_locations (
  id TEXT PRIMARY KEY,
  object_kind TEXT NOT NULL CHECK (object_kind IN ('artifact','checkpoint')),
  object_id TEXT NOT NULL,
  run_id TEXT NOT NULL REFERENCES runs(id),
  runner_id TEXT,
  store TEXT NOT NULL CHECK (store IN ('runner_local','server_store','core_local')),
  relpath TEXT NOT NULL,
  uri TEXT,
  sha256 TEXT NOT NULL, bytes INTEGER NOT NULL CHECK (bytes >= 0),
  verified_by TEXT NOT NULL,
  verified_at TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('PRESENT','TRANSFERRING','PRUNED','MISSING','LOAD_FAILED')),
  job_id TEXT, attempt INTEGER,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS artifact_locations_object ON artifact_locations(object_kind, object_id);
CREATE INDEX IF NOT EXISTS artifact_locations_run ON artifact_locations(run_id, relpath);

-- v5 (WP-4 configuration revisions, F5d / DD-A23; additive) ------------------------------------------------------------
-- One row per recorded revision of a candidate model configuration: original (the uploaded / registered file), selected
-- (bound by an explicit training selection), executed (what the trainer ran), generated (written by the proposer).
-- ``source_sha256`` is the sha256 of the stored bytes (``text`` when core holds them; an ``original`` upload's bytes are
-- also in the gateway store under ``artifact_id`` = upl-<sha256>), ``canonical_hash`` the yaml_hash of the canonical YAML
-- and ``canonical_sha256`` the sha256 of the canonical text, so the row records both digests.
CREATE TABLE IF NOT EXISTS config_revisions (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES runs(id),
  candidate_id TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('original','selected','executed','generated')),
  source_sha256 TEXT NOT NULL,
  canonical_hash TEXT,
  canonical_sha256 TEXT,
  architecture_hash TEXT,
  parent_revision_id TEXT REFERENCES config_revisions(id),
  artifact_id TEXT,
  text TEXT,
  meta_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS config_revisions_candidate ON config_revisions(run_id, candidate_id, created_at);

-- v6 (DD-A66 / WP-5 section 2 R11, T3-34; additive) --------------------------------------------------------------------
-- Retention prune round trip of runner-held checkpoints: orchestration.retention plans a deletion -> one row here
-- (``id`` = prq-<sha of the checkpoint id>: the idempotency key; one request per checkpoint) -> the heartbeat of the
-- holding runner carries it as ``control.prune`` (at most once per lease: delivered_job_id / delivered_attempt) -> the
-- runner deletes and reports E11 -> DONE + checkpoints.state PRUNED. CANCELLED: the checkpoint became protected (latest
-- full state / referenced by an evaluation row) or was pruned otherwise before the order was handed out.
CREATE TABLE IF NOT EXISTS prune_requests (
  id TEXT PRIMARY KEY,
  checkpoint_id TEXT NOT NULL UNIQUE REFERENCES checkpoints(id),
  run_id TEXT NOT NULL REFERENCES runs(id),
  candidate_id TEXT NOT NULL,
  runner_id TEXT NOT NULL,
  relpath TEXT NOT NULL,
  sha256 TEXT NOT NULL,
  bytes INTEGER NOT NULL CHECK (bytes >= 0),
  policy TEXT NOT NULL,
  reason TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('PENDING','DELIVERED','DONE','CANCELLED')),
  planned_by TEXT,
  deliveries INTEGER NOT NULL DEFAULT 0 CHECK (deliveries >= 0),
  delivered_job_id TEXT,
  delivered_attempt INTEGER,
  delivered_at TEXT,
  cancel_reason TEXT,
  report_json TEXT,
  reported_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS prune_requests_runner ON prune_requests(runner_id, state, created_at);
CREATE INDEX IF NOT EXISTS prune_requests_run ON prune_requests(run_id, candidate_id);

INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', '1');
