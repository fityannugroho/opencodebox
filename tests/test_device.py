"""Device mount contract tests; no real devices are exposed or started.

Run with: uv run --group dev pytest tests/test_device.py -v
Regular fixture files become character/block devices only to the mocked stat
command. The helper is extracted from the production launcher, never
reimplemented here.
"""

import json
import os
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

from conftest import LAUNCHER, Launcher

DEVICE_BEGIN, DEVICE_END = "# BEGIN DEVICE MOUNTS", "# END DEVICE MOUNTS"
DEVICE_CALL = 'configure_device_mounts /dev "${DEVICE_PATHS[@]}" || exit 1'


def _symbolic(kind: str, mode: int) -> str:
    """Permission string like stat's %A; only the trailing '+' matters."""
    lead = "c" if "character" in kind else "b"

    def triple(shift: int) -> str:
        bits = (mode >> shift) & 7
        return ("r" if bits & 4 else "-") + ("w" if bits & 2 else "-") \
            + ("x" if bits & 1 else "-")

    return lead + triple(6) + triple(3) + triple(0)


class DeviceLauncher(Launcher):
    """Launcher harness plus a fake /dev root for --device nodes.

    A mocked `stat` reports fixture device files as character/block devices
    with configurable mode and ownership; every other stat invocation is
    delegated to the real stat binary. `id` is mocked so the launch-time
    access advisory is deterministic.
    """

    def __init__(self, root: Path):
        super().__init__(root)
        self.uid = os.getuid() or 12345
        self.dev = self.root / "dev fixture"
        self.dev.mkdir()
        self.kinds: dict[str, str] = {}
        self.access: dict[str, list[str]] = {}
        self.env["TEST_UID"] = str(self.uid)
        # conftest symlinks the real id; replace it with the fixture mock.
        (self.bin / "id").unlink()
        self.command("id", """
values = {'-u': os.environ['TEST_UID'], '-g': '12345',
          '-un': 'test-user', '-gn': 'test-group',
          '-G': os.environ.get('TEST_GROUPS', '12345')}
print(values[sys.argv[1]])
""")
        # conftest symlinks the real stat; replace it with the fixture mock.
        (self.bin / "stat").unlink()
        real_stat = shutil.which("stat", path="/usr/bin:/bin")
        assert real_stat is not None
        self.command("stat", f"""
kinds = json.loads(os.environ['DEVICE_TEST_KINDS'])
access = json.loads(os.environ['DEVICE_TEST_ACCESS'])
fmt = sys.argv[sys.argv.index('-c') + 1] if '-c' in sys.argv else ''
if '%a' in fmt and sys.argv[-1] in access:
    print(*access[sys.argv[-1]])
elif '%F' in fmt and Path(sys.argv[-1]).exists() and sys.argv[-1] in kinds:
    print(kinds[sys.argv[-1]])
else:
    os.execv({real_stat!r}, ['stat', *sys.argv[1:]])
""")
        self.env["DEVICE_TEST_KINDS"] = "{}"
        self.env["DEVICE_TEST_ACCESS"] = "{}"

    def node(self, name: str, kind: str = "character special file",
             mode: int = 0o666, uid: int = 0, gid: int = 0,
             acl: bool = False) -> Path:
        """Create a fixture file that the mocked stat reports as a device."""
        path = self.dev / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
        self.kinds[str(path)] = kind
        self.access[str(path)] = [f"{mode:o}", str(uid), str(gid),
                                  _symbolic(kind, mode) + ("+" if acl else "")]
        self.env["DEVICE_TEST_KINDS"] = json.dumps(self.kinds)
        self.env["DEVICE_TEST_ACCESS"] = json.dumps(self.access)
        return path

    def device_helper_text(self) -> str:
        text = LAUNCHER.read_text()
        assert text.count(DEVICE_BEGIN) == 1, "Device helper needs its BEGIN marker"
        assert text.count(DEVICE_END) == 1, "Device helper needs its END marker"
        helper = text.split(DEVICE_BEGIN, 1)[1].split(DEVICE_END, 1)[0]
        assert "configure_device_mounts" in helper
        return helper

    def run_device_helper(self, *paths: Path) -> subprocess.CompletedProcess:
        script = (self.device_helper_text() + '\nBWRAP_ARGS=(--dev /dev --proc /proc)\n'
                  + "configure_device_mounts "
                  + shlex.join([str(self.dev), *map(str, paths)]) + ' || exit 1\n'
                  + 'exec bwrap "${BWRAP_ARGS[@]}" --\n')
        self.capture.unlink(missing_ok=True)
        self.calls.unlink(missing_ok=True)
        return subprocess.run(
            [str(self.bin / "bash"), "-c", script], cwd=str(self.project),
            env=self.env, capture_output=True, text=True, timeout=30,
        )

    def run_fixture_launcher(self, *args: str) -> subprocess.CompletedProcess:
        """Run the launcher with the device helper pointed at the fixture root."""
        text = LAUNCHER.read_text()
        assert text.count(DEVICE_CALL) == 1, "launcher must propagate device helper failure"
        replacement = ("configure_device_mounts " + shlex.join([str(self.dev)])
                       + ' "${DEVICE_PATHS[@]}" || exit 1')
        launcher = self.root / "device fixture launcher"
        launcher.write_text(text.replace(DEVICE_CALL, replacement, 1))
        return self.run_launcher(*args, script=launcher)

    @staticmethod
    def binds(options: list, flag: str = "--dev-bind") -> list:
        return [options[i:i + 3] for i, item in enumerate(options) if item == flag]

    def assert_bound(self, options: list, names: list) -> None:
        assert sorted(map(tuple, self.binds(options))) == sorted(
            ("--dev-bind", str(self.dev / name), "/dev/" + name) for name in names)

    def assert_failure(self, result: subprocess.CompletedProcess) -> None:
        assert result.returncode != 0, result.stdout + result.stderr
        assert not self.capture.exists(), "bwrap ran after device setup failed"
        assert result.stderr.strip(), "device failure needs a diagnostic"


