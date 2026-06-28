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

# --- navigation ---
alias ll='ls -alh'
alias ..='cd ..'
alias ...='cd ../..'

# --- safety ---
alias rm='rm -i'
alias cp='cp -i'
alias mv='mv -i'
