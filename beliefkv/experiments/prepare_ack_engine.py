"""Verify the single gated PREPARE ACK fix against an immutable native source."""

import ast
import hashlib
from pathlib import Path
import subprocess

FILE = "python/sglang/srt/mem_cache/unified_radix_cache.py"
ASSIGNMENT = ast.parse(
    "beliefkv_retry_after_write = bool("
    "getattr(self, '_beliefkv_incremental_full_enabled', False) is True"
    " and self.ongoing_write_through)"
).body[0]
RETRY = ast.parse(
    "if beliefkv_retry_after_write:\n"
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
    before, after = ast.parse(original), ast.parse(candidate)

    def guard(module):
        cache = next(node for node in module.body
                     if isinstance(node, ast.ClassDef) and node.name == "UnifiedRadixCache")
        method = next(node for node in cache.body
                      if isinstance(node, ast.FunctionDef) and node.name == "_evict")
        matches = [node for node in method.body
                   if isinstance(node, ast.If) and shape(node.test) == shape(WRITE_BACK)]
        if len(matches) != 1:
            raise ValueError("expected one native write-back completion guard")
        return matches[0]

    try:
        old, new = guard(before), guard(after)
        sync = ast.parse("self.writing_check(write_back=True)").body[0]
        if (
            old.orelse or new.orelse or len(old.body) != 1
            or shape(old.body[0]) != shape(sync)
            or len(new.body) != 3
            or [shape(node) for node in new.body]
            != [shape(ASSIGNMENT), shape(sync), shape(RETRY)]
        ):
            raise ValueError("candidate is not the bounded custom PREPARE ACK retry")
        new.body = [new.body[1]]
        if shape(before) != shape(after):
            raise ValueError("engine behavior changed outside the gated PREPARE ACK retry")
    except (StopIteration, SyntaxError) as error:
        raise ValueError("engine does not contain the expected native cache") from error


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
    if changed != [FILE]:
        raise ValueError("engine tree changed outside the gated PREPARE ACK cache file")
    original = git("show", original_tree + ":" + FILE)
    candidate = git("show", candidate_tree + ":" + FILE)
    verify_prepare_ack_projection(original, candidate)
    return {
        "policy": "custom_prepare_ack_retry_only",
        "source_engine_tree": original_tree, "candidate_engine_tree": candidate_tree,
        "changed_file": FILE,
        "source_file_sha256": hashlib.sha256(original.encode()).hexdigest(),
        "candidate_file_sha256": hashlib.sha256(candidate.encode()).hexdigest(),
        "native_method_projection_matches": True,
        "gate": "_beliefkv_incremental_full_enabled",
        "max_additional_eviction_passes": 1,
        "source_custom_runtime_enabled": False,
        "scope": (
            "The only production delta is a custom-incremental-prepare-gated retry "
            "after the existing D2H completion fence. Native capture has that "
            "runtime disabled. All other production files and cache AST are identical."
        ),
    }
