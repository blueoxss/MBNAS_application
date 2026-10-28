"""QA: workspace integrity (criterion sets 1, 12) and the recorded baseline14 registry evidence (set 3).

These read the real host state under /root/detection (read-only). Hash/commit constants are the values recorded at the
start of the integration; a mismatch means the research repository or the secret hygiene changed.
"""
from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.legacy_records  # DD-A22: record test of an earlier integration round; excluded by default

ROOT = Path(os.environ.get("MBNAS_TEST_ROOT", "/root/detection"))  # 2026-10-15: injectable (MBNAS_TEST_ROOT / _ETRI_ROOT)
# 2026-10-15: the two projects moved into ETRI_NAS/application (MBNAS_runs / MBNAS_data stay under ROOT)
ETRI = ROOT / "ETRI_NAS"  # WP-1: MBNAS_TEST_ETRI_ROOT removed (contract §9); legacy record of the old host layout
APP = ETRI / "application"
SERVER, WEB = APP / "MBNAS_server", APP / "MBNAS_web"
ETRI_HEAD = "1d3a459"
REGISTRY_DIR = ROOT / "MBNAS_runs" / "registry"


def git_raw(*args: str, cwd: Path = ROOT) -> str:
    """stdout of a read-only git command, not stripped (porcelain and -z output are positional); no optional locks."""
    return subprocess.run(["git", "--no-optional-locks", *args], cwd=cwd, capture_output=True, encoding="utf-8",
                          errors="surrogateescape", check=True).stdout


def git(*args: str, cwd: Path = ROOT) -> str:
    return git_raw(*args, cwd=cwd).strip()


def status_paths(repo: Path) -> list[str]:
    """Every path ``git status`` reports for ``repo`` (both paths of a rename or copy), repo-relative and verbatim.

    Parsed from NUL-separated porcelain v1, which is neither stripped nor quoted. The stripped ``--short`` text lost the
    leading blank of its first line (`` M path`` became ``M path``, so ``ln[3:]`` cut the first character of the first
    path) and quoted paths with special characters.
    """
    fields, paths = iter(git_raw("status", "--porcelain=v1", "-z", "--untracked-files=all", cwd=repo).split("\0")), []
    for entry in fields:
        if entry:
            paths.append(entry[3:])
            if "R" in entry[:2] or "C" in entry[:2]:  # -z order of a rename or copy: "XY <new>\0<old>\0"
                paths.append(next(fields))
    return paths


def test_projects_are_siblings_and_the_package_is_mbnas_server():
    assert SERVER.parent == WEB.parent == APP
    assert not str(SERVER).startswith(str(WEB) + "/") and not str(WEB).startswith(str(SERVER) + "/")
    import mbnas_server

    assert Path(mbnas_server.__file__).resolve().is_relative_to(SERVER / "src")
    for rel in ("pyproject.toml", "src/mbnas_server/api/app.py", "src/mbnas_server/worker/main.py", "src/mbnas_server/db/schema.sql",
                "src/mbnas_server/training/stage_trainer.py", "src/mbnas_server/search/ga.py", "src/mbnas_server/benchmark/pipeline.py",
                "src/mbnas_server/artifacts/registry.py", "src/mbnas_server/provenance/source_hash.py", "contracts/openapi.json"):
        assert (SERVER / rel).is_file(), rel
    for rel in ("package.json", "package-lock.json", "src/App.tsx", "src/api/client.ts", "src/api/sse.ts", "src/components/Scatter.tsx",
                "src/components/CandidateModal.tsx", "README.md"):
        assert (WEB / rel).is_file(), rel
    assert {p.stem for p in (WEB / "src" / "screens").glob("*.tsx")} == {"Archive", "Compare", "History", "Lineage", "Monitor", "Server", "Settings"}  # 2026-10-15: + NAS archive screens


