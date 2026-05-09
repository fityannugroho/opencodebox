#!/bin/bash
set -e

REPO="fityannugroho/opencodebox"
RAW_URL="https://raw.githubusercontent.com/$REPO/main/opencodebox"
INSTALL_DIR="$HOME/.local/bin"
INSTALL_PATH="$INSTALL_DIR/opencodebox"

echo "Installing opencodebox from $REPO..."

# Create install directory if it doesn't exist
mkdir -p "$INSTALL_DIR"

# Download opencodebox script
if curl -fsSL "$RAW_URL" -o "${INSTALL_PATH}.tmp"; then
    mv "${INSTALL_PATH}.tmp" "$INSTALL_PATH"
else
    rm -f "${INSTALL_PATH}.tmp"
    echo "ERROR: Failed to download opencodebox from $RAW_URL"
    exit 1
fi

# Make it executable
chmod +x "$INSTALL_PATH"

echo "opencodebox installed to $INSTALL_PATH"

echo ""
echo "Security check for CVE-2017-5226 (TIOCSTI sandbox escape):"

if sysctl -n dev.tty.legacy_tiocsti >/dev/null 2>&1; then
    LEGACY=$(sysctl -n dev.tty.legacy_tiocsti 2>/dev/null || echo "1")
    if [[ "$LEGACY" == "0" ]]; then
        echo "  [OK] Kernel >= 6.2 with TIOCSTI disabled (protected)"
    else
        echo "  [WARN] Kernel >= 6.2 but legacy_tiocsti=1 (TIOCSTI allowed)"
        echo "    Disable it with: echo 'dev.tty.legacy_tiocsti=0' | sudo tee /etc/sysctl.d/99-tiocsti.conf && sudo sysctl --system"
    fi
else
    echo "  [INFO] Kernel < 6.2 - ensure bubblewrap >= 0.1.5 with seccomp"
fi

if command -v bwrap >/dev/null 2>&1; then
    BWRAP_VERSION=$(bwrap --version 2>&1 | grep -oP '\d+\.\d+\.\d+' | head -1)
    echo "  [INFO] bubblewrap version: $BWRAP_VERSION"
fi

# Download seccomp BPF filter
echo ""
echo "Setting up seccomp BPF filter..."

SECCOMP_DIR="$HOME/.local/share/opencodebox"
mkdir -p "$SECCOMP_DIR"

ARCH=$(uname -m)
case "$ARCH" in
    x86_64)
        SECCOMP_ARCH="x86_64"
        ;;
    aarch64|arm64)
        SECCOMP_ARCH="aarch64"
        ;;
    *)
        echo "  [WARN] Unsupported architecture: $ARCH"
        echo "    Seccomp BPF filter not available for this architecture"
        echo "    opencodebox will run without seccomp sandbox filter"
        SECCOMP_ARCH=""
        ;;
esac

if [[ -n "$SECCOMP_ARCH" ]]; then
    SECCOMP_URL="https://raw.githubusercontent.com/$REPO/main/seccomp/seccomp-security-${SECCOMP_ARCH}.bpf"
    SECCOMP_FILE="$SECCOMP_DIR/seccomp-security.bpf"

    if curl -fsSL "$SECCOMP_URL" -o "${SECCOMP_FILE}.tmp"; then
        mv "${SECCOMP_FILE}.tmp" "$SECCOMP_FILE"
        echo "  [OK] Seccomp BPF filter downloaded for $SECCOMP_ARCH"
    else
        rm -f "${SECCOMP_FILE}.tmp"
        echo "  [WARN] Failed to download seccomp BPF filter for $SECCOMP_ARCH"
        echo "    opencodebox will run without seccomp sandbox filter"
    fi
fi

# Check if install directory is in PATH
if [[ ":$PATH:" != *":$INSTALL_DIR:"* ]]; then
    echo ""
    echo "WARNING: $INSTALL_DIR is not in your PATH."
    echo "Add it by running:"
    echo "  echo 'export PATH=\"\$HOME/.local/bin:\$PATH\"' >> ~/.bashrc"
    echo "  or restart your shell if already added."
fi

echo ""
echo "Done! Run 'opencodebox --version' to verify."
