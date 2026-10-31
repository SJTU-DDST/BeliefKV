import os
from pathlib import Path
import shutil
import subprocess

import pytest

from beliefkv.experiments.native_engine_variant import COMMON_TREE, FREE_EXTENT_TREE, engine_patch
from scripts import run_causal_source_capture as capture


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def native_base(tmp_path):
    repository = Path(os.environ.get(
        "SGLANG_SOURCE_CHECKOUT", ROOT / "third_party/sglang-v0.5.20",
    )).resolve()
    base = subprocess.check_output(
        ["git", "-C", str(repository), "rev-parse", "HEAD"], text=True,
    ).strip()
    engine = tmp_path / "native"
    subprocess.run(
        ["git", "-C", str(repository), "worktree", "add", "--detach", str(engine), base],
        check=True, capture_output=True,
    )
    try:
        yield engine
    finally:
        subprocess.run(
            ["git", "-C", str(repository), "worktree", "remove", "--force", str(engine)],
            check=True, capture_output=True,
        )


@pytest.mark.parametrize("expected", (COMMON_TREE, FREE_EXTENT_TREE))
def test_capture_freezes_the_actual_reviewed_native_tree(native_base, expected):
    observed = capture.freeze_native_engine(ROOT, native_base, expected)
    assert observed["matches_worktree"]
    assert observed["actual_tree"] == expected
    assert Path(observed["path"]) == engine_patch(ROOT, expected)


def test_legacy_common_patch_cannot_qualify_the_free_extent_tree(native_base, monkeypatch):
    common = engine_patch(ROOT, COMMON_TREE)
    monkeypatch.setattr(capture, "engine_patch", lambda _root, _expected: common)
    with pytest.raises(ValueError, match="differs from the tested"):
        capture.freeze_native_engine(ROOT, native_base, FREE_EXTENT_TREE)
    observed = capture.git_source_state(native_base, canonical_patch=common)["canonical_patch"]
    assert observed["matches_worktree"] and observed["actual_tree"] == COMMON_TREE


def test_freeze_refuses_a_tree_that_does_not_match_the_recorded_evidence(native_base):
    with pytest.raises(ValueError, match="differs from the tested"):
        capture.freeze_native_engine(ROOT, native_base, "0" * 40)


def test_modified_variant_patch_is_rejected_before_git_apply(native_base, tmp_path):
    checkout = tmp_path / "operator"
    patch = checkout / "patches/sglang-layer-free-extent-runtime-20261029.patch"
    patch.parent.mkdir(parents=True)
    shutil.copyfile(engine_patch(ROOT, FREE_EXTENT_TREE), patch)
    patch.write_bytes(patch.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="differs from the reviewed"):
        capture.freeze_native_engine(checkout, native_base, FREE_EXTENT_TREE)
    assert subprocess.check_output(
        ["git", "-C", str(native_base), "status", "--porcelain"], text=True,
    ) == ""
