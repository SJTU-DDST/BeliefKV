"""Qualify native queued-demand restoration with real GPU copies and accessors."""

from array import array
import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace as NS

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import git_source_state, sha256_file
from beliefkv.experiments.native_engine_variant import (
    DEMAND_PREFILL_PIPELINE_TREE, engine_patch,
)
from beliefkv.runtime.demand_prefill_pipeline import create_pipeline
from beliefkv.runtime.sglang_v0520_admission import _request_key
from scripts.audit_staged_native_restore import inspect as staged_inspect
from scripts import qualify_free_page_h2d_fence as common


SCENARIOS = ("resident_access", "selected_batch", "no_decode_fallback", "abort_shared_prefix")


def scenario(engine, name):
    import torch
    from sglang.srt.managers.schedule_batch import Req
    from sglang.srt.managers.schedule_policy import PrefillAdder
    from sglang.srt.mem_cache.base_prefix_cache import InsertParams
    from sglang.srt.mem_cache.radix_cache import RadixKey
    from sglang.srt.sampling.sampling_params import SamplingParams

    if name not in SCENARIOS:
        raise ValueError("unknown native demand component scene")
    f = common.NativeFenceFixture(engine, enabled=True)
    cache, cc, pool = f.cache, f.controller, f.pool
    host = cc.mem_pool_host.anchor_entry.host_pool
    original_load = host.load_to_device_per_layer
    commits, journals, members, leaves, sessions = [], [], [], [], []
    pipeline, shared = None, None
    cache.on_hicache_transfer_commit = commits.append
    cache.load_back_threshold = 1

    def save(indices):
        return tuple((key[indices].clone(), value[indices].clone()) for key, value in zip(
            pool.k_buffer, pool.v_buffer, strict=True,
        ))

    def insert(tokens, session):
        indices = f.allocator.alloc(len(tokens))
        if indices is None:
            raise ValueError("native component allocation failed")
        values = torch.tensor(tokens, device="cuda", dtype=torch.float32)[:, None, None]
        for layer, (key, value) in enumerate(zip(pool.k_buffer, pool.v_buffer, strict=True)):
            key[indices] = (values / 8. + layer).to(key.dtype)
            value[indices] = (values / 8. + layer + 4.).to(value.dtype)
        anchor = cache.insert(InsertParams(
            key=RadixKey(array("q", tokens)), value=indices,
        )).last_device_node
        generation = cache.ensure_session_generation(session)
        cache.session_refs.register_session_ref(NS(
            session_id=session, session_generation=generation, session=None,
            last_node=anchor, origin_input_ids=array("q", tokens), output_ids=array("q"),
            extra_key=None,
        ))
        sessions.append((session, generation))
        return cache.tree_core.node_by_id(anchor), generation

    def request(tokens, session, generation, rid):
        req = Req(
            rid=rid, origin_input_text="", origin_input_ids=array("q", (*tokens, 999)),
            session_id=session, sampling_params=SamplingParams(max_new_tokens=2),
            beliefkv_metadata={
                "root_workflow_id": "demand-component", "invocation_id": rid,
                "context_id": rid, "context_epoch": 0,
            },
        )
        req.session_generation = generation
        return req

    def adder():
        return PrefillAdder(
            page_size=1, tree_cache=cache, token_to_kv_pool_allocator=f.allocator,
            running_batch=NS(reqs=[]), new_token_ratio=1., rem_input_tokens=32,
            rem_chunk_tokens=32,
        )

    def read(reqs, consumer):
        cc.layer_done_counter.set_consumer(consumer)
        with torch.cuda.stream(f.forward):
            actual = [
                tuple((pool.get_key_buffer(layer)[req.prefix_indices].clone(),
                       pool.get_value_buffer(layer)[req.prefix_indices].clone())
                      for layer in range(f.layers))
                for req in reqs
            ]
            done = torch.cuda.Event()
            done.record()
        return actual, done

    def same(actual, expected):
        return all(
            torch.equal(left, right)
            for pair, saved in zip(actual, expected, strict=True)
            for left, right in zip(pair, saved, strict=True)
        )

    try:
        f.warmup()
        resident, resident_generation = insert(list(range(201, 217)), "resident")
        resident_lock = cache.inc_lock_ref(resident.id).to_dec_params()
        resident_expected = save(resident.component_data[0].value)
        resident_req = request(list(range(201, 217)), "resident", resident_generation, "resident")
        resident_req.last_node = resident.id
        resident_req.prefix_indices = resident.component_data[0].value
        running = NS(reqs=[resident_req], is_prefill_only=False, hicache_consumer_index=0)
        for index in range(2 if name == "selected_batch" else 1):
            tokens = list(range(1 + index * 64, 33 + index * 64))
            leaf, generation = insert(tokens, f"cold-{index}")
            leaves.append((leaf, save(leaf.component_data[0].value)))
            members.append(request(tokens, f"cold-{index}", generation, f"request-{index}"))
        torch.cuda.synchronize()
        for leaf, _ in leaves:
            if not cache._prepare_beliefkv_cold_full_nodes(
                ((leaf.id, leaf.creation_time, 32),), read_only_backup=True,
                max_batch_bytes=32 * f.dimensions()["bytes_per_token"],
            ):
                raise ValueError("native component could not back up its cold prefix")
            f.wait(cc.ack_write_queue[0].finish_event)
            cache.writing_check(finish_count=1)
            cache._demote(leaf.id, {})
        # Compile the exact merged native load shape before the finite overlap window.
        for leaf, _ in leaves:
            if not cache.load_back(leaf.id):
                raise ValueError("native load warmup failed")
        cache.ready_to_load_host_cache()
        f.wait(cc.ack_load_queue[0].finish_event)
        cache.loading_check(finish_count=1)
        for leaf, expected in leaves:
            if not same(save(leaf.component_data[0].value), expected):
                raise ValueError("native warmup changed cold prefix K/V")
            cache._demote(leaf.id, {})
        poison = f.allocate("released-page-poison", f.allocator.available_size())
        f.poison(poison)
        f.release("released-page-poison")
        torch.cuda.synchronize()
        keys = tuple(_request_key(req) for req in members)
        runtime = NS(
            demand_prefill_pipeline_enabled=True, internal_comparison_mode="full",
            _paper_baseline=None, _agent_admission=NS(allows=lambda key: key in keys),
            visible={req.rid: key for req, key in zip(members, keys)},
            _terminal=lambda _: False, counts=Counter(),
            _opportunity_writer=NS(record=journals.append),
        )
        pipeline = create_pipeline(runtime, cache)
        selection = adder()
        for req in members:
            req.init_next_round_input(cache)
            selection.add_one_req(req, has_chunked_req=False, truncation_align_size=None)
        if selection.can_run_list != members:
            raise ValueError("native capacity did not approve the whole selected fixture")

        def delayed_load(device, host_indices, device_indices, layer, backend):
            if layer == f.layers - 1:
                torch.cuda._sleep(2_000_000_000)
            return original_load(device, host_indices, device_indices, layer, backend)

        host.load_to_device_per_layer = delayed_load
        cc.beliefkv_record_free_page_boundary()
        if not pipeline.stage(
            members[0], adder=selection, running_batch=running, candidate_count=len(members),
            native_load_queue_was_empty=True, chunked_req=None,
        ):
            raise ValueError("real native demand did not enter staged restoration")
        pending = pipeline.pending
        if not pipeline.defer(members, running) or running.hicache_consumer_index != -1:
            raise ValueError("resident work inherited an unrelated load fence")
        resident_actual, resident_done = read([resident_req], running.hicache_consumer_index)
        f.wait(resident_done)
        resident_early = not pending.finish_event.query()
        if not resident_early or not same(resident_actual[0], resident_expected):
            raise ValueError("resident accessor failed to finish during the real H2D window")
        consumer_index = pending.consumer_index
        cancelled_shared = False
        if name == "abort_shared_prefix":
            pipeline.on_abort(NS(abort_all=False, rid=members[0].rid), exact=True)
            shared_session = "shared-consumer"
            shared_generation = cache.ensure_session_generation(shared_session)
            sessions.append((shared_session, shared_generation))
            shared = request(list(range(1, 33)), shared_session, shared_generation, "shared")
            shared.init_next_round_input(cache)
            shared_adder = adder()
            shared_adder.add_one_req(shared, has_chunked_req=False, truncation_align_size=None)
            if shared_adder.can_run_list != [shared]:
                raise ValueError("native shared-prefix consumer was not admitted")
            consumer_index = cache.ready_to_load_host_cache()
            consumer_index = cache.beliefkv_prefill_load_consumer([shared], consumer_index)
            if consumer_index < 0 or pipeline.pending is not None:
                raise ValueError("cancelled restore lost its shared-prefix layer dependency")
            cancelled_shared = True
            active = [shared]
        elif name == "no_decode_fallback":
            running.reqs.clear()
            if pipeline.defer(members, running):
                raise ValueError("restoration deferred without useful decode")
            again = adder()
            for req in members:
                req.init_next_round_input(cache)
                again.add_one_req(req, has_chunked_req=False, truncation_align_size=None)
            if again.can_run_list != members:
                raise ValueError("normal native fallback lost its selected members")
            batch = NS(reqs=members, hicache_consumer_index=-1)
            pipeline.bind(batch)
            consumer_index = batch.hicache_consumer_index
            if consumer_index < 0:
                raise ValueError("early native fallback did not retain its layer fence")
            active = members
        else:
            f.wait(pending.finish_event)
            cache.loading_check(finish_count=1)
            if pipeline.defer(members, running):
                raise ValueError("completed native restore remained deferred")
            again = adder()
            for req in members:
                req.init_next_round_input(cache)
                again.add_one_req(req, has_chunked_req=False, truncation_align_size=None)
            if again.can_run_list != members:
                raise ValueError("restored native members did not readmit together")
            consumer_index = -1
            active = members
        actual, consumer_done = read(active, consumer_index)
        f.wait(consumer_done)
        f.wait(pending.finish_event)
        if cc.ack_load_queue:
            cache.loading_check(finish_count=1)
        if any(not same(value, expected) for value, (_, expected) in zip(actual, leaves, strict=True)):
            raise ValueError("native consumer read incorrect restored K/V")
        for req in active:
            cache.dec_lock_ref(req.last_node, req.lock_receipt)
            req.lock_receipt = None
        pipeline.close()
        cache.dec_lock_ref(resident.id, resident_lock)
        cache.sanity_check()
        transfers = [{
            "direction": row.direction, "status": row.status,
            "submit_ts_ms": row.submit_ts_ms, "node_ids": list(row.node_ids),
            "actual_bytes": row.actual_bytes,
            "num_tokens_by_pool": dict(row.num_tokens_by_pool),
            "tagged_child_commits": list(row.child_commits),
        } for row in commits]
        accounting = staged_inspect(
            journals, transfers, bytes_per_token=f.dimensions()["bytes_per_token"],
        )
        if accounting["counts"].get("matched_staging_episodes") != 1:
            raise ValueError("actual staged copy lacks its exact native completion")
        for session, generation in sessions:
            cache.retire_beliefkv_child_session(
                session_id=session, session_generation=generation, max_nodes=128,
            )
        cleanup = f.finish()
        return {
            "scenario": name, "staged_members": len(members),
            "real_native_adder_and_request_receipts": True,
            "resident_accessor_completed_before_h2d_ack": resident_early,
            "all_consumer_kv_bytes_equal": True,
            "cancelled_shared_prefix_fence_verified": cancelled_shared,
            "early_fallback_fence_verified": name == "no_decode_fallback",
            "staged_native_ack": accounting, "cleanup": cleanup,
            "geometry": f.dimensions(),
            "model_forward_qualified": False, "performance_accepted": False,
            "scope": "Finite delayed-copy component; real GPU accessors, not model decode.",
        }
    finally:
        host.load_to_device_per_layer = original_load
        if pipeline is not None:
            pipeline.close()
        f.close()


