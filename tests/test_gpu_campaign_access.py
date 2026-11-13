import os
from pathlib import Path
import stat
import subprocess
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from scripts import hold_gpu_campaign_access as lease


class ACLCommands:
    def __init__(self):
        self.current = "original ACL\n"
        self.calls = []

    def __call__(self, arguments, **kwargs):
        self.calls.append((arguments, kwargs))
        if arguments[0] == "getfacl":
            return SimpleNamespace(stdout=self.current)
        if "--restore=-" in arguments:
            self.current = kwargs["input"]
        else:
            self.current = "campaign ACL\n"
        return SimpleNamespace(stdout="")


def test_restore_exact_original_acl_and_checkpoint_before_restricting(tmp_path):
    device = tmp_path / "nvidia0"
    device.touch()
    commands, evidence = ACLCommands(), []
    access = lease.DeviceAccess(device, 2603, run=commands)
    metadata = device.stat()
    with patch.object(lease.stat, "S_ISCHR", return_value=True), patch.object(
        lease.Path, "stat",
        return_value=SimpleNamespace(
            st_mode=metadata.st_mode, st_uid=0, st_dev=metadata.st_dev,
            st_ino=metadata.st_ino, st_rdev=metadata.st_rdev,
        ),
    ):
        access.acquire(lambda update: evidence.append((dict(update), len(commands.calls))))
        assert evidence[0][0]["original_acl"] == "original ACL\n"
        assert evidence[0][1] == 1
        assert commands.calls[1][0][-2] == "u::rw-,u:2603:rw-,g::---,m::rw-,o::---"
        access.verify()
        assert access.restore()
        assert commands.current == "original ACL\n"
        assert not access.restore()


def test_do_not_overwrite_an_administrators_changed_acl(tmp_path):
    device = tmp_path / "nvidia0"
    device.touch()
    commands = ACLCommands()
    access = lease.DeviceAccess(device, 2603, run=commands)
    metadata = device.stat()
    access.identity = [metadata.st_dev, metadata.st_ino, metadata.st_rdev]
    access.original, access.applied = "original ACL\n", "campaign ACL\n"
    commands.current = "administrator ACL\n"
    with pytest.raises(RuntimeError, match="changed outside"):
        access.restore()
    assert len(commands.calls) == 1


def test_replaced_device_is_not_restored(tmp_path):
    device = tmp_path / "nvidia0"
    device.touch()
    access = lease.DeviceAccess(device, 2603, run=ACLCommands())
    access.identity = [-1, -1, -1]
    access.original, access.applied = "original ACL\n", "campaign ACL\n"
    with pytest.raises(RuntimeError, match="identity changed"):
        access.restore()


def test_owner_identity_does_not_accept_pid_reuse_or_zombies():
    owner = lease.process(os.getpid())
    assert lease.alive(owner)
    assert not lease.alive({**owner, "start_ticks": "-1"})
    with patch.object(lease, "process", return_value={**owner, "state": "Z"}):
        assert not lease.alive(owner)


def test_refuse_regular_files_before_acl_mutation(tmp_path):
    device = tmp_path / "not-a-GPU"
    device.touch()
    commands = ACLCommands()
    with pytest.raises(ValueError, match="character device"):
        lease.DeviceAccess(device, os.getuid(), run=commands).acquire(lambda _: None)
    assert commands.calls == []


def test_helper_setuid_removed_and_original_mode_restored(tmp_path):
    helper = tmp_path / "helper"
    helper.touch()
    helper.chmod(0o4755)
    calls = []

    def local_chmod(arguments, **kwargs):
        assert arguments[:3] == ["sudo", "-n", "chmod"]
        calls.append(arguments)
        return subprocess.run(arguments[2:], **kwargs)

    access = lease.HelperPrivileges(helper, run=local_chmod)
    evidence = []
    access.acquire(evidence.append)
    assert stat.S_IMODE(helper.stat().st_mode) == 0o755
    assert evidence[0]["helper_original_mode"] == 0o4755
    access.verify()
    assert access.restore()
    assert stat.S_IMODE(helper.stat().st_mode) == 0o4755
    assert len(calls) == 2
