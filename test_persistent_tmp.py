"""Black-box persistent /tmp tests; bubblewrap and application execution are mocked.

Run with: PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -v
"""

import os
import shutil
import stat
import unittest

import test_podman


class PersistentTmpTests(test_podman.LauncherTestCase):
    def setUp(self):
        super().setUp()
        self.tmp = self.project / ".opencodebox-tmp"
        # Log creation while preserving real mkdir semantics and permissions.
        real_mkdir = shutil.which("mkdir", path="/usr/bin:/bin")
        self.assertIsNotNone(real_mkdir)
        (self.bin / "mkdir").unlink()
        self.command("mkdir", f"""
if os.environ.get('FAIL_TMP_MKDIR') == '1' and os.environ['TMP_PATH'] in sys.argv:
    print('mkdir: simulated failure', file=sys.stderr)
    raise SystemExit(1)
os.execv({real_mkdir!r}, ['mkdir', *sys.argv[1:]])
""")
        self.env["TMP_PATH"] = str(self.tmp)

    def tmp_mkdir_calls(self):
        return [call for call in self.command_calls()
                if call[0] == "mkdir" and str(self.tmp) in call]

    def assert_persistent(self, result, args=()):
        options, command = self.captured(result)
        mounts = [options[i:i + 3] for i, arg in enumerate(options)
                  if arg in ("--bind", "--ro-bind") and options[i + 2] == "/tmp"]
        self.assertEqual(mounts, [["--bind", str(self.tmp), "/tmp"]])
        self.assertNotIn(["--tmpfs", "/tmp"],
                         [options[i:i + 2] for i in range(len(options))])
        self.assertEqual(command[:2], ["bash", "-c"])
        self.assertIn('/opt/opencode/opencode "$@"', command[2])
        self.assertEqual(command[3:], ["--", *args])
        self.assertTrue(self.tmp.is_dir())
        self.assertFalse(self.tmp.is_symlink())
        self.assertEqual(stat.S_IMODE(self.tmp.stat().st_mode), 0o700)
        return options

    def assert_rejected(self):
        result = self.run_launcher("--persistent-tmp")
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(self.capture.exists(), "bwrap ran with unsafe persistent /tmp")
        self.assertTrue(result.stderr.strip(), "missing failure diagnostic")

    def test_fresh_directory_uses_explicit_private_mkdir_mode(self):
        # A permissive inherited umask must not expose the new directory.
        old_umask = os.umask(0)
        try:
            self.assert_persistent(self.run_launcher("--persistent-tmp"))
        finally:
            os.umask(old_umask)
        self.assertEqual(self.tmp.stat().st_uid, os.getuid())
        calls = self.tmp_mkdir_calls()
        self.assertEqual(len(calls), 1, calls)
        self.assertIn(["-m", "700"],
                      [calls[0][i:i + 2] for i in range(len(calls[0]))])

    def test_reuses_directory_and_preserves_contents_across_launches(self):
        self.tmp.mkdir(mode=0o700)
        nested = self.tmp / "nested data"
        nested.mkdir()
        sentinel = nested / "keep me"
        sentinel.write_bytes(b"persistent contents\x00\xff")
        inode = self.tmp.stat().st_ino
        for _ in range(2):
            self.assert_persistent(self.run_launcher("--persistent-tmp"))
            self.assertEqual(self.tmp.stat().st_ino, inode)
            self.assertEqual(sentinel.read_bytes(), b"persistent contents\x00\xff")
            self.assertEqual(self.tmp_mkdir_calls(), [])

    def test_default_tmpfs_does_not_create_directory(self):
        options, _ = self.captured(self.run_launcher())
        self.assertIn(["--tmpfs", "/tmp"],
                      [options[i:i + 2] for i in range(len(options))])
        self.assertFalse(self.tmp.exists())
        self.assertEqual(self.tmp_mkdir_calls(), [])

    def test_default_ignores_unsafe_existing_paths(self):
        for kind in ("file", "symlink", "dangling", "mode", "owner"):
            with self.subTest(kind=kind):
                target = self.root / "absent target"
                if kind == "file":
                    self.tmp.write_text("keep")
                elif kind in ("symlink", "dangling"):
                    self.tmp.symlink_to(self.home if kind == "symlink" else target)
                else:
                    self.tmp.mkdir(mode=0o755 if kind == "mode" else 0o700)
                self.env["TEST_OWNER"] = str(self.uid + 1)
                before = self.tmp.lstat()
                options, _ = self.captured(self.run_launcher())
                self.assertIn(["--tmpfs", "/tmp"],
                              [options[i:i + 2] for i in range(len(options))])
                self.assertEqual(self.tmp.lstat(), before)
                self.assertEqual(self.tmp_mkdir_calls(), [])
                if self.tmp.is_symlink() or self.tmp.is_file():
                    self.tmp.unlink()
                else:
                    self.tmp.rmdir()
                self.assertFalse(target.exists())

    def test_help_documents_flag_without_creating_directory(self):
        for args in (("--help",), ("-h",), ("--persistent-tmp", "--help"),
                     ("--help", "--persistent-tmp")):
            with self.subTest(args=args):
                result = self.run_launcher(*args)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertFalse(self.tmp.exists())
                self.assertEqual(self.tmp_mkdir_calls(), [])
                self.assertFalse(self.capture.exists())
                self.assertIn("--persistent-tmp", result.stdout)

    def test_rejects_symlink_to_private_owned_directory(self):
        target = self.root / "private target"
        target.mkdir(mode=0o700)
        sentinel = target / "keep"
        sentinel.write_text("untouched")
        self.tmp.symlink_to(target)
        self.assert_rejected()
        self.assertTrue(self.tmp.is_symlink())
        self.assertEqual(sentinel.read_text(), "untouched")

    def test_rejects_dangling_symlink(self):
        target = self.root / "missing target"
        self.tmp.symlink_to(target)
        self.assert_rejected()
        self.assertTrue(self.tmp.is_symlink())
        self.assertFalse(target.exists())

    def test_rejects_regular_file(self):
        self.tmp.write_text("untouched")
        self.tmp.chmod(0o700)
        self.assert_rejected()
        self.assertEqual(self.tmp.read_text(), "untouched")

    def test_rejects_wrong_owner(self):
        self.tmp.mkdir(mode=0o700)
        self.env["TEST_OWNER"] = str(self.uid + 1)
        self.assert_rejected()
        self.assertEqual(stat.S_IMODE(self.tmp.stat().st_mode), 0o700)

    def test_rejects_every_nonexact_permission_mode(self):
        self.tmp.mkdir(mode=0o700)
        for mode in (0o777, 0o755, 0o750, 0o770, 0o711, 0o500, 0o600,
                     0o1700, 0o2700, 0o4700):
            with self.subTest(mode=oct(mode)):
                self.tmp.chmod(mode)
                try:
                    self.assert_rejected()
                    self.assertEqual(stat.S_IMODE(self.tmp.stat().st_mode), mode)
                finally:
                    self.tmp.chmod(0o700)

    def test_sensitive_project_is_rejected_before_creation(self):
        for project in (self.home, self.home / ".ssh", self.home / ".gnupg", self.root):
            with self.subTest(project=project):
                project.mkdir(mode=0o700, exist_ok=True)
                self.project = project
                self.tmp = project / ".opencodebox-tmp"
                self.env["PWD"] = str(project)
                self.assert_rejected()
                self.assertFalse(self.tmp.exists())
                self.assertEqual(self.tmp_mkdir_calls(), [])

    def test_mkdir_failure_stops_before_bwrap(self):
        self.env["FAIL_TMP_MKDIR"] = "1"
        self.assert_rejected()
        self.assertFalse(self.tmp.exists())
        self.assertEqual(len(self.tmp_mkdir_calls()), 1)

    def test_repeated_flags_spaces_and_resume_arguments_use_launch_directory(self):
        other = self.root / "resumed project"
        other.mkdir()
        args = ["-s", "session with spaces", str(other), "run", "a prompt", "", "--model", "test/model"]
        self.assert_persistent(self.run_launcher(
            "--persistent-tmp", *args[:2], "--persistent-tmp", *args[2:],
            "--persistent-tmp"), args)
        self.assertFalse((other / ".opencodebox-tmp").exists())
        self.assertEqual(len(self.tmp_mkdir_calls()), 1)

    def test_persistent_bind_replaces_tmpfs_at_same_position(self):
        default, _ = self.captured(self.run_launcher())
        persistent = self.assert_persistent(self.run_launcher("--persistent-tmp"))
        index = default.index("/tmp") - 1
        self.assertEqual(default[index:index + 2], ["--tmpfs", "/tmp"])
        self.assertEqual(persistent[index:index + 3], ["--bind", str(self.tmp), "/tmp"])
        self.assertEqual(persistent[:index], default[:index])
        self.assertEqual(persistent[index + 3:], default[index + 2:])

    def test_composes_with_podman_and_extra_binds(self):
        source = self.make_socket()
        extra = self.root / "extra data"
        extra.mkdir()
        for flag in ("--with", "--with-ro"):
            with self.subTest(flag=flag):
                options = self.assert_persistent(self.run_launcher(
                    flag, str(extra), "--persistent-tmp", "--podman", "-s", "session"),
                    ["-s", "session"])
                self.assertIn(["--bind", str(source), test_podman.DESTINATION],
                              [options[i:i + 3] for i in range(len(options))])
                self.assertIn(["--setenv", "CONTAINER_HOST", "unix://" + test_podman.DESTINATION],
                              [options[i:i + 3] for i in range(len(options))])
                self.assertEqual(options[-3:],
                                 ["--bind" if flag == "--with" else "--ro-bind", str(extra), str(extra)])

    def test_explicit_tmp_overrides_remain_last_wins_in_argument_order(self):
        first = self.root / "first tmp"
        last = self.root / "last tmp"
        first.mkdir()
        last.mkdir()
        for flags in (("--with", "--with-ro"), ("--with-ro", "--with")):
            with self.subTest(flags=flags):
                options, command = self.captured(self.run_launcher(
                    flags[0], f"{first}:/tmp", "--persistent-tmp",
                    flags[1], f"{last}:/tmp"))
                binds = [options[i:i + 3] for i, arg in enumerate(options)
                         if arg in ("--bind", "--ro-bind") and options[i + 2] == "/tmp"]
                expected = [["--bind", str(self.tmp), "/tmp"],
                            ["--bind" if flags[0] == "--with" else "--ro-bind", str(first), "/tmp"],
                            ["--bind" if flags[1] == "--with" else "--ro-bind", str(last), "/tmp"]]
                self.assertEqual(binds, expected)
                self.assertEqual(options[-6:], expected[1] + expected[2])
                self.assertEqual(command[3:], ["--"])


if __name__ == "__main__":
    unittest.main()
