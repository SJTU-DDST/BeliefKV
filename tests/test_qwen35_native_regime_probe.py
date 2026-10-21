from __future__ import annotations

import os
import json
from pathlib import Path
import subprocess

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/run_qwen35_native_regime_probe.sh"
TRAIN_SCRIPT = Path(__file__).resolve().parents[1] / "scripts/run_qwen35_native_train_batches.sh"


def test_probe_defaults_to_current_staging_patch() -> None:
    script = SCRIPT.read_text()
    assert 'SGLANG_PATCH_FLAVOR="${SGLANG_PATCH_FLAVOR:-staging}"' in script
    assert 'SGLANG_PATCH_FLAVOR="$SGLANG_PATCH_FLAVOR" \\' in script


def test_probe_defaults_to_training_only_moderate_pressure_candidate() -> None:
    script = SCRIPT.read_text()
    for setting in (
        'ROOT_COUNT="${ROOT_COUNT:-108}"',
        'HICACHE_SIZE_GB="${HICACHE_SIZE_GB:-200}"',
        'HOST_SPLIT="${HOST_SPLIT:-80:20}"',
        'HICACHE_WRITE_POLICY="${HICACHE_WRITE_POLICY:-write_back}"',
        'FANOUT_PROFILE="${FANOUT_PROFILE:-native_in_graph_nested_2to4}"',
    ):
        assert setting in script
    assert '--enable-beliefkv-admission --beliefkv-event-socket-path "$SOCKET"' in script
    assert '--mamba-full-memory-ratio 0.9' in script
    assert 'env -u BELIEFKV_FULL_MAMBA_HOST_SPLIT' in script
    assert '--stream-completion-shadow --child-stream-content-shadow' in script


def test_future_train_collection_defaults_to_user_selected_host_split() -> None:
    script = TRAIN_SCRIPT.read_text()
    assert 'FULL_MAMBA_HOST_SPLIT="${FULL_MAMBA_HOST_SPLIT:-80:20}"' in script
    assert 'env -u BELIEFKV_FULL_MAMBA_HOST_SPLIT' in script
    assert '.capacity.device_full_bytes / .capacity.device_total_bytes' in script


def test_auto_host_split_is_forwarded_explicitly_to_launcher() -> None:
    for path, variable in ((SCRIPT, "HOST_SPLIT"), (TRAIN_SCRIPT, "FULL_MAMBA_HOST_SPLIT")):
        assert f'host_split_env=(BELIEFKV_FULL_MAMBA_HOST_SPLIT="${variable}")' in path.read_text()
    launcher = SCRIPT.with_name("launch_qwen35_native_v0520.sh").read_text()
    assert 'BELIEFKV_FULL_MAMBA_HOST_SPLIT="${BELIEFKV_FULL_MAMBA_HOST_SPLIT:-80:20}"' in launcher
    assert 'if [[ "${BELIEFKV_FULL_MAMBA_HOST_SPLIT}" == auto ]]; then' in launcher
    assert 'unset BELIEFKV_FULL_MAMBA_HOST_SPLIT' in launcher


def test_resident_first_is_common_but_execution_handoff_is_predictive_only() -> None:
    script = SCRIPT.read_text()
    assert 'BELIEFKV_ENABLE_RESIDENT_FIRST=1' in script
    assert 'BELIEFKV_ENABLE_EXECUTION_HANDOFF=0' in script
    assert 'EXECUTION_HANDOFF="${EXECUTION_HANDOFF:-1}"' in script
    assert 'BELIEFKV_ENABLE_EXECUTION_HANDOFF="$EXECUTION_HANDOFF"' in script
    pair = SCRIPT.with_name("run_qwen35_semantic_h2d_ab.sh").read_text()
    assert 'HOST_SPLIT="${HOST_SPLIT:-80:20}"' in pair
    assert 'HOST_SPLIT="$HOST_SPLIT" HICACHE_SIZE_GB=200' in pair
    assert '--host-split "$HOST_SPLIT"' in pair


def test_confirmed_join_requires_verified_ack_patch_before_start(tmp_path: Path) -> None:
    run_root = tmp_path / "probe"
    result = subprocess.run(
        ["bash", str(SCRIPT)],
        env={
            **os.environ,
            "RUN_ROOT": str(run_root),
            "CONFIRMED_JOIN_CANARY": "1",
            "SGLANG_PATCH_FLAVOR": "staging",
        },
        text=True,
        capture_output=True,
        timeout=5,
    )
    assert result.returncode == 2
    assert "CONFIRMED_JOIN_CANARY" in result.stderr
    assert not run_root.exists()


