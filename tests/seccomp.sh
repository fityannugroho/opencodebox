#!/bin/bash
# Real seccomp integration test. No mocks: uses the repo's prebuilt BPF
# filter with a real bwrap. Skips cleanly when bwrap is unavailable.
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

exec {FILTER_FD}<"$FILTER"

echo "ok: benign command runs under filter"
bwrap --ro-bind /usr /usr --proc /proc --dev /dev \
    --seccomp "$FILTER_FD" -- /bin/true

echo "ok: AF_RXRPC socket blocked (EPERM expected)"
if bwrap --ro-bind /usr /usr --proc /proc --dev /dev \
    --seccomp "$FILTER_FD" -- \
    /usr/bin/python3 -c "import socket; socket.socket(socket.AF_RXRPC, socket.SOCK_DGRAM)" 2>/dev/null; then
    echo "FAIL: AF_RXRPC socket unexpectedly succeeded" >&2
    exit 1
fi

echo "PASS: seccomp integration"
