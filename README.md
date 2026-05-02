# opencodebox

Run OpenCode inside a bubblewrap sandbox for security isolation.

`opencodebox` is a bash script that runs [OpenCode](https://opencode.ai) (AI coding assistant) inside a sandbox using [bubblewrap](https://github.com/containers/bubblewrap). The sandbox provides process isolation with Linux namespaces (PID, IPC, UTS) and restricted filesystem access.

## Features

- **Process Isolation**: Uses unshare PID, IPC, and UTS namespaces
- **Controlled Filesystem**: Most system filesystem mounted read-only
- **Custom Bind Mounts**: Add read-write or read-only access with `--with` and `--with-ro`
- **Mise Support**: Integrated with [mise](https://mise.jdx.dev) for tool management

## Prerequisites

- **bubblewrap** (`bwrap`) - for sandboxing
- **opencode** - AI coding assistant

## Installation

```bash
curl -fsSL https://raw.githubusercontent.com/fityannugroho/opencodebox/main/install.sh | bash
```

This installs `opencodebox` to `~/.local/bin/opencodebox`. Make sure `~/.local/bin` is in your PATH.

## Usage

```bash
opencodebox [OPTIONS] [OPENCODE_ARGS...]
```

### Options

- `--with /host[:/sandbox]` - Bind host path read-write to sandbox
- `--with-ro /host[:/sandbox]` - Bind host path read-only to sandbox

Format: `/host/path` or `/host/path:/sandbox/path`

### Examples

```bash
# Run opencode in sandbox with read-write access to /data
opencodebox --with /data

# Bind to different path in sandbox
opencodebox --with /mnt/data:/workspace/data

# Read-only access to config
opencodebox --with-ro /etc/hosts

# Combine and pass arguments to opencode
opencodebox --with /data --with-ro /config --version
```

## How It Works

1. Checks prerequisites (bwrap and opencode)
2. Parses `--with` and `--with-ro` arguments for additional bind mounts
3. Builds bubblewrap arguments with:
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
