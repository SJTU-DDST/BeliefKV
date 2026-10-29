"""Reviewed optional Agentix hooks over the common paper-baseline engine."""

import ast
from pathlib import Path
import subprocess

from beliefkv.experiments.native_engine_variant import COMMON_TREE
from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.experiments.tokencake_engine import method, replace_once, shape


LEGACY_TREE = "c489dbb85265efce25622a52abfd698e9199688d"
SCHEDULER = "python/sglang/srt/managers/scheduler.py"
BATCH = "python/sglang/srt/managers/schedule_batch.py"
NATIVE_SOURCE_SHA256 = {
    SCHEDULER: "db5e2c21f9620ce9bdfe4cb426f8f265d5c5fdeb4f5d13da0bba0b451d9e87fe",
    BATCH: "190b363e8483ebe71e93385576d34b05ab042922af732804bbcbfb73d60cc695",
}
ATTACH = """
runtime.attach_native_cache(self.tree_cache)
runtime.attach_native_scheduler(self)
"""
ATTACH_NATIVE = "runtime.attach_native_cache(self.tree_cache)"
RETRACT = """
retracted_reqs, new_token_ratio, reqs_to_abort = batch.retract_decode(
    before_release=getattr(runtime, "before_native_request_release", None),
)
"""
RETRACT_NATIVE = """
retracted_reqs, new_token_ratio, reqs_to_abort = batch.retract_decode()
"""
VICTIM = """
idx = sorted_indices.pop()
req = self.reqs[idx]
if before_release is not None:
    before_release(req, reason="decode_retraction")
"""
VICTIM_NATIVE = """
idx = sorted_indices.pop()
req = self.reqs[idx]
"""
ABORT = """
reqs_to_abort.append(last_req)
if before_release is not None:
    before_release(last_req, reason="decode_oom_abort")
"""
ABORT_NATIVE = "reqs_to_abort.append(last_req)"


def project_scheduler(original, candidate):
    before, after = ast.parse(original), ast.parse(candidate)
    try:
        replace_once(method(after, "Scheduler", "init_beliefkv_runtime"), ATTACH, ATTACH_NATIVE)
        replace_once(method(after, "Scheduler", "update_running_batch"), RETRACT, RETRACT_NATIVE)
    except (StopIteration, ValueError) as error:
        raise ValueError("Agentix scheduler differs from its reviewed optional hooks") from error
    if shape(before) != shape(after):
        raise ValueError("scheduler changed outside Agentix's optional bindings")
    return ast.unparse(after)


def project_batch(original, candidate):
    before, after = ast.parse(original), ast.parse(candidate)
    try:
        target = method(after, "ScheduleBatch", "retract_decode")
        if (
            [argument.arg for argument in target.args.kwonlyargs] != ["before_release"]
            or [shape(value) for value in target.args.kw_defaults] != [shape(ast.Constant(None))]
        ):
            raise ValueError("Agentix retraction check must default to the native path")
        target.args.kwonlyargs, target.args.kw_defaults = [], []
        replace_once(target, VICTIM, VICTIM_NATIVE)
        replace_once(target, ABORT, ABORT_NATIVE)
    except (StopIteration, ValueError) as error:
        raise ValueError("Agentix batch differs from its reviewed pre-release checks") from error
    if shape(before) != shape(after):
        raise ValueError("batch changed outside Agentix's optional pre-release checks")
    return ast.unparse(after)


def verify_native_hooks(engine):
    engine = Path(engine)
    changed = subprocess.check_output(
        ["git", "diff", "--name-only", COMMON_TREE, "--"], cwd=engine, text=True,
    ).splitlines()
    if changed != sorted(NATIVE_SOURCE_SHA256):
        raise ValueError("Agentix common engine changes files outside its two optional bindings")
    sources = {}
    for name, projection in ((SCHEDULER, project_scheduler), (BATCH, project_batch)):
        original = subprocess.check_output(
            ["git", "show", COMMON_TREE + ":" + name], cwd=engine, text=True,
        )
        path = engine / name
        projection(original, path.read_text())
        sources[name] = sha256_file(path)
    if sources != NATIVE_SOURCE_SHA256:
        raise ValueError("Agentix native hook source bytes changed")
    return {
        "common_engine_tree": COMMON_TREE,
        "exact_optional_hooks_projection_verified": True,
        "constructor_binds_scheduler_after_cache": True,
        "uncheckpointed_retraction_checked_before_release": True,
        "native_compute_copy_cache_and_sampler_changed": False,
        "native_victim_order_changed": False,
        "source_sha256": sources,
    }


def verify_qualification_engine(engine, tree):
    from beliefkv.experiments.native_engine_variant import AGENTIX_TREE

    if tree == LEGACY_TREE:
        return {"legacy_qualification_engine": True, "common_engine_equivalence_claimed": False}
    if tree != AGENTIX_TREE:
        raise ValueError("Agentix qualification requires a reviewed explicit native tree")
    return verify_native_hooks(engine)
