"""DD-A32 / DD-A49 (a): every training and EVALUATE result the runner commits carries ``outputs.device_used``
(``cpu`` on a CPU-only runner, else the CUDA device the job ran on) and ``outputs.device_uuid`` (the uuid the child
read through torch, else the NVML uuid the runner registered).

UNIT level against the stdlib fake core (``tests/_shared/fake_core.py``) with the SYNTHETIC stub handlers; the core
side (``raw_metrics.device_used``) is covered by ``tests/owner/runner_protocol/test_device_used.py``. No GPU is used:
the "GPU runner" cases fake the runner's NVML identity, never query NVML, and every child runs with
``CUDA_VISIBLE_DEVICES=""``; torch observations in the child are faked through ``sys.modules``.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from fake_core import FakeCore, envelope

from mbnas_contracts.schemas.jobs import JobKind, JobResult, JobSpec
from mbnas_runner import child, gpu
from mbnas_runner.config import RunnerSettings
from mbnas_runner.daemon import CUDA_DEVICE_ORDER as DAEMON_CUDA_ORDER
from mbnas_runner.daemon import DEVICE_RESULT_KINDS, RunnerDaemon
from mbnas_runner.journal import AttemptJournal

SHARED = Path(__file__).resolve().parents[2] / "_shared"
GPU_UUID = "GPU-00000000-1111-2222-3333-444444444444"  # SYNTHETIC
FAKE_DEVICE = {"id": "host-gpu-0", "index": 0, "uuid": GPU_UUID, "name": "SYNTHETIC GPU", "memory_total_mb": 1}


@pytest.fixture(autouse=True)
def no_nvml(monkeypatch):
    """No real NVML query from these tests: the runner's device identity, occupancy and samples are faked."""
    monkeypatch.setattr(gpu, "device_info", lambda index: {"id": f"host-gpu-{index}", "index": index, "uuid": None,
                                                         "name": None, "memory_total_mb": None})
    monkeypatch.setattr(gpu, "compute_pids", lambda index: [])
    monkeypatch.setattr(gpu, "sample", lambda index: {})


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("MBNAS_JOB_HANDLERS_MODULE", "stub_handlers")
    monkeypatch.setenv("PYTHONPATH", str(SHARED))
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "")
    monkeypatch.setenv("MBNAS_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MBNAS_STUB_EPOCH_S", "0.02")
    return tmp_path


def rsettings(tmp_path: Path, url: str, **kw) -> RunnerSettings:
    tok = tmp_path / "token"
    tok.write_text("tok-test\n")
    return RunnerSettings(core_url=url, token_file=str(tok), runner_id="runner-A", work_root=str(tmp_path / "work"),
                          request_backoff_s=0.05, poll_s=0.1, kill_grace_s=2, **kw)


def _popen_cpu(argv, env, log):
    """The daemon's child, but always with ``CUDA_VISIBLE_DEVICES=""`` (this test host's GPUs are never touched)."""
    return subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=open(log, "a", encoding="utf-8"), env={**env, "CUDA_VISIBLE_DEVICES": ""},
                            text=True, bufsize=1)


def _gpu_runner(monkeypatch, settings, logs: list[str] | None = None, *, uuid: str | None = GPU_UUID) -> RunnerDaemon:
    """A runner that registered a (fake) GPU: no NVML query (identity, occupancy and samples are faked) and a child
    that still runs with ``CUDA_VISIBLE_DEVICES=""``."""
    monkeypatch.setattr(gpu, "device_info", lambda index: {**FAKE_DEVICE, "uuid": uuid})
    d = RunnerDaemon(settings, log=(logs.append if logs is not None else None))
    d.cpu_only = False
    d.device = {**FAKE_DEVICE, "uuid": uuid}
    d._spawn_fn = _popen_cpu
    return d


def _committed(core: FakeCore) -> dict:
    (_, res), = core.results.items()
    return res["body"]


def _result(outputs: dict | None = None) -> JobResult:
    return JobResult(job_id="job-1", status="SUCCEEDED", outputs=outputs or {})


# -- the daemon over the fake core -------------------------------------------------------------------------------
def test_cpu_only_runner_commits_device_used_cpu_without_uuid(env):
    core = FakeCore([envelope(target=2)])
    try:
        d = RunnerDaemon(rsettings(env, core.url))
        assert d.cpu_only and d.device["id"] == "cpu"
        assert d.run(once=True) == 0
    finally:
        core.stop()
    body = _committed(core)
    out = body["result"]["outputs"]
    assert body["result"]["status"] == "SUCCEEDED"
    assert out["device_used"] == "cpu" and out["device_uuid"] is None and body["device_uuid"] is None
    assert out["evaluation"]["raw_metrics"]["child_env"]["CUDA_VISIBLE_DEVICES"] == ""  # the child really ran CPU-only


