"""Project test settings shared by CPU qualification and live sandbox commands."""

from collections.abc import Mapping


PROJECT_ENVIRONMENT_KEYS = frozenset({
    "DJANGO_SETTINGS_MODULE", "PYTHONPATH", "PYTEST_ADDOPTS",
    "QT_QPA_PLATFORM", "QT_OPENGL", "MPLBACKEND",
    "AWS_EC2_METADATA_DISABLED", "AWS_DEFAULT_REGION", "DATABASE_URL", "REDIS_URL",
    "JWT_PUBLIC_KEY",
})


def project_environment(value):
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise TypeError("sandbox_environment must be a mapping")
    result = {}
    for key, setting in value.items():
        if key not in PROJECT_ENVIRONMENT_KEYS:
            raise ValueError(f"unsupported project sandbox environment key: {key!r}")
        if not isinstance(setting, str) or "\0" in setting:
            raise ValueError("sandbox environment settings must be strings without NUL")
        result[key] = setting
    return result


def merge_project_environment(base, value):
    settings = project_environment(value)
    merged = {**base, **settings}
    if "PYTHONPATH" in settings:
        paths = [
            "/beliefkv-support", *settings["PYTHONPATH"].split(":"),
            *base.get("PYTHONPATH", "").split(":"),
        ]
        merged["PYTHONPATH"] = ":".join(dict.fromkeys(path for path in paths if path))
    return merged
