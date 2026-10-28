"""Project only the exact reviewed default-off demand pipeline scheduler hooks."""

import ast

from beliefkv.experiments.free_page_h2d_fence import method, replace_once, shape


def project_disabled_scheduler(original, candidate):
    before, after = ast.parse(original), ast.parse(candidate)
    try:
        constructor = method(after, "Scheduler", "init_beliefkv_runtime")
        replace_once(constructor, "self._beliefkv_demand_pipeline = None")
        replace_once(constructor, """
from beliefkv.runtime.demand_prefill_pipeline import create_pipeline
self._beliefkv_demand_pipeline = create_pipeline(runtime, self.tree_cache)
""")
        selection = method(after, "Scheduler", "_get_new_batch_prefill_raw")
        replace_once(selection, """
demand_pipeline = getattr(self, "_beliefkv_demand_pipeline", None)
if (
    demand_pipeline is not None and self.chunked_req is None
    and demand_pipeline.defer(self.waiting_queue, running_batch)
):
    return None, running_batch
""")
        replace_once(selection, """
candidates = self._beliefkv_prefill_candidates(running_batch, adder)
demand_load_queue_was_empty = (
    demand_pipeline is not None and not self.tree_cache.cache_controller.load_queue
)
demand_admission_results = []
""")
        loops = [
            node for node in ast.walk(selection) if isinstance(node, ast.For)
            and shape(node.iter) == shape(ast.parse("candidates", mode="eval").body)
        ]
        if len(loops) != 1:
            raise ValueError("demand pipeline selection iterator differs")
        loops[0].iter = ast.parse(
            "self._beliefkv_prefill_candidates(running_batch, adder)", mode="eval",
        ).body
        replace_once(selection, """
if self.beliefkv_runtime is not None:
    if demand_pipeline is not None and req in adder.can_run_list:
        demand_admission_results.append((req, res.name))
    else:
        capacity_changed = self.beliefkv_runtime.on_prefill_candidate_result(
            req, admitted=req in adder.can_run_list, result=res.name
        ) is True
""", """
if self.beliefkv_runtime is not None:
    capacity_changed = self.beliefkv_runtime.on_prefill_candidate_result(
        req, admitted=req in adder.can_run_list, result=res.name
    ) is True
""")
        replace_once(selection, """
demand_staged = (
    demand_pipeline is not None and bool(adder.can_run_list)
    and demand_pipeline.stage(
        adder.can_run_list[0], adder=adder, running_batch=running_batch,
        candidate_count=len(candidates),
        native_load_queue_was_empty=demand_load_queue_was_empty,
        chunked_req=self.chunked_req,
    )
)
for req, result in demand_admission_results:
    self.beliefkv_runtime.on_prefill_candidate_result(
        req, admitted=not demand_staged,
        result="RESTORE_STAGED" if demand_staged else result,
    )
if demand_staged:
    return None, running_batch
""")
        replace_once(selection, """
if demand_pipeline is not None:
    demand_pipeline.bind(new_batch)
""")
        replace_once(method(after, "Scheduler", "_beliefkv_on_abort"), """
if (pipeline := getattr(self, "_beliefkv_demand_pipeline", None)) is not None:
    pipeline.on_abort(recv_req, exact=exact)
""")
        if shape(before) != shape(after):
            raise ValueError("native scheduler changed outside the reviewed demand pipeline")
    except (StopIteration, ValueError) as error:
        raise ValueError("native demand pipeline default-off projection refused: " + str(error)) from error
    return ast.unparse(after)
