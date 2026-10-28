import ast
import os
from pathlib import Path
import subprocess

import pytest

from beliefkv.experiments.demand_prefill_pipeline_engine import project_disabled_scheduler
from beliefkv.experiments.native_engine_variant import SIBLING_CAPACITY_TREE


@pytest.fixture
def sources():
    checkout = Path(os.environ["SGLANG_SOURCE_CHECKOUT"])
    relative = "python/sglang/srt/managers/scheduler.py"
    original = subprocess.check_output(
        ["git", "-C", str(checkout), "show", SIBLING_CAPACITY_TREE + ":" + relative],
        text=True,
    )
    return original, (checkout / relative).read_text()


def test_exact_new_native_hooks_project_to_the_qualified_parent_engine_when_disabled(sources):
    original, current = sources
    projected = project_disabled_scheduler(original, current)
    assert ast.dump(ast.parse(projected)) == ast.dump(ast.parse(original))


@pytest.mark.parametrize("old,new", (
    ("self._beliefkv_demand_pipeline = None", "self._beliefkv_demand_pipeline = True"),
    ("demand_pipeline.defer(self.waiting_queue, running_batch)", "True"),
    ("admitted=not demand_staged", "admitted=True"),
    ("pipeline.on_abort(recv_req, exact=exact)", "pass"),
    ("demand_pipeline.bind(new_batch)", "pass"),
    ("native_load_queue_was_empty=demand_load_queue_was_empty", "native_load_queue_was_empty=True"),
))
def test_changed_or_unsafe_pipeline_hooks_cannot_reuse_default_off_proof(sources, old, new):
    original, current = sources
    assert old in current
    with pytest.raises(ValueError, match="default-off projection refused"):
        project_disabled_scheduler(original, current.replace(old, new))


def test_unrelated_native_change_cannot_be_hidden_by_pipeline_projection(sources):
    original, current = sources
    with pytest.raises(ValueError, match="outside the reviewed demand pipeline"):
        project_disabled_scheduler(original, current + "\nunrelated = True\n")


def test_legacy_free_page_checker_still_rejects_the_unprojected_new_engine():
    from beliefkv.experiments.free_page_h2d_fence import FILES, verify_free_page_fence_projection

    checkout = Path(os.environ["SGLANG_SOURCE_CHECKOUT"])
    before = {
        name: subprocess.check_output(
            ["git", "-C", str(checkout), "show",
             "6437d7deeacc533f0d795ba166dfd5256b3f44b2:" + name],
            text=True,
        ) for name in FILES
    }
    after = {name: (checkout / name).read_text() for name in FILES}
    with pytest.raises(ValueError, match="outside reviewed free-page"):
        verify_free_page_fence_projection(before, after)
