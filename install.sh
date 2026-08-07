#!/usr/bin/env bash
#
# Ona dotfiles installer.
#
# Ona clones this repo to ~/dotfiles on every new environment and runs this
# script automatically. Its job: layer YOUR personal, cross-project preferences
# on top of whatever Dev Container the project ships, so every environment feels
# like home and the AI agent picks up your global preferences.
#
# DESIGN RULES (per Ona docs):
#   - Non-interactive: never prompt (no `read`, no interactive installers) or the
#     environment hangs at startup.
#   - Fast: every second here adds to environment startup time.
#   - Self-contained & idempotent: safe to re-run; check before installing.
#   - No secrets: never hardcode tokens/passwords here. Use Ona secrets instead.
#
# Run manually to re-apply in a running environment:
#   cd ~/dotfiles && git pull && ./install.sh

set -euo pipefail

DOTFILES_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
log() { printf '  [dotfiles] %s\n' "$*"; }

log "installing from $DOTFILES_DIR"

# ---------------------------------------------------------------------------
# 1. Global Claude Code preferences  (~/.claude/CLAUDE.md)
#
# This is your ACCOUNT-LEVEL agent memory: preferences that apply to EVERY
# project you open (tone, language, general working style). It is distinct from
# a project's own AGENTS.md / CLAUDE.md, which carries project-specific memory
# and always takes precedence for that project.
#
# We symlink so edits in the repo are reflected live; push to persist.
# ---------------------------------------------------------------------------
if [ -f "$DOTFILES_DIR/claude/CLAUDE.md" ]; then
  mkdir -p "$HOME/.claude"
  # Back up a pre-existing real file once (not our symlink) so we never clobber.
  if [ -e "$HOME/.claude/CLAUDE.md" ] && [ ! -L "$HOME/.claude/CLAUDE.md" ]; then
    mv "$HOME/.claude/CLAUDE.md" "$HOME/.claude/CLAUDE.md.pre-dotfiles.$(date +%s)"
    log "backed up existing ~/.claude/CLAUDE.md"
  fi
  ln -sfn "$DOTFILES_DIR/claude/CLAUDE.md" "$HOME/.claude/CLAUDE.md"
  log "linked ~/.claude/CLAUDE.md -> dotfiles"
fi

# ---------------------------------------------------------------------------
# 2. Shell aliases / functions  (sourced from ~/.bashrc)
#
# Idempotent: we add a single guarded `source` line, not duplicate blocks.
# ---------------------------------------------------------------------------
if [ -f "$DOTFILES_DIR/shell/aliases.sh" ]; then
  marker="# >>> ona-dotfiles aliases >>>"
  for rc in "$HOME/.bashrc" "$HOME/.zshrc"; do
    [ -e "$rc" ] || continue
    if ! grep -qF "$marker" "$rc" 2>/dev/null; then
      {
        echo ""
        echo "$marker"
        echo "[ -f \"$DOTFILES_DIR/shell/aliases.sh\" ] && source \"$DOTFILES_DIR/shell/aliases.sh\""
        echo "# <<< ona-dotfiles aliases <<<"
      } >> "$rc"
      log "hooked aliases into $(basename "$rc")"
    fi
  done
fi

