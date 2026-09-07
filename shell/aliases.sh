#!/usr/bin/env bash
# Personal shell aliases & functions, sourced from ~/.bashrc / ~/.zshrc by
# install.sh. Keep this POSIX-friendly and side-effect-free (no installs here).

# --- git shortcuts ---
alias gs='git status'
alias gd='git diff'
alias gl='git log --oneline --graph --decorate -20'
alias gp='git pull --ff-only'
alias gco='git checkout'
alias gb='git branch'

# Sync current repo's main, then return to your branch:
#   gsync            -> updates local main from origin (ff-only)
gsync() {
  local cur
  cur="$(git rev-parse --abbrev-ref HEAD 2>/dev/null)" || { echo "not a git repo"; return 1; }
  git fetch origin main && git checkout main && git pull --ff-only && git checkout "$cur"
}

# Push the dotfiles repo to BOTH the primary remote and the cross-account
# backup mirror (348903531/Ona-dotfiles-WY), in one go.
#   dotfiles-sync ["commit message"]
# If a message is given and there are staged/unstaged changes, it commits first.
# The backup lives under a different GitHub account, so we push it with a token
# injected ONLY at push time (never written to .git/config, never echoed).
# Requires env var SYNC_PAT_348 (fine-grained PAT with write to 348903531).
dotfiles-sync() {
  local dir="$HOME/dotfiles" msg="${1:-}"
  ( cd "$dir" || return 1
    if [ -n "$msg" ] && ! git diff --quiet --cached 2>/dev/null; then :; fi
    if [ -n "$msg" ] && { ! git diff --quiet 2>/dev/null || ! git diff --quiet --cached 2>/dev/null; }; then
      git add -A && git commit -m "$msg"$'\n\nCo-authored-by: Ona <no-reply@ona.com>'
    fi
    # 1) primary remote
    git push || { echo "primary push failed"; return 1; }
    # 2) cross-account backup mirror (token injected at push time only)
    if [ -n "${SYNC_PAT_348:-}" ]; then
      git push "https://x-access-token:${SYNC_PAT_348}@github.com/348903531/Ona-dotfiles-WY.git" main:main \
        2>&1 | sed "s/${SYNC_PAT_348}/***/g"
    else
      echo "SYNC_PAT_348 not set — backup mirror skipped (primary push done)"
    fi
  )
}

# --- Claude 用户级安全网自愈（第二层兜底）-------------------------------------
# 第一层是 claude/patch_welcome_claude.sh：把镜像自带 welcome-claude.sh 的
# 「整文件覆盖 ~/.claude/settings.json」改成「合并」，从源头止血。
# 但那层依赖 ①有免密 sudo ②镜像那行写法没变。这里是**不依赖任何一条**的兜底：
# ~/.bashrc 里 dotfiles 这段 source 排在 `source welcome-claude.sh` 之后，
# 所以只要开过一个新 shell，被清掉的 permissions/hooks 就会被补回来。
#
# 造价刻意压到近乎为零：只做一次 grep，**没被清空就什么都不干**（不起 python）。
# 全程 fail-soft，任何异常都静默——shell 启动绝不能因为它变慢或报错。
_ona_claude_settings_heal() {
  local f="$HOME/.claude/settings.json"
  local m="$HOME/dotfiles/claude/merge_settings.py"
  [ -f "$f" ] && [ -f "$m" ] || return 0
  grep -q '"permissions"' "$f" 2>/dev/null && return 0   # 还活着，走人
  python3 "$m" >/dev/null 2>&1 || true
}
_ona_claude_settings_heal

