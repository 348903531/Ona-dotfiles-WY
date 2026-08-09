#!/usr/bin/env bash
#
# dotfiles-doctor —— 一条命令回答「我的跨项目设置，现在还活着吗？」
#
# ── 为什么需要它 ───────────────────────────────────────────────────────────
# 本仓所有跨项目设置都靠「在新环境里自动再发生一次」成立，而这类机制**会静默
# 失效**：软链断了、系统脚本把配置覆盖了、hook 指到已删的目录、dotfiles 改了
# 没 push。共同点是——**坏了不吭声，你以为它在，其实早没了**。
# 「装过 ≠ 现在还活着」，所以每一层都要有一道「它还在不在」的自查。
#
# 退出码：0 = 全绿；1 = 有 FAIL（该修）。WARN 不影响退出码。
# 用法：dotfiles-doctor        （alias，见 shell/aliases.sh）
#       bash ~/dotfiles/claude/doctor.sh

set -uo pipefail

DOT="$HOME/dotfiles"
FAILS=0; WARNS=0
G=$'\033[32m'; R=$'\033[31m'; Y=$'\033[33m'; D=$'\033[2m'; N=$'\033[0m'

ok()   { printf '  %s✅%s %s\n' "$G" "$N" "$1"; }
bad()  { printf '  %s❌%s %s\n' "$R" "$N" "$1"; [ -n "${2:-}" ] && printf '     %s修：%s%s\n' "$D" "$2" "$N"; FAILS=$((FAILS+1)); }
warn() { printf '  %s⚠️%s  %s\n' "$Y" "$N" "$1"; [ -n "${2:-}" ] && printf '     %s%s%s\n' "$D" "$2" "$N"; WARNS=$((WARNS+1)); }
head_() { printf '\n%s\n' "$1"; }

printf '\n跨项目设置体检（层④ = 换项目、换容器都还在）\n'

# ── 1. dotfiles 仓库本身 ───────────────────────────────────────────────────
head_ "1) dotfiles 仓库"
if [ -d "$DOT/.git" ]; then
  ok "已 clone 到 $DOT"
  if [ -n "$(git -C "$DOT" status --porcelain 2>/dev/null)" ]; then
    warn "有未提交改动——改了不 commit+push，新环境 clone 不到 = 白改" \
         "dotfiles-sync \"说明这次改了什么\""
  else ok "工作区干净"; fi
  git -C "$DOT" fetch -q origin 2>/dev/null || true
  local_ahead="$(git -C "$DOT" rev-list --count origin/main..HEAD 2>/dev/null || echo 0)"
  if [ "${local_ahead:-0}" != "0" ]; then
    bad "有 $local_ahead 个提交没 push——新环境拿不到" "dotfiles-sync"
  else ok "已与远端同步"; fi
else
  bad "$DOT 不存在或不是 git 仓库" "在 Ona 账户设置里配 dotfiles repo，或手动 clone 后跑 install.sh"
fi

# ── 2. Claude 全局偏好（agent 行为）───────────────────────────────────────
head_ "2) Claude 全局偏好 ~/.claude/CLAUDE.md"
if [ -L "$HOME/.claude/CLAUDE.md" ] && [ -e "$HOME/.claude/CLAUDE.md" ]; then
  ok "软链有效 → $(readlink "$HOME/.claude/CLAUDE.md")"
elif [ -e "$HOME/.claude/CLAUDE.md" ]; then
  warn "是普通文件、不是指向 dotfiles 的软链——改了不会进 git" "bash ~/dotfiles/install.sh"
else
  bad "缺失：所有项目都读不到你的全局偏好" "bash ~/dotfiles/install.sh"
fi

# ── 2b. 输出风格（agent 怎么说话）─────────────────────────────────────────
#
# 这一层有个**独有的静默失效形态**，别的层没有：settings.json 里的 outputStyle 写着
# 一个名字，而对应的风格文件没装上 / 名字对不上 → Claude Code 静默回退到 Default，
# **不报错、不提示**，你只会觉得「怎么最近它又说回机器话了」，永远想不到是配置断了。
# 所以这里不只查软链在不在，还要真解析 frontmatter 的 name，跟 settings 里的值对账。
head_ "2b) 输出风格 ~/.claude/output-styles"
_style_want="$(python3 -c "
import json,sys
try: print((json.load(open('$HOME/.claude/settings.json')) or {}).get('outputStyle') or '')
except Exception: print('')
" 2>/dev/null)"
_style_dir="$HOME/.claude/output-styles"
if [ -z "$_style_want" ]; then
  warn "settings.json 没设 outputStyle——回复风格用内置 Default（机器腔）" \
       "bash ~/dotfiles/install.sh"
