#!/bin/sh
# OriMem one-line installer for macOS / Linux.
#
#   curl -LsSf https://raw.githubusercontent.com/Wen5555/OriMem/main/scripts/install.sh | sh
#
# Environment overrides:
#   ORIMEM_SOURCE      package source (default: GitHub main archive; a local path also works)
#   ORIMEM_INIT_ARGS   arguments for `orimem init` (default: interactive); "skip" skips init
#   ORIMEM_MIRROR      "cn" forces China mirrors, "none" disables auto-detection
#   ORIMEM_NO_MODIFY_PATH=1  do not add uv's tool directory to shell profiles
set -eu

SOURCE="${ORIMEM_SOURCE:-https://github.com/Wen5555/OriMem/archive/refs/heads/main.zip}"
step() { printf '\033[36m==> %s\033[0m\n' "$1"; }
reachable() { curl -fsS -o /dev/null -m 5 -I "$1" 2>/dev/null; }

# 1. Mirrors for networks where PyPI / GitHub are slow or blocked.
USE_CN=0
case "${ORIMEM_MIRROR:-auto}" in
  cn) USE_CN=1 ;;
  none) USE_CN=0 ;;
  *) reachable https://pypi.org/simple/pip/ || USE_CN=1 ;;
esac
if [ "$USE_CN" = 1 ]; then
  step "Using China mirrors (Tsinghua PyPI, npmmirror Python builds, hf-mirror.com)"
  export UV_DEFAULT_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple
  export UV_PYTHON_INSTALL_MIRROR=https://registry.npmmirror.com/-/binary/python-build-standalone
  export HF_ENDPOINT=https://hf-mirror.com
fi

# 2. uv (manages Python and an isolated tool environment).
if ! command -v uv >/dev/null 2>&1; then
  step "Installing uv"
  if [ "$USE_CN" = 0 ] && curl -LsSf https://astral.sh/uv/install.sh | sh; then
    :
  elif command -v python3 >/dev/null 2>&1; then
    if [ "$USE_CN" = 1 ]; then python3 -m pip install --user --upgrade uv -i "$UV_DEFAULT_INDEX"
    else python3 -m pip install --user --upgrade uv; fi
  else
    echo "Could not install uv. Install it from https://docs.astral.sh/uv/ and re-run." >&2; exit 1
  fi
  PATH="$HOME/.local/bin:$PATH"; export PATH
fi

# 3. OriMem itself.
step "Installing OriMem from $SOURCE"
uv tool install --force --python 3.12 "$SOURCE"
[ -n "${ORIMEM_NO_MODIFY_PATH:-}" ] || uv tool update-shell >/dev/null 2>&1 || true
PATH="$(uv tool dir --bin):$PATH"; export PATH

# 4. First-time setup (reads answers from the terminal even when piped from curl).
if [ "${ORIMEM_INIT_ARGS:-}" = skip ]; then
  step "Skipping init (run 'orimem init' later)"
elif [ -n "${ORIMEM_INIT_ARGS:-}" ]; then
  step "Running orimem init $ORIMEM_INIT_ARGS"
  # shellcheck disable=SC2086
  orimem init $ORIMEM_INIT_ARGS
elif [ -r /dev/tty ]; then
  step "Running orimem init"
  orimem init </dev/tty
else
  step "Running orimem init --yes (no terminal attached)"
  orimem init --yes
fi

printf '\n\033[32mOriMem is installed. Restart Claude Code / Codex / DeepSeek Harness to load it.\033[0m\n'
echo "  orimem doctor             check the setup"
echo "  uv tool upgrade orimem    update later"
