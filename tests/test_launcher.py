"""Smoke tests for current main behavior. All black-box: bwrap is mocked,
argv is asserted. No sandbox ever runs here."""

from conftest import Launcher, has_seq, seqs


def test_help_exits_zero_without_bwrap(launcher):
    for args in (("--help",), ("-h",)):
        result = launcher.run_launcher(*args)
        assert result.returncode == 0, result.stderr
        assert "--with" in result.stdout
        assert not launcher.capture.exists()


def test_version_reports_opencodebox(launcher):
    for args in (("--version",), ("-v",)):
        result = launcher.run_launcher(*args)
        assert result.returncode == 0, result.stderr
        assert "opencodebox v" in result.stdout


def test_unconditional_mounts(launcher):
    options, command = launcher.captured(launcher.run_launcher())
    for flag in ("--unshare-pid", "--unshare-ipc", "--unshare-uts", "--die-with-parent"):
        assert flag in options
    assert has_seq(options, ["--dev", "/dev"])
    assert has_seq(options, ["--proc", "/proc"])
    assert has_seq(options, ["--tmpfs", "/tmp"])
    assert has_seq(options, ["--ro-bind", "/usr", "/usr"])
    assert has_seq(options, ["--symlink", "usr/lib64", "/lib64"])
    assert has_seq(options, ["--symlink", "usr/bin", "/bin"])
    assert has_seq(options, ["--symlink", "usr/lib", "/lib"])
    assert has_seq(options, ["--hostname", "opencodebox"])
    assert has_seq(options, ["--ro-bind", "/etc/resolv.conf", "/etc/resolv.conf"])
    assert has_seq(options, ["--ro-bind", "/etc/hosts", "/etc/hosts"])
    assert has_seq(options, ["--ro-bind-try", "/etc/nsswitch.conf", "/etc/nsswitch.conf"])
    assert has_seq(options, ["--ro-bind-try", "/etc/ssl", "/etc/ssl"])
    assert has_seq(options, ["--ro-bind-try", "/etc/ca-certificates", "/etc/ca-certificates"])
    assert has_seq(options, ["--ro-bind-try", "/etc/pki/ca-trust", "/etc/pki/ca-trust"])
    assert has_seq(options, ["--ro-bind-try", "/etc/pki/tls/certs", "/etc/pki/tls/certs"])
    assert has_seq(options, ["--ro-bind", "/etc/alternatives", "/etc/alternatives"])
    assert command[:2] == ["bash", "-c"]
    assert command[3:] == ["--"]


def test_user_and_project_paths(launcher):
    options, _ = launcher.captured(launcher.run_launcher())
    home = str(launcher.home)
    assert has_seq(options, ["--ro-bind", f"{home}/.local", f"{home}/.local"])
    assert has_seq(options, ["--tmpfs", f"{home}/.cache"])
    assert has_seq(options, ["--ro-bind", f"{home}/.config/opencode", f"{home}/.config/opencode"])
    assert has_seq(options, ["--ro-bind", f"{home}/.agents", f"{home}/.agents"])
    assert has_seq(options, ["--ro-bind", f"{home}/.cache/opencode", f"{home}/.cache/opencode"])
    assert has_seq(options, ["--bind", f"{home}/.local/share/opencode", f"{home}/.local/share/opencode"])
    assert has_seq(options, ["--bind", f"{home}/.local/state/opencode", f"{home}/.local/state/opencode"])
    assert has_seq(options, ["--bind", str(launcher.project), str(launcher.project)])
    assert has_seq(options, ["--bind", str(launcher.bin), "/opt/opencode"])
    assert any(s[:1] == ["--file"] and s[-1] == "/etc/passwd" for s in seqs(options, 3))
    assert any(s[:1] == ["--file"] and s[-1] == "/etc/group" for s in seqs(options, 3))


