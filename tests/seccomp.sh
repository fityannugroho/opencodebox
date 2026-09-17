#!/bin/bash
# Real seccomp integration test. No mocks: uses the repo's prebuilt BPF
# filter with a real bwrap. Skips cleanly when bwrap or user namespaces
# are unavailable (e.g. GitHub-hosted runners).
# Note: this loads the repo's .bpf, while the launcher loads the installed
# copy at $HOME/.local/share/opencodebox/seccomp-security.bpf. A drift
# between the two is not covered here; argv wiring is covered by pytest.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

case "$(uname -m)" in
    x86_64) FILTER="$REPO_ROOT/seccomp/seccomp-security-x86_64.bpf" ;;
    aarch64 | arm64) FILTER="$REPO_ROOT/seccomp/seccomp-security-aarch64.bpf" ;;
    *) echo "SKIP: unsupported arch $(uname -m) for seccomp filter" ; exit 0 ;;
esac

if ! command -v bwrap >/dev/null 2>&1; then
    echo "SKIP: bwrap not installed"
    exit 0
fi

if [[ ! -f "$FILTER" ]]; then
    echo "FAIL: filter missing: $FILTER" >&2
    exit 1
fi

# Positive control first: the probe must succeed WITHOUT the filter,
# otherwise a failure under the filter proves nothing.
PROBE=(/usr/bin/python3 -c "import socket; socket.socket(socket.AF_RXRPC, socket.SOCK_DGRAM)")
if [[ ! -x /usr/bin/python3 ]]; then
    echo "SKIP: /usr/bin/python3 not available for socket probe"
    exit 0
fi

exec {FILTER_FD}<"$FILTER"

BASE_ARGS=(--ro-bind /usr /usr --proc /proc --dev /dev)

PREFLIGHT_ERR="$(bwrap "${BASE_ARGS[@]}" -- /bin/true 2>&1)" || {
    if [[ "$PREFLIGHT_ERR" == *"uid map"* || "$PREFLIGHT_ERR" == *"user namespace"* \
        || "$PREFLIGHT_ERR" == *"Operation not permitted"* || "$PREFLIGHT_ERR" == *"permission"* ]]; then
        echo "SKIP: sandbox unavailable: $PREFLIGHT_ERR"
        exit 0
    fi
    echo "FAIL: bwrap preflight failed: $PREFLIGHT_ERR" >&2
    exit 1
}
echo "ok: bwrap sandbox runs"

bwrap "${BASE_ARGS[@]}" --seccomp "$FILTER_FD" -- /bin/true
echo "ok: benign command runs under filter"

if ! bwrap "${BASE_ARGS[@]}" -- "${PROBE[@]}" 2>/dev/null; then
    echo "SKIP: AF_RXRPC probe fails even without filter (kernel lacks RXRPC); cannot verify blocking"
    exit 0
fi
echo "ok: probe succeeds without filter (positive control)"

if bwrap "${BASE_ARGS[@]}" --seccomp "$FILTER_FD" -- "${PROBE[@]}" 2>/dev/null; then
    echo "FAIL: AF_RXRPC socket unexpectedly succeeded under filter" >&2
    exit 1
fi
echo "ok: AF_RXRPC socket blocked under filter"

echo "PASS: seccomp integration"
