"""GPU mount contract tests; no devices, containers, or services are started.

Run with: uv run --group dev pytest tests/test_gpu.py -v
Regular fixture files become character devices only to the mocked stat command.
The helper is extracted from the production launcher, never reimplemented here.
"""

import json
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

from conftest import LAUNCHER, Launcher

GPU_BEGIN, GPU_END = "# BEGIN GPU MOUNTS", "# END GPU MOUNTS"
HELPER_CALL = "configure_gpu_mounts || exit 1"


class GpuLauncher(Launcher):
    """Launcher harness plus fake /dev, /sys, /etc, /proc roots for the helper.

    A mocked `stat` reports fixture device files as character devices; every
    other stat invocation is delegated to the real stat binary.
    """

    def __init__(self, root: Path):
        super().__init__(root)
        self.roots = [self.root / (name + " fixture")
                      for name in ("dev", "sys", "etc", "proc")]
        self.dev, self.sys, self.etc, self.proc = self.roots
        for root in self.roots:
            root.mkdir()
        self.devices: set[str] = set()
        # conftest symlinks the real stat; replace it with the fixture mock.
        (self.bin / "stat").unlink()
        real_stat = shutil.which("stat", path="/usr/bin:/bin")
        assert real_stat is not None
        self.command("stat", f"""
if '%F' in sys.argv and Path(sys.argv[-1]).exists() and sys.argv[-1] in json.loads(os.environ['GPU_TEST_DEVICES']):
    print('character special file')
else:
    os.execv({real_stat!r}, ['stat', *sys.argv[1:]])
""")
        self.env["GPU_TEST_DEVICES"] = "[]"

    def device(self, name: str, character: bool = True) -> Path:
        path = self.dev / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
        if character:
            self.devices.add(str(path))
        self.env["GPU_TEST_DEVICES"] = json.dumps(sorted(self.devices))
        return path

    def fixture_call(self) -> str:
        return "configure_gpu_mounts " + shlex.join(map(str, self.roots)) + " || exit 1"

    def helper_text(self) -> str:
        text = LAUNCHER.read_text()
        assert text.count(GPU_BEGIN) == 1, "GPU helper needs its BEGIN marker"
        assert text.count(GPU_END) == 1, "GPU helper needs its END marker"
        helper = text.split(GPU_BEGIN, 1)[1].split(GPU_END, 1)[0]
        assert "configure_gpu_mounts" in helper
        return helper

    def run_helper(self) -> subprocess.CompletedProcess:
        script = (self.helper_text() + '\nBWRAP_ARGS=(--dev /dev --proc /proc)\n'
                  + self.fixture_call() + '\nexec bwrap "${BWRAP_ARGS[@]}" --\n')
        self.capture.unlink(missing_ok=True)
        self.calls.unlink(missing_ok=True)
        return subprocess.run(
            [str(self.bin / "bash"), "-c", script], cwd=str(self.project),
            env=self.env, capture_output=True, text=True, timeout=30,
        )

    def run_fixture_launcher(self, *args: str) -> subprocess.CompletedProcess:
        """Run the launcher with the helper pointed at the fixture roots."""
        text = LAUNCHER.read_text()
        assert text.count(HELPER_CALL) == 1, "launcher must propagate helper failure"
        launcher = self.root / "fixture launcher"
        launcher.write_text(text.replace(HELPER_CALL, self.fixture_call(), 1))
        return self.run_launcher(*args, script=launcher)

    @staticmethod
    def binds(options: list, flag: str = "--dev-bind") -> list:
        return [options[i:i + 3] for i, item in enumerate(options) if item == flag]

    def assert_devices(self, options: list, names: list) -> None:
        assert sorted(map(tuple, self.binds(options))) == sorted(
            ("--dev-bind", str(self.dev / name), "/dev/" + name) for name in names)
        for name in names:
            assert any(call[0] == "stat" and "-c" in call and "%F" in call
                       and str(self.dev / name) == call[-1]
                       for call in self.command_calls()), name
        pairs = [options[i:i + 2] for i in range(len(options) - 1)]
        for parent in ("dri", "nvidia-caps"):
            expected = any(name.startswith(parent + "/") for name in names)
            assert pairs.count(["--dir", "/dev/" + parent]) == int(expected)
            if expected:
                assert options.index("/dev/" + parent) < min(
                    options.index(str(self.dev / name)) for name in names
                    if name.startswith(parent + "/"))

    def assert_failure(self, result: subprocess.CompletedProcess) -> None:
        assert result.returncode != 0, result.stdout + result.stderr
        assert not self.capture.exists(), "bwrap ran after GPU setup failed"
        assert result.stderr.strip(), "GPU failure needs a diagnostic"