@pytest.fixture
def device(tmp_path: Path) -> DeviceLauncher:
    return DeviceLauncher(tmp_path)


# --- Standalone helper contract ---


def test_top_level_node_needs_no_directory(device):
    tty = device.node("ttyUSB0")
    options, _ = device.captured(device.run_device_helper(tty))
    assert options[:4] == ["--dev", "/dev", "--proc", "/proc"]
    device.assert_bound(options, ["ttyUSB0"])
    assert "--dir" not in options


def test_nested_nodes_create_each_ancestor_once(device):
    control = device.node("snd/controlC0")
    pcm = device.node("snd/pcmC0D0p")
    deep = device.node("hidraw/by-serial/usb-1")
    options, _ = device.captured(device.run_device_helper(control, pcm, deep))
    device.assert_bound(options, ["snd/controlC0", "snd/pcmC0D0p",
                                  "hidraw/by-serial/usb-1"])
    pairs = [options[i:i + 2] for i in range(len(options) - 1)]
    for directory in ("/dev/snd", "/dev/hidraw", "/dev/hidraw/by-serial"):
        assert pairs.count(["--dir", directory]) == 1, directory
    assert options.index("/dev/snd") < options.index(str(control))
    assert options.index("/dev/snd") < options.index(str(pcm))
    assert options.index("/dev/hidraw") < options.index("/dev/hidraw/by-serial")
    assert options.index("/dev/hidraw/by-serial") < options.index(str(deep))


def test_block_device_is_accepted(device):
    loop = device.node("loop0", kind="block special file")
    options, _ = device.captured(device.run_device_helper(loop))
    device.assert_bound(options, ["loop0"])