def test_pressure_scan_rejects_unauthorized_single_wave_above_108(tmp_path: Path) -> None:
    run_root = tmp_path / "probe"
    result = subprocess.run(
        ["bash", str(SCRIPT)],
        env={**os.environ, "RUN_ROOT": str(run_root), "ROOT_COUNT": "109"},
        text=True,
        capture_output=True,
        timeout=5,
    )
    assert result.returncode == 2
    assert "Usage:" in result.stderr
    assert not run_root.exists()


def test_native_policy_baseline_disables_beliefkv_policy() -> None:
    script = SCRIPT.read_text()
    assert 'control_flags=(--native-policy-baseline)' in script
    assert 'telemetry_env=(BELIEFKV_NATIVE_TELEMETRY_DIR="$RUN_ROOT/server")' in script
    assert 'BELIEFKV_ENABLE_PREPARE_HOST=0 BELIEFKV_ENABLE_TOOL_PREFETCH=0' in script
    assert 'unset_env=(-u BELIEFKV_NATIVE_TELEMETRY_DIR)' in script


def test_pair_driver_freezes_both_arms_and_exports_timelines_automatically():
    script = SCRIPT.with_name("run_qwen35_semantic_h2d_ab.sh").read_text()
    assert '--verify-frozen-plan' in script
    assert 'NATIVE_POLICY_BASELINE="$baseline"' in script
    assert 'ARRIVAL_BATCH_SIZE="$ARRIVAL_BATCH_SIZE"' in script
    assert '--output-html "$RUN_ROOT/timelines/$arm.html"' in script
    assert 'scripts/compare_native_policy_runs.py' in script


@pytest.mark.parametrize("arm_order,cache_setting,environment_name", (
    ("native predictive_h2d", {"chat_token_block_cache": True},
     "NATIVE_CHAT_TOKEN_BLOCK_CACHE"),
    ("reactive predictive_h2d", {"chat_token_block_cache": True},
     "REACTIVE_CHAT_TOKEN_BLOCK_CACHE"),
    ("predictive_h2d", {
        "chat_token_block_cache_by_arm": {"predictive_h2d": False},
    }, "PREDICTIVE_CHAT_TOKEN_BLOCK_CACHE"),
))
def test_pair_driver_passes_frozen_frontend_to_probe(
    tmp_path, arm_order, cache_setting, environment_name,
):
    import shutil
    from sys import executable

    scripts = tmp_path / "scripts"
    scripts.mkdir()
    driver = scripts / "run_qwen35_semantic_h2d_ab.sh"
    shutil.copyfile(SCRIPT.with_name(driver.name), driver)
    (scripts / "summarize_semantic_h2d_ab.py").write_text("")
    (scripts / SCRIPT.name).write_text(
        '#!/bin/bash\nexec "$PYTHON" -c \''
        "import json,os; from pathlib import Path; "
        'Path(os.environ["RUN_ROOT"] + ".frontend.json").write_text('
        'json.dumps({key: value for key, value in os.environ.items() '
        'if key.endswith("CHAT_TOKEN_BLOCK_CACHE") or key == "SERVER_RANDOM_SEED"})); '
        "raise SystemExit(1)'"
    )
    run_root = tmp_path / "run"
    run_root.mkdir()
    (run_root / "ab_plan.json").write_text(json.dumps(cache_setting))
    enabled = cache_setting.get("chat_token_block_cache", False)
    result = subprocess.run(
        ["/bin/bash", str(driver)],
        env={
            **os.environ, "PYTHON": executable,
            "RUN_ROOT": str(run_root), "RESUME_PENDING": "1",
            "ROOT_COUNT": "1", "ARRIVAL_BATCH_SIZE": "0",
            "ARRIVAL_BATCH_INTERVAL_MS": "0", "ARRIVAL_RATE_PER_SECOND": "0",
            "ARM_ORDER": arm_order, "NATIVE_REFERENCE_RUN": "",
            "SERVER_RANDOM_SEED": "1234",
            "NATIVE_REFERENCE_TIMELINE": "", "NATIVE_REFERENCE_CACHE": "",
            "BELIEFKV_CHAT_TOKEN_BLOCK_CACHE": str(int(enabled)),
            environment_name: str(int(not enabled)),
        },
        text=True, capture_output=True, timeout=10,
    )
    assert result.returncode == 1, result.stderr
    assert "Arm did not produce a terminal workload summary" in result.stderr
    actual = json.loads(
        (run_root / (arm_order.split()[0] + ".frontend.json")).read_text()
    )
    assert actual[environment_name] == str(int(enabled))
    assert actual["BELIEFKV_CHAT_TOKEN_BLOCK_CACHE"] == str(int(enabled))
    assert actual["SERVER_RANDOM_SEED"] == "1234"


