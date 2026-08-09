#!/usr/bin/env python3
"""把 claude/settings-permissions.json 幂等合并进 ~/.claude/settings.json。

为什么需要一个合并脚本，而不是直接软链
=====================================
`~/.claude/settings.json` 里已经有**这台机器独有、且不能进 git 的东西**——
`env.ANTHROPIC_BASE_URL`（内网代理）、`env.ANTHROPIC_CUSTOM_HEADERS`（含本人邮箱、
Ona user id，属 PII）。软链会把它整个换掉，等于删掉这些。所以只能合并：
**只写我们声明的键，其余原样不动。**

幂等性（install.sh 每次开环境都会跑）
- `permissions.ask`：按条去重后并集，已有的不重复加、不删用户手工加的。
- `hooks.<event>`：按 `id` 去重。已存在同 id 的条目 → **覆盖**（让 dotfiles 成为这两个
  hook 的单一事实源，改了能生效）；其它 id 的条目原样保留。
- 从不删除任何我们没声明的键。

安全
- 写前备份一次（`.bak-<epoch>`，只在内容真的会变时备份，避免每次开机堆一堆备份）。
- 先写临时文件再原子替换，中途失败不会留下半个 JSON。
- 目标文件损坏（不是合法 JSON）→ 直接放弃并报错退 1，绝不用空对象覆盖掉它。

用法：python3 merge_settings.py [--dry-run] [--target ~/.claude/settings.json]
"""

import argparse
import io
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
FRAGMENT = os.path.join(HERE, "settings-permissions.json")
DEFAULT_TARGET = os.path.expanduser("~/.claude/settings.json")

# 顶层标量键：**只在缺失时写入，绝不覆盖**。
# 语义与 permissions.ask 的「并集」不同——这类键只有一个值，覆盖就等于替用户做主。
# 用户在 /config 里换了别的 output style，install.sh 下次重跑不该把他换回来。
# 想恢复我们的默认：把该键从 ~/.claude/settings.json 删掉，下次 install 会补回。
TOP_LEVEL_SCALARS = ("outputStyle",)

# `permissions` 下的标量键：同样**只在缺失时写入，绝不覆盖**。
# 目前只有 `defaultMode`（会话起步用哪种权限模式）。它是官方支持写进 settings 的
# 持久默认（文档 permission-modes：「Enable it at launch with
# permissions.defaultMode: "bypassPermissions" in settings」），所以「一次配置、
# 换项目换容器都还在」这件事**不需要**每次开会话手动切模式——早先仓库里记的
# 「必须每次启动时启用」是读错了，实际那句只是说不能在会话中途切进去。
# 缺失才写的理由同 outputStyle：用户自己在 /config 或设置里改过就该尊重；
# 想恢复默认，把这个键从 ~/.claude/settings.json 删掉，下次 install 会补回。
PERMISSION_SCALARS = ("defaultMode",)

# `sandbox` 下的标量键：同样**只在缺失时写入，绝不覆盖**。
# 为什么需要这一族（2026-08-09 实测定位）：Bash 沙箱是**独立于 permissions 的第三层**，
# `defaultMode: bypassPermissions` 完全管不到它。沙箱按操作系统边界放行命令——
# 读全盘 OK、**写只限当前工作目录**、**网络只限白名单域名**；越界的命令不是被拒，
# 而是回落到权限流程去**弹窗**。于是即便 bypass 已开、ask 只剩 11 条，
# `git fetch origin`（联网）、`… > /tmp/x`（写工作目录外）这类日常命令仍会逐条弹确认框。
# 用户 2026-08-08 已明确「弹窗我看不懂、只会点 yes，不如别问、事后对账」，
# 沙箱制造的弹窗与该决定直接冲突，且它拦的恰恰是最日常的两类操作，噪音极大。
# 关掉它的净风险增量约等于零：bypassPermissions 官方已声明对提示词注入零防护，
# 沙箱在这个组合下只剩「制造弹窗」的副作用；且本工作台是隔离 devcontainer。
# 想收紧回去：把 ~/.claude/settings.json 里 sandbox.enabled 改回 true（或删掉本键
# 再把片段里的值改成 true）——install.sh 是「缺失才写」，不会把你改回来。
SANDBOX_SCALARS = ("enabled",)


