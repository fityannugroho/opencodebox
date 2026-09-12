# opencodebox

Run OpenCode inside a bubblewrap sandbox for security isolation.

`opencodebox` is a bash script that runs [OpenCode](https://opencode.ai) (AI coding assistant) inside a sandbox using [bubblewrap](https://github.com/containers/bubblewrap). The sandbox provides process isolation with Linux namespaces (PID, IPC, UTS) and restricted filesystem access.

## Features

- **Process Isolation**: Uses unshare PID, IPC, and UTS namespaces
- **Controlled Filesystem**: Most system filesystem mounted read-only
- **Persistent Temporary Storage (Opt-In)**: Keep `/tmp` in the launch directory with `--persistent-tmp`
- **Custom Bind Mounts**: Add read-write or read-only access with `--with` and `--with-ro`
- **Mise Support**: Integrated with [mise](https://mise.jdx.dev) for tool management
- **Direct GPU Access (Opt-In)**: Expose NVIDIA/DRM GPU devices with `--gpu`
- **Rootless Podman (Opt-In)**: Use the host container service with `--podman` (grants host-user container access)
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

`opencodebox` is a wrapper for the `opencode` command. Arguments other than wrapper options are passed through to `opencode` inside the sandbox.

```bash
opencodebox [OPTIONS] [OPENCODE_ARGS...]
```

> **Note:** The `opencode` command stays available when you need it. We didn't replace it.

### Options

`opencodebox` adds the following options :

- `--with /host[:/sandbox]` - Bind host path read-write to sandbox
- `--with-ro /host[:/sandbox]` - Bind host path read-only to sandbox

`--with` and `--with-ro` accept additional paths (directories, files, or sockets).

- `--gpu` - Expose host GPU devices for direct sandbox workloads; see [direct GPU access](#direct-gpu-access-opt-in).
- `--persistent-tmp` - Mount `.opencodebox-tmp` in the launch directory at `/tmp`; see [persistent temporary storage](#persistent-temporary-storage-opt-in).
- `--podman` - Forward the current user's rootless Podman API socket; see [Podman support](#podman-support-opt-in) for setup and security implications.

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

1. Parse arguments (`--with`, `--with-ro`, `--podman`, `--persistent-tmp`, `--gpu`, `--version`, `--help`)
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

> **Optional system paths:** System mounts marked “if present” use bubblewrap’s `--ro-bind-try`: they are mounted read-only when available and skipped when absent. This accommodates differences between Linux distributions. It is an internal bubblewrap option, not an opencodebox command-line option.

### Unconditional Mounts (Always Present)

**Read-Only:**
- `/usr` - System basics
- `/etc/resolv.conf`, `/etc/hosts` - DNS configuration and host mappings
- `/etc/nsswitch.conf` - Name service lookup configuration (if present)
- `/etc/ssl` - SSL/TLS certificates (if present)
- `/etc/ca-certificates` - CA certificate store (if present)
- `/etc/pki/ca-trust`, `/etc/pki/tls/certs` - Fedora/RHEL trust store and certificate targets for `/etc/ssl` symlinks (if present); `/etc/pki/tls/private` is not mounted
- `/etc/alternatives` - System alternatives (managed by update-alternatives)
- `$HOME/.local` - User local data (except keyrings/tool data)
- `$HOME/.cache/opencode` - OpenCode cache
- `$HOME/.ssh/*.pub` - Sanitized OpenSSH public key material, when `$HOME/.ssh` is not a symlink
- `$HOME/.ssh/known_hosts` - SSH host key, read-only, for Git-over-SSH host verification
- `gpg.ssh.allowedSignersFile` - Configured SSH allowed signers file (if configured)
- OpenCode: `.config/opencode`, `.agents`

**Read-Write:**
- Current project directory (`$PWD`)
- `$HOME/.local/share/opencode` - OpenCode application data

**Tmpfs (Private, writable per-session):**
- `/tmp` - Temporary files (default; replaced by a persistent bind with `--persistent-tmp`)
- `$HOME/.cache` - Universal cache
- `$HOME/.local/share/keyrings` - Exclude private keyring (if exists on host)

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
| **Mise** | `~/.config/mise`, `~/.local/share/mise`, `~/.cache/mise` | — |

## Security Restrictions

`opencodebox` enforces several security restrictions to prevent sandbox escape:

- **Project directory**: Cannot run from `$HOME`, `~/.ssh`, `~/.gnupg`, or their ancestors. Use a dedicated project directory.
- **Bind mounts**: `--with` and `--with-ro` reject paths that point to or enclose sensitive locations (`$HOME`, `~/.ssh`, `~/.gnupg`).
- **SSH directory**: `~/.ssh` must have permissions `0700`. Fix with: `chmod 700 ~/.ssh`

## Direct GPU Access (Opt-In)

`--gpu` exposes GPU devices to programs running **directly inside opencodebox**:

```bash
opencodebox --gpu
# All three opt-ins can be combined:
opencodebox --gpu --podman --persistent-tmp -s SESSION_ID
```

When testing a checkout, use `./opencodebox` rather than an older installed copy.
Launch from your normal host environment: an existing sandbox without GPU
mounts cannot forward devices it cannot see. No mounts are added to an already
running session.

### Devices and driver dependencies

The switch discovers and mounts these existing character devices individually
using bubblewrap's `--dev-bind` (not a bind of the entire host `/dev`):

| Device family | Paths |
|---|---|
| NVIDIA GPUs | `/dev/nvidiaN` (numeric N) |
| NVIDIA auxiliary devices | `/dev/nvidiactl`, `/dev/nvidia-uvm`, `/dev/nvidia-uvm-tools`, `/dev/nvidia-modeset`, `/dev/nvidia-caps/nvidia-capN` |
| DRM GPUs (Intel/AMD/NVIDIA) | `/dev/dri/cardN`, `/dev/dri/renderDN` |
| AMD compute | `/dev/kfd` |

It requires at least one NVIDIA GPU or DRM card/render character device. It
skips non-character files and symlinked device nodes, rejects symlinked GPU
parent directories, and fails clearly when no GPU is found. These checks assume
trusted host `/dev`; they do not authenticate device drivers or eliminate
concurrent replacement races. Devices disappearing during launch cause a mount
failure rather than silent partial access.

GPU mode also mounts these **read-only, if present**:

- `/sys`, preserving the host sysfs topology and its symlink relationships.
- `/etc/ld.so.cache`, for host driver library discovery.
- `/etc/OpenCL/vendors`, `/etc/vulkan/icd.d`, `/etc/glvnd/egl_vendor.d`.
- `/proc/driver/nvidia`, when an NVIDIA GPU device was found.

Driver libraries and `/usr/share` vendor manifests under `/usr` are already
visible. The switch does not install CUDA, ROCm, PyTorch, or other SDKs. Libraries,
cache entries, or manifests pointing outside the exposed filesystem may need
explicit same-path `--with-ro` mounts. For example, a ROCm installation under
`/opt/rocm` may require both that path and its versioned symlink target. Direct
GPU access is not a guarantee of compatibility with every driver/SDK layout.

### Verify and troubleshoot

For NVIDIA, first ensure `nvidia-smi -L` works **on the host**. After launching
with `--gpu`, run inside the new sandbox:

```bash
nvidia-smi -L
# If a CUDA-enabled PyTorch environment is already installed:
python -c 'import torch; print(torch.cuda.is_available()); print(torch.cuda.device_count())'
```

`nvidia-smi` succeeding alone does not prove a CUDA workload works. When NVIDIA
GPU nodes exist but `/dev/nvidia-uvm` is absent, the launcher warns that CUDA may
fail. Initialize the NVIDIA/UVM driver using your distribution's normal host
setup procedure, then restart opencodebox. The launcher never loads modules,
runs `sudo`, changes device permissions, or initializes GPUs automatically.

Existing host permissions, ACLs, supplementary group access, SELinux rules, and
device-cgroup restrictions still apply. Fix access through the host's normal
GPU configuration rather than granting blanket privileges. Hotplugged or
replaced device nodes may require a sandbox restart. Explicit `--with` mounts
still take precedence and can hide or replace GPU-related mounts.

### Security and Podman distinction

> **Warning:** `--gpu` exposes all matching GPU nodes present at launch, subject
> to host access controls. It adds access to GPU driver ioctls, shared GPU
> resources, and potentially display/control interfaces—not just compute. Driver
> vulnerabilities, resource exhaustion, and host display interference are risks.
> Read-only `/sys` also exposes non-GPU host hardware/kernel information. There
> is no GPU memory quota, per-device selection, or cross-session isolation.

The local namespace, capability, and seccomp settings are unchanged; the seccomp
filter does not broadly block GPU ioctls. GPU visibility environment variables
are not security boundaries. This switch does not forward X11/Wayland sockets
or otherwise provide desktop-session access. Without `--gpu`, no GPU-specific
mounts or probes are added.

`--podman` uses the **host** container service, so GPU-enabled containers do not
need `--gpu` on opencodebox. Conversely, `--gpu` does not automatically assign
GPUs to Podman containers. Continue requesting them through the host's CDI setup:

```bash
podman run --rm --device nvidia.com/gpu=all \
  docker.io/library/ubuntu:24.04 nvidia-smi -L
```

If the host's SELinux policy requires `--security-opt=label=disable`, apply it
only to that container with the understanding that it disables its SELinux
separation. Podman/CDI must already be configured on the host.

## Persistent Temporary Storage (Opt-In)

By default, `/tmp` is a private tmpfs discarded when the sandbox exits. To keep
its contents across launches, run:

```bash
opencodebox --persistent-tmp
# Can be combined with Podman and session resume:
opencodebox --persistent-tmp --podman -s SESSION_ID
```

The launcher creates **`.opencodebox-tmp` in the directory from which you launch
opencodebox** and bind-mounts it read-write at `/tmp`. For example, launching from
`/home/jgf/git/my-project` stores `/tmp/example.txt` at
`/home/jgf/git/my-project/.opencodebox-tmp/example.txt` on the host. It is not
created beside the script unless that is also your launch directory. Resuming a
session or passing a different project argument to OpenCode does not change the
storage location selected by the wrapper.

- The directory is created with mode **0700** and reused on later launches.
  Existing paths must be non-symlink directories owned by your user with mode
  0700. Unsafe paths are rejected, not deleted or silently changed. These are
  startup checks, not protection against a concurrent process with your user's
  access replacing files in the project.
- Contents are **not automatically cleaned**. Temporary credentials, downloads,
  stale sockets, lock files, and application data may remain. Review and clean
  the directory manually when no sessions are using it. It consumes space on
  the launch directory's filesystem rather than a new tmpfs; it is only as
  durable as that underlying filesystem.
- Sessions launched from the same directory with this flag **share `/tmp`**.
  They are not isolated from one another's temporary files and may encounter
  name or lock conflicts. Mode 0700 is intended for the invoking user, not a
  multi-user `/tmp` shared by processes running under other UIDs.
- Add `.opencodebox-tmp/` to your project's `.gitignore` (or local
  `.git/info/exclude`) and exclude it from build contexts/backups as appropriate.
  The launcher does not edit your project's ignore files. This repository
  already ignores the directory.
- Without the flag, `/tmp` remains ephemeral and any existing persistent
  directory is left untouched. Because the project itself is mounted, that
  directory is still accessible at its project path; this flag is a storage
  choice, not a confidentiality boundary.
- Explicit `--with`/`--with-ro` mounts still take precedence. Avoid overriding
  `/tmp` if you want this storage mapping to remain in effect.
- With `--podman`, container bind-mount sources still use **host paths**. Use the
  full host path to `.opencodebox-tmp`, not `/tmp`, when sharing these files with
  a container through the host service.

Restart using the updated launcher to enable the mount. This does not migrate
files from an already-running sandbox's ephemeral `/tmp`.

## Podman Support (Opt-In)

`--podman` lets the Podman CLI use a **host rootless Podman service**. It does
not run a container runtime inside bubblewrap or weaken the local seccomp,
capability, or device restrictions. Install the `podman` client in a location
visible inside the sandbox (normally `/usr/bin/podman`).

### Host setup and launch

Run these commands **outside opencodebox**, as your normal non-root user:

```bash
systemctl --user start podman.socket
# Optional: enable socket activation for future user sessions
systemctl --user enable podman.socket

# From the project directory, using the updated launcher:
opencodebox --podman
# Or resume an existing OpenCode session:
opencodebox --podman -s SESSION_ID
```

When testing a repository checkout before installing it, use `./opencodebox`
instead of the installed `opencodebox` command. Restart the sandbox to add this
mount; changing the launcher cannot add it to an existing session.

The launcher discovers `${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/podman/podman.sock`,
resolves its path, and requires a Unix socket owned by the invoking non-root
user. It fails with setup instructions if the socket is missing; it never starts
host services automatically. Socket validation checks type/ownership, not service
health or authenticity. If the service is unavailable, Podman will report the
connection error. The host service must actually be configured rootless.

Only the socket is bound to `/run/opencodebox/podman.sock`. The launcher sets
`CONTAINER_HOST=unix:///run/opencodebox/podman.sock` and unsets
`CONTAINER_CONNECTION` so an inherited named connection does not override the
selected endpoint. Ordinary `podman` commands then use remote mode. Without
`--podman`, no Podman-specific mounts or environment changes are added.
User-supplied `--with` mounts still take precedence; avoid overriding `/run`,
`/run/opencodebox`, or the socket destination when using this feature.

### Verify inside the new sandbox

```bash
podman info
podman run --rm docker.io/library/alpine:latest id
```

The second command downloads an image if necessary and runs a disposable
container through the host service. Docker API clients, Compose providers,
custom endpoints, and nested/local Podman are not configured by this option.
`DOCKER_HOST` is not set.

### Security, paths, and cleanup

> **Warning:** This is explicit delegation of host-user container execution,
> not project-confined access. Processes in the sandbox can use the service to
> start containers with bind mounts of other host files accessible to your user,
> including files normally hidden by opencodebox. Rootless does not mean confined
> to the workspace. Use a dedicated restricted account or VM if that boundary
> must remain intact. A read-only socket mount would not make the API read-only.

- Root invocation is rejected; the rootful `/run/podman/podman.sock` endpoint is
  not selected. Do not manually forward a rootful service as a workaround.
- Container bind-mount source paths are resolved on the **host**. Same-path
  project mounts work naturally; sandbox-only aliases and private `/tmp` files
  are not automatically available to the service. Build contexts and copy
  commands have their own remote-transfer semantics.
- Containers run outside bubblewrap's process tree and do not inherit its
  seccomp policy or teardown lifecycle. They may outlive OpenCode. Use `--rm`
  where appropriate and explicitly stop/remove long-lived containers.
- Host ports and container storage are managed by the host service.
- Restart the sandbox if the host service socket is replaced; a bind of the old
  socket may no longer reach the new listener.

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

Run launcher regression tests (Python 3 standard library; no Podman service or
container downloads required):

```bash
bash -n opencodebox
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -v
```

The tests use isolated temporary homes, real temporary Unix sockets, and mocked
commands to inspect launcher arguments. A live service/container smoke test must
still be run after launching with `--podman` as described above.

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
