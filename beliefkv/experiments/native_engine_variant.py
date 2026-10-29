"""Explicit immutable native engine variants accepted by replay launchers."""

COMMON_TREE = "11b84f7c31986782be3fbe699bae2574ef092389"
TOKENCAKE_TREE = "131d175450fe2b74d31a0508c2e13c55a3750a56"
TOKENCAKE_PATCH_SHA256 = "56d7fdefec0beb0fb5277aaa1704c1bcc0e380d49ee2240ee96905c2977557a6"
LAYER_TREE = "0d407f981c5179b20859e82e7088d85589cd08b3"
LAYER_PATCH_SHA256 = "cfdb22efb69b8deecac93d4a675519d9fdd9182197bfc744eec4f43241e09237"
GROUPED_LAYER_TREE = "2ce5e5389979a718c19f80abb9cc9b381b8cff0e"
GROUPED_LAYER_PATCH_SHA256 = "8b8a8d35404d9f1c035a57645684c6758f15c3e48ee6ef83551b3e5815754612"
SIBLING_CAPACITY_TREE = "cdd58a4acbb5038207d3a5020c54d7cbcef983b9"
SIBLING_CAPACITY_PATCH_SHA256 = "5e4038068f793a46b5bdabbbc83d0d4ccf6f854fc0d7be00ea51a72a827bc997"
DEMAND_PREFILL_PIPELINE_TREE = "729aa0caa7a1ffac84463f46b3d1843d1a6a22e4"
DEMAND_PREFILL_PIPELINE_PATCH_SHA256 = "c9b3ae50490a5023e5d7fd1fb473c97b1c4c7f22923d06211bf1ea6173f86f0b"


def engine_patch(root, tree):
    from beliefkv.experiments.source_provenance import sha256_file

    if tree == TOKENCAKE_TREE:
        patch = root / "patches/sglang-tokencake-scheduler-runtime-20261028.patch"
        if sha256_file(patch) != TOKENCAKE_PATCH_SHA256:
            raise ValueError("TokenCake scheduler patch differs from the reviewed native hooks")
        return patch
    if tree == LAYER_TREE:
        patch = root / "patches/sglang-layer-handoff-runtime-20261027.patch"
        if sha256_file(patch) != LAYER_PATCH_SHA256:
            raise ValueError("layer engine patch differs from the reviewed implementation")
        return patch
    if tree == GROUPED_LAYER_TREE:
        patch = root / "patches/sglang-layer-handoff-groups-runtime-20261028.patch"
        if sha256_file(patch) != GROUPED_LAYER_PATCH_SHA256:
            raise ValueError("grouped layer engine patch differs from the reviewed implementation")
        return patch
    if tree == SIBLING_CAPACITY_TREE:
        patch = root / "patches/sglang-sibling-capacity-runtime-20261028.patch"
        if sha256_file(patch) != SIBLING_CAPACITY_PATCH_SHA256:
            raise ValueError("sibling capacity engine patch differs from the reviewed implementation")
        return patch
    if tree == DEMAND_PREFILL_PIPELINE_TREE:
        patch = root / "patches/sglang-demand-prefill-pipeline-runtime-20261028.patch"
        if sha256_file(patch) != DEMAND_PREFILL_PIPELINE_PATCH_SHA256:
            raise ValueError("native demand pipeline engine patch differs from the reviewed implementation")
        return patch
    return root / "patches/sglang-v0.5.20-beliefkv-staging.patch"