def test_original_research_repository_is_untouched():
    audit = (SERVER / "docs" / "orchestration" / "ENVIRONMENT_AUDIT.md").read_text()
    external = audit.split("## External changes to ETRI_NAS", 1)[-1] if "## External changes to ETRI_NAS" in audit else ""
    # ETRI_NAS/application/ is where the user placed the MBNAS projects (2026-10-15); it is not research code
    dirty = [p for p in status_paths(ETRI) if not p.startswith(APP.name + "/")]
    # The user's own sessions (e.g. an interactive Codex CLI or the COCO campaign working in ETRI_NAS) may change it;
    # this work never does. Every dirty path must be recorded, path by path, as an external change in ENVIRONMENT_AUDIT.md.
    unrecorded = [p for p in dirty if p.rstrip("/") not in external]
    assert not unrecorded, f"ETRI_NAS has unrecorded changes (not external per ENVIRONMENT_AUDIT): {unrecorded}"
    head = git("rev-parse", "HEAD", cwd=ETRI)
    if not head.startswith(ETRI_HEAD):
        # A HEAD move is allowed only when ENVIRONMENT_AUDIT records it as an external change with its reflog entry,
        # i.e. this work did not move it (master prompt §1: no pull/merge/reset of ETRI_NAS by this work).
        assert head in external, f"ETRI_NAS HEAD {head} moved and is not recorded as an external change"
        reflog = git("reflog", "-n", "1", "--date=iso", cwd=ETRI)
        assert head[:7] in reflog and reflog.split("HEAD@{", 1)[1][:19] in external, (reflog, "reflog time not recorded")
    # A name match outside application/ is allowed only for a file that came with a recorded external commit: git
    # tracks it at the HEAD checked above and the external section names its path (e.g. the user's research-direction
    # PDF of 704064c). An untracked or unrecorded match, or a directory, still fails.
    tracked = set(git_raw("ls-tree", "-r", "-z", "--name-only", "HEAD", cwd=ETRI).split("\0"))
    outside_app = [c for c in ETRI.iterdir() if c != APP]
    for pattern in ("mbnas_server*", "MBNAS_*"):
        found = [c for c in outside_app if c.match(pattern)] + [p for c in outside_app if c.is_dir() for p in c.rglob(pattern)]
        found = [p for p in found if not (p.is_file() and (rel := p.relative_to(ETRI).as_posix()) in tracked and rel in external)]
        assert not found, f"adapter code must live only in the new project (ETRI_NAS/application): {found[:5]}"


def test_secret_hygiene_files():
    env = SERVER / ".env"
    if env.exists():
        assert stat.S_IMODE(env.stat().st_mode) == 0o600
        # cwd=SERVER: the enclosing repository (ETRI_NAS since 2026-10-15) is the one that must ignore it
        assert subprocess.run(["git", "check-ignore", "-q", str(env)], cwd=SERVER).returncode == 0, ".env must be git-ignored"
    example = (SERVER / ".env.example").read_text().splitlines()
    for line in example:
        if line.strip() and not line.startswith("#"):
            name, _, value = line.partition("=")
            assert name.startswith("MBNAS_") and value == "", f"{name} must list the name only"
    assert {"MBNAS_API_TOKEN", "MBNAS_ARTIFACT_SIGNING_KEY", "MBNAS_CORS_ORIGINS", "MBNAS_MAX_REQUEST_BODY_BYTES"} <= {
        line.partition("=")[0] for line in example}


def test_lock_files_and_environment_record_exist():
    lock = (SERVER / "requirements.lock.txt").read_text()
    assert "torch==2.6.0" in lock and "ultralytics==8.4.173" in lock and "fastapi==" in lock
    env = json.loads((SERVER / "environment.lock.json").read_text())
    assert env.get("nvidia") or env.get("torch_build")
    pkg = json.loads((WEB / "package-lock.json").read_text())
    assert pkg["lockfileVersion"] >= 2 and "node_modules/react" in pkg["packages"]