@pytest.fixture
def gpu(tmp_path: Path) -> GpuLauncher:
    return GpuLauncher(tmp_path)


def test_vendor_fixtures(gpu):
    fixtures = {
        "intel": ["dri/card0", "dri/renderD128"],
        "amd": ["dri/card1", "dri/renderD129", "kfd"],
        "nvidia": ["nvidia0", "nvidia12", "nvidiactl", "nvidia-uvm",
                   "nvidia-uvm-tools", "nvidia-modeset", "nvidia-caps/nvidia-cap1"],
        "mixed": ["nvidia0", "nvidia-uvm", "dri/card0", "dri/renderD128", "kfd"],
        "card-only": ["dri/card12"],
        "render-only": ["dri/renderD130"],
    }
    for vendor, names in fixtures.items():
        gpu.devices.clear()
        for path in gpu.dev.iterdir():
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
        for name in names:
            gpu.device(name)
        result = gpu.run_helper()
        options, _ = gpu.captured(result)
        assert options[:4] == ["--dev", "/dev", "--proc", "/proc"]
        gpu.assert_devices(options, names)
        assert "uvm" not in result.stderr.lower()


def test_optional_metadata_present(gpu):
    gpu.device("nvidia0")
    gpu.device("nvidia-uvm")
    (gpu.etc / "ld.so.cache").touch()
    directories = ["OpenCL/vendors", "vulkan/icd.d", "glvnd/egl_vendor.d"]
    for directory in directories:
        (gpu.etc / directory).mkdir(parents=True)
    (gpu.proc / "driver/nvidia").mkdir(parents=True)
    options, _ = gpu.captured(gpu.run_helper())
    expected = [("--ro-bind-try", str(gpu.sys), "/sys")]
    expected += [("--ro-bind-try", str(gpu.etc / name), "/etc/" + name)
                 for name in ["ld.so.cache", *directories]]
    expected += [("--ro-bind-try", str(gpu.proc / "driver/nvidia"),
                  "/proc/driver/nvidia")]
    assert sorted(map(tuple, gpu.binds(options, "--ro-bind-try"))) == sorted(expected)


def test_missing_optional_metadata_is_not_required(gpu):
    gpu.device("dri/renderD128")
    gpu.sys.rmdir()
    options, _ = gpu.captured(gpu.run_helper())
    gpu.assert_devices(options, ["dri/renderD128"])
    assert not gpu.binds(options, "--ro-bind")
    assert "/proc/driver/nvidia" not in options


def test_nvidia_proc_metadata_requires_nvidia_primary(gpu):
    gpu.device("dri/card0")
    gpu.device("nvidiactl")
    (gpu.proc / "driver/nvidia").mkdir(parents=True)
    options, _ = gpu.captured(gpu.run_helper())
    assert "/proc/driver/nvidia" not in options


def test_nvidia_warns_without_valid_uvm(gpu):
    gpu.device("nvidia0")
    for kind in ("absent", "regular", "symlink", "character"):
        path = gpu.dev / "nvidia-uvm"
        path.unlink(missing_ok=True)
        if kind == "regular":
            gpu.device("nvidia-uvm", character=False)
        elif kind == "symlink":
            path.symlink_to(gpu.device("target"))
            gpu.devices.add(str(path))
            gpu.env["GPU_TEST_DEVICES"] = json.dumps(sorted(gpu.devices))
        elif kind == "character":
            gpu.device("nvidia-uvm")
        result = gpu.run_helper()
        options, _ = gpu.captured(result)
        assert ("uvm" in result.stderr.lower()) == (kind != "character")
        gpu.assert_devices(options, ["nvidia0"] +
                           (["nvidia-uvm"] if kind == "character" else []))


def test_no_primary_fails_including_auxiliary_only(gpu):
    gpu.assert_failure(gpu.run_helper())
    for name in ("nvidiactl", "nvidia-uvm", "nvidia-uvm-tools", "nvidia-modeset",
                 "nvidia-caps/nvidia-cap0", "kfd"):
        gpu.device(name)
    gpu.assert_failure(gpu.run_helper())


def test_invalid_names_and_noncharacters_do_not_count_as_primary(gpu):
    for name in ("nvidia", "nvidia0extra", "nvidia-1", "nvidia1.2", "xnvidia0",
                 "dri/card", "dri/card0extra", "dri/card-1", "dri/xcard0",
                 "dri/renderD", "dri/renderD128extra", "dri/renderD-1",
                 "nvidia-caps/nvidia-cap", "nvidia-caps/nvidia-cap1extra"):
        gpu.device(name)
    for name in ("nvidia1", "dri/card1", "dri/renderD129", "kfd"):
        gpu.device(name, character=False)
    (gpu.dev / "nvidia2").mkdir()
    gpu.assert_failure(gpu.run_helper())
    gpu.device("nvidia0")
    options, _ = gpu.captured(gpu.run_helper())
    gpu.assert_devices(options, ["nvidia0"])


