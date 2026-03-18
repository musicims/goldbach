#!/bin/bash
# ============================================================================
# GOLDBACH VERIFICATION ENGINE — Build Script
# ============================================================================
# Detects your CPU, picks optimal compiler flags, and builds.
# Just run: ./build.sh
# ============================================================================

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

echo -e "${CYAN}=====================================================================${NC}"
echo -e "${CYAN}  GOLDBACH VERIFICATION ENGINE — Build${NC}"
echo -e "${CYAN}=====================================================================${NC}"
echo ""

# Find the source
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SRC="$SCRIPT_DIR/goldbach.c"

if [ ! -f "$SRC" ]; then
    echo -e "${RED}ERROR: goldbach.c not found in $SCRIPT_DIR${NC}"
    exit 1
fi

# Find a C compiler
CC=""
for candidate in gcc cc clang; do
    if command -v "$candidate" &>/dev/null; then
        CC="$candidate"
        break
    fi
done

# Fallback: check for zig cc (portable, no install needed)
if [ -z "$CC" ]; then
    if command -v zig &>/dev/null; then
        CC="zig cc"
    elif [ -x "/tmp/zig-linux-x86_64-0.13.0/zig" ]; then
        CC="/tmp/zig-linux-x86_64-0.13.0/zig cc"
    fi
fi

if [ -z "$CC" ]; then
    echo -e "${RED}ERROR: No C compiler found.${NC}"
    echo ""
    echo "Install one with:"
    echo "  Ubuntu/Debian:  sudo apt install gcc"
    echo "  Fedora/RHEL:    sudo dnf install gcc"
    echo "  macOS:          xcode-select --install"
    echo "  Arch:           sudo pacman -S gcc"
    echo ""
    echo "Or download Zig (portable, no sudo needed):"
    echo "  curl -sL https://ziglang.org/download/0.13.0/zig-linux-x86_64-0.13.0.tar.xz | tar xJ -C /tmp"
    echo "  export PATH=/tmp/zig-linux-x86_64-0.13.0:\$PATH"
    echo "  ./build.sh"
    exit 1
fi

echo -e "Compiler:  ${GREEN}$CC${NC} ($(${CC} --version 2>&1 | head -1))"

# Detect CPU
ARCH=$(uname -m)
OS=$(uname -s)
CORES=$(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 4)

echo -e "Platform:  ${GREEN}${OS} ${ARCH}${NC}"
echo -e "CPU cores: ${GREEN}${CORES}${NC}"

# Pick optimal flags
CFLAGS="-O3 -pthread -lm"

# Architecture-specific optimizations
if [ "$ARCH" = "x86_64" ]; then
    # Test for march=native support
    if echo "int main(){return 0;}" | $CC -x c -march=native -o /dev/null - 2>/dev/null; then
        CFLAGS="-O3 -march=native -pthread -lm"
        echo -e "Tuning:    ${GREEN}native (auto-detected CPU features)${NC}"
    else
        CFLAGS="-O3 -pthread -lm"
        echo -e "Tuning:    ${YELLOW}generic x86_64${NC}"
    fi
elif [ "$ARCH" = "aarch64" ] || [ "$ARCH" = "arm64" ]; then
    # ARM64 (Apple Silicon, Graviton, etc.)
    if echo "int main(){return 0;}" | $CC -x c -march=native -o /dev/null - 2>/dev/null; then
        CFLAGS="-O3 -march=native -pthread -lm"
        echo -e "Tuning:    ${GREEN}native ARM64${NC}"
    else
        CFLAGS="-O3 -pthread -lm"
        echo -e "Tuning:    ${YELLOW}generic ARM64${NC}"
    fi

    # Check for __int128 support (needed for mulmod)
    if ! echo "typedef unsigned __int128 u128; int main(){u128 a=1;return 0;}" | $CC -x c -o /dev/null - 2>/dev/null; then
        echo -e "${RED}ERROR: Compiler does not support __int128 (required)${NC}"
        exit 1
    fi
fi

# Build
OUT="$SCRIPT_DIR/goldbach"
echo ""
echo -e "Building:  ${CYAN}$CC $CFLAGS goldbach.c -o goldbach${NC}"
$CC $CFLAGS "$SRC" -o "$OUT"

# Verify it built
if [ ! -x "$OUT" ]; then
    echo -e "${RED}Build failed.${NC}"
    exit 1
fi

SIZE=$(ls -lh "$OUT" | awk '{print $5}')
echo ""
echo -e "${GREEN}Build successful!${NC}"
echo -e "Binary:    $OUT ($SIZE)"
echo ""
echo -e "Run:       ${CYAN}./goldbach${NC}              (interactive menu)"
echo -e "           ${CYAN}./goldbach --selftest${NC}    (verify installation)"
echo -e "           ${CYAN}./goldbach --help${NC}        (all options)"
echo ""
