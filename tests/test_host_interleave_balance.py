"""Bounded physical placement skew never permits missing or foreign pages."""

import pytest

from beliefkv.experiments import host_numa as numa
from tests.test_host_pool_interleave import mapping


@pytest.mark.parametrize("shift,accepted", ((0, True), (1, True), (4, True), (5, True), (6, False)))
def test_explicit_one_percent_total_page_imbalance_bound(shift, accepted):
    page = 4096
    maps, pages = mapping(1000 * page)
    pages = pages.replace("N0=500 N1=500", f"N0={500 - shift} N1={500 + shift}")
    if not accepted:
        with pytest.raises(ValueError, match="evenly interleaved"):
            numa.inspect_mapping(maps, pages, expected_bytes=1000 * page, node=1, page_bytes=page, memory_nodes=[0, 1])
    else:
        proof = numa.inspect_mapping(
            maps, pages, expected_bytes=1000 * page, node=1, page_bytes=page, memory_nodes=[0, 1],
        )
        assert proof["maximum_node_page_imbalance_fraction"] == .01
        assert proof["actual_node_page_imbalance_fraction"] == 2 * shift / 1000
        assert proof["remote_pool_fraction"] == (500 - shift) / 1000


def test_measured_unpinned_400gb_snapshot_with_missing_accounting_still_cannot_qualify():
    size = 400000057344
    maps, _pages = mapping(size)
    line = (
        maps.split("-")[0] + " interleave:0-1 file=/dev/zero\\040(deleted) "
        "dirty=97656256 active=27 N0=48623089 N1=49033167 kernelpagesize_kB=4\n"
    )
    with pytest.raises(ValueError, match="complete Host pool must be resident"):
        numa.inspect_mapping(maps, line, expected_bytes=size, node=1, page_bytes=4096, memory_nodes=[0, 1])
