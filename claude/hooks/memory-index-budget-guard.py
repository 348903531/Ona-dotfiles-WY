#!/usr/bin/env python3
r"""memory-index-budget-guard — memory 索引涨到什么程度了？没有刹车就会无声涨下去。

为什么有它（2026-08-29 实测发现的缺口）
========================================
文件式 memory 的加载是**两层**，这一点决定了成本结构：

    第一层 · 索引 MEMORY.md   每条一行  → 每次会话【全量注入】，固定开销
    第二层 · 正文 <name>.md   完整事实  → 只在 agent 判断相关时才单独 Read

所以真正的常驻成本**只有索引**。正文再多也不自动进 context。
但索引是**只增不减**的：每沉淀一条就加一行，没有任何东西在盯着它涨到哪了。

实测（本机 WY-workspace-P，2026-08-29）：115 条 memory，索引 **15,029 codepoints**
（≈1.4 万 token，每次会话都读一遍）。按这个写法外推，1000 条 ≈ 13 万 token 常驻，
而其中绝大多数与当天任务无关——那时候「加了也白加」就从个例变成常态。

同族参照：本仓另一套 lessons-index 规模 159 条、注入预算 9000 字符，实测
**只进 45 条**，注入报文末尾自己写着「有 35 条无兜底教训也没塞进来」。
**那 35 条就是「白加」真实发生的样子。** memory 这套还没有预算机制，
所以它不会像 lessons-index 那样「挤不进来」，而是**全都挤进来、悄悄吃掉上下文**。

判据为什么可机械化
------------------
「索引有多大」是纯数数，零语义判断、零误报可能。属 AGENTS.md 卡#18 里
「有稳定可判信号」的那一档——能做成闸门就别只写散文。

⚠️ 口径：必须数 **codepoints**（`len(text)`），不是字节。
中文 1 字 = 3 字节，拿 `wc -c` 的数去比字符阈值会虚高约 2 倍
（本机实测同一文件：27,095 字节 vs 15,029 codepoints）。
**本 hook 的作者第一次口述这个数时就踩了这个坑**，把字节说成了字符、
据此判断「已超预算」，实际还有三成余量——所以这里写死用 len()，
并在报文里显式标注单位。

阈值怎么定的（刻意不设成「当前值」）
------------------------------------
设 20,000 codepoints。当前 15,029 → **今天是绿的**，还能再加约 40 条才响。

这是刻意的：闸门一挂上就恒红，人会立刻学会忽略它——本仓已有一条教训
（「恒定假红的测试会拦死整个 skill」）记的正是这个。**刹车的意义不是现在就停车，
是别让它无声地涨下去。**

超了之后该做什么（报文会说，这里记原因）
----------------------------------------
两条出路，先做便宜的：
  ① 压缩最长的那几行索引 —— 索引行的职责只是「什么情况下你需要我」，
     不是摘要正文。超过 ~120 codepoints 的行基本都能压。
  ② 分区加载 —— 给每条打 domain 标签，按当天任务只注入相关那批
     （lessons-index + task-rule-injector 已是这个形态，可照抄）。
  ③ 退役 —— 只清 project 类里已被证伪/已过期的；feedback 与 user 类
     **不因年龄退役**（工作方式的要求越老越可能是对的，它老恰恰因为写对了）。

诚实边界
--------
- 只量索引大小，**判不了「哪条该删」**——那是语义判断，机器做必错。
- 只报告、不阻断（SessionStart 硬拦会卡死会话启动）。
- 数的是 codepoints，与真实 token 数不完全相等（中文≈1:1，英文≈1:0.3），
  作为**趋势指标**足够，不当精确账单用。

第二个预算：每个分域子索引对 loader 的 MAX_DOMAIN_CP（2026-09-08 补）
--------------------------------------------------------------------
上面那个预算只管**常驻的 MEMORY.md**。分域之后冒出第二个预算，而它一直没人查：
`memory-domain-loader` 注入某个域时，若该域索引行的总长超过 `MAX_DOMAIN_CP`，
就**从尾部截断**并附一句「…还有 N 条没列」。**而新教训一律追加在索引末尾**——
于是「最新写的那几条，生下来就注不进上下文」。写了、进了 git、索引也有，
就是永远不会在做那类活时出现在眼前。

2026-09-08 本机实测（WY-workspace-P，喂真实 prompt 给 loader 数注入条数）：

    _index_ppt.md         34 条 → 只注入 30 条，尾部 4 条被丢
                                   （含当天刚 commit 的「做 PPT 前先查引擎装没装」）
    _index_gatecraft.md   40 条 → 只注入 28 条，丢 12 条
    _index_medical.md     31 条 → 只注入 28 条，丢 3 条

**这道自查此前报的是假绿**：MEMORY.md 3,320 cp 远低于 20,000，`--check` 一路 ✅，
而三个域每天都在丢条目。缺的不是阈值，是**根本没查这一层**。

⚠️ 阈值**从 loader 源码 import**，绝不在这里抄一份 4000——同一个常数两份必漂移
（本仓已有一条教训记的正是这个）。loader 找不到时整道检查静默让位，
**刻意不设兜底默认值**：没有权威阈值时报绿报红都是编的。

⚠️ 超了之后**别直接调大 `MAX_DOMAIN_CP`**：它跨所有项目生效，调大 = 给每个项目
每一轮都加上下文，属**取舍不属 bug 修复**，该由用户定。先做便宜的：压索引行、
或把过大的域拆成两个。

用法
----
  SessionStart hook：读 stdin JSON，超阈值则注入 additionalContext，永远 exit 0
  python3 memory-index-budget-guard.py --check     # 超阈值真的 exit 1，给 CI/doctor 用
  python3 memory-index-budget-guard.py --selftest  # 含阴性对照
  MEMORY_INDEX_BUDGET_GUARD_SKIP=1                 # 逃生阀

落点：`~/dotfiles/claude/hooks/`（层④跨项目）——任何项目的 memory 索引都会涨，
不是本仓专属。项目级若有同名 hook 则静默让位，不重复报。
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

# 索引预算（codepoints，不是字节——见 docstring 的口径警告）。
# 当前基线 15,029（115 条）；设 20,000 留约 40 条余量，保证挂上去当天是绿的。
BUDGET_CP = 20_000

# 索引行超过这个长度就点名为「可压缩」。120 cp 约等于一句话 + 一个钩子，够用了。
LONG_LINE_CP = 120

# 报文里最多点名几条最长的行——列太多没人看，5 条足够指出方向。
TOP_N = 5

ESCAPE_ENV = "MEMORY_INDEX_BUDGET_GUARD_SKIP"

# 项目级同名 hook 存在时让位，避免同一件事报两遍（dotfiles 用户级 hook 的既有约定）。
PROJECT_HOOK_NAMES = ("memory-index-budget-guard.py",)


def _project_hook_present(cwd: Path) -> bool:
    for name in PROJECT_HOOK_NAMES:
        if (cwd / ".claude" / "hooks" / name).is_file():
            return True
    return False


def slug_for(cwd: Path) -> str:
    """Claude Code 的项目目录名规则：绝对路径里的 '/' 换成 '-'。

    /workspaces/WY-workspace-P  ->  -workspaces-WY-workspace-P
    """
    return str(cwd).replace("/", "-")


def index_path_for(cwd: Path, home: Path | None = None) -> Path:
    home = home or Path.home()
    slug_path = home / ".claude" / "projects" / slug_for(cwd) / "memory" / "MEMORY.md"
    if slug_path.exists():
        return slug_path
    # 兜底：projects 目录是按 cwd 的**全路径**映射的，所以在 git worktree 里
    # （cwd = /tmp/wt-xxx）永远映射不到任何东西，整道自查静默 SKIP——而 worktree
    # 恰恰是改 memory 最常用的地方。回落到仓库内的 .claude/memory/。
    # 只在 slug 路径不存在时才用，不改变主工作区的既有行为。
    local = cwd / ".claude" / "memory" / "MEMORY.md"
    if local.exists():
        return local
    return slug_path


# ── 第二个预算：每个分域子索引 vs loader 的 MAX_DOMAIN_CP ──────────────────
# 阈值只有一个权威来源：memory-domain-loader.py 自己。这里**只 import、不抄**。
LOADER_NAMES = ("memory-domain-loader.py",)


def _load_loader():
    """把同目录（或 ~/.claude/hooks/）的 loader 当模块加载，只为拿它的常量。

    文件名带连字符，`import` 语法用不了，故走 importlib 按路径加载。
    加载失败 → 返回 None，本节检查整体让位（**不设默认阈值**：
    没有权威值时，报绿报红都是编的）。
    """
    import importlib.util

    here = Path(__file__).resolve().parent
    for cand in [here / n for n in LOADER_NAMES] + \
                [Path.home() / ".claude" / "hooks" / n for n in LOADER_NAMES]:
        if not cand.is_file():
            continue
        try:
            spec = importlib.util.spec_from_file_location("_mem_domain_loader", cand)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)          # 顶层只有常量与函数定义
            if isinstance(getattr(mod, "MAX_DOMAIN_CP", None), int):
                return mod
        except Exception:
            continue
    return None


def _index_rows(text: str) -> list[str]:
    """与 loader.build_block 逐字相同的取行方式（改一处要同步改另一处）。"""
    return [ln for ln in text.splitlines() if ln.strip().startswith("- [")]


def _would_drop(rows: list[str], budget: int) -> list[str]:
    """复刻 loader.build_block 的截断循环，算出**哪几条注不进来**。

    注意它按 len(row) 累加、**不算换行符**——照抄，别"顺手修正"，
    否则这里报的条数和实际被丢的条数对不上，比不报还糟。
    """
    keep: list[str] = []
    dropped: list[str] = []
    for r in rows:
        if sum(len(x) for x in keep) + len(r) > budget:
            dropped.append(r)
        else:
            keep.append(r)
    return dropped


def check_domain_budgets(mem_dir: Path) -> list[str]:
    """每个 `_index_<域>.md` 会不会被 loader 截掉尾巴。没启用分域 → 完全静默。"""
    conf = mem_dir / "domains.json"
    if not conf.is_file():
        return []
    loader = _load_loader()
    if loader is None:
        return []                        # 拿不到权威阈值 → 让位，不编一个
    budget = loader.MAX_DOMAIN_CP

    try:
        doms = json.loads(conf.read_text(encoding="utf-8")).get("domains", [])
    except Exception:
        return []

    problems = []
    for d in doms:
        if d.get("resident"):
            continue                     # 常驻域走 MEMORY.md 那条预算，不在这
        did = d.get("id")
        f = mem_dir / f"_index_{did}.md"
        try:
            rows = _index_rows(f.read_text(encoding="utf-8"))
        except OSError:
            continue
        total = sum(len(r) for r in rows)
        if total <= budget:
            continue
        dropped = _would_drop(rows, budget)
        names = []
        for r in dropped[:3]:
            m = re.search(r"^-\s*\[([^\]]+)\]", r.strip())
            names.append(m.group(1) if m else r.strip()[:24])
        problems.append(
            f"**DOMAIN_INDEX_OVER_BUDGET · `{did}`**："
            f"_index_{did}.md 共 {len(rows)} 条 / {total:,} cp，"
            f"超 loader 的 MAX_DOMAIN_CP={budget:,}，"
            f"做这类任务时**最后 {len(dropped)} 条注不进上下文**"
            + ("（" + "、".join(names) + ("…" if len(dropped) > 3 else "") + "）" if names else "")
        )

    if problems:
        problems.append(
            "→ loader 是**从尾部截断**的，而新教训一律追加在末尾——"
            "所以被丢的永远是最新写的那几条。"
            "先做便宜的：压缩该域最长的几行索引（索引行只是「什么情况下你需要我」，"
            "不是摘要正文），或把过大的域拆成两个。"
            "**别直接调大 MAX_DOMAIN_CP**——它跨所有项目生效，属取舍、该用户定。"
        )
    return problems


def measure(idx: Path) -> dict | None:
    """量索引。文件不存在/读不了 → None（fail-open，静默）。"""
    try:
        text = idx.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None

    lines = [ln.rstrip("\n") for ln in text.splitlines()]
    entries = [ln for ln in lines if ln.lstrip().startswith("- ")]
    long_lines = sorted(
        (ln for ln in entries if len(ln) > LONG_LINE_CP),
        key=len,
        reverse=True,
    )
    return {
        "codepoints": len(text),          # ← len()，不是 os.path.getsize()
        "entries": len(entries),
        "long_lines": long_lines,
        "over": len(text) > BUDGET_CP,
    }


def check_partitioning(mem_dir: Path) -> list[str]:
    """分域腐化检查：只在项目启用了分域（有 domains.json）时才查。

    为什么要有它（2026-08-29 当场撞见）
    ------------------------------------
    分域刚做完、还没提交，**另一个会话就按旧规则往 MEMORY.md 末尾追加了一条新
    memory**——没有 domain、不属于任何域。它功能上还能被读到，但每多一条这样的，
    常驻索引就长回去一点；半年后就腐化回平铺，81% 的收益一点点漏光。

    腐化是**渐进且无声**的，正是最该做成机械检查的那类：判据完全确定
    （有没有 domain 字段 / 在不在索引里 / 是不是非 always 却写进了 MEMORY.md），
    零语义判断、零误报可能。

    刻意只报三件事，且只在真出问题时开口（没启用分域的项目完全静默）。
    """
    conf = mem_dir / "domains.json"
    if not conf.is_file():
        return []                        # 该项目没启用分域 → 不是它的规矩，不管
    try:
        import json as _json
        doms = _json.loads(conf.read_text(encoding="utf-8")).get("domains", [])
        resident = {d["id"] for d in doms if d.get("resident")}
        valid = {d["id"] for d in doms}
    except Exception:
        return []

    # 2026-09-08 修一个**先于本次改动就存在**的假红：排除表里只有 `_README`，
    # 于是普通的 `README.md`（说明文档，不是 memory 条目）被当成 fact，
    # 恒报「缺 domain + 不在任何索引里」——本脚本自己的 selftest 里那条
    # 「阴性·README 与 _index_ 不当 fact」一直是红的（20 过 1 红）。
    # 恒定假红的测试最后的下场是被整体忽略，所以顺手修掉。
    facts = [p for p in mem_dir.glob("*.md")
             if p.stem not in ("MEMORY", "_README", "README")
             and not p.stem.startswith("_index_")]

    dom_of: dict[str, str | None] = {}
    for p in facts:
        try:
            m = re.search(r"^\s+domain:\s*(\S+)\s*$", p.read_text(encoding="utf-8"), re.M)
        except OSError:
            continue
        dom_of[p.stem] = m.group(1) if m else None

    problems = []

    # ① 缺 domain / 域名写错 —— 新加的 memory 最常见的两种漏
    no_dom = [s for s, d in dom_of.items() if d is None]
    bad_dom = [s for s, d in dom_of.items() if d is not None and d not in valid]
    if no_dom:
        problems.append(
            f"**{len(no_dom)} 条 memory 缺 `domain`**（新加的忘了写？）："
            + "、".join(sorted(no_dom)[:4]) + ("…" if len(no_dom) > 4 else "")
        )
    if bad_dom:
        problems.append(
            f"**{len(bad_dom)} 条 memory 的 domain 不在 domains.json 里**："
            + "、".join(sorted(bad_dom)[:4])
        )

    # ② 非 always 域却写进了 MEMORY.md —— 这就是「按旧规则往末尾追加」的指纹
    try:
        top = (mem_dir / "MEMORY.md").read_text(encoding="utf-8")
    except OSError:
        top = ""
    misplaced = []
    for ln in top.splitlines():
        m = re.match(r"^-\s*\[[^\]]+\]\(([^)/]+)\.md\)", ln.strip())
        if m:
            stem = m.group(1)
            d = dom_of.get(stem)
            if d is not None and d not in resident:
                misplaced.append(f"{stem}（应在 _index_{d}.md）")
            elif stem not in dom_of:
                misplaced.append(f"{stem}（索引里有、盘上没有）")
    if misplaced:
        problems.append(
            f"**{len(misplaced)} 条本该在分域索引里，却写进了常驻的 MEMORY.md**："
            + "、".join(misplaced[:4]) + ("…" if len(misplaced) > 4 else "")
        )

    # ③ 在盘不在任何索引 —— 写了文件忘了加索引行，等于没写
    indexed: set[str] = set()
    for f in [mem_dir / "MEMORY.md"] + sorted(mem_dir.glob("_index_*.md")):
        try:
            for ln in f.read_text(encoding="utf-8").splitlines():
                m = re.match(r"^-\s*\[[^\]]+\]\(([^)/]+)\.md\)", ln.strip())
                if m:
                    indexed.add(m.group(1))
        except OSError:
            pass
    orphans = sorted(set(dom_of) - indexed)
    if orphans:
        problems.append(
            f"**{len(orphans)} 条 memory 不在任何索引里**（写了文件忘了加索引行 = 永远不会被读到）："
            + "、".join(orphans[:4]) + ("…" if len(orphans) > 4 else "")
        )

    if problems:
        problems.append(
            "→ 一条命令修：`python3 .claude/memory/scripts/rebuild_indexes.py`"
            "（幂等、自带对账；缺 domain 的要先手写 `metadata.domain`）。"
            "规矩见 `.claude/memory/README.md`。"
        )
    return problems


def build_message(m: dict) -> str:
    cp, n = m["codepoints"], m["entries"]
    pct = cp * 100 // BUDGET_CP
    head = (
        f"【memory 索引超预算】MEMORY.md 现 **{cp:,} codepoints**（{n} 条条目），"
        f"超出 {BUDGET_CP:,} 的预算，已达 {pct}%。\n"
        f"这段是**每次会话全量注入**的固定开销——它涨，每一轮对话都跟着变贵，"
        f"而其中大多数条目与当天任务无关。"
    )

    body = [head, "", "**先做便宜的那一步**："]
    longs = m["long_lines"]
    if longs:
        body.append(
            f"① 压缩最长的索引行（>{LONG_LINE_CP} cp 的有 {len(longs)} 条）。"
            f"索引行的职责只是「什么情况下你需要我」，不是摘要正文："
        )
        for ln in longs[:TOP_N]:
            body.append(f"   · {len(ln)} cp — {ln[:70]}…")
    body += [
        "② 分区加载：给每条打 domain 标签，按当天任务只注入相关那批"
        "（lessons-index + task-rule-injector 已是这个形态，可照抄）。",
        "③ 退役：**只清 project 类**里已被证伪/已过期的。"
        "feedback 与 user 类不因年龄退役——工作方式的要求越老越可能是对的。",
        "",
        f"（判不了「哪条该删」，那是语义判断；本 hook 只负责在它无声涨下去时喊一声。"
        f"逃生阀 `{ESCAPE_ENV}=1`。）",
    ]
    return "\n".join(body)


def _resolve_cwd(payload: dict | None = None) -> Path:
    if payload:
        for key in ("cwd", "project_dir", "projectDir"):
            v = payload.get(key)
            if v:
                return Path(v)
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    return Path(env) if env else Path.cwd()


def run_hook() -> int:
    if os.environ.get(ESCAPE_ENV):
        return 0
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except Exception:
        payload = {}

    try:
        cwd = _resolve_cwd(payload)
        if _project_hook_present(cwd):
            return 0                      # 项目级同名 hook 会报，这里让位
        idx = index_path_for(cwd)
        m = measure(idx)
        if not m:
            return 0                      # 该项目没有 memory 索引 → 静默
        msgs = []
        if m["over"]:
            msgs.append(build_message(m))
        rot = check_partitioning(idx.parent)
        if rot:
            msgs.append("【memory 分域腐化】\n" + "\n".join(f"· {r}" for r in rot))
        over = check_domain_budgets(idx.parent)
        if over:
            msgs.append("【分域索引超预算：最新那几条注不进来】\n"
                        + "\n".join(f"· {r}" for r in over))
        if not msgs:
            return 0                      # 都没问题 → 静默
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": "\n\n".join(msgs),
            }
        }))
    except Exception:
        return 0                          # fail-open：绝不因为这道自查卡住会话
    return 0


def run_check(cwd: Path | None = None) -> int:
    """给 doctor / CI 用：超预算真的 exit 1。"""
    cwd = cwd or _resolve_cwd()
    m = measure(index_path_for(cwd))
    if not m:
        print(f"⏭️  SKIP：{index_path_for(cwd)} 读不到（该项目没有 memory 索引）")
        return 0
    status = "🔴 OVER" if m["over"] else "✅ OK"
    print(
        f"{status}  MEMORY.md {m['codepoints']:,} cp / 预算 {BUDGET_CP:,} cp"
        f"  ·  {m['entries']} 条条目  ·  超长行 {len(m['long_lines'])} 条"
    )
    rc = 0
    if m["over"]:
        print()
        print(build_message(m))
        rc = 1
    mem_dir = index_path_for(cwd).parent
    rot = check_partitioning(mem_dir)
    if rot:
        print("\n🔴 分域腐化：")
        for r in rot:
            print(f"  · {r}")
        rc = 1
    elif (mem_dir / "domains.json").is_file():
        print("✅ OK  分域完整（每条都有合法 domain、都在索引里、常驻区只有 always）")

    # ── 第二个预算：每个分域子索引 vs loader 的 MAX_DOMAIN_CP ──────────
    if (mem_dir / "domains.json").is_file():
        loader = _load_loader()
        if loader is None:
            print("⏭️  SKIP  每域预算：找不到 memory-domain-loader.py，"
                  "拿不到权威阈值（刻意不在本脚本里抄一份——两份必漂移）")
        else:
            over = check_domain_budgets(mem_dir)
            if over:
                print(f"\n🔴 分域索引超预算（loader MAX_DOMAIN_CP={loader.MAX_DOMAIN_CP:,}）：")
                for r in over:
                    print(f"  · {r}")
                rc = 1
            else:
                print(f"✅ OK  每域索引都在 loader 预算内"
                      f"（MAX_DOMAIN_CP={loader.MAX_DOMAIN_CP:,}，无条目被截断）")
    return rc


def _selftest() -> int:
    import json
    import tempfile

    passed, failed = [], []

    def check(name, got, want):
        (passed if got == want else failed).append(f"{name}（got={got!r} want={want!r}）")

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        proj = tmp / "workspaces" / "demo"
        proj.mkdir(parents=True)
        home = tmp / "home"
        mem = home / ".claude" / "projects" / slug_for(proj) / "memory"
        mem.mkdir(parents=True)
        idx = mem / "MEMORY.md"

        # ── 阴性对照：小索引必须静默 ────────────────────────────────
        idx.write_text("- [a](a.md) — 短钩子\n- [b](b.md) — 另一条\n", encoding="utf-8")
        m = measure(idx)
        check("阴性·小索引不报", m["over"], False)
        check("阴性·条目数数对", m["entries"], 2)
        check("阴性·无超长行", len(m["long_lines"]), 0)

        # ── 阴性对照：正好卡在预算上不报（边界是 > 不是 >=）──────────
        idx.write_text("x" * BUDGET_CP, encoding="utf-8")
        check("阴性·正好等于预算不报", measure(idx)["over"], False)

        # ── 阳性：超一个字符就报 ────────────────────────────────────
        idx.write_text("x" * (BUDGET_CP + 1), encoding="utf-8")
        check("阳性·超一个字符即报", measure(idx)["over"], True)

        # ── 口径：中文必须按 codepoints 数，不能按字节 ───────────────
        # 这是本 hook 存在的直接原因之一，钉死防回归。
        cjk = "汉" * 1000                                  # 1000 cp / 3000 bytes
        idx.write_text(cjk, encoding="utf-8")
        check("口径·中文按 codepoints", measure(idx)["codepoints"], 1000)
        check("口径·1000中文不该超2万预算", measure(idx)["over"], False)

        # ── 超长行点名 ──────────────────────────────────────────────
        idx.write_text(
            "- [short](s.md) — 短\n"
            + "- [long](l.md) — " + "长" * 200 + "\n",
            encoding="utf-8",
        )
        m = measure(idx)
        check("超长行·抓到 1 条", len(m["long_lines"]), 1)
        check("超长行·短行不误报", m["long_lines"][0].startswith("- [long]"), True)

        # ── 索引不存在 → None，绝不炸 ───────────────────────────────
        check("缺文件·返回 None", measure(mem / "nope.md"), None)

        # ── run_check 退出码：坏状态真的非 0 ────────────────────────
        idx.write_text("x" * (BUDGET_CP + 1), encoding="utf-8")
        real_home = os.environ.get("HOME")
        try:
            os.environ["HOME"] = str(home)
            check("run_check(OVER)→1", run_check(proj), 1)
            idx.write_text("- [a](a.md) — 短\n", encoding="utf-8")
            check("run_check(OK)→0", run_check(proj), 0)
        finally:
            if real_home is not None:
                os.environ["HOME"] = real_home

        # ── 分域腐化检查 ────────────────────────────────────────────
        # 阴性对照最要紧：没启用分域的项目必须完全静默，否则这道检查会在
        # 每一个没用分域的仓库里天天报错，人三天就学会忽略它。
        pdir = tmp / "pmem"
        pdir.mkdir()
        (pdir / "MEMORY.md").write_text("- [a](a.md) — x\n", encoding="utf-8")
        (pdir / "a.md").write_text("---\nmetadata:\n  type: feedback\n---\nx\n", encoding="utf-8")
        check("阴性·没有 domains.json 时完全静默", check_partitioning(pdir), [])

        (pdir / "domains.json").write_text(json.dumps({"domains": [
            {"id": "always", "resident": True}, {"id": "ppt"}, {"id": "git"},
        ]}), encoding="utf-8")
        # a.md 缺 domain → 该报
        r = check_partitioning(pdir)
        check("缺 domain 被抓到", any("缺 `domain`" in x for x in r), True)

        # 补上合法 domain=always 且在 MEMORY.md 里 → 干净
        (pdir / "a.md").write_text(
            "---\nmetadata:\n  type: feedback\n  domain: always\n---\nx\n", encoding="utf-8")
        check("阴性·always 在 MEMORY.md 里不报", check_partitioning(pdir), [])

        # 非 always 域却写在 MEMORY.md → 这是「按旧规则往末尾追加」的指纹
        (pdir / "b.md").write_text(
            "---\nmetadata:\n  type: feedback\n  domain: ppt\n---\nx\n", encoding="utf-8")
        (pdir / "MEMORY.md").write_text("- [a](a.md) — x\n- [b](b.md) — y\n", encoding="utf-8")
        r = check_partitioning(pdir)
        check("非 always 写进 MEMORY.md 被抓到",
              any("却写进了常驻的 MEMORY.md" in x for x in r), True)

        # 挪进分域索引 → 干净
        (pdir / "MEMORY.md").write_text("- [a](a.md) — x\n", encoding="utf-8")
        (pdir / "_index_ppt.md").write_text("- [b](b.md) — y\n", encoding="utf-8")
        check("阴性·挪进分域后不报", check_partitioning(pdir), [])

        # 写了文件但没进任何索引 → 等于没写
        (pdir / "c.md").write_text(
            "---\nmetadata:\n  type: feedback\n  domain: git\n---\nx\n", encoding="utf-8")
        r = check_partitioning(pdir)
        check("孤儿被抓到", any("不在任何索引里" in x for x in r), True)

        # 域名写错 → 该报
        (pdir / "c.md").write_text(
            "---\nmetadata:\n  type: feedback\n  domain: 打错的域\n---\nx\n", encoding="utf-8")
        (pdir / "_index_git.md").write_text("- [c](c.md) — z\n", encoding="utf-8")
        r = check_partitioning(pdir)
        check("非法 domain 被抓到", any("不在 domains.json 里" in x for x in r), True)

        # _index_*.md 与 README.md 本身不该被当成 fact 去查 domain
        (pdir / "README.md").write_text("# 说明\n", encoding="utf-8")
        (pdir / "c.md").write_text(
            "---\nmetadata:\n  type: feedback\n  domain: git\n---\nx\n", encoding="utf-8")
        check("阴性·README 与 _index_ 不当 fact", check_partitioning(pdir), [])

        # ── 每域预算（2026-09-08 新增）──────────────────────────────
        loader = _load_loader()
        check("能从 loader 拿到权威阈值（拿不到就整节让位）", loader is not None, True)
        if loader is not None:
            MAXD = loader.MAX_DOMAIN_CP
            check("阈值确实是 int 且 >0", MAXD > 0, True)

            bdir = tmp / "bmem"
            bdir.mkdir()
            (bdir / "MEMORY.md").write_text("- [a](a.md) — x\n", encoding="utf-8")

            # 阴性对照①：没有 domains.json 一律静默（没启用分域的项目天天报＝没人看）
            (bdir / "_index_ppt.md").write_text(
                "".join(f"- [t{i}](t{i}.md) — " + "长" * 200 + "\n" for i in range(30)),
                encoding="utf-8")
            check("阴性·无 domains.json 时不查每域预算", check_domain_budgets(bdir), [])

            (bdir / "domains.json").write_text(json.dumps({"domains": [
                {"id": "always", "resident": True}, {"id": "ppt"}, {"id": "git"},
            ]}), encoding="utf-8")

            # 阳性：30 × ~210 cp 远超预算 → 必红，且报文要带标识串与域名
            r = check_domain_budgets(bdir)
            check("阳性·超预算被抓到", bool(r), True)
            check("阳性·报文带标识串 DOMAIN_INDEX_OVER_BUDGET",
                  any("DOMAIN_INDEX_OVER_BUDGET" in x for x in r), True)
            check("阳性·点名到域", any("`ppt`" in x for x in r), True)

            # 报的「丢几条」必须与 loader 真实丢的条数一致——这才是本节的判别力。
            # 拿 loader 自己的 build_block 出来的**产物**反查，而不是信我这边的算术。
            #
            # ⚠️ 判据刻意数「真被注入了几行」，不去 regex 那句截断提示的措辞
            # （2026-09-08 踩到：提示语从「还有 N 条没列」改成「另 N 条这次没列」，
            # 我这边 regex 立刻 got=None、整条锚静默失效）。措辞是会变的，
            # 「注进去了几行」是行为、不会变——**对着行为写判据，别对着话术写**。
            rows = _index_rows((bdir / "_index_ppt.md").read_text(encoding="utf-8"))
            mine = len(_would_drop(rows, MAXD))
            doms_l = [{"id": "ppt", "label": "x", "keywords": ["ppt"]}]
            # loader 读的是 <cwd>/.claude/memory/，造一份同内容的给它
            lm = tmp / "lproj" / ".claude" / "memory"
            lm.mkdir(parents=True)
            (lm / "_index_ppt.md").write_text(
                (bdir / "_index_ppt.md").read_text(encoding="utf-8"), encoding="utf-8")
            blk = loader.build_block(tmp / "lproj", ["ppt"], doms_l) or ""
            listed = len(_index_rows(blk))
            check("丢的条数与 loader 实际截断一致", len(rows) - listed == mine, True)

            # 阴性对照②：正好卡在预算上不报（边界是 > 不是 >=）
            (bdir / "_index_git.md").write_text("- [x](x.md) — " + "长" * (MAXD - 14) + "\n",
                                                encoding="utf-8")
            check("阴性·git 域正好等于预算不报",
                  any("`git`" in x for x in check_domain_budgets(bdir)), False)

            # 阴性对照③：resident 域不参与（它走 MEMORY.md 那条预算）
            (bdir / "_index_always.md").write_text(
                "".join(f"- [a{i}](a{i}.md) — " + "长" * 200 + "\n" for i in range(30)),
                encoding="utf-8")
            check("阴性·resident 域不参与每域预算",
                  any("`always`" in x for x in check_domain_budgets(bdir)), False)

            # 阴性对照④：把超长的那个域压回预算内 → 完全静默（判别力：能分开好坏）
            (bdir / "_index_ppt.md").write_text("- [t](t.md) — 短\n", encoding="utf-8")
            check("阴性·压回预算内后完全静默", check_domain_budgets(bdir), [])

            # run_check 退出码：每域超预算也要真的 exit 1
            pj = tmp / "workspaces" / "bproj"
            (pj / ".claude").mkdir(parents=True)
            import shutil
            shutil.copytree(bdir, pj / ".claude" / "memory")
            (pj / ".claude" / "memory" / "_index_ppt.md").write_text(
                "".join(f"- [t{i}](t{i}.md) — " + "长" * 200 + "\n" for i in range(30)),
                encoding="utf-8")
            (pj / ".claude" / "memory" / "a.md").write_text(
                "---\nmetadata:\n  domain: always\n---\nx\n", encoding="utf-8")
            check("run_check(每域超预算)→1", run_check(pj), 1)

        # ── 项目级同名 hook 存在 → 让位 ─────────────────────────────
        (proj / ".claude" / "hooks").mkdir(parents=True)
        (proj / ".claude" / "hooks" / "memory-index-budget-guard.py").write_text("#", encoding="utf-8")
        check("让位·项目级 hook 在则不报", _project_hook_present(proj), True)

    for line in passed:
        print(f"  ✅ {line}")
    for line in failed:
        print(f"  ❌ {line}")
    if failed:
        print(f"\n❌ selftest 失败 {len(failed)}/{len(passed) + len(failed)}")
        return 1
    print(f"\n✅ selftest 全过（{len(passed)} 项，含 9 条阴性对照 + 2 条口径防回归"
          f" + 1 条「报的条数与 loader 实际截断一致」判别力锚）")
    return 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(_selftest())
    if "--check" in sys.argv:
        sys.exit(run_check())
    sys.exit(run_hook())
