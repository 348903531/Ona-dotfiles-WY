#!/usr/bin/env bash
#
# 按 vscode/extensions.txt 把跨项目 VS Code 扩展装进**所有**存在的 server 侧。
#
# 契约：fail-soft，永远 exit 0（--verify 除外）。扩展是「锦上添花的开发体验」，
# 装不上不该让环境启动变红。但 fail-soft ≠ fail-silent：
#   · 缺料（没有任何 server / 没网）→ SKIP，安静跳过合理；
#   · 有料却失败（marketplace 拒绝、装了没生效）→ 结尾打 ❌ 汇总，别假绿。
#
# 用法：
#   bash install_extensions.sh            # 装（install.sh 调用）
#   bash install_extensions.sh --verify   # 只查不装，缺一个即退 1

set -uo pipefail

MODE="install"
[ "${1:-}" = "--verify" ] && MODE="verify"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIST="$HERE/extensions.txt"
log()  { printf '  [vscode-ext] %s\n' "$*"; }
skip() { printf '  [vscode-ext] SKIP %s\n' "$*"; exit 0; }

[ -f "$LIST" ] || skip "没有 $LIST"

# ── 定位两侧的 code-server ────────────────────────────────────────────────
# 刻意用 server 自带的 code-server + 显式目录，不用 PATH 上的 `code`：后者是
# 薄客户端，靠 $VSCODE_IPC_HOOK_CLI 转发给「已连上的窗口」——环境创建期没有
# 窗口，有窗口时又会**不返回**（实测 100s 未退被 timeout 杀）。
LABELS=(); CMDS=()
c="$(ls -t "$HOME"/.vscode-server/cli/servers/*/server/bin/code-server 2>/dev/null | head -1)"
[ -n "$c" ] && [ -x "$c" ] && { LABELS+=("桌面Remote"); CMDS+=("$c --extensions-dir $HOME/.vscode-server/extensions"); }
for c in /usr/local/gitpod/shared/vscode/vscode-server/bin/*/bin/code-server; do
  [ -x "$c" ] && { LABELS+=("网页版"); CMDS+=("$c --server-data-dir $HOME/.vscode-browser-server"); break; }
done
[ ${#LABELS[@]} -eq 0 ] && skip "找不到任何 VS Code server（纯 SSH / CI）"

WANT=()
while IFS= read -r line; do
  line="${line%%#*}"; line="$(printf '%s' "$line" | tr -d '[:space:]')"
  [ -n "$line" ] && WANT+=("$line")
done < "$LIST"
[ ${#WANT[@]} -eq 0 ] && skip "清单为空"

FAILS=0
for i in "${!LABELS[@]}"; do
  # shellcheck disable=SC2086  # 刻意分词：命令前缀含参数，路径无空格
  have="$(${CMDS[$i]} --list-extensions 2>/dev/null)"
  for id in "${WANT[@]}"; do
    if printf '%s\n' "$have" | grep -qix "$id"; then continue; fi
    if [ "$MODE" = "verify" ]; then
      printf '  [vscode-ext] ❌ %s 不在 [%s] 侧\n' "$id" "${LABELS[$i]}"; FAILS=$((FAILS+1)); continue
    fi
    log "装 $id → [${LABELS[$i]}]"
    # shellcheck disable=SC2086
    if timeout 180 ${CMDS[$i]} --install-extension "$id" --force >/dev/null 2>&1; then
      log "  ✓ $id"
    else
      printf '  [vscode-ext] ❌ %s 装不上（marketplace 不可达？）\n' "$id"; FAILS=$((FAILS+1))
    fi
  done
done

if [ "$FAILS" -gt 0 ]; then
  printf '  [vscode-ext] ❌ %d 项未就绪\n' "$FAILS"
  [ "$MODE" = "verify" ] && exit 1
else
  # 一律留一行痕迹：静默通过看不出「跑没跑过」，是假绿的温床
  log "${#WANT[@]} 个跨项目扩展在 ${#LABELS[@]} 侧均已就绪"
fi
exit 0
