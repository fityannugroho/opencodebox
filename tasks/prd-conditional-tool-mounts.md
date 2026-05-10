# PRD: Conditional Tool Mounts in opencodebox

## Introduction

Currently, `opencodebox` mounts language runtime and tool directories (Bun, npm, pnpm, uv, Rust/Cargo, git, mise) into the bubblewrap sandbox unconditionally — regardless of whether those tools are actually installed on the host. This causes two problems:

1. **bwrap failure** if a mounted path does not exist on the host (e.g., `~/.bun`, `~/.gitconfig`)
2. **Unnecessary mounts** bloating the sandbox and increasing attack surface for tools the user never uses

This refactoring makes every tool-specific mount conditional on the tool actually being installed (`command -v`), falling back to a directory/file existence check. Only core system paths (e.g., `/usr`, `/etc/ssl`, `~/.cache`) remain unconditional.

## Goals

- Mount tool directories only when the corresponding tool is installed on the host
- Prevent bwrap crashes from non-existent bind source paths
- Group all tool-specific mounts into self-contained, consistent conditional blocks
- Preserve all existing security checks (sensitive path filtering, SSH validation, seccomp)
- Keep the public interface (`--with`, `--with-ro`, CLI flags) unchanged

## User Stories

### US-001: Conditional Bun mounts
**Description:** As a user who does not use Bun, I want `~/.bun` and `~/.bun/install/cache` to not be mounted into the sandbox.

**Acceptance Criteria:**
- [ ] If `command -v bun` fails, no Bun-related bind or tmpfs mounts are added
- [ ] If `command -v bun` succeeds, `~/.bun` is ro-bind (if exists) and `~/.bun/install/cache` is tmpfs
- [ ] `opencodebox --version` still works regardless of Bun installation

### US-002: Conditional npm mounts
**Description:** As a user who does not use npm, I want `~/.npm` and `~/.npmrc` to not be mounted into the sandbox.

**Acceptance Criteria:**
- [ ] If `command -v npm` fails, no npm-related mounts are added
- [ ] If `command -v npm` succeeds, `~/.npm` is tmpfs and `~/.npmrc` is ro-bind (if exists)
- [ ] Existing `.npmrc` conditional mount is absorbed into this block

### US-003: Conditional pnpm mounts
**Description:** As a user who does not use pnpm, I want `~/.config/pnpm` and `~/.local/share/pnpm/store` to not be mounted.

**Acceptance Criteria:**
- [ ] If `command -v pnpm` fails, no pnpm-related mounts are added
- [ ] If `command -v pnpm` succeeds, `~/.config/pnpm` is ro-bind (if exists) and `~/.local/share/pnpm/store` is tmpfs
- [ ] pnpm store tmpfs is removed from the unconditional array and placed in this block

### US-004: Conditional uv mounts
**Description:** As a user who does not use uv, I want `~/.config/uv` to not be mounted.

**Acceptance Criteria:**
- [ ] If `command -v uv` fails, no uv-related mounts are added
- [ ] If `command -v uv` succeeds, `~/.config/uv` is ro-bind (if exists)

### US-005: Conditional Rust/Cargo mounts
**Description:** As a user who does not have Rust installed, I want `~/.rustup`, `~/.cargo/bin`, `~/.cargo/registry`, and `~/.cargo/config.toml` to not be mounted.

**Acceptance Criteria:**
- [ ] If `command -v cargo` fails, no Rust/Cargo-related mounts are added
- [ ] If `command -v cargo` succeeds: `~/.rustup` ro-bind (if exists), `~/.cargo/bin` ro-bind (if exists), `~/.cargo/registry` tmpfs, `~/.cargo/config.toml` ro-bind (if exists)
- [ ] Existing `~/.cargo/config.toml` conditional mount is absorbed into this block

### US-006: Conditional gitconfig mount
**Description:** As a user without git installed or without `~/.gitconfig`, I want the sandbox not to fail on a missing file bind.