def test_gpu_runner_commits_the_assigned_cuda_device_with_its_registered_uuid(env, monkeypatch):
    core = FakeCore([envelope(target=1)])
    try:
        d = _gpu_runner(monkeypatch, rsettings(env, core.url))
        assert d.run(once=True) == 0
    finally:
        core.stop()
    body = _committed(core)
    out = body["result"]["outputs"]
    assert body["result"]["status"] == "SUCCEEDED"
    # the stub child never initialises CUDA: the assigned device (core name cuda:0) and the registered NVML uuid
    assert out["device_used"] == "cuda:0" and out["device_uuid"] == GPU_UUID == body["device_uuid"]
    register = next(b for p, b in core.calls if p.endswith("/register"))
    assert register["device_uuid"] == GPU_UUID and register["device"] == "host-gpu-0"


def test_child_env_pins_cuda_device_order_to_nvml_pci_bus_order(env, monkeypatch):
    """gpu_index is an NVML index (PCI bus order): the child gets CUDA_DEVICE_ORDER=PCI_BUS_ID next to
    CUDA_VISIBLE_DEVICES=<gpu_index>, so CUDA enumerates like NVML and the child's cuda:0 is the registered GPU; an
    inherited CUDA_DEVICE_ORDER never reaches the child. Captured at the spawn boundary (the exact child env)."""
    monkeypatch.setenv("CUDA_DEVICE_ORDER", "FASTEST_FIRST")
    seen: list[dict] = []

    def capture(argv, env, log):
        seen.append(dict(env))
        return _popen_cpu(argv, env, log)

    for make in (lambda s: RunnerDaemon(s), lambda s: _gpu_runner(monkeypatch, s)):
        core = FakeCore([envelope(target=1)])
        try:
            d = make(rsettings(env, core.url, gpu_index=1))
            d._spawn_fn = capture
            assert d.run(once=True) == 0
        finally:
            core.stop()
        assert _committed(core)["result"]["status"] == "SUCCEEDED"
    cpu_env, gpu_env = seen
    assert DAEMON_CUDA_ORDER == "PCI_BUS_ID"
    assert cpu_env["CUDA_DEVICE_ORDER"] == gpu_env["CUDA_DEVICE_ORDER"] == "PCI_BUS_ID"
    assert cpu_env["CUDA_VISIBLE_DEVICES"] == "" and gpu_env["CUDA_VISIBLE_DEVICES"] == "1"


def test_with_device_rules(env, monkeypatch):
    logs: list[str] = []
    d = _gpu_runner(monkeypatch, rsettings(env, "http://127.0.0.1:9"), logs)
    out = d._with_device(_result({"device_used": "cuda:0", "device_uuid": "GPU-child"}), {"device": "cuda:0"}).outputs
    assert (out["device_used"], out["device_uuid"]) == ("cuda:0", "GPU-child")  # what the child ran on wins
    assert any("GPU-child" in m and GPU_UUID in m and "CUDA_DEVICE_ORDER" in m for m in logs)
    out = d._with_device(_result({"evaluation": {"ap": 0.1}}), {"device": "host-gpu-0"}).outputs
    assert (out["device_used"], out["device_uuid"]) == ("cuda:0", GPU_UUID)  # a target id is not a torch device
    assert out["evaluation"] == {"ap": 0.1}
    out = d._with_device(_result(), {"device": "cpu"}).outputs
    assert (out["device_used"], out["device_uuid"]) == ("cpu", None)  # an explicit CPU job has no GPU uuid
    out = d._with_device(_result(), None).outputs
    assert (out["device_used"], out["device_uuid"]) == ("cuda:0", GPU_UUID)

    no_nvml = _gpu_runner(monkeypatch, rsettings(env, "http://127.0.0.1:9"), uuid=None)
    out = no_nvml._with_device(_result({"device_used": "cuda:0", "device_uuid": "GPU-torch"}), {}).outputs
    assert out["device_uuid"] == "GPU-torch"  # no NVML in the daemon: the uuid torch reported in the child
    assert no_nvml._with_device(_result(), {}).outputs["device_uuid"] is None  # nothing known: never invented

    cpu = RunnerDaemon(rsettings(env, "http://127.0.0.1:9"))
    assert cpu.cpu_only
    out = cpu._with_device(_result({"device_used": "cuda:0", "device_uuid": "GPU-x"}), {"device": "cuda:0"}).outputs
    assert (out["device_used"], out["device_uuid"]) == ("cpu", None)  # DD-A32: a CPU-only runner always says cpu
    assert DEVICE_RESULT_KINDS == {"BASELINE_TRAIN", "CANDIDATE_TRAIN", "FINAL_RETRAIN", "EVALUATE"}


