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
# 3. Git convenience config (safe, non-secret, global)
# ---------------------------------------------------------------------------
git config --global pull.ff only 2>/dev/null || true
git config --global init.defaultBranch main 2>/dev/null || true
git config --global push.autoSetupRemote true 2>/dev/null || true
log "applied global git conveniences"

# ---------------------------------------------------------------------------
# 4. Optional CLI tools (commented out by default — keep startup fast).
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
