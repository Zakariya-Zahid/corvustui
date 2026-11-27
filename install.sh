#!/usr/bin/env bash
# CorvusTUI installer.
#
# Preferred: uv tool install (isolated, easy upgrades)
# Fallbacks: pipx, then pip --user
#
# Local checkout:
#   ./install.sh
#
# From GitHub (after replacing OWNER):
#   curl -fsSL https://raw.githubusercontent.com/OWNER/corvustui/main/install.sh | bash
#
# Optional environment variables:
#   CORVUS_EXTRAS=tui        # default: all (tui,rag,web)
#   CORVUS_VERSION=v0.1.0    # git tag/branch for remote installs
#   CORVUS_SOURCE=/path      # explicit local path or git URL
#   CORVUS_REPO_URL=...      # override the repository URL

set -euo pipefail

REPO_URL="${CORVUS_REPO_URL:-git+https://github.com/OWNER/corvustui.git}"
EXTRAS="${CORVUS_EXTRAS:-all}"
VERSION="${CORVUS_VERSION:-}"

log() {
    printf '\033[1;36m==>\033[0m %s\n' "$*"
}

warn() {
    printf '\033[1;33mwarning:\033[0m %s\n' "$*" >&2
}

die() {
    printf '\033[1;31merror:\033[0m %s\n' "$*" >&2
    exit 1
}

if [[ ! "$EXTRAS" =~ ^[A-Za-z0-9_,.-]+$ ]]; then
    die "CORVUS_EXTRAS contains invalid characters: $EXTRAS"
fi

# Pick the source: explicit override, local checkout, or the repository URL.
if [[ -n "${CORVUS_SOURCE:-}" ]]; then
    SOURCE="$CORVUS_SOURCE"
elif [[ -f "pyproject.toml" && -d "src/corvustui" ]]; then
    SOURCE="."
else
    SOURCE="$REPO_URL"
fi

# Build a PEP 508 direct reference with extras so uv/pipx/pip all understand it.
if [[ "$SOURCE" == "." || "$SOURCE" == ./* || "$SOURCE" == /* ]]; then
    SOURCE_DIR="$(cd "$SOURCE" && pwd)"
    SPEC="corvustui[$EXTRAS] @ file://$SOURCE_DIR"
else
    if [[ "$SOURCE" == *"/OWNER/"* && -z "${CORVUS_ALLOW_PLACEHOLDER:-}" ]]; then
        die "Set CORVUS_REPO_URL to your repository, e.g. CORVUS_REPO_URL=git+https://github.com/you/corvustui.git"
    fi
    if [[ -n "$VERSION" ]]; then
        SOURCE="${SOURCE}@${VERSION}"
    fi
    SPEC="corvustui[$EXTRAS] @ $SOURCE"
fi

log "Installing CorvusTUI with extras: $EXTRAS"

INSTALLER=""
BIN_DIR=""
if command -v uv >/dev/null 2>&1; then
    INSTALLER="uv"
    BIN_DIR="$(uv tool dir --bin 2>/dev/null || true)"
    log "Using uv tool install"
    uv tool install --force "$SPEC"
elif command -v pipx >/dev/null 2>&1; then
    INSTALLER="pipx"
    BIN_DIR="$(pipx environment --value PIPX_BIN_DIR 2>/dev/null || true)"
    log "Using pipx"
    pipx install --force "$SPEC"
elif command -v python3 >/dev/null 2>&1; then
    INSTALLER="pip"
    BIN_DIR="$(python3 -m site --user-base 2>/dev/null || true)/bin"
    log "Using pip --user"
    python3 -m pip install --user --upgrade "$SPEC"
else
    die "No installer found. Install uv first: https://docs.astral.sh/uv/getting-started/installation/"
fi

# Fall back to the conventional location if the installer did not report one.
if [[ -z "$BIN_DIR" || "$BIN_DIR" == "/bin" ]]; then
    BIN_DIR="$HOME/.local/bin"
fi
BIN_DIR="${BIN_DIR%/}"

if [[ -x "$BIN_DIR/corvustui" ]]; then
    log "Installed: $BIN_DIR/corvustui"
    case ":$PATH:" in
        *":$BIN_DIR:"*) ;;
        *)
            warn "$BIN_DIR is not on your PATH."
            warn "Add it with: export PATH=\"$BIN_DIR:\$PATH\""
            if [[ "$INSTALLER" == "uv" ]]; then
                warn "Or run: uv tool update-shell"
            elif [[ "$INSTALLER" == "pipx" ]]; then
                warn "Or run: pipx ensurepath"
            fi
            warn "Then restart your shell."
            ;;
    esac
else
    warn "The install finished, but '$BIN_DIR/corvustui' was not found."
    warn "Run 'command -v corvustui' to locate it, or check the installer output above."
fi

cat <<'EOF'

CorvusTUI is ready.

Next steps:
  1. Start Ollama (for local models):  ollama serve
  2. Pull a model if needed:           ollama pull qwen3.5:0.8b
  3. Launch the TUI:                   corvustui tui
  4. See all commands:                 corvustui --help
EOF