def validate_scenarios(rows):
    if [row.get("scenario") for row in rows] != list(SCENARIOS):
        raise ValueError("native demand qualification requires every declared scene")
    for row in rows:
        members = 2 if row["scenario"] == "selected_batch" else 1
        ack = row.get("staged_native_ack") or {}
        if (
            row.get("staged_members") != members
            or row.get("real_native_adder_and_request_receipts") is not True
            or row.get("resident_accessor_completed_before_h2d_ack") is not True
            or row.get("all_consumer_kv_bytes_equal") is not True
            or row.get("cleanup") != {
                "final_device_free_tokens": 128, "initial_host_free_tokens": 257,
                "final_host_free_tokens": 257,
            }
            or row.get("model_forward_qualified") is not False
            or row.get("performance_accepted") is not False
            or row.get("geometry") != {
                "device_tokens": 128, "host_tokens": 257, "layers": 2, "bytes_per_token": 1024,
            }
            or ack.get("exact_native_ack_matches_verified") is not True
            or ack.get("counts") != {
                "matched_staging_episodes": 1, "native_selected_requests": members,
                "native_full_tokens": 32 * members, "native_h2d_bytes": 32 * members * 1024,
            }
        ):
            raise ValueError("native demand component evidence is incomplete")
    if (
        rows[2].get("early_fallback_fence_verified") is not True
        or rows[3].get("cancelled_shared_prefix_fence_verified") is not True
    ):
        raise ValueError("native demand fallback or cancellation is unverified")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cpu-evidence", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("preserve earlier native GPU qualifications")
    report = {"status": "failed", "performance_accepted": False}
    try:
        common.guard_gpu()
        if os.getpriority(os.PRIO_PROCESS, os.getpid()) != 0:
            raise ValueError("native GPU qualification requires nice0")
        application = git_source_state(ROOT)
        engine = Path(os.environ["SGLANG_SOURCE_CHECKOUT"]).resolve()
        native = git_source_state(engine)
        tree = subprocess.check_output(
            ["git", "rev-parse", "HEAD^{tree}"], cwd=engine, text=True,
        ).strip()
        if application["dirty"] or native["dirty"] or tree != DEMAND_PREFILL_PIPELINE_TREE:
            raise ValueError("freeze the exact application and native engine before GPU use")
        cpu = json.loads(args.cpu_evidence.read_bytes())
        manifest_path = Path(cpu["source_manifest"]["path"])
        if (
            sha256_file(manifest_path) != cpu["source_manifest"]["sha256"]
            or cpu["passing_unique_case_identities"] < 3839 or cpu["native_engine"] != native
        ):
            raise ValueError("real native components require current complete CPU evidence")
        for relative, digest in json.loads(manifest_path.read_bytes()).items():
            if sha256_file(ROOT / relative) != digest:
                raise ValueError("CPU-tested application changed: " + relative)
        patch = engine_patch(ROOT, tree)
        report.update(
            application=application, native_engine=native, native_tree=tree,
            canonical_patch_sha256=sha256_file(patch),
            operator_sha256=sha256_file(Path(__file__)), owned_gpu_pid=os.getpid(),
            authorization=common.check_reservation(),
            cpu_evidence={"path": str(args.cpu_evidence.resolve()), "sha256": sha256_file(args.cpu_evidence)},
        )
        sys.path.insert(0, str(engine / "python"))
        report["scenarios"] = []
        for name in SCENARIOS:
            report["scenarios"].append(scenario(engine, name))
        validate_scenarios(report["scenarios"])
        report.update(
            status="native_gpu_demand_prefill_components_qualified",
            model_forward_qualified=False, whole_workload_qualified=False,
            observed_at=datetime.now(timezone.utc).isoformat(),
        )
    except BaseException as error:
        report["error"] = repr(error)
        raise
    finally:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x") as stream:
            json.dump(report, stream, indent=2, allow_nan=False)
            stream.write("\n")


if __name__ == "__main__":
    main()