def test_source_tree_hash_is_deterministic():
    out = [subprocess.run([str(SERVER / ".venv/bin/python"), str(SERVER / "scripts/source_tree_hash.py"), "--digest-only"],
                          capture_output=True, text=True, check=True).stdout.strip() for _ in range(2)]
    assert out[0] == out[1] and out[0].startswith("sha256:") and len(out[0]) == len("sha256:") + 64
    # artifact_ref covers build and test inputs too (S1-F6), and every one of them exists on this host
    full = json.loads(subprocess.run([str(SERVER / ".venv/bin/python"), str(SERVER / "scripts/source_tree_hash.py")],
                                     capture_output=True, text=True, check=True).stdout)
    assert full["sha256"] == out[0]
    roots = {r["label"]: r for r in full["roots"]}
    for label in ("MBNAS_server/pyproject.toml", "MBNAS_server/requirements.lock.txt", "MBNAS_server/environment.lock.json",
                  "MBNAS_web/tests", "MBNAS_web/e2e", "MBNAS_web/scripts", "MBNAS_web/public", "MBNAS_web/package.json",
                  "MBNAS_web/package-lock.json", "MBNAS_web/index.html", "MBNAS_web/tsconfig.json", "MBNAS_web/vite.config.ts",
                  "MBNAS_web/playwright.config.ts", "MBNAS_web/playwright.real.config.ts"):
        assert roots[label]["present"], label
    assert roots["MBNAS_web/scripts"]["files"] >= 1 and roots["MBNAS_web/tests"]["files"] >= 10
    assert not any(label.startswith(("MBNAS_server/docs", "MBNAS_web/evidence")) or label.endswith((".env", "README.md"))
                   for label in roots)


# -- registry evidence -------------------------------------------------------------------------------------------
def _latest_registry() -> dict[str, dict]:
    try:
        files = sorted(REGISTRY_DIR.glob("registry_*.json"))
    except OSError:  # PermissionError when the original host's /root is unreadable (2026-10-15)
        files = []
    if not files:
        pytest.skip("no registry probe evidence on this host")
    doc = json.loads(files[-1].read_text())
    return {e["id"]: e for e in doc["registry"]}


def test_all_14_baselines_are_present_with_status_reason_capabilities_and_evidence():
    from mbnas_contracts.constants import DEFAULT_BASELINE14

    reg = _latest_registry()
    assert len(DEFAULT_BASELINE14) == 14 and set(DEFAULT_BASELINE14) <= set(reg), set(DEFAULT_BASELINE14) - set(reg)
    for bid in DEFAULT_BASELINE14:
        e = reg[bid]
        assert e["family"] and e["status"] in ("AVAILABLE", "BLOCKED_SOURCE", "UNSUPPORTED", "UNVERIFIED") and e["reason"], bid
        cap, ev = e["capabilities"], e["evidence"]
        for key in ("init_modes", "native_loss", "import_class", "version", "train", "val", "full_state_resume", "export_onnx", "export_trt",
                    "precisions", "input_size_rule", "determinism_profile"):
            assert cap.get(key) not in (None, "", []), (bid, key)
        assert set(cap["init_modes"]) <= {"scratch", "pretrained_native"} and "scratch" in cap["init_modes"], bid
        assert ev["build"]["ok"] is True and ev["import_class"] and ev["version"] and ev["config_ref"], bid
        assert str(ev["build"].get("structural_hash_nc1", "")).startswith("sha256:"), bid
        weights = ev["weights"]
        assert weights.get("sha256") or weights.get("status") == "NOT_APPLICABLE" and weights.get("reason"), bid
        assert cap["train"]["status"].startswith("VERIFIED") and cap["val"]["status"].startswith("VERIFIED"), bid
        assert cap["full_state_resume"]["status"].startswith("VERIFIED") and cap["full_state_resume"]["declared"], bid
        # honesty: ONNX is the CPU probe's; TensorRT comes from benchmark evidence only (S3-F6: snapshot == load_registry)
        assert cap["export_onnx"]["status"].startswith("VERIFIED"), bid
        trt = cap["export_trt"]
        assert trt["status"] in ("VERIFIED_GPU", "VERIFIED_ADAPTER_PATH", "FAILED_GPU", "NOT_RUN"), (bid, trt)
        if trt["status"] == "NOT_RUN":
            assert trt.get("reason") and trt.get("evidence") is None, (bid, trt)
        else:
            assert trt.get("evidence") and trt.get("engines") and trt.get("device") == "host-gpu-0", (bid, trt)
        host = cap["devices"]["host-gpu-0"]
        assert host["status"] in ("VERIFIED_TRT", "VERIFIED_TRT_ADAPTER_PATH", "FAILED_TRT", "NOT_RUN"), (bid, host)
        assert host["status"] != "NOT_RUN" or host.get("reason"), (bid, host)


