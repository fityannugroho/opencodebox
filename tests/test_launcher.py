"""Smoke tests for current main behavior. All black-box: bwrap is mocked,
argv is asserted. No sandbox ever runs here."""

import pytest

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
        assert result.stdout.splitlines()[0] == "opencode v9.9.9-test"


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
    # EXTRA_BINDS is appended after --seccomp, but the v2 isolation overlay
    # binds come after EXTRA_BINDS (later binds win), so the overlay forms
    # the tail and user binds sit immediately before it.
    extra = launcher.root / "extra"
    extra.mkdir()
    options, _ = launcher.captured(launcher.run_launcher("--with-ro", f"{extra}:/custom/path"))
    overlay_src = str(launcher.home / ".local/share/opencodebox/disabled-service.json")
    config = str(launcher.home / ".config/opencode")
    tail = ["--ro-bind", overlay_src, f"{config}/service.json",
            "--ro-bind", overlay_src, f"{config}/service-local.json",
            "--ro-bind", overlay_src, f"{config}/service-prod.json"]
    triples = seqs(options, 3)
    assert triples.index(["--ro-bind", str(extra), "/custom/path"]) < len(options) - len(tail)


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
    assert 'exec "/opt/opencode/.bin/opencode" "$@"' in command[2]


def test_plain_bin_launcher_mount_and_target_unchanged(launcher):
    # Non-.bin layouts must keep the pre-existing mount and exec target.
    options, command = launcher.captured(launcher.run_launcher())
    assert has_seq(options, ["--bind", str(launcher.bin), "/opt/opencode"])
    assert 'exec "/opt/opencode/opencode" "$@"' in command[2]


def test_shim_under_home_falls_back_without_binding_home(tmp_path):
    # $HOME/.bin/opencode: $HOME must never enter the sandbox, so the shim is
    # not relocated and the user gets a warning instead.
    launcher = Launcher(tmp_path, bin_dir=tmp_path / "home/.bin")
    result = launcher.run_launcher()
    options, command = launcher.captured(result)
    assert has_seq(options, ["--bind", str(launcher.bin), "/opt/opencode"])
    assert 'exec "/opt/opencode/opencode" "$@"' in command[2]
    assert "Warning" in result.stderr


def test_non_node_modules_bin_falls_back(tmp_path):
    # The relocation is limited to the npm convention; any other ".bin"
    # directory must not pull its parent into the sandbox.
    launcher = Launcher(tmp_path, bin_dir=tmp_path / "vendor/.bin")
    result = launcher.run_launcher()
    options, command = launcher.captured(result)
    assert has_seq(options, ["--bind", str(launcher.bin), "/opt/opencode"])
    assert 'exec "/opt/opencode/opencode" "$@"' in command[2]
    assert "Warning" in result.stderr


def test_dot_dot_path_normalised_before_guard(tmp_path):
    # A PATH entry containing ".." must not change what gets mounted: the path
    # is normalised before the layout checks, and the fallback binds the
    # normalised directory (here the (fake) $HOME/.bin itself).
    launcher = Launcher(tmp_path, bin_dir=tmp_path / "home/.local/../.bin")
    result = launcher.run_launcher()
    options, command = launcher.captured(result)
    assert has_seq(options, ["--bind", str(tmp_path / "home/.bin"), "/opt/opencode"])
    assert 'exec "/opt/opencode/opencode" "$@"' in command[2]
    assert "Warning" in result.stderr


def test_missing_opencode_dirs_created_before_bind(launcher):
    # bwrap hard-fails on missing bind sources, and opencode (v1 and v2)
    # mkdirs ~/.cache/opencode/bin at startup while the cache tree is read-only.
    dirs = (
        launcher.home / ".config/opencode",
        launcher.home / ".agents",
        launcher.home / ".cache/opencode/bin",
        launcher.home / ".local/share/opencode",
    )
    for directory in dirs:
        assert not directory.exists()
    launcher.captured(launcher.run_launcher())
    for directory in dirs:
        assert directory.is_dir(), f"launcher must create {directory}"


