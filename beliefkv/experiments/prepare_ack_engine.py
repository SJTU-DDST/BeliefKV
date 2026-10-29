"""Verify bounded custom-runtime engine fixes against a native capture."""

import ast
import hashlib
from pathlib import Path
import subprocess

FILE = "python/sglang/srt/mem_cache/unified_radix_cache.py"
SCHEDULER_FILE = "python/sglang/srt/managers/scheduler.py"
DEPENDENCY_TEST = "test/srt/test_beliefkv_prefetch_batch_dependency.py"
PAPER_PRESSURE_TEST = "test/srt/test_beliefkv_paper_decode_pressure.py"
PAPER_PRESSURE_PREFIX = ast.parse("""
kv_full_retract_flag = not batch.check_decode_mem()
runtime = getattr(self, "beliefkv_runtime", None)
release_pin = getattr(runtime, "on_native_decode_capacity_pressure", None)
while kv_full_retract_flag and release_pin is not None and release_pin(batch) is True:
    kv_full_retract_flag = not batch.check_decode_mem()
""").body
PAPER_PRESSURE_NATIVE_TEST = ast.parse("""
(kv_full_retract_flag := not batch.check_decode_mem()) or (
    TEST_RETRACT and self.forward_ct % TEST_RETRACT_INTERVAL == 0
)
""").body[0].value
PAPER_PRESSURE_NEW_TEST = ast.parse("""
kv_full_retract_flag or (
    TEST_RETRACT and self.forward_ct % TEST_RETRACT_INTERVAL == 0
)
""").body[0].value
DEPENDENCY_METHOD = "beliefkv_prefill_load_consumer"
# Exact AST of the reviewed FULL-only, write-back, bounded prefix check.
DEPENDENCY_METHOD_SHA256 = "869160ccdf8d2f3f52bd8c7030c91aa8caf937a3b484d3f157f984210b8556b6"
DEPENDENCY_BINDING = ast.parse("""
if callable(
    resolve_consumer := getattr(self.tree_cache, "beliefkv_prefill_load_consumer", None)
):
    new_batch.hicache_consumer_index = resolve_consumer(
        getattr(new_batch, "reqs", ()), new_batch.hicache_consumer_index
    )
""").body[0]
DEPENDENCY_LOAD = ast.parse(
    "new_batch.hicache_consumer_index = self.tree_cache.ready_to_load_host_cache()"
).body[0]
DEPENDENCY_BRANCH = ast.parse(
    "self.enable_hierarchical_cache or self.enable_unified_cache_external_linker"
).body[0].value
RETRY = ast.parse(
    "if (getattr(self, '_beliefkv_incremental_full_enabled', False) is True"
    " and any(tracker[ct] < request_by_type[ct] for ct in self.tree_components)):\n"
    "    self._evict_components(request_by_type, tracker,"
    " available_size_targets=available_size_targets)"
).body[0]
WRITE_BACK = ast.parse(
    "self.cache_controller is not None and"
    " self.cache_controller.write_policy == 'write_back'"
).body[0].value


def shape(node):
    return ast.dump(node, include_attributes=False)


def verify_prepare_ack_projection(original: str, candidate: str) -> None:
    try:
        before, after = ast.parse(original), ast.parse(candidate)
    except SyntaxError as error:
        raise ValueError("engine cache source is not valid Python") from error

    def guard(module):
        cache = next(node for node in module.body
                     if isinstance(node, ast.ClassDef) and node.name == "UnifiedRadixCache")
        method = next(node for node in cache.body
                      if isinstance(node, ast.FunctionDef) and node.name == "_evict")
        matches = [node for node in method.body
                   if isinstance(node, ast.If) and shape(node.test) == shape(WRITE_BACK)]
        if len(matches) != 1:
            raise ValueError("expected one native write-back completion guard")
        return method, matches[0]

    try:
        _old_method, old = guard(before)
        _new_method, new = guard(after)
        sync = ast.parse("self.writing_check(write_back=True)").body[0]
        if (
            old.orelse or new.orelse or len(old.body) != 1
            or shape(old.body[0]) != shape(sync)
            or len(new.body) != 2
            or [shape(node) for node in new.body]
            != [shape(sync), shape(RETRY)]
        ):
            raise ValueError("candidate is not the bounded custom PREPARE ACK retry")
        new.body = [new.body[0]]
        if shape(before) != shape(after):
            raise ValueError("engine behavior changed outside the gated PREPARE ACK retry")
    except (StopIteration, SyntaxError) as error:
        raise ValueError("engine does not contain the expected native cache") from error


