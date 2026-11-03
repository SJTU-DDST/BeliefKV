"""Bounded conditional remaining-work estimates from independent training traces."""

from collections import defaultdict
import math

from beliefkv.predictor.workflow_work import (
    InitialWorkProjection, R5_FEATURES, R5_SCHEMA, before_observed_work,
)


KIND = "attained_aligned_training_references_v1"
REFERENCE_FEATURES = (
    "completed_calls", "tool_calls", "children", "returned_children", "joins",
    "completed_joins", "max_fanout", "max_depth", "completion_intents",
    "root_completed_calls", "child_completed_calls", "root_input_tokens",
    "child_input_tokens", "root_output_tokens", "child_output_tokens",
    "unfinished_children", "initial_prompt_tokens",
)
INDICES = tuple(R5_FEATURES.index(name) for name in REFERENCE_FEATURES)


def progress_projection(projection):
    import numpy as np

    if isinstance(projection, InitialWorkProjection):
        projection = projection.progress_projection
    values = np.asarray(projection, dtype=float)
    if values.ndim != 1 or not np.isfinite(values).all():
        raise ValueError("reference matching requires a finite cached task projection")
    return values / max(1e-12, float(np.linalg.norm(values)))


def fit_references(rows, model, *, maximum_states=64):
    """Offline totals are supervision; serialized references contain no graph or tokens."""
    import numpy as np

    if model.schema != R5_SCHEMA or type(maximum_states) is not int or not 2 <= maximum_states <= 64:
        raise ValueError("training references require bounded R5 observed states")
    groups = defaultdict(list)
    for row in rows:
        features = np.asarray(row["features"], dtype=float)
        if (
            features.shape != (len(R5_FEATURES),)
            or not np.isfinite(features).all() or (features < 0).any()
            or not all(math.isfinite(row[name]) for name in (
                "attained_work", "remaining_work", "total_work",
            ))
            or row["total_work"] <= 0 or row["remaining_work"] < 0
            or not math.isclose(
                row["attained_work"] + row["remaining_work"], row["total_work"], abs_tol=1e-6,
            )
            or not math.isclose(
                .01 * features[0] + features[1], row["attained_work"], abs_tol=1e-6,
            )
        ):
            raise ValueError("training references require conserved causal observed-work labels")
        groups[row["workflow"]].append(row)
    if not 4 <= len(groups) <= 1024:
        raise ValueError("reference distributions require four to 1024 independent workflows")
    references, observed, totals, tasks, sizes = [], [], [], [], []
    for _workflow, values in sorted(groups.items()):
        values = sorted(values, key=lambda row: row["snapshot"])
        attained = np.asarray([row["attained_work"] for row in values])
        text = values[0].get("task_text", values[0]["text"])
        if (
            sum(row["snapshot"] == 0 for row in values) != 1 or attained[0] != 0
            or (np.diff(attained) < -1e-6).any()
            or any(row["total_work"] != values[0]["total_work"] for row in values)
            or any(row.get("task_text", row["text"]) != text for row in values)
        ):
            raise ValueError("reference states must belong to one complete ordered original workflow")
        projection = model.task_projection(text)
        if projection is None:
            continue
        # Equal attained-work anchors do not overweight repeated tool/queue observations.
        anchors = np.linspace(0., attained[-1], maximum_states)
        indices = np.unique(np.maximum(0, np.searchsorted(attained, anchors, side="right") - 1))
        selected = [values[int(index)] for index in indices]
        sizes.append(len(selected))
        numeric = np.log1p(np.asarray([row["features"] for row in selected])[:, INDICES])
        pad = maximum_states - len(selected)
        references.append(np.pad(numeric, ((0, pad), (0, 0)), mode="edge"))
        observed.append([row["attained_work"] for row in selected] + [attained[-1]] * pad)
        totals.append(values[0]["total_work"])
        tasks.append(progress_projection(projection).tolist())
    if len(references) < 4:
        raise ValueError("too few training workflows have supported task projections")
    references = np.asarray(references)
    center = np.mean([group[:size].mean(axis=0) for group, size in zip(references, sizes)], axis=0)
    variance = np.mean([
        ((group[:size] - center) ** 2).mean(axis=0) for group, size in zip(references, sizes)
    ], axis=0)
    scale = np.maximum(.5, np.sqrt(variance))
    return {
        "kind": KIND, "features": list(REFERENCE_FEATURES), "maximum_states": maximum_states,
        "center": center.tolist(), "scale": scale.tolist(), "states": references.tolist(),
        "attained": np.asarray(observed).tolist(), "totals": totals,
        "tasks": tasks, "sizes": sizes, "work_thresholds": list(model.thresholds),
        "input_contract": "cached_task_projection_and_causal_R5_observations",
        "reference_weighting": "one_conditional_vote_per_independent_training_workflow",
    }


