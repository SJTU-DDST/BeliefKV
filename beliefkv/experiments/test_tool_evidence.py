"""Bounded diagnostics for real shell test commands, including child commands."""

from collections import Counter
from pathlib import Path
import re
import shlex


ENVIRONMENT_FAILURE_STATUSES = frozenset({
    "collection_or_configuration_exit", "test_setup_or_teardown_error",
    "import_or_configuration_failure_in_test_call",
})


def is_test_command(command):
    if not any(word in command for word in ("pytest", "unittest", "manage.py", "runtests.py")):
        return False
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|")
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        return False
    segments, current = [], []
    for token in [*tokens, ";"]:
        if token and all(char in ";&|" for char in token):
            segments.append(current)
            current = []
        else:
            current.append(token)
    for segment in segments:
        while segment and re.match(r"^[A-Za-z_][A-Za-z_0-9]*=", segment[0]):
            segment = segment[1:]
        if not segment:
            continue
        executable = Path(segment[0]).name
        if executable in ("pytest", "py.test"):
            return True
        if re.fullmatch(r"python(?:\d+(?:\.\d+)*)?", executable):
            args = segment[1:]
            if args[:2] in (["-m", "pytest"], ["-m", "unittest"]):
                return True
            if args and (
                Path(args[0]).name == "runtests.py"
                or Path(args[0]).name == "manage.py" and args[1:2] == ["test"]
            ):
                return True
    return False


def result_evidence(output, exit_code):
    # Only the bounded returned output is inspected, outside the serving engine.
    lines = output.splitlines()
    summary = next((
        line for line in reversed(lines)
        if re.search(r"\b\d+ (?:passed|failed|errors?|skipped|xfailed|xpassed)\b", line)
        and re.search(r"\bin \d", line)
    ), "")
    counts = Counter()
    for count, kind in re.findall(
        r"\b(\d+) (passed|failed|errors?|skipped|xfailed|xpassed)\b", summary,
    ):
        counts["error" if kind == "errors" else kind] += int(count)
    diagnostics = [
        line.strip()[:600] for line in lines
        if re.search(
            r"ERROR collecting|^E\s+.*(?:ModuleNotFoundError|ImportError|"
            r"ImproperlyConfigured|FixtureLookupError)|reserved name", line,
        )
    ]
    calls = counts["passed"] + counts["failed"] + counts["xpassed"] + counts["xfailed"]
    import_or_settings_error = any(
        re.search(r"^E\s+(?:(?:[A-Za-z_]\w*)\.)*(?:ModuleNotFoundError|ImportError|ImproperlyConfigured)\b", line)
        for line in lines
    )
    if "ERROR collecting" in output or (exit_code in (2, 4) and not calls):
        status = "collection_or_configuration_exit"
    elif counts["error"]:
        status = "test_setup_or_teardown_error"
    elif import_or_settings_error:
        status = "import_or_configuration_failure_in_test_call"
    elif calls:
        status = "test_calls_observed"
    elif exit_code == 5 or "no tests ran" in output:
        status = "no_tests_executed"
    else:
        status = "execution_not_proven_from_saved_output"
    return {
        "status": status, "summary": summary.strip(),
        "reported_counts": dict(counts), "reported_test_calls": calls,
        "diagnostics": diagnostics[:8],
        "django_configuration_error": any(
            "settings are not configured" in line for line in lines
            if line.lstrip().startswith("E ")
        ),
    }
