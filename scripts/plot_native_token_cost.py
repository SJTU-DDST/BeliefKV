"""Export useful-token cost shares and separately measured component gains."""

import argparse
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


def plot(accounting, graphs, moe, output, *, combined=None, combined_only=False):
    a = json.loads(accounting.read_bytes())
    phases = a["phase_costs"]
    total = a["scopes"]["common_window"]["worker_ms"]
    values = [phases["window:decode"]["worker_ms"] / total * 100,
              phases["window:prefill"]["worker_ms"] / total * 100]
    c = json.loads(combined.read_bytes()) if combined is not None else None
    if combined_only:
        if c is None:
            raise ValueError("combined-only plot requires a measured combined comparison")
        widths = sorted(map(int, c["by_width"]))
        series = []
        footnote = (
            f"Median of 3 bursts; {c['numerical']['requests']} requests per mode across "
            f"{len(c.get('source_comparisons', [None]))} width groups\n"
            "Separate component measurements; not workflow JCT gains"
        )
    else:
        g, m = (json.loads(path.read_bytes()) for path in (graphs, moe))
        widths = sorted(map(int, g["by_width"]))
        exact = [g["by_width"][str(width)]["exact_worker_cost_reduction_percent"] for width in widths]
        tuned = [m["by_width"][str(width)]["candidate_worker_cost_reduction_percent"] for width in widths]
        series = [
            ("Exact decode graph buckets", exact, "#009E73"),
            ("Tuned MoE, sparse graph buckets", tuned, "#D55E00"),
        ]
        footnote = (
            "Median of 3 unprofiled bursts; 186 requests per mode\n"
            "Each candidate measured separately; not workflow JCT gains"
        )
    plt.rcParams.update({"font.size": 10, "axes.spines.top": False, "axes.spines.right": False})
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.4), gridspec_kw={"width_ratios": [1, 2]})
    axes[0].bar(["Decode", "Prefill"], values, color=["#0072B2", "#E69F00"], width=.55)
    for index, value in enumerate(values):
        axes[0].text(index, value + 2, f"{value:.1f}%", ha="center")
    axes[0].set_ylim(0, 86)
    axes[0].set_ylabel("Worker interval share (%)")
    axes[0].set_title("Retained Full: execution cost")
    axes[0].text(.5, -.25, "477,974 outputs, arrival offsets 55.69-1638.95 s\n3.308 ms/output; includes waits and CPU work",
                 transform=axes[0].transAxes, ha="center", va="top", fontsize=9)
    x = np.arange(len(widths))
    if c is not None:
        series.append(("Exact graphs + tuned MoE", [
            c["by_width"][str(width)]["candidate_worker_cost_reduction_percent"] for width in widths
        ], "#CC79A7"))
    bar_width = .78 / len(series)
    for index, (label, costs, color) in enumerate(series):
        axes[1].bar(
            x + (index - (len(series) - 1) / 2) * bar_width,
            costs, bar_width, label=label, color=color,
        )
    axes[1].axhline(0, color="#444444", linewidth=.7)
    axes[1].set_xticks(x, [str(width) for width in widths])
    axes[1].set_xlabel("Actual batch width (captured context mix for each width)")
    axes[1].set_ylabel("Worker cost reduction (%)\nper actual output token")
    axes[1].set_ylim(
        min(-1.3, min(min(row[1]) for row in series) - 1),
        max(12, max(max(row[1]) for row in series) + 2),
    )
    axes[1].set_title("Native serving components, equal Device KV capacity")
    axes[1].legend(frameon=False, fontsize=9)
    axes[1].text(.5, -.25, footnote,
                 transform=axes[1].transAxes, ha="center", va="top", fontsize=9)
    fig.subplots_adjust(left=.07, right=.98, top=.86, bottom=.30, wspace=.38)
    output.parent.mkdir(parents=True, exist_ok=True)
    for extension in (".png", ".pdf"):
        path = output.with_suffix(extension)
        if path.exists():
            raise FileExistsError("preserve prior figures")
        fig.savefig(path, dpi=180, bbox_inches="tight")
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("accounting", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--graphs", type=Path)
    parser.add_argument("--moe", type=Path)
    parser.add_argument("--combined", type=Path)
    parser.add_argument("--combined-only", action="store_true")
    args = parser.parse_args()
    if args.combined_only and args.combined is None:
        parser.error("--combined-only requires --combined")
    if not args.combined_only and (args.graphs is None or args.moe is None):
        parser.error("separate candidate plotting requires --graphs and --moe")
    plot(
        args.accounting, args.graphs, args.moe, args.output,
        combined=args.combined, combined_only=args.combined_only,
    )


if __name__ == "__main__":
    main()
