# opencodebox

Run OpenCode inside a bubblewrap sandbox for security isolation.

`opencodebox` is a bash script that runs [OpenCode](https://opencode.ai) (AI coding assistant) inside a sandbox using [bubblewrap](https://github.com/containers/bubblewrap). The sandbox provides process isolation with Linux namespaces (PID, IPC, UTS) and restricted filesystem access.

## Features

- **Process Isolation**: Uses unshare PID, IPC, and UTS namespaces
- **Controlled Filesystem**: Most system filesystem mounted read-only
- **Custom Bind Mounts**: Add read-write or read-only access with `--with` and `--with-ro`
- **Mise Support**: Integrated with [mise](https://mise.jdx.dev) for tool management

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

1. Checks prerequisites (bwrap and opencode)
2. Parses `--with` and `--with-ro` arguments for additional bind mounts
3. Builds bubblewrap arguments with :
   - Namespace isolation (PID, IPC, UTS)
   - Read-only bindings for system basics (/usr, /etc/ssl, etc.)
   - Read-only bindings for language runtimes and configs
   - Read-write bindings for current project and opencode data
4. Executes opencode inside the sandbox

## Bind Mounts Structure

### Read-Only (Default)
- `/usr` - System basics
- `$HOME/.local` - User local data (except keyrings)
- `$HOME/.cache` - Cache
- Language runtimes: `.bun`, `.npm`, `.rustup`, `.cargo`
- Configs: `pnpm`, `uv`, `gitconfig`
- OpenCode: `.config/opencode`, `.agents`

### Read-Write
- Current project directory (`$PWD`)
- `$HOME/.local/share/opencode` - OpenCode application data