def _load(path, default):
    if not os.path.isfile(path):
        return default
    raw = io.open(path, encoding="utf-8").read().strip()
    if not raw:
        return default
    return json.loads(raw)          # 故意不 try：损坏就该炸，别静默覆盖


def merge(target, frag):
    """把 frag 合并进 target（原地改），返回是否有变化。"""
    changed = False

    # ── permissions.ask：并集去重，保留用户手工加的 ──────────────────────
    want_ask = (frag.get("permissions") or {}).get("ask") or []
    # 退役清单：**我们自己曾经发过、现在决定撤回**的条目，存在即移除。
    # 为什么必须有它：ask 用并集语义（刻意的，保护用户手工加的条目），所以「从片段里
    # 删掉一条」对已装过的环境毫无作用——那条会永远赖在 ~/.claude/settings.json 里继续
    # 弹窗，只有全新环境才干净。同型教训本仓 2026-08-05 记过：同步机制只会叠加、不会
    # 表达删除，用户删掉的东西会「又出来」。用户手工加的不在退役清单里，不受影响。
    retired = frag.get("_ask_retired") or []
    if want_ask or retired:
        perms = target.setdefault("permissions", {})
        have = perms.get("ask") or []
        kept = [a for a in have if a not in retired]
        merged = kept + [a for a in want_ask if a not in kept]
        if merged != have:
            perms["ask"] = merged
            changed = True
        # 说明性注释键：只在缺失时补，不覆盖用户改过的措辞
        if "_ask_note" in frag and "_ask_note" not in perms:
            perms["_ask_note"] = frag["_ask_note"]
            changed = True

    # ── permissions 下的标量键（defaultMode）：缺失才写，已有则尊重 ──────
    frag_perms = frag.get("permissions") or {}
    for key in PERMISSION_SCALARS:
        if key in frag_perms:
            perms = target.setdefault("permissions", {})
            if key not in perms:
                perms[key] = frag_perms[key]
                changed = True
    if "_defaultMode_note" in frag_perms:
        perms = target.setdefault("permissions", {})
        if "_defaultMode_note" not in perms:
            perms["_defaultMode_note"] = frag_perms["_defaultMode_note"]
            changed = True

    # ── sandbox 下的标量键（enabled）：缺失才写，已有则尊重 ──────────────
    frag_sbx = frag.get("sandbox") or {}
    for key in SANDBOX_SCALARS:
        if key in frag_sbx:
            sbx = target.setdefault("sandbox", {})
            if key not in sbx:
                sbx[key] = frag_sbx[key]
                changed = True
    if "_enabled_note" in frag_sbx:
        sbx = target.setdefault("sandbox", {})
        if "_enabled_note" not in sbx:
            sbx["_enabled_note"] = frag_sbx["_enabled_note"]
            changed = True

    # ── 顶层标量键（outputStyle）：缺失才写，已有则尊重用户的选择 ────────
    for key in TOP_LEVEL_SCALARS:
        if key in frag and key not in target:
            target[key] = frag[key]
            changed = True

    # ── hooks：按 id 去重，同 id 覆盖（dotfiles 是这两个 hook 的事实源）──
    for event, entries in (frag.get("hooks") or {}).items():
        bucket = target.setdefault("hooks", {}).setdefault(event, [])
        for entry in entries:
            eid = entry.get("id")
            idx = next((i for i, e in enumerate(bucket)
                        if isinstance(e, dict) and e.get("id") == eid), None)
            if idx is None:
                bucket.append(entry)
                changed = True
            elif bucket[idx] != entry:
                bucket[idx] = entry
                changed = True
    return changed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", default=DEFAULT_TARGET)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    target_path = os.path.expanduser(args.target)
    try:
        frag = _load(FRAGMENT, None)
    except Exception as e:
        print("  [merge_settings] 片段文件读不了，跳过：%s" % e, file=sys.stderr)
        return 1
    if not frag:
        print("  [merge_settings] 片段为空，跳过", file=sys.stderr)
        return 1

    try:
        target = _load(target_path, {})
    except Exception as e:
        print("  [merge_settings] ❌ %s 不是合法 JSON（%s）——放弃合并，"
              "绝不覆盖。请先手工修好它。" % (target_path, e), file=sys.stderr)
        return 1
    if not isinstance(target, dict):
        print("  [merge_settings] ❌ 目标不是 JSON 对象，放弃", file=sys.stderr)
        return 1

    before = json.dumps(target, ensure_ascii=False, sort_keys=True)
    changed = merge(target, frag)
    after = json.dumps(target, ensure_ascii=False, sort_keys=True)
    if not changed or before == after:
        print("  [merge_settings] 已是最新，无需改动")
        return 0

    n_ask = len((target.get("permissions") or {}).get("ask") or [])
    n_hooks = sum(len(v) for v in (target.get("hooks") or {}).values())
    if args.dry_run:
        print("  [merge_settings] （dry-run）将写入：ask %d 条 / hook 条目 %d 个"
              % (n_ask, n_hooks))
        return 0

    os.makedirs(os.path.dirname(target_path), exist_ok=True)
    if os.path.isfile(target_path):
        bak = "%s.bak-%d" % (target_path, int(time.time()))
        io.open(bak, "w", encoding="utf-8").write(
            io.open(target_path, encoding="utf-8").read())
    tmp = target_path + ".tmp"
    io.open(tmp, "w", encoding="utf-8").write(
        json.dumps(target, ensure_ascii=False, indent=2) + "\n")
    os.replace(tmp, target_path)     # 原子替换
    print("  [merge_settings] ✅ 已合并：ask %d 条 / hook 条目 %d 个 → %s"
          % (n_ask, n_hooks, target_path))
    return 0


