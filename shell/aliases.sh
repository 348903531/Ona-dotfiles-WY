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

# --- navigation ---
alias ll='ls -alh'
alias ..='cd ..'
alias ...='cd ../..'

# --- safety ---
alias rm='rm -i'
alias cp='cp -i'
alias mv='mv -i'
