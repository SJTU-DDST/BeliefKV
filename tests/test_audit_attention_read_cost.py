import pytest

from scripts.audit_attention_read_cost import logical_kv_bytes


def test_logical_reads_include_layers_heads_and_both_K_and_V():
    assert logical_kv_bytes([100, 200], layers=48, kv_heads=4, head_dim=128) == 300 * 48 * 2048


@pytest.mark.parametrize("lengths,layers", [
    ([], 48), ([0], 48), ([1.5], 48), ([100], 0), ([100], True),
])
def test_invalid_geometry_is_not_silently_counted(lengths, layers):
    with pytest.raises(ValueError, match="positive integer"):
        logical_kv_bytes(lengths, layers=layers, kv_heads=4, head_dim=128)
