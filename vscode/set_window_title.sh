#!/usr/bin/env bash
# 让 VS Code 标题栏显示「仓库名 - [当前 Ona 环境名] - 分支 - 当前文件」
#
# 为什么要有这个脚本（而不是直接把标题写进 User 设置）：
#   · 写进 Remote/Machine 设置  → 名字对，但容器重建即丢
#   · 写进 User 设置（本机）     → 不怕重建，但环境名只能写死；
#                                 在别的环境里会顶着错名字（EU-CENTRAL 显示 US-WEST）
#   · 本脚本                    → 每次环境启动现查一次名字再写，两个问题一起解决
#
# 由 install.sh 调用；Ona 每个新环境都会 clone dotfiles 并跑 install.sh，
# 所以设置被容器重建冲掉后会自动长回来，且长回来的是那个环境自己的名字。
#
# ── 判据（什么该用这套、什么不该）───────────────────────────────────────────
# 只有**值本身跟环境走**的设置才适合这么干（环境名、区域、容器内路径）。
# **跟人走的静态偏好**（右侧栏默认隐藏、主题、键位、claudeCode.* 开关）
# 应该留在 VS Code **User** 层，别搬进来——两个理由：
#   ① 本脚本只在 **Ona 环境**里跑。你在本机直接打开一个文件夹、连非 Ona 的远程，
#      它根本不执行，那些地方就没有你的偏好；User 层才覆盖得到。
#   ② 它写的是 **Machine 层，优先级高于 User**。往这里写一个键 = 永久制造一个
#      覆盖层，以后你在 User 里改同一个键会「改了不生效」，极难排查。
# 换句话说：这里每多写一个键，就多欠一笔以后要还的排查债。只放非动态不可的。
#
# ── 两个 server 目录都要写（与 install_extensions.sh 同一个坑）─────────────
#   ~/.vscode-server/data/Machine/settings.json          桌面 VS Code 走 Remote
#   ~/.vscode-browser-server/data/Machine/settings.json  Ona 网页版
# 只写一侧的话，从另一侧连进来标题栏就是默认的（2026-08-07 实测两侧都存在）。
#
# 非远程环境 / 拿不到环境名时，退化成不带环境名的模板，不报错、不阻塞。
#
# 用法：
#   bash set_window_title.sh          # 写入（install.sh 调用）
#   bash set_window_title.sh --check  # 只查不写，任一侧不是目标值即退 1

set -uo pipefail

MODE="write"; [ "${1:-}" = "--check" ] && MODE="check"
log() { printf '  [window-title] %s\n' "$*"; }

# ── 现查当前环境名 ─────────────────────────────────────────────────────────
# 加 timeout：install.sh 的设计约束是「快、非交互」，CLI 万一挂起会拖住整个
# 环境启动。拿不到名字不是错误，退化即可。
ENV_NAME=""
if command -v ona >/dev/null 2>&1; then
  ENV_NAME="$(timeout 10 ona environment get -f name 2>/dev/null | head -1 | tr -d '\r')"
fi

if [ -n "$ENV_NAME" ]; then
  TITLE='${dirty}${rootNameShort}${separator}['"$ENV_NAME"']${separator}${activeRepositoryBranchName}${separator}${activeEditorShort}'
else
  TITLE='${dirty}${rootNameShort}${separator}${activeRepositoryBranchName}${separator}${activeEditorShort}'
  log "拿不到环境名（非 Ona 环境 / CLI 不可用），退化成不带环境名的标题"
fi

TARGETS=(
  "$HOME/.vscode-server/data/Machine/settings.json"
  "$HOME/.vscode-browser-server/data/Machine/settings.json"
)

RC=0; TOUCHED=0
for SETTINGS in "${TARGETS[@]}"; do
  # 只写「这一侧的 server 确实存在」的目录。不存在就跳过——凭空造一个
  # .vscode-browser-server 目录没有意义，还会误导 doctor 以为该侧在用。
  SERVER_ROOT="$(dirname "$(dirname "$SETTINGS")")"
  [ -d "$SERVER_ROOT" ] || continue
  TOUCHED=1

  MODE="$MODE" python3 - "$SETTINGS" "$TITLE" <<'PY'
import json, os, sys

path, title = sys.argv[1], sys.argv[2]
mode = os.environ.get("MODE", "write")
label = "桌面Remote" if ".vscode-server" in path else "网页版"

data = {}
if os.path.isfile(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        # 文件在但不是干净 JSON（可能带注释）——不冒险覆盖。
        # 但**必须出声**：静默退出会让「没生效」查不出原因（fail-soft ≠ fail-silent）。
        print("  [window-title] SKIP [%s] %s 不是干净 JSON，未改动" % (label, path))
        sys.exit(0)

if data.get("window.title") == title:
    print("  [window-title] [%s] 已是目标值" % label)
    sys.exit(0)

if mode == "check":
    print("  [window-title] ❌ [%s] 不是目标值" % label)
    sys.exit(1)

data["window.title"] = title
tmp = path + ".tmp"
os.makedirs(os.path.dirname(path), exist_ok=True)
with open(tmp, "w", encoding="utf-8") as f:
    json.dump(data, f, indent=4, ensure_ascii=False)
    f.write("\n")
os.replace(tmp, path)          # 原子替换，中途失败不留半个 JSON
print("  [window-title] ✅ [%s] window.title -> %s" % (label, title))
PY
  [ $? -ne 0 ] && RC=1
done

[ "$TOUCHED" = "0" ] && log "SKIP 没找到任何 VS Code server 目录（纯 SSH / CI）"
exit $RC
