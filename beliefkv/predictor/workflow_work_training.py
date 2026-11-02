"""Workflow and progress-balanced weights for offline remaining-work learning."""

from collections import defaultdict
import math


PHASE_BALANCED = "initial_and_three_work_phases_equal_workflow_v1"
READY_BALANCED = "ready_observations_three_quarters_equal_workflow_v1"
PHASE_PAIRS = "phase_matched"
READY_MIXED_PAIRS = "ready_mixed_progress"


def continuous_work_targets(rows, weights):
    """Normalize complete-work labels for an offline auxiliary training head."""
    import numpy as np

    values = np.asarray([
        (row["total_work"], row["attained_work"], row["remaining_work"]) for row in rows
    ], dtype=float)
    weights = np.asarray(weights, dtype=float)
    if (
        not rows or values.shape != (len(rows), 3)
        or weights.shape != (len(rows),) or not np.isfinite(values).all()
        or not np.isfinite(weights).all() or (weights < 0).any() or weights.sum() <= 0
        or (values[:, 0] <= 0).any() or (values[:, 1:] < 0).any()
        or not np.allclose(values[:, 0], values[:, 1] + values[:, 2], atol=1e-6, rtol=0.)
    ):
        raise ValueError("continuous supervision requires finite conserved complete-work labels")
    target = np.log1p(values[:, 2])
    mean = float(np.average(target, weights=weights))
    scale = max(.25, float(np.sqrt(np.average((target - mean) ** 2, weights=weights))))
    return (target - mean) / scale, {"log_mean": mean, "log_scale": scale}


DECISION_OBSERVATION = "sealed_live_admission_competition_v1"


def decision_observation_weights(rows, base, mass):
    """Give bounded training mass to distinct observed admission states."""
    import numpy as np

    weights = np.asarray(base, dtype=float).copy()
    if (
        type(mass) not in (int, float) or not math.isfinite(mass) or not 0 < mass <= .75
        or weights.shape != (len(rows),) or not np.isfinite(weights).all()
        or (weights <= 0).any()
    ):
        raise ValueError("invalid observed-decision training mass or base weights")
    groups = defaultdict(lambda: [[], []])
    for index, row in enumerate(rows):
        groups[row["workflow"]][row.get("training_observation_kind") == DECISION_OBSERVATION].append(index)
    observed_workflows = 0
    for ordinary, observed in groups.values():
        if not observed:
            continue
        if not ordinary:
            raise ValueError("observed-decision training requires its complete original workflow")
        observed_workflows += 1
        total = weights[ordinary].sum() + weights[observed].sum()
        weights[ordinary] *= total * (1. - mass) / weights[ordinary].sum()
        weights[observed] = total * mass / len(observed)
    if not observed_workflows:
        raise ValueError("observed-decision training has no supported training workflow")
    return weights


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


def work_ranking_pairs(rows, *, seed=21, pairs_per_phase=512, mode=PHASE_PAIRS):
    """Offline labels define pair targets and strata, never additional features."""
    import numpy as np

    if type(pairs_per_phase) is not int or not 1 <= pairs_per_phase <= 4096:
        raise ValueError("invalid bounded workflow ranking pair count")
    if mode not in (PHASE_PAIRS, READY_MIXED_PAIRS):
        raise ValueError("unknown workflow ranking pair mode")
    groups = defaultdict(lambda: defaultdict(list))
    for index, row in enumerate(rows):
        total, attained, remaining = row["total_work"], row["attained_work"], row["remaining_work"]
        if (
            any(not math.isfinite(value) for value in (total, attained, remaining))
            or total <= 0 or not 0 <= attained <= total + 1e-6 or remaining < 0
            or not math.isclose(total, attained + remaining, abs_tol=1e-6)
        ):
            raise ValueError("ranking labels require complete conserved workflow work")
        phase = -1 if row["snapshot"] == 0 else min(2, int(3 * attained / total))
        groups[phase][row["workflow"]].append(index)
    if mode == READY_MIXED_PAIRS:
        from beliefkv.predictor.workflow_work import R5_FEATURES

        ready = R5_FEATURES.index("ready")
        completed = R5_FEATURES.index("completed_calls")
        for index, row in enumerate(rows):
            values = row["features"]
            if len(values) != len(R5_FEATURES):
                raise ValueError("ready ranking pairs require R5 observed features")
            if values[ready] > 0:
                groups[3][row["workflow"]].append(index)
            if row["event"] == "llm_submit" and values[completed] == 0:
                groups[4][row["workflow"]].append(index)
    rng, pairs = np.random.default_rng(seed), []
    for phase, workflows in sorted(groups.items()):
        identities = sorted(workflows)
        if len(identities) < 2:
            continue
        stratum = []
        # Uniform workflows prevent a long trace from generating more pair mass.
        for _attempt in range(pairs_per_phase):
            first, second = rng.choice(len(identities), size=2, replace=False)
            left = int(rng.choice(workflows[identities[first]]))
            right = int(rng.choice(workflows[identities[second]]))
            difference = math.log1p(rows[right]["remaining_work"]) - math.log1p(rows[left]["remaining_work"])
            if abs(difference) < .1:
                continue
            stratum.append((left, right, 1. if difference > 0 else -1., min(1., abs(difference))))
        if mode == READY_MIXED_PAIRS and stratum:
            mass = sum(pair[3] for pair in stratum)
            stratum = [(*pair[:3], pair[3] / mass) for pair in stratum]
        pairs.extend(stratum)
    if not pairs:
        raise ValueError("workflow ranking requires distinct remaining-work labels")
    return pairs
