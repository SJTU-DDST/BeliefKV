import pytest

from scripts.benchmark_attention_token_cost import describe_case


def test_memory_geometry_includes_k_and_v_but_only_one_layer():
    shape = describe_case({"width": 2, "context_lengths": [1024, 4096]})
    assert shape["kv_tokens"] == 5120
    assert shape["kv_bytes_per_layer"] == 5120 * 4 * 128 * 2 * 2
    assert shape["query_heads"] == 32
    assert shape["page_size"] == 1


@pytest.mark.parametrize("case", [
    {"width": 2, "context_lengths": [1024]},
    {"width": 0, "context_lengths": []},
    {"width": 1, "context_lengths": [262145]},
    {"width": 1, "context_lengths": [0]},
    {"width": 1, "context_lengths": [1.5]},
])
def test_corrupt_or_out_of_context_capture_is_rejected(case):
    with pytest.raises(ValueError, match="attention shape"):
        describe_case(case)