# ── 自测：好输入要合对、坏输入要拒绝（没红过的绿 = 没测过）──────────────────
def _selftest():
    import tempfile
    ok = fail = 0

    def want(cond, desc):
        nonlocal ok, fail
        if cond:
            ok += 1
            print("  PASS: %s" % desc)
        else:
            fail += 1
            print("  FAIL: %s" % desc)

    frag = _load(FRAGMENT, {})
    # 条数**从片段现读**，不写死。写死的话每次增删 ask 条目都要记得同步改这里，
    # 而漏改的那次正好是「名单动了、测试却仍按老数字判绿」。同型教训 doctor.sh 已踩过一次
    # （待查 hook 名曾写死，加了第三个 hook 后体检仍只查那两个老的、却报全绿）。
    n_want = len((frag.get("permissions") or {}).get("ask") or [])

    # 1) 空目标 → 全量写入
    t = {}
    merge(t, frag)
    # 用 .get 取：2026-08-09 ask 清零后，合并结果里**根本不会再有 ask 键**
    # （merge 只在 merged != have 时才写，空数组合空数组无变化），硬取会 KeyError。
    want(len(t.get("permissions", {}).get("ask", [])) == n_want,
         "空目标：%d 条 ask 全部写入" % n_want)
    want("PreToolUse" in t["hooks"] and "Stop" in t["hooks"], "两个 hook 事件都写入")

    want(t.get("outputStyle") == "说人话", "空目标：outputStyle 写入")
    want(t["permissions"].get("defaultMode") == "bypassPermissions",
         "空目标：permissions.defaultMode 写入")

    # 2) 幂等：再合一次不应有变化
    want(merge(t, frag) is False, "幂等：重复合并无变化")

    # 2b) 顶层标量键**已存在**时绝不覆盖——用户在 /config 里换过风格就该尊重。
    #     这是与 permissions.ask「并集」相反的语义，必须单独验，不然改一行就悄悄退化成覆盖。
    t_style = {"outputStyle": "Explanatory"}
    changed_style = merge(t_style, frag)
    want(t_style["outputStyle"] == "Explanatory",
         "用户已选的 outputStyle 不被覆盖")
    want(changed_style is True, "同一次合并里其它键仍照常写入（不因跳过标量而整体短路）")

    # 2c) permissions.defaultMode **已存在**时同样绝不覆盖。与 ask 的「并集」语义相反，
    #     必须单独验：这是权限总开关，写成覆盖 = install.sh 每次开环境都替用户重设权限，
    #     用户主动收紧回 default/plan 会被静默改回去，属最不可接受的一类退化。
    t_mode = {"permissions": {"defaultMode": "plan"}}
    changed_mode = merge(t_mode, frag)
    want(t_mode["permissions"]["defaultMode"] == "plan",
         "用户已设的 permissions.defaultMode 不被覆盖")
    want(changed_mode is True, "跳过 defaultMode 后其它键仍照常写入")

    # 2d) sandbox.enabled：与 defaultMode 同族的第三层开关（Bash 沙箱），语义同样是
    #     「缺失才写、已有不覆盖」。必须单独验而不是信「反正走的是同一段循环」——
    #     它走的是**另一个**常量族（SANDBOX_SCALARS）和另一个 setdefault 分支，
    #     那段代码可以整个删掉而上面的测试全绿（本仓踩过：变异测试要验「把被测逻辑
    #     整个删掉会不会红」，不红说明用例根本没跑到那段）。
    want(t.get("sandbox", {}).get("enabled") is False,
         "空目标：sandbox.enabled 写入 false（关掉 Bash 沙箱，止住越界命令弹窗）")
    t_sbx = {"sandbox": {"enabled": True}}
    changed_sbx = merge(t_sbx, frag)
    want(t_sbx["sandbox"]["enabled"] is True,
         "用户已开的 sandbox.enabled 不被覆盖")
    want(changed_sbx is True, "跳过 sandbox.enabled 后其它键仍照常写入")

    # 3) 不碰已有的 env（最关键——那里有不能丢的内网代理与 PII）
    t2 = {"env": {"ANTHROPIC_BASE_URL": "http://proxy", "X": "keep me"}}
    merge(t2, frag)
    want(t2["env"] == {"ANTHROPIC_BASE_URL": "http://proxy", "X": "keep me"},
         "已有 env 原样保留，一个字节没动")

    # 4) 不删用户手工加的 ask 条目
    t3 = {"permissions": {"ask": ["Bash(my-own-danger:*)"]}}
    merge(t3, frag)
    want("Bash(my-own-danger:*)" in t3["permissions"]["ask"], "用户手工加的 ask 保留")

    # 4b) 退役条目：已装过的环境里必须**真的被删掉**，不能只对全新环境生效。
    #     这是本次最容易假绿的一条——并集语义下「从片段删一条」看起来生效了（新环境干净），
    #     实则老环境纹丝不动、继续弹窗。必须单独验。
    retired = frag.get("_ask_retired") or []
    want(len(retired) > 0, "片段声明了退役清单（否则下面两条测的是空集、恒真）")
    t_ret = {"permissions": {"ask": list(retired) + ["Bash(my-own-danger:*)"]}}
    merge(t_ret, frag)
    left = t_ret["permissions"]["ask"]
    want(not (set(retired) & set(left)), "退役条目在已装过的环境里被真的移除")
    want("Bash(my-own-danger:*)" in left, "移除退役条目时不误伤用户手工加的")
    want(len(t3["permissions"]["ask"]) == n_want + 1,
         "并集去重后 %d 条（片段 %d + 用户手工 1）" % (n_want + 1, n_want))

    # 5) 不动别的 hook（比如项目无关的用户级 hook）
    t4 = {"hooks": {"Stop": [{"id": "stop:my-own", "matcher": "*", "hooks": []}]}}
    merge(t4, frag)
    ids = [e.get("id") for e in t4["hooks"]["Stop"]]
    want("stop:my-own" in ids and "stop:session-change-digest" in ids,
         "别人的 hook 保留、我们的追加")

    # 6) 同 id 存在时覆盖（让 dotfiles 成为事实源）
    t5 = {"hooks": {"Stop": [{"id": "stop:session-change-digest", "old": True}]}}
    merge(t5, frag)
    want(t5["hooks"]["Stop"][0].get("old") is None, "同 id 被覆盖为最新定义")

    # 7) 坏输入：目标是损坏 JSON → 必须拒绝而不是覆盖
    fd, bad = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w") as fh:
        fh.write("{ this is not json ")
    rc = os.system("python3 %s --target %s >/dev/null 2>&1"
                   % (os.path.abspath(__file__), bad))
    want(rc != 0, "坏输入：损坏的 settings.json 被拒绝（退出码非 0），未被覆盖")
    want(io.open(bad).read() == "{ this is not json ", "坏输入：原文件一个字节没动")
    os.unlink(bad)

    print("\n%d/%d 通过" % (ok, ok + fail))
    return 1 if fail else 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(_selftest())
    sys.exit(main())
