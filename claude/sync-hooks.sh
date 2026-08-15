#!/usr/bin/env bash
#
# 把 WY-workspace-P 仓库里的 hook 源文件同步成本 dotfiles 的用户级副本。
#
# 为什么会有两份
# ==============
# 项目级（WY-workspace-P/.claude/hooks/）跟着那个仓库走，fork 给同事时一起带上；
# 用户级（本目录 → ~/.claude/hooks/）跟着**人**走，换项目换容器都在。两者都需要，
# 所以必然有两份。这个脚本让「两份」不至于变成「两个版本」。
#
# 纪律：**源在 WY-workspace-P，不要直接改这里的副本。** 改完源跑一次本脚本。
# 副本头部会自动插入 preamble（说明来源 + 防双响逻辑），不需要手写。
#
# 用法：
#   bash ~/dotfiles/claude/sync-hooks.sh            # 同步
#   bash ~/dotfiles/claude/sync-hooks.sh --check    # 只检查是否漂移（CI/收尾自查用）
#                                                   # 漂移则退出码 1

set -uo pipefail

SRC_REPO="${WY_REPO:-/workspaces/WY-workspace-P}"
SRC_DIR="$SRC_REPO/.claude/hooks"
DST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/hooks"
HOOKS=(destructive-command-guard.py session-change-digest.py regen-overwrite-guard.py unverified-claim-guard.py)
CHECK_ONLY=0
[ "${1:-}" = "--check" ] && CHECK_ONLY=1

if [ ! -d "$SRC_DIR" ]; then
  echo "  [sync-hooks] 源仓库不在（$SRC_DIR）——跳过。"
  echo "  这不是错误：在别的项目/机器上没有那个仓库很正常，副本照常可用。"
  exit 0
fi

gen() {   # gen <源文件> → stdout（源 + preamble）
  python3 - "$1" <<'PY'
import io, re, sys, os
src = io.open(sys.argv[1], encoding="utf-8").read()
name = os.path.basename(sys.argv[1])
preamble = '''
# ══════════════════════════════════════════════════════════════════════════
# 用户级副本（~/.claude/hooks/）——由 Ona-dotfiles 装到**每一个**项目
#
# 来源：WY-workspace-P 仓库的 .claude/hooks/%s
# 改动纪律：**以来源仓库为准**。改了那边就跑 ~/dotfiles/claude/sync-hooks.sh
# 同步过来；不要只改这一份，否则两边漂移。
#
# 为什么要有用户级副本：项目级的只跟着那个仓库走，换个项目就没了。用户明确要求
# 「以后所有规定默认跨项目、跨容器通用」（2026-08-07），所以安全网必须装在用户级。
#
# 防双响：在**同时**有项目级同名 hook 的仓库里（如 WY-workspace-P），本副本静默
# 退出、让项目级那份跑——它带着仓库自己的上下文，更准。
# ══════════════════════════════════════════════════════════════════════════
import os as _os
import sys as _sys

_proj = _os.environ.get("CLAUDE_PROJECT_DIR") or ""
if _proj and _os.path.isfile(
        _os.path.join(_proj, ".claude", "hooks", _os.path.basename(__file__))):
    _sys.exit(0)          # 项目级已装同名 hook → 让它来，避免同一件事报两遍
''' % name
# 插入点：docstring 之后；但若源文件有 `from __future__ import ...`，
# 必须插在它**之后**——__future__ 导入必须紧跟 docstring，插前面会 SyntaxError。
# （2026-08-14 踩到：regen-overwrite-guard.py 带 __future__，生成的副本语法不过。）
marker = '\n"""\n'
i = src.index(marker, src.index('"""')) + len(marker)
fut = re.search(r'^from __future__ import [^\n]*\n', src[i:], re.M)
if fut and fut.start() < 200:          # 只认紧跟 docstring 的那一条
    i += fut.end()
sys.stdout.write(src[:i] + preamble + src[i:])
PY
}

drift=0
for h in "${HOOKS[@]}"; do
  [ -f "$SRC_DIR/$h" ] || { echo "  [sync-hooks] 源缺 $h，跳过"; continue; }
  tmp="$(mktemp)"
  gen "$SRC_DIR/$h" > "$tmp" || { echo "  [sync-hooks] 生成 $h 失败"; rm -f "$tmp"; drift=1; continue; }
  if [ -f "$DST_DIR/$h" ] && diff -q "$tmp" "$DST_DIR/$h" >/dev/null 2>&1; then
    echo "  [sync-hooks] $h 已是最新"
  elif [ "$CHECK_ONLY" = 1 ]; then
    echo "  [sync-hooks] ⚠️  $h 与源仓库**已漂移** —— 跑 sync-hooks.sh 同步"
    drift=1
  else
    # 先验语法再写盘：旧版是「先 mv 覆盖、再 py_compile」，语法不过时坏文件
    # 已经落到 ~/.claude/hooks/ 了，而末行还照打 ✅——等于用一个坏副本替换好副本。
    # 2026-08-14 实测踩到，改为「验过才覆盖」。
    if ! python3 -m py_compile "$tmp" 2>/dev/null; then
      echo "  [sync-hooks] ❌ $h 生成后语法不过，**不覆盖**已有副本；请修 gen() 的插入点"
      python3 -m py_compile "$tmp" 2>&1 | tail -3
      rm -f "$tmp"; drift=1; continue
    fi
    mkdir -p "$DST_DIR"
    mv "$tmp" "$DST_DIR/$h"
    echo "  [sync-hooks] ✅ 已同步 $h"
  fi
  rm -f "$tmp"
done

# 同步完跑一次自测：副本必须自己能跑通，不能只是「文件拷过来了」
if [ "$CHECK_ONLY" = 0 ] && [ "$drift" = 0 ]; then
  for h in "${HOOKS[@]}"; do
    [ -f "$DST_DIR/$h" ] || continue
    if python3 "$DST_DIR/$h" --selftest >/dev/null 2>&1; then
      echo "  [sync-hooks] $h --selftest ✓"
    else
      echo "  [sync-hooks] ❌ $h --selftest 未通过，别提交"
      drift=1
    fi
  done
fi

exit "$drift"
