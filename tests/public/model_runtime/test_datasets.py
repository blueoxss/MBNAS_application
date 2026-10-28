"""datasets: safe YAML schema, manifests (hashes, label validation, duplicates, cache), COCO conversion, subset CLI."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from mbnas_model_runtime.datasets.coco_convert import manifest_to_coco, write_coco_json
from mbnas_model_runtime.datasets.manifest import (
    DatasetService, Manifest, build_manifest, class_mapping_hash, cross_split_overlap, label_path_for,
)
from mbnas_model_runtime.datasets.subset import SubsetError, SubsetSpec, build_subset
from mbnas_model_runtime.datasets.yaml_safe import DatasetYamlError, load_dataset_yaml

SERVER = Path(__file__).resolve().parents[3]  # TARGET_ROOT (WP-1 layout)


# -- yaml ----------------------------------------------------------------------------------------------------------
def write(tmp_path: Path, text: str) -> str:
    p = tmp_path / "data.yaml"
    p.write_text(text)
    return str(p)


def test_yaml_resolves_relative_to_path(tmp_path) -> None:
    dy = load_dataset_yaml(write(tmp_path, "path: sub\ntrain: images/train\nval: images/val\nnames: {0: drone, 1: bird}\n"))
    assert dy.root == str(tmp_path / "sub") and dy.splits["train"] == str(tmp_path / "sub/images/train")
    assert dy.names == {0: "drone", 1: "bird"} and dy.nc == 2 and "test" not in dy.splits


def test_yaml_defaults_and_nc_only(tmp_path) -> None:
    dy = load_dataset_yaml(write(tmp_path, "train: /abs/train\nval: v\nnc: 2\ndownload: rm -rf /\n"))
    assert dy.root == str(tmp_path) and dy.splits["train"] == "/abs/train" and dy.names == {0: "0", 1: "1"}
    assert dy.ignored_keys == ("download",)
    assert load_dataset_yaml(write(tmp_path, "train: t\nval: v\nnames: [0]\n")).names == {0: "0"}


@pytest.mark.parametrize("text", [
    "- a\n- b\n", "train: [a, b]\nval: v\nnames: [x]\n", "val: v\nnames: [x]\n", "train: t\nval: v\n",
    "train: t\nval: v\nnames: [x]\nnc: 2\n", "train: t\nval: v\nnames: {1: x}\n", "train: t\nval: v\nnames: [[x]]\n",
    "train: t\nval: v\nnames: [x]\npath: 5\n", "!!python/object/apply:os.system ['echo pwned']\n", "a: [unclosed\n",
])
def test_yaml_rejects(tmp_path, text: str) -> None:
    with pytest.raises(DatasetYamlError) as exc:
        load_dataset_yaml(write(tmp_path, text))
    assert exc.value.code == "DATASET_YAML_INVALID"


# -- manifest ------------------------------------------------------------------------------------------------------
def test_label_rule() -> None:
    assert label_path_for("/d/images/train/a.b.jpg") == "/d/labels/train/a.b.txt"
    assert label_path_for("/images/x/images/train/a.png") == "/images/x/labels/train/a.txt"


def test_build_manifest_tiny(make_dataset) -> None:
    paths = make_dataset(counts={"train": 3, "val": 1, "test": 1})
    lbl = Path(paths["root"]) / "labels" / "train"
    (lbl / "train_001.txt").write_text("")
    (lbl / "train_002.txt").unlink()
    m = build_manifest(paths["train_root"], names={0: "drone"})
    assert [s.sample_id for s in m.samples] == ["train_000", "train_001", "train_002"]
    s0 = m.samples[0]
    assert (s0.width, s0.height) == (32, 24) and s0.boxes == [(0, 0.5, 0.5, 0.25, 0.25)] and len(s0.image_sha256) == 64
    assert m.counts == {"images": 3, "labelled": 2, "missing_labels": 1, "empty_labels": 1, "boxes": 1,
                        "per_class": {"0": 1}, "issues": 0}
    assert m.samples[2].label is None and m.class_ids == [0] and m.split_hash.startswith("sha256:")
    assert m.class_mapping_hash == class_mapping_hash({0: "drone"})


def test_split_hash_is_location_independent_and_content_sensitive(make_dataset, tmp_roots) -> None:
    paths = make_dataset()
    copy_root = tmp_roots["data"] / "copy"
    shutil.copytree(paths["root"], copy_root)
    a = build_manifest(paths["train_root"], names={0: "drone"})
    b = build_manifest(str(copy_root / "images/train"), names={0: "drone"})
    assert a.split_hash == b.split_hash
    (copy_root / "labels/train/train_000.txt").write_text("0 0.5 0.5 0.3 0.3\n")
    assert build_manifest(str(copy_root / "images/train"), names={0: "drone"}).split_hash != a.split_hash
    assert build_manifest(paths["train_root"], names={0: "uav"}).split_hash != a.split_hash


@pytest.mark.parametrize("row,code", [("0 0.5 0.5 0.2", "LABEL_INVALID"), ("0 0.5 0.5 0.2 0.2 0.1", "LABEL_INVALID"),
                                      ("0 inf 0.5 0.2 0.2", "LABEL_INVALID"), ("a b c d e", "LABEL_INVALID"),
                                      ("0 0.5 0.5 0 0.2", "LABEL_INVALID"), ("0 1.2 0.5 0.1 0.2", "LABEL_INVALID"),
                                      ("1 0.5 0.5 0.2 0.2", "CLASS_MAP_MISMATCH"), ("0.5 0.5 0.5 0.2 0.2", "CLASS_MAP_MISMATCH"),
                                      ("-1 0.5 0.5 0.2 0.2", "CLASS_MAP_MISMATCH")])
def test_label_row_validation(make_dataset, row: str, code: str) -> None:
    paths = make_dataset(counts={"train": 1}, label=row + "\n0 0.5 0.5 0.1 0.1\n")
    m = build_manifest(paths["train_root"], names={0: "drone"})
    assert [(i.code, i.line) for i in m.issues] == [(code, 1)]
    assert len(m.samples[0].boxes) == 1


def test_duplicates_overlap_and_bad_image(make_dataset) -> None:
    paths = make_dataset(counts={"train": 2, "val": 1, "test": 1})
    train = Path(paths["train_root"])
    shutil.copyfile(train / "train_000.png", train / "dup.png")
    (train / "broken.png").write_bytes(b"not an image")
    m = build_manifest(paths["train_root"], names={0: "drone"})
    assert ["dup", "train_000"] in [sorted(d) for d in m.duplicates]
    assert any(i.code == "IMAGE_INVALID" and i.sample_id == "broken" for i in m.issues)
    shutil.copyfile(train / "train_001.png", Path(paths["search_val_root"]) / "leak.png")
    val = build_manifest(paths["search_val_root"], names={0: "drone"})
    overlap = cross_split_overlap({"train": m, "search_val": val})
    assert len(overlap) == 1 and overlap[0]["sample_ids"]["search_val"] == ["leak"]


def test_symlink_escape_excluded(make_dataset, tmp_path) -> None:
    paths = make_dataset(counts={"train": 1})
    outside = tmp_path / "outside.png"
    shutil.copyfile(Path(paths["train_root"]) / "train_000.png", outside)
    os.symlink(outside, Path(paths["train_root"]) / "evil.png")
    m = build_manifest(paths["train_root"], names={0: "drone"}, allow_roots=[paths["root"]])
    assert [s.sample_id for s in m.samples] == ["train_000"]
    assert [i.code for i in m.issues] == ["PATH_OUTSIDE_ALLOWLIST"]


def test_manifest_from_image_list(make_dataset) -> None:
    paths = make_dataset(counts={"train": 2})
    imgs = sorted(str(p) for p in Path(paths["train_root"]).iterdir())
    m = build_manifest(imgs, names={0: "drone"})
    assert [s.sample_id for s in m.samples] == ["train_000", "train_001"]
    with pytest.raises(ValueError):
        build_manifest([], names={0: "drone"})


def test_dataset_service_cache(make_dataset, settings) -> None:
    paths = make_dataset()
    svc = DatasetService.from_settings(settings)
    a = svc.manifest_for(paths["train_root"], {0: "drone"})
    cache_file = Path(settings.output_root) / "manifests" / (a.split_hash.split(":")[1] + ".json")
    assert cache_file.is_file()
    assert Manifest.model_validate_json(cache_file.read_text()) == a
    b = svc.manifest_for(paths["train_root"], {0: "drone"})
    assert b == a
    lbl = Path(paths["root"]) / "labels/train/train_000.txt"
    lbl.write_text("0 0.4 0.4 0.2 0.2\n")
    os.utime(lbl, ns=(1, 1))
    c = svc.manifest_for(paths["train_root"], {0: "drone"})
    assert c.split_hash != a.split_hash


def test_pilot_manifest_matches_subset_manifest(pilot_required) -> None:
    root = Path(os.environ["MBNAS_TEST_DATA_ROOT"]) / "pilot_visdrone_v1"  # A4/A8: same dir pilot_required checks
    subset = json.loads((root / "manifest.json").read_text())
    for role in ("train", "search_val", "final_eval"):
        m = build_manifest(str(root / "images" / role), names={0: "0"})
        expected = {s["sample_id"]: (s["image_sha256"], s["label_sha256"], s["boxes"]) for s in subset["roles"][role]["samples"]}
        got = {s.sample_id: (s.image_sha256, s.label_sha256, len(s.boxes)) for s in m.samples}
        assert got == expected and not m.issues


# -- coco ----------------------------------------------------------------------------------------------------------
def test_manifest_to_coco(make_dataset, tmp_path) -> None:
    paths = make_dataset(counts={"train": 2}, label="0 0.5 0.5 0.25 0.5\n")
    m = build_manifest(paths["train_root"], names={0: "drone"})
    coco = manifest_to_coco(m, category_id_offset=1)
    assert [i["file_name"] for i in coco["images"]] == ["train_000.png", "train_001.png"]
    ann = coco["annotations"][0]
    assert ann["bbox"] == [12.0, 6.0, 8.0, 12.0] and ann["category_id"] == 1 and ann["area"] == 96.0
    assert coco["categories"] == [{"id": 1, "name": "drone", "supercategory": "none"}]
    assert coco["info"]["split_hash"] == m.split_hash and coco["images"][0]["sample_id"] == "train_000"
    out = write_coco_json(m, tmp_path / "ann.json")
    assert json.loads(out.read_text())["annotations"] == json.loads(json.dumps(manifest_to_coco(m)))["annotations"]


# -- subset --------------------------------------------------------------------------------------------------------
def _source(tmp_path: Path) -> Path:
    from PIL import Image

    src = tmp_path / "src"
    for split, n in (("train", 6), ("val", 4), ("test", 4)):
        (src / "images" / split).mkdir(parents=True)
        (src / "labels" / split).mkdir(parents=True)
        for i in range(n):
            Image.new("RGB", (8, 8), (i * 20, len(split) * 30, 7)).save(src / "images" / split / f"{split}{i}.jpg")
            if i % 2 == 0:
                (src / "labels" / split / f"{split}{i}.txt").write_text("0 0.5 0.5 0.1 0.1\n")
    return src


def test_build_subset_deterministic(tmp_path) -> None:
    src = _source(tmp_path)
    logs: list[str] = []
    a = build_subset(SubsetSpec(src, tmp_path / "a", seed=7, n_train=3, n_search_val=2, n_final_eval=2), log=logs.append)
    b = build_subset(SubsetSpec(src, tmp_path / "b", seed=7, n_train=3, n_search_val=2, n_final_eval=2), log=lambda s: None)
    pick = lambda m: {r: [s["sample_id"] for s in m["roles"][r]["samples"]] for r in m["roles"]}  # noqa: E731
    assert pick(a) == pick(b) and a["roles"]["train"]["count"] == 3
    assert logs[-1].startswith("data.yaml: ") and "[ok] train: 3 images from train" in logs
    assert load_dataset_yaml(str(tmp_path / "a" / "data.yaml")).splits["test"] == str(tmp_path / "a/images/final_eval")
    assert build_subset(SubsetSpec(src, tmp_path / "a"), log=logs.append) is None and logs[-1].startswith("[skip]")
    with pytest.raises(SubsetError):
        build_subset(SubsetSpec(src, tmp_path / "c", n_train=99), log=lambda s: None)


def test_subset_cli_unchanged_interface(tmp_path) -> None:
    """The original option set and output of scripts/public/make_pilot_subset.py; --source and --out are required (no
    host default paths, DD-A59 b), so a call without them is refused by argparse before anything is read or written."""
    import re

    script = [sys.executable, "-B", str(SERVER / "scripts" / "public" / "make_pilot_subset.py")]
    helped = subprocess.run(script + ["--help"], capture_output=True, text=True, check=False)
    assert helped.returncode == 0, helped.stderr
    assert set(re.findall(r"--[a-z][a-z-]*", helped.stdout)) == {
        "--help", "--source", "--out", "--seed", "--n-train", "--n-search-val", "--n-final-eval", "--names"}
    for args, missing in (([], "--source, --out"), (["--out", str(tmp_path / "x")], "--source"),
                          (["--source", str(tmp_path / "x")], "--out")):
        refused = subprocess.run(script + args, capture_output=True, text=True, check=False)
        assert refused.returncode == 2, (args, refused.stderr)
        assert f"error: the following arguments are required: {missing}" in refused.stderr, refused.stderr
    assert not (tmp_path / "x").exists()
    src = _source(tmp_path)
    cmd = script + ["--source", str(src), "--out", str(tmp_path / "out"), "--n-train", "2", "--n-search-val", "1",
                    "--n-final-eval", "1", "--seed", "3"]
    done = subprocess.run(cmd, capture_output=True, text=True, check=False)
    assert done.returncode == 0, done.stderr
    assert done.stdout.splitlines()[-1] == f"data.yaml: {tmp_path / 'out' / 'data.yaml'}"
    assert done.stdout.splitlines() == ["[ok] train: 2 images from train", "[ok] search_val: 1 images from val",
                                        "[ok] final_eval: 1 images from test",
                                        f"data.yaml: {tmp_path / 'out' / 'data.yaml'}"]
    manifest = json.loads((tmp_path / "out" / "manifest.json").read_text())
    assert manifest["schema"] == "mbnas.pilot_subset/1" and manifest["seed"] == 3
    assert manifest["names"] == ["0"] and {r: v["count"] for r, v in manifest["roles"].items()} == {
        "train": 2, "search_val": 1, "final_eval": 1}
    again = subprocess.run(cmd, capture_output=True, text=True, check=False)
    assert again.returncode == 0 and again.stdout.splitlines() == [
        f"[skip] {tmp_path / 'out'} already holds a subset (manifest.json present)"]
    named = subprocess.run(script + ["--source", str(src), "--out", str(tmp_path / "named"), "--n-train", "1",
                                     "--n-search-val", "1", "--n-final-eval", "1", "--names", "drone,bird"],
                           capture_output=True, text=True, check=False)
    assert named.returncode == 0, named.stderr
    assert json.loads((tmp_path / "named" / "manifest.json").read_text())["names"] == ["drone", "bird"]
    assert load_dataset_yaml(str(tmp_path / "named" / "data.yaml")).names == {0: "drone", 1: "bird"}
    bad = subprocess.run(script + ["--source", str(src), "--out", str(tmp_path / "o2"), "--n-train", "50"],
                         capture_output=True, text=True, check=False)
    assert bad.returncode == 1 and "not enough images" in bad.stderr