def test_version_handles_v2_prefixed_output(launcher):
    # opencode v2 prints "opencode v<version>"; v1 prints a bare version.
    launcher.command("opencode", """
if sys.argv[1:] == ['--version']:
    print('opencode v9.9.9-test')
else:
    raise SystemExit('opencode must not execute')
""")
    result = launcher.run_launcher("--version")
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[0] == "opencode v9.9.9-test"
    assert lines[1].startswith("opencodebox v")


def test_v2_overlay_forces_disabled_service(launcher):
    options, _ = launcher.captured(launcher.run_launcher())
    overlay_src = str(launcher.home / ".local/share/opencodebox/disabled-service.json")
    config = str(launcher.home / ".config/opencode")
    assert has_seq(options, ["--ro-bind", overlay_src, f"{config}/service.json"])
    assert has_seq(options, ["--ro-bind", overlay_src, f"{config}/service-local.json"])
    assert has_seq(options, ["--ro-bind", overlay_src, f"{config}/service-prod.json"])
    overlay_path = launcher.home / ".local/share/opencodebox/disabled-service.json"
    assert overlay_path.read_text() == '{\n  "disabled": true\n}\n'
    triples = seqs(options, 3)
    config_ro = [s for s in triples if s == ["--ro-bind", config, config]]
    assert config_ro, "config ro-bind must exist"
    assert triples.index(["--ro-bind", overlay_src, f"{config}/service.json"]) > triples.index(config_ro[0])
    assert triples.index(["--ro-bind", overlay_src, f"{config}/service-local.json"]) > triples.index(config_ro[0])
    assert triples.index(["--ro-bind", overlay_src, f"{config}/service-prod.json"]) > triples.index(config_ro[0])


def test_v2_overlay_after_extra_bind(launcher):
    extra = launcher.root / "extra"
    extra.mkdir()
    options, _ = launcher.captured(launcher.run_launcher("--with-ro", f"{extra}:/custom/path"))
    overlay_src = str(launcher.home / ".local/share/opencodebox/disabled-service.json")
    config = str(launcher.home / ".config/opencode")
    triples = seqs(options, 3)
    extra_idx = triples.index(["--ro-bind", str(extra), "/custom/path"])
    assert triples.index(["--ro-bind", overlay_src, f"{config}/service.json"]) > extra_idx
    assert triples.index(["--ro-bind", overlay_src, f"{config}/service-local.json"]) > extra_idx
    assert triples.index(["--ro-bind", overlay_src, f"{config}/service-prod.json"]) > extra_idx


def test_overlay_seeds_missing_service_config(launcher):
    config = launcher.home / ".config/opencode"
    launcher.captured(launcher.run_launcher())
    for name in ("service.json", "service-local.json", "service-prod.json"):
        path = config / name
        assert path.exists(), f"launcher must seed missing {name}"
        assert path.read_text() == '{}\n'


def test_overlay_preserves_existing_service_config(launcher):
    config = launcher.home / ".config/opencode"
    config.mkdir(parents=True)
    (config / "service.json").write_text('{"password": "pw"}')
    options, _ = launcher.captured(launcher.run_launcher())
    assert (config / "service.json").read_text() == '{"password": "pw"}'
    overlay_src = str(launcher.home / ".local/share/opencodebox/disabled-service.json")
    assert has_seq(options, ["--ro-bind", overlay_src, f"{config}/service.json"])


def test_overlay_prod_channel_covered(launcher):
    # A prod-channel binary reads service-prod.json; on a fresh host the file
    # is absent upstream, which reads as {} (not disabled) and would take the
    # managed path in-sandbox. The overlay must cover it exactly once.
    options, _ = launcher.captured(launcher.run_launcher())
    config = launcher.home / ".config/opencode"
    prod = config / "service-prod.json"
    assert prod.exists(), "launcher must seed missing service-prod.json"
    assert prod.read_text() == '{}\n'
    overlay_src = str(launcher.home / ".local/share/opencodebox/disabled-service.json")
    triples = seqs(options, 3)
    prod_seq = ["--ro-bind", overlay_src, f"{config}/service-prod.json"]
    assert triples.count(prod_seq) == 1, "prod overlay must appear exactly once"
    config_ro = [s for s in triples if s == ["--ro-bind", str(config), str(config)]]
    assert config_ro, "config ro-bind must exist"
    assert triples.index(prod_seq) > triples.index(config_ro[0])


