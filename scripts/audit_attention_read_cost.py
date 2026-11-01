"""Relate captured attention cost to logical KV reads, without claiming DRAM counters."""

from datetime import datetime, timezone
import argparse
import gzip
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts.analyze_native_token_cost import partition_kernels
from scripts.audit_decode_batch_efficiency import OutputConservation
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts.train_workflow_work import write


def logical_kv_bytes(lengths, *, layers, kv_heads, head_dim, element_bytes=2):
    dimensions = (layers, kv_heads, head_dim, element_bytes)
    if (
        not lengths or any(type(value) is not int or value < 1 for value in lengths)
        or any(type(value) is not int or value < 1 for value in dimensions)
    ):
        raise ValueError("positive integer context and KV geometry required")
    return sum(lengths) * layers * kv_heads * head_dim * element_bytes * 2


def analyze(component_root, model_config):
    directory = component_root / "sparse"
    summary_path, info_path = directory / "summary.json", directory / "server/server_info.json"
    summary, info = (json.loads(path.read_bytes()) for path in (summary_path, info_path))
    if info["dtype"] != "bfloat16" or info["kv_cache_dtype"] != "bfloat16" or info["attention_backend"] != "fa3":
        raise ValueError("this screen requires the retained native BF16/FA3 geometry")
    config = json.loads(model_config.read_bytes())
    geometry = {
        "layers": config["num_hidden_layers"], "kv_heads": config["num_key_value_heads"],
        "head_dim": config.get("head_dim", config["hidden_size"] // config["num_attention_heads"]),
        "element_bytes": 2,
    }
    profiles = [row for row in summary["profiles"] if "DECODE" in Path(row["trace"]).name]
    if len(profiles) != 1:
        raise ValueError("one bounded native decode profile is required")
    trace_path = Path(profiles[0]["trace"])
    opener = gzip.open if trace_path.suffix == ".gz" else open
    with opener(trace_path, "rt") as stream:
        events = json.load(stream)["traceEvents"]
    mainloops = sum(
        row.get("cat") == "kernel" and row.get("ph") == "X" and "FlashAttnFwdSm90" in row["name"]
        for row in events
    )
    if mainloops == 0 or mainloops % geometry["layers"]:
        raise ValueError("profile does not have complete FA3 layer sets")
    forwards = mainloops // geometry["layers"]
    partition = partition_kernels(events)
    attention_ms = (
        partition["by_group"]["attention"]["exclusive_interval_ms"]
        + sum(
            value for groups, value in partition["simultaneously_active_kernel_groups_ms"].items()
            if "attention" in groups.split("+")
        )
    )
    journal = directory / "server/runtime_audit.jsonl"
    reader, conservation, selected = SealedRecords(), OutputConservation(), []
    for row in reader(journal):
        if row.get("event") != "gpu_service_sample":
            continue
        conservation.observe(row)
        if (
            row["phase"] == "decode" and row["batch_size"] == 9
            and all(member["request_id"].startswith("cost:w9:profile:") for member in row["request_samples"])
        ):
            selected.append(row)
    receipts_path = directory / "receipts.json"
    receipts = json.loads(receipts_path.read_bytes())
    conservation.verify({
        identity: {"output_tokens": len(row["tokens"])} for identity, row in receipts.items()
    })
    if len(selected) < forwards or forwards != 24:
        raise ValueError("registered 24-step, full-width9 profile prefix is unavailable")
    sampled = selected[:forwards]
    logical = sum(
        logical_kv_bytes([member["sequence_tokens_before"] for member in row["request_samples"]], **geometry)
        for row in sampled
    )
    equivalent = logical / (attention_ms / 1000) / 1e12
    paths = (summary_path, info_path, model_config, trace_path, journal, receipts_path, Path(__file__))
    return {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "geometry": geometry, "profiled_forwards": forwards, "actual_batch_width": 9,
        "selected_native_forward_iterations": [row["forward_iter"] for row in sampled],
        "logical_KV_read_bytes": logical, "attention_kernel_union_ms": attention_ms,
        "logical_KV_read_equivalent_TB_per_second": equivalent,
        "native_profiler_prefix_alignment": (
            "The registered profiler starts at the first full-width9 decode batch and captures 24 steps. "
            "Use that initial native prefix; this is not a timestamp-correlated hardware-counter measurement."
        ),
        "published_hardware_reference": {
            "vendor": "NVIDIA", "GPU": "H200 NVL", "memory_bandwidth_TB_per_second": 4.8,
            "source_url": "https://resources.nvidia.com/en-us-gpu-resources/hpc-datasheet-sc23",
            "checked_on": "2026-11-01", "is_a_measured_counter": False,
        },
        "logical_equivalent_divided_by_published_bandwidth": equivalent / 4.8,
        "semantics": [
            "Logical bytes count each real request's context once per layer for both K and V.",
            "These are sequence-length-based read equivalents, not unique physical pages or measured DRAM traffic.",
            "Cross-request prefix sharing, L2 reuse, repeated loads, padding, addressing and other traffic are not modeled.",
            "Kernel union includes attention and its combine kernels, with overlap counted once.",
            "The ratio to published bandwidth is not HBM utilization, a rigorous roofline bound, or removable JCT.",
            "This CPU audit does not modify native execution, official workload, Full policy or baseline results.",
        ],
        "all_component_output_ordinals_reconciled": True,
        "input_jsonl_prefixes": reader.prefixes,
        "inputs_sha256": {str(path.resolve()): sha256_file(path) for path in paths},
        "GPU_action": False, "performance_accepted": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--component-root", type=Path, required=True)
    parser.add_argument("--model-config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("preserve previous traffic evidence")
    result = analyze(args.component_root.resolve(), args.model_config.resolve())
    write(args.output, result)
    print(json.dumps({
        key: result[key] for key in (
            "profiled_forwards", "logical_KV_read_bytes", "attention_kernel_union_ms",
            "logical_KV_read_equivalent_TB_per_second",
        )
    }))


if __name__ == "__main__":
    main()