def verify_prefetch_dependency_projection(
    original_cache: str, candidate_cache: str,
    original_scheduler: str, candidate_scheduler: str,
) -> str:
    """Remove only the exact custom producer guard, then verify the ACK delta."""
    try:
        before_cache, after_cache, before_scheduler, after_scheduler = map(
            ast.parse,
            (original_cache, candidate_cache, original_scheduler, candidate_scheduler),
        )
        classes = [
            next(node for node in module.body
                 if isinstance(node, ast.ClassDef) and node.name == "UnifiedRadixCache")
            for module in (before_cache, after_cache)
        ]
        methods = [
            [node for node in cls.body
             if isinstance(node, ast.FunctionDef) and node.name == DEPENDENCY_METHOD]
            for cls in classes
        ]
        if methods[0] or len(methods[1]) != 1:
            raise ValueError("expected one new custom prefetch dependency method")
        method = methods[1][0]
        if hashlib.sha256(shape(method).encode()).hexdigest() != DEPENDENCY_METHOD_SHA256:
            raise ValueError("custom prefetch dependency guard differs from reviewed AST")
        classes[1].body.remove(method)

        scheduler = next(
            node for node in after_scheduler.body
            if isinstance(node, ast.ClassDef) and node.name == "Scheduler"
        )
        matches = [
            node for node in ast.walk(scheduler)
            if isinstance(node, ast.If)
            and shape(node) == shape(DEPENDENCY_BINDING)
        ]
        if len(matches) != 1:
            raise ValueError("expected one guarded batch dependency binding")
        binding = matches[0]
        parents = [
            node for node in ast.walk(scheduler)
            if isinstance(node, ast.If) and binding in node.body
        ]
        if (
            len(parents) != 1 or parents[0].orelse
            or shape(parents[0].test) != shape(DEPENDENCY_BRANCH)
            or len(parents[0].body) != 2
            or shape(parents[0].body[0]) != shape(DEPENDENCY_LOAD)
            or parents[0].body[1] is not binding
        ):
            raise ValueError("custom dependency binding is outside native HiCache branch")
        parents[0].body.remove(binding)
        if shape(before_scheduler) != shape(after_scheduler):
            raise ValueError("scheduler changed outside the guarded dependency binding")
        projected_cache = ast.unparse(after_cache)
        verify_prepare_ack_projection(original_cache, projected_cache)
        return projected_cache
    except (StopIteration, SyntaxError) as error:
        raise ValueError("engine does not contain the expected native dependency path") from error


def project_paper_pressure_hook(candidate: str) -> str:
    """Remove only the reviewed optional-pin hook before native OOM retraction."""
    try:
        module = ast.parse(candidate)
        scheduler = next(node for node in module.body
                         if isinstance(node, ast.ClassDef) and node.name == "Scheduler")
        method = next(node for node in scheduler.body
                      if isinstance(node, ast.FunctionDef) and node.name == "update_running_batch")
        count = len(PAPER_PRESSURE_PREFIX)
        matches = [
            index for index in range(len(method.body) - count)
            if [shape(node) for node in method.body[index:index + count]]
            == [shape(node) for node in PAPER_PRESSURE_PREFIX]
        ]
        if len(matches) != 1:
            raise ValueError("paper pressure callback differs from reviewed native hook")
        index = matches[0]
        guard = method.body[index + count]
        if not isinstance(guard, ast.If) or shape(guard.test) != shape(PAPER_PRESSURE_NEW_TEST):
            raise ValueError("paper pressure callback is not immediately before native retraction")
        del method.body[index:index + count]
        guard.test = PAPER_PRESSURE_NATIVE_TEST
        return ast.unparse(module)
    except (StopIteration, SyntaxError) as error:
        raise ValueError("engine lacks the expected native decode pressure path") from error