# ---------------------------------------------------------------------------
# 3. Claude Code 权限安全网（用户级 → 对**每一个**项目生效）
#
# 背景（2026-08-07 用户要求）：技术执行类的 bash / edit 确认弹窗，正文是原始 shell 与
# diff，用户看不懂、每次只会点 yes——弹与不弹结果一样，只多一次打断。于是把总开关切到
# Bypass permissions（VS Code User 设置，跨项目跨容器）。**但放开确认权就必须补上安全网**，
# 否则是净损失：不弹窗 = 不知道 agent 干了什么。
#
# 三层，全部装在用户级，因此换项目、换容器都还在：
#   ① permissions.ask —— 20 条不可逆操作。官方明文保证 explicit ask rules 在
#      bypassPermissions 下**仍然强制弹窗**，是唯一有明文保证的确定层。
#   ② destructive-command-guard —— 危险命令弹窗正文换成**中文人话**（治「看不懂」）。
#   ③ session-change-digest —— 会话收尾**自动打印本轮改动清单**，把「逐次事前确认」
#      换成「一次性事后对账」。
#
# 注意：agent **不能**替用户开 Bypass 模式（Claude Code 安全分类器判为自我授权并硬拒，
# 且明文「用户同意也不解除」）。总开关必须用户自己在 VS Code 设置里切；这里只装安全网。
#
# 合并而非软链：~/.claude/settings.json 里有本机独有且含 PII 的 env（内网代理地址、
# 邮箱、user id），软链会把它整个换掉。merge_settings.py 只写声明过的键，幂等、
# 写前备份、原子替换，目标损坏时宁可报错也不覆盖。
# ---------------------------------------------------------------------------
if [ -d "$DOTFILES_DIR/claude/hooks" ]; then
  mkdir -p "$HOME/.claude/hooks"
  for hook in "$DOTFILES_DIR"/claude/hooks/*.py; do
    [ -e "$hook" ] || continue
    ln -sfn "$hook" "$HOME/.claude/hooks/$(basename "$hook")"
  done
  log "linked user-level Claude hooks -> dotfiles"
fi
# 3a. 先止血，再合并。
#
# 镜像自带的 /usr/local/bin/welcome-claude.sh 被 ~/.bashrc 无条件 source，它会用
# `jq ... > ~/.claude/settings.json` **整文件覆盖**掉我们下面合并进去的一切
# （2026-08-07 实测：同一天被清空至少三次，permissions/hooks 全部蒸发）。
# 所以顺序必须是「先把覆盖改成合并，再写入」，否则写了也白写。
# 补丁幂等、写前备份、bash -n 校验后才装、无 sudo 则 SKIP —— 全程 fail-soft。
if [ -f "$DOTFILES_DIR/claude/patch_welcome_claude.sh" ]; then
  bash "$DOTFILES_DIR/claude/patch_welcome_claude.sh" || log "welcome-claude patch skipped (non-fatal)"
fi
if [ -f "$DOTFILES_DIR/claude/merge_settings.py" ]; then
  # 失败绝不中断环境启动：安全网装不上顶多回到「照常弹窗」，那是安全的失败方向。
  python3 "$DOTFILES_DIR/claude/merge_settings.py" || log "merge_settings skipped (non-fatal)"
fi

# ---------------------------------------------------------------------------
# 3b. 跨项目 VS Code 扩展（vscode/extensions.txt）
#
# 为什么在这儿而不是各项目的 devcontainer.json：那是项目级，换个仓库就没了。
# 这里装的是「不管打开哪个项目我都想要」的那几个。两侧 server 都装——只装一侧
# 的话从另一侧连进来什么都看不到（实测：github PR 扩展只在网页版侧有）。
# fail-soft：装不上不影响环境启动。
# ---------------------------------------------------------------------------
if [ -f "$DOTFILES_DIR/vscode/install_extensions.sh" ]; then
  bash "$DOTFILES_DIR/vscode/install_extensions.sh" || log "vscode extensions skipped (non-fatal)"
fi

# ---------------------------------------------------------------------------
# 3c. 窗口标题栏带上当前 Ona 环境名（vscode/set_window_title.sh）
#
# 多窗口时任务栏里各个 VS Code 长得一样，分不清哪个是哪个环境。标题栏写上环境名
# 就能一眼认出。为什么不直接写进 User 设置：那层不怕重建，但环境名只能写死，
# 在别的环境里会顶着错名字。本脚本每次现查一次再写，两个问题一起解决。
# fail-soft：拿不到名字就退化成不带环境名的模板，不阻塞启动。
# ---------------------------------------------------------------------------
if [ -f "$DOTFILES_DIR/vscode/set_window_title.sh" ]; then
  bash "$DOTFILES_DIR/vscode/set_window_title.sh" || log "window title skipped (non-fatal)"
fi

# ---------------------------------------------------------------------------
# 4. Git convenience config (safe, non-secret, global)
# ---------------------------------------------------------------------------
git config --global pull.ff only 2>/dev/null || true
git config --global init.defaultBranch main 2>/dev/null || true
git config --global push.autoSetupRemote true 2>/dev/null || true
log "applied global git conveniences"

# ---------------------------------------------------------------------------
# 5. Optional CLI tools (commented out by default — keep startup fast).
#    Uncomment selectively if you decide you want them. Each block checks first
#    so re-runs are cheap, and failures never abort startup.
# ---------------------------------------------------------------------------
# if ! command -v fzf >/dev/null 2>&1; then
#   FZF_VERSION="0.60.3"
#   curl -fsSL "https://github.com/junegunn/fzf/releases/download/v${FZF_VERSION}/fzf-${FZF_VERSION}-linux_amd64.tar.gz" \
#     | tar xzf - -C /tmp \
#     && sudo mv /tmp/fzf /usr/local/bin/ \
#     && log "installed fzf ${FZF_VERSION}" || log "fzf install skipped (non-fatal)"
# fi

log "done"
