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