class WorkflowWorkReferences:
    def __init__(self, raw):
        import numpy as np

        self.raw = raw
        if raw.get("kind") != KIND or raw.get("features") != list(REFERENCE_FEATURES):
            raise ValueError("unknown conditional workflow reference contract")
        self.maximum_states = raw["maximum_states"]
        self.states, self.attained, self.totals, self.tasks, self.sizes, self.center, self.scale = (
            np.asarray(raw[name], dtype=float)
            for name in ("states", "attained", "totals", "tasks", "sizes", "center", "scale")
        )
        self.thresholds = tuple(raw["work_thresholds"])
        count, dimension = len(self.totals), len(REFERENCE_FEATURES)
        if (
            type(self.maximum_states) is not int or not 2 <= self.maximum_states <= 64
            or not 4 <= count <= 1024
            or self.states.shape != (count, self.maximum_states, dimension)
            or self.attained.shape != (count, self.maximum_states)
            or self.tasks.ndim != 2 or self.tasks.shape[0] != count
            or self.totals.shape != (count,)
            or self.sizes.shape != (count,) or (self.sizes != np.floor(self.sizes)).any()
            or (self.sizes < 1).any() or (self.sizes > self.maximum_states).any()
            or self.center.shape != (dimension,) or self.scale.shape != (dimension,)
            or any(not values.size or not np.isfinite(values).all() for values in (
                self.states, self.attained, self.totals, self.tasks, self.sizes, self.center, self.scale,
            ))
            or (self.scale <= 0).any() or (self.totals <= 0).any()
            or (self.attained < 0).any() or (np.diff(self.attained, axis=1) < -1e-6).any()
            or (self.attained > self.totals[:, None] + 1e-6).any()
            or (self.attained[:, 0] != 0).any()
            or len(self.thresholds) != 2 or not 0 < self.thresholds[0] < self.thresholds[1]
            or not all(math.isfinite(value) for value in self.thresholds)
        ):
            raise ValueError("invalid conditional workflow reference geometry or values")
        self.states = (self.states - self.center) / self.scale

    def predict(self, projection, features, *, bandwidth=.7, task_weight=1., neighbors=8):
        import numpy as np

        features = np.asarray(features, dtype=float)
        if (
            features.shape != (len(R5_FEATURES),) or not np.isfinite(features).all()
            or (features < 0).any()
            or type(bandwidth) not in (int, float) or not math.isfinite(bandwidth) or bandwidth <= 0
            or type(task_weight) not in (int, float) or not math.isfinite(task_weight) or task_weight < 0
            or type(neighbors) is not int or not 4 <= neighbors <= 64
        ):
            raise ValueError("invalid observed reference query or bounded kernel")
        attained = .01 * features[0] + features[1]
        survivors = self.totals > attained + 1e-6
        if survivors.sum() < 4:
            return None
        # Match each training execution at the query's actually observed service amount.
        positions = np.minimum(
            (self.attained <= attained + 1e-6).sum(axis=1) - 1, self.sizes.astype(int) - 1,
        )
        numeric = (np.log1p(features[list(INDICES)]) - self.center) / self.scale
        distance = ((self.states[np.arange(len(self.totals)), positions] - numeric) ** 2).mean(axis=1)
        task = progress_projection(projection)
        if task.shape != self.tasks.shape[1:]:
            raise ValueError("conditional workflow task projection dimension changed")
        distance += task_weight * np.maximum(0., 1. - self.tasks @ task)
        eligible = np.flatnonzero(survivors)
        selected = eligible[np.argsort(distance[eligible], kind="stable")[:neighbors]]
        weights = np.exp(-(distance[selected] - distance[selected].min()) / (2. * bandwidth ** 2))
        weights /= weights.sum()
        labels = np.searchsorted(self.thresholds, self.totals[selected] - attained, side="right")
        probabilities = .95 * np.bincount(labels, weights=weights, minlength=3) + .05 / 3.
        return tuple(float(value) for value in probabilities)


