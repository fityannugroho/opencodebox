"""GPU mount contract tests; no devices, containers, or services are started.

Run with: python3 -m unittest -v test_gpu.py
Regular fixture files become character devices only to the mocked stat command.
The helper is extracted from the production launcher, never reimplemented here.
"""

import json
import shlex
import shutil
import subprocess
import unittest
from unittest.mock import patch

import test_podman
from test_podman import LAUNCHER, LauncherTestCase


class GPUFixture(LauncherTestCase):
    def setUp(self):
        super().setUp()
        self.roots = [self.root / (name + " fixture")
                      for name in ("dev", "sys", "etc", "proc")]
        self.dev, self.sys, self.etc, self.proc = self.roots
        for root in self.roots:
            root.mkdir()
        self.devices = set()
        real_stat = shutil.which("stat", path="/usr/bin:/bin")
        self.command("stat", f"""
if '%F' in sys.argv and Path(sys.argv[-1]).exists() and sys.argv[-1] in json.loads(os.environ['GPU_TEST_DEVICES']):
    print('character special file')
elif '%u' in sys.argv and os.getuid() == 0:
    print(os.environ['TEST_UID'])
else:
    os.execv({real_stat!r}, ['stat', *sys.argv[1:]])
""")
        self.env["GPU_TEST_DEVICES"] = "[]"

    def device(self, name, character=True):
        path = self.dev / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
        if character:
            self.devices.add(str(path))
        self.env["GPU_TEST_DEVICES"] = json.dumps(sorted(self.devices))
        return path

    def fixture_call(self):
        return "configure_gpu_mounts " + shlex.join(map(str, self.roots)) + " || exit 1"

    def helper_text(self):
        text = LAUNCHER.read_text()
        begin, end = "# BEGIN GPU MOUNTS", "# END GPU MOUNTS"
        self.assertEqual(text.count(begin), 1, "GPU helper needs its BEGIN marker")
        self.assertEqual(text.count(end), 1, "GPU helper needs its END marker")
        helper = text.split(begin, 1)[1].split(end, 1)[0]
        self.assertIn("configure_gpu_mounts", helper)
        return helper

    def run_helper(self):
        script = (self.helper_text() + '\nBWRAP_ARGS=(--dev /dev --proc /proc)\n'
                  + self.fixture_call() + '\nexec bwrap "${BWRAP_ARGS[@]}" --\n')
        self.capture.unlink(missing_ok=True)
        self.calls.unlink(missing_ok=True)
        return subprocess.run(
            [str(self.bin / "bash"), "-c", script], cwd=self.project,
            env=self.env, capture_output=True, text=True, timeout=10,
        )

    def run_fixture_launcher(self, *args):
        text = LAUNCHER.read_text()
        call = "configure_gpu_mounts || exit 1"
        self.assertEqual(text.count(call), 1, "launcher must propagate helper failure")
        launcher = self.root / "fixture launcher"
        launcher.write_text(text.replace(call, self.fixture_call(), 1))
        with patch.object(test_podman, "LAUNCHER", launcher):
            return self.run_launcher(*args)

    @staticmethod
    def binds(options, flag="--dev-bind"):
        return [options[i:i + 3] for i, item in enumerate(options) if item == flag]

    def assert_devices(self, options, names):
        self.assertCountEqual(self.binds(options), [
            ["--dev-bind", str(self.dev / name), "/dev/" + name] for name in names
        ])
        for name in names:
            self.assertTrue(any(call[0] == "stat" and "-c" in call and "%F" in call
                                and str(self.dev / name) == call[-1]
                                for call in self.command_calls()), name)
        pairs = [options[i:i + 2] for i in range(len(options) - 1)]
        for parent in ("dri", "nvidia-caps"):
            expected = any(name.startswith(parent + "/") for name in names)
            self.assertEqual(pairs.count(["--dir", "/dev/" + parent]), int(expected))
            if expected:
                self.assertLess(options.index("/dev/" + parent),
                                min(options.index(str(self.dev / name)) for name in names
                                    if name.startswith(parent + "/")))

    def assert_failure(self, result):
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(self.capture.exists(), "bwrap ran after GPU setup failed")
        self.assertTrue(result.stderr.strip(), "GPU failure needs a diagnostic")