def test_latest_registry_snapshot_equals_load_registry_for_trt_and_devices():
    """S3-F6: the persisted snapshot does not contradict the live /registry on export_trt / devices."""
    from mbnas_core.registry import load_registry

    reg, live = _latest_registry(), load_registry(str(REGISTRY_DIR.parent))
    assert set(reg) == set(live)
    for mid, entry in live.items():
        for key in ("export_trt", "devices"):
            assert reg[mid]["capabilities"][key] == json.loads(json.dumps(entry["capabilities"][key], default=str)), (mid, key)


def test_families_have_distinct_adapters_and_backends():
    reg = _latest_registry()
    backends = {reg[b]["capabilities"]["backend"] for b in reg}
    assert {"ultralytics", "rfdetr", "etri_hybrid"} <= backends
    by_family = {}
    for e in reg.values():
        by_family.setdefault(e["id"].split("-")[0] if e["id"].startswith(("yolo", "rf", "rt")) else "etri", set()).add(e["capabilities"]["import_class"])
    assert len({frozenset(v) for v in by_family.values()}) >= 4  # YOLO11/YOLO26 share ultralytics, RT-DETR, RF-DETR and ETRI differ
    assert all(reg[i]["capabilities"]["import_class"].startswith("ultralytics") for i in ("yolo11s", "yolo26s", "rt-detr-l"))
    assert reg["rf-detr-small"]["capabilities"]["import_class"].startswith("rfdetr")
    assert "etri_hybrid" in reg["etri-controlled-cnn-transformer"]["capabilities"]["import_class"]


def test_unsupported_entries_are_blocked_by_the_validator_with_their_reason(registry, ctx_factory, make_dataset, tmp_roots):
    """RF-DETR without scratch support and a Jetson device are refused with the documented codes (never skipped silently)."""
    from mbnas_core.protocol.presets import pilot_smoke_protocol
    from mbnas_core.protocol.validator import validate

    paths = make_dataset()
    proto = pilot_smoke_protocol()
    proto["console"]["paths"] = {k: paths[k] for k in ("dataset_yaml", "train_root", "search_val_root", "final_eval_root")}
    proto["console"]["paths"]["output_root"] = str(tmp_roots["runs"] / "out")
    registry["rf-detr-nano"]["capabilities"]["init_modes"] = ["pretrained_native"]
    registry["rt-detr-l"].update(status="UNSUPPORTED", reason="RT-DETR build unsupported on this host (qa)")
    proto["console"]["benchmark"]["device"] = "Jetson Orin NX"
    result = validate(proto, ctx_factory())
    codes = {e["code"] for e in result.errors}
    assert {"INIT_UNSUPPORTED", "BASELINE_UNAVAILABLE", "DEVICE_UNAVAILABLE"} <= codes
    assert any("RT-DETR build unsupported on this host (qa)" in e["message"] for e in result.errors)
