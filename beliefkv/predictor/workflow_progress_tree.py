"""Portable shallow trees over explicit task structure and observed progress."""

from dataclasses import dataclass
import math

from beliefkv.predictor.workflow_task_encoder import TASK_FEATURES, task_features
from beliefkv.predictor.workflow_work import R5_FEATURES


KIND = "causal_progress_shallow_trees_v1"
RATIOS = (
    ("input_per_call", "input_tokens", "completed_calls"),
    ("output_per_call", "output_tokens", "completed_calls"),
    ("tools_per_call", "tool_calls", "completed_calls"),
    ("root_output_per_call", "root_output_tokens", "root_completed_calls"),
    ("child_output_per_call", "child_output_tokens", "child_completed_calls"),
    ("returned_child_fraction", "returned_children", "children"),
    ("completed_join_fraction", "completed_joins", "joins"),
    ("completed_tool_fraction", "completed_tools", "tool_calls"),
    ("root_output_fraction", "root_output_tokens", "output_tokens"),
    ("ready_child_fraction", "ready_children", "unfinished_children"),
    ("delegation_per_child", "delegation_chars", "children"),
    ("root_calls_per_join", "root_completed_calls", "completed_joins"),
)
FEATURES = (
    tuple("log_" + name for name in R5_FEATURES)
    + tuple(name for name, _numerator, _denominator in RATIOS)
    + tuple("task_" + name for name in TASK_FEATURES)
)


@dataclass(frozen=True)
class TreeProjection:
    progress_projection: object
    task_structure: tuple


def features(progress, task):
    import numpy as np

    values, static = np.asarray(progress, dtype=float), np.asarray(task, dtype=float)
    if (
        values.shape != (len(R5_FEATURES),) or static.shape != (len(TASK_FEATURES),)
        or not np.isfinite(values).all() or not np.isfinite(static).all()
        or (values < 0).any() or (static < 0).any()
    ):
        raise ValueError("progress trees require finite observed features and task structure")
    named = dict(zip(R5_FEATURES, values))
    ratios = [
        math.log1p(named[numerator] / max(1., named[denominator]))
        for _name, numerator, denominator in RATIOS
    ]
    return np.concatenate((np.log1p(values), ratios, static))


class ProgressTreeHead:
    def __init__(self, raw):
        import numpy as np

        if not isinstance(raw, dict) or raw.keys() != {
            "kind", "features", "objective", "trees", "maximum_depth",
            "blend", "temperature", "regression_offset", "regression_scale",
        } or (
            raw["kind"] != KIND or raw["features"] != list(FEATURES)
            or raw["objective"] not in ("multiclass", "log_remaining_work")
            or type(raw["maximum_depth"]) is not int or not 1 <= raw["maximum_depth"] <= 6
            or not isinstance(raw["trees"], list) or not 1 <= len(raw["trees"]) <= 384
        ):
            raise ValueError("invalid causal progress tree contract")
        for name in ("blend", "temperature", "regression_offset", "regression_scale"):
            if type(raw[name]) not in (int, float) or not math.isfinite(raw[name]):
                raise ValueError("nonfinite progress tree calibration")
        if not 0 < raw["blend"] <= 1 or not .05 <= raw["temperature"] <= 20 or (
            not .05 <= raw["regression_scale"] <= 10
            or abs(raw["regression_offset"]) > 20
        ):
            raise ValueError("invalid progress tree calibration")
        self.objective = raw["objective"]
        self.blend, self.temperature = raw["blend"], raw["temperature"]
        self.offset, self.scale = raw["regression_offset"], raw["regression_scale"]
        self.depth = raw["maximum_depth"]
        if self.objective == "multiclass" and len(raw["trees"]) % 3:
            raise ValueError("multiclass tree head requires complete three-class rounds")
        width = max(len(tree) for tree in raw["trees"])
        if not 1 <= width <= 127:
            raise ValueError("progress tree exceeds the shallow-node budget")
        shape = len(raw["trees"]), width
        self.feature = np.full(shape, -1, dtype=np.int64)
        self.threshold = np.zeros(shape)
        self.left = np.zeros(shape, dtype=np.int64)
        self.right = np.zeros(shape, dtype=np.int64)
        self.value = np.zeros(shape)
        for index, tree in enumerate(raw["trees"]):
            if not isinstance(tree, list) or not tree:
                raise ValueError("empty progress tree")
            reached, stack = set(), [(0, 0)]
            while stack:
                node, depth = stack.pop()
                if node in reached or not 0 <= node < len(tree) or depth > self.depth:
                    raise ValueError("cyclic, disconnected, or overly deep progress tree")
                reached.add(node)
                row = tree[node]
                if not isinstance(row, list) or len(row) != 5:
                    raise ValueError("invalid progress tree node")
                feature, threshold, left, right, value = row
                if (
                    type(feature) is not int or not -1 <= feature < len(FEATURES)
                    or any(type(item) is not int for item in (left, right))
                    or any(type(item) not in (int, float) or not math.isfinite(item)
                           for item in (threshold, value))
                ):
                    raise ValueError("invalid progress tree split")
                if feature == -1:
                    if left != -1 or right != -1:
                        raise ValueError("progress tree leaves cannot reference children")
                else:
                    stack.extend(((left, depth + 1), (right, depth + 1)))
                self.feature[index, node], self.threshold[index, node] = feature, threshold
                self.left[index, node], self.right[index, node] = left, right
                self.value[index, node] = value
            if len(reached) != len(tree):
                raise ValueError("unreachable progress tree nodes")
        self.tree_index = np.arange(shape[0])

    def score(self, values):
        """Traverse all trees together; work is bounded by the declared depth."""
        import numpy as np

        values = np.asarray(values, dtype=float)
        if values.shape != (len(FEATURES),) or not np.isfinite(values).all():
            raise ValueError("invalid progress tree input")
        nodes = np.zeros(len(self.tree_index), dtype=np.int64)
        for _step in range(self.depth):
            split = self.feature[self.tree_index, nodes]
            active = split >= 0
            if not active.any():
                break
            go_left = values[np.maximum(split, 0)] <= self.threshold[self.tree_index, nodes]
            following = np.where(
                go_left, self.left[self.tree_index, nodes], self.right[self.tree_index, nodes],
            )
            nodes = np.where(active, following, nodes)
        if (self.feature[self.tree_index, nodes] >= 0).any():
            raise ValueError("progress tree traversal did not reach its leaves")
        leaves = self.value[self.tree_index, nodes]
        return leaves.reshape(-1, 3).sum(0) if self.objective == "multiclass" else float(leaves.sum())

    def probabilities(self, values, thresholds):
        import numpy as np

        score = self.score(values)
        if self.objective == "multiclass":
            logits = score / self.temperature
            probability = np.exp(logits - logits.max())
            return probability / probability.sum()
        mean = score + self.offset
        cdf = [
            .5 * math.erfc((mean - math.log1p(threshold)) / self.scale / math.sqrt(2.))
            for threshold in thresholds
        ]
        return np.asarray((cdf[0], cdf[1] - cdf[0], 1. - cdf[1]))

    def combine(self, probabilities, progress, task, thresholds):
        own = self.probabilities(features(progress, task), thresholds)
        return tuple(
            float(self.blend * left + (1. - self.blend) * right)
            for left, right in zip(own, probabilities)
        )


def project(progress_projection, text):
    return TreeProjection(progress_projection, tuple(task_features(text)))
