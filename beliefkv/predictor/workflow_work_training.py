"""Workflow and progress-balanced weights for offline remaining-work learning."""

from collections import defaultdict
import math


PHASE_BALANCED = "initial_and_three_work_phases_equal_workflow_v1"
READY_BALANCED = "ready_observations_three_quarters_equal_workflow_v1"


def phase_weights(rows):
    """Future totals determine offline sampling weights, never online features."""
    import numpy as np

    groups = defaultdict(lambda: defaultdict(list))
    for index, row in enumerate(rows):
        total, attained = row["total_work"], row["attained_work"]
        if (
            not math.isfinite(total) or not math.isfinite(attained)
            or total <= 0 or not 0 <= attained <= total + 1e-6
        ):
            raise ValueError("invalid complete-work training weight")
        phase = -1 if row["snapshot"] == 0 else min(2, int(3 * attained / total))
        groups[row["workflow"]][phase].append(index)
    if not groups:
        raise ValueError("phase weights require complete workflows")
    weights = np.zeros(len(rows), dtype=np.float64)
    for phases in groups.values():
        if -1 not in phases or len(phases[-1]) != 1:
            raise ValueError("phase weights require one initial snapshot per workflow")
        for indices in phases.values():
            weights[indices] = 1. / len(groups) / len(phases) / len(indices)
    return weights


def ready_weights(rows, base):
    import numpy as np
    from beliefkv.predictor.workflow_work import R5_FEATURES

    ready_index = R5_FEATURES.index("ready")
    groups = defaultdict(list)
    for index, row in enumerate(rows):
        groups[row["workflow"]].append(index)
    weights = np.asarray(base, dtype=float).copy()
    for indices in groups.values():
        ready = [index for index in indices if rows[index]["features"][ready_index] > 0]
        if not ready:
            continue
        mass = weights[indices].sum()
        chosen = weights[ready].copy()
        weights[indices] *= .25
        weights[ready] += .75 * mass * chosen / chosen.sum()
    return weights