@pytest.mark.parametrize("args", [
    ("pair",),
    ("pair", "--url", "http://127.0.0.1:4097"),
    ("pair", "--remote"),
    ("service", "status"),
    ("service", "start"),
    ("upgrade",),
    ("update",),
    ("uninstall",),
    ("serve", "--service"),
    ("serve", "--stdio"),
    ("--server", "http://127.0.0.1:1"),
    ("models", "--server", "http://127.0.0.1:1"),
    ("run", "--title", "--server"),
])
def test_isolation_refusals(launcher, args):
    result = launcher.run_launcher(*args)
    assert result.returncode == 1
    assert "cannot run in isolation" in result.stderr
    assert not launcher.capture.exists()


@pytest.mark.parametrize("args", [
    (),
    ("run", "hi"),
    ("models",),
    ("models", "--standalone"),
    ("mcp", "list"),
    ("plugin", "list"),
    ("debug", "agents"),
    ("acp",),
    ("serve", "--port", "4097"),
])
def test_isolation_allows_server_commands(launcher, args):
    _, command = launcher.captured(launcher.run_launcher(*args))
    assert command[3:] == ["--", *args]


def test_refusal_scanner_flag_values(launcher):
    _, command = launcher.captured(launcher.run_launcher("--prompt", "serve"))
    assert command[3:] == ["--", "--prompt", "serve"]
    result = launcher.run_launcher("--session", "x", "pair")
    assert result.returncode == 1
    assert "cannot run in isolation" in result.stderr
    assert not launcher.capture.exists()
    _, command = launcher.captured(launcher.run_launcher("mcp", "add", "f", "--", "--server", "x"))
    assert command[3:] == ["--", "mcp", "add", "f", "--", "--server", "x"]


def test_config_dir_precedence(launcher):
    import tempfile
    with tempfile.TemporaryDirectory(dir=str(launcher.root)) as tmp:
        launcher.env["XDG_CONFIG_HOME"] = tmp
        options, _ = launcher.captured(launcher.run_launcher())
        overlay_src = str(launcher.home / ".local/share/opencodebox/disabled-service.json")
        assert has_seq(options, ["--ro-bind", f"{tmp}/opencode", f"{tmp}/opencode"])
        assert has_seq(options, ["--ro-bind", overlay_src, f"{tmp}/opencode/service.json"])
    with tempfile.TemporaryDirectory(dir=str(launcher.root)) as tmp2:
        launcher.env["XDG_CONFIG_HOME"] = tmp2
        launcher.env["OPENCODE_CONFIG_DIR"] = tmp2 + "/custom"
        options, _ = launcher.captured(launcher.run_launcher())
        overlay_src = str(launcher.home / ".local/share/opencodebox/disabled-service.json")
        assert has_seq(options, ["--ro-bind", overlay_src, f"{tmp2}/custom/service.json"])
    launcher.env.pop("XDG_CONFIG_HOME", None)
    launcher.env["OPENCODE_CONFIG_DIR"] = str(launcher.home)
    result = launcher.run_launcher()
    assert result.returncode == 1
    assert "sensitive" in result.stderr


def test_help_mentions_isolation(launcher):
    result = launcher.run_launcher("--help")
    assert result.returncode == 0, result.stderr
    assert "Isolation:" in result.stdout


# Every command/flag the refusal scanner rejects must also be listed in the
# help text: a refusal the user cannot discover up front is a help bug.
REFUSED_BY_SCANNER = [
    "pair",
    "service",
    "serve --service",
    "serve --stdio",
    "--server",
    "upgrade",
    "update",
    "uninstall",
]


def test_help_lists_every_refused_command(launcher):
    result = launcher.run_launcher("--help")
    assert result.returncode == 0, result.stderr
    missing = [name for name in REFUSED_BY_SCANNER if f"'{name}'" not in result.stdout]
    assert not missing, f"help does not mention refused command(s): {missing}"
