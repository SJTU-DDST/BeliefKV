import pytest

from scripts.pilot_cold_child_tool_long import transfer_pilot


def test_transfer_pilot_requires_project_disjoint_data():
    call = {
        "project": "pydata", "class": "test",
        "input_chars": 100, "duration_ms": 3000.,
    }
    with pytest.raises(ValueError, match="project-disjoint"):
        transfer_pilot([call], [call])
    with pytest.raises(ValueError, match="insufficient"):
        transfer_pilot([call], [{**call, "project": "astropy"}])
