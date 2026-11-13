"""Keep a GPU device accessible to one campaign across model restarts."""

import argparse
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import time


def write(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.replace(path)


def process(pid):
    try:
        fields = Path(f"/proc/{int(pid)}/stat").read_text().rsplit(")", 1)[1].split()
        return {"pid": int(pid), "start_ticks": fields[19], "state": fields[0]}
    except (FileNotFoundError, ProcessLookupError):
        return None


def alive(owner):
    current = process(owner["pid"])
    return bool(
        current and current["state"] not in ("Z", "X")
        and current["start_ticks"] == owner["start_ticks"]
    )


class DeviceAccess:
    def __init__(self, device, uid, *, run=subprocess.run):
        self.device = Path(device)
        self.uid = int(uid)
        self.run = run
        self.original = None
        self.applied = None
        self.identity = None

    def command(self, arguments, **kwargs):
        return self.run(arguments, check=True, text=True, capture_output=True, **kwargs)

    def acl(self):
        return self.command(
            ["getfacl", "--absolute-names", "--numeric", str(self.device)]
        ).stdout

    def acquire(self, checkpoint):
        metadata = self.device.stat()
        if not stat.S_ISCHR(metadata.st_mode) or metadata.st_uid != 0:
            raise ValueError("require the root-owned GPU character device")
        self.identity = [metadata.st_dev, metadata.st_ino, metadata.st_rdev]
        self.original = self.acl()
        checkpoint({"original_acl": self.original, "device_identity": self.identity})
        try:
            self.command([
                "sudo", "-n", "setfacl", "--set",
                f"u::rw-,u:{self.uid}:rw-,g::---,m::rw-,o::---", str(self.device),
            ])
            self.applied = self.acl()
            checkpoint({"applied_acl": self.applied})
        except BaseException:
            self.command(["sudo", "-n", "setfacl", "--restore=-"], input=self.original)
            raise

    def verify(self):
        metadata = self.device.stat()
        if [metadata.st_dev, metadata.st_ino, metadata.st_rdev] != self.identity:
            raise RuntimeError("GPU device identity changed during the campaign")
        if self.acl() != self.applied:
            raise RuntimeError("GPU device ACL changed outside this campaign")

    def restore(self):
        if self.applied is None:
            return False
        self.verify()
        self.command(["sudo", "-n", "setfacl", "--restore=-"], input=self.original)
        if self.acl() != self.original:
            raise RuntimeError("GPU device ACL restoration did not match its saved value")
        self.applied = None
        return True


class HelperPrivileges:
    """Prevent the setuid helper from resetting device permissions to 0666."""

    def __init__(self, path=Path("/usr/bin/nvidia-modprobe"), *, run=subprocess.run):
        self.path, self.run = path, run
        self.original = self.applied = self.identity = None

    def acquire(self, checkpoint):
        metadata = self.path.stat()
        self.identity = [metadata.st_dev, metadata.st_ino]
        self.original = stat.S_IMODE(metadata.st_mode)
        self.applied = self.original & ~stat.S_ISUID
        checkpoint({
            "helper": str(self.path), "helper_original_mode": self.original,
            "helper_applied_mode": self.applied, "helper_identity": self.identity,
        })
        self.run(["sudo", "-n", "chmod", oct(self.applied)[2:], str(self.path)], check=True)

    def verify(self):
        metadata = self.path.stat()
        if (
            [metadata.st_dev, metadata.st_ino] != self.identity
            or stat.S_IMODE(metadata.st_mode) != self.applied
        ):
            raise RuntimeError("NVIDIA helper identity or permissions changed")

    def restore(self):
        if self.original is None:
            return False
        self.verify()
        self.run(["sudo", "-n", "chmod", oct(self.original)[2:], str(self.path)], check=True)
        if stat.S_IMODE(self.path.stat().st_mode) != self.original:
            raise RuntimeError("NVIDIA helper permission restoration failed")
        return True


def watch(args):
    owner_path, state_path = args.owner_file.resolve(), args.state.resolve()
    owner = json.loads(owner_path.read_text())
    if not alive(owner):
        raise ValueError("campaign owner must actually be alive before acquisition")
    if state_path.exists():
        raise FileExistsError("preserve the previous device access lease")
    state = {
        "status": "acquiring", "guard": process(os.getpid()),
        "device": str(args.device), "uid": os.getuid(), "owner_file": str(owner_path),
        "owner": owner, "maximum_hours": args.maximum_hours,
        "no_GPU_context_or_compute": True,
    }

    def pulse(update=None):
        state.update(update or {})
        state["checked_at"] = datetime.now(timezone.utc).isoformat()
        write(state_path, state)

    def terminate(signum, frame):
        raise KeyboardInterrupt(f"device lease received signal {signum}")

    signal.signal(signal.SIGTERM, terminate)
    signal.signal(signal.SIGINT, terminate)
    deadline = time.monotonic() + args.maximum_hours * 3600
    device = DeviceAccess(args.device, os.getuid())
    helper = HelperPrivileges()
    with Path("/home/longhao/experiment/ops/gpu_campaign_access.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            pulse()
            helper.acquire(pulse)
            device.acquire(pulse)
            pulse({"status": "held"})
            while True:
                owner = json.loads(owner_path.read_text())
                state["owner"] = owner
                if not alive(owner):
                    state["release_reason"] = "campaign_owner_exited"
                    break
                if time.monotonic() >= deadline:
                    state["release_reason"] = "maximum_lease_duration"
                    break
                device.verify()
                helper.verify()
                pulse()
                time.sleep(2)
        except BaseException as error:
            state.update(error=repr(error), release_reason="guard_interrupted_or_failed")
        finally:
            try:
                restored = device.restore()
                pulse({"status": "released", "original_acl_restored": restored})
            except BaseException as error:
                pulse({"status": "restore_requires_review", "restore_error": repr(error)})
            finally:
                try:
                    pulse({"original_helper_permissions_restored": helper.restore()})
                except BaseException as error:
                    pulse({"status": "restore_requires_review", "helper_restore_error": repr(error)})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", type=Path, default=Path("/dev/nvidia0"))
    parser.add_argument("--owner-file", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--maximum-hours", type=float, default=18)
    args = parser.parse_args()
    if not 0 < args.maximum_hours <= 24:
        parser.error("maximum lease duration must be between zero and 24 hours")
    watch(args)


if __name__ == "__main__":
    main()