class GPUHelperTests(GPUFixture):
    def test_vendor_fixtures(self):
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
            with self.subTest(vendor=vendor):
                self.devices.clear()
                for path in self.dev.iterdir():
                    if path.is_dir():
                        shutil.rmtree(path)
                    else:
                        path.unlink()
                for name in names:
                    self.device(name)
                result = self.run_helper()
                options, _ = self.captured(result)
                self.assertEqual(options[:4], ["--dev", "/dev", "--proc", "/proc"])
                self.assert_devices(options, names)
                self.assertNotIn("uvm", result.stderr.lower())

    def test_optional_metadata_present(self):
        self.device("nvidia0")
        self.device("nvidia-uvm")
        (self.etc / "ld.so.cache").touch()
        directories = ["OpenCL/vendors", "vulkan/icd.d", "glvnd/egl_vendor.d"]
        for directory in directories:
            (self.etc / directory).mkdir(parents=True)
        (self.proc / "driver/nvidia").mkdir(parents=True)
        options, _ = self.captured(self.run_helper())
        expected = [["--ro-bind-try", str(self.sys), "/sys"]]
        expected += [["--ro-bind-try", str(self.etc / name), "/etc/" + name]
                     for name in ["ld.so.cache", *directories]]
        expected += [["--ro-bind-try", str(self.proc / "driver/nvidia"),
                      "/proc/driver/nvidia"]]
        self.assertCountEqual(self.binds(options, "--ro-bind-try"), expected)

    def test_missing_optional_metadata_is_not_required(self):
        self.device("dri/renderD128")
        self.sys.rmdir()
        options, _ = self.captured(self.run_helper())
        self.assert_devices(options, ["dri/renderD128"])
        self.assertFalse(self.binds(options, "--ro-bind"))
        self.assertNotIn("/proc/driver/nvidia", options)

    def test_nvidia_proc_metadata_requires_nvidia_primary(self):
        self.device("dri/card0")
        self.device("nvidiactl")
        (self.proc / "driver/nvidia").mkdir(parents=True)
        options, _ = self.captured(self.run_helper())
        self.assertNotIn("/proc/driver/nvidia", options)

    def test_nvidia_warns_without_valid_uvm(self):
        self.device("nvidia0")
        for kind in ("absent", "regular", "symlink", "character"):
            with self.subTest(uvm=kind):
                path = self.dev / "nvidia-uvm"
                path.unlink(missing_ok=True)
                if kind == "regular":
                    self.device("nvidia-uvm", character=False)
                elif kind == "symlink":
                    path.symlink_to(self.device("target"))
                    self.devices.add(str(path))
                    self.env["GPU_TEST_DEVICES"] = json.dumps(sorted(self.devices))
                elif kind == "character":
                    self.device("nvidia-uvm")
                result = self.run_helper()
                options, _ = self.captured(result)
                self.assertEqual("uvm" in result.stderr.lower(), kind != "character")
                self.assert_devices(options, ["nvidia0"] +
                                    (["nvidia-uvm"] if kind == "character" else []))

    def test_no_primary_fails_including_auxiliary_only(self):
        self.assert_failure(self.run_helper())
        for name in ("nvidiactl", "nvidia-uvm", "nvidia-uvm-tools", "nvidia-modeset",
                     "nvidia-caps/nvidia-cap0", "kfd"):
            self.device(name)
        self.assert_failure(self.run_helper())

    def test_invalid_names_and_noncharacters_do_not_count_as_primary(self):
        for name in ("nvidia", "nvidia0extra", "nvidia-1", "nvidia1.2", "xnvidia0",
                     "dri/card", "dri/card0extra", "dri/card-1", "dri/xcard0",
                     "dri/renderD", "dri/renderD128extra", "dri/renderD-1",
                     "nvidia-caps/nvidia-cap", "nvidia-caps/nvidia-cap1extra"):
            self.device(name)
        for name in ("nvidia1", "dri/card1", "dri/renderD129", "kfd"):
            self.device(name, character=False)
        (self.dev / "nvidia2").mkdir()
        self.assert_failure(self.run_helper())
        self.device("nvidia0")
        options, _ = self.captured(self.run_helper())
        self.assert_devices(options, ["nvidia0"])

    def test_symlink_nodes_skipped_even_if_stat_reports_character(self):
        target = self.device("target")
        names = ["nvidia0", "dri/card0", "dri/renderD128", "nvidiactl", "nvidia-uvm",
                 "nvidia-uvm-tools", "nvidia-modeset", "nvidia-caps/nvidia-cap1", "kfd"]
        for name in names:
            path = self.dev / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.symlink_to(target)
            self.devices.add(str(path))
        (self.dev / "nvidia99").symlink_to(self.dev / "missing")
        self.env["GPU_TEST_DEVICES"] = json.dumps(sorted(self.devices))
        self.assert_failure(self.run_helper())
        self.device("nvidia1")
        options, _ = self.captured(self.run_helper())
        self.assert_devices(options, ["nvidia1"])

    def test_symlink_parent_directories_rejected(self):
        self.device("nvidia0")
        for parent, child in (("dri", "card0"), ("nvidia-caps", "nvidia-cap1")):
            with self.subTest(parent=parent):
                target = self.root / (parent + " external")
                target.mkdir()
                (target / child).touch()
                link = self.dev / parent
                link.symlink_to(target, target_is_directory=True)
                self.assert_failure(self.run_helper())
                link.unlink()


