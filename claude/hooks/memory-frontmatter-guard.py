#!/usr/bin/env python3
r"""memory-frontmatter-guard — PreToolUse：新 memory 落盘前必须带 domain。

为什么有它（2026-08-29，从「这一条怎么办」改问「怎么让以后都不缺」）
======================================================================
memory 分域之后，**每条都必须有 `metadata.domain`**——没有它，那条就不属于任何
分域索引，`memory-domain-loader` 永远不会注入它，等于写了个永远不会被读到的文件。

真实经过：分域刚做完、PR 还没合，另一个会话就按旧规则写了一条没有 domain 的
memory。当时的处理是「不碰别人的文件，等检查提醒谁在场谁补」——**那是把问题
留在下游**。用户追问「你现在能不能解决」，逼出了正解：

    **别问「这一条怎么办」，问「怎么让以后所有条都不会缺」。**

下游有两道（开局的分域腐化检查 + health-check），它们只能**发现**已经发生的事；
本 hook 在**落盘那一刻**拦，让缺 domain 的 memory 根本写不出来。这是本仓
「补下游不如堵上游」的又一次落地。

为什么用 deny 而不是 ask
------------------------
按 AGENTS.md 卡#27：**agent 自己能修的用 deny，别弹给用户**。
「加一行 `domain: xxx`」纯技术执行，用户看了那个弹窗也只会点 yes ——
等于纯打断。deny 会把可用域列表直接给 agent，改完重试即可。

诚实边界
--------
- **只管新建**（Write 全文写入）。用 Edit 局部改一条已有 memory 时不查——
  那种场景 domain 早就在了，查它只会制造噪音。
- 判不了「这条该归哪个域」（语义判断），只判「有没有填」。
- 项目没有 `.claude/memory/domains.json`（= 没启用分域）→ 完全静默。

用法
----
  PreToolUse hook（Write/Edit/MultiEdit）：缺 domain 则 deny，附可用域列表
  python3 memory-frontmatter-guard.py --selftest
  MEMORY_FRONTMATTER_GUARD_SKIP=1                # 逃生阀
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

ESCAPE_ENV = "MEMORY_FRONTMATTER_GUARD_SKIP"

# 这些不是 memory 条目，是索引/说明，不该被要求带 domain。
# 与 memory_lint 的筛选规则保持一致（.md 且不叫 MEMORY.md 且不以 _ 开头）
# ——判据有两份就必然漂移，而漂移的那份报的绿是假绿。
def is_fact_path(p: Path) -> bool:
    return (
        p.suffix == ".md"
        and p.parent.name == "memory"
        and p.parent.parent.name == ".claude"
        and p.name != "MEMORY.md"
        and not p.name.startswith("_")
    )


def domains_for(p: Path) -> list[str] | None:
    """读项目的域定义。没有 → None（该项目没启用分域，本 hook 不管）。"""
    try:
        conf = json.loads((p.parent / "domains.json").read_text(encoding="utf-8"))
        return [d["id"] for d in conf.get("domains", [])]
    except Exception:
        return None


def has_domain(text: str) -> bool:
    return bool(re.search(r"^\s+domain:\s*\S+\s*$", text, re.M))


def domain_value(text: str) -> str | None:
    m = re.search(r"^\s+domain:\s*(\S+)\s*$", text, re.M)
    return m.group(1) if m else None


def verdict(path_str: str, content: str) -> tuple[bool, str]:
    """(要不要拦, 理由)。拦 = True。"""
    p = Path(path_str)
    if not is_fact_path(p):
        return False, ""
    doms = domains_for(p)
    if doms is None:
        return False, ""                       # 该项目没启用分域
    if not has_domain(content):
        listed = " / ".join(doms)
        return True, (
            f"这条 memory 缺 `metadata.domain`——**没有它，它不属于任何分域索引，"
            f"`memory-domain-loader` 永远不会注入它，等于写了个读不到的文件。**\n\n"
            f"在 frontmatter 的 `metadata:` 下加一行 `domain: <id>`，可用取值：\n"
            f"  {listed}\n\n"
            f"跨任务恒成立的（答题纪律 / 不可逆操作 / 安全红线）才填 `always`，"
            f"它是唯一常驻 MEMORY.md 的域，门槛要卡死——每加一条都永久抬高每一轮对话的成本。\n"
            f"写完把索引行追加进对应的 `_index_<id>.md`（`always` 才写 MEMORY.md），"
            f"规矩见 `.claude/memory/_README.md`。"
        )
    val = domain_value(content)
    if val not in doms:
        return True, (
            f"`domain: {val}` 不在 `.claude/memory/domains.json` 里。"
            f"可用取值：{' / '.join(doms)}。"
            f"（真要新增一个域，先改 domains.json，再跑 "
            f"`python3 .claude/memory/scripts/rebuild_indexes.py`。）"
        )
    return False, ""


def main() -> int:
    if os.environ.get(ESCAPE_ENV):
        return 0
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
    except Exception:
        return 0

    try:
        tool = data.get("tool_name", "")
        inp = data.get("tool_input", {}) or {}
        # 只管 Write（新建/整文件覆盖）。Edit 局部改已有 memory 时 domain 早在了，
        # 查它只会制造噪音——见 docstring 的诚实边界。
        if tool != "Write":
            return 0
        path_str = inp.get("file_path", "")
        content = inp.get("content", "")
        if not path_str:
            return 0
        block, reason = verdict(path_str, content)
        if not block:
            return 0
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason":
                    f"【memory 缺 domain，已拦下】{reason}\n"
                    f"（这条 deny 是给 agent 看的，不弹给用户——加一行就好，"
                    f"改完重试即可。逃生阀 `{ESCAPE_ENV}=1`。）",
            }
        }))
    except Exception:
        return 0                              # fail-open：绝不因为这道检查卡住工作
    return 0


def _selftest() -> int:
    import tempfile

    passed, failed = [], []

    def check(name, got, want):
        (passed if got == want else failed).append(f"{name}（got={got!r} want={want!r}）")

    OK = "---\nname: x\nmetadata:\n  type: feedback\n  domain: ppt\n---\n正文\n"
    NO = "---\nname: x\nmetadata:\n  type: feedback\n---\n正文\n"
    BAD = "---\nname: x\nmetadata:\n  type: feedback\n  domain: 打错的\n---\n正文\n"

    with tempfile.TemporaryDirectory() as td:
        mem = Path(td) / ".claude" / "memory"
        mem.mkdir(parents=True)

        # ── 阴性对照：项目没启用分域 → 一律放行 ─────────────────────
        check("未启用分域·缺 domain 也放行", verdict(str(mem / "a.md"), NO)[0], False)

        (mem / "domains.json").write_text(
            json.dumps({"domains": [{"id": "always"}, {"id": "ppt"}, {"id": "git"}]}),
            encoding="utf-8")

        # ── 阳性 ────────────────────────────────────────────────────
        check("缺 domain → 拦", verdict(str(mem / "a.md"), NO)[0], True)
        check("域名写错 → 拦", verdict(str(mem / "a.md"), BAD)[0], True)
        check("报文列出可用域", "ppt" in verdict(str(mem / "a.md"), NO)[1], True)

        # ── 阴性对照：这些都不该拦（误报会让人把 hook 关掉）─────────
        check("有合法 domain → 放行", verdict(str(mem / "a.md"), OK)[0], False)
        check("MEMORY.md 不是条目", verdict(str(mem / "MEMORY.md"), NO)[0], False)
        check("_index_*.md 不是条目", verdict(str(mem / "_index_ppt.md"), NO)[0], False)
        check("_README.md 不是条目", verdict(str(mem / "_README.md"), NO)[0], False)
        check("memory 之外的 .md 不管",
              verdict(str(Path(td) / "docs" / "note.md"), NO)[0], False)
        check("非 .md 不管", verdict(str(mem / "a.json"), NO)[0], False)

        # ── 只管 Write：Edit 走不到 verdict，这里验主流程的 tool 分支 ──
        import io
        for tool, want_out in (("Edit", ""), ("Write", "deny")):
            payload = json.dumps({"tool_name": tool, "tool_input": {
                "file_path": str(mem / "a.md"), "content": NO}})
            old_in, old_out = sys.stdin, sys.stdout
            sys.stdin, sys.stdout = io.StringIO(payload), io.StringIO()
            try:
                main()
                out = sys.stdout.getvalue()
            finally:
                sys.stdin, sys.stdout = old_in, old_out
            check(f"tool={tool} 的输出", want_out in out if want_out else out == "", True)

    for line in passed:
        print(f"  ✅ {line}")
    for line in failed:
        print(f"  ❌ {line}")
    if failed:
        print(f"\n❌ selftest 失败 {len(failed)}/{len(passed) + len(failed)}")
        return 1
    print(f"\n✅ selftest 全过（{len(passed)} 项，含 7 条阴性对照）")
    return 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(_selftest())
    sys.exit(main())
