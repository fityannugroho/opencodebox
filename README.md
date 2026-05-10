# opencodebox

Run OpenCode inside a bubblewrap sandbox for security isolation.

`opencodebox` is a bash script that runs [OpenCode](https://opencode.ai) (AI coding assistant) inside a sandbox using [bubblewrap](https://github.com/containers/bubblewrap). The sandbox provides process isolation with Linux namespaces (PID, IPC, UTS) and restricted filesystem access.

## Features

- **Process Isolation**: Uses unshare PID, IPC, and UTS namespaces
- **Controlled Filesystem**: Most system filesystem mounted read-only
- **Custom Bind Mounts**: Add read-write or read-only access with `--with` and `--with-ro`
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

`opencodebox` is a wrapper for the `opencode` command. All arguments are passed through to `opencode` inside the sandbox.

```bash
opencodebox [OPTIONS] [OPENCODE_ARGS...]
```

> **Note:** The `opencode` command stays available when you need it. We didn't replace it.

### Options

`opencodebox` adds the following options :

- `--with /host[:/sandbox]` - Bind host path read-write to sandbox
- `--with-ro /host[:/sandbox]` - Bind host path read-only to sandbox

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

# Run sandboxed opencode server with specified bind mounts
opencodebox --with /data --with-ro /config serve
```

## How It Works

1. Parse arguments (`--with`, `--with-ro`, `--version`, `--help`)
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
- `$HOME/.local` - User local data (except keyrings/tool data)
- `$HOME/.cache/opencode` - OpenCode cache
- `$HOME/.ssh/*.pub` - Sanitized OpenSSH public key material, when `$HOME/.ssh` is not a symlink
- `$HOME/.ssh/known_hosts*` - SSH host keys, read-only, for Git-over-SSH host verification
- `gpg.ssh.allowedSignersFile` - Configured SSH allowed signers file (if configured)
- OpenCode: `.config/opencode`, `.agents`

**Read-Write:**
- Current project directory (`$PWD`)
- `$HOME/.local/share/opencode` - OpenCode application data

**Tmpfs (Private, writable per-session):**
- `$HOME/.cache` - Universal cache
- `$HOME/.local/share/keyrings` - Private keyring, if exists

### Conditional Tool Mounts (Requires Tool Installed on Host)

Each tool is mounted only when `command -v <tool>` succeeds on the host. If the tool is not installed, none of its directories are bound into the sandbox.

- **Bun** — `~/.bun` (read-only, if exists) + `~/.bun/install/cache` (tmpfs, if exists)
- **npm** — `~/.npm` (tmpfs, if exists) + `~/.npmrc` (read-only, if exists)
- **pnpm** — `~/.config/pnpm` (read-only, if exists) + `~/.local/share/pnpm/store` (tmpfs, if exists)
- **uv** — `~/.config/uv` (read-only, if exists)
- **pipenv** — `~/.local/share/virtualenvs` (tmpfs, if exists, centralized store)
- **Rust/Cargo** — `~/.rustup`, `~/.cargo/bin` (read-only, if exists) + `~/.cargo/registry` (tmpfs, if exists) + `~/.cargo/config.toml` (read-only, if exists)
- **Git** — `~/.gitconfig` (read-only, requires both `command -v git` and file existence)
- **Mise** — `~/.config/mise`, `~/.local/share/mise`, `~/.cache/mise` (read-only, if exists, subject to sensitive path filtering)

## Security Restrictions

`opencodebox` enforces several security restrictions to prevent sandbox escape:

- **Project directory**: Cannot run from `$HOME`, `~/.ssh`, `~/.gnupg`, or their ancestors. Use a dedicated project directory.
- **Bind mounts**: `--with` and `--with-ro` reject paths that point to or enclose sensitive locations (`$HOME`, `~/.ssh`, `~/.gnupg`).
- **SSH directory**: `~/.ssh` must have permissions `0700`. Fix with: `chmod 700 ~/.ssh`

## SSH Agent and Git Signing

If `SSH_AUTH_SOCK` points to a valid socket, `opencodebox` forwards that socket into the sandbox. This allows SSH commit signing with keys already loaded by `ssh-add` on the host. This feature does not mount private SSH keys into the sandbox.

For Git SSH signing, use a public key path such as `~/.ssh/id_ed25519.pub`, or an inline `key::ssh-ed25519 ...` value. Validates and sanitizes `.pub` files (rejects symlinks, hardlinks, multi-line files; validates key type, base64 format, and OpenSSH key structure with `ssh-keygen`). The sandbox receives sanitized key material only (`<key-type> <key-data>`), so comments or extra file content are not exposed.

For Git-over-SSH network operations, `known_hosts` is mounted read-only when available. This allows host verification without exposing private keys. `~/.ssh/config` is not mounted by default because it can contain broader host-specific behavior; bind it explicitly with `--with-ro ~/.ssh/config` only when needed.

For local SSH signature verification, the configured `gpg.ssh.allowedSignersFile` is mounted read-only when it is an absolute regular file.

> **Note:** `.pub` validation occurs at script startup. There is a small TOCTOU window between reading and validating each `.pub` file; this is an accepted limitation of shell scripting.

Git-over-SSH network operations may still need explicit read-only binds for files such as `~/.ssh/config` in custom setups. User-provided binds and the current project bind can expose private keys if they include those files, so avoid binding `~/.ssh` wholesale.

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
