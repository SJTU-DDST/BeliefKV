"""Offline hash-block synthesis and immutable tokens for published AgentX replay.

AgentX publishes lengths and block hashes, not original token content. This
adapter uses the author's per-scope hash-seeded corpus sampling principle and
the shared pretokenized ingress. The resulting benchmark tokens are fixed
across arms; no native trajectory capture is involved.
"""

from array import array
from copy import deepcopy
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import random
import sys

from beliefkv.experiments.agentx_replay import context_id, load_agentx_preparation
from beliefkv.experiments.causal_replay_tokens import CapturedReplayRequest
from beliefkv.runtime.replay_token_capture import validate_generation_parameters


def sha256_file(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _seed(*values):
    return int.from_bytes(hashlib.sha256(
        ":".join(map(str, values)).encode(),
    ).digest()[:8], "big")


def _wire(tokens):
    values = array("I", tokens)
    if sys.byteorder != "little":
        values.byteswap()
    return values.tobytes()


class AgentXTokenSynthesis:
    def __init__(self, corpus_tokens, *, seed=21, eos_token_id):
        self.corpus = array("I", corpus_tokens)
        if (
            len(self.corpus) < 128 or type(seed) is not int or not 0 <= seed < 2 ** 32
            or type(eos_token_id) is not int or not 0 <= eos_token_id < 2 ** 32
        ):
            raise ValueError("AgentX synthesis needs a token corpus, seed and terminal token")
        self.seed, self.eos = seed, eos_token_id

    def _sample(self, count, seed):
        start = random.Random(seed).randrange(len(self.corpus))
        result = array("I")
        while len(result) < count:
            take = min(count - len(result), len(self.corpus) - start)
            result.extend(self.corpus[start:start + take])
            start = 0
        return result

    @lru_cache(maxsize=8192)
    def _block(self, scope, block_size, hash_id):
        return self._sample(block_size, _seed(self.seed, scope, hash_id))

    def prompt(self, scope, block_size, call):
        if type(block_size) is not int or block_size <= 0:
            raise ValueError("AgentX block size must be positive")
        count = call["input_tokens"]
        result = array("I")
        for value in call["hash_ids"]:
            result.extend(self._block(scope, block_size, value))
            if len(result) >= count:
                break
        del result[count:]
        if len(result) < count:
            result.extend(self._sample(
                count - len(result), _seed(self.seed, scope, call["request_id"], "tail"),
            ))
        return result

    def output(self, scope, call):
        count = call["output_tokens"]
        if type(count) is not int or count <= 0:
            raise ValueError("zero-output AgentX calls need a separate stopping adaptation")
        result = self._sample(count, _seed(self.seed, scope, call["request_id"], "output"))
        result[-1] = self.eos
        return result


def prepare_agentx_tokens(prepared: Path, output: Path, synthesis, *, corpus_evidence):
    """Pack once outside measured execution; preserve lengths and hash scopes."""
    plan, _, workflows, source = load_agentx_preparation(prepared)
    if synthesis.seed != source["seed"]:
        raise ValueError("AgentX tokens must use the prepared common random seed")
    if output.exists():
        raise FileExistsError("preserve previous AgentX token bundles")
    output.mkdir(parents=True)
    index = {}
    with (output / "tokens.u32").open("xb") as stream:
        for workflow in workflows:
            wid = workflow["workflow_id"]
            namespace = "agentx:" + hashlib.sha256(wid.encode()).hexdigest()
            for agent in workflow["agents"]:
                iid = agent["invocation_id"]
                for call in agent["calls"]:
                    rid = call["request_id"]
                    prompt = synthesis.prompt(wid, workflow["block_size"], call)
                    generated = synthesis.output(wid, call)
                    offset = stream.tell()
                    prompt_raw, output_raw = _wire(prompt), _wire(generated)
                    stream.write(prompt_raw)
                    stream.write(output_raw)
                    index[rid] = {
                        "identity": [wid, iid, context_id(iid), 0],
                        "offset_bytes": offset, "prompt_tokens": len(prompt),
                        "output_tokens": len(generated),
                        "prompt_sha256": hashlib.sha256(prompt_raw).hexdigest(),
                        "output_sha256": hashlib.sha256(output_raw).hexdigest(),
                        "cache_namespace": namespace,
                    }
    if (
        index.keys() != plan.request_usage.keys()
        or any(
            (row["prompt_tokens"], row["output_tokens"]) != plan.request_usage[rid]
            for rid, row in index.items()
        )
    ):
        raise ValueError("AgentX synthesis changed the source population or token work")
    with (output / "index.json").open("x") as stream:
        json.dump(index, stream, separators=(",", ":"), allow_nan=False)
        stream.write("\n")
    manifest = {
        "schema_version": 1, "source_kind": "agentx_published_lengths_and_hashes",
        "source_plan_sha256": source["artifacts"]["plan.json"],
        "source_workflows_sha256": source["artifacts"]["workflows.json"],
        "source_full_sha256": source["source_full_sha256"],
        "requests": len(index), "seed": synthesis.seed,
        "eos_token_id": synthesis.eos,
        "prompt_tokens": sum(row["prompt_tokens"] for row in index.values()),
        "output_tokens": sum(row["output_tokens"] for row in index.values()),
        "corpus": corpus_evidence,
        "input_token_synthesis": "scope_hash_sha256_seeded_corpus_blocks_and_request_local_tail",
        "output_token_synthesis": "request_local_corpus_tokens_with_fixed_length_terminal_token",
        "stopping": "fixed_recorded_length_ignore_eos",
        "original_content_or_eos_reconstructed": False,
        "generated_output_to_future_input_overlap_reconstructed": False,
        "within_workflow_recorded_hash_sharing_retained": True,
        "cross_workflow_cache_namespaces": "independent",
        "native_source_capture_required": False,
        "original_application_correctness_verified": False,
        "gpu_qualified": False, "performance_accepted": False,
        "artifacts": {
            name: sha256_file(output / name) for name in ("index.json", "tokens.u32")
        },
    }
    with (output / "manifest.json").open("x") as stream:
        json.dump(manifest, stream, indent=2, allow_nan=False)
        stream.write("\n")
    return manifest


class AgentXTokenCorpus:
    """Bounded, thread-safe random reads of offline benchmark token bytes."""

    def __init__(self, directory, prepared):
        self.directory = Path(directory)
        plan, _, _, source = load_agentx_preparation(Path(prepared))
        self.evidence = json.loads((self.directory / "manifest.json").read_bytes())
        if (
            self.evidence.get("source_kind") != "agentx_published_lengths_and_hashes"
            or self.evidence.get("source_plan_sha256") != source["artifacts"]["plan.json"]
            or self.evidence.get("source_workflows_sha256") != source["artifacts"]["workflows.json"]
        ):
            raise ValueError("AgentX tokens belong to a different published source")
        for name in ("index.json", "tokens.u32"):
            if sha256_file(self.directory / name) != self.evidence["artifacts"][name]:
                raise ValueError("AgentX token bundle changed: " + name)
        self.index = json.loads((self.directory / "index.json").read_bytes())
        identities = {
            node.event.attributes["request_id"]: [
                node.event.workflow_id, node.event.invocation_id,
                node.event.context_id, node.event.context_epoch,
            ]
            for node in plan.nodes if node.event is not None
            and node.event.kind.value == "llm_submit"
        }
        if self.index.keys() != plan.request_usage.keys() or any(
            (row["prompt_tokens"], row["output_tokens"]) != plan.request_usage[rid]
            or row["identity"] != identities[rid]
            or row["cache_namespace"] != "agentx:" + hashlib.sha256(
                identities[rid][0].encode(),
            ).hexdigest()
            for rid, row in self.index.items()
        ):
            raise ValueError("AgentX token index changes request work")
        self.path = self.directory / "tokens.u32"
        self._file = self.path.open("rb")
        self._signature = self._stat_signature(self.path.stat())
        self._generation = {
            "temperature": 1., "top_p": 1., "top_k": 1, "min_p": 0.,
            "frequency_penalty": 0., "presence_penalty": 0., "repetition_penalty": 1.,
            "sampling_seed": self.evidence["seed"],
        }
        validate_generation_parameters(self._generation)

    @staticmethod
    def _stat_signature(stat):
        return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns

    def get(self, request_id):
        if (
            self._file.closed
            or self._stat_signature(os.fstat(self._file.fileno())) != self._signature
            or self._stat_signature(self.path.stat()) != self._signature
        ):
            raise ValueError("AgentX token bytes changed or corpus is closed")
        row = self.index[request_id]
        count, generated = row["prompt_tokens"], row["output_tokens"]
        raw = os.pread(self._file.fileno(), 4 * (count + generated), row["offset_bytes"])
        parts = raw[:4 * count], raw[4 * count:]
        if len(raw) != 4 * (count + generated) or any(
            hashlib.sha256(part).hexdigest() != row[name]
            for part, name in zip(parts, ("prompt_sha256", "output_sha256"))
        ):
            raise ValueError("AgentX request token bytes failed integrity verification")
        arrays = []
        for part in parts:
            tokens = array("I")
            tokens.frombytes(part)
            if sys.byteorder != "little":
                tokens.byteswap()
            arrays.append(tokens)
        return CapturedReplayRequest(
            request_id, tuple(row["identity"]), arrays[0], arrays[1],
            row["prompt_sha256"], row["output_sha256"],
            {
                "max_new_tokens": generated, "min_new_tokens": generated,
                "ignore_eos": True, "stop": [], "stop_regex": [], "stop_token_ids": None,
            },
            {"type": "length", "length": generated},
            None, None, row["cache_namespace"], row["cache_namespace"],
            deepcopy(self._generation),
        )

    def close(self):
        self._file.close()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()
