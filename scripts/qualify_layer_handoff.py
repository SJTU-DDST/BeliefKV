#!/usr/bin/env python3
"""Exercise reserved native cache extents with real per-layer D2H/H2D copies."""

from array import array
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import git_source_state, sha256_file
from scripts import qualify_free_page_h2d_fence as common


def scenario(engine, *, delayed):
    import torch
    from sglang.srt.mem_cache.base_prefix_cache import InsertParams
    from sglang.srt.mem_cache.radix_cache import RadixKey
    from sglang.srt.mem_cache import beliefkv_layer_handoff as native
    from sglang.srt.mem_cache.l2_transfer import L2Transfer
    from types import SimpleNamespace as NS

    f = common.NativeFenceFixture(engine, enabled=True)
    cache, cc, pool = f.cache, f.controller, f.pool
    host = cc.mem_pool_host.anchor_entry.host_pool
    original_backup = host.backup_from_device_per_layer

    def save(indices):
        return tuple((k[indices].clone(), v[indices].clone()) for k, v in zip(
            pool.k_buffer, pool.v_buffer, strict=True,
        ))

    def insert(tokens, session):
        indices = f.allocator.alloc(len(tokens))
        if indices is None:
            raise ValueError("fixture Device allocation failed")
        values = torch.tensor(tokens, dtype=torch.float32, device="cuda")[:, None, None]
        features = torch.arange(128, dtype=torch.float32, device="cuda").reshape(1, 2, 64) / 256.
        for layer, (k, v) in enumerate(zip(pool.k_buffer, pool.v_buffer, strict=True)):
            k[indices] = (values + features + layer / 8.).to(k.dtype)
            v[indices] = (values + features + layer / 8. + 4.).to(v.dtype)
        node_id = cache.insert(InsertParams(key=RadixKey(array("q", tokens)), value=indices)).last_device_node
        generation = cache.ensure_session_generation(session)
        cache.session_refs.register_session_ref(NS(
            session_id=session, session_generation=generation, session=None,
            last_node=node_id, origin_input_ids=array("q", tokens), output_ids=array("q"),
            extra_key=None,
        ))
        return cache.tree_core.node_by_id(node_id), generation

    try:
        if not native.supported(cache):
            raise ValueError("real native MHA pool does not support the declared handoff")
        # Compile the exact two copy shapes before opening the finite proof window.
        warm = f.allocator.alloc(48)
        warm_donor, warm_consumer = cc.mem_pool_host.alloc(48), cc.mem_pool_host.alloc(32)
        d_host, d_device = cc.move_indices(warm_donor, warm)
        h_host, _ = cc.move_indices(warm_consumer, warm[:32])
        boundary = torch.cuda.Event()
        boundary.record()
        completion = cc.l2_transfer_engine.submit_layer_handoff(
            L2Transfer(host, pool, d_host, d_device),
            L2Transfer(host, pool, h_host, d_device[:32]),
            layer_num=2, donor_last_use_event=boundary,
        )
        completion.donor.finish_event.synchronize()
        completion.consumer.finish_event.synchronize()
        f.allocator.free(warm)
        cc.mem_pool_host.free(warm_donor)
        cc.mem_pool_host.free(warm_consumer)

        a, a_generation = insert(range(1, 49), "donor")
        b, b_generation = insert(range(101, 133), "consumer")
        resident, _ = insert(range(201, 249), "resident")
        a_expected = save(a.component_data[0].value)
        b_expected = save(b.component_data[0].value)
        resident_expected = save(resident.component_data[0].value)
        torch.cuda.synchronize()
        if not cache._prepare_beliefkv_cold_full_nodes(
            ((b.id, b.creation_time, 32),), read_only_backup=True,
            max_batch_bytes=32 * f.dimensions()["bytes_per_token"],
        ):
            raise ValueError("real consumer Host checkpoint did not enqueue")
        deadline = time.monotonic() + 15.
        while cache.ongoing_write_through:
            common.guard_gpu(allow_current=True)
            cache.writing_check()
            if time.monotonic() > deadline:
                raise TimeoutError("consumer checkpoint did not settle")
            time.sleep(.001)
        cache._demote(b.id, {})
        blocked = f.allocator.alloc(28)
        if blocked is None or f.allocator.available_size() != 4:
            raise ValueError("A and B unexpectedly fit together")
        resident_lock = cache.inc_lock_ref(resident.id).to_dec_params()
        reader_done = torch.cuda.Event()
        with torch.cuda.stream(f.forward):
            f.forward.wait_stream(torch.cuda.current_stream())
            torch.cuda._sleep(100_000_000)
            old_a_reader = save(a.component_data[0].value)
            reader_done.record()

        def backup(device, host_indices, device_indices, layer, backend):
            if delayed and layer == 1:
                torch.cuda._sleep(1_000_000_000)
            return original_backup(device, host_indices, device_indices, layer, backend)

        host.backup_from_device_per_layer = backup
        result = cache.begin_beliefkv_layer_handoff(
            donor_node_id=a.id, donor_creation_time=a.creation_time,
            donor_session_id="donor", donor_session_generation=a_generation,
            consumer_node_id=b.id, consumer_creation_time=b.creation_time,
            consumer_session_id="consumer", consumer_session_generation=b_generation,
            reserve_tokens=4, authorize=lambda *_: True,
        )
        if result is None or f.allocator.available_size() != 4:
            raise ValueError("real handoff failed or gave early free capacity")
        transaction = cache._beliefkv_layer_handoff
        if not native.blocks_load(cache, a.id) or cache.load_back(a.id):
            raise ValueError("early donor return could consume an incomplete Host checkpoint")
        first_compute_done = torch.cuda.Event()
        compute = torch.cuda.Stream()
        pool.register_layer_transfer_counter(cc.layer_done_counter)
        cc.layer_done_counter.set_consumer(cache.ready_to_load_host_cache())
        with torch.cuda.stream(compute):
            first_layer_kv = (
                pool.get_key_buffer(0)[b.component_data[0].value].clone(),
                pool.get_value_buffer(0)[b.component_data[0].value].clone(),
            )
            first_compute_done.record()
        first_compute_done.synchronize()
        early = not transaction.completion.donor.finish_event.query()
        if delayed and not early:
            raise ValueError("B first-layer read waited for A final-layer backup")
        transaction.completion.donor.finish_event.synchronize()
        transaction.completion.consumer.finish_event.synchronize()
        reader_done.synchronize()
        if any(
            not torch.equal(actual, expected)
            for pair, saved in zip(old_a_reader, a_expected, strict=True)
            for actual, expected in zip(pair, saved, strict=True)
        ):
            raise ValueError("handoff overwrote a real outstanding donor read")
        if any(not torch.equal(actual, expected) for actual, expected in zip(
            first_layer_kv, b_expected[0], strict=True,
        )):
            raise ValueError("first consumer layer has incorrect K/V")
        a_host = a.component_data[0].host_value
        for layer, (expected_k, expected_v) in enumerate(a_expected):
            if not (
                torch.equal(host.k_data_refs[layer][a_host], expected_k.cpu())
                and torch.equal(host.v_data_refs[layer][a_host], expected_v.cpu())
            ):
                raise ValueError("donor Host backup does not preserve every K/V byte")
        for actual, expected in (
            (save(b.component_data[0].value), b_expected),
            (save(resident.component_data[0].value), resident_expected),
        ):
            if any(
                not torch.equal(k, old_k) or not torch.equal(v, old_v)
                for (k, v), (old_k, old_v) in zip(actual, expected, strict=True)
            ):
                raise ValueError("consumer or protected resident K/V changed")
        cache.writing_check(finish_count=1)
        cache.loading_check(finish_count=1)
        if cache._beliefkv_layer_handoff is not None or f.allocator.available_size() != 20:
            raise ValueError("final native reservation did not settle exactly once")
        cache.dec_lock_ref(resident.id, resident_lock)
        cache.sanity_check()
        return {
            "delayed_final_donor_layer": delayed, **result,
            "initial_free_tokens": 4, "post_submit_free_tokens": 4,
            "final_free_tokens": 20, "all_donor_host_and_consumer_device_kv_verified": True,
            "protected_resident_kv_unchanged": True,
            "old_donor_reader_completed_before_overwrite": True,
            "first_consumer_layer_read_before_final_donor_ack": early,
            "actual_native_pool_layer_counter_and_accessors_used": True,
            "donor_early_return_blocked_until_backup_ack": True,
        }
    finally:
        host.backup_from_device_per_layer = original_backup
        f.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("preserve prior component results")
    engine = Path(os.environ["SGLANG_SOURCE_CHECKOUT"]).resolve()
    report = {"status": "failed", "performance_accepted": False}
    try:
        common.guard_gpu()
        if os.getpriority(os.PRIO_PROCESS, os.getpid()) != 0:
            raise ValueError("GPU qualification requires nice0")
        state = git_source_state(engine)
        if state["dirty"]:
            raise ValueError("freeze and commit native sources before GPU qualification")
        report.update(
            operator_sha256=sha256_file(Path(__file__)), native_engine=state,
            owned_gpu_pid=os.getpid(), authorization=common.check_reservation(),
        )
        sys.path.insert(0, str(engine / "python"))
        report["scenarios"] = [
            scenario(engine, delayed=False), scenario(engine, delayed=True),
        ]
        report.update(
            status="native_gpu_layer_handoff_components_qualified",
            observed_at=datetime.now(timezone.utc).isoformat(),
            model_forward_qualified=False, whole_workload_qualified=False,
            synthetic_finite_layer_delay_is_fixture_only=True,
        )
    except BaseException as error:
        report["error"] = repr(error)
        raise
    finally:
        common.controls.reservation.write_json(args.output, report)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
