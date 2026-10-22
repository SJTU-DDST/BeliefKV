"""Compatibility aliases needed by older SWE-bench Python repositories."""

import collections
import collections.abc
import os


for _name in (
    "Callable",
    "Container",
    "Hashable",
    "ItemsView",
    "Iterable",
    "Iterator",
    "KeysView",
    "Mapping",
    "MappingView",
    "MutableMapping",
    "MutableSequence",
    "MutableSet",
    "Sequence",
    "Set",
    "Sized",
    "ValuesView",
):
    if _name not in collections.__dict__:
        setattr(collections, _name, getattr(collections.abc, _name))

_experiment_seed = os.environ.get("BELIEFKV_SANDBOX_RANDOM_SEED")
if _experiment_seed is not None:
    import random

    random.seed(int(_experiment_seed))