def test_replayed_child_exit_without_result_reports_the_device_from_the_journal_spec(env):
    core = FakeCore([])
    s = rsettings(env, core.url)
    e = envelope()
    core.leased["job-1"] = {**e, "_state": "RUNNING"}
    j = AttemptJournal.create(s.work_root, "job-1", 1)
    j.record_claim({"envelope": e, "ticket": "tkt-job-1", "server_time": "2026-10-26T00:00:00Z"})
    j.record_spec({**e["job"]["spec"], "attempt": 1, "params": {**e["job"]["spec"]["params"], "device": "cuda:0"}})
    j.record_started({"started_at": "2026-10-26T00:00:00.000000Z", "lease_expires_at": None})
    try:
        d = RunnerDaemon(s)
        d.register()
        d.replay()
    finally:
        core.stop()
    out = _committed(core)["result"]["outputs"]
    assert _committed(core)["result"]["reason_code"] == "CHILD_EXIT_NO_RESULT"
    assert out["device_used"] == "cpu" and out["device_uuid"] is None and out["retryable"] is True
    assert j.spec["params"]["device"] == "cuda:0"


def test_envelope_refused_before_spawn_reports_no_device(env):
    e = envelope(target=3)
    e["job"]["spec"]["fixed_horizon"] = 2  # invalid spec: nothing runs on any device
    core = FakeCore([e])
    try:
        RunnerDaemon(rsettings(env, core.url)).run(once=True)
    finally:
        core.stop()
    out = _committed(core)["result"]["outputs"]
    assert _committed(core)["result"]["reason_code"] == "ENVELOPE_INVALID"
    assert "device_used" not in out and "device_uuid" not in out


# -- the child's observation ---------------------------------------------------------------------------------------
class _Uuid:
    def __str__(self) -> str:  # torch's _CUuuid prints the bare 8-4-4-4-12 form
        return "e8fe2321-0000-1111-2222-333344445555"


def _fake_torch(initialised: bool):
    cuda = SimpleNamespace(is_initialized=lambda: initialised, current_device=lambda: 0,
                           get_device_properties=lambda index: SimpleNamespace(uuid=_Uuid(), name="SYNTHETIC"))
    return SimpleNamespace(cuda=cuda)


def _spec(kind: JobKind) -> JobSpec:
    training = {"candidate_id": "c1", "rung": "L0", "cumulative_target": 1, "fixed_horizon": 1}
    return JobSpec(job_id="j1", run_id="r1", kind=kind, protocol_hash="sha256:p", params={},
                   **(training if kind.value in DEVICE_RESULT_KINDS - {"EVALUATE"} else {}))


def test_child_reports_the_cuda_device_torch_initialised_and_keeps_handler_values(monkeypatch):
    monkeypatch.setitem(sys.modules, "torch", _fake_torch(True))
    seen = child.observed_cuda_device()
    assert seen == {"device_used": "cuda:0", "device_uuid": "GPU-e8fe2321-0000-1111-2222-333344445555"}
    res = child.with_observed_device(_spec(JobKind.EVALUATE), JobResult(job_id="j1", status="SUCCEEDED",
                                                                        outputs={"evaluation": {"ap": 0.2}}))
    assert res.outputs == {**seen, "evaluation": {"ap": 0.2}}
    kept = child.with_observed_device(_spec(JobKind.BASELINE_TRAIN),
                                      JobResult(job_id="j1", status="SUCCEEDED", outputs={"device_used": "cuda:3"}))
    assert kept.outputs["device_used"] == "cuda:3" and kept.outputs["device_uuid"] == seen["device_uuid"]
    owner = JobResult(job_id="j1", status="SUCCEEDED", outputs={"candidates": []})
    assert child.with_observed_device(_spec(JobKind.PROPOSE_GENERATION), owner) is owner  # not a device kind


def test_child_reports_nothing_without_an_initialised_cuda_context(monkeypatch):
    monkeypatch.setitem(sys.modules, "torch", _fake_torch(False))
    assert child.observed_cuda_device() == {}
    res = JobResult(job_id="j1", status="FAILED", reason_code="X", outputs={"retryable": True})
    assert child.with_observed_device(_spec(JobKind.CANDIDATE_TRAIN), res) is res
    monkeypatch.setitem(sys.modules, "torch", None)  # torch never imported by the job (an import would fail here)
    assert child.observed_cuda_device() == {}
