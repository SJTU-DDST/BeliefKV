import ast
from copy import deepcopy
from pathlib import Path
import subprocess
import textwrap

import pytest

from beliefkv.experiments.prepare_ack_engine import (
    ASSIGNMENT, FILE, RETRY, prepare_ack_engine_compatibility,
    verify_prepare_ack_projection,
)


ORIGINAL = textwrap.dedent("""
    class UnifiedRadixCache:
        def _evict(self, params):
            tracker = {}
            if self.cache_controller is not None and self.cache_controller.write_policy == "write_back":
                self.writing_check(write_back=True)
            return tracker

        def unrelated(self):
            return 0
""")


def candidate(original=ORIGINAL):
    tree = ast.parse(original)
    guard = tree.body[0].body[0].body[1]
    guard.body = [deepcopy(ASSIGNMENT), *guard.body, deepcopy(RETRY)]
    return ast.unparse(ast.fix_missing_locations(tree))


def test_only_the_known_custom_guard_and_one_retry_project_to_native():
    verify_prepare_ack_projection(ORIGINAL, candidate())


@pytest.mark.parametrize("fault", ("always_on", "two_retries", "other_cache_change", "missing_retry"))
def test_unsafe_cache_changes_do_not_qualify_as_the_custom_fix(fault):
    revised = candidate()
    if fault == "always_on":
        revised = revised.replace(
            "getattr(self, '_beliefkv_incremental_full_enabled', False) is True",
            "True",
        )
    elif fault == "two_retries":
        revised = revised.replace(
            "self._evict_components(request_by_type, tracker, available_size_targets=available_size_targets)",
            "self._evict_components(request_by_type, tracker, available_size_targets=available_size_targets)\n"
            "            self._evict_components(request_by_type, tracker, available_size_targets=available_size_targets)",
        )
    elif fault == "other_cache_change":
        revised = revised.replace("return 0", "return 1")
    elif fault == "missing_retry":
        revised = ORIGINAL
    with pytest.raises(ValueError):
        verify_prepare_ack_projection(ORIGINAL, revised)


def tree(repository):
    subprocess.run(["git", "-C", str(repository), "add", "."], check=True, capture_output=True)
    return subprocess.check_output(["git", "-C", str(repository), "write-tree"], text=True).strip()


def engine_fixture(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    target = tmp_path / FILE
    target.parent.mkdir(parents=True)
    target.write_text(ORIGINAL)
    original = tree(tmp_path)
    target.write_text(candidate())
    current = tree(tmp_path)
    return {
        "engine_tree": original,
        "environment": {"AB_MODE": "off", "NATIVE_POLICY_BASELINE": "1", "PREPARE_HOST": "0"},
    }, current


def test_fixed_tree_requires_identical_other_production_files_and_native_source(tmp_path):
    source, current = engine_fixture(tmp_path)
    evidence = prepare_ack_engine_compatibility(tmp_path, source, candidate_tree=current)
    assert evidence["native_method_projection_matches"] is True
    assert evidence["max_additional_eviction_passes"] == 1
    assert evidence["source_engine_tree"] == source["engine_tree"]
    assert evidence["candidate_engine_tree"] == current
    assert evidence["gate"] == "_beliefkv_incremental_full_enabled"
    (tmp_path / "other.py").write_text("unrelated = 1\n")
    with pytest.raises(ValueError, match="outside"):
        prepare_ack_engine_compatibility(tmp_path, source, candidate_tree=tree(tmp_path))


@pytest.mark.parametrize("field,value", (
    ("AB_MODE", "predictive_h2d"), ("NATIVE_POLICY_BASELINE", "0"), ("PREPARE_HOST", "1"),
))
def test_custom_or_ambiguous_sources_cannot_use_native_projection(tmp_path, field, value):
    source, current = engine_fixture(tmp_path)
    source["environment"][field] = value
    with pytest.raises(ValueError, match="native source"):
        prepare_ack_engine_compatibility(tmp_path, source, candidate_tree=current)


def test_identical_engine_keeps_existing_strict_source_contract():
    assert prepare_ack_engine_compatibility(
        Path("/not/read"), {"engine_tree": "identical"}, candidate_tree="identical",
    ) is None
