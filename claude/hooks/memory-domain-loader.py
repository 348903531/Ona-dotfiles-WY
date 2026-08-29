#!/usr/bin/env python3
r"""memory-domain-loader — UserPromptSubmit hook：按当天任务，只注入相关那批 memory 索引。

为什么有它（2026-08-29，用户追问「几千条你还能读吗」逼出来的）
==============================================================
文件式 memory 是两层加载：

    MEMORY.md（索引）  harness 【原生全量注入】，每次会话固定开销 —— hook 拦不住
    <name>.md（正文）  只在 agent 判断相关时才单独 Read

于是索引成了唯一的常驻成本，而它**只增不减**。实测本机 114 条时索引已
15,029 codepoints（≈1.4 万 token 每轮）；外推 1000 条 ≈ 13 万 token 常驻，
其中绝大多数与当天任务无关。**「加了也白加」就是这么从个例变成常态的。**

同族参照 lessons-index：159 条、预算 9000 字符，实测只进 45 条，注入报文自己
写着「有 35 条无兜底教训也没塞进来」——那 35 条就是白加真实发生的样子。

**既然 MEMORY.md 的全量注入拦不住，就让它本身变短。** 做法：
  · `always` 域（跨任务恒成立）留在 MEMORY.md —— 原样常驻，一条不动
  · 其余按域拆进 `.claude/memory/_index_<id>.md`
  · 本 hook 在 prompt 进来时按关键词判任务类型，把相关域的子索引注入上下文

实测效果：常驻索引 15,209 → 2,834 codepoints（**降 81%**），115 条一条没丢。

与 task-rule-injector 的分工（同事件点、不重叠）
-----------------------------------------------
task-rule-injector 注入的是 **AGENTS.md 的硬规定**（该看哪张卡、该跑哪个闸门）；
本 hook 注入的是 **memory 里的经验事实**（这类活以前踩过什么坑）。
一个是「规矩」，一个是「踩过的坑」，都在任务开始那一刻送到眼前。
两者刻意用**各自的**关键词表：规矩按 AGENTS.md 的卡片分类，
经验按 memory 实际的内容分布（如 `corpus` 资料库在哪、`gatecraft` 造闸门方法），
后者在前者里根本没有对应类型。

设计取舍（三条，都是踩过才这么定的）
------------------------------------
1. **域定义放项目、机制放 dotfiles**。域的取值（ppt / medical / corpus…）是各项目
   自己的事，写在项目的 `.claude/memory/domains.json`；本脚本只认那份文件。
   读不到 → 静默不注入（fail-safe：退化成拆分前的现状，不会更差）。
2. **宁多勿漏**。关键词命中多个域就都注入——多读几行索引的成本，远小于
   「那条坑就在库里，但今天没被送到眼前」。
3. **注入的是索引行不是正文**。一行一条「什么情况下你需要我」，agent 看了自行决定
   Read 哪个文件。正文动辄几千字，全量注入等于把省下来的开销又还回去。

诚实边界
--------
- 关键词判任务类型**会漏也会误**。漏了就退化成「得自己 grep」，不会更差；
  误了就多几行索引。**但漏的那次，那条 memory 就等于不存在**——所以
  MEMORY.md 里必须留「其余在 _index_*.md、可直接 grep」的指引，本 hook 不是唯一入口。
- 判不了「这条 memory 现在还对不对」（过期与否是语义判断）。
- 提示型注入，保证不了 agent 真去读。

用法
----
  UserPromptSubmit hook：读 stdin JSON，命中则注入 additionalContext，永远 exit 0
  python3 memory-domain-loader.py --check "做个PPT"   # 干跑，看会注入哪些域
  python3 memory-domain-loader.py --selftest
  MEMORY_DOMAIN_LOADER_SKIP=1                        # 逃生阀
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

CACHE_DIR = Path(os.path.expanduser("~/.cache/claude-memory-domain-loader"))
ESCAPE_ENV = "MEMORY_DOMAIN_LOADER_SKIP"

# 一次最多注入几个域。命中太多说明 prompt 很泛（"帮我看看这个项目"），
# 那种情况全灌等于没筛，还不如让 agent 自己去 grep。
MAX_DOMAINS = 3

# 单个域的子索引超过这个长度就截断并提示——防某个域自己长成第二个 MEMORY.md。
MAX_DOMAIN_CP = 4000


def _memory_dir(cwd: Path) -> Path:
    return cwd / ".claude" / "memory"


def load_domains(cwd: Path) -> list[dict] | None:
    try:
        conf = json.loads((_memory_dir(cwd) / "domains.json").read_text(encoding="utf-8"))
        return [d for d in conf.get("domains", []) if not d.get("resident")]
    except Exception:
        return None                      # 没配 / 读不了 → 静默退化


def match_domains(prompt: str, domains: list[dict]) -> list[str]:
    """返回命中的域 id，按「命中关键词数」降序——命中越多越可能是主任务。"""
    low = prompt.lower()
    scored = []
    for d in domains:
        hits = sum(1 for kw in d.get("keywords", []) if kw.lower() in low)
        if hits:
            scored.append((hits, d["id"]))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [i for _, i in scored[:MAX_DOMAINS]]


def _already_injected(session_id: str, ids: list[str]) -> list[str]:
    """每会话每域只注入一次（同一会话里做第二个 PPT 不必再灌一遍）。"""
    if not session_id:
        return ids                       # 拿不到 session_id 就不去重，宁多勿漏
    path = CACHE_DIR / session_id
    done: set[str] = set()
    try:
        if path.exists():
            done = set(path.read_text(encoding="utf-8").split())
    except Exception:
        done = set()
    fresh = [i for i in ids if i not in done]
    if fresh:
        try:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as f:
                f.write("\n".join(fresh) + "\n")
        except Exception:
            pass                         # 写不了 cache 顶多重复注入一次
    return fresh


def build_block(cwd: Path, ids: list[str], domains: list[dict]) -> str | None:
    label = {d["id"]: d.get("label", d["id"]) for d in domains}
    parts = []
    for did in ids:
        f = _memory_dir(cwd) / f"_index_{did}.md"
        try:
            text = f.read_text(encoding="utf-8")
        except OSError:
            continue
        rows = [ln for ln in text.splitlines() if ln.strip().startswith("- [")]
        if not rows:
            continue
        body = "\n".join(rows)
        if len(body) > MAX_DOMAIN_CP:
            keep, dropped = [], 0
            for r in rows:
                if sum(len(x) for x in keep) + len(r) > MAX_DOMAIN_CP:
                    dropped += 1
                else:
                    keep.append(r)
            body = "\n".join(keep) + (
                f"\n  …还有 {dropped} 条没列（该域索引偏长，"
                f"全量见 .claude/memory/_index_{did}.md）"
            )
        parts.append(f"### `{did}` {label.get(did, did)}\n{body}")

    if not parts:
        return None
    head = (
        "【按任务加载的 memory · memory-domain-loader】\n"
        "这些是**这类任务过去踩过的坑**，只在做这类活时才送到眼前"
        "（跨任务恒成立的那批常驻在 MEMORY.md，不在这里重复）。\n"
        "下面每行是「什么情况下你需要我」——**觉得相关就去 Read 那个文件读全文**，"
        "别只凭这一行下判断。"
    )
    tail = (
        f"（漏了没注入到的域可自己找：`ls .claude/memory/_index_*.md` 或 "
        f"`grep -rn <关键词> .claude/memory/`。逃生阀 `{ESCAPE_ENV}=1`。）"
    )
    return "\n\n".join([head] + parts + [tail])


def _resolve_cwd(payload: dict) -> Path:
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
        return 0

    try:
        prompt = payload.get("prompt", "") or ""
        if not prompt.strip():
            return 0
        cwd = _resolve_cwd(payload)
        domains = load_domains(cwd)
        if not domains:
            return 0                     # 项目没启用分域 → 静默
        ids = match_domains(prompt, domains)
        if not ids:
            return 0                     # 闲聊 / 泛问 → 不注入
        ids = _already_injected(payload.get("session_id", "") or "", ids)
        if not ids:
            return 0
        block = build_block(cwd, ids, domains)
        if not block:
            return 0
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": block,
            }
        }))
    except Exception:
        return 0                         # fail-open：绝不因为这道注入卡住用户提问
    return 0


def run_check(prompt: str, cwd: Path | None = None) -> int:
    cwd = cwd or _resolve_cwd({})
    domains = load_domains(cwd)
    if not domains:
        print(f"⏭️  SKIP：{_memory_dir(cwd)}/domains.json 读不到（该项目未启用分域）")
        return 0
    ids = match_domains(prompt, domains)
    if not ids:
        print(f"（无命中，不注入）prompt={prompt!r}")
        return 0
    print(f"命中域：{', '.join(ids)}\n")
    block = build_block(cwd, ids, domains)
    print(block or "（域文件为空）")
    print(f"\n注入体量：{len(block or ''):,} codepoints")
    return 0


def _selftest() -> int:
    import tempfile

    passed, failed = [], []

    def check(name, got, want):
        (passed if got == want else failed).append(f"{name}（got={got!r} want={want!r}）")

    conf = {"domains": [
        {"id": "always", "label": "常驻", "resident": True, "keywords": []},
        {"id": "ppt", "label": "幻灯", "keywords": ["ppt", "幻灯", "slide"]},
        {"id": "git", "label": "git", "keywords": ["git", "分支", "pr"]},
        {"id": "lit", "label": "文献", "keywords": ["文献", "pubmed"]},
        {"id": "medical", "label": "医学", "keywords": ["样本量", "单臂"]},
    ]}

    with tempfile.TemporaryDirectory() as td:
        cwd = Path(td)
        mem = cwd / ".claude" / "memory"
        mem.mkdir(parents=True)
        (mem / "domains.json").write_text(json.dumps(conf), encoding="utf-8")
        for d in ("ppt", "git", "lit", "medical"):
            (mem / f"_index_{d}.md").write_text(
                f"# {d}\n\n- [{d}坑一]({d}-1.md) — 描述一\n- [{d}坑二]({d}-2.md) — 描述二\n",
                encoding="utf-8",
            )
        doms = load_domains(cwd)

        # ── resident 域不参与匹配（它常驻 MEMORY.md，注入=重复）──────
        check("always 不参与匹配", [d["id"] for d in doms].count("always"), 0)

        # ── 基本命中 ────────────────────────────────────────────────
        check("命中 ppt", match_domains("帮我做个PPT", doms), ["ppt"])
        check("中文命中", match_domains("这套幻灯要改", doms), ["ppt"])
        check("多域命中", sorted(match_domains("做完PPT后开分支提PR", doms)), ["git", "ppt"])

        # ── 阴性对照：闲聊不注入（最重要，误注入会污染每一条 prompt）──
        check("阴性·闲聊不命中", match_domains("你好，今天天气怎么样", doms), [])
        check("阴性·空 prompt", match_domains("", doms), [])
        check("阴性·无关技术词", match_domains("解释一下什么是傅里叶变换", doms), [])

        # ── 排序：命中词多的域排前面 ────────────────────────────────
        order = match_domains("查文献 pubmed 顺便看看 ppt", doms)
        check("排序·文献命中2词排第一", order[0], "lit")

        # ── MAX_DOMAINS 截断 ────────────────────────────────────────
        many = match_domains("ppt 分支 文献 样本量 都要", doms)
        check("最多注入3个域", len(many) <= MAX_DOMAINS, True)

        # ── 注入块内容正确 ──────────────────────────────────────────
        block = build_block(cwd, ["ppt"], doms)
        check("注入块含索引行", "- [ppt坑一](ppt-1.md)" in block, True)
        check("注入块含 grep 兜底指引", "grep -rn" in block, True)

        # ── 项目没配 domains.json → 静默退化，绝不报错 ───────────────
        with tempfile.TemporaryDirectory() as td2:
            check("未启用分域·返回 None", load_domains(Path(td2)), None)

        # ── 会话级去重 ──────────────────────────────────────────────
        real = os.environ.get("HOME")
        try:
            os.environ["HOME"] = td
            global CACHE_DIR
            CACHE_DIR = Path(td) / "cache"
            check("首次注入", _already_injected("s1", ["ppt", "git"]), ["ppt", "git"])
            check("同会话同域不重复", _already_injected("s1", ["ppt"]), [])
            check("同会话新域仍注入", _already_injected("s1", ["lit"]), ["lit"])
            check("换会话重新注入", _already_injected("s2", ["ppt"]), ["ppt"])
            check("无 session_id 不去重", _already_injected("", ["ppt"]), ["ppt"])
        finally:
            if real is not None:
                os.environ["HOME"] = real

    for line in passed:
        print(f"  ✅ {line}")
    for line in failed:
        print(f"  ❌ {line}")
    if failed:
        print(f"\n❌ selftest 失败 {len(failed)}/{len(passed) + len(failed)}")
        return 1
    print(f"\n✅ selftest 全过（{len(passed)} 项，含 4 条阴性对照）")
    return 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(_selftest())
    if "--check" in sys.argv:
        i = sys.argv.index("--check")
        sys.exit(run_check(sys.argv[i + 1] if len(sys.argv) > i + 1 else ""))
    sys.exit(run_hook())