class ReferenceWorkCandidate:
    """Offline candidate adapter; the serving model is unchanged until explicitly exported."""

    def __init__(self, base, references, *, blend, bandwidth, task_weight, progress_only):
        if type(blend) not in (int, float) or not 0 < blend <= 1 or type(progress_only) is not bool:
            raise ValueError("invalid conditional reference blend")
        if references.thresholds != base.thresholds:
            raise ValueError("reference and progress work units differ")
        self.base, self.references = base, references
        self.blend, self.bandwidth = blend, bandwidth
        self.task_weight, self.progress_only = task_weight, progress_only

    def __getattr__(self, name):
        return getattr(self.base, name)

    def predict(self, projection, features):
        base = self.base.predict(projection, features)
        if self.progress_only and before_observed_work(features):
            return base
        conditional = self.references.predict(
            projection, features, bandwidth=self.bandwidth, task_weight=self.task_weight,
        )
        if conditional is None:
            return base
        return tuple((1. - self.blend) * a + self.blend * b for a, b in zip(base, conditional))


class SurvivalWorkCandidate:
    """Condition the learned initial class prior on actual observed service survival."""

    def __init__(self, base, totals, *, blend, after_short_threshold):
        import numpy as np

        self.base, self.totals = base, np.asarray(totals, dtype=float)
        self.blend, self.after_short_threshold = blend, after_short_threshold
        if (
            self.totals.ndim != 1 or len(self.totals) < 4
            or not np.isfinite(self.totals).all() or (self.totals <= 0).any()
            or type(blend) not in (int, float) or not math.isfinite(blend) or not 0 < blend <= 1.
            or type(after_short_threshold) is not bool
        ):
            raise ValueError("invalid initial-prior service-survival correction")
        self.classes = np.searchsorted(base.thresholds, self.totals, side="right")
        self.counts = np.bincount(self.classes, minlength=3)
        if (self.counts < 2).any():
            raise ValueError("survival correction needs independent training support in every work class")

    def __getattr__(self, name):
        return getattr(self.base, name)

    def predict(self, projection, features):
        import numpy as np

        original = self.base.predict(projection, features)
        attained = .01 * features[0] + features[1]
        if attained <= (self.base.thresholds[0] if self.after_short_threshold else 0.):
            return original
        surviving = self.totals > attained + 1e-6
        if surviving.sum() < 4:
            return original
        prior = self.base.predict(projection, [0.] * len(R5_FEATURES))
        mass = np.asarray(prior)[self.classes] / self.counts[self.classes]
        labels = np.searchsorted(self.base.thresholds, self.totals[surviving] - attained, side="right")
        conditional = np.bincount(labels, weights=mass[surviving], minlength=3)
        surviving_mass = conditional.sum()
        if not np.isfinite(surviving_mass) or surviving_mass <= 1e-12:
            return original
        conditional /= surviving_mass
        return tuple((1. - self.blend) * a + self.blend * b for a, b in zip(original, conditional))