def test_missing_state_dir_created_before_bind(launcher):
    # bwrap's plain --bind hard-fails if the source path is absent. Simulate an
    # opencode-v1 host (application data present) that has not created the v2
    # state dir yet, so ~/.local/state/opencode does not exist on the host.
    state_dir = launcher.home / ".local/state/opencode"
    (launcher.home / ".local/share/opencode").mkdir(parents=True)  # v1 data still present
    assert not state_dir.exists()
    options, _ = launcher.captured(launcher.run_launcher())
    # The launcher must create the bind source before bwrap runs, and still bind it.
    assert state_dir.is_dir(), "launcher must mkdir -p ~/.local/state/opencode before binding"
    assert has_seq(options, ["--bind", str(state_dir), str(state_dir)])


def test_with_overrides_tmpfs_last_wins(launcher):
    extra = launcher.root / "extra"
    extra.mkdir()
    options, _ = launcher.captured(launcher.run_launcher("--with", f"{extra}:/tmp"))
    triples = seqs(options, 3)
    pairs = seqs(options, 2)
    assert ["--bind", str(extra), "/tmp"] in triples
    assert ["--tmpfs", "/tmp"] in pairs
    assert triples.index(["--bind", str(extra), "/tmp"]) > pairs.index(["--tmpfs", "/tmp"])


def test_with_ro_custom_destination_is_last(launcher):
    # EXTRA_BINDS is appended after --seccomp in the launcher, so user binds
    # always form the tail regardless of conditional mounts.
    extra = launcher.root / "extra"
    extra.mkdir()
    options, _ = launcher.captured(launcher.run_launcher("--with-ro", f"{extra}:/custom/path"))
    assert options[-3:] == ["--ro-bind", str(extra), "/custom/path"]


def test_seccomp_fd_forwarded_when_filter_present(launcher):
    seccomp_dir = launcher.home / ".local/share/opencodebox"
    seccomp_dir.mkdir(parents=True)
    (seccomp_dir / "seccomp-security.bpf").write_bytes(b"fake filter")
    options, _ = launcher.captured(launcher.run_launcher())
    idx = options.index("--seccomp")
    assert options[idx + 1].isdigit()


def test_passthrough_args_reach_opencode(launcher):
    _, command = launcher.captured(launcher.run_launcher("-s", "session", "run", "prompt"))
    assert command[3:] == ["--", "-s", "session", "run", "prompt"]


def test_reject_sensitive_and_bad_binds(launcher):
    launcher.assert_rejected("--with", str(launcher.home))
    launcher.assert_rejected("--with", "/")
    launcher.assert_rejected("--with")
    launcher.assert_rejected("--with", "/path/does/not/exist")
    launcher.assert_rejected("--with-ro")


def test_reject_sensitive_working_directory(launcher):
    launcher.assert_rejected(cwd=launcher.home)


def test_ssh_permissions_enforced(launcher):
    ssh = launcher.home / ".ssh"
    ssh.mkdir(mode=0o755)
    err = launcher.assert_rejected()
    assert "0700" in err
    ssh.chmod(0o700)
    options, _ = launcher.captured(launcher.run_launcher())
    assert has_seq(options, ["--dir", str(ssh)])


def test_shim_launcher_keeps_relative_layout(launcher_shim):
    # npm-style installs exec their target relative to the shim, so the parent
    # of node_modules/.bin must be mounted, not the .bin directory alone.
    options, command = launcher_shim.captured(launcher_shim.run_launcher())
    assert has_seq(options, ["--ro-bind", str(launcher_shim.bin.parent), "/opt/opencode"])
    assert "/opt/opencode/.bin/opencode" in command[2]


def test_plain_bin_launcher_argv_unchanged(launcher):
    # Non-.bin layouts must keep the pre-existing argv and exec target.
    options, command = launcher.captured(launcher.run_launcher())
    assert has_seq(options, ["--bind", str(launcher.bin), "/opt/opencode"])
    assert "/opt/opencode/opencode" in command[2]


def test_shim_under_home_falls_back_without_binding_home(tmp_path):
    # ~/.bin/opencode: parent is $HOME, which must never enter the sandbox.
    launcher = Launcher(tmp_path, bin_dir=tmp_path / "home/.bin")
    options, command = launcher.captured(launcher.run_launcher())
    assert has_seq(options, ["--bind", str(launcher.bin), "/opt/opencode"])
    assert "/opt/opencode/opencode" in command[2]
