"""Plot sealed repetitions against the already reviewed, different-engine V345."""

from datetime import datetime, timezone
import gzip
import importlib.util
import json
from pathlib import Path


HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location(
    "repeat_control_plot_contract", HERE / "repeat_complete_review.py",
)
REVIEW = importlib.util.module_from_spec(spec)
spec.loader.exec_module(REVIEW)
BASE = REVIEW.BASE
FIGURES = BASE.ROOT / "experiments/figures/demand_prefill_pipeline_20261028_repeat_vs_thunder"


def main():
    if FIGURES.exists():
        raise FileExistsError("preserve earlier reviewed-engine control figures")
    source_paths = (
        HERE / "repeat_r2_complete/final_index.json", HERE / "complete_review.json",
    )
    sources = [BASE.causal.load(path) for path in source_paths]
    for report in sources:
        for source, digest in report["inputs_sha256"].items():
            if BASE.sha256_file(Path(source)) != digest:
                raise ValueError("sealed complete comparison changed")
    cached_paths = (
        BASE.ROOT / "experiments/figures/demand_prefill_pipeline_20261028_r2/evidence.json.gz",
        BASE.ROOT / "experiments/figures/demand_prefill_pipeline_20261028_r1/evidence.json.gz",
    )
    repeated, original = (
        json.loads(gzip.decompress(path.read_bytes())) for path in cached_paths
    )
    arms = {
        "Full R1": repeated["arms"]["Full R1"],
        "Full R2": repeated["arms"]["Full R2"],
        "ThunderAgent decay": original["arms"]["ThunderAgent decay"],
    }
    expected = {
        "Full R1": BASE.DEMAND_PREFILL_PIPELINE_TREE,
        "Full R2": BASE.DEMAND_PREFILL_PIPELINE_TREE, "ThunderAgent decay": BASE.COMMON_TREE,
    }
    if any(arm["match_contract"]["engine_tree"] != expected[name] for name, arm in arms.items()):
        raise ValueError("control figure has an unreviewed native engine")
    from scripts import plot_causal_decode_diagnostics as plotting

    try:
        plotting.require_matching(arms)
    except ValueError as error:
        if str(error) != "unmatched comparison conditions: engine_tree":
            raise
        refusal = str(error)
    else:
        raise AssertionError("generic matching must retain its cross-engine rejection")
    plotting.require_matching({
        name: {"match_contract": {
            key: value for key, value in arm["match_contract"].items() if key != "engine_tree"
        }} for name, arm in arms.items()
    })
    FIGURES.mkdir(parents=True)
    figures = plotting.render(
        arms, FIGURES, bin_s=60,
        title="Same V311 work: Full R1/R2 / ThunderAgent decay (different engines)",
    )
    for name, field in (("time_bins.csv", "bins"), ("geometry.csv", "geometry")):
        BASE.write_csv(FIGURES / name, [
            {"arm": label, **row} for label, arm in arms.items() for row in arm[field]
        ])
    inputs = {
        str(path): BASE.sha256_file(path) for path in (
            *source_paths, *cached_paths, Path(__file__), Path(plotting.__file__),
        )
    }
    evidence = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "scope": "Sealed complete repetitions and the exact previously reviewed V345 engine pair.",
        "native_engines": expected, "generic_guard_refusal_reproduced": refusal,
        "initial_cli_exit_code": 1, "initial_cli_output_artifacts_created": False,
        "generic_matcher_changed": False, "arms": arms, "figures": figures,
        "all_normal_tails_retained": True, "inputs_sha256": inputs,
        "figure_sha256": {
            str(path): BASE.sha256_file(path) for path in FIGURES.iterdir()
        },
        "performance_accepted": False,
        "limitations": [
            "R1/R2 have identical source and engine; V345 is the disclosed previously reviewed different engine.",
            "This is a complete system comparison, not an isolated policy or native-engine ablation.",
            "All actual partial bins and normal tails remain; no smoothing or post-end zero padding.",
            "The reused control has one complete run; these plots do not establish all-baseline superiority.",
        ],
    }
    with (FIGURES / "evidence.json.gz").open("xb") as stream:
        stream.write(gzip.compress(json.dumps(evidence, indent=2, allow_nan=False).encode(), mtime=0))
    print(json.dumps({
        "figures": figures, "native_engines": expected,
        "generic_guard_refusal_reproduced": refusal, "generic_matcher_changed": False,
    }, indent=2))


if __name__ == "__main__":
    main()