# --- 标题栏环境名自愈（治「只在环境创建那一刻写过一次」）----------------------
# vscode/set_window_title.sh 原本只由 install.sh 调用，而 install.sh 只在**环境
# 创建那一刻**跑一次（环境 stop→start 都不重跑）。于是两类漂移从此没人管：
#   ① 环境被改名 → 标题永远停在创建时的名字。2026-09-07 实测：本环境早已改叫
#      「VSCode CC-EU-CENTRAL」，标题栏还挂着「VSCode CC-US-WEST」，挂了 12 天。
#      期间 set_window_title.sh --check 与 doctor 一直在报红，只是没人去跑。
#   ② `.vscode-browser-server` 目录在环境创建**之后**才出现（本环境实测创建于
#      8-26、该目录 9-07 才建）→ 脚本刻意不凭空造目录，于是网页版那一侧从头到尾
#      没被写过，且没有任何时机会去补。
# 判据同上面那条 settings 自愈：期望值唯一确定（现查一次就知道）、要改的东西不在
# git 里（改了不脏任何工作区）、留了显式后门 → 可以自动修，不必只做提醒。
#
# 造价：`ona environment get -f name` 实测 ~290ms，且**整段丢进后台子 shell**，
# shell 启动一毫秒都不等；再加 60 秒节流，防某些工具连开一串 shell 时猛刷 API。
# 名字没变时脚本自己 exit 0 不写盘，所以重复跑是幂等的。
# 关掉它：export ONA_WINDOW_TITLE_HEAL=off
_ona_window_title_heal() {
  [ "${ONA_WINDOW_TITLE_HEAL:-on}" = "off" ] && return 0
  local s="$HOME/dotfiles/vscode/set_window_title.sh"
  [ -f "$s" ] || return 0
  command -v ona >/dev/null 2>&1 || return 0            # 非 Ona 环境，走人

  # 60 秒节流，防某些工具连开一串 shell 时猛刷 API。
  # 刻意**不用** `find -mmin +1`：它只有分钟粒度、且是「严格大于」，实测真实阈值
  # 是 120 秒而不是 60（2026-09-07：100 秒判「还新」、130 秒才判「已旧」）——注释
  # 与行为对不上，本身就是下一个坑；本机 find 是 bfs，`-newermt` 又不吃相对时间。
  # dotfiles 只在 Linux 容器里跑，`stat -c` 可用；取不到就当过期、宁可多跑一次。
  local stamp="${TMPDIR:-/tmp}/.ona-window-title.stamp"
  local now last
  now="$(date +%s 2>/dev/null || echo 0)"
  last="$(stat -c %Y "$stamp" 2>/dev/null || echo 0)"
  [ "$now" -gt 0 ] && [ $(( now - last )) -lt 60 ] && return 0

  # 整个复合命令一起重定向，**不能**写成 `: > "$stamp" 2>/dev/null`：重定向按从
  # 左到右生效，`> "$stamp"` 失败时报错是 shell 打在**还没被改掉**的 fd 2 上的，
  # 后面那个 2>/dev/null 根本轮不到，`|| true` 也只吞退出码、不吞消息。
  # 实测（2026-09-07）四种触发：只读 /tmp、别人拥有的 stamp、rc 里设了 noclobber、
  # TMPDIR 指向已删目录——每一种都让用户**每开一个终端**看见一行红字。
  { : > "$stamp"; } 2>/dev/null || true

  # fail-soft ≠ fail-silent（脚本自己的注释里就写着这条）：全丢 /dev/null 的话，
  # 「环境名连着好几天解析失败」这件事一点痕迹都不留。留一份小日志，超 20KB 清空。
  # 落点先探可写再用，探不通就退回 /dev/null——用 `>>` 不用 `>`，既不截断、
  # 也不会被 rc 里的 noclobber 拦；整段照样包在 { } 里，理由同上面那条。
  local log="${TMPDIR:-/tmp}/.ona-window-title.log" sink="/dev/null"
  if { : >> "$log"; } 2>/dev/null; then
    sink="$log"
    [ "$(wc -c < "$log" 2>/dev/null || echo 0)" -gt 20000 ] && { : > "$log"; } 2>/dev/null
  fi

  # --only-if-known：拿不到环境名时什么都不做。别用一次网络抖动把已经写对的标题
  # 抹成退化版——这条路 detach 在后台，抹了当场没人会知道。
  # （脚本里另有一道不依赖本参数的兜底：盘上已带 [名字] 就永不降级。）
  ( bash "$s" --only-if-known >>"$sink" 2>&1 & ) >/dev/null 2>&1
}
_ona_window_title_heal

# 刚改完环境名、想立刻看到标题跟上：敲这条（不等 60 秒节流、也不用新开终端）
alias fix-window-title='bash "$HOME/dotfiles/vscode/set_window_title.sh"'

# --- 「设定没生效」自动提醒（2026-09-07 用户点名：只提醒不动手，但要提醒到位）---
# 体检脚本一直都在、判据也对，但**要人主动敲命令才会跑**——真实后果是标题栏名字
# 错了 12 天、三个功能装了没接上、设定仓库落后 16 个版本，而没有一次结论到达过
# 用户眼前。用户原话：「像这次标题栏名字错了，你其实也没提醒我呀。」
# **一个需要你先想起来去查的提醒，等于没有提醒。** 所以把结论推到每次开终端时。
#
# 用户同轮明确选了「只提醒不动手」：这里只显示 + 给一行修法，绝不自动装东西改配置。
# 造价：显示是毫秒级（只 cat 一个文件）；真正的体检 1.3 秒，丢后台、每天最多一次。
# 关掉它：export ONA_ENV_HEALTH_NOTICE=off
_ona_env_health_notice() {
  [ "${ONA_ENV_HEALTH_NOTICE:-on}" = "off" ] && return 0
  local s="$HOME/dotfiles/shell/env_health_notice.sh"
  [ -f "$s" ] || return 0

  bash "$s"                                    # 显示上一次的结论，毫秒级

  local stamp="${TMPDIR:-/tmp}/.ona-env-health.stamp"
  local now last
  now="$(date +%s 2>/dev/null || echo 0)"
  last="$(stat -c %Y "$stamp" 2>/dev/null || echo 0)"
  [ "$now" -gt 0 ] && [ $(( now - last )) -lt 86400 ] && return 0
  { : > "$stamp"; } 2>/dev/null || true        # { } 包起来的理由同上面那条
  ( bash "$s" --refresh >/dev/null 2>&1 & ) >/dev/null 2>&1
}
_ona_env_health_notice

# 一键修：把设定拉到最新 + 重新装一遍（幂等）。子 shell 里 cd，不改你当前目录。
alias dotfiles-fix='( cd "$HOME/dotfiles" && git pull --ff-only ) && bash "$HOME/dotfiles/install.sh"'

# 一键体检：装过 ≠ 现在还活着（见 claude/doctor.sh）
alias dotfiles-doctor='bash "$HOME/dotfiles/claude/doctor.sh"'

# --- navigation ---
alias ll='ls -alh'
alias ..='cd ..'
alias ...='cd ../..'

# --- safety ---
alias rm='rm -i'
alias cp='cp -i'
alias mv='mv -i'
