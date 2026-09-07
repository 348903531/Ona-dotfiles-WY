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

MODE="write"; ONLY_IF_KNOWN=0
for _arg in "$@"; do
  case "$_arg" in
    --check)          MODE="check" ;;
    --only-if-known)  ONLY_IF_KNOWN=1 ;;
  esac
done
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
elif [ "$MODE" = "check" ]; then
  # ── check 拿不到名字时必须【拒绝判断】，不能拿合成的退化 TITLE 去比对 ────────
  # 早先 TITLE 在分支之前就算好，于是 API 一抖动，--check 在**两个方向上都错**
  # （2026-09-07 审查实测）：盘上已经退化了 → 与合成的退化 TITLE 相等 → RC=0
  # 「已是目标值」= **假绿**，而 doctor 正是读这个退出码，于是标题明明是错的、
  # 体检却报健康；盘上是正确的带名字标题 → 判不等 → RC=1 = 假红。
  # 退 3 走本仓 SKIP 约定：SKIP 不是 PASS，也不是 FAIL。doctor 已配套识别 3。
  log "SKIP 拿不到环境名，无法判断标题对不对（SKIP 不是 PASS）"
  exit 3
elif [ "$ONLY_IF_KNOWN" = "1" ]; then
  # ── 自愈路径专用：拿不到名字 = 「**不知道**」，不是「答案是没有名字」──────
  # 差别在这里才致命。install.sh 那条路是**首次**写入，此前根本没有值，退化成
  # 不带环境名的模板是唯一合理答案，而且它在前台跑、那行 log 人看得见。
  # 自愈这条路相反：盘上通常已经有一个**写对了的**值，而 `ona environment get`
  # 是一次网络往返（实测 280–500ms，外加 timeout 10），API 抖动 / token 过期 /
  # 出口被挡任一情况都会让 ENV_NAME 变空。若此时照写退化版，就等于**用一次网络
  # 抖动把正确的标题抹掉**——而它 detach 在后台、输出全进 /dev/null，没有任何人
  # 任何时刻会知道。抖动期里每开一个终端抹一次，正是这套机制本来要根治的症状。
  # 所以：不知道就别动，保住上一次写对的值。（2026-09-07 审查实测提出）
  exit 0
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

  MODE="$MODE" NAME_KNOWN="$([ -n "$ENV_NAME" ] && echo 1 || echo 0)" \
  python3 - "$SETTINGS" "$TITLE" <<'PY'
import json, os, re, sys

path, title = sys.argv[1], sys.argv[2]
mode = os.environ.get("MODE", "write")
name_known = os.environ.get("NAME_KNOWN") == "1"
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

# ── 永不把「已经带环境名的标题」降级成不带名字的（最后一道，不依赖调用方）──────
# 上面 --only-if-known 那道是 opt-in 的，靠**每个调用方都记得传**。这条纪律在
# 本次改动里当场就失败过一次：flag 已加进脚本、aliases.sh 却还没传，审查者正好
# 在那个中间状态复现出了整条 bug（2026-09-07）。所以再补一道**结构上不可能绕过**
# 的：只要这次没拿到环境名、而盘上已有的标题里已经带着 [某某环境]，就拒绝覆盖。
# 谁调用、传没传参数，都降级不了。
if not name_known:
    existing = data.get("window.title") or ""
    if re.search(r"\[[^\]]+\]", existing):
        print("  [window-title] SKIP [%s] 拿不到环境名，但盘上已有带名字的标题——不降级" % label)
        sys.exit(0)

if data.get("window.title") == title:
    print("  [window-title] [%s] 已是目标值" % label)
    sys.exit(0)

if mode == "check":
    print("  [window-title] ❌ [%s] 不是目标值" % label)
    sys.exit(1)

data["window.title"] = title
# temp 名必须带 PID，不能是固定的 path + ".tmp"（2026-09-07 审查实测复现）：
# 自愈挂到每次开 shell 之后，两个 shell 同时起就有两个进程写同一个 settings.json。
# 固定名会让它们抢同一个 inode——A 先 os.replace 让该 inode 成为 settings.json，
# B 的 fd 仍指着它、从偏移 0 继续写；B 的内容若更短（能不能拿到环境名会让标题
# 差 34 字符），落盘就是「B 的 JSON + A 的尾巴」= 非法 JSON。
# 后果不是标题错了这么轻：VS Code 对读不出的 Machine settings 是**整份丢弃**，
# 用户手勾的 claudeCode.allowDangerouslySkipPermissions 跟着失效（症状是
# 「Bypass 选项从菜单里消失了」，哪儿都不报错），而且此后本脚本每次都判
# 「不是干净 JSON」而 SKIP —— 永不自愈、也永不出声。
tmp = "%s.tmp.%d" % (path, os.getpid())
os.makedirs(os.path.dirname(path), exist_ok=True)
try:
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)      # 原子替换，中途失败不留半个 JSON
except Exception:
    # 写失败别把 .tmp.<pid> 垃圾留在 Machine 目录里
    try:
        os.unlink(tmp)
    except OSError:
        pass
    raise
print("  [window-title] ✅ [%s] window.title -> %s" % (label, title))
PY
  [ $? -ne 0 ] && RC=1
done

[ "$TOUCHED" = "0" ] && log "SKIP 没找到任何 VS Code server 目录（纯 SSH / CI）"
exit $RC
