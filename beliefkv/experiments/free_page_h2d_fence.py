"""Verify the reviewed opt-in free-page H2D fence against a captured engine."""

import ast
import hashlib


CONTROLLER = "python/sglang/srt/managers/cache_controller.py"
ALLOCATOR = "python/sglang/srt/mem_cache/allocator/token.py"
SCHEDULER = "python/sglang/srt/managers/scheduler.py"
HYBRID = "python/sglang/srt/mem_cache/hybrid_cache/hybrid_cache_controller.py"
FIXTURE = "test/srt/test_beliefkv_transfer_receipts.py"
FILES = frozenset((CONTROLLER, ALLOCATOR, SCHEDULER, HYBRID, FIXTURE))
METHODS = {
    "beliefkv_record_free_page_boundary": "4b149e63f9384c1bf9f650f9040fd2034b0daf8201e999c7ba9d53299c1d3cad",
    "_beliefkv_wait_load_fence": "7c9bd93663f4185c4026c096e91da561c2af703e956f7436ce71dd0fcb18699b",
    "_beliefkv_bind_free_page_fence": "894e7b4fdc32cfb2b6fcbf4d248240adb9498ace6b0d6749e704c2a9e5244ce8",
}


def shape(node):
    return ast.dump(node, include_attributes=False)


def method(module, cls, name):
    owner = next(node for node in module.body if isinstance(node, ast.ClassDef) and node.name == cls)
    return next(node for node in owner.body if isinstance(node, ast.FunctionDef) and node.name == name)


def replace_once(root, source, replacement=""):
    expected = ast.parse(source).body
    substitute = ast.parse(replacement).body
    found = 0
    for node in ast.walk(root):
        for value in vars(node).values():
            if not isinstance(value, list):
                continue
            for index in range(len(value) - len(expected) + 1):
                selected = value[index:index + len(expected)]
                if all(isinstance(item, ast.AST) for item in selected) and (
                    [shape(item) for item in selected] == [shape(item) for item in expected]
                ):
                    value[index:index + len(expected)] = substitute
                    found += 1
                    break
    if found != 1:
        raise ValueError("free-page fence projection has a missing, duplicate or changed statement")


def verify_free_page_fence_projection(original, candidate):
    if set(original) != FILES or set(candidate) != FILES:
        raise ValueError("free-page fence projection requires the exact reviewed file set")
    try:
        before = {name: ast.parse(text) for name, text in original.items()}
        after = {name: ast.parse(text) for name, text in candidate.items()}
        allocator = after[ALLOCATOR]
        replace_once(method(allocator, "TokenToKVPoolAllocator", "clear"), """
self.beliefkv_free_generation = getattr(self, "beliefkv_free_generation", -1) + 1
""")
        replace_once(method(allocator, "TokenToKVPoolAllocator", "free"), """
self.beliefkv_free_generation += 1
""")
        controller = after[CONTROLLER]
        replace_once(controller, "import os")
        replace_once(method(controller, "HiCacheController", "__init__"), """
from sglang.srt.mem_cache.allocator.token import TokenToKVPoolAllocator
self.beliefkv_free_page_fence_enabled = (
    os.environ.get("BELIEFKV_FREE_PAGE_H2D_FENCE", "0") == "1"
    and type(token_to_kv_pool_allocator) is TokenToKVPoolAllocator
)
self._beliefkv_free_page_boundary = None
self.beliefkv_h2d_fence_counts = {}
""")
        owner = next(node for node in controller.body if isinstance(node, ast.ClassDef) and node.name == "HiCacheController")
        for name, digest in METHODS.items():
            definition = method(controller, "HiCacheController", name)
            if hashlib.sha256(shape(definition).encode()).hexdigest() != digest:
                raise ValueError("free-page fence method differs from the reviewed implementation")
            owner.body.remove(definition)
        replace_once(method(controller, "HiCacheController", "load"), """
self._beliefkv_bind_free_page_fence(op)
""")
        replace_once(method(controller, "HiCacheController", "start_loading"), """
self._beliefkv_wait_load_fence(op)
""", """
if self.load_fence_stream is not None:
    self.l2_transfer_engine.host_to_device_stream.wait_stream(self.load_fence_stream)
""")
        replace_once(method(after[HYBRID], "HybridCacheController", "load"), """
self._beliefkv_bind_free_page_fence(op)
""")
        scheduler = after[SCHEDULER]
        replace_once(method(scheduler, "Scheduler", "__init__"), """
if (
    cache_controller.beliefkv_free_page_fence_enabled
    and self.enable_overlap and self.spec_algorithm.is_none()
    and self.is_generation
):
    self._beliefkv_load_free_boundary = cache_controller.beliefkv_record_free_page_boundary
""")
        replace_once(method(scheduler, "Scheduler", "run_batch"), """
if (boundary := getattr(self, "_beliefkv_load_free_boundary", None)) is not None:
    boundary()
""")
        selected = [
            node for node in ast.walk(after[FIXTURE]) if isinstance(node, ast.Set)
            and set(METHODS) <= {
                item.value for item in node.elts if isinstance(item, ast.Constant)
            }
        ]
        if len(selected) != 1:
            raise ValueError("free-page fence fixture extraction is not the reviewed method set")
        selected[0].elts = [
            item for item in selected[0].elts
            if not isinstance(item, ast.Constant) or item.value not in METHODS
        ]
        for name in FILES:
            if shape(before[name]) != shape(after[name]):
                raise ValueError("engine changed outside reviewed free-page fence statements: " + name)
    except (SyntaxError, StopIteration) as error:
        raise ValueError("free-page fence source is incomplete or invalid") from error
    return {
        "native_method_projection_matches": True,
        "gate": "BELIEFKV_FREE_PAGE_H2D_FENCE",
        "default_enabled": False,
        "required_allocator": "exact TokenToKVPoolAllocator; page_size=1",
        "requires_recorded_boundary_before_current_forward": True,
        "requires_no_free_since_boundary_at_allocation": True,
        "unproven_or_different_boundary_batch_uses_original_fence": True,
        "same_proof_applies_to_native_demand": True,
        "reviewed_method_ast_sha256": dict(METHODS),
    }
