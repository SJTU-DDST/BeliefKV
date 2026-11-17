"""Record user-home allocation and final experiment storage without altering user data."""

from datetime import datetime
import json
from pathlib import Path
import pwd
import subprocess
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]


def du(*paths, sudo=False, depth=None):
    command = (["sudo", "-n"] if sudo else []) + ["nice", "-n", "19", "ionice", "-c", "3", "du", "-x", "-B1"]
    command.extend(["--max-depth=" + str(depth)] if depth is not None else ["-s"])
    command.extend(map(str, paths))
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    return [
        {"path": line.split("\t", 1)[1], "allocated_bytes": int(line.split("\t", 1)[0])}
        for line in result.stdout.splitlines()
    ]


def main():
    output = HERE / "disk_usage_after.json"
    if output.exists():
        raise FileExistsError("preserve the original final storage observation")
    started = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()
    users = du("/home", sudo=True, depth=1)
    for row in users:
        if row["path"] != "/home":
            row["home_directory_user"] = pwd.getpwuid(Path(row["path"]).stat().st_uid).pw_name
    wanted = {
        str(ROOT), str(ROOT / "experiments/raw"), str(ROOT / "experiments/archive"),
        str(ROOT / "experiments/reports"),
    }
    experiments = [row for row in du(ROOT, depth=2) if row["path"] in wanted]
    experiments += du(ROOT.parent / ".worktrees")
    shared = du("/var/lib", "/opt/downloaded_models", "/srv/ai", sudo=True)
    report = {
        "started_at": started,
        "finished_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
        "scope": "Allocated filesystem blocks by directory; not a filesystem-wide UID quota.",
        "unit": "bytes", "follow_symbolic_links": False, "cross_filesystems": False,
        "user_homes": sorted(users, key=lambda row: row["allocated_bytes"], reverse=True),
        "experiment_directories": experiments,
        "shared_directories_not_attributed_to_home_users": shared,
        "df_bytes": subprocess.check_output(["df", "-B1", str(ROOT)], text=True),
        "df_human": subprocess.check_output(["df", "-h", str(ROOT)], text=True),
        "initial_observations": {
            "worktrees_allocated_bytes": 430197551104,
            "beliefkv_allocated_bytes": 444723572736,
            "raw_allocated_bytes": 375592587264,
            "longhao_home_allocated_bytes": 958403817472,
            "df_human": "3.5T total; 3.2T used; 101G available; 97% used",
            "note": "Captured before this maintenance; concurrent writes may alter df differences.",
        },
    }
    with output.open("x") as stream:
        json.dump(report, stream, indent=2)
        stream.write("\n")
    print(json.dumps(report, indent=2), flush=True)


if __name__ == "__main__":
    main()
