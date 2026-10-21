# opencodebox

Run OpenCode inside a bubblewrap sandbox for security isolation.

`opencodebox` is a bash script that runs [OpenCode](https://opencode.ai) (AI coding assistant) inside a sandbox using [bubblewrap](https://github.com/containers/bubblewrap). The sandbox provides process isolation with Linux namespaces (PID, IPC, UTS) and restricted filesystem access.

## Features

- **Process Isolation**: Uses unshare PID, IPC, and UTS namespaces
- **Controlled Filesystem**: Most system filesystem mounted read-only
- **Custom Bind Mounts**: Add read-write or read-only access with `--with` and `--with-ro`
- **Device Access (Opt-In)**: Expose host device nodes with their host mode using `--device`
- **Mise Support**: Integrated with [mise](https://mise.jdx.dev) for tool management
- **SSH Agent Forwarding**: Supports SSH commit signing through the host `ssh-agent`
- **Seccomp Sandbox Filter**: Mitigates kernel privilege escalation vulnerabilities (see [details](#seccomp-sandbox-filter))

## Prerequisites

- [**bubblewrap** (`bwrap`)](https://github.com/containers/bubblewrap) - for sandboxing
- [**opencode**](https://opencode.ai) - AI coding assistant

> **Security Note (CVE-2017-5226):** Bubblewrap sandbox can be escaped via `TIOCSTI` ioctl if the kernel allows it. Since Linux 6.2, `TIOCSTI` is restricted when `dev.tty.legacy_tiocsti=0` (default). On older kernels, ensure bubblewrap >= 0.1.5 (uses `setsid()` fix) or enable seccomp filtering. The `install.sh` script performs this check automatically.

## Installation

```bash
curl -fsSL https://raw.githubusercontent.com/fityannugroho/opencodebox/main/install.sh | bash
```

This installs `opencodebox` to `~/.local/bin/opencodebox`. Make sure `~/.local/bin` is in your PATH.

Verify the installation :

```bash
opencodebox --version
```

## Usage

`opencodebox` is a wrapper for the `opencode` command. All arguments are passed through to `opencode` inside the sandbox, except the commands OpenCode 2 refuses in isolation (see `### OpenCode Launcher Mount`).

```bash
opencodebox [OPTIONS] [OPENCODE_ARGS...]
```

> **Note:** The `opencode` command stays available when you need it. We didn't replace it.

### Options

`opencodebox` adds the following options :

- `--with /host[:/sandbox]` - Bind host path read-write to sandbox
- `--with-ro /host[:/sandbox]` - Bind host path read-only to sandbox
- `--device /dev/NAME` - Expose one host device node with its host mode; repeat for more nodes; see [device access](#device-access-opt-in)

These options allow you to specify additional directories to mount inside the sandbox for read-write or read-only access.

### Examples

```bash
# Run sandboxed opencode in current directory
opencodebox

# Run sandboxed opencode with read-write access to /data
opencodebox --with /data

# Run sandboxed opencode with read-write access to /mnt/data mapped to /workspace/data
opencodebox --with /mnt/data:/workspace/data

# Run sandboxed opencode with read-only access to config
opencodebox --with-ro /etc/hosts

# Run sandboxed opencode with direct access to a serial port and a webcam
opencodebox --device /dev/ttyUSB0 --device /dev/video0

# Run sandboxed opencode server with specified bind mounts
opencodebox --with /data --with-ro /config serve
```

## How It Works

1. Parse arguments (`--with`, `--with-ro`, `--device`, `--version`, `--help`)
2. Check prerequisites (bwrap and opencode)
3. Load seccomp sandbox filter (see [details](#seccomp-sandbox-filter))
4. **Enforce security restrictions**:
   - Rejects running from `$HOME`, `~/.ssh`, `~/.gnupg`, or their ancestors
   - Rejects sensitive paths in `--with`/`--with-ro` binds
   - Validates `~/.ssh` directory permissions (must be `0700`)
5. Build bubblewrap sandbox with namespace isolation and bind mounts
6. Setup SSH (sanitized `.pub` keys, `known_hosts`, agent forwarding)
7. Add conditional tool mounts (bun, npm, pnpm, uv, pipenv, cargo, git, mise) and extra bind mounts
8. Execute opencode inside the sandbox

## Bind Mounts Structure

### Unconditional Mounts (Always Present)

**Read-Only:**
- `/usr` - System basics
- `/etc/resolv.conf`, `/etc/hosts` - DNS configuration and host mappings
- `/etc/nsswitch.conf` - Name service lookup configuration (if present)
- `/etc/ssl` - SSL/TLS certificates (if present)
- `/etc/ca-certificates` - CA certificate store (if present)
- `/etc/pki/ca-trust`, `/etc/pki/tls/certs` - Fedora/RHEL trust store and certificate targets for `/etc/ssl` symlinks (if present); `/etc/pki/tls/private` is not mounted
- `/etc/alternatives` - System alternatives (managed by update-alternatives)
- `/etc/passwd`, `/etc/group` - Synthetic identity: your user plus `root`, `nobody`, and every group you belong to on the host, so group and owner lookups (`getent`, initgroups) resolve inside the sandbox; ids not mapped into the sandbox's user namespace (any other host user) still display as `65534`/`nobody`; no system credential files are exposed
- `$HOME/.local` - User local data (except keyrings/tool data)
- `$HOME/.cache/opencode` - OpenCode cache
- `$HOME/.ssh/*.pub` - Sanitized OpenSSH public key material, when `$HOME/.ssh` is not a symlink
- `$HOME/.ssh/known_hosts` - SSH host key, read-only, for Git-over-SSH host verification
- `gpg.ssh.allowedSignersFile` - Configured SSH allowed signers file (if configured and under `$HOME`)
- OpenCode: config dir (`$OPENCODE_CONFIG_DIR`, else `$XDG_CONFIG_HOME/opencode`, else `~/.config/opencode`), `.agents`

**Read-Write:**
- Current project directory (`$PWD`)
- `$HOME/.local/share/opencode` - OpenCode application data
- `$HOME/.local/state/opencode` - OpenCode runtime state (locks, etc.)

**Tmpfs (Private, writable per-session):**
- `/tmp` - Temporary files
- `$HOME/.cache` - Universal cache
- `$HOME/.local/share/keyrings` - Exclude private keyring (if exists on host)

### OpenCode Launcher Mount

The directory holding the `opencode` binary is mounted at `/opt/opencode` and executed from there (read-write, as before). When `opencode` is an npm-style shim inside a `<...>/node_modules/.bin` directory, the sandbox instead mounts that `node_modules` directory **read-only** and executes the shim at `/opt/opencode/.bin/opencode`, because such shims resolve their target relative to their own location.

- The shim's target must resolve with exactly one `..` level (as with mise's npm backend/aube); a deeper path escapes the mounted root and fails.
- A shim outside `node_modules` (for example `$HOME/.bin`) is not relocated: it keeps the old mount, which binds only the shim's own directory, and prints a warning; expect `not found` (exit 127) for relative-target shims.
- Self-upgrading or installing plugins from inside the sandbox is not supported for the read-only layout: `opencode upgrade` writes to the install tree, and `opencode plugin add` updates the global configuration under `~/.config/opencode` (mounted read-only) — do both on the host instead (e.g. `mise upgrade opencode`).
- On first run the launcher creates opencode's config, agents, cache (including the helper `bin` directory), data and state directories when they are missing, so a fresh host does not fail at the bind step. OpenCode 2 starts from there; OpenCode 1 may still need its config seeded on the host (it writes `.gitignore` into `~/.config/opencode`, which is mounted read-only).
- Only `node_modules/.bin` shims are relocated. Global `bin` shims from `npm|pnpm|bun install -g` and symlinks whose target lies outside the mounted roots still fail with `not found` (exit 127).
- OpenCode 2 runs fully isolated: every command gets a private in-sandbox server — the launcher overlays a generated `{"disabled": true}` service config so the client can never attach to a host service — and masks the host service registration in the state directory, so nothing in the sandbox can read the host server URL/password — while sessions, credentials, and TUI state stay shared (the config directory is shared read-only). Commands needing the host service (`pair`, `service`, `serve --service`/`--stdio`, `--server`, `upgrade`, `update`, `uninstall`) are refused; each error names the alternative.

### Conditional Tool Mounts (Requires Tool Installed on Host)

Each tool is mounted only when `command -v <tool>` succeeds on the host. If the tool is not installed, none of its directories are bound into the sandbox.

| Tool | Read-Only Bind | Tmpfs |
|------|---------------|-------|
| **Bun** | `~/.bun` | `~/.bun/install/cache` |
| **npm** | `~/.npmrc` | `~/.npm` |
| **pnpm** | `~/.config/pnpm` | `~/.local/share/pnpm/store` |
| **uv** | `~/.config/uv` | — |
| **pipenv** | — | `~/.local/share/virtualenvs` |
| **Rust/Cargo** | `~/.rustup`, `~/.cargo/bin`, `~/.cargo/config.toml` | `~/.cargo/registry` |
| **Git** | `~/.gitconfig` | — |
| **Mise** | `~/.config/mise`, `~/.local/share/mise`, `~/.cache/mise` (or `MISE_CONFIG_DIR`/`MISE_DATA_DIR`/`MISE_CACHE_DIR` when set; a sensitive directory is skipped with a warning) | — |

## Security Restrictions

`opencodebox` enforces several security restrictions to prevent sandbox escape:

- **Project directory**: Cannot run from `$HOME`, `~/.ssh`, `~/.gnupg`, or their ancestors. Use a dedicated project directory.
- **Bind mounts**: `--with` and `--with-ro` reject paths that point to or enclose sensitive locations (`$HOME`, `~/.ssh`, `~/.gnupg`).
- **Device binds**: `--device` accepts only existing character or block device nodes under `/dev`; symlinks, symlinked parent directories, and unsafe path components are rejected.
- **State directory**: `~/.local/state/opencode` is mounted read-write so that locks and TUI state stay shared between the sandbox and the host. The launcher masks the host background-service registration inside the sandbox, but a sandboxed process can still create files in that directory, and the host client reads some of them (including the pty runtime registration). Treat that directory as shared state, not as a trust boundary.
- **SSH directory**: `~/.ssh` must have permissions `0700`. Fix with: `chmod 700 ~/.ssh`

## Device Access (Opt-In)

`--device /dev/NAME` exposes an individual host device node to programs running
**directly inside opencodebox**. Repeat the switch for multiple nodes:

```bash
opencodebox --device /dev/ttyUSB0
opencodebox --device /dev/video0 --device /dev/snd/controlC0
# Combine with other options:
opencodebox --device /dev/ttyUSB0 -s SESSION_ID
```

When testing a checkout, use `./opencodebox` rather than an older installed
copy, and launch from your normal host environment. No mounts are added to an
already running session.

### Same mode as on the host

Each node is bound with bubblewrap's `--dev-bind` at the **same path** inside
the sandbox's private `/dev`. The bind shares the host inode, so the device
keeps **the same mode (permission bits), owner, group, and ACLs as on the
host** — opencodebox never recreates device nodes, changes device permissions,
or grants access the host denies. Verify inside the sandbox:

```bash
ls -l /dev/ttyUSB0        # same type, mode, and device numbers
test -r /dev/ttyUSB0 && echo readable   # access matches the host user
test -w /dev/ttyUSB0 && echo writable
```

Owner and group **names** may render as `nobody` — see
[When a bound device cannot be opened](#when-a-bound-device-cannot-be-opened)
for why that display artifact is harmless.

If your user cannot open the device on the host, fix that through the host's
normal configuration (udev rules, group membership) and relaunch; the sandbox
only inherits host device access.

### When a bound device cannot be opened

The bind shares the host inode, and the sandbox keeps your uid, gid, and
supplementary group membership, so an `open()` inside the sandbox is
authorized exactly like on the host: same mode bits, owner, group, POSIX
ACLs, SELinux label, and device-cgroup rules. If a program inside cannot
open the node, your user could not open it on the host either. Check without
`sudo` and without opening or powering up the device:

```bash
test -r /dev/ttyUSB0 && echo readable
test -w /dev/ttyUSB0 && echo writable
```

On servers and over SSH — typical RHEL 9 setups — there is no graphical
login, so systemd-logind grants no per-seat ACLs and device access comes
from group membership:

| Node | Typical group |
|---|---|
| `/dev/ttyUSB*`, `/dev/ttyACM*` | `dialout` |
| `/dev/video*` | `video` |
| `/dev/snd/*` | `audio` |

Fix it host-side, then start a new session (reconnect SSH or log out and
back in) so the new group applies, and relaunch:

```bash
sudo usermod -aG dialout "$USER"
```

At launch, opencodebox prints a warning when a requested node's mode bits
deny your user both read and write (for example `root:dialout 0660` while you
are not in `dialout`). Nodes with POSIX ACLs are skipped because their mode
bits do not describe their access; the bind still happens either way.

#### Why `id` and `ls` look wrong inside the sandbox

bubblewrap always runs the sandbox in a user namespace that maps only your
own uid and gid. Any *other* id on a bound inode — for example
`root:dialout` on `/dev/ttyACM0` — is unmapped inside, so the kernel reports
it as the overflow id `65534`, which renders as `nobody`:

```console
$ ls -l /dev/ttyACM0            # host shows: root dialout
crw-rw----. 1 nobody nobody 166, 0 Oct  7 04:25 /dev/ttyACM0
```

This is a display artifact, not a change of ownership or access:

- The inode is still the host's `root:dialout` node with unchanged mode
  bits, device numbers, ACLs, and SELinux label — and the kernel still
  authorizes `open()` with your real host uid and group membership.
- No process inside the sandbox can *become* those unmapped ids, so the
  artifact never widens access.
- Inside the sandbox, `id` likewise prints your supplementary groups as
  `nobody`/`65534`. Do not diagnose device access with `id` or `ls` inside
  the sandbox; use `test -r`/`test -w` or the actual device operation.

opencodebox writes your host groups into the sandbox's `/etc/group` (plus
`root` and `nobody` entries) so lookups by number or name — `getent group
dialout`, initgroups-style membership checks — resolve normally; the
`nobody` rendering of unmapped ids is expected and harmless.

> **Note:** Upgrading bubblewrap does not change device-access semantics.
> RHEL 9 ships bubblewrap 0.4.1 originally, rebased to 0.6.3 in 2024
> (`dnf update` does not go further). Every option opencodebox uses —
> including `--seccomp` and `--dev-bind` — exists in 0.4.1, and
> `--dev-bind` behaves the same across versions: the node is exposed with
> its host mode, and host permissions decide whether it can be opened.

### Validation and behavior

- Only absolute paths under `/dev` are accepted, and the node must already
  exist. Validation failure aborts the launch.
- The node must be a **character or block device** (checked with
  `stat -c '%F'`). Regular files, directories, FIFOs, and symlinks are
  rejected; resolve symlinks such as `/dev/serial/by-id/...` yourself and pass
  the real node (usually `/dev/ttyUSB0`). Symlinked parent directories and
  paths with empty, `.`, or `..` components are also refused.
- Parent directories (for example `/dev/snd`) are created inside the sandbox
  before the node is bound; repeating an identical `--device` binds it once.
- Before binding, the launcher warns when the node's mode bits deny your
  user both read and write on the host (POSIX-ACL nodes are skipped);
  the bind still happens.
- The launcher never runs `sudo`, loads drivers, or changes device permissions.
- Host udev, ACL, group, SELinux, and device-cgroup restrictions still apply.
  Hotplugged or renumbered nodes need a sandbox restart. Explicit `--with`
  mounts still take precedence and can hide or replace device mounts.

> **Warning:** `--device` exposes everything the node allows on the host. For
> block devices (`/dev/sda`) or privileged nodes (`/dev/mem`, `/dev/kvm`,
> `/dev/net/tun`), that can mean raw storage, kernel, or virtualization access;
> the node's host permissions are the only boundary. The local namespace,
> capability, and seccomp settings are unchanged. Expose the narrowest node
> you need.

## SSH Agent and Git Signing

If `SSH_AUTH_SOCK` points to a valid socket, `opencodebox` forwards that socket into the sandbox. This allows SSH commit signing with keys already loaded by `ssh-add` on the host. This feature does not mount private SSH keys into the sandbox.

For Git SSH signing, use a public key path such as `~/.ssh/id_ed25519.pub`, or an inline `key::ssh-ed25519 ...` value. Validates and sanitizes `.pub` files (rejects hardlinks, multi-line files; validates key type, base64 format, and OpenSSH key structure with `ssh-keygen`). The sandbox receives sanitized key material only (`<key-type> <key-data>`), so comments or extra file content are not exposed.

For Git-over-SSH network operations, `known_hosts` is mounted read-only when available. This allows host verification without exposing private keys. `~/.ssh/config` is not mounted, and user-supplied binds under `~/.ssh` are rejected by the sensitive-path guard (the launcher still mounts `known_hosts` and sanitized `*.pub` itself).

For local SSH signature verification, the configured `gpg.ssh.allowedSignersFile` is mounted read-only when it is an absolute regular file under `$HOME`; otherwise it is skipped with a warning.

> **Note:** `.pub` files are validated on the already-open file descriptor (via `/proc/self/fd`), so the validated content cannot be swapped afterwards.

Git-over-SSH network operations may still need explicit read-only binds for other files in custom setups, but every user-supplied path under `~/.ssh` is rejected by the sensitive-path guard. User-provided binds and the current project bind can expose private keys if they include such files, so avoid binding `~/.ssh` wholesale.

Forwarding an agent still lets sandboxed processes ask the agent to authenticate or sign while the socket is available. Use a dedicated signing key and consider `ssh-add -c -t 1h ~/.ssh/signing_key` for confirmation and expiry.

## Seccomp Sandbox Filter

`opencodebox` includes a seccomp BPF filter that blocks socket creation for several protocol families to mitigate kernel privilege escalation vulnerabilities from inside the sandbox:

| Vulnerability | CVEs | Blocked Sockets |
|---|---|---|
| Copy Fail | [CVE-2026-31431](https://copy.fail) | `socket(AF_ALG, *, *)` |
| Dirty Frag (ESP) | [CVE-2026-43284](https://github.com/V4bel/dirtyfrag) | `socket(AF_INET/AF_INET6, *, IPPROTO_ESP)` |
| Dirty Frag (ESP Bypass) | [CVE-2026-43284](https://github.com/V4bel/dirtyfrag) | `socket(AF_NETLINK, *, NETLINK_XFRM)`, `setsockopt(*, IPPROTO_UDP, UDP_ENCAP, *)` |
| Dirty Frag (RxRPC) | [CVE-2026-43500](https://github.com/V4bel/dirtyfrag) | `socket(AF_RXRPC, *, *)` |
| Dirty Frag (IPCOMP) | [CVE-2026-43284](https://github.com/V4bel/dirtyfrag) | `socket(AF_INET/AF_INET6, *, IPPROTO_IPCOMP)` |

These are defense-in-depth mitigations and do not replace kernel patches. Supported architectures: **x86_64** and **aarch64**.

The filter is automatically applied if the corresponding `.bpf` file is available; otherwise a warning is displayed and the sandbox runs without it. The seccomp filter is stored at `~/.local/share/opencodebox/seccomp-security.bpf` after installation.

### References

- [Copy Fail — CVE-2026-31431](https://copy.fail)
- [Dirty Frag — CVE-2026-43284 / CVE-2026-43500](https://github.com/V4bel/dirtyfrag)
- [Ubuntu Security Advisory — Dirty Frag](https://ubuntu.com/blog/dirty-frag-linux-vulnerability-fixes-available)
- [AWS Security Bulletin — 2026-027](https://aws.amazon.com/security/security-bulletins/2026-027-aws/)

## Development

### Tests

**Dependencies:**
- **uv** - Python package manager ([install](https://docs.astral.sh/uv/getting-started/installation/))
- **bubblewrap** - Required only for `tests/seccomp.sh` (skips cleanly when absent)

**Run:**
```bash
uv run --group dev pytest -v   # launcher tests (mocked bwrap, no root needed)
bash tests/seccomp.sh          # seccomp integration (real bwrap + prebuilt .bpf)
```

Launcher tests are black-box: `bwrap` is mocked, the argv it would receive is
asserted. Device tests (`tests/test_device.py`) use the same `tests/conftest.py` harness.

### Seccomp BPF filter

To generate the seccomp BPF filter files (`.bpf`):

**Dependencies:**
- **gcc** - C compiler
- **libseccomp-dev** - libseccomp development headers and library

Install on Ubuntu/Debian:
```bash
sudo apt install gcc libseccomp-dev
```

**Compile and generate:**
```bash
# Compile the BPF generator
gcc -o seccomp/seccomp-security-gen seccomp/seccomp-security-gen.c -lseccomp

# Generate BPF filters for each architecture
./seccomp/seccomp-security-gen x86_64 > seccomp/seccomp-security-x86_64.bpf
./seccomp/seccomp-security-gen aarch64 > seccomp/seccomp-security-aarch64.bpf

# Clean up compiled generator
rm seccomp/seccomp-security-gen
```

The `.bpf` filter files are pre-generated and shipped with the repository, so end users do **not** need these development dependencies.

## License

[MIT License](LICENSE)