def prepare_ack_engine_compatibility(
    engine: Path, source: dict, *, candidate_tree: str,
) -> dict | None:
    original_tree = source["engine_tree"]
    if original_tree == candidate_tree:
        return None
    environment = source.get("environment", {})
    if any(environment.get(name) != value for name, value in (
        ("AB_MODE", "off"), ("NATIVE_POLICY_BASELINE", "1"), ("PREPARE_HOST", "0"),
    )):
        raise ValueError("gated fix requires an unmodified native source capture")

    def git(*arguments):
        return subprocess.check_output(
            ["git", "-C", str(engine), *arguments], text=True,
        )

    changed = git("diff", "--name-only", original_tree, candidate_tree).splitlines()
    from beliefkv.experiments.native_engine_variant import (
        COMMON_TREE, LAYER_TREE, GROUPED_LAYER_TREE, SIBLING_CAPACITY_TREE,
        DEMAND_PREFILL_PIPELINE_TREE, TOKENCAKE_TREE, AGENTIX_TREE,
    )
    if candidate_tree == AGENTIX_TREE:
        from beliefkv.experiments.agentix_engine import verify_native_hooks

        hooks = verify_native_hooks(engine)
        if git("diff", "--name-only", COMMON_TREE, candidate_tree).splitlines() != sorted((
            SCHEDULER_FILE, "python/sglang/srt/managers/schedule_batch.py",
        )):
            raise ValueError("Agentix engine changes files outside its optional native bindings")
        return {
            "policy": "explicit_agentix_common_native_hook_variant",
            "source_engine_tree": original_tree, "candidate_engine_tree": candidate_tree,
            "common_engine_tree": COMMON_TREE,
            "common_engine_compatibility": prepare_ack_engine_compatibility(
                engine, source, candidate_tree=COMMON_TREE,
            ),
            "changed_files": changed, "native_scheduler_bindings": hooks,
            "requires_fresh_gpu_components_model_and_position_smoke": True,
            "scope": "Optional scheduler bindings only; compute, copy, cache and sampler equal common native.",
        }
    if candidate_tree == TOKENCAKE_TREE:
        from beliefkv.experiments.tokencake_engine import verify_native_hooks

        hooks = verify_native_hooks(engine)
        if git("diff", "--name-only", COMMON_TREE, candidate_tree).splitlines() != sorted((
            SCHEDULER_FILE, "python/sglang/srt/managers/schedule_batch.py",
        )):
            raise ValueError("TokenCake engine changes files outside its scheduler hooks")
        return {
            "policy": "explicit_tokencake_native_scheduler_hook_variant",
            "source_engine_tree": original_tree, "candidate_engine_tree": candidate_tree,
            "common_engine_tree": COMMON_TREE,
            "common_engine_compatibility": prepare_ack_engine_compatibility(
                engine, source, candidate_tree=COMMON_TREE,
            ),
            "changed_files": changed, "native_scheduler_bindings": hooks,
            "requires_fresh_gpu_components_model_and_position_smoke": True,
            "scope": "Optional policy callbacks only; compute and transfer code equal common native.",
        }
    if candidate_tree in (
        LAYER_TREE, GROUPED_LAYER_TREE, SIBLING_CAPACITY_TREE, DEMAND_PREFILL_PIPELINE_TREE,
    ):
        common = prepare_ack_engine_compatibility(
            engine, source, candidate_tree=COMMON_TREE,
        )
        if candidate_tree == DEMAND_PREFILL_PIPELINE_TREE:
            from beliefkv.experiments.demand_prefill_pipeline_engine import project_disabled_scheduler

            project_disabled_scheduler(
                git("show", SIBLING_CAPACITY_TREE + ":" + SCHEDULER_FILE),
                git("show", candidate_tree + ":" + SCHEDULER_FILE),
            )
        return {
            "policy": (
                "explicit_native_demand_pipeline_engine_variant"
                if candidate_tree == DEMAND_PREFILL_PIPELINE_TREE else
                "explicit_sibling_capacity_engine_variant" if candidate_tree == SIBLING_CAPACITY_TREE else
                "explicit_grouped_layer_handoff_engine_variant" if candidate_tree == GROUPED_LAYER_TREE
                else "explicit_layer_handoff_engine_variant"
            ),
            "source_engine_tree": original_tree, "candidate_engine_tree": candidate_tree,
            "common_engine_tree": COMMON_TREE, "common_engine_compatibility": common,
            "changed_files": changed,
            "binding": "exact_reviewed_git_tree_not_arbitrary_native_changes",
            "layer_handoff_default_enabled": False,
            **(
                {"demand_prefill_pipeline_default_enabled": False}
                if candidate_tree == DEMAND_PREFILL_PIPELINE_TREE else {}
            ),
            "requires_fresh_gpu_components_model_and_position_smoke": True,
            "scope": "Engine identity only; GPU proofs and performance remain separate.",
        }
    from beliefkv.experiments.free_page_h2d_fence import (
        FILES as FENCE_FILES, verify_free_page_fence_projection,
    )
    if set(changed) == FENCE_FILES:
        if environment.get("BELIEFKV_FREE_PAGE_H2D_FENCE", environment.get("FREE_PAGE_H2D_FENCE", "0")) != "0":
            raise ValueError("free-page fence source capture must retain its original fence")
        result = verify_free_page_fence_projection(
            {name: git("show", original_tree + ":" + name) for name in FENCE_FILES},
            {name: git("show", candidate_tree + ":" + name) for name in FENCE_FILES},
        )
        return {
            **result, "policy": "opt_in_preexisting_free_page_h2d_fence",
            "source_engine_tree": original_tree, "candidate_engine_tree": candidate_tree,
            "changed_files": changed,
            "scope": (
                "Exact reviewed AST projection, including original default-off load fences. "
                "Native and controlled loads use the same proof when enabled. "
                "Added allocator generations grant no capacity or eviction authority. "
                "CPU projection is not GPU copy safety or numerical equivalence."
            ),
        }
    dependency_fix = SCHEDULER_FILE in changed
    paper_pressure = PAPER_PRESSURE_TEST in changed
    allowed = {FILE, SCHEDULER_FILE, DEPENDENCY_TEST} if dependency_fix else {FILE}
    if paper_pressure:
        allowed.add(PAPER_PRESSURE_TEST)
    if FILE not in changed or not set(changed) <= allowed:
        raise ValueError("engine tree changed outside the gated PREPARE ACK cache file")
    original = git("show", original_tree + ":" + FILE)
    candidate = git("show", candidate_tree + ":" + FILE)
    if dependency_fix:
        scheduler = git("show", candidate_tree + ":" + SCHEDULER_FILE)
        if paper_pressure:
            scheduler = project_paper_pressure_hook(scheduler)
        verify_prefetch_dependency_projection(
            original, candidate,
            git("show", original_tree + ":" + SCHEDULER_FILE),
            scheduler,
        )
    else:
        verify_prepare_ack_projection(original, candidate)
    return {
        "policy": (
            "custom_prepare_ack_batch_dependency_and_paper_pressure"
            if paper_pressure else
            "custom_prepare_ack_retry_and_batch_dependency"
            if dependency_fix else "custom_prepare_ack_retry_only"
        ),
        "source_engine_tree": original_tree, "candidate_engine_tree": candidate_tree,
        "changed_file": FILE,
        "source_file_sha256": hashlib.sha256(original.encode()).hexdigest(),
        "candidate_file_sha256": hashlib.sha256(candidate.encode()).hexdigest(),
        "native_method_projection_matches": True,
        "gate": "_beliefkv_incremental_full_enabled",
        "max_additional_eviction_passes": 1,
        "source_custom_runtime_enabled": False,
        **({
            "dependency_gate": "_beliefkv_prefetch_consumer_index",
            "dependency_method_ast_sha256": DEPENDENCY_METHOD_SHA256,
            "dependency_max_ancestor_checks": 256,
            "dependency_requires_full_only_write_back": True,
            "native_without_custom_producer_keeps_original_consumer": True,
            "changed_files": changed,
        } if dependency_fix else {}),
        **({
            "paper_decode_pressure_callback": "on_native_decode_capacity_pressure",
            "paper_decode_retraction_remains_native": True,
            "source_has_no_paper_runtime": True,
        } if paper_pressure else {}),
        "scope": ((
            "The reviewed batch callback is active only for a custom speculative "
            "producer. Native capture has none. Exact scheduler AST projection "
            "and the bounded PREPARE ACK projection both match the source."
            + (" The optional paper-pin hook is also exactly projected; native "
               "capture has no callback and keeps its original pressure check."
               if paper_pressure else "")
        ) if dependency_fix else (
            "The only production delta is a custom-incremental-prepare-gated retry "
            "after the existing D2H completion fence. Native capture has that "
            "runtime disabled. All other production files and cache AST are identical."
        )),
    }
