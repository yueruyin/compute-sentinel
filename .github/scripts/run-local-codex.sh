#!/usr/bin/env bash
set -euo pipefail

SANDBOX_MODE="${1:?sandbox mode is required}"
PROMPT_FILE="${2:?prompt file is required}"
OUTPUT_FILE="${3:?output file is required}"

# Prefer the same ARM64 NVM toolchain that is used successfully in the
# interactive shell. A self-hosted runner does not necessarily inherit the
# interactive shell's NVM PATH, and /usr/local/bin may contain Intel/Rosetta
# Node binaries on Apple Silicon Macs.
NVM_DIR="${NVM_DIR:-$HOME/.nvm}"
PREFERRED_NVM_BIN="$NVM_DIR/versions/node/v22.23.2/bin"

if [ -x "$PREFERRED_NVM_BIN/node" ] && [ -e "$PREFERRED_NVM_BIN/codex" ]; then
  export PATH="$PREFERRED_NVM_BIN:/opt/homebrew/bin:$HOME/.local/bin:$HOME/bin:/usr/local/bin:$PATH"
elif [ -s "$NVM_DIR/nvm.sh" ]; then
  # shellcheck disable=SC1090
  . "$NVM_DIR/nvm.sh"
  nvm use --silent default >/dev/null 2>&1 || true
  export PATH="$PATH:/opt/homebrew/bin:$HOME/.local/bin:$HOME/bin:/usr/local/bin"
else
  export PATH="/opt/homebrew/bin:$HOME/.local/bin:$HOME/bin:/usr/local/bin:$PATH"
fi

# This workflow intentionally uses the local ChatGPT/Codex login, not API-key
# billing. Clear common API overrides so the CLI reuses the stored ChatGPT auth.
unset OPENAI_API_KEY || true
unset CODEX_API_KEY || true
unset OPENAI_BASE_URL || true

if ! command -v node >/dev/null 2>&1; then
  echo "::error::当前 self-hosted runner 找不到 node 命令。"
  exit 1
fi

if ! command -v codex >/dev/null 2>&1; then
  echo "::error::当前 self-hosted runner 找不到 codex 命令。请在运行 Runner 的同一 macOS 用户下安装 Codex CLI，并确认 command -v codex 可用。"
  exit 1
fi

HOST_ARCH="$(uname -m)"
NODE_BIN="$(command -v node)"
NODE_ARCH="$(node -p 'process.arch')"
CODEX_BIN="$(command -v codex)"

echo "Host architecture: $HOST_ARCH"
echo "Node binary: $NODE_BIN"
echo "Node architecture: $NODE_ARCH"
echo "Codex binary: $CODEX_BIN"
file "$NODE_BIN" || true

if [ "$HOST_ARCH" = "arm64" ] && [ "$NODE_ARCH" != "arm64" ]; then
  echo "::error::Runner 是 ARM64，但当前 Node 架构为 $NODE_ARCH。PATH 中可能混入了 /usr/local/bin 的 Intel/Rosetta Node。"
  exit 1
fi

echo "Codex CLI: $(codex --version)"

LOGIN_STATUS="$(codex login status 2>&1 || true)"
echo "$LOGIN_STATUS"
if ! printf '%s' "$LOGIN_STATUS" | grep -q "Logged in using ChatGPT"; then
  echo "::error::当前 Runner 用户没有使用 ChatGPT 登录 Codex。请在运行 Runner 的同一用户下执行 codex login，并确认 codex login status 显示 'Logged in using ChatGPT'。"
  exit 1
fi

if [ ! -f "$PROMPT_FILE" ]; then
  echo "::error::Prompt 文件不存在: $PROMPT_FILE"
  exit 1
fi

mkdir -p "$(dirname "$OUTPUT_FILE")"
rm -f "$OUTPUT_FILE"

# `codex exec` is the official non-interactive CLI entry point. `--ephemeral`
# avoids filling the local Codex history with CI orchestration sessions.
codex exec \
  --ephemeral \
  --sandbox "$SANDBOX_MODE" \
  --output-last-message "$OUTPUT_FILE" \
  - < "$PROMPT_FILE"

if [ ! -s "$OUTPUT_FILE" ]; then
  echo "::error::Codex 执行结束，但没有生成最终输出文件: $OUTPUT_FILE"
  exit 1
fi
