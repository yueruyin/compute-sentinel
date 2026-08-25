#!/usr/bin/env bash
set -euo pipefail

SANDBOX_MODE="${1:?sandbox mode is required}"
PROMPT_FILE="${2:?prompt file is required}"
OUTPUT_FILE="${3:?output file is required}"

# GitHub self-hosted runner services do not always inherit the same PATH as an
# interactive terminal. Include the common Apple Silicon/Homebrew and user bins.
export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$HOME/bin:$PATH"

# This workflow intentionally uses the local ChatGPT/Codex login, not API-key
# billing. Clear common API overrides so the CLI reuses the stored ChatGPT auth.
unset OPENAI_API_KEY || true
unset CODEX_API_KEY || true
unset OPENAI_BASE_URL || true

if ! command -v codex >/dev/null 2>&1; then
  echo "::error::当前 self-hosted runner 找不到 codex 命令。请在运行 Runner 的同一 macOS 用户下安装 Codex CLI，并确认 command -v codex 可用。"
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