**Acceptance Criteria:**
- [ ] If `command -v git` fails OR `~/.gitconfig` does not exist, the file is not mounted
- [ ] Only if both conditions pass is `~/.gitconfig` mounted as ro-bind

### US-007: Conditional mise mounts + activation
**Description:** As a user without mise installed, I want mise directories and the runtime `command -v` check inside the sandbox to be skipped entirely.

**Acceptance Criteria:**
- [ ] If `command -v mise` fails, no mise directories are bound and the `mise activate` block inside the sandbox is skipped
- [ ] If `command -v mise` succeeds, existing directory-exists logic applies unchanged
- [ ] The existing `is_sensitive_path` guard is preserved

### US-008: Update README documentation
**Description:** As a developer reading the docs, I want the "Bind Mounts Structure" section to accurately reflect that tool mounts are conditional, so I know which paths are guaranteed to exist inside the sandbox versus which depend on host tool installation.

**Acceptance Criteria:**
- [ ] "Bind Mounts Structure" section lists unconditional mounts and conditional tool mounts separately
- [ ] Conditional mounts note they require the corresponding tool to be installed on the host
- [ ] "How It Works" step ordering reflects the refactored flow

## Functional Requirements

- FR-1: All tool mounts use `command -v <tool> >/dev/null 2>&1` as the primary guard
- FR-2: After `command -v` passes, a secondary `[[ -d <path> ]]` or `[[ -f <path> ]]` check prevents bwrap errors on missing paths
- FR-3: Tool-specific tmpfs mounts (pnpm store, bun cache, cargo registry, npm cache) are moved from the static `BWRAP_ARGS` array into their respective conditional blocks
- FR-4: The unconditional `BWRAP_ARGS` array retains only: system resources, read-only root, SSL certs, user identity, `~/.local` (ro-bind), `~/.cache` (tmpfs), `~/.local/share/keyrings` (tmpfs), `~/.local/share/virtualenvs` (tmpfs), OpenCode config/agents dirs, project dir (rw-bind), app data (rw-bind), and the OpenCode binary bind
- FR-5: `check_prerequisites` for bwrap and opencode remains unchanged (fail-fast)
- FR-6: SSH, seccomp, `--with`/`--with-ro` logic is untouched
- FR-7: Mise activation inside the sandbox (`command -v mise && eval "$(mise activate bash)"`) is also guarded by the same host-side `command -v mise` check; if mise is absent on the host, the bash snippet should skip rather than running `command -v mise` at runtime unnecessarily

## Non-Goals

- No changes to the bwrap/seccomp infrastructure
- No config file format (JSON/YAML) for tool definitions — all stays in bash
- No plugin system for user-defined tools
- No changes to `--with`/`--with-ro` CLI interface
- No changes to SSH key handling, seccomp loading, or sensitive path detection

## Technical Considerations

- **Single file:** All changes are confined to `opencodebox` (~460 lines)
- **Bash compatibility:** Must remain POSIX-ish; tested on bash 4+
- **Order of mounts:** Conditional tool blocks are appended after the main array but before `EXTRA_BINDS`, so user `--with` overrides still take highest priority as before
- **Regression risk:** The current code has unconditional binds to paths that may not exist — this refactoring actually fixes latent crashes
- **Mise activation:** The `command -v mise` check inside the sandbox bash snippet (line 458) can be dropped entirely if mise is not installed on the host, simplifying the entry command

## Success Metrics

- Zero bwrap crashes from non-existent bind source paths
- All tool-specific mounts are guarded by `command -v`
- No functional regression in existing workflows (test via `opencodebox --version`, `opencodebox` in a project dir)
- `git status` shows only intended changes to `opencodebox` and `README.md`
- `README.md` "Bind Mounts Structure" section accurately reflects conditional vs unconditional mounts

## Open Questions

- Should we also guard `~/.local/share/virtualenvs` tmpfs behind a `pip`/`pipenv` check? (Currently kept unconditional in the plan)
- Should tmpfs for `~/.cache` be kept unconditional or split per tool? (Kept unconditional — too many tools use it)