def test_native_policy_baseline_rejects_predictive_mix_before_launch(tmp_path: Path) -> None:
    run_root = tmp_path / "baseline"
    result = subprocess.run(
        ["bash", str(SCRIPT)],
        env={
            **os.environ, "RUN_ROOT": str(run_root),
            "NATIVE_POLICY_BASELINE": "1", "AB_MODE": "predictive_h2d",
        },
        text=True, capture_output=True, timeout=5,
    )
    assert result.returncode == 2
    assert not run_root.exists()


@pytest.mark.parametrize("comparison", (
    "full", "join_priority_only", "scheduler_only", "kv_only", "native_matched",
))
def test_internal_comparison_effective_environment_before_gpu_launch(
    tmp_path, comparison, *, protection="1",
):
    import sys
    from beliefkv.experiments.internal_comparison import arm_environment

    binary = tmp_path / "bin"
    binary.mkdir()
    capture = binary / "bash"
    capture.write_text(
        "#!" + sys.executable + "\n"
        "import json,os,sys\n"
        'print(json.dumps({"environment":dict(os.environ),"arguments":sys.argv[1:]}))\n'
    )
    capture.chmod(0o755)
    artifact = tmp_path / "model.json"
    artifact.write_text("{}")
    run = tmp_path / "probe"
    environment = arm_environment({
        "PYTHON": sys.executable, "RUN_ROOT": str(run), "ROOT_COUNT": "1",
        "PORT": "1", "SGLANG_SOURCE_CHECKOUT": str(tmp_path / "engine"),
        "TOOL_TIMING_ARTIFACT": str(artifact), "TRANSFER_SERVICE_SEED": str(artifact),
        "BELIEFKV_EXPERIMENT_PAUSE_FILE": str(tmp_path / "pause"),
        "PREFETCH_RESIDENCY_PROTECTION": protection,
    }, comparison)
    result = subprocess.run(
        ["/bin/bash", str(SCRIPT)],
        env={**os.environ, **environment, "PATH": str(binary) + os.pathsep + os.environ["PATH"]},
        text=True, capture_output=True, timeout=10,
    )
    assert result.returncode == 1, result.stderr
    payload = json.loads(next(row for row in (run / "server.log").read_text().splitlines()
                              if row.startswith("{")))
    actual = payload["environment"]
    custom_kv = comparison in ("full", "kv_only")
    assert actual["BELIEFKV_PREFETCH_RESIDENCY_PROTECTION"] == (
        protection if custom_kv else "0"
    )
    assert actual["BELIEFKV_ENABLE_PREPARE_HOST"] == str(int(custom_kv))
    assert actual["BELIEFKV_ENABLE_FINAL_STAGE_PREFETCH"] == str(int(custom_kv))
    assert actual["BELIEFKV_ENABLE_TOOL_PREFETCH"] == str(int(custom_kv))
    assert actual["BELIEFKV_ENABLE_FINAL_STAGE_PRIORITY"] == str(int(comparison in ("full", "scheduler_only")))
    assert actual["BELIEFKV_ENABLE_RESIDENT_FIRST"] == str(int(comparison in ("full", "scheduler_only")))
    assert actual["BELIEFKV_CHAT_TOKEN_BLOCK_CACHE"] == "1"
    if not custom_kv:
        assert not actual.get("BELIEFKV_TOOL_TIMING_ARTIFACT")
        assert actual.get("BELIEFKV_BORROWABLE_PARENT_HEADROOM", "0") == "0"
    assert ("--enable-beliefkv-admission" in payload["arguments"]) is (comparison != "native_matched")
    launch = json.loads((run / "server/launch_configuration.json").read_text())
    assert launch["internal_comparison_mode"] == comparison
    assert launch["chat_token_block_cache"] is True
    assert launch["prefetch_residency_protection_requested"] is (
        protection == "1" and custom_kv
    )


def test_disabled_post_ack_protection_reaches_the_launcher_without_a_gpu(tmp_path):
    test_internal_comparison_effective_environment_before_gpu_launch(
        tmp_path, "full", protection="0",
    )