class GPUCLITests(GPUFixture):
    def test_help_documents_gpu(self):
        for flag in ("--help", "-h"):
            with self.subTest(flag=flag):
                result = self.run_launcher(flag)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("--gpu", result.stdout)
                self.assertFalse(self.capture.exists())

    def test_default_does_not_probe_or_mount_gpu(self):
        options, command = self.captured(self.run_launcher("-s", "session"))
        self.assertFalse(self.binds(options))
        self.assertNotIn("/sys", options)
        self.assertNotIn("/etc/ld.so.cache", options)
        self.assertFalse(any(call[0] == "stat" and "%F" in call
                             for call in self.command_calls()))
        self.assertEqual(command[3:], ["--", "-s", "session"])

    def test_cli_exits_on_helper_failure(self):
        self.assert_failure(self.run_fixture_launcher("--gpu", "-s", "session"))

    def test_repeated_flags_preserve_arguments_and_mount_once(self):
        self.device("dri/renderD128")
        options, command = self.captured(self.run_fixture_launcher(
            "--gpu", "-s", "session with spaces", "--gpu", "run", "prompt", ""))
        self.assert_devices(options, ["dri/renderD128"])
        self.assertEqual(command[3:], ["--", "-s", "session with spaces", "run", "prompt", ""])
        self.assertEqual(options.count("/sys"), 1)

    def test_composition_seccomp_namespaces_and_override_order(self):
        self.device("dri/renderD128")
        source = self.make_socket()
        seccomp = self.home / ".local/share/opencodebox/seccomp-security.bpf"
        seccomp.parent.mkdir(parents=True)
        seccomp.write_bytes(b"test filter")
        (self.bin / "uname").unlink()
        self.command("uname", "print('x86_64')")
        baseline, _ = self.captured(self.run_launcher())
        override = self.root / "GPU override"
        override.mkdir()
        options, command = self.captured(self.run_fixture_launcher(
            "--with-ro", f"{override}:/dev/dri", "--podman", "--gpu",
            "--persistent-tmp", "-s", "session", "--gpu"))
        self.assert_devices(options, ["dri/renderD128"])
        self.assertIn(["--bind", str(source), test_podman.DESTINATION], self.binds(options, "--bind"))
        self.assertIn(["--bind", str(self.project / ".opencodebox-tmp"), "/tmp"],
                      self.binds(options, "--bind"))
        self.assertEqual(command[3:], ["--", "-s", "session"])
        self.assertEqual(options[-3:], ["--ro-bind", str(override), "/dev/dri"])
        isolation = lambda args: [arg for arg in args if arg.startswith("--unshare")
                                  or arg in ("--share-net", "--die-with-parent", "--new-session")]
        self.assertEqual(isolation(options), isolation(baseline))
        for args in (baseline, options):
            self.assertEqual(args.count("--seccomp"), 1)
            self.assertTrue(args[args.index("--seccomp") + 1].isdigit())
            self.assertIn(["--dev", "/dev"], [args[i:i + 2] for i in range(len(args))])


if __name__ == "__main__":
    unittest.main()