def test_repeated_path_is_bound_once(device):
    tty = device.node("ttyUSB0")
    options, _ = device.captured(device.run_device_helper(tty, tty))
    assert device.binds(options) == [["--dev-bind", str(tty), "/dev/ttyUSB0"]]


def test_empty_call_adds_nothing(device):
    result = device.run_device_helper()
    options, _ = device.captured(result)
    assert not device.binds(options)
    assert "--device" not in result.stderr


def test_success_emits_warning(device):
    tty = device.node("ttyUSB0")
    result = device.run_device_helper(tty)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Warning: --device" in result.stderr


def test_helper_skips_access_advisory_without_launcher_identity(device):
    # The extracted helper runs without the launcher's identity variables,
    # so the host-access advisory must stay silent instead of guessing.
    tty = device.node("ttyUSB0", mode=0o600)
    result = device.run_device_helper(tty)
    options, _ = device.captured(result)
    device.assert_bound(options, ["ttyUSB0"])
    assert "usermod" not in result.stderr


def test_only_character_or_block_kinds_pass(device):
    for kind in ("regular file", "directory", "socket", "fifo"):
        path = device.node("weird", kind=kind)
        device.assert_failure(device.run_device_helper(path))


def test_missing_node_is_rejected(device):
    device.assert_failure(device.run_device_helper(device.dev / "missing"))


def test_unregistered_regular_file_is_rejected(device):
    regular = device.dev / "regular"
    regular.touch()
    device.assert_failure(device.run_device_helper(regular))


def test_directory_is_rejected(device):
    directory = device.dev / "subdir"
    directory.mkdir()
    device.assert_failure(device.run_device_helper(directory))


def test_symlink_node_is_rejected_even_if_target_is_a_device(device):
    target = device.node("target")
    link = device.dev / "ttyUSB0"
    link.symlink_to(target)
    device.assert_failure(device.run_device_helper(link))


def test_paths_outside_the_dev_root_are_rejected(device):
    outside = device.project / "ttyUSB0"
    outside.touch()
    device.assert_failure(device.run_device_helper(outside))
    device.assert_failure(device.run_device_helper("ttyUSB0"))
    device.assert_failure(device.run_device_helper(device.dev))
    device.assert_failure(device.run_device_helper(""))


def test_unsafe_path_components_are_rejected(device):
    for suffix in ("/../escape", "/./dot", "//double", "/trailing/"):
        path = device.dev / ("ttyUSB0" + suffix)
        device.assert_failure(device.run_device_helper(path))


def test_symlinked_parent_directory_is_rejected(device):
    external = device.root / "external snd"
    external.mkdir()
    (external / "controlC0").touch()
    (device.dev / "snd").symlink_to(external, target_is_directory=True)
    device.assert_failure(device.run_device_helper(device.node("snd/controlC0")))


# --- CLI contract ---


def test_help_documents_device(device):
    for flag in ("--help", "-h"):
        result = device.run_launcher(flag)
        assert result.returncode == 0, result.stderr
        assert "--device" in result.stdout
        assert not device.capture.exists()


def test_default_does_not_probe_or_bind_devices(device):
    options, command = device.captured(device.run_launcher("-s", "session"))
    assert not device.binds(options)
    assert not any(call[0] == "stat" and "%F" in call
                   for call in device.command_calls())
    assert command[3:] == ["--", "-s", "session"]


def test_device_requires_a_value(device):
    result = device.run_launcher("--device")
    assert result.returncode != 0, result.stdout + result.stderr
    assert not device.capture.exists()
    assert "requires a value" in result.stderr


