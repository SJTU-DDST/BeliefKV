"""Call-conditioned piecewise-exponential tool return distributions."""

from __future__ import annotations

import ast
from bisect import bisect_right
from collections import defaultdict, deque
from dataclasses import dataclass
import heapq
import math
from pathlib import PurePosixPath
import shlex
from statistics import median

from beliefkv.predictor.tool_timing_prior import tool_signature


# Seconds. Dense around the sandbox startup and the offload time budgets.
INTERVAL_STARTS = (
    0., .025, .05, .075, .1, .15, .225, .3, .4, .5, .65, .8,
    1., 1.25, 1.5, 2., 2.5, 3., 4., 5., 6., 8., 10., 15., 20., 30.,
    60., 120., 180.,
)


def _log_size(value):
    if type(value) in (int, float) and math.isfinite(value) and value >= 0:
        return math.log1p(value)
    return math.nan


_COMMAND_GROUPS = (
    frozenset({"pytest", "py.test", "runtests.py", "unittest", "tox", "jest", "vitest"}),
    frozenset({"pip", "pip3", "uv", "poetry", "npm", "yarn", "pnpm"}),
    frozenset({"make", "ninja", "cmake", "gcc", "cargo", "tsc", "compileall", "py_compile"}),
    frozenset({"cat", "head", "tail", "ls", "sed", "wc"}),
    frozenset({"find", "rg", "grep"}),
    frozenset({"git"}),
    frozenset({"curl", "wget"}),
)
COMMAND_FEATURE_COUNT = len(_COMMAND_GROUPS) + 12


class ToolCallHistory:
    """Features from calls in the same workflow that are already observable."""

    def __init__(self):
        self._active = set()
        self._shapes = defaultdict(lambda: deque(maxlen=16))
        self._inputs = defaultdict(lambda: deque(maxlen=8))
        self._all = deque(maxlen=16)

    def start(self, identity, signature, input_digest):
        if identity in self._active:
            raise ValueError("duplicate active tool call")
        shape = self._shapes.get(signature, ())
        repeated = self._inputs.get(input_digest, ()) if input_digest else ()
        features = (
            math.log1p(len(self._active)),
            self._recent_median(self._all), math.log1p(len(self._all)),
            self._recent_median(shape), math.log1p(len(shape)),
            self._recent_median(repeated), math.log1p(len(repeated)),
        )
        self._active.add(identity)
        return features

    def end(self, identity, signature, input_digest, duration_s, *, returned):
        self._active.remove(identity)
        if returned:
            self._all.append(duration_s)
            self._shapes[signature].append(duration_s)
            if input_digest:
                self._inputs[input_digest].append(duration_s)

    @staticmethod
    def _recent_median(values):
        if not values:
            return math.nan
        return math.log1p(median(values) * 1000.)


def command_work_features(command):
    """Count work descriptors from actual argv without using path identities."""
    if not isinstance(command, str) or not 0 < len(command) <= 65_536:
        return (math.nan,) * COMMAND_FEATURE_COUNT
    lexer = shlex.shlex(command, posix=True, punctuation_chars="|;&<>")
    lexer.whitespace_split = True
    try:
        words = list(lexer)
    except ValueError:
        return (math.nan,) * COMMAND_FEATURE_COUNT
    names = {PurePosixPath(word).name.lower() for word in words}
    python = any(name.startswith("python") for name in names)
    tree = None
    if python and "-c" in words:
        code_index = words.index("-c") + 1
        if code_index < len(words):
            try:
                tree = ast.parse(words[code_index])
            except (SyntaxError, ValueError, RecursionError):
                pass
    nodes = list(ast.walk(tree)) if tree is not None else ()
    return (
        *(float(bool(names & group)) for group in _COMMAND_GROUPS),
        math.log1p(len(words)),
        math.log1p(command.count("\n")),
        math.log1p(sum("::" in word for word in words)),
        math.log1p(sum(word.endswith((".py", ".js", ".ts", ".json")) for word in words)),
        math.log1p(sum(word in {"&&", "||", ";", "|"} for word in words)),
        float(python),
        float(bool(names & {"--collect-only", "--help", "--version", "--list"})),
        float(bool(names & {"install", "build", "test", "run"})),
        math.log1p(len(nodes)),
        math.log1p(sum(isinstance(node, (ast.For, ast.While, ast.comprehension)) for node in nodes)),
        math.log1p(sum(isinstance(node, ast.Call) for node in nodes)),
        math.log1p(sum(isinstance(node, (ast.Import, ast.ImportFrom)) for node in nodes)),
    )


