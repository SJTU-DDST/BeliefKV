"""Plot complete fixed-cohort JCT and common-window decode from reviewed reports."""

import argparse
import csv
import json
import math
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts.plot_causal_decode_diagnostics import weighted_cdf
from scripts.train_workflow_work import write


# Okabe-Ito colors; explicit labels keep method colors stable across comparisons.
COLORS = {
    "Nonranking Full": "#0072B2",
    "Oracle": "#009E73",
    "Model-work Oracle": "#E69F00",
    "Continuous-work Oracle": "#009E73",
    "Learned R5": "#CC79A7",
    "SGLang native": "#999999",
    "Latest without R5": "#56B4E9",
    "Joint optimization": "#CC79A7",
    "ThunderAgent": "#D55E00",
    "Ratio first": "#009E73",
    "Ratio confirmation": "#E69F00",
}


def validate_reports(reports):
    reference = next(iter(reports.values()))
    cohort = reference["cohort"]
    measured = set(cohort["measured_workflow_ids"])
    start, end = cohort["throughput_window_offset_seconds"]
    if cohort["schema_version"] != 2 or not measured or not 0 <= start < end:
        raise ValueError("require a fixed measured cohort and a common wall-clock window")
    geometry = None
    for report in reports.values():
        jct, bins = report["measured_workflow_JCT_ms"], report["common_window_throughput_bins"]
        if (
            report["cohort"] != cohort or set(jct) != measured
            or report.get("all_measured_completed") is not True
            or report.get("normal_tails_removed") is not False
            or report.get("measured_service_output_matches_complete_receipts") is not True
            or any(type(v) not in (int, float) or not math.isfinite(v) or v <= 0 for v in jct.values())
            or not bins
        ):
            raise ValueError("require complete identical measurement members and natural tails")
        edges = [(row["start_s"], row["end_s"]) for row in bins]
        if edges[0][0] != start or edges[-1][1] != end or any(
            not a < b or index and edges[index - 1][1] != a
            for index, (a, b) in enumerate(edges)
        ):
            raise ValueError("throughput bins must partition the whole common window")
        if geometry is None:
            geometry = edges
        if geometry != edges:
            raise ValueError("all methods must use identical throughput bin boundaries")
        for row in bins:
            tokens, rate = row["decode_tokens"], row["decode_tokens_per_second"]
            if (
                type(tokens) is not int or tokens < 0
                or type(rate) not in (int, float) or not math.isfinite(rate)
                or not math.isclose(rate, tokens / (row["end_s"] - row["start_s"]), abs_tol=1e-9)
            ):
                raise ValueError("decode rate disagrees with its token count and duration")
        if not math.isclose(
            sum(row["decode_tokens"] for row in bins) / (end - start),
            report["common_window_decode_tokens_per_second"], abs_tol=1e-9,
        ):
            raise ValueError("decode bins do not conserve the reported window throughput")
    return cohort


def render(reports, paths, output):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    cohort = validate_reports(reports)
    output.mkdir(parents=True, exist_ok=False)
    start, end = cohort["throughput_window_offset_seconds"]
    fig, axes = plt.subplots(1, 3, figsize=(15, 4.6), constrained_layout=True)
    for label, report in reports.items():
        color = COLORS[label]
        values = sorted(value / 1000. for value in report["measured_workflow_JCT_ms"].values())
        mean = sum(values) / len(values)
        axes[0].step([0., *values], [0., *((i + 1) / len(values) for i in range(len(values)))],
                     where="post", color=color, label=f"{label} (mean {mean:.1f}s)")
        bins = report["common_window_throughput_bins"]
        edges = [bins[0]["start_s"], *(row["end_s"] for row in bins)]
        axes[1].stairs([row["decode_tokens_per_second"] for row in bins], edges,
                       baseline=None, color=color, label=label)
        x, y = weighted_cdf([
            (row["decode_tokens_per_second"], row["end_s"] - row["start_s"])
            for row in bins
        ])
        axes[2].step([0., *x], [0., *y], where="post", color=color, label=label)
    titles = ("Complete Measured Workflow JCT", "Common-Window Decode Throughput",
              "Time-Weighted Throughput Distribution")
    labels = ("JCT (seconds)", "Time Since First Arrival (seconds)", "Decode Tokens / Second")
    for ax, title, xlabel in zip(axes, titles, labels):
        ax.set(title=title, xlabel=xlabel)
        ax.grid(alpha=.2)
        ax.legend(fontsize=8)
    axes[0].set_ylabel("Fraction of Measured Workflows")
    axes[0].set_xlim(left=0)
    axes[0].set_ylim(0, 1.02)
    axes[1].set_ylabel("Decode Tokens / Second")
    axes[1].set_xlim(start, end)
    axes[2].set_ylabel("Fraction of Common-Window Time")
    axes[2].set_ylim(0, 1.02)
    fig.suptitle(
        f"JCT: {len(cohort['measured_workflow_ids'])} fixed workflows with complete tails; "
        f"decode: all workflows including background, [{start:.2f}, {end:.2f}] seconds\n"
        "One run per method; empirical distributions without confidence intervals",
        fontsize=10,
    )
    for suffix in ("png", "pdf"):
        fig.savefig(output / ("complete_JCT_and_decode." + suffix), dpi=180)
    plt.close(fig)
    with (output / "throughput_bins.csv").open("x", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=(
            "method", "start_s", "end_s", "decode_tokens", "decode_tokens_per_second",
        ))
        writer.writeheader()
        for label, report in reports.items():
            for row in report["common_window_throughput_bins"]:
                writer.writerow({"method": label, **{key: row[key] for key in writer.fieldnames[1:]}})
    write(output / "provenance.json", {
        "inputs_sha256": {str(path.resolve()): sha256_file(path) for path in (*paths, Path(__file__))},
        "cohort": cohort, "palette": "Okabe-Ito", "colors": {key: COLORS[key] for key in reports},
        "single_run_no_confidence_interval": True, "throughput_CDF_weight": "bin duration seconds",
        "complete_fixed_cohort_JCT": True, "decode_population": "all workflows including background",
        "background_submission_coverage": {
            key: report.get("background_submission_coverage") for key, report in reports.items()
        },
        "steady_state_established": {
            key: report.get("steady_state_established") for key, report in reports.items()
        },
        "GPU_action": False, "performance_accepted": False,
    })


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", action="append", required=True, help="METHOD=COHORT_JSON")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if os.environ.get("CUDA_VISIBLE_DEVICES") != "99999":
        raise ValueError("hide CUDA for report-only plotting")
    reports, paths = {}, []
    for argument in args.report:
        label, separator, value = argument.partition("=")
        if not separator or label not in COLORS or label in reports:
            raise ValueError("require unique named methods with stable colors")
        path = Path(value)
        reports[label] = json.loads(path.read_text())
        paths.append(path)
    render(reports, paths, args.output_dir)


if __name__ == "__main__":
    main()