def test_option_like_value_is_rejected_before_any_setup(device):
    for value in ("--device", "--with", "-s"):
        result = device.run_launcher("--with-ro", "/etc/hosts", "--device", value,
                                     "/dev/ttyACM0", "-s", "session")
        assert result.returncode != 0, result.stdout + result.stderr
        assert not device.capture.exists(), "bwrap ran despite option-like --device value"
        assert "Error: --device requires a device path" in result.stderr
        assert value in result.stderr
        assert "Usage: opencodebox --device" in result.stderr
        # Parsing aborts before any filesystem or device setup happens.
        assert not any(call[0] == "stat" and "%F" in call
                       for call in device.command_calls())


def test_real_host_null_device_is_bound_via_cli(device):
    result = device.run_launcher("--device", "/dev/null", "-s", "session")
    options, command = device.captured(result)
    assert device.binds(options) == [["--dev-bind", "/dev/null", "/dev/null"]]
    assert command[3:] == ["--", "-s", "session"]
    assert "Warning: --device" in result.stderr


def test_inaccessible_device_warns_with_host_fix_hint(device):
    # root:dialout-style node that the invoking user cannot open on the
    # host is still bound, but the launcher explains the host-side fix.
    # getent is absent from the test PATH, so the group keeps its gid.
    tty = device.node("ttyUSB0", mode=0o660, gid=100018)
    result = device.run_fixture_launcher("--device", str(tty), "-s", "session")
    options, _ = device.captured(result)
    assert device.binds(options) == [["--dev-bind", str(tty), "/dev/ttyUSB0"]]
    assert str(tty) in result.stderr
    assert "usermod -aG 100018 test-user" in result.stderr
    assert "cannot open" in result.stderr


def test_group_accessible_device_does_not_warn(device):
    tty = device.node("ttyUSB0", mode=0o660, gid=100018)
    device.env["TEST_GROUPS"] = "12345 100018"
    result = device.run_fixture_launcher("--device", str(tty), "-s", "session")
    device.captured(result)
    assert "usermod" not in result.stderr


def test_owner_accessible_device_does_not_warn(device):
    tty = device.node("ttyUSB0", mode=0o600, uid=device.uid)
    result = device.run_fixture_launcher("--device", str(tty), "-s", "session")
    device.captured(result)
    assert "usermod" not in result.stderr


def test_owner_mode_denied_warns_about_node_permissions(device):
    tty = device.node("ttyUSB0", mode=0o000, uid=device.uid)
    result = device.run_fixture_launcher("--device", str(tty), "-s", "session")
    device.captured(result)
    assert "cannot open" in result.stderr
    assert "udev rule" in result.stderr
    assert "usermod" not in result.stderr


def test_acl_backed_device_does_not_warn_beyond_mode_bits(device):
    # POSIX ACLs (stat '%A' ends in '+') can grant access the mode bits
    # deny; the advisory cannot reason about them and stays silent.
    tty = device.node("ttyUSB0", mode=0o600, gid=100018, acl=True)
    result = device.run_fixture_launcher("--device", str(tty), "-s", "session")
    device.captured(result)
    assert "usermod" not in result.stderr


def test_paths_outside_dev_are_rejected_via_cli(device):
    for value in ("/dev", "ttyUSB0", str(device.project / "ttyUSB0"), "/dev/./null"):
        result = device.run_launcher("--device", value, "-s", "session")
        assert result.returncode != 0, result.stdout + result.stderr
        assert not device.capture.exists(), "bwrap ran despite invalid device"


def test_single_flag_binds_fixture_node(device):
    tty = device.node("ttyUSB0")
    result = device.run_fixture_launcher("--device", str(tty), "-s", "session with spaces")
    options, command = device.captured(result)
    assert device.binds(options) == [["--dev-bind", str(tty), "/dev/ttyUSB0"]]
    assert command[3:] == ["--", "-s", "session with spaces"]
    assert "Warning: --device" in result.stderr


def test_repeated_flags_bind_each_node_once(device):
    tty = device.node("ttyUSB0")
    video = device.node("video0")
    options, command = device.captured(device.run_fixture_launcher(
        "--device", str(tty), "run", "--device", str(video),
        "--device", str(tty), "-s", "session"))
    assert device.binds(options) == [
        ["--dev-bind", str(tty), "/dev/ttyUSB0"],
        ["--dev-bind", str(video), "/dev/video0"],
    ]
    assert command[3:] == ["--", "run", "-s", "session"]


