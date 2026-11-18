"""Use the qualified fast resource supervisor for one frozen materialization arm."""

import argparse
import importlib.util
import json
from pathlib import Path

HELPER = Path("/home/longhao/experiment/.worktrees/beliefkv-fast-resource-wait-20261115")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--watch", type=Path, required=True)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    args = parser.parse_args()
    preparation = json.loads((args.evidence_dir / "preparation.json").read_bytes())
    source = Path(preparation["fast_supervisor"])
    spec = importlib.util.spec_from_file_location("materialization_fast_supervisor", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.HERE = args.evidence_dir.resolve()
    module.watch(args.watch.resolve())


if __name__ == "__main__":
    main()
