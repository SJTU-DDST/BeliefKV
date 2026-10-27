"""Exercise reference admission phase sensitivity without KV transfers or GPUs."""

from dataclasses import asdict
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.policy.paper_baselines import ThunderActingDecayWorkingSet


def inspect(policy, action, now):
    return {
        "action": action, "now_s": now,
        "remaining_raw_tokens": policy.remaining_capacity(),
        "programs": {name: asdict(item) for name, item in policy.programs.items()},
    }


def scenario(control_during_tool):
    policy = ThunderActingDecayWorkingSet(805198)
    policy.arrive("other_running_agent", 650000, now=0.)
    policy.arrive("returning_root", 129000, now=0.)
    policy.completed("returning_root", 129000, now=.1)
    policy.arrive("new_child", 50000, now=.2)
    rows = [inspect(policy, "child_waits_while_root_is_ACTING", .2)]
    if control_during_tool:
        policy.tick(now=1.2)
        rows.append(inspect(policy, "periodic_control_during_tool", 1.2))
        assert not policy.programs["returning_root"].active
        assert policy.programs["new_child"].active
    policy.arrive("returning_root", 129100, now=3.1)
    rows.append(inspect(policy, "observed_tool_return_and_successor", 3.1))
    tick_times = (6.2, 11.2, 16.2) if control_during_tool else (4., 9., 14.)
    for now in tick_times:
        policy.tick(now=now)
        rows.append(inspect(policy, "periodic_control_after_return", now))
    remains_active = policy.programs["returning_root"].active
    assert remains_active is not control_during_tool
    return {
        "control_during_tool": control_during_tool,
        "root_active_after_three_following_controls": remains_active,
        "observed_tool_round_ms": 3000.,
        "timeline": rows,
    }


def main():
    output = Path(__file__).with_suffix(".json")
    if output.exists():
        raise FileExistsError("preserve reference counterexample evidence")
    result = {
        "scope": "CPU reference policy counterexample; synthetic three-program fixture",
        "capacity_tokens": 805198, "buffer_tokens": 100, "control_interval_s": 5.,
        "same_program_arrivals_and_tool_return": True,
        "cases": [scenario(True), scenario(False)],
        "gpu_used": False, "kv_transfers_performed": False,
        "historical_frequency_verified": False, "performance_accepted": False,
        "source_sha256": {
            str(path.relative_to(ROOT)): sha256_file(path) for path in (
                Path(__file__), ROOT / "beliefkv/policy/paper_baselines.py",
            )
        },
        "interpretation": (
            "A periodic control during a three-second tool can resume a smaller "
            "child against decayed ACTING cost, then pause the root under raw "
            "capacity. Its successor no longer fits after return. A different "
            "phase preserves the root on the same arrival/return sequence. "
            "This proves a possible reference-core mechanism, not the cause or "
            "duration of the historical 194-second wait. No KV copy or resident-first "
            "ordering is required for this counterexample."
        ),
    }
    with output.open("x") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({
        "same_arrivals_and_tool_return": True,
        "root_active_with_control_during_tool": result["cases"][0]["root_active_after_three_following_controls"],
        "root_active_with_control_after_return": result["cases"][1]["root_active_after_three_following_controls"],
        "historical_cause_proven": False,
    }, indent=2))


if __name__ == "__main__":
    main()
