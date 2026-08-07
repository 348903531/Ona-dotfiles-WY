#!/usr/bin/env bash
#
# 让 /usr/local/bin/welcome-claude.sh 停止清空 ~/.claude/settings.json。
#
# ── 为什么需要这个补丁（2026-08-07 实测根因）────────────────────────────────
# Ona/Roche 镜像自带的 /usr/local/bin/welcome-claude.sh 被 ~/.bashrc 无条件
# `source`，它在 configure_claude_email() 末尾干了这么一件事：
#
#     jq --null-input ... '{env: {...}}' > "$HOME/.claude/settings.json"
#
# **整文件覆盖**——不是合并。于是每开一个 bash（包括 Claude Code 启动时为做
# shell-snapshot 而跑的那个），用户级 settings.json 就被重置成只剩 env 两条，
# 我们靠 merge_settings.py 装进去的 20 条 permissions.ask 与两个用户级 hook
# **全部蒸发**。实测证据：同一天 06:27 / 06:53 两次合并各留下一个备份，备份内容
# 都是「只有 env」——说明两次合并之间它又被清空了一次；14:27 再看，又只剩 env。
#
# 这是本仓 AGENTS.md 卡#25 的元形态、且更阴：不是「闸门放行了错的」，是**闸门
# 被别人删了，而且不吭声**。装过 ≠ 现在还活着。
#
# ── 补丁做什么 ─────────────────────────────────────────────────────────────
# 只把那一行「覆盖」换成「递归合并」：env 该写照写，其余顶层键（permissions /
# hooks / 用户手工加的任何东西）原样保留。行为对镜像的原意零损失。
#
# ── 安全 ───────────────────────────────────────────────────────────────────
#   · 幂等：认标记 PATCH_MARKER，已打过直接退 0。
#   · 备份：首次打补丁时留 .orig-<epoch>（root 所有，同目录）。
#   · 先验后换：`bash -n` 语法校验通过才 install，语法坏了立刻放弃并报错——
#     否则每开一个终端都会刷错，比原问题更糟。
#   · fail-soft：没有 sudo / 没有目标文件 / 结构对不上 → SKIP 退 0，
#     绝不让环境启动变红（自愈还有 shell/aliases.sh 那层兜底）。
#
# 用法：
#   bash patch_welcome_claude.sh            # 打补丁（install.sh 调用）
#   bash patch_welcome_claude.sh --check    # 只查不改，未打补丁则退 1
#   bash patch_welcome_claude.sh --revert   # 从 .orig 还原
#   bash patch_welcome_claude.sh --selftest # 好/坏输入各跑一遍（见它红过）

set -uo pipefail

TARGET="${WELCOME_CLAUDE_PATH:-/usr/local/bin/welcome-claude.sh}"
PATCH_MARKER="# [ona-dotfiles] merge-not-clobber"
CLOBBER_NEEDLE='> "$HOME/.claude/settings.json"'

log()  { printf '  [welcome-patch] %s\n' "$*"; }
skip() { printf '  [welcome-patch] SKIP %s\n' "$*"; exit 0; }

# 替换段：把原来的单行覆盖改成「读旧 → 递归合并 → 原子换」。
# `. * {env:{...}}` 是 jq 的递归合并：env 内其它键、以及 permissions/hooks
# 等所有顶层键都保住。旧文件不存在或不是合法 JSON → 回退到原来的 null-input
# 写法（等价于镜像原行为），保证任何情况下 env 都写得出去。
read -r -d '' REPLACEMENT <<'PATCHED_BLOCK' || true
        PATCH_MARKER_LINE
        _ona_tmp="$(mktemp 2>/dev/null)" || _ona_tmp=""
        if [ -n "$_ona_tmp" ]; then
            if ! jq --arg header "$header_value" --arg base_url "$base_url" \
                 '. * {env: {ANTHROPIC_CUSTOM_HEADERS: $header, ANTHROPIC_BASE_URL: $base_url}}' \
                 "$HOME/.claude/settings.json" > "$_ona_tmp" 2>/dev/null; then
                jq --null-input --arg header "$header_value" --arg base_url "$base_url" \
                   '{env: {ANTHROPIC_CUSTOM_HEADERS: $header, ANTHROPIC_BASE_URL: $base_url}}' \
                   > "$_ona_tmp" 2>/dev/null
            fi
            if [ -s "$_ona_tmp" ]; then
                mv "$_ona_tmp" "$HOME/.claude/settings.json"
            else
                rm -f "$_ona_tmp"
            fi
            unset _ona_tmp
        fi
