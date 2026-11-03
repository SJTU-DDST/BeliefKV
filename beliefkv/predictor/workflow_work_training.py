"""Workflow and progress-balanced weights for offline remaining-work learning."""

from collections import defaultdict
import math


PHASE_BALANCED = "initial_and_three_work_phases_equal_workflow_v1"
READY_BALANCED = "ready_observations_three_quarters_equal_workflow_v1"
PHASE_PAIRS = "phase_matched"
READY_MIXED_PAIRS = "ready_mixed_progress"


def project_task_weights(rows, weights):
    """Give projects equal mass and divide that mass equally across their original tasks."""
    import numpy as np

    result = np.asarray(weights, dtype=float).copy()
    if (
        not rows or result.shape != (len(rows),) or not np.isfinite(result).all()
        or (result <= 0).any()
    ):
        raise ValueError("project/task balancing requires finite positive complete-workflow weights")
    projects, ownership = defaultdict(lambda: defaultdict(list)), {}
    for index, row in enumerate(rows):
        project, workflow = row.get("project"), row.get("workflow")
        if (
            type(project) is not str or not project or type(workflow) is not str or not workflow
            or workflow in ownership and ownership[workflow] != project
        ):
            raise ValueError("a complete training workflow must have one explicit project owner")
        ownership[workflow] = project
        projects[project][workflow].append(index)
    for workflows in projects.values():
        target = 1. / len(projects) / len(workflows)
        for indices in workflows.values():
            result[indices] *= target / result[indices].sum()
    return result


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


def remaining_call_counts(rows):
    """Derive offline root/child call labels from complete causal snapshot sequences."""
    import numpy as np
    from beliefkv.predictor.workflow_work import R5_FEATURES

    indices = [R5_FEATURES.index(name) for name in (
        "completed_calls", "root_completed_calls", "child_completed_calls",
    )]
    groups = defaultdict(list)
    for position, row in enumerate(rows):
        identity, snapshot, features = row.get("workflow"), row.get("snapshot"), row["features"]
        if (
            type(identity) is not str or not identity or type(snapshot) is not int or snapshot < 0
            or len(features) != len(R5_FEATURES)
            or any(not math.isfinite(value) or value < 0 for value in features)
            or any(not math.isfinite(row[name]) or row[name] < 0
                   for name in ("total_work", "attained_work", "remaining_work"))
            or row["total_work"] <= 0
            or not math.isclose(
                row["total_work"], row["attained_work"] + row["remaining_work"],
                abs_tol=1e-6, rel_tol=0.,
            )
        ):
            raise ValueError("remaining-call labels require complete R5 workflow snapshots")
        counts = tuple(features[index] for index in indices)
        if any(value != int(value) for value in counts) or counts[0] != counts[1] + counts[2]:
            raise ValueError("observed root/child completions must conserve integral logical calls")
        groups[identity].append((snapshot, position, counts))
    if not groups:
        raise ValueError("remaining-call supervision requires complete workflows")
    targets = np.zeros((len(rows), 2), dtype=float)
    final_counts = {}
    for identity, values in groups.items():
        values.sort()
        if [value[0] for value in values] != list(range(len(values))):
            raise ValueError("remaining-call labels require every original snapshot exactly once")
        final_row, final = rows[values[-1][1]], values[-1][2]
        if (
            final[0] <= 0 or not math.isclose(final_row["remaining_work"], 0., abs_tol=1e-6)
            or not math.isclose(final_row["total_work"], final_row["attained_work"], abs_tol=1e-6)
            or values[0][2] != (0., 0., 0.) or rows[values[0][1]]["attained_work"] != 0.
        ):
            raise ValueError("remaining-call labels require the complete natural model-work tail")
        previous = (0., 0., 0.)
        for _snapshot, position, counts in values:
            if any(current < old for current, old in zip(counts, previous)):
                raise ValueError("logical completion counts cannot go backwards")
            targets[position] = (final[1] - counts[1], final[2] - counts[2])
            previous = counts
        final_counts[identity] = {
            "total": int(final[0]), "root": int(final[1]), "child": int(final[2]),
        }
    return targets, final_counts


def remaining_call_targets(rows, weights):
    import numpy as np

    counts, _finals = remaining_call_counts(rows)
    weights = np.asarray(weights, dtype=float)
    if (
        weights.shape != (len(rows),) or not np.isfinite(weights).all()
        or (weights < 0).any() or weights.sum() <= 0
    ):
        raise ValueError("remaining-call normalization requires finite positive training mass")
    target = np.log1p(counts)
    center = np.average(target, axis=0, weights=weights)
    scale = np.maximum(.25, np.sqrt(np.average((target - center) ** 2, axis=0, weights=weights)))
    return (target - center) / scale, {"log_mean": center.tolist(), "log_scale": scale.tolist()}


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


def work_ranking_pairs(
    rows, *, seed=21, pairs_per_phase=512, mode=PHASE_PAIRS, balance_projects=False,
):
    """Offline labels define pair targets and strata, never additional features."""
    import numpy as np

    if type(pairs_per_phase) is not int or not 1 <= pairs_per_phase <= 4096:
        raise ValueError("invalid bounded workflow ranking pair count")
    if mode not in (PHASE_PAIRS, READY_MIXED_PAIRS):
        raise ValueError("unknown workflow ranking pair mode")
    if type(balance_projects) is not bool:
        raise ValueError("project-balanced ranking must be explicitly enabled")
    groups = defaultdict(lambda: defaultdict(list))
    owners = {}
    for index, row in enumerate(rows):
        total, attained, remaining = row["total_work"], row["attained_work"], row["remaining_work"]
        if (
            any(not math.isfinite(value) for value in (total, attained, remaining))
            or total <= 0 or not 0 <= attained <= total + 1e-6 or remaining < 0
            or not math.isclose(total, attained + remaining, abs_tol=1e-6)
        ):
            raise ValueError("ranking labels require complete conserved workflow work")
        if balance_projects:
            project = row.get("project")
            if (
                type(project) is not str or not project
                or row["workflow"] in owners and owners[row["workflow"]] != project
            ):
                raise ValueError("project-balanced ranking requires consistent training project owners")
            owners[row["workflow"]] = project
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
        project_workflows = defaultdict(list)
        if balance_projects:
            for identity in identities:
                project_workflows[owners[identity]].append(identity)
            project_ids = sorted(project_workflows)
            if len(project_ids) < 2:
                continue
        stratum = []
        # Uniform workflows prevent a long trace from generating more pair mass.
        for _attempt in range(pairs_per_phase):
            if balance_projects:
                first, second = rng.choice(len(project_ids), size=2, replace=False)
                first = str(rng.choice(project_workflows[project_ids[first]]))
                second = str(rng.choice(project_workflows[project_ids[second]]))
            else:
                first, second = rng.choice(len(identities), size=2, replace=False)
                first, second = identities[first], identities[second]
            left = int(rng.choice(workflows[first]))
            right = int(rng.choice(workflows[second]))
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
