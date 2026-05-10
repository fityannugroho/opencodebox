# PRD: Sandbox PATH Fix for opencodebox

## Introduction

The opencodebox bubblewrap sandbox sets a minimal `PATH` (`/usr/bin:/bin:/opt/opencode:$HOME/.local/bin`) that overrides the host PATH entirely. Tools installed under `~/.local/share/pnpm`, `~/.local/share/mise/installs/*/bin`, `~/.cargo/bin`, and other global binary directories exist on the filesystem (since `~/.local` and `~/.cargo/bin` are already bind-mounted) but are not discoverable because PATH doesn't point to them.

## Goals

- All binaries under `~/.local` are automatically discoverable inside the sandbox with zero manual configuration
- Zero security regression — maintain existing isolation guarantees
- Zero maintenance — solution works for future tools without changes
- Minimal code change — surgical edit only

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

## Functional Requirements

- FR-1: Remove `--setenv PATH "/usr/bin:/bin:/opt/opencode:$HOME/.local/bin"` from `opencodebox` line 354 — bwrap inherits host environment by default when no `--setenv` is given

## Non-Goals

- No change to any bind mount or tmpfs configuration
- No new dependencies or tools added
- No changes to seccomp filter or other isolation layers
- No complex PATH scanning logic introduced
- No changes to `~/.cache` mount (stays `--ro-bind`)

## Technical Considerations

- **Host PATH scrutiny**: The host PATH contains entries irrelevant inside the sandbox (`/mnt/c/*`, `/snap/bin`). These are harmless — bwrap can only execute binaries from directories that are bind-mounted.
- **Missing directories**: If a host PATH entry points to a directory not bind-mounted into the sandbox, bwrap simply won't find executables there. This is silent and safe.
- **`/opt/opencode`**: Already bind-mounted at line 351. Whether or not it's in PATH via host inheritance, it's already accessible — but host PATH also includes it.
- **Backward compatibility**: All existing behavior (`--with`, `--with-ro`, seccomp, SSH, mise activation) is unaffected since only the `--setenv PATH` line is removed.

## Success Metrics

- `ncu` runs without full path inside the sandbox
- `opencodebox -- bash -c 'echo $PATH'` matches host PATH
- Threat model is unchanged
- One line deleted — minimal diff

## Open Questions

None — resolved via the prior discussion.