PATCHED_BLOCK
REPLACEMENT="${REPLACEMENT/PATCH_MARKER_LINE/$PATCH_MARKER}"

is_patched() { [ -f "$TARGET" ] && grep -qF "$PATCH_MARKER" "$TARGET"; }

do_check() {
  [ -f "$TARGET" ] || { log "目标不存在：$TARGET（非 Ona 镜像？）"; return 0; }
  if is_patched; then log "✅ 已打补丁：$TARGET 不会再清空 settings.json"; return 0; fi
  log "❌ 未打补丁：$TARGET 仍会整文件覆盖 ~/.claude/settings.json"; return 1
}

do_revert() {
  local bak
  bak="$(ls -1t "$TARGET".orig-* 2>/dev/null | head -1)"
  [ -n "$bak" ] || skip "没有 .orig 备份可还原"
  sudo -n install -m 0755 -o root -g root "$bak" "$TARGET" \
    && log "已还原自 $bak" || log "还原失败（需要 sudo）"
}

do_patch() {
  [ -f "$TARGET" ] || skip "目标不存在：$TARGET"
  is_patched && { log "已是最新，无需改动"; exit 0; }
  grep -qF "$CLOBBER_NEEDLE" "$TARGET" \
    || skip "没找到预期的覆盖写法——上游可能已改，先别动（请人工看一眼 $TARGET）"
  sudo -n true 2>/dev/null || skip "没有免密 sudo，改不了系统文件（由 aliases.sh 的自愈兜底）"

  local tmp; tmp="$(mktemp)" || skip "mktemp 失败"
  # 用 python 做精确整行替换：找到那条以 `jq --null-input` 开头、以重定向到
  # settings.json 结尾的行，整行换成 REPLACEMENT 块。刻意不用 sed —— 该行含
  # 大量 $ / " / { }，sed 转义极易出错（本仓正则解析 shell 的坑吃过三次）。
  REPLACEMENT="$REPLACEMENT" python3 - "$TARGET" "$tmp" <<'PY'
import os, sys
src, dst = sys.argv[1], sys.argv[2]
repl = os.environ["REPLACEMENT"]
out, hits = [], 0
for line in open(src, encoding="utf-8").read().splitlines(True):
    s = line.strip()
    if s.startswith("jq --null-input") and s.endswith('> "$HOME/.claude/settings.json"'):
        out.append(repl.rstrip("\n") + "\n")
        hits += 1
    else:
        out.append(line)
if hits != 1:
    sys.stderr.write("expected exactly 1 clobber line, found %d\n" % hits)
    sys.exit(3)
open(dst, "w", encoding="utf-8").write("".join(out))
PY
  local rc=$?
  if [ $rc -ne 0 ]; then rm -f "$tmp"; skip "定位覆盖行失败（rc=$rc），不动它"; fi

  # 见它红过：语法坏了宁可不打补丁，也不能让每个终端刷错
  if ! bash -n "$tmp" 2>/dev/null; then
    rm -f "$tmp"; log "❌ 补丁后语法校验失败，已放弃（原文件未动）"; exit 0
  fi

  if [ ! -e "$TARGET".orig-* ] 2>/dev/null; then :; fi
  ls -1 "$TARGET".orig-* >/dev/null 2>&1 || \
    sudo -n cp -p "$TARGET" "$TARGET.orig-$(date +%s)" 2>/dev/null || true
  if sudo -n install -m 0755 -o root -g root "$tmp" "$TARGET" 2>/dev/null; then
    log "✅ 已打补丁：welcome-claude.sh 改为合并写入，不再清空 permissions/hooks"
  else
    log "补丁安装失败（sudo?），跳过——由 aliases.sh 自愈兜底"
  fi
  rm -f "$tmp"
}

