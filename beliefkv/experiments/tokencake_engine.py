"""Exact optional TokenCake scheduler bindings over the common native engine."""

import ast
from pathlib import Path
import subprocess

from beliefkv.experiments.native_engine_variant import COMMON_TREE
from beliefkv.experiments.source_provenance import sha256_file


SCHEDULER = "python/sglang/srt/managers/scheduler.py"
BATCH = "python/sglang/srt/managers/schedule_batch.py"
NATIVE_SOURCE_SHA256 = {
    SCHEDULER: "0ceab8b945d0ea3163de13bc949404ab5a4fb61e412e1ac4fd10b276e467aefe",
    BATCH: "38790e4f56d95c84f2d070ab5073bc6fe163d187c711f65c1e818e1bb3c6fd5c",
}
GUARD = """
capacity_guard = getattr(self.beliefkv_runtime, "allow_native_prefill_candidate", None)
if capacity_guard is not None and not capacity_guard(
    req, running_batch=running_batch, adder=adder
):
    continue
"""
RETRACT_CALL = """
retracted_reqs, new_token_ratio, reqs_to_abort = batch.retract_decode(
    select_victim=getattr(runtime, "select_native_decode_victim", None),
    before_release=getattr(runtime, "before_native_request_release", None),
)
"""
RETRACT_NATIVE = """
retracted_reqs, new_token_ratio, reqs_to_abort = batch.retract_decode()
"""
VICTIM = """
selected = select_victim(self, tuple(sorted_indices)) if select_victim is not None else None
if selected is None:
    idx = sorted_indices.pop()
else:
    if type(selected) is not int or selected not in sorted_indices:
        raise ValueError("decode victim is outside the native running batch")
    sorted_indices.remove(selected)
    idx = selected
req = self.reqs[idx]
if before_release is not None:
    before_release(req, reason="decode_retraction")
"""
VICTIM_NATIVE = """
idx = sorted_indices.pop()
req = self.reqs[idx]
"""
ABORT = """
if before_release is not None:
    before_release(last_req, reason="decode_oom_abort")
"""


def shape(node):
    return ast.dump(node, include_attributes=False)


def method(module, class_name, name):
    cls = next(
        node for node in module.body if isinstance(node, ast.ClassDef) and node.name == class_name
    )
    return next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == name)


def replace_once(node, expected, replacement):
    before, after = ast.parse(expected).body, ast.parse(replacement).body
    matches = []
    for parent in ast.walk(node):
        for _, value in ast.iter_fields(parent):
            if isinstance(value, list):
                for index in range(len(value) - len(before) + 1):
                    if all(
                        isinstance(observed, ast.AST) and shape(observed) == shape(wanted)
                        for observed, wanted in zip(value[index:index + len(before)], before)
                    ):
                        matches.append((value, index))
    if len(matches) != 1:
        raise ValueError("TokenCake native hook differs from its exact reviewed binding")
    value, index = matches[0]
    value[index:index + len(before)] = after


def project_scheduler(original, candidate):
    before, after = ast.parse(original), ast.parse(candidate)
    prefill = method(after, "Scheduler", "_get_new_batch_prefill_raw")
    loop = next(
        node for node in ast.walk(prefill) if isinstance(node, ast.For)
        and isinstance(node.iter, ast.Call) and isinstance(node.iter.func, ast.Attribute)
        and node.iter.func.attr == "_beliefkv_prefill_candidates"
    )
    guard = ast.parse(GUARD).body
    positions = [
        index for index in range(len(loop.body) - 2)
        if [shape(node) for node in loop.body[index:index + 2]] == [shape(node) for node in guard]
    ]
    if len(positions) != 1 or shape(loop.body[positions[0] + 2]) != shape(
        ast.parse("req.init_next_round_input(self.tree_cache)").body[0]
    ):
        raise ValueError("TokenCake capacity guard must immediately precede native input initialization")
    replace_once(prefill, GUARD, "")
    replace_once(method(after, "Scheduler", "update_running_batch"), RETRACT_CALL, RETRACT_NATIVE)
    if shape(before) != shape(after):
        raise ValueError("scheduler changed outside TokenCake's optional native hooks")
    return ast.unparse(after)


def project_batch(original, candidate):
    before, after = ast.parse(original), ast.parse(candidate)
    target = method(after, "ScheduleBatch", "retract_decode")
    if (
        [arg.arg for arg in target.args.kwonlyargs] != ["select_victim", "before_release"]
        or [shape(node) for node in target.args.kw_defaults]
        != [shape(ast.Constant(None)), shape(ast.Constant(None))]
    ):
        raise ValueError("TokenCake decode hooks must default to native victim selection")
    target.args.kwonlyargs, target.args.kw_defaults = [], []
    replace_once(target, VICTIM, VICTIM_NATIVE)
    replace_once(target, ABORT, "")
    if shape(before) != shape(after):
        raise ValueError("native batch changed outside TokenCake's optional retraction hooks")
    return ast.unparse(after)


def verify_native_hooks(engine):
    engine = Path(engine)
    sources = {}
    for name, project in ((SCHEDULER, project_scheduler), (BATCH, project_batch)):
        original = subprocess.check_output(
            ["git", "show", COMMON_TREE + ":" + name], cwd=engine, text=True,
        )
        path = engine / name
        project(original, path.read_text())
        sources[name] = sha256_file(path)
    if sources != NATIVE_SOURCE_SHA256:
        raise ValueError("TokenCake native hook source bytes changed")
    return {
        "common_engine_tree": COMMON_TREE, "exact_optional_hooks_projection_verified": True,
        "prefill_guard_before_native_input": True,
        "decode_victim_and_before_release_bound": True,
        "native_compute_or_copy_implementation_changed": False, "source_sha256": sources,
    }
