"""Plot measured old work and the separate, not-yet-measured scope proposal."""

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts.train_workflow_work import write


def main():
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--declaration", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=False)
    review = json.loads((args.inventory / "review.json").read_bytes())
    groups = review["protocol_groups"]
    group = max(groups, key=lambda item: item["population"]["executions"])
    rows = [
        row for path in sorted(args.inventory.glob("*.json")) if path.name != "review.json"
        for row in json.loads(path.read_bytes())["workflows"]
        if row["eligible"] and row["protocol_sha256"] == group["protocol_sha256"]
    ]
    declaration = json.loads(args.declaration.read_bytes())
    plt.rcParams.update({"font.size": 10, "axes.spines.top": False, "axes.spines.right": False})
    figure, axes = plt.subplots(1, 3, figsize=(14, 4.6), constrained_layout=True)
    work = sorted(row["model_work_units"] / 1000. for row in rows)
    axes[0].step(work, [(i + 1) / len(work) for i in range(len(work))], where="post", color="#147d76")
    for quantile in ("p10", "p90"):
        axes[0].axvline(group["population"]["model_work_units"][quantile] / 1000., color="#555555", alpha=.5, linestyle=":")
    axes[0].set(
        title=f"Existing compatible pool: {len(rows)} executions\nP90/P10 = {group['population']['model_work_units']['p90_p10']:.2f}",
        xlabel="Model work proxy (thousand units)", ylabel="ECDF", ylim=(0, 1.03),
    )
    colors = ("#147d76", "#b34e5f", "#4355a4")
    for color, source in zip(colors, sorted({row["source_capture"] for row in rows})):
        selected = [row for row in rows if row["source_capture"] == source]
        label = {"locobench_parallel_retry_source_20261026_v230": "Original 18",
                 "workflow_work_train8_source_20261029_r1": "Alternative 8",
                 "workflow_work_source_20261029_matched6": "Repeat 6"}[Path(source).parent.name]
        axes[1].scatter(
            [row["initial_prompt_tokens"] / 1000. for row in selected],
            [row["model_work_units"] / 1000. for row in selected],
            s=35, alpha=.8, color=color, label=label,
        )
    axes[1].set(
        title="Measured work vs. context\n18 distinct original projects",
        xlabel="Initial prompt (thousand tokens)", ylabel="Model work proxy (thousand units)",
    )
    axes[1].legend(frameon=False, fontsize=8)
    for index, (color, scope) in enumerate(zip(colors, ("investigation", "implementation", "multi_stage"))):
        selected = [row for row in declaration["assignments"] if row["scope_hypothesis"] == scope]
        axes[2].scatter(
            [row["context_tokens"] / 1000. for row in selected], [index] * len(selected),
            color=color, s=45,
        )
    axes[2].set(
        title="New scope proposal: 6 + 6 + 6\nNot measured size tiers",
        xlabel="Frozen source context (thousand tokens)", yticks=(0, 1, 2),
        yticklabels=("Investigation", "Implement / refactor", "Multi-stage"), ylim=(-.7, 2.7),
    )
    figure.savefig(args.output_dir / "workload_mix.png", dpi=160)
    figure.savefig(args.output_dir / "workload_mix.pdf")
    plt.close(figure)
    write(args.output_dir / "evidence.json", {
        "inventory_review_sha256": sha256_file(args.inventory / "review.json"),
        "declaration_sha256": sha256_file(args.declaration), "plot_source_sha256": sha256_file(Path(__file__)),
        "measured_executions": len(rows), "scope_hypotheses_not_measured_work": True,
    })


if __name__ == "__main__":
    main()
