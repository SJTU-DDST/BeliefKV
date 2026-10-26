#!/usr/bin/env python3
"""Create fixed pretokenized AgentX benchmark content entirely offline."""

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.agentx_tokens import (
    AgentXTokenSynthesis, prepare_agentx_tokens, sha256_file,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepared", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--tokenizer", type=Path, required=True)
    parser.add_argument("--corpus-text", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=21)
    args = parser.parse_args()
    if args.output_dir.exists():
        parser.error("preserve previous token bundles")
    from transformers import AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(args.tokenizer, local_files_only=True)
    tokens = tokenizer.encode(args.corpus_text.read_text(), add_special_tokens=False)
    synthesis = AgentXTokenSynthesis(tokens, seed=args.seed, eos_token_id=tokenizer.eos_token_id)
    evidence = {
        "path": str(args.corpus_text.resolve()), "sha256": sha256_file(args.corpus_text),
        "tokenizer_path": str(args.tokenizer.resolve()), "corpus_tokens": len(tokens),
        "tokenizer_files": {
            path.name: sha256_file(path)
            for path in sorted(args.tokenizer.glob("*token*.json"))
        },
        "hash_sampling_reference": (
            "SemiAnalysisAI/agentx-harness@e4bb2f835ffedbbe6b76252ea5c93caec68c5faf:"
            "src/aiperf/common/hash_id_random_generator.py"
        ),
        "ingress_adaptation": "direct_token_ids_without_chat_or_text_roundtrip",
    }
    manifest = prepare_agentx_tokens(
        args.prepared, args.output_dir, synthesis, corpus_evidence=evidence,
    )
    print(json.dumps({
        key: manifest[key] for key in (
            "requests", "prompt_tokens", "output_tokens", "native_source_capture_required",
            "original_content_or_eos_reconstructed", "gpu_qualified",
        )
    }, indent=2), flush=True)


if __name__ == "__main__":
    main()