def call_features(attrs, *, command=None):
    """Use only attributes present when TOOL_START is delivered."""
    backend, shape = tool_signature(attrs)
    previous = _log_size(attrs.get("previous_same_input_duration_ms"))
    previous_status = attrs.get("previous_same_input_status")
    return (
        backend, shape, str(attrs.get("observed_command_class") or "unknown"),
        _log_size(attrs.get("input_chars")),
        float(attrs["is_child"]) if type(attrs.get("is_child")) is bool else math.nan,
        previous if previous_status == "success" else math.nan,
        previous if previous_status == "error" else math.nan,
        *command_work_features(command),
    )


@dataclass(frozen=True)
class ToolReturnDistribution:
    """Constant event rate inside each interval, including the open tail."""

    rates: tuple[float, ...]

    def __post_init__(self):
        if len(self.rates) != len(INTERVAL_STARTS) or any(
            not math.isfinite(rate) or rate <= 0 for rate in self.rates
        ):
            raise ValueError("expected a positive finite return rate per interval")

    def _segments(self, elapsed):
        index = bisect_right(INTERVAL_STARTS, elapsed) - 1
        while index < len(self.rates):
            end = INTERVAL_STARTS[index + 1] if index + 1 < len(self.rates) else math.inf
            yield end - elapsed, self.rates[index]
            elapsed = end
            index += 1

    def survival(self, elapsed, horizon):
        if not math.isfinite(elapsed) or elapsed < 0 or not math.isfinite(horizon) or horizon < 0:
            raise ValueError("query times must be finite and nonnegative")
        hazard = 0.
        for width, rate in self._segments(elapsed):
            hazard += min(width, horizon) * rate
            if horizon <= width:
                return math.exp(-hazard)
            horizon -= width
        raise AssertionError("missing open-ended tail")

    def quantile(self, elapsed, probability):
        if not math.isfinite(elapsed) or elapsed < 0 or not 0 < probability < 1:
            raise ValueError("invalid elapsed time or quantile")
        target = -math.log1p(-probability)
        remaining = 0.
        for width, rate in self._segments(elapsed):
            if target <= width * rate:
                return remaining + target / rate
            target -= width * rate
            remaining += width
        raise AssertionError("missing open-ended tail")


class DelayedBrierWeights:
    """Exponentially weighted forecasts with one outstanding label per lane."""

    def __init__(self, experts):
        if experts < 2:
            raise ValueError("at least two forecasts are required")
        self._experts = experts
        self._free = []
        self._losses = []
        self._pending = {}

    @property
    def lanes(self):
        return len(self._losses)

    def forecast(self, identity, probabilities):
        if identity in self._pending:
            raise ValueError("duplicate forecast identity")
        probabilities = tuple(tuple(row) for row in probabilities)
        if (
            len(probabilities) != self._experts
            or not probabilities[0]
            or any(len(row) != len(probabilities[0]) for row in probabilities)
            or any(not 0 <= value <= 1 for row in probabilities for value in row)
        ):
            raise ValueError("expected matching survival horizons from each expert")
        if not self._free:
            heapq.heappush(self._free, self.lanes)
            self._losses.append([0.] * self._experts)
        lane = heapq.heappop(self._free)
        losses = self._losses[lane]
        best = min(losses)
        weights = [math.exp(-.5 * (loss - best)) for loss in losses]
        total = sum(weights)
        self._pending[identity] = lane, probabilities
        return tuple(weight / total for weight in weights)

    def resolve(self, identity, survived):
        probabilities = self._pending[identity][1]
        if len(survived) != len(probabilities[0]) or any(
            label is not None and label not in (0., 1.) for label in survived
        ):
            raise ValueError("survival labels must match the forecast horizons")
        lane, probabilities = self._pending.pop(identity)
        known = sum(label is not None for label in survived)
        if known:
            self._losses[lane] = [
                loss + sum(
                    (probability - label) ** 2
                    for probability, label in zip(row, survived) if label is not None
                ) / known
                for loss, row in zip(self._losses[lane], probabilities)
            ]
        heapq.heappush(self._free, lane)


def mixture_quantile(survivals, weights, probability, *, maximum_s):
    """Approximate the first crossing, including empirical curves with jumps."""
    if not 0 < probability < 1 or not math.isfinite(maximum_s) or maximum_s <= 0:
        raise ValueError("invalid mixture quantile query")
    target = 1. - probability

    def surviving(horizon):
        return sum(weight * survival(horizon) for weight, survival in zip(weights, survivals))

    if surviving(maximum_s) > target:
        return math.inf
    low, high = 0., maximum_s
    while high - low > .001:
        middle = (low + high) / 2.
        if surviving(middle) > target:
            low = middle
        else:
            high = middle
    return high
