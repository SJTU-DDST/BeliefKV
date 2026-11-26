#!/usr/bin/env python3
"""Check the frozen cross-harness pilots on CPU, or capture one selected task."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
from hashlib import sha256
from importlib import import_module
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
from urllib.parse import urlsplit
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.sandbox_environment import PROJECT_ENVIRONMENT_KEYS, project_environment
from beliefkv.experiments.swebench_images import swebench_instance_image
from beliefkv.runtime.clock_evidence import local_monotonic_clock_domain
from beliefkv.runtime.codex_capture_container import model_socket

PILOTS = ROOT / "workloads/cross_harness_20261125"
HARNESSES = ("codex", "opencode", "openhands")
TESTBED_BIN = "/opt/miniconda3/envs/testbed/bin"
CONTAINER_REPO = "/opt/beliefkv"
CONTROL = "/opt/beliefkv-capture"
PROVIDER_SOCKET = f"{CONTAINER_REPO}/provider.sock"
PROVIDER_RELAY = f"{CONTAINER_REPO}/beliefkv/runtime/codex_capture_container.py"
ENTRY = f"{CONTAINER_REPO}/scripts/{Path(__file__).name}"
BINARIES = {"codex": f"{CONTROL}/codex", "opencode": f"{CONTROL}/opencode"}
COLLECTORS = {
    "codex": "scripts.run_codex_subagent_benchmark",
    "opencode": "scripts.run_opencode_capture",
    "openhands": "scripts.run_openhands_capture",
}
EVENTS = {
    "codex": "runtime_events.codex.jsonl",
    "opencode": "opencode-events.jsonl",
    "openhands": "runtime_events.openhands.jsonl",
}
DEFAULT_CODEX = Path(
    "/home/longhao/.vscode-server/extensions/openai.chatgpt-26.1120.50957-linux-x64"
    "/bin/linux-x86_64/codex"
)
DEFAULT_OPENCODE = Path("/tmp/beliefkv-opencode.F3UbWj/opencode")
DEFAULT_OPENHANDS = Path("/tmp/beliefkv-openhands-sdk-python312/bin/python")

# Official SWE-bench x86_64 tag resolutions for the frozen four-task pilot.
SWEBENCH_DIGESTS = {
    "sympy__sympy-13877": "e8c37ba67508d19e3f3595de6bdd8f834e518d8fb73c687ce355a894feb41a31",
    "sympy__sympy-17630": "9b839af473048e347a0f435e920bfa5206e588b59a5187bef826352ac05ae73a",
    "sympy__sympy-13878": "d0796de4f7c4749e36e32b043edc2c98b3c6f7127691af37c325944726aa7d0a",
    "sympy__sympy-17318": "dac5e27382a309b8dd1e6bc6aa4fe4dc3cb8c8384c6d5d516ea4c18e08fa06fd",
}

SWEBENCH_SMOKE = """
import pathlib, sys
import flake8, flake8_comprehensions, mpmath, sympy
assert sys.version_info[:2] == (3, 9), sys.version
assert mpmath.__version__ == "1.3.0", mpmath.__version__
assert pathlib.Path(sympy.__file__).parent.samefile("sympy"), sympy.__file__
x = sympy.Symbol("x")
assert sympy.expand((x + 1)**2) == x**2 + 2*x + 1
print("SymPy", sympy.__version__, "mpmath", mpmath.__version__, "flake8", flake8.__version__)
"""

GATEWAY = "locobench/python_api_gateway_hard_009"
LEDGERLINK = "locobench/python_api_microservice_medium_008"
SHOWTIME = "locobench/python_blockchain_nft_medium_035"
PULSESTREAM = "locobench/python_data_analytics_easy_082"
LOCO_IMPORTS = {
    GATEWAY: ("aiohttp", "hypothesis"),
    LEDGERLINK: (
        "fastapi", "sqlmodel", "asyncpg", "strawberry", "redis", "slowapi", "alembic", "ledgerlink",
    ),
    SHOWTIME: ("showtime_stash.application.rewards",),
    PULSESTREAM: ("pandas", "numpy", "pyarrow", "hypothesis", "processing.validators"),
}
LOCO_ENVIRONMENT = {
    # The package root supports ledgerlink.* without shadowing graphql-core.
    LEDGERLINK: {"PYTHONPATH": "/workspace:/workspace/ledgerlink_gateway"},
    SHOWTIME: {"PYTHONPATH": "/workspace:/workspace/showtime-stash/src"},
    PULSESTREAM: {
        "PYTHONPATH": "/workspace:/workspace/PulseStream_Insights/src",
        "PULSE_DB_ENGINE": "sqlite",
        "PULSE_SQLITE_PATH": "/tmp/pulsestream.sqlite3",
    },
}
LOCO_LIMITATIONS = {
    SHOWTIME: (
        "The Python rewards module imports. The frozen governance and NFT modules raise TypeError on import.",
        "The contract suite requires Brownie. PyPI lacks the declared brownie==1.19.4. "
        "eth-brownie==1.19.4 pins web3==5.31.3 and conflicts with the declared web3==6.8.0.",
    ),
    PULSESTREAM: (
        "The local SQLite path passes. The full lock pins botocore==1.34.3, "
        "while s3fs==2023.10.0 requires aiobotocore~=2.7.0, which requires botocore<1.31.65.",
    ),
}
IMAGE_RUNTIME_KEYS = PROJECT_ENVIRONMENT_KEYS | {
    "LANG", "TZ", "KIVY_NO_ARGS", "KIVY_NO_CONSOLELOG", "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
}


@dataclass(frozen=True)
class Cell:
    dataset: str
    dataset_name: str
    harness: str
    manifest: Path
    manifest_sha256: str
    revision: str
    workload: dict

    @property
    def instance(self):
        return self.workload["instance_id"]

    @property
    def workspace(self):
        return Path(self.workload["worktree"])

    @property
    def prompt(self):
        return Path(self.workload["prompt_file"])


def read_pinned(path, expected):
    data = path.read_bytes()
    if sha256(data).hexdigest() != expected:
        raise ValueError(f"frozen SHA-256 mismatch: {path}")
    return data


def cells(dataset, pilots=PILOTS):
    matrix = json.loads((pilots / dataset / "capture_matrix.json").read_bytes())
    fields = ("dataset", "dataset_revision", "source_manifest", "source_manifest_sha256")
    source = json.loads(read_pinned(
        Path(matrix["source_manifest"]), matrix["source_manifest_sha256"],
    ))
    if (source["dataset"] != matrix["dataset"]
            or (source.get("dataset_revision") or source["upstream_revision"]) != matrix["dataset_revision"]):
        raise ValueError(f"{dataset}: frozen source dataset or revision mismatch")
    source_rows = {row["instance_id"]: row for row in source["workloads"]}
    instances = matrix["instances"]
    if len(instances) != 4 or len(set(instances)) != 4 or set(matrix["harness_manifests"]) != set(HARNESSES):
        raise ValueError(f"{dataset}: expected four distinct tasks and the three pilot harnesses")

    result = []
    used_workspaces = set()
    for harness in HARNESSES:
        pin = matrix["harness_manifests"][harness]
        path = Path(pin["path"])
        manifest = json.loads(read_pinned(path, pin["sha256"]))
        if any(manifest[key] != matrix[key] for key in fields):
            raise ValueError(f"{path}: frozen dataset provenance mismatch")
        if [row["instance_id"] for row in manifest["workloads"]] != instances:
            raise ValueError(f"{path}: frozen task order or identity mismatch")
        for row in manifest["workloads"]:
            cell = Cell(dataset, matrix["dataset"], harness, path, pin["sha256"], matrix["dataset_revision"], row)
            original = source_rows[cell.instance]
            for key in ("repo", "base_commit", "problem_statement", "docker_image",
                        "sandbox_environment", "preflight_command"):
                if row.get(key) != original.get(key):
                    raise ValueError(f"{path}: frozen task {cell.instance} differs in {key}")
            source_repo = Path(original.get("source_repo") or source["source_repo"]).resolve()
            if Path(row["source_repo"]).resolve() != source_repo:
                raise ValueError(f"{path}: frozen task {cell.instance} differs in source checkout")
            if cell.workspace.resolve() in used_workspaces:
                raise ValueError(f"worktree is shared across cells: {cell.workspace}")
            used_workspaces.add(cell.workspace.resolve())
            result.append(cell)
    return result


def run(argv, *, timeout=30):
    result = subprocess.run(argv, text=True, capture_output=True, timeout=timeout)
    if result.returncode:
        detail = (result.stderr + "\n" + result.stdout).strip()
        raise RuntimeError(f"{argv[0]} failed ({result.returncode}): {detail[-4000:]}")
    return result.stdout.strip()


def clock_coordinates():
    namespace = os.readlink("/proc/self/ns/time")
    if os.readlink("/proc/self/ns/time_for_children") != namespace:
        raise RuntimeError("CLOCK_MONOTONIC offsets do not describe the active time namespace")
    offsets = dict(
        (name, int(seconds) * 10**9 + int(nanoseconds))
        for name, seconds, nanoseconds in (
            line.split() for line in Path("/proc/self/timens_offsets").read_text().splitlines()
        )
    )
    return {
        "domain": local_monotonic_clock_domain(),
        "namespace": namespace,
        "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
        "implementation": time.get_clock_info("monotonic").implementation,
        "monotonic_offset_ns": offsets["monotonic"],
    }


def bind_clock(host, container):
    # Linux time namespace offsets are additive to the clock from this boot.
    if not host["domain"] or not container["domain"] or any(
        host[key] != container[key] for key in ("boot_id", "implementation", "monotonic_offset_ns")
    ):
        raise RuntimeError("task container clock differs from the host CLOCK_MONOTONIC")
    return {"native": host, "container": container}


def verify_checkout(cell):
    if read_pinned(cell.prompt, cell.workload["prompt_sha256"]) != cell.workload["problem_statement"].encode("utf-8"):
        raise ValueError(f"frozen prompt bytes mismatch: {cell.prompt}")
    git = ["git", "--no-optional-locks", "-C", str(cell.workspace)]
    if run([*git, "rev-parse", "HEAD"]) != cell.workload["base_commit"]:
        raise ValueError(f"worktree base commit mismatch: {cell.workspace}")
    if run([*git, "status", "--porcelain", "--untracked-files=all"]):
        raise ValueError(f"dirty worktree: {cell.workspace}")
    common_dir = ["rev-parse", "--path-format=absolute", "--git-common-dir"]
    git_dir = Path(run([*git, *common_dir])).resolve()
    source_git = ["git", "-C", cell.workload["source_repo"]]
    if git_dir != Path(run([*source_git, *common_dir])).resolve():
        raise ValueError(f"worktree is not linked to its frozen source: {cell.workspace}")
    return git_dir


def image_reference(cell):
    if cell.dataset == "locobench":
        image = cell.workload["docker_image"]
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", image):
            raise ValueError(f"{cell.instance}: expected immutable local image ID")
        return image
    repo = swebench_instance_image(cell.instance).removesuffix(":latest")
    return f"{repo}@sha256:{SWEBENCH_DIGESTS[cell.instance]}"


def inspect_image(cell):
    reference = image_reference(cell)
    try:
        image, = json.loads(run(["docker", "image", "inspect", reference]))
    except RuntimeError as error:
        hint = f" Run docker pull --platform=linux/amd64 {reference}." if cell.dataset == "swebench" else ""
        raise RuntimeError(f"missing local image {reference}.{hint} {error}") from error
    if (image["Os"], image["Architecture"]) != ("linux", "amd64"):
        raise ValueError(f"{reference}: expected a Linux amd64 image")
    image_env = dict(setting.split("=", 1) for setting in image["Config"]["Env"])
    return reference, image["Id"], tool_environment(cell, image_env)


def tool_environment(cell, image_env):
    project = project_environment(cell.workload.get("sandbox_environment"))
    if cell.dataset == "swebench":
        project["PYTHONPATH"] = "/workspace"
    else:
        project.update(LOCO_ENVIRONMENT.get(cell.workload["repo"], {}))
    return {
        **{key: value for key, value in image_env.items() if key in IMAGE_RUNTIME_KEYS},
        **project, "PATH": f"{TESTBED_BIN}:{image_env['PATH']}",
        "CUDA_VISIBLE_DEVICES": "", "GIT_OPTIONAL_LOCKS": "0",
        "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1",
    }


def mount(source, target, *, readonly=True):
    if "," in str(source) or "," in str(target):
        raise ValueError("Docker bind paths cannot contain commas")
    return ["--mount", f"type=bind,src={source},dst={target}" + (",readonly" if readonly else "")]


def runtime_mounts(args, harness):
    venv, base = map(Path, json.loads(run([
        str(args.openhands_python), "-I", "-c",
        "import json, sys; print(json.dumps([sys.prefix, sys.base_prefix]))",
    ])))
    python = args.openhands_python if harness == "openhands" else base / "bin/python"
    mounts = [*mount(base, base), *mount(ROOT / "beliefkv", f"{CONTAINER_REPO}/beliefkv"),
              *mount(ROOT / "scripts", f"{CONTAINER_REPO}/scripts")]
    if harness == "openhands" and venv != base:
        mounts += mount(venv, venv)
    if harness in BINARIES:
        mounts += mount(getattr(args, harness + "_binary").resolve(), BINARIES[harness])
    return str(python), mounts


def shell_probe(cell, settings):
    base = shlex.quote(cell.workload["base_commit"])
    environment_tests = "".join(
        f'test "${{{key}-unset}}" = {shlex.quote(value)}\n' for key, value in settings.items()
    )
    common = (
        "set -eu\n" + environment_tests +
        f'test "$(git rev-parse HEAD)" = {base}\n'
        'test -z "$(git status --porcelain --untracked-files=all)"\n'
        f'test "$(command -v python)" = {TESTBED_BIN}/python\n'
        'test "$PWD" -ef /workspace\n'
        "command -v bash git patch >/dev/null\n"
        'python -c \'import sys; print(sys.executable, sys.version.split()[0])\'\n'
    )
    if cell.dataset == "swebench":
        return common + "python -c " + shlex.quote(SWEBENCH_SMOKE)
    repo = cell.workload["repo"]
    imports = LOCO_IMPORTS[repo]
    dependency_probe = "import " + ", ".join(imports)
    if repo == PULSESTREAM:
        dependency_probe += (
            '; from shared.db_utils import db; assert db.fetch_one("SELECT 1 AS ready") == {"ready": 1}'
            '; print("PulseStream SQLite probe passed")'
        )
    return common + "python -c " + shlex.quote(dependency_probe) + "\n" + cell.workload["preflight_command"]


def write_opencode_shell(directory, settings):
    wrapper = directory / "bash"
    wrapper.write_text(
        "#!/bin/sh\n"
        + "".join(f"export {key}={shlex.quote(value)}\n" for key, value in settings.items())
        + 'if [ "$1" = -l ]; then shift; fi\n'
        + 'exec /bin/bash --noprofile --norc "$@"\n',
        encoding="utf-8",
    )
    wrapper.chmod(0o755)
    (directory / "opencode.json").write_text(
        json.dumps({"shell": f"{CONTROL}/config/bash"}), encoding="utf-8",
    )


@contextmanager
def container(cell, args, image, settings, git_dir, *, output=None, socket=None):
    name = "beliefkv-cross-harness-" + uuid4().hex
    python, mounts = runtime_mounts(args, cell.harness)
    with tempfile.TemporaryDirectory(prefix="beliefkv-cross-harness-") as directory:
        control = Path(directory)
        # Bun and the shell tools perform a passwd lookup even with a numeric Docker user.
        passwd = control / "passwd"
        user_entry = f"capture:x:{os.getuid()}:{os.getgid()}:capture:/tmp:/bin/bash\n"
        passwd.write_text(
            ("root:x:0:0:root:/root:/bin/bash\n" if os.getuid() else "") + user_entry,
            encoding="ascii",
        )
        command = [
            "docker", "run", "--rm", "--init", "--name", name, "--pull=never",
            "--network=none", "--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges",
            "--user", f"{os.getuid()}:{os.getgid()}",
            "--tmpfs", "/tmp:rw,exec,nosuid,size=2g,mode=1777",
            "--workdir", str(cell.workspace), *mounts,
            *mount(passwd, "/etc/passwd"),
            *mount(git_dir, git_dir), *mount(cell.workspace, cell.workspace, readonly=output is None),
            *mount(cell.workspace, "/workspace", readonly=output is None),
        ]
        if cell.dataset == "swebench":
            # Replace the checkout baked into the official evaluation image.
            command += mount(cell.workspace, "/testbed", readonly=output is None)
        if cell.harness == "opencode":
            # OpenCode starts with a filtered environment and normally runs a login shell.
            config = control / "config"
            config.mkdir()
            write_opencode_shell(config, settings)
            command += mount(config, f"{CONTROL}/config")
        elif cell.harness == "openhands":
            settings = {
                **settings, "OPENHANDS_SUPPRESS_BANNER": "1",
                "LITELLM_LOCAL_MODEL_COST_MAP": "True", "XDG_CACHE_HOME": "/tmp/openhands",
            }
        if output is not None:
            command += [
                *mount(output, f"{CONTROL}/output", readonly=False),
                *mount(cell.prompt, cell.prompt), *mount(cell.manifest, cell.manifest),
                *mount(socket, PROVIDER_SOCKET),
            ]
        for key, value in settings.items():
            command += ["--env", f"{key}={value}"]
        try:
            yield [*command, "--entrypoint", python, image], python
        finally:
            subprocess.run(
                ["docker", "container", "rm", "-f", name],
                capture_output=True, timeout=15, check=False,
            )


def check_codex(script):
    for dependency in ("fastapi", "httpx", "uvicorn"):
        import_module(dependency)
    from beliefkv.runtime.codex_app_server import CodexAppServerClient, verify_codex_capture_binary

    binary = BINARIES["codex"]
    runtime = verify_codex_capture_binary(binary)
    with tempfile.TemporaryDirectory() as state, CodexAppServerClient(
        [binary, "app-server", "--stdio"], codex_home=state,
    ) as client:
        result = client.request("command/exec", {
            "command": ["/bin/bash", "--noprofile", "--norc", "-c", script],
            "cwd": os.getcwd(), "sandboxPolicy": {"type": "dangerFullAccess"},
            "timeoutMs": 20000,
        })
    if result["exitCode"]:
        raise RuntimeError(result)
    return runtime, result["stdout"]


def check_opencode(script):
    from scripts.run_opencode_capture import BINARY_SHA256, PROVIDER, REVISION, VERSION, opencode_server, verify_binary

    binary = Path(BINARIES["opencode"])
    verify_binary(binary)
    config = json.loads(Path(f"{CONTROL}/config/opencode.json").read_text())
    args = SimpleNamespace(binary=binary, model="cpu-environment-check", timeout=20,
                           context_limit=32768, output_limit=512)
    marker = "cross-harness-environment-ok"
    with tempfile.TemporaryDirectory() as state:
        directory = Path(state)
        probe = directory / "project-check.sh"
        probe.write_text(f"{script}\nprintf '%s\\n' {marker}\n")
        # The management API injects a shell command without entering a model turn.
        with opencode_server(args, directory, Path.cwd(), 1, config) as api:
            session = api.request("POST", "/session", {"title": "CPU environment check"})
            result = api.request("POST", f"/session/{session['id']}/shell", {
                "agent": "build", "command": f"/bin/bash {probe}",
                "model": {"providerID": PROVIDER, "modelID": args.model},
            })
    output = "\n".join(part["state"].get("output", "") for part in result["parts"] if part["type"] == "tool")
    if marker not in output.splitlines():
        raise RuntimeError(f"OpenCode shell probe did not complete: {output}")
    return {"version": VERSION, "source_commit": REVISION, "binary_sha256": BINARY_SHA256}, output


def openhands_packages():
    from scripts.run_openhands_capture import _setup_sdk

    packages = _setup_sdk()
    for name, pin in {"litellm": "1.80.10", "openai": "2.8.1"}.items():
        if packages[name] != pin:
            raise RuntimeError(f"OpenHands capture requires {name}=={pin}, found {packages[name]}")
    return packages


def check_openhands(script):
    from openhands.tools.terminal.definition import TerminalAction
    from openhands.tools.terminal.impl import TerminalExecutor
    from scripts.run_openhands_capture import CAPTURE_SHELL, OPENHANDS_COMMIT

    runtime = {"source_commit": OPENHANDS_COMMIT, "packages": openhands_packages()}
    shell = TerminalExecutor(
        working_dir=os.getcwd(), terminal_type="subprocess", shell_path=str(CAPTURE_SHELL),
    )
    try:
        with tempfile.TemporaryDirectory() as state:
            probe = Path(state) / "project-check.sh"
            probe.write_text(script, encoding="utf-8")
            result = shell(TerminalAction(command=f"bash {probe}", timeout=20))
        if result.exit_code != 0:
            raise RuntimeError(result.text)
        return runtime, result.text
    finally:
        shell.close()


def run_in_container(mode, harness, clock_json, *argv):
    if not Path("/.dockerenv").is_file():
        raise RuntimeError("internal entry point requires the task container")
    binding = bind_clock(json.loads(clock_json), clock_coordinates())
    if mode == "_check":
        check = {"codex": check_codex, "opencode": check_opencode, "openhands": check_openhands}
        runtime, smoke = check[harness](*argv)
        print(json.dumps({"runtime": runtime, "project_smoke": smoke.strip(), "clock_binding": binding}))
        return 0

    Path(f"{CONTROL}/output/clock_binding.json").write_text(json.dumps(binding, indent=2) + "\n")
    collector = import_module(COLLECTORS[harness])
    # These collectors label their timestamps with the clock proven above.
    collector.local_monotonic_clock_domain = lambda: binding["native"]["domain"]
    sys.argv = [collector.__file__, *argv]
    if harness == "codex":
        return collector.main(list(argv), fixture_sandbox="danger-full-access")
    if harness == "openhands":
        openhands_packages()
    return collector.main()


def check_cell(cell, args, image, settings, git_dir):
    with container(cell, args, image, settings, git_dir) as (command, _):
        return json.loads(run([
            *command, ENTRY, "_check", cell.harness, json.dumps(clock_coordinates()),
            shell_probe(cell, settings),
        ], timeout=90))


def capture_argv(cell, args):
    output = f"{CONTROL}/output/capture"
    if cell.harness == "codex":
        return [
            "--manifest", str(cell.manifest), "--instance", cell.instance,
            "--codex", BINARIES["codex"], "--run-dir", output,
            "--sglang-base-url", args.base_url, "--model", args.model,
            "--bridge-port", "0", "--context-limit", str(args.context_limit),
            "--max-completion-tokens", str(args.max_output_tokens),
            "--timeout-seconds", str(args.timeout), "--skip-gpu-monitor",
            "--original-task", "--workspace-write", "--concurrency", "1",
        ]
    if cell.harness == "opencode":
        return [
            "--binary", BINARIES["opencode"], "--config", f"{CONTROL}/config/opencode.json",
            "--workspace", str(cell.workspace), "--prompt", str(cell.prompt), "--output", output,
            "--upstream", args.base_url, "--model", args.model, "--native-metadata",
            "--context-limit", str(args.context_limit), "--output-limit", str(args.max_output_tokens),
            "--timeout", str(args.timeout),
        ]
    return [
        "capture",
        "--workspace", str(cell.workspace), "--prompt-file", str(cell.prompt), "--output", output,
        "--base-url", args.base_url, "--model", "openai/" + args.model,
        "--workflow-id", f"openhands:{args.output.name}:{uuid4().hex}", "--task-id", cell.instance,
        "--max-output-tokens", str(args.max_output_tokens),
    ]


def command_template(cell, args):
    command = shlex.join([
        sys.executable, __file__, "capture", "--dataset", cell.dataset,
        "--harness", cell.harness, "--instance", cell.instance,
        "--openhands-python", str(args.openhands_python),
        "--codex-binary", str(args.codex_binary), "--opencode-binary", str(args.opencode_binary),
    ])
    relative = shlex.quote(f"{cell.dataset}/{cell.harness}/{cell.instance}")
    return (
        f'{command} --output "$BELIEFKV_CAPTURE_ROOT"/{relative}'
        ' --base-url "$BELIEFKV_CAPTURE_BASE_URL" --model "$BELIEFKV_CAPTURE_MODEL"'
        ' --context-limit "$BELIEFKV_CAPTURE_CONTEXT_LIMIT"'
        ' --max-output-tokens "$BELIEFKV_CAPTURE_OUTPUT_TOKENS"'
    )


def cell_receipt(cell, args):
    result = {
        "pilot": cell.dataset, "dataset": cell.dataset_name, "dataset_revision": cell.revision, "harness": cell.harness,
        "instance_id": cell.instance, "base_commit": cell.workload["base_commit"],
        "worktree": str(cell.workspace), "prompt_file": str(cell.prompt),
        "prompt_sha256": cell.workload["prompt_sha256"], "manifest": str(cell.manifest),
        "manifest_sha256": cell.manifest_sha256, "image_reference": image_reference(cell),
        "events": f"capture/{EVENTS[cell.harness]}", "capture_command": command_template(cell, args),
    }
    if cell.harness == "opencode":
        result["raw_protocol"] = "capture/opencode-protocol.jsonl"
    if limitations := LOCO_LIMITATIONS.get(cell.workload["repo"]):
        result["project_limitations"] = limitations
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_subparsers(dest="mode", required=True)
    for mode in ("check", "capture"):
        command = modes.add_parser(mode)
        command.add_argument("--dataset", choices=("locobench", "swebench"), required=mode == "capture")
        command.add_argument("--harness", choices=HARNESSES, required=mode == "capture")
        command.add_argument("--instance", required=mode == "capture")
        command.add_argument("--openhands-python", type=Path, default=DEFAULT_OPENHANDS)
        command.add_argument("--codex-binary", type=Path, default=DEFAULT_CODEX)
        command.add_argument("--opencode-binary", type=Path, default=DEFAULT_OPENCODE)
    modes.choices["check"].add_argument("--report", type=Path)
    capture = modes.choices["capture"]
    capture.add_argument("--output", type=Path, required=True)
    capture.add_argument("--base-url", required=True)
    capture.add_argument("--model", required=True)
    capture.add_argument("--context-limit", type=int, required=True)
    capture.add_argument("--max-output-tokens", type=int, required=True)
    capture.add_argument("--timeout", type=float, default=7200)
    args = parser.parse_args(argv)
    selected = [
        cell for dataset in ((args.dataset,) if args.dataset else ("locobench", "swebench"))
        for cell in cells(dataset)
        if (args.harness is None or cell.harness == args.harness)
        and (args.instance is None or cell.instance == args.instance)
    ]
    if not selected:
        parser.error("no frozen cells match the selection")

    if args.mode == "capture":
        upstream = urlsplit(args.base_url)
        if (upstream.scheme, upstream.hostname, upstream.path) != ("http", "127.0.0.1", "/v1") or (
            not upstream.port or upstream.username or upstream.query or upstream.fragment
        ):
            parser.error("--base-url must be http://127.0.0.1:PORT/v1")
        if not args.model.strip() or not 0 < args.max_output_tokens < args.context_limit or not 0 < args.timeout < float("inf"):
            parser.error("require a model, positive output cap below context limit, and finite positive timeout")
        cell, = selected
        git_dir = verify_checkout(cell)
        _, image, settings = inspect_image(cell)
        args.output = args.output.resolve()
        args.output.mkdir(parents=True)
        receipt = {
            **cell_receipt(cell, args), "image_id": image, "environment": settings,
            "model": args.model, "requested_context_limit": args.context_limit,
            "client_context_limit": None if cell.harness == "openhands" else args.context_limit,
            "max_output_tokens": args.max_output_tokens,
        }
        (args.output / "capture_environment.json").write_text(json.dumps(receipt, indent=2) + "\n")
        with model_socket(args.base_url) as socket:
            with container(cell, args, image, settings, git_dir, output=args.output, socket=socket) as (command, python):
                return subprocess.call([
                    *command, PROVIDER_RELAY, str(upstream.port), python,
                    ENTRY, "_capture", cell.harness, json.dumps(clock_coordinates()), *capture_argv(cell, args),
                ])

    if args.report and args.report.exists():
        parser.error(f"preflight report already exists: {args.report}")
    results = []
    for cell in selected:
        result = cell_receipt(cell, args)
        try:
            git_dir = verify_checkout(cell)
            _, image, settings = inspect_image(cell)
            result.update(image_id=image, environment=settings)
            result.update(check_cell(cell, args, image, settings, git_dir))
            result["status"] = "ready"
        except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
            result.update(status="blocked", error=str(error))
        results.append(result)
        print(f"{result['status']} {cell.dataset}/{cell.harness}/{cell.instance}", file=sys.stderr, flush=True)
    report = json.dumps({"schema_version": 1, "scope": "cpu_environment_preflight", "cells": results}, indent=2) + "\n"
    if args.report:
        with args.report.open("x", encoding="utf-8") as stream:
            stream.write(report)
    else:
        print(report, end="")
    return int(any(row["status"] != "ready" for row in results))


if __name__ == "__main__":
    if sys.argv[1:2] in (["_check"], ["_capture"]):
        raise SystemExit(run_in_container(*sys.argv[1:]))
    raise SystemExit(main())
