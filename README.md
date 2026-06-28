# Ona dotfiles

个人**账号级**配置仓库。Ona 在每个新环境启动时自动 clone 本仓库到 `~/dotfiles`
并运行 `install.sh`,把我的跨项目个人习惯叠加到团队标准 Dev Container 之上——
让任何新对话环境、任何新项目都符合我的使用习惯,AI 也能读到我的全局偏好。

## 它做什么

`install.sh`(Ona 启动时自动跑,也可手动重跑)会:

1. **软链全局 Claude 偏好** — `claude/CLAUDE.md` → `~/.claude/CLAUDE.md`
   账号级 agent 记忆,对所有项目生效;项目自己的 `AGENTS.md` 优先级更高。
2. **挂载 shell 别名** — `shell/aliases.sh` 里的 git 快捷键等(幂等地 source 进 rc)。
3. **应用通用 git 配置** — `pull.ff=only`、`init.defaultBranch=main` 等安全默认。
4. **(可选)装 CLI 小工具** — 默认注释掉,想要时取消注释(保持启动快)。

## 目录结构

```
install.sh          # 入口脚本(Ona 自动执行)
claude/CLAUDE.md    # 账号级 Claude 全局偏好(软链到 ~/.claude/)
shell/aliases.sh    # 个人 shell 别名/函数
.gitignore          # 防止误传 secret
```

## 配置到 Ona

```bash
ona user dotfiles set --repository https://github.com/wangy548_roche/Ona-dotfiles.git
```

或 Web UI:**Settings → Preferences → Dotfiles repository** 填本仓库 URL。
私有仓库需先在 **Git authentications** 授权 GitHub 账号。

## 在运行中的环境里更新

推到本仓库的改动**只对新环境生效**。要在当前环境立即应用:

```bash
cd ~/dotfiles && git pull && ./install.sh
```

## 纪律

- **单向恢复**:dotfiles 只在启动时把配置读进环境。在环境里改了配置想留下,
  必须 `git commit && push` 回本仓库,否则环境重建即丢。习惯:**先 push 再让旧环境消失**。
- **绝不放 secret**:本仓库虽私有,也绝不硬编码 token/密码/密钥;用 Ona Secrets。
- **绝不放他人 PII**。
- **备份**:本仓库已纳入 `WY-workspace-P` 的 `upstream-sync` skill 镜像备份链,
  同步时会镜像到 `348903531/Ona-dotfiles-WY`。
