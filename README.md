# opencodebox

Run OpenCode inside a bubblewrap sandbox for security isolation.

`opencodebox` is a bash script that runs [OpenCode](https://opencode.ai) (AI coding assistant) inside a sandbox using [bubblewrap](https://github.com/containers/bubblewrap). The sandbox provides process isolation with Linux namespaces (PID, IPC, UTS) and restricted filesystem access.

## Features

- **Process Isolation**: Uses unshare PID, IPC, and UTS namespaces
- **Controlled Filesystem**: Most system filesystem mounted read-only
- **Custom Bind Mounts**: Add read-write or read-only access with `--with` and `--with-ro`
- **Mise Support**: Integrated with [mise](https://mise.jdx.dev) for tool management
- **SSH Agent Forwarding**: Supports SSH commit signing through the host `ssh-agent`
- **CVE-2026-31431 Mitigation**: Seccomp filter blocks `AF_ALG` sockets to prevent "Copy Fail" privilege escalation vulnerability

## Prerequisites

- [**bubblewrap** (`bwrap`)](https://github.com/containers/bubblewrap) - for sandboxing
- [**opencode**](https://opencode.ai) - AI coding assistant

> **Security Note (CVE-2017-5226):** Bubblewrap sandbox can be escaped via `TIOCSTI` ioctl if the kernel allows it. Since Linux 6.2, `TIOCSTI` is restricted when `dev.tty.legacy_tiocsti=0` (default). On older kernels, ensure bubblewrap >= 0.1.5 (uses `setsid()` fix) or enable seccomp filtering. The `install.sh` script performs this check automatically.

> **Security Note (CVE-2026-31431 - "Copy Fail"):** A local privilege escalation vulnerability in the Linux kernel's `algif_aead` crypto module allows unprivileged users to gain root access. `opencodebox` includes a seccomp BPF filter that blocks `socket(AF_ALG)` creation, effectively cutting off the exploit's entry point inside the sandbox. This is a defense-in-depth mitigation (not a kernel patch replacement). Supported architectures: **x86_64** and **aarch64**. The filter is automatically applied if the corresponding `.bpf` file is available; otherwise a warning is displayed and the sandbox runs without it. The seccomp filter is stored at `~/.local/share/opencodebox/seccomp-af_alg.bpf` after installation.

## Development Dependencies

To generate the seccomp BPF filter files (`.bpf`) for CVE-2026-31431 mitigation:

- **gcc** - C compiler
- **libseccomp-dev** - libseccomp development headers and library

Install on Ubuntu/Debian:
```bash
sudo apt install gcc libseccomp-dev
```

The `.bpf` filter files are pre-generated and shipped with the repository, so end users do **not** need these development dependencies.

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

1. Checks prerequisites (bwrap and opencode)
2. **Enforces security restrictions**:
   - Rejects running from `$HOME`, `~/.ssh`, `~/.gnupg`, or their ancestors
   - Rejects `--with`/`--with-ro` binds that point to sensitive paths
   - Validates `~/.ssh` directory permissions (must be `0700`)
3. Parses `--with` and `--with-ro` arguments for additional bind mounts
4. Builds bubblewrap arguments with :
   - Namespace isolation (PID, IPC, UTS)
   - Read-only bindings for system basics (/usr, /etc/ssl, etc.)
   - Read-only bindings for language runtimes and configs
   - Read-write bindings for current project and opencode data
5. Executes opencode inside the sandbox

## Bind Mounts Structure

### Read-Only (Default)
- `/usr` - System basics
- `$HOME/.local` - User local data (except keyrings)
- `$HOME/.cache` - Cache
- `$HOME/.ssh/*.pub` - Sanitized OpenSSH public key material, when `$HOME/.ssh` is not a symlink
- `$HOME/.ssh/known_hosts*` - SSH host keys, read-only, for Git-over-SSH host verification
- `gpg.ssh.allowedSignersFile` - Configured SSH allowed signers file, read-only, for local signature verification
- Language runtimes: `.bun`, `.npm`, `.rustup`, `.cargo`
- Configs: `pnpm`, `uv`, `gitconfig`
- OpenCode: `.config/opencode`, `.agents`

### Read-Write
- Current project directory (`$PWD`)
- `$HOME/.local/share/opencode` - OpenCode application data

### Security Restrictions

`opencodebox` enforces several security restrictions to prevent sandbox escape:

- **Project directory**: Cannot run from `$HOME`, `~/.ssh`, `~/.gnupg`, or their ancestors. Use a dedicated project directory.
- **Bind mounts**: `--with` and `--with-ro` reject paths that point to or enclose sensitive locations (`$HOME`, `~/.ssh`, `~/.gnupg`).
- **SSH directory**: `~/.ssh` must have permissions `0700`. Fix with: `chmod 700 ~/.ssh`

### SSH Agent and Git Signing

If `SSH_AUTH_SOCK` points to a valid socket, `opencodebox` forwards that socket into the sandbox. This allows SSH commit signing with keys already loaded by `ssh-add` on the host. This feature does not mount private SSH keys into the sandbox.

For Git SSH signing, use a public key path such as `~/.ssh/id_ed25519.pub`, or an inline `key::ssh-ed25519 ...` value. Validates and sanitizes `.pub` files (rejects symlinks, hardlinks, multi-line files; validates key type, base64 format, and OpenSSH key structure with `ssh-keygen`). The sandbox receives sanitized key material only (`<key-type> <key-data>`), so comments or extra file content are not exposed.

For Git-over-SSH network operations, `known_hosts` and `known_hosts2` are mounted read-only when available. This allows host verification without exposing private keys. `~/.ssh/config` is not mounted by default because it can contain broader host-specific behavior; bind it explicitly with `--with-ro ~/.ssh/config` only when needed.

For local SSH signature verification, the configured `gpg.ssh.allowedSignersFile` is mounted read-only when it is an absolute regular file.

> **Note:** `.pub` validation occurs at script startup. There is a small TOCTOU window between reading and validating each `.pub` file; this is an accepted limitation of shell scripting.

Git-over-SSH network operations may still need explicit read-only binds for files such as `~/.ssh/config` in custom setups. User-provided binds and the current project bind can expose private keys if they include those files, so avoid binding `~/.ssh` wholesale.

Forwarding an agent still lets sandboxed processes ask the agent to authenticate or sign while the socket is available. Use a dedicated signing key and consider `ssh-add -c -t 1h ~/.ssh/signing_key` for confirmation and expiry.


## License

[MIT License](LICENSE)
