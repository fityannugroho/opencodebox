"""Black-box --podman regression tests; no services or containers are started.

Run with: python3 -m unittest -v test_podman.py
Only an allowlisted temporary PATH and an empty temporary HOME reach the launcher.
"""

import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest


LAUNCHER = Path(__file__).resolve().with_name("opencodebox")
DESTINATION = "/run/opencodebox/podman.sock"


class LauncherTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ocb-launcher-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / "home"
        self.project = self.root / "project with spaces"
        self.runtime = self.root / "runtime with spaces"
        self.bin = self.root / "bin"
        for directory in (self.home, self.project, self.runtime, self.bin):
            directory.mkdir()
        self.capture = self.root / "bwrap.json"
        self.calls = self.root / "calls.jsonl"
        self.uid = os.getuid() or 12345
        self.env = {
            "HOME": str(self.home),
            "PATH": str(self.bin),
            "PWD": str(self.project),
            "XDG_RUNTIME_DIR": str(self.runtime),
            "XDG_CONFIG_HOME": str(self.home / ".config"),
            "XDG_DATA_HOME": str(self.home / ".local/share"),
            "XDG_CACHE_HOME": str(self.home / ".cache"),
            "LC_ALL": "C",
            "CAPTURE": str(self.capture),
            "CALLS": str(self.calls),
            "TEST_UID": str(self.uid),
            "CONTAINER_HOST": "unix:///inherited-host.sock",
            "CONTAINER_CONNECTION": "inherited-connection",
        }
        # No host PATH entries: optional tools and personal config stay isolated.
        for name in ("bash", "cat", "dirname", "uname", "mkdir"):
            target = shutil.which(name, path="/usr/bin:/bin")
            self.assertIsNotNone(target, name)
            (self.bin / name).symlink_to(target)
        self.command("bwrap", """
Path(os.environ['CAPTURE']).write_text(json.dumps(sys.argv[1:]))
""")
        self.command("opencode", "raise SystemExit('opencode must not execute')")
        self.command("podman", "raise SystemExit('podman must not execute')")
        self.command("git", "raise SystemExit(1)")
        self.command("id", """
values = {'-u': os.environ['TEST_UID'], '-g': '12345',
          '-un': 'test-user', '-gn': 'test-group'}
print(values[sys.argv[1]])
""")
        real_stat = shutil.which("stat", path="/usr/bin:/bin")
        real_realpath = shutil.which("realpath", path="/usr/bin:/bin")
        self.assertIsNotNone(real_stat)
        self.assertIsNotNone(real_realpath)
        self.command("stat", f"""
if '%u' in sys.argv and (os.getuid() == 0 or 'TEST_OWNER' in os.environ):
    print(os.environ.get('TEST_OWNER', os.environ['TEST_UID']))
else:
    os.execv({real_stat!r}, ['stat', *sys.argv[1:]])
""")
        self.command("realpath", f"""
args = sys.argv[1:]
fallback = '/run/user/' + os.environ['TEST_UID'] + '/podman/podman.sock'
if 'FALLBACK_SOCKET' in os.environ and args[-1] == fallback:
    args[-1] = os.environ['FALLBACK_SOCKET']
os.execv({real_realpath!r}, ['realpath', *args])
""")

    def command(self, name, body):
        path = self.bin / name
        path.write_text(
            f"#!{sys.executable}\n"
            "import json, os, sys\nfrom pathlib import Path\n"
            "with open(os.environ['CALLS'], 'a') as log:\n"
            "    log.write(json.dumps([Path(sys.argv[0]).name, *sys.argv[1:]]) + '\\n')\n"
            + body
            + "\n"
        )
        path.chmod(0o755)

    def make_socket(self, path=None):
        path = path or self.runtime / "podman/podman.sock"
        path.parent.mkdir(parents=True, exist_ok=True)
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.addCleanup(sock.close)
        sock.bind(str(path))
        return path

    def run_launcher(self, *args):
        self.capture.unlink(missing_ok=True)
        self.calls.unlink(missing_ok=True)
        result = subprocess.run(
            [str(self.bin / "bash"), str(LAUNCHER), *args],
            cwd=self.project, env=self.env, capture_output=True, text=True,
            timeout=10,
        )
        calls = self.command_calls()
        self.assertFalse(any(call[0] in ("podman", "opencode") for call in calls), calls)
        return result

    def command_calls(self):
        if not self.calls.exists():
            return []
        return [json.loads(line) for line in self.calls.read_text().splitlines()]

    def captured(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(self.capture.exists(), result.stdout + result.stderr)
        argv = json.loads(self.capture.read_text())
        split = argv.index("--")
        return argv[:split], argv[split + 1:]

class PodmanTests(LauncherTestCase):
    def assert_rejected(self, *diagnostic_words):
        result = self.run_launcher("--podman", "-s", "session")
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(self.capture.exists(), "bwrap ran despite invalid Podman setup")
        diagnostic = (result.stdout + result.stderr).lower()
        self.assertTrue(any(word in diagnostic for word in diagnostic_words), diagnostic)

    def assert_forwarded(self, result, source, args):
        options, command = self.captured(result)
        binds = [options[i:i + 3] for i, arg in enumerate(options)
                 if arg in ("--bind", "--ro-bind", "--bind-try", "--ro-bind-try")]
        podman_binds = [bind for bind in binds
                        if bind[2] == DESTINATION or str(self.runtime) in bind[1]
                        or bind[1] == str(source)]
        self.assertEqual(podman_binds, [["--bind", str(source), DESTINATION]])
        self.assertEqual(options.count(DESTINATION), 1)
        for flag in ("--unshare-pid", "--unshare-ipc", "--unshare-uts", "--die-with-parent"):
            self.assertIn(flag, options)
        for directory in ("/run", "/run/opencodebox"):
            self.assertIn(["--dir", directory],
                          [options[i:i + 2] for i in range(len(options))])
        self.assertNotIn("DOCKER_HOST", options)
        settings = [options[i:i + 3] for i, arg in enumerate(options)
                    if arg == "--setenv" and options[i + 1].startswith("CONTAINER_")]
        self.assertEqual(settings, [["--setenv", "CONTAINER_HOST", "unix://" + DESTINATION]])
        self.assertIn(["--unsetenv", "CONTAINER_CONNECTION"],
                      [options[i:i + 2] for i in range(len(options))])
        # The launcher wraps OpenCode in bash -c; arguments follow its $0 marker.
        self.assertEqual(command[0:2], ["bash", "-c"])
        self.assertIn('/opt/opencode/opencode "$@"', command[2])
        self.assertEqual(command[3:], ["--", *args])
        owner_calls = [call for call in self.command_calls() if call[0] == "stat"
                       and "%u" in call]
        self.assertTrue(any("-L" in call and "-c" in call and str(source) in call
                            for call in owner_calls), owner_calls)

    def test_help_documents_podman_without_socket_or_command(self):
        (self.bin / "podman").unlink()
        for flag in ("--help", "-h"):
            with self.subTest(flag=flag):
                result = self.run_launcher(flag)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("--podman", result.stdout)
                self.assertFalse(self.capture.exists())

    def test_success_with_spaces_and_opencode_arguments(self):
        source = self.make_socket()
        args = ["-s", "session with spaces", "run", "a prompt", "", "--model", "test/model"]
        self.assert_forwarded(self.run_launcher("--podman", *args), source, args)

    def test_repeated_flag_is_idempotent_and_preserves_argument_order(self):
        source = self.make_socket()
        self.assert_forwarded(
            self.run_launcher("--podman", "-s", "session", "--podman", "run", "prompt"),
            source, ["-s", "session", "run", "prompt"],
        )

    def test_symlink_socket_is_resolved_with_realpath_e(self):
        source = self.make_socket(self.root / "actual socket")
        link = self.runtime / "podman/podman.sock"
        link.parent.mkdir()
        link.symlink_to(source)
        self.assert_forwarded(self.run_launcher("--podman"), source, [])
        self.assertIn(["realpath", "-e", str(link)], self.command_calls())

    def test_without_flag_needs_neither_socket_nor_podman(self):
        (self.bin / "podman").unlink()
        # Invalid Podman runtime must be irrelevant without opt-in.
        self.env["XDG_RUNTIME_DIR"] = "relative-runtime"
        options, command = self.captured(self.run_launcher("-s", "session", "run", "prompt"))
        self.assertFalse(any("podman" in item.lower() or item.startswith("CONTAINER_")
                             for item in options
                             if not item.startswith(str(self.root))))
        self.assertEqual(command[3:], ["--", "-s", "session", "run", "prompt"])
        self.assertFalse(any(call[0] in ("stat", "realpath") for call in self.command_calls()))

    def test_missing_socket(self):
        self.assert_rejected("socket", "podman.sock")

    def test_missing_socket_explains_host_setup(self):
        result = self.run_launcher("--podman")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("systemctl --user start podman.socket", result.stderr)
        self.assertFalse(self.capture.exists())

    def test_extra_binds_and_seccomp_are_preserved(self):
        source = self.make_socket()
        # bwrap is mocked: bytes need not be an actual BPF program, but the
        # launcher must still open and forward the filter FD when opted in.
        seccomp = self.home / ".local/share/opencodebox/seccomp-security.bpf"
        seccomp.parent.mkdir(parents=True)
        seccomp.write_bytes(b"test filter")
        extra = self.root / "extra data"
        extra.mkdir()
        result = self.run_launcher("--with-ro", str(extra), "--podman", "-s", "session")
        self.assert_forwarded(result, source, ["-s", "session"])
        options, _ = self.captured(result)
        self.assertIn("--seccomp", options)
        self.assertEqual(options[-3:], ["--ro-bind", str(extra), str(extra)])

    def test_regular_file_is_not_a_socket(self):
        path = self.runtime / "podman/podman.sock"
        path.parent.mkdir()
        path.touch()
        self.assert_rejected("socket")

    def test_relative_runtime_is_rejected_even_with_existing_socket(self):
        self.make_socket(self.project / "relative/podman/podman.sock")
        self.env["XDG_RUNTIME_DIR"] = "relative"
        self.assert_rejected("absolute", "runtime")

    def test_incorrect_socket_owner(self):
        self.make_socket()
        self.env["TEST_OWNER"] = str(self.uid + 1)
        self.assert_rejected("owner", "owned", "uid")

    def test_root_is_rejected(self):
        self.make_socket()
        self.env["TEST_UID"] = "0"
        self.env["TEST_OWNER"] = "0"
        self.assert_rejected("root", "uid")

    def test_missing_podman_command(self):
        self.make_socket()
        (self.bin / "podman").unlink()
        self.assert_rejected("podman")

    def test_default_runtime_when_unset_or_empty(self):
        source = self.make_socket()
        self.env["FALLBACK_SOCKET"] = str(source)
        for value in (None, ""):
            with self.subTest(runtime=value):
                if value is None:
                    self.env.pop("XDG_RUNTIME_DIR", None)
                else:
                    self.env["XDG_RUNTIME_DIR"] = value
                self.assert_forwarded(self.run_launcher("--podman"), source, [])
                self.assertIn(
                    ["realpath", "-e", f"/run/user/{self.uid}/podman/podman.sock"],
                    self.command_calls(),
                )


if __name__ == "__main__":
    unittest.main()
