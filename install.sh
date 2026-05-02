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
curl -fsSL "$RAW_URL" -o "$INSTALL_PATH"

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