@pytest.mark.parametrize("mode,native,host_gb", (
    ("reactive", False, "200"), ("predictive_h2d", False, "200"),
    ("off", True, "200"), ("off", True, "0"),
))
@pytest.mark.parametrize("cache_override", [None, "0", "1"])
@pytest.mark.parametrize("prefix_diagnostics", [None, "1"])
def test_effective_policy_environment_without_launching_gpu(
    tmp_path, mode, native, host_gb, cache_override, prefix_diagnostics,
):
    from sys import executable

    binary = tmp_path / "bin"
    binary.mkdir()
    capture = binary / "bash"
    capture.write_text(
        "#!/bin/sh\nexec " + executable + " -c '"
        'import json,os,sys;print(json.dumps({"environment":'
        '{k:v for k,v in os.environ.items() if k.startswith("BELIEFKV_")'
        ' or k in ("SGLANG_SOURCE_CHECKOUT","SERVER_RANDOM_SEED")},'
        '"arguments":sys.argv[1:]}))'
        "' \"$@\"\n"
    )
    capture.chmod(0o755)
    artifact = tmp_path / "model.json"
    artifact.write_text("{}")
    run_root = tmp_path / "probe"
    cache_variable = (
        "NATIVE_CHAT_TOKEN_BLOCK_CACHE" if native
        else "PREDICTIVE_CHAT_TOKEN_BLOCK_CACHE" if mode == "predictive_h2d"
        else "REACTIVE_CHAT_TOKEN_BLOCK_CACHE"
    )
    inherited_environment = {
        key: value for key, value in os.environ.items()
        if key not in (
            "NATIVE_CHAT_TOKEN_BLOCK_CACHE", "PREDICTIVE_CHAT_TOKEN_BLOCK_CACHE",
            "REACTIVE_CHAT_TOKEN_BLOCK_CACHE", "PREFETCH_PREFIX_DIAGNOSTICS",
        )
    }
    result = subprocess.run(
        ["/bin/bash", str(SCRIPT)],
        env={
            **inherited_environment, "PATH": str(binary) + os.pathsep + os.environ["PATH"],
            "RUN_ROOT": str(run_root), "AB_MODE": mode,
            "NATIVE_POLICY_BASELINE": str(int(native)),
            "HICACHE_SIZE_GB": host_gb,
            "DEVICE_FULL_TOKENS": "1798992" if host_gb == "0" else "",
            "DEVICE_MAMBA_SLOTS": "513" if host_gb == "0" else "",
            "PREPARE_HOST": "1", "ENABLE_TOOL_TIMING": "1", "PORT": "1",
            "SERVER_RANDOM_SEED": "21",
            "CONFIRMED_JOIN_CANARY": "0",
            "SEMANTIC_REPORT_ARTIFACT": str(artifact),
            "TOOL_TIMING_ARTIFACT": str(artifact),
            "TRANSFER_SERVICE_SEED": str(artifact),
            "BELIEFKV_SEMANTIC_REPORT_ARTIFACT": "/inherited/unused-semantic.json",
            "BELIEFKV_EMIT_SEMANTIC_TEXT": "1",
            "BELIEFKV_EXPERIMENT_PAUSE_FILE": str(tmp_path / "unused-pause"),
            "SGLANG_SOURCE_CHECKOUT": str(tmp_path / "candidate-engine"),
            "BELIEFKV_CHAT_TOKEN_BLOCK_CACHE": "1",
            "BELIEFKV_PREFETCH_PREFIX_DIAGNOSTICS": "1",
            **({cache_variable: cache_override} if cache_override is not None else {}),
            **({"PREFETCH_PREFIX_DIAGNOSTICS": prefix_diagnostics}
               if prefix_diagnostics is not None else {}),
        },
        text=True, capture_output=True, timeout=10,
    )
    assert result.returncode == 1
    rows = (run_root / "server.log").read_text().splitlines()
    payload = json.loads(next(line for line in rows if line.startswith("{")))
    environment, arguments = payload["environment"], payload["arguments"]
    assert environment["SGLANG_SOURCE_CHECKOUT"] == str(tmp_path / "candidate-engine")
    assert environment["SERVER_RANDOM_SEED"] == "21"
    cache_enabled = cache_override == "1" if cache_override is not None else not native
    assert environment["BELIEFKV_CHAT_TOKEN_BLOCK_CACHE"] == str(int(cache_enabled))
    assert environment["BELIEFKV_PREFETCH_PREFIX_DIAGNOSTICS"] == (
        prefix_diagnostics if prefix_diagnostics is not None else "0"
    )
    assert json.loads((run_root / "server/launch_configuration.json").read_text()) == {
        "schema_version": 1, "chat_token_block_cache": cache_enabled,
        "native_policy_baseline": native,
        "prefetch_prefix_diagnostics": prefix_diagnostics == "1",
        "prefetch_residency_protection_requested": True,
        "server_random_seed": 21,
        "internal_comparison_mode": "full",
    }
    if host_gb == "0":
        assert arguments[arguments.index("--max-total-tokens") + 1] == "1798992"
        assert arguments[arguments.index("--max-mamba-cache-size") + 1] == "513"
    else:
        assert "--max-total-tokens" not in arguments
        assert "--max-mamba-cache-size" not in arguments
    predictive = mode == "predictive_h2d"
    assert environment["BELIEFKV_EMIT_SEMANTIC_TEXT"] == "0"
    assert environment["BELIEFKV_ENABLE_FINAL_STAGE_PREFETCH"] == str(int(predictive))
    assert environment["BELIEFKV_ENABLE_TOOL_PREFETCH"] == str(int(predictive))
    assert environment["BELIEFKV_ENABLE_PREPARE_HOST"] == str(int(not native))
    assert environment["BELIEFKV_ENABLE_RESIDENT_FIRST"] == str(int(not native))
    assert "BELIEFKV_SEMANTIC_REPORT_ARTIFACT" not in environment
    assert environment.get("BELIEFKV_H2D_OPPORTUNITY_SAMPLING", "0") == "0"
    if not native:
        assert environment["BELIEFKV_TOOL_TIMING_ARTIFACT"] == str(artifact)
    if mode == "reactive":
        assert environment["BELIEFKV_H2D_OPPORTUNITY_SAMPLING"] == "0"
        assert environment["BELIEFKV_ENABLE_EXECUTION_HANDOFF"] == "0"


