from __future__ import annotations

def repository_sandbox_contract(repo: str) -> str:
    """Describe the mounted checkout without leaking another repository's layout."""

    common = f"""
Workload-specific repository contract:
- Repository identity is `{repo}`, but its checkout root is still exactly
  `/workspace`. Do not append `{repo}` to `/workspace`.
"""
    if repo == "django/django":
        return common + """- Django source paths such as `django/db/backends/base/base.py` map to
  `/workspace/django/db/backends/base/base.py`, not
  `/workspace/django/django/db/backends/base/base.py`.
- Run focused Django tests from `/workspace` with the repository test runner, for
  example `python tests/runtests.py <test_label>`.
"""
    if repo == "sympy/sympy":
        return common + """- SymPy source paths such as `sympy/core/basic.py` map to
  `/workspace/sympy/core/basic.py`, not `/workspace/sympy/sympy/core/basic.py`.
- Run focused SymPy tests with `python bin/test <test-path>`. This runner accepts
  `-k <test-name>` or a complete test-file path; do not use a pytest-style `::` selector.
"""
    return common + """- Inspect the checkout's top-level files before choosing a package path or test
  command. Prefer paths returned by repository search tools over paths inferred from
  the repository slug.
"""


def build_swebench_task_prompt(
    *,
    instance_id: str,
    repo: str,
    base_commit: str,
    problem_statement: str,
) -> str:
    return (
        f"SWE-bench instance: {instance_id}\n"
        f"Repository: {repo}\n"
        f"Base commit: {base_commit}\n\n"
        f"Problem statement:\n{problem_statement}\n\n"
        f"{repository_sandbox_contract(repo)}\n"
        "Produce a complete working patch for every stated requirement and run a "
        "focused repository test command before reporting success."
    )
