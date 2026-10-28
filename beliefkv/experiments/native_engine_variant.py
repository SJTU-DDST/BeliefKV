"""Explicit immutable native engine variants accepted by replay launchers."""

COMMON_TREE = "11b84f7c31986782be3fbe699bae2574ef092389"
LAYER_TREE = "0d407f981c5179b20859e82e7088d85589cd08b3"
LAYER_PATCH_SHA256 = "cfdb22efb69b8deecac93d4a675519d9fdd9182197bfc744eec4f43241e09237"
GROUPED_LAYER_TREE = "2ce5e5389979a718c19f80abb9cc9b381b8cff0e"
GROUPED_LAYER_PATCH_SHA256 = "8b8a8d35404d9f1c035a57645684c6758f15c3e48ee6ef83551b3e5815754612"


def engine_patch(root, tree):
    from beliefkv.experiments.source_provenance import sha256_file

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
    return root / "patches/sglang-v0.5.20-beliefkv-staging.patch"