@pytest.mark.parametrize("mode", ["off", "reactive", "predictive_h2d"])
def test_probe_without_host_is_native_only(tmp_path, mode):
    run_root = tmp_path / "no-host"
    result = subprocess.run(
        ["bash", str(SCRIPT)],
        env={
            **os.environ, "RUN_ROOT": str(run_root), "HICACHE_SIZE_GB": "0",
            "AB_MODE": mode, "NATIVE_POLICY_BASELINE": "0",
        }, text=True, capture_output=True, timeout=5,
    )
    assert result.returncode == 2
    assert not run_root.exists()


@pytest.mark.parametrize("seed", (None, "0", "1234"))
def test_native_launcher_passes_an_explicit_seed_without_starting_sglang(tmp_path, seed):
    import sys

    model = tmp_path / "model"
    model.mkdir()
    (model / "config.json").write_text("{}")
    cuda = tmp_path / "cuda"
    (cuda / "bin").mkdir(parents=True)
    (cuda / "lib").mkdir()
    (cuda / "bin/nvcc").write_text("")
    (cuda / "bin/nvcc").chmod(0o755)
    for name in ("libcudart.so.13", "libcudart.so"):
        (cuda / "lib" / name).write_text("")
    python = tmp_path / "capture-python"
    python.write_text(
        f"#!{sys.executable}\n"
        "import json,sys\n"
        'if sys.argv[1:3] == ["-m", "sglang.launch_server"]:\n'
        "    print(json.dumps(sys.argv[3:]))\n"
    )
    python.chmod(0o755)
    environment = {
        name: value for name, value in os.environ.items()
        if not name.startswith("BELIEFKV_") and name != "SERVER_RANDOM_SEED"
    }
    environment.update({
        "PYTHON": str(python), "MODEL_PATH": str(model), "CUDA_HOME": str(cuda),
        "HICACHE_SIZE_GB": "0", "ENABLE_SESSION_RADIX_CACHE": "0",
        "SGLANG_SOURCE_CHECKOUT": "", "SGLANG_PATCH_FLAVOR": "staging",
        "SGLANG_NATIVE_SESSION_CLOSE_RECLAIM": "0", "CUDA_VISIBLE_DEVICES": "99999",
    })
    if seed is not None:
        environment["SERVER_RANDOM_SEED"] = seed
    result = subprocess.run(
        ["/bin/bash", str(SCRIPT.with_name("launch_qwen35_native_v0520.sh"))],
        env=environment, text=True, capture_output=True, timeout=5,
    )
    assert result.returncode == 0, result.stderr
    arguments = json.loads(result.stdout)
    assert arguments.count("--random-seed") == 1
    assert arguments[arguments.index("--random-seed") + 1] == (seed or "21")


@pytest.mark.parametrize("seed", ("-1", "4294967296", "invalid"))
def test_probe_rejects_invalid_server_seeds_before_creating_a_run(tmp_path, seed):
    run = tmp_path / "run"
    result = subprocess.run(
        ["/bin/bash", str(SCRIPT)],
        env={**os.environ, "RUN_ROOT": str(run), "SERVER_RANDOM_SEED": seed},
        text=True, capture_output=True, timeout=5,
    )
    assert result.returncode == 2
    assert not run.exists()
