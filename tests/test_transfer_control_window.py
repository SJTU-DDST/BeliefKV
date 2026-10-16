import unittest

from scripts.audit_transfer_control_window import (
    counter_delta,
    host_window,
    recompute_window,
)


def usage(timestamp, nodes, *, cap=32):
    return {
        "event": "host_pool_usage", "ts_ms": timestamp,
        "pools": {"full": {"used_fraction": 0.5}},
        "write_back_batch": {
            "max_nodes": cap, "counts": {"nodes": nodes, "batches": nodes // 2},
        },
        "full_host_eviction_scans": {"calls": nodes},
    }


class TransferControlWindowTest(unittest.TestCase):
    def test_snapshots_and_events_use_distinct_boundary_semantics(self):
        rows = [
            usage(90, 0), usage(190, 4), usage(140, 2),
            {
                "event": "host_pool_eviction", "ts_ms": 100,
                "pools": {"full": {"evicted_units": 5, "evicted_bytes": 100}},
            },
            {
                "event": "host_pool_eviction", "ts_ms": 200,
                "pools": {"full": {"evicted_units": 99, "evicted_bytes": 1980}},
            },
            usage(210, 6),
        ]
        result = host_window(rows, 100, 200)
        self.assertEqual(result["boundary_ts_ms"], [90, 190])
        self.assertEqual(result["write_back_batch"]["counts"]["nodes"], 4)
        self.assertEqual(result["evictions"]["full_units"], 5)
        self.assertEqual(result["sampled_occupancy_fraction"]["full"]["count"], 2)

    def test_reject_incomplete_coverage_reset_or_changed_limit(self):
        with self.assertRaisesRegex(ValueError, "cover"):
            host_window([usage(90, 0), usage(190, 4)], 100, 200)
        with self.assertRaisesRegex(ValueError, "counter reset"):
            counter_delta({"nodes": 4}, {"nodes": 2})
        with self.assertRaisesRegex(ValueError, "limit changed"):
            host_window([
                usage(90, 0), usage(190, 4, cap=16), usage(210, 6, cap=16),
            ], 100, 200)

    def test_prefix_proxy_and_eviction_attribution_remain_separate(self):
        result = recompute_window([
            {
                "event": "context_prefix_reuse_probe", "ts_ms": 100,
                "previously_served_prefix_recompute_proxy_tokens": 30,
            },
            {
                "event": "host_block_reaccess_attributed", "ts_ms": 150,
                "pool": "full", "full_recomputed_units": 7,
            },
            {
                "event": "host_block_reaccess_attributed", "ts_ms": 160,
                "pool": "mamba", "full_recomputed_units": None,
            },
            {
                "event": "host_block_reaccess_attributed", "ts_ms": 170,
                "pool": "full", "full_recomputed_units": None,
            },
            {
                "event": "context_prefix_reuse_probe", "ts_ms": 200,
                "previously_served_prefix_recompute_proxy_tokens": 999,
            },
        ], 100, 200)
        self.assertEqual(result["same_session_missing_input_proxy_tokens"], 30)
        self.assertEqual(result["observed_host_eviction_recompute_tokens"], 7)
        self.assertEqual(result["observed_full_reaccess_records"], 1)
        self.assertEqual(result["full_reaccess_records_with_unknown_recompute"], 1)
        self.assertNotIn("total_recomputed_tokens", result)


if __name__ == "__main__":
    unittest.main()
