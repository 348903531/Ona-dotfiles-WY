#!/usr/bin/env bash
# 「你的通用设定在这个容器里没生效」——每天自动提醒一次，不用你敲任何命令。
#
# ── 为什么要有这个（2026-09-07 用户点名）───────────────────────────────────
# dotfiles-doctor 这套体检一直都在、判据也写得好好的，但它**要人主动敲命令才会跑**。
# 真实后果：VS Code 标题栏的环境名错了 12 天、三个功能装了没接上、设定仓库落后 16
# 个版本，体检脚本天天查得出来，而**没有一次结论到达过用户眼前**。用户原话：
# 「像这次标题栏名字错了，你其实也没提醒我呀。」
#   **一个需要你先想起来去查的提醒，等于没有提醒。**
#
# 用户同轮明确选了「只提醒不动手」——不自动装东西、不自动改配置，所以这里只显示
# 结论 + 给一行修法，动手与否由人决定。
#
# ── 为什么是两段式 ────────────────────────────────────────────────────────
# doctor 跑一次约 1.3 秒（含一次 git fetch），挂在 shell 启动上会被明显感知到。
# 所以拆开：
#   · 显示（默认，毫秒级）：把**上一次**后台体检的结论打出来
#   · 刷新（--refresh，后台）：跑一次 doctor，把 ❌ 那几条写进 state
# 代价是「刚建好的环境第一次开终端看不到结论，第二次才看到」——可接受，因为这类
# 漂移的时间尺度是天，不是分钟。
#
# ── 为什么只摘 ❌ 不摘 ⚠️ ──────────────────────────────────────────────────
# doctor 的 ⚠️ 里有几条是常态噪音（有未提交改动、本会话档位是 auto、某扩展没装）。
# 全报 = 天天满屏 = 人开始无视 = 提醒本身作废。宁可少报也要保证「一出现就值得看」。
# 想看全部：dotfiles-doctor。
#
# 用法：
#   env_health_notice.sh            显示（shell 启动调这个）
#   env_health_notice.sh --refresh  后台刷新（不显示）
#   env_health_notice.sh --now      立刻刷新并显示（人工查用）

set -uo pipefail

STATE="${TMPDIR:-/tmp}/.ona-env-health"
DOCTOR="$HOME/dotfiles/claude/doctor.sh"

_write_state() {
  # 整个复合命令一起重定向——不能写成 `: > "$f" 2>/dev/null`：重定向从左到右生效，
  # `> "$f"` 失败时报错是 shell 打在还没被改掉的 fd 2 上的（2026-09-07 实测，
  # 只读 /tmp、noclobber、TMPDIR 指向已删目录都会打印）。
  { : > "$STATE"; } 2>/dev/null || true
}

refresh() {
  [ -f "$DOCTOR" ] || return 0
  local out fails n
  out="$(bash "$DOCTOR" 2>/dev/null)" || true
  fails="$(printf '%s\n' "$out" | grep '❌' || true)"

  if [ -z "$fails" ]; then
    _write_state                       # 全绿 → 清空，下次开终端一个字都不打扰
    return 0
  fi

  n="$(printf '%s\n' "$fails" | grep -c '❌' || echo 0)"
  # 最多列 4 条。一次刷 13 行的「提醒」跟没提醒是一个效果——人会开始整段跳过，
  # 而那正是这套机制要治的病（2026-09-07 测试时真打出过 13 行，当场改掉）。
  {
    printf '\n\033[33m⚠️  你的通用设定有 %s 处在这个容器里没生效\033[0m \033[2m（每天提醒一次）\033[0m\n' "$n"
    printf '%s\n' "$fails" | head -4 | sed 's/^ */   /'
    if [ "${n:-0}" -gt 4 ] 2>/dev/null; then
      printf '   \033[2m…另有 %s 条\033[0m\n' "$(( n - 4 ))"
    fi
    printf '   \033[2m一键修\033[0m \033[1mdotfiles-fix\033[0m   \033[2m看全部\033[0m \033[1mdotfiles-doctor\033[0m   \033[2m关掉\033[0m \033[2mexport ONA_ENV_HEALTH_NOTICE=off\033[0m\n\n'
  } > "$STATE.tmp" 2>/dev/null && mv "$STATE.tmp" "$STATE" 2>/dev/null || true
}

show() { [ -s "$STATE" ] && cat "$STATE" 2>/dev/null; return 0; }

case "${1:-}" in
  --refresh) refresh ;;
  --now)     refresh; show ;;
  *)         show ;;
esac
exit 0