elif printf '%s' "$_style_want" | grep -qxE 'Default|Explanatory|Learning|Proactive'; then
  ok "用的是内置风格「$_style_want」（不需要文件，正常）"
else
  # 自定义风格：必须能在已装的 .md 里找到同名 name（或同名文件）
  _style_hit="$(python3 - "$_style_dir" "$_style_want" <<'PY' 2>/dev/null
import os, re, sys
d, want = sys.argv[1], sys.argv[2]
for fn in sorted(os.listdir(d)) if os.path.isdir(d) else []:
    if not fn.endswith(".md"):
        continue
    p = os.path.join(d, fn)
    try:
        head = open(p, encoding="utf-8").read(2000)
    except Exception:
        continue
    m = re.search(r"^name:\s*(.+?)\s*$", head, re.M)
    name = m.group(1).strip() if m else fn[:-3]
    if name == want:
        print(os.path.realpath(p))
        break
PY
)"
  if [ -n "$_style_hit" ]; then
    case "$_style_hit" in
      "$DOT"/*) ok "风格「$_style_want」已装且指向 dotfiles（换项目换容器都在）" ;;
      *) warn "风格「$_style_want」装着，但不在 dotfiles 里——换台机器就没了" \
              "bash ~/dotfiles/install.sh" ;;
    esac
  else
    bad "settings 要用风格「$_style_want」，但 $_style_dir 里找不到它——Claude Code 会静默退回默认机器腔" \
        "bash ~/dotfiles/install.sh"
  fi
fi

# ── 3. 用户级权限安全网（最脆的一层）──────────────────────────────────────
head_ "3) 用户级权限安全网 ~/.claude/settings.json"
SET="$HOME/.claude/settings.json"
if [ -f "$SET" ]; then
  read -r n_ask n_hook has_env dmode <<<"$(python3 - "$SET" <<'PY' 2>/dev/null || echo "err err err err"
import json,sys
d=json.load(open(sys.argv[1]))
print(len((d.get("permissions") or {}).get("ask") or []),
      sum(len(v) for v in (d.get("hooks") or {}).values()),
      int("ANTHROPIC_BASE_URL" in (d.get("env") or {})),
      (d.get("permissions") or {}).get("defaultMode") or "-")
PY
)"
  if [ "$n_ask" = "err" ]; then
    bad "settings.json 不是合法 JSON" "先人工修好它，再 python3 ~/dotfiles/claude/merge_settings.py"
  else
    # 期望条数**从片段现读**，不写死——写死的话每次增删 ask 名单都要记得同步改这里，
    # 漏改的那次正好是「名单被清空、体检却仍报绿」。同型教训见下方 hook 名那段。
    # 2026-08-09 修正判据方向：用户要求 ask 清零后，这里原来的「应 ≥11 条，少了就 FAIL」
    # 当场把**正确的新状态**报成了 FAIL。根因是那句兜底 `[ "$_n_want" -gt 0 ] || _n_want=11`
    # **把「读到 0」和「读不到」混为一谈**——片段真的是 0 条时，它当成读取失败、退回硬编码 11。
    # 教训同型：判据要跟着 SSOT 走，而「读不到就退回旧硬编码」这种兜底会在 SSOT 合法地
    # 变成 0/空 时反噬。现在只把**非数字**当读取失败，读到 0 就是 0。
    _n_want="$(python3 -c "import json,io,sys;d=json.load(io.open(sys.argv[1],encoding='utf-8'));print(len((d.get('permissions') or {}).get('ask') or []))" \
               "$DOT/claude/settings-permissions.json" 2>/dev/null)"
    case "$_n_want" in ''|*[!0-9]*) _n_want=0 ;; esac
    if [ "$_n_want" -eq 0 ]; then
      [ "${n_ask:-0}" -eq 0 ] && ok "permissions.ask 已清零（用户 2026-08-09 要求：一条都别弹）" \
        || warn "permissions.ask 还剩 $n_ask 条残留，这几条仍会弹窗" \
                "python3 ~/.claude/hooks/no-prompt-guard.py   # 直接跑一次即清掉"
    else
      [ "${n_ask:-0}" -ge "$_n_want" ] && ok "permissions.ask $n_ask 条（做完真回不去的操作仍会弹窗）" \
        || bad "permissions.ask 只有 $n_ask 条（应 ≥$_n_want）——安全网被清空了" \
               "python3 ~/dotfiles/claude/merge_settings.py"
    fi
    [ "${n_hook:-0}" -ge 2 ] && ok "用户级 hook $n_hook 个（危险命令中文弹窗 + 收尾改动清单）" \
      || bad "用户级 hook 只有 $n_hook 个（应 ≥2）" "python3 ~/dotfiles/claude/merge_settings.py"
    [ "${has_env:-0}" = "1" ] && ok "内网代理 env 完好（合并没伤到它）" \
      || warn "env.ANTHROPIC_BASE_URL 不在——新开一个终端会自动补回"
    # defaultMode：会话起步用哪种权限模式。没有这个键 = 每个新会话都从 Manual 起步、
    # 每条命令都弹窗——这正是「装过 ≠ 现在还活着」在权限层的形态，而且它坏了完全不吭声
    # （不会报错，只是又开始一直问你）。刻意不把「值不是 bypassPermissions」判成 bad：
    # 用户主动收紧回 default/plan/auto 是合法选择，体检不该逼他改回来。
    case "${dmode:--}" in
      -)  warn "permissions.defaultMode 没设——新会话会从 Manual 起步、每条命令都弹窗" \
               "python3 ~/dotfiles/claude/merge_settings.py" ;;
      bypassPermissions)
          ok "defaultMode=bypassPermissions（技术执行类不再弹窗；ask 剩 ${n_ask} 条，详见 3b 节）" ;;
      *)  ok "defaultMode=$dmode（你自己设的，体检不改它）" ;;
    esac
  fi
else
  bad "settings.json 不存在" "python3 ~/dotfiles/claude/merge_settings.py"
fi
# 待查的 hook 名**从 settings-permissions.json 现读**，不写死在这里——写死的话每加一个
# hook 就要记得改两处，而漏改的那次正好是「新 hook 静默缺失、体检却报全绿」（本次差点
# 发生：加了第三个 hook，条数变 3、逐条核验却仍只查那两个老的）。读不到就退回硬编码兜底。
_hook_names="$(python3 - "$DOT/claude/settings-permissions.json" <<'PY' 2>/dev/null
import json, re, sys
try:
    d = json.load(open(sys.argv[1], encoding="utf-8"))
except Exception:
    sys.exit(1)
names = []
for entries in (d.get("hooks") or {}).values():
    for e in entries:
        for h in e.get("hooks") or []:
            m = re.search(r"hooks/([A-Za-z0-9_-]+)\.py", h.get("command") or "")
            if m and m.group(1) not in names:
                names.append(m.group(1))
print(" ".join(names))
PY
)"
[ -n "$_hook_names" ] || _hook_names="destructive-command-guard session-change-digest"
for h in $_hook_names; do
  p="$HOME/.claude/hooks/$h.py"
  if [ -e "$p" ]; then ok "hook 脚本在：$h.py"
  else bad "hook 脚本缺失/断链：$h.py" "bash ~/dotfiles/install.sh"; fi
done

# ── 3b. 免弹窗四层：不是「设过没有」，是「现在还全不全」────────────────────
#
# 为什么单列一节而不是并进第 3 节：第 3 节查的是 defaultMode 这**一层**，而 2026-08-09
# 实测发现免弹窗其实是**四层**，前两层全绿、真凶在后两层（Bash 沙箱 + ask 名单）。
# 当时 doctor 报的正是「全绿」，用户却还在被弹——**体检查了它认识的层，就宣布一切正常**,
# 这本身就是一种假绿。所以这一节改成调 no-prompt-guard.py --check，让「守配置」的那个
# 脚本自己来报，体检和 hook 共用同一份判据、不会各说各话（判据只有一份，不会漂移）。
head_ "3b) 免弹窗四层（沙箱 / ask 名单 / 项目级）"
_npg="$HOME/.claude/hooks/no-prompt-guard.py"
if [ ! -e "$_npg" ]; then
  bad "no-prompt-guard.py 缺失/断链——没有任何东西在守免弹窗配置了" "bash ~/dotfiles/install.sh"
else
  _npg_out="$(python3 "$_npg" --check 2>&1)"; _npg_rc=$?
  printf '%s\n' "$_npg_out" | sed 's/^/  /'
  if [ "$_npg_rc" -eq 0 ]; then
    ok "四层齐全（开机 hook 会把前三项自动修回去，不用你记）"
  else
    bad "有层没到位——上面标 ❌ 的那几条会让命令继续弹窗" \
        "python3 ~/.claude/hooks/no-prompt-guard.py   # 直接跑一次即自动修复（项目级需手改）"
  fi
fi

# ── 4. 止血补丁：镜像脚本别再清空上面那一层 ───────────────────────────────
head_ "4) welcome-claude.sh 覆盖补丁"
if bash "$DOT/claude/patch_welcome_claude.sh" --check >/dev/null 2>&1; then
  ok "已打补丁——它现在是合并写入，不会再清空 permissions/hooks"
else
  if [ -f /usr/local/bin/welcome-claude.sh ]; then
    bad "未打补丁：每开一个终端都会把上面那层清空" "bash ~/dotfiles/claude/patch_welcome_claude.sh"
  else
    ok "本环境没有该脚本（非 Ona 镜像），无需补丁"
  fi
fi
if grep -q "_ona_claude_settings_heal" "$HOME/.bashrc" 2>/dev/null \
   || grep -q "ona-dotfiles aliases" "$HOME/.bashrc" 2>/dev/null; then
  ok "shell 自愈已挂进 ~/.bashrc（补丁失效时的第二层兜底）"
else
  bad "~/.bashrc 没挂 dotfiles aliases，自愈兜底不存在" "bash ~/dotfiles/install.sh"
fi

# ── 5. VS Code 扩展（两个 server 目录都要有）──────────────────────────────
head_ "5) VS Code 扩展（跨项目声明式安装）"
LIST="$DOT/vscode/extensions.txt"
if [ -f "$LIST" ]; then
  found_any=0
  for dir in "$HOME/.vscode-server/extensions" "$HOME/.vscode-browser-server/extensions"; do
    [ -d "$dir" ] || continue
    found_any=1
    missing=""
    while read -r id; do
      case "$id" in ''|\#*) continue ;; esac
      ls -d "$dir/$id"-* >/dev/null 2>&1 || missing="$missing $id"
    done < "$LIST"
    label="$(basename "$(dirname "$dir")")/$(basename "$dir")"
    if [ -z "$missing" ]; then ok "$(basename "$(dirname "$dir")") 侧：声明的扩展全在"
    else warn "$(basename "$(dirname "$dir")") 侧缺：$missing" "bash ~/dotfiles/install.sh"; fi
  done
  [ "$found_any" = "1" ] || ok "本环境没有 VS Code server 目录（纯 SSH/CI），跳过"
else
  warn "没有 $LIST，扩展装配未声明化（换环境要手装）"
fi
if [ -f "$DOT/vscode/set_window_title.sh" ]; then
  if bash "$DOT/vscode/set_window_title.sh" --check >/dev/null 2>&1; then
    ok "标题栏环境名：两侧 server 都是当前环境的名字"
  else
    bad "标题栏环境名没写全（某侧缺 / 名字过期）" "bash ~/dotfiles/vscode/set_window_title.sh"
  fi
fi

# ── 6. 只能你自己在本地 VS Code 里确认的（容器内看不到）──────────────────
head_ "6) 容器内查不到的两项（存在你本地电脑上）"
printf '  %s· Bypass permissions 总开关：VS Code 设置搜 "dangerously skip"，\n' "$D"
printf '    必须勾在 %sUser%s 标签页（跟你这台电脑走、对所有项目生效），\n' "$N$D" "$D"
printf '    勾在 Remote/Workspace 就只对当前容器/当前项目成立。\n'
printf '    与上面第 3 节的 defaultMode 是%s一对%s、缺一不可：defaultMode 决定\n' "$N$D" "$D"
printf '    「新会话起步在哪个模式」（跟 dotfiles 走、换容器还在），这个 toggle\n'
printf '    决定「这台电脑上允不允许出现该模式」。agent 无权代勾——那属自我授权。\n'
printf '  · dotfiles 仓库地址：Ona 账户设置 → Dotfiles，填 Ona-dotfiles 仓库。\n'
printf '    没填的话新环境不会自动 clone，本文件所有检查都无从谈起。%s\n' "$N"

# ── 汇总 ──────────────────────────────────────────────────────────────────
printf '\n'
if [ "$FAILS" -eq 0 ]; then
  printf '%s全绿%s —— %d 项提醒。跨项目设置健在。\n\n' "$G" "$N" "$WARNS"; exit 0
else
  printf '%s%d 项 FAIL%s / %d 项提醒 —— 按上面「修：」逐条执行，或直接 %sbash ~/dotfiles/install.sh%s 一把梭。\n\n' \
    "$R" "$FAILS" "$N" "$WARNS" "$D" "$N"; exit 1
fi
