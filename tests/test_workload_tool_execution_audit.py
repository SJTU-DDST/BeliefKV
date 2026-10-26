import pytest

from scripts.audit_workload_tool_execution import is_test_command, result_evidence


@pytest.mark.parametrize(("command", "expected"), [
    ("cd /workspace/project && python -m pytest tests", True),
    ("PYTHONPATH=/workspace/service/src:$PYTHONPATH python -m pytest -q", True),
    ("pytest tests/test_one.py", True),
    ("python manage.py test billing", True),
    ("python tests/runtests.py billing", True),
    ("python -m unittest discover", True),
    ("pip install pytest moto pandas", False),
    ("grep -n pytest requirements.txt", False),
    ("python -c 'print(\"pytest tests\")'", False),
    ("echo 'python -m pytest tests'", False),
])
def test_audit_distinguishes_test_execution_from_mentions(command, expected):
    assert is_test_command(command) is expected


def test_collection_failure_is_not_real_test_execution():
    result = result_evidence(
        "collected 38 items / 1 error\nERROR collecting tests/test_main.py\n"
        "E   ModuleNotFoundError: No module named 'web_dashboard'\n"
        "================ 1 error in 0.65s =================\n", 2,
    )
    assert result["status"] == "collection_or_configuration_exit"
    assert result["reported_test_calls"] == 0
    assert len(result["diagnostics"]) == 2


def test_failing_assertions_remain_real_executed_work():
    result = result_evidence("======== 1 failed, 2 passed in 0.76s ========\n", 1)
    assert result["status"] == "test_calls_observed"
    assert result["reported_test_calls"] == 3
    assert result["reported_counts"] == {"failed": 1, "passed": 2}


def test_django_configuration_marker_requires_actual_error_output():
    diagnostic = (
        "E   django.core.exceptions.ImproperlyConfigured: Requested setting REST_FRAMEWORK, "
        "but settings are not configured.\n1 error in 0.92s"
    )
    assert result_evidence(diagnostic, 2)["django_configuration_error"] is True
    assert result_evidence(
        "source example: settings are not configured.\n1 passed in 0.4s", 0,
    )["django_configuration_error"] is False