def test_nested_node_gets_parent_directory(device):
    control = device.node("snd/controlC0")
    options, _ = device.captured(device.run_fixture_launcher("--device", str(control)))
    pairs = [options[i:i + 2] for i in range(len(options) - 1)]
    assert pairs.count(["--dir", "/dev/snd"]) == 1
    assert options.index("/dev/snd") < options.index(str(control))


def test_block_node_via_cli(device):
    loop = device.node("loop0", kind="block special file")
    options, _ = device.captured(device.run_fixture_launcher("--device", str(loop)))
    assert device.binds(options) == [["--dev-bind", str(loop), "/dev/loop0"]]


def test_empty_value_is_rejected(device):
    device.assert_failure(device.run_fixture_launcher("--device", ""))


def test_helper_failure_exits_without_bwrap(device):
    device.assert_failure(device.run_fixture_launcher(
        "--device", str(device.dev / "missing"), "-s", "session"))


# --- Interaction with the rest of the launcher ---


def test_composition_seccomp_namespaces_and_override_order(device):
    tty = device.node("ttyUSB0")
    seccomp = device.home / ".local/share/opencodebox/seccomp-security.bpf"
    seccomp.parent.mkdir(parents=True)
    seccomp.write_bytes(b"test filter")
    (device.bin / "uname").unlink()
    device.command("uname", "print('x86_64')")
    baseline, _ = device.captured(device.run_launcher())
    override = device.root / "device override"
    override.mkdir()
    options, command = device.captured(device.run_fixture_launcher(
        "--with-ro", f"{override}:/dev/ttyUSB1", "--device", str(tty),
        "-s", "session"))
    assert device.binds(options) == [["--dev-bind", str(tty), "/dev/ttyUSB0"]]
    assert command[3:] == ["--", "-s", "session"]
    # Device mounts come first, explicit user binds override them, and the
    # OpenCode 2 isolation overlay still forms the tail (later binds win).
    assert options.index(str(tty)) < options.index(str(override))
    assert options.index(str(override)) < options.index(
        str(device.home / ".local/share/opencodebox/disabled-service.json"))
    def isolation(args):
        return [arg for arg in args if arg.startswith("--unshare")
                or arg in ("--share-net", "--die-with-parent", "--new-session")]
    assert isolation(options) == isolation(baseline)
    for args in (baseline, options):
        assert args.count("--seccomp") == 1
        assert args[args.index("--seccomp") + 1].isdigit()
        assert ["--dev", "/dev"] in [args[i:i + 2] for i in range(len(args) - 1)]


def test_nss_identity_files_list_host_groups(device):
    # Every host group of the invoking user must be resolvable inside the
    # sandbox: getent is absent from the test PATH, so unresolvable gids
    # keep numeric names. The device access advisory relies on the same data.
    device.env["TEST_GROUPS"] = "12345 100018"
    device.run_launcher("-s", "session")
    assert device.files.exists()
    files = json.loads(device.files.read_text())
    assert "/etc/passwd" in files
    assert "/etc/group" in files
    # Root and the kernel overflow id resolve for display and lookup;
    # the user keeps the same ids.
    assert files["/etc/passwd"].startswith("root:x:0:0:root:/root:")
    assert "nobody:x:65534:65534:" in files["/etc/passwd"]
    assert f"test-user:x:{device.uid}:12345:" in files["/etc/passwd"]
    group_lines = files["/etc/group"].splitlines()
    assert group_lines[:2] == ["root:x:0:", "nobody:x:65534:"]
    for gid in ("12345", "100018"):
        assert f"{gid}:x:{gid}:test-user" in group_lines
