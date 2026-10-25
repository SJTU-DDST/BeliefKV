import ast
from copy import deepcopy
import os
from pathlib import Path
import subprocess
import textwrap

import pytest

from beliefkv.experiments.prepare_ack_engine import (
    DEPENDENCY_BINDING, DEPENDENCY_BRANCH, DEPENDENCY_LOAD, DEPENDENCY_METHOD,
    FILE, RETRY, SCHEDULER_FILE, prepare_ack_engine_compatibility,
    verify_prefetch_dependency_projection, verify_prepare_ack_projection,
)


ORIGINAL = textwrap.dedent("""
    class UnifiedRadixCache:
        def _evict(self, params):
            tracker = {}
            self._evict_components(request_by_type, tracker, available_size_targets=available_size_targets)
            if self.cache_controller is not None and self.cache_controller.write_policy == "write_back":
                self.writing_check(write_back=True)
            return tracker

        def unrelated(self):
            return 0
""")


def candidate(original=ORIGINAL):
    tree = ast.parse(original)
    method = tree.body[0].body[0]
    guard = method.body[2]
    guard.body = [*guard.body, deepcopy(RETRY)]
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


def dependency_candidate():
    engine = os.environ.get("SGLANG_SOURCE_CHECKOUT")
    if not engine:
        pytest.skip("set SGLANG_SOURCE_CHECKOUT to the qualified native engine")
    native_cache = ast.parse((Path(engine) / FILE).read_text())
    cls = next(node for node in native_cache.body
               if isinstance(node, ast.ClassDef) and node.name == "UnifiedRadixCache")
    method = next(node for node in cls.body
                  if isinstance(node, ast.FunctionDef) and node.name == DEPENDENCY_METHOD)
    cache = ast.parse(candidate())
    cache.body[0].body.append(deepcopy(method))
    scheduler = ast.parse("class Scheduler:\n    def batch(self):\n        pass")
    branch = ast.If(
        test=deepcopy(DEPENDENCY_BRANCH), body=[deepcopy(DEPENDENCY_LOAD)], orelse=[],
    )
    scheduler.body[0].body[0].body = [branch]
    original_scheduler = ast.unparse(ast.fix_missing_locations(scheduler))
    branch.body.append(deepcopy(DEPENDENCY_BINDING))
    return (
        ast.unparse(ast.fix_missing_locations(cache)), original_scheduler,
        ast.unparse(ast.fix_missing_locations(scheduler)),
    )


def test_exact_custom_producer_callback_and_ack_retry_project_to_native():
    cache, original_scheduler, scheduler = dependency_candidate()
    verify_prefetch_dependency_projection(ORIGINAL, cache, original_scheduler, scheduler)


@pytest.mark.parametrize("fault", (
    "remove_producer_guard", "remove_pending_guard", "write_through", "unbounded",
    "unconditional_binding", "wrong_branch", "scheduler_change", "cache_change",
))
def test_dependency_projection_rejects_unreviewed_engine_changes(fault):
    cache, original_scheduler, scheduler = dependency_candidate()
    if fault == "remove_producer_guard":
        cache = cache.replace(
            "consumer_index != getattr(self, '_beliefkv_prefetch_consumer_index', None)",
            "False",
        )
    elif fault == "remove_pending_guard":
        cache = cache.replace("node.load_back_pending_id is not None", "False")
    elif fault == "write_through":
        cache = cache.replace("'write_back'", "'write_through'")
    elif fault == "unbounded":
        cache = cache.replace("remaining == 0", "False")
    elif fault == "unconditional_binding":
        scheduler = scheduler.replace("if callable(", "if bool(")
    elif fault == "wrong_branch":
        original_scheduler = original_scheduler.replace(
            "self.enable_hierarchical_cache", "self.unrelated",
        )
        scheduler = scheduler.replace("self.enable_hierarchical_cache", "self.unrelated")
    elif fault == "scheduler_change":
        scheduler += "\nvalue = 1"
    elif fault == "cache_change":
        cache = cache.replace("return 0", "return 1")
    with pytest.raises(ValueError):
        verify_prefetch_dependency_projection(ORIGINAL, cache, original_scheduler, scheduler)


def test_dependency_compatibility_retains_native_capture_environment_gate(tmp_path):
    cache, original_scheduler, scheduler = dependency_candidate()
    source, _current = engine_fixture(tmp_path)
    target = tmp_path / SCHEDULER_FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(original_scheduler)
    (tmp_path / FILE).write_text(ORIGINAL)
    source["engine_tree"] = tree(tmp_path)
    target.write_text(scheduler)
    (tmp_path / FILE).write_text(cache)
    current = tree(tmp_path)
    evidence = prepare_ack_engine_compatibility(tmp_path, source, candidate_tree=current)
    assert evidence["policy"] == "custom_prepare_ack_retry_and_batch_dependency"
    assert evidence["dependency_requires_full_only_write_back"] is True
    assert evidence["native_without_custom_producer_keeps_original_consumer"] is True
    source["environment"]["NATIVE_POLICY_BASELINE"] = "0"
    with pytest.raises(ValueError, match="native source"):
        prepare_ack_engine_compatibility(tmp_path, source, candidate_tree=current)
