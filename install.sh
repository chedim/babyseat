#!/usr/bin/env bash
# babyseat (formerly membrane) installer -- curl | bash
#
#   curl -fsSL https://raw.githubusercontent.com/chedim/membrane/main/install.sh | bash
#
# Installs the `membrane` and `babyseat` CLIs globally (user-local, no sudo):
#   1. uv tool install  (preferred, if uv is on PATH)
#   2. fallback: a managed venv at ~/.membrane/venv + `membrane` on PATH
#
# Then, in ANY project directory, just run:
#   membrane opencode
# and it bootstraps that project on first use: agent skills into
# .opencode/skills/ there, per-project memory data into .membrane/ there.
#
# Re-running the installer is safe (upgrade/relink in place).
# Override the source (e.g. a local checkout while hacking):
#   MEMBRANE_REPO=/path/to/membrane bash install.sh

set -eo pipefail

REPO_URL="${MEMBRANE_REPO:-https://github.com/chedim/membrane.git}"
case "$REPO_URL" in
  /*) PIP_SPEC="$REPO_URL" ;;                    # local dir
  *)  PIP_SPEC="babyseat @ git+$REPO_URL" ;;   # the distribution's name (#303)
esac

BLUE='\033[1;34m'; GREEN='\033[1;32m'; YELLOW='\033[1;33m'; OFF='\033[0m'
say()  { printf "${BLUE}[membrane]${OFF} %s\n" "$*"; }
ok()   { printf "${GREEN}[membrane]${OFF} %s\n" "$*"; }
warn() { printf "${YELLOW}[membrane]${OFF} %s\n" "$*"; }

BIN_DIR="${MEMBRANE_BIN_DIR:-$HOME/.local/bin}"

# ------------------------------------------------------------- rust (0.7.5) ---
# babyseat's field engine is native (Rust): this installer builds it from source, so it needs cargo. When there is
# none, a user-local Rust toolchain is installed with rustup (no sudo, the shell profile left alone); the package's
# build (setuptools-rust) then compiles the engine. A `pip install babyseat` from PyPI needs none of this on macOS and
# Linux: the wheels there carry the engine compiled.
ensure_rust() {
  export PATH="$HOME/.cargo/bin:$PATH"
  command -v cargo >/dev/null 2>&1 && return 0
  say "babyseat's field engine is native (Rust): installing a user-local Rust toolchain (rustup, no sudo)..."
  command -v curl >/dev/null 2>&1 || { warn "curl is needed to fetch rustup"; return 1; }
  curl -sSf https://sh.rustup.rs | sh -s -- -y --profile minimal --no-modify-path >/dev/null \
    || { warn "rustup failed: install Rust (https://rustup.rs) and re-run"; return 1; }
  command -v cargo >/dev/null 2>&1 || { warn "cargo still not on PATH ($HOME/.cargo/bin)"; return 1; }
  ok "Rust ready ($(cargo --version))"
}

# ---------------------------------------------------------------- uv path ---
install_with_uv() {
  command -v uv >/dev/null 2>&1 || return 1
  say "installing with uv (isolated tool venv, upgrades on re-run)..."
  uv tool install --force "$PIP_SPEC" >/dev/null
  ok "membrane CLI installed via uv -> $(command -v membrane || echo "$HOME/.local/bin/membrane")"
}

# -------------------------------------------------------------- venv path ---
install_with_venv() {
  local PY="${MEMBRANE_PYTHON:-}"
  [ -n "$PY" ] || PY="$(command -v python3 || command -v python)"
  [ -n "$PY" ] || { warn "no python3 found on PATH"; return 1; }

  local VENV="$HOME/.membrane/venv"
  say "installing into $VENV (managed venv)..."
  "$PY" -m venv "$VENV"
  "$VENV/bin/pip" install --upgrade pip -q
  "$VENV/bin/pip" install -q "$PIP_SPEC"

  mkdir -p "$BIN_DIR"
  ln -sfn "$VENV/bin/membrane" "$BIN_DIR/membrane"
  ln -sfn "$VENV/bin/babyseat" "$BIN_DIR/babyseat"
  ok "membrane CLI installed -> $BIN_DIR/membrane"

  case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *) warn "$BIN_DIR is not on your PATH -- add it, e.g.:"
       warn "  echo 'export PATH=\"$BIN_DIR:\$PATH\"' >> ~/.zshrc" ;;
  esac
}

# ------------------------------------------------------------------- main ---
ensure_rust || exit 1
say "building babyseat (its native field engine compiles here: a minute or two the first time)..."
if ! install_with_uv 2>/dev/null; then
  install_with_venv
fi

# the field engine must be in: a seat without it fails at startup
_py="$HOME/.membrane/venv/bin/python"
[ -x "$_py" ] || _py="$(uv tool dir 2>/dev/null)/babyseat/bin/python"
if [ -x "$_py" ] && ! "$_py" -c "from membrane import membrane_vm" >/dev/null 2>&1; then
  warn "the native field engine did not build (membrane.membrane_vm is missing): re-run with Rust on PATH"
  exit 1
fi

# sanity check
if ! "$BIN_DIR/membrane" --help >/dev/null 2>&1 \
   && ! membrane --help >/dev/null 2>&1; then
  warn "installed, but the smoke check failed -- open a fresh shell and try: membrane --help"
fi

ok "done. in any project directory:"
ok "  babyseat            # the seat with its own web UI (= membrane standalone)"
ok "  membrane opencode   # field-backed opencode; auto-installs skills + .membrane/ there on first run"
ok "  membrane doctor     # what's installed/running"
ok "  MEMBRANE_NO_AUTOINSTALL=1 membrane opencode   # never touch the project dir"