"""Shared black-box harness for opencodebox launcher tests.

The launcher (a bash script) runs with an allowlisted temporary PATH and an
empty temporary HOME. Bubblewrap and application execution are mocked: the
mock `bwrap` records its argv as JSON instead of creating a sandbox.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

LAUNCHER = Path(__file__).resolve().parent.parent / "opencodebox"


class Launcher:
    def __init__(self, root: Path):
        self.root = root
        self.home = root / "home"
        self.project = root / "project with spaces"
        self.bin = root / "bin"
        for directory in (self.home, self.project, self.bin):
            directory.mkdir(parents=True)
        self.capture = root / "bwrap.json"
        self.calls = root / "calls.jsonl"
        self.env = {
            "HOME": str(self.home),
            "PATH": str(self.bin),
            "LC_ALL": "C",
            "CAPTURE": str(self.capture),
            "CALLS": str(self.calls),
        }
        for name in ("bash", "cat", "dirname", "uname", "id", "stat", "realpath"):
            target = shutil.which(name, path="/usr/bin:/bin")
            assert target is not None, name
            (self.bin / name).symlink_to(target)
        self.command("bwrap", """
Path(os.environ['CAPTURE']).write_text(json.dumps(sys.argv[1:]))
""")
        self.command("opencode", """
if sys.argv[1:] == ['--version']:
    print('9.9.9-test')
else:
    raise SystemExit('opencode must not execute')
""")
        self.command("git", "raise SystemExit(1)")

    def command(self, name: str, body: str) -> None:
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

    def run_launcher(self, *args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
        self.capture.unlink(missing_ok=True)
        self.calls.unlink(missing_ok=True)
        result = subprocess.run(
            [str(self.bin / "bash"), str(LAUNCHER), *args],
            cwd=str(cwd or self.project),
            env=self.env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        calls = self.command_calls()
        assert not any(call[0] == "opencode" and call[1:] != ["--version"] for call in calls), calls
        return result

    def command_calls(self) -> list:
        if not self.calls.exists():
            return []
        return [json.loads(line) for line in self.calls.read_text().splitlines()]

    def captured(self, result: subprocess.CompletedProcess) -> tuple[list, list]:
        assert result.returncode == 0, result.stdout + result.stderr
        assert self.capture.exists(), result.stdout + result.stderr
        argv = json.loads(self.capture.read_text())
        split = argv.index("--")
        return argv[:split], argv[split + 1:]

    def assert_rejected(self, *args: str, cwd: Path | None = None) -> str:
        result = self.run_launcher(*args, cwd=cwd)
        assert result.returncode != 0, result.stdout + result.stderr
        assert not self.capture.exists(), "bwrap ran despite invalid input"
        assert (result.stdout + result.stderr).strip(), "missing failure diagnostic"
        return result.stderr


@pytest.fixture
def launcher(tmp_path: Path) -> Launcher:
    return Launcher(tmp_path)


def seqs(options: list, n: int) -> list:
    return [options[i:i + n] for i in range(len(options) - n + 1)]


def has_seq(options: list, seq: list) -> bool:
    return seq in seqs(options, len(seq))