selftest() {
  local ok=0 bad=0 tmpd; tmpd="$(mktemp -d)"
  _w() { if [ "$1" = 1 ]; then ok=$((ok+1)); echo "  PASS: $2"; else bad=$((bad+1)); echo "  FAIL: $2"; fi; }

  # 好输入：造一个和镜像同构的假 welcome-claude.sh，打补丁后应能合并
  cat > "$tmpd/good.sh" <<'EOS'
#!/usr/bin/env bash
configure_claude_email() {
    local header_value="H"; local base_url="U"
    if mkdir -p "$HOME/.claude" 2>/dev/null; then
        jq --null-input --arg header "$header_value" --arg base_url "$base_url" '{env: {ANTHROPIC_CUSTOM_HEADERS: $header, ANTHROPIC_BASE_URL: $base_url}}' > "$HOME/.claude/settings.json"
    fi
}
EOS
  WELCOME_CLAUDE_PATH="$tmpd/good.sh" bash "$0" --check >/dev/null 2>&1
  _w "$([ $? -ne 0 ] && echo 1 || echo 0)" "未打补丁时 --check 真的红（退出码非 0）"

  # 在 sandbox HOME 里跑：不需要 sudo，直接用 python 走一遍替换逻辑
  REPLACEMENT="$REPLACEMENT" python3 - "$tmpd/good.sh" "$tmpd/good.patched.sh" <<'PY'
import os, sys
src, dst = sys.argv[1], sys.argv[2]
repl = os.environ["REPLACEMENT"]; out=[]; hits=0
for line in open(src, encoding="utf-8").read().splitlines(True):
    s=line.strip()
    if s.startswith("jq --null-input") and s.endswith('> "$HOME/.claude/settings.json"'):
        out.append(repl.rstrip("\n")+"\n"); hits+=1
    else: out.append(line)
sys.exit(3) if hits!=1 else open(dst,"w",encoding="utf-8").write("".join(out))
PY
  _w "$([ -s "$tmpd/good.patched.sh" ] && echo 1 || echo 0)" "好输入：定位到唯一覆盖行并替换"
  bash -n "$tmpd/good.patched.sh" 2>/dev/null; _w "$([ $? -eq 0 ] && echo 1 || echo 0)" "补丁后 bash -n 语法通过"

  # 行为验证：补丁后必须保住已有的 permissions
  mkdir -p "$tmpd/home/.claude"
  printf '%s\n' '{"env":{"OLD":"x"},"permissions":{"ask":["Bash(rm -rf:*)"]}}' > "$tmpd/home/.claude/settings.json"
  ( export HOME="$tmpd/home"; . "$tmpd/good.patched.sh"; configure_claude_email ) >/dev/null 2>&1
  python3 - "$tmpd/home/.claude/settings.json" <<'PY'
import json,sys
d=json.load(open(sys.argv[1]))
sys.exit(0 if d.get("permissions",{}).get("ask")==["Bash(rm -rf:*)"]
         and d["env"]["ANTHROPIC_BASE_URL"]=="U" and d["env"]["OLD"]=="x" else 1)
PY
  _w "$([ $? -eq 0 ] && echo 1 || echo 0)" "补丁后：permissions 保住 + env 照常写入"

  # 坏输入：结构对不上的文件必须 SKIP、且一个字节不动
  printf '#!/bin/bash\necho hi\n' > "$tmpd/bad.sh"
  before="$(md5sum < "$tmpd/bad.sh")"
  WELCOME_CLAUDE_PATH="$tmpd/bad.sh" bash "$0" >/dev/null 2>&1
  after="$(md5sum < "$tmpd/bad.sh")"
  _w "$([ "$before" = "$after" ] && echo 1 || echo 0)" "坏输入：结构不符时原文件一个字节没动"

  rm -rf "$tmpd"
  echo; echo "$ok/$((ok+bad)) 通过"; [ $bad -eq 0 ]
}

case "${1:-}" in
  --check)    do_check ;;
  --revert)   do_revert ;;
  --selftest) selftest ;;
  "")         do_patch ;;
  *) echo "用法：$(basename "$0") [--check|--revert|--selftest]" >&2; exit 2 ;;
esac
