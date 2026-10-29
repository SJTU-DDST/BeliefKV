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


def scenario(
    engine, *, delayed, donor_group_layers=1, consumer_group_layers=1,
    free_extent=False,
):
    import torch
    from sglang.srt.mem_cache.base_prefix_cache import InsertParams
    from sglang.srt.mem_cache.radix_cache import RadixKey
    from sglang.srt.mem_cache import beliefkv_layer_handoff as native
    from sglang.srt.mem_cache.l2_transfer import L2Transfer
    from types import SimpleNamespace as NS

    class Fixture(common.NativeFenceFixture):
        layers = (
            2 if (donor_group_layers, consumer_group_layers) == (1, 1)
            else 2 * max(donor_group_layers, consumer_group_layers) + 1
        )

    groups = ({
        "donor_group_layers": donor_group_layers,
        "consumer_group_layers": consumer_group_layers,
    } if (donor_group_layers, consumer_group_layers) != (1, 1) else {})
    donor_tokens, consumer_tokens = (24 if free_extent else 48), 32
    initial_free = 12 if free_extent else 4
    final_free = 4 if free_extent else 20
    additional = 8 if free_extent else 0
    f = Fixture(engine, enabled=True)
    cache, cc, pool = f.cache, f.controller, f.pool
    host = cc.mem_pool_host.anchor_entry.host_pool
    original_backup = host.backup_from_device_per_layer
    original_groups = getattr(host, "transfer_handoff_layers", None)

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
        warm_donor = cc.mem_pool_host.alloc(donor_tokens)
        warm_consumer = cc.mem_pool_host.alloc(consumer_tokens)
        d_host, d_device = cc.move_indices(warm_donor, warm[:donor_tokens])
        h_host, _ = cc.move_indices(warm_consumer, warm[:32])
        boundary = torch.cuda.Event()
        boundary.record()
        completion = cc.l2_transfer_engine.submit_layer_handoff(
            L2Transfer(host, pool, d_host, d_device),
            L2Transfer(host, pool, h_host, warm[:consumer_tokens]),
            layer_num=f.layers, donor_last_use_event=boundary, **groups,
            **({"additional_consumer_tokens": additional} if additional else {}),
        )
        completion.donor.finish_event.synchronize()
        completion.consumer.finish_event.synchronize()
        f.allocator.free(warm)
        cc.mem_pool_host.free(warm_donor)
        cc.mem_pool_host.free(warm_consumer)

        a, a_generation = insert(range(1, donor_tokens + 1), "donor")
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
        blocked = f.allocator.alloc(44 if free_extent else 28)
        if blocked is None or f.allocator.available_size() != initial_free:
            raise ValueError("A and B unexpectedly fit together")
        resident_lock = cache.inc_lock_ref(resident.id).to_dec_params()
        reader_done = torch.cuda.Event()
        with torch.cuda.stream(f.forward):
            f.forward.wait_stream(torch.cuda.current_stream())
            torch.cuda._sleep(100_000_000)
            old_a_reader = save(a.component_data[0].value)
            reader_done.record()

        def backup(device, host_indices, device_indices, layer, backend):
            if delayed and layer == f.layers - 1:
                torch.cuda._sleep(1_000_000_000)
            return original_backup(device, host_indices, device_indices, layer, backend)

        host.backup_from_device_per_layer = backup
        if groups:
            def group_copy(device, host_indices, device_indices, start, end, backend, *, load):
                if delayed and not load and end == f.layers:
                    torch.cuda._sleep(1_000_000_000)
                return original_groups(
                    device, host_indices, device_indices, start, end, backend, load=load,
                )
            host.transfer_handoff_layers = group_copy
        result = cache.begin_beliefkv_layer_handoff(
            donor_node_id=a.id, donor_creation_time=a.creation_time,
            donor_session_id="donor", donor_session_generation=a_generation,
            consumer_node_id=b.id, consumer_creation_time=b.creation_time,
            consumer_session_id="consumer", consumer_session_generation=b_generation,
            reserve_tokens=4, authorize=lambda *_: True, **groups,
            **({"allow_free_extent": True} if free_extent else {}),
        )
        if result is None or f.allocator.available_size() != 4:
            raise ValueError("real handoff failed or gave early free capacity")
        if free_extent and (
            result.get("free_extent_tokens") != additional
            or result.get("restored_tokens") != consumer_tokens
            or result["donor_tokens"] != donor_tokens
            or result["reused_tokens"] != donor_tokens
            or result["reserved_tail_tokens"] != 0
        ):
            raise ValueError("real handoff did not allocate exactly the missing free extent")
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
        if cache._beliefkv_layer_handoff is not None or f.allocator.available_size() != final_free:
            raise ValueError("final native reservation did not settle exactly once")
        cache.dec_lock_ref(resident.id, resident_lock)
        cache.sanity_check()
        return {
            "delayed_final_donor_layer": delayed, **result,
            "initial_free_tokens": initial_free, "post_submit_free_tokens": 4,
            "final_free_tokens": final_free, "all_donor_host_and_consumer_device_kv_verified": True,
            "protected_resident_kv_unchanged": True,
            "old_donor_reader_completed_before_overwrite": True,
            "first_consumer_layer_read_before_final_donor_ack": early,
            "actual_native_pool_layer_counter_and_accessors_used": True,
            "donor_early_return_blocked_until_backup_ack": True,
        }
    finally:
        host.backup_from_device_per_layer = original_backup
        if original_groups is not None:
            host.transfer_handoff_layers = original_groups
        f.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--donor-group-layers", type=int, choices=range(1, 65), default=1)
    parser.add_argument("--consumer-group-layers", type=int, choices=range(1, 65), default=1)
    parser.add_argument("--include-free-extent", action="store_true")
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
            direction_groups={
                "d2h": args.donor_group_layers, "h2d": args.consumer_group_layers,
            },
        )
        sys.path.insert(0, str(engine / "python"))
        report["scenarios"] = [
            scenario(
                engine, delayed=delayed, donor_group_layers=args.donor_group_layers,
                consumer_group_layers=args.consumer_group_layers,
            ) for delayed in (False, True)
        ]
        if args.include_free_extent:
            report["free_extent_scenarios"] = [
                scenario(
                    engine, delayed=delayed, donor_group_layers=args.donor_group_layers,
                    consumer_group_layers=args.consumer_group_layers, free_extent=True,
                ) for delayed in (False, True)
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
