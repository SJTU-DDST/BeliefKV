from array import array

import pytest

from scripts.audit_live_kv_demand import prefix_union, reusable_checkpoint


def row(ids, namespace="a"):
    tokens = array("I", ids)
    return namespace, tokens.itemsize, tokens.tobytes()


def test_live_union_deduplicates_shared_prefix_and_subsequence_only_when_prefix():
    assert prefix_union([row([1, 2, 3]), row([1, 2]), row([1, 2, 4])]) == 4
    assert prefix_union([row([1, 2]), row([2, 3])]) == 4


def test_native_namespaces_remain_distinct_and_partial_token_byte_match_is_not_a_token():
    assert prefix_union([row([1, 2]), row([1, 2], "b")]) == 4
    assert prefix_union([row([1]), row([257])]) == 2


def test_corrupt_partial_token_storage_is_rejected():
    with pytest.raises(ValueError, match="complete fixed-width"):
        prefix_union([("a", 4, b"abc")])


def test_reusable_old_prefix_excludes_compaction_suffix_and_no_future_model_call():
    assert reusable_checkpoint(row([1, 2, 3]), row([1, 2, 4])) == row([1, 2])
    assert reusable_checkpoint(row([1, 2, 3]), None) == row([])
    assert reusable_checkpoint(row([1, 2]), row([1, 2, 3])) == row([1, 2])


def test_reusable_old_prefix_respects_native_namespace_and_complete_tokens():
    assert reusable_checkpoint(row([1]), row([1], "b")) == row([])
    assert reusable_checkpoint(row([1]), row([257])) == row([])
    with pytest.raises(ValueError, match="representation changed"):
        reusable_checkpoint(row([1]), ("a", 8, b"\0" * 8))
