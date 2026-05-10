# PRD: Sandbox PATH & Cache Fix for opencodebox

## Introduction

The opencodebox bubblewrap sandbox restricts `PATH` to a minimal set (`/usr/bin:/bin:/opt/opencode:$HOME/.local/bin`) and mounts `$HOME/.cache` read-only. This breaks tools installed under `~/.local/share/pnpm`, `~/.cargo/bin`, `~/.bun/bin`, and other global binary directories — they exist on the filesystem (since `~/.local` is already bound) but PATH doesn't point to them. Package managers (npm, pnpm, uv, etc.) also fail because they need cache write access.

## Goals

- All binaries under `~/.local` are automatically discoverable inside the sandbox with zero manual configuration
- Cache is writable so tools and package managers work correctly
- Zero security regression — maintain existing isolation guarantees
- Zero maintenance — solution works for future tools without changes
- Minimal code change — surgical edits only

## User Stories

### US-001: Host PATH inheritance

**Description:** As a user, I want the sandbox to inherit my host PATH so all my installed tools (pnpm global bins, cargo tools, mise-managed runtimes, bun, etc.) are automatically available without manual PATH configuration.

**Acceptance Criteria:**
- [ ] `--setenv PATH` on line 354 is removed
- [ ] `ncu`, `pnpm`, `cargo`, `bun`, and other host PATH binaries are found inside the sandbox
- [ ] Dead PATH entries (pointing to directories not bound into the sandbox) fail silently — no errors
- [ ] `/opt/opencode` is still found (via host PATH or existing bind)
- [ ] `npm run typecheck` passes (if applicable)
- [ ] Verification: `opencodebox -- bash -c 'echo $PATH'` shows the same PATH as the host

### US-002: Writable ephemeral cache

**Description:** As a user, I want package managers and tools to write to `~/.cache` inside the sandbox, so `npm install`, `pnpm outdated`, `uv sync`, and similar commands don't fail with permission errors.

**Acceptance Criteria:**
- [ ] `--ro-bind "$HOME/.cache"` on line 328 is replaced with `--tmpfs "$HOME/.cache"`
- [ ] Tools can write to `~/.cache/npm`, `~/.cache/pnpm`, etc. inside the sandbox
- [ ] Cache data does not persist to the host after the sandbox exits (tmpfs isolation)
- [ ] `~/.cache/mise` remains visible as read-only (separately bound on line 437)
- [ ] Security: no write-through to host cache files
- [ ] Verification: `opencodebox -- bash -c 'touch ~/.cache/test-write && echo OK'` succeeds

## Functional Requirements

- FR-1: Remove `--setenv PATH "/usr/bin:/bin:/opt/opencode:$HOME/.local/bin"` from `opencodebox` line 354
- FR-2: Replace `--ro-bind "$HOME/.cache" "$HOME/.cache"` with `--tmpfs "$HOME/.cache"` in `opencodebox` line 328
- FR-3: Keep the mise cache ro-bind (`$HOME/.cache/mise`) on line 437 as-is — it is a subpath that mounts on top of the tmpfs

## Non-Goals

- No change to `~/.local` write permission — stays read-only
- No new dependencies or tools added
- No changes to seccomp filter or other isolation layers
- No complex PATH scanning logic introduced
- No changes to tool-specific bind mounts (existing binds already cover `~/.cargo/bin`, `~/.bun`, etc.)

## Technical Considerations

- **Bwrap mount order**: `BWRAP_ARGS` is built sequentially. The mise cache bind (`--ro-bind "$HOME/.cache/mise"`) is appended after core args (line 437), so it mounts on top of the tmpfs `$HOME/.cache`. Result: `~/.cache` is a writable tmpfs, but `~/.cache/mise` remains read-only from the host.
- **Host PATH scrutiny**: The host PATH contains entries irrelevant inside the sandbox (`/mnt/c/*`, `/snap/bin`). These are harmless — bwrap can only execute binaries from directories that are bind-mounted.
- **Backward compatibility**: All existing behavior (`--with`, `--with-ro`, seccomp, SSH, mise activation) is unaffected since only PATH and the cache mount are changed.

## Success Metrics

- `ncu` and other pnpm global binaries run without full path
- `pnpm outdated`, `pnpm audit`, `npm install`, etc. don't fail due to cache
- Threat model is unchanged or improved (tmpfs cache is more isolated than ro-bind)
- One line deleted + one line changed — minimal diff

## Open Questions

None — resolved via the prior grill session.