def test_symlink_nodes_skipped_even_if_stat_reports_character(gpu):
    target = gpu.device("target")
    names = ["nvidia0", "dri/card0", "dri/renderD128", "nvidiactl", "nvidia-uvm",
             "nvidia-uvm-tools", "nvidia-modeset", "nvidia-caps/nvidia-cap1", "kfd"]
    for name in names:
        path = gpu.dev / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.symlink_to(target)
        gpu.devices.add(str(path))
    (gpu.dev / "nvidia99").symlink_to(gpu.dev / "missing")
    gpu.env["GPU_TEST_DEVICES"] = json.dumps(sorted(gpu.devices))
    gpu.assert_failure(gpu.run_helper())
    gpu.device("nvidia1")
    options, _ = gpu.captured(gpu.run_helper())
    gpu.assert_devices(options, ["nvidia1"])


def test_symlink_parent_directories_rejected(gpu):
    gpu.device("nvidia0")
    for parent, child in (("dri", "card0"), ("nvidia-caps", "nvidia-cap1")):
        target = gpu.root / (parent + " external")
        target.mkdir()
        (target / child).touch()
        link = gpu.dev / parent
        link.symlink_to(target, target_is_directory=True)
        gpu.assert_failure(gpu.run_helper())
        link.unlink()


def test_help_documents_gpu(gpu):
    for flag in ("--help", "-h"):
        result = gpu.run_launcher(flag)
        assert result.returncode == 0, result.stderr
        assert "--gpu" in result.stdout
        assert not gpu.capture.exists()


def test_default_does_not_probe_or_mount_gpu(gpu):
    options, command = gpu.captured(gpu.run_launcher("-s", "session"))
    assert not gpu.binds(options)
    assert "/sys" not in options
    assert "/etc/ld.so.cache" not in options
    assert not any(call[0] == "stat" and "%F" in call
                   for call in gpu.command_calls())
    assert command[3:] == ["--", "-s", "session"]


def test_cli_exits_on_helper_failure(gpu):
    gpu.assert_failure(gpu.run_fixture_launcher("--gpu", "-s", "session"))


def test_repeated_flags_preserve_arguments_and_mount_once(gpu):
    gpu.device("dri/renderD128")
    options, command = gpu.captured(gpu.run_fixture_launcher(
        "--gpu", "-s", "session with spaces", "--gpu", "run", "prompt", ""))
    gpu.assert_devices(options, ["dri/renderD128"])
    assert command[3:] == ["--", "-s", "session with spaces", "run", "prompt", ""]
    assert options.count("/sys") == 1


def test_composition_seccomp_namespaces_and_override_order(gpu):
    gpu.device("dri/renderD128")
    seccomp = gpu.home / ".local/share/opencodebox/seccomp-security.bpf"
    seccomp.parent.mkdir(parents=True)
    seccomp.write_bytes(b"test filter")
    (gpu.bin / "uname").unlink()
    gpu.command("uname", "print('x86_64')")
    baseline, _ = gpu.captured(gpu.run_launcher())
    override = gpu.root / "GPU override"
    override.mkdir()
    options, command = gpu.captured(gpu.run_fixture_launcher(
        "--with-ro", f"{override}:/dev/dri", "--gpu", "-s", "session", "--gpu"))
    gpu.assert_devices(options, ["dri/renderD128"])
    # GPU mounts come first, explicit user binds override them, and the
    # OpenCode 2 isolation overlay still forms the tail (later binds win).
    user_idx = options.index(str(override))
    gpu_idx = options.index(str(gpu.dev / "dri/renderD128"))
    overlay_idx = options.index(str(gpu.home / ".local/share/opencodebox/disabled-service.json"))
    assert gpu_idx < user_idx < overlay_idx
    assert command[3:] == ["--", "-s", "session"]
    def isolation(args):
        return [arg for arg in args if arg.startswith("--unshare")
                or arg in ("--share-net", "--die-with-parent", "--new-session")]
    assert isolation(options) == isolation(baseline)
    for args in (baseline, options):
        assert args.count("--seccomp") == 1
        assert args[args.index("--seccomp") + 1].isdigit()
        assert ["--dev", "/dev"] in [args[i:i + 2] for i in range(len(args) - 1)]
