"""Project structure report (2026-09-25): the whole ic-opt-modular repository organised with archify and graphify.

Usage:
  build_structure_report.py collect   # repo / git / blocks / docs / graphify-out / diagrams -> structure_facts.json
  build_structure_report.py page      # structure_facts.json (+ library_rows.json) -> PROJECT_STRUCTURE_2026-09-25_CN.html, figs/

`collect` runs `.venv/bin/ic-opt blocks` and reads graphify-out/ (graph.json, GRAPH_REPORT.md, .graphify_labels.json), which
is not tracked; the JSON it writes is, so `page` reproduces the page without graphify.
"""

from __future__ import annotations

import base64
import collections
import html
import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
FACTS = HERE / "structure_facts.json"
ROWS = HERE / "library_rows.json"
OUT = HERE / "PROJECT_STRUCTURE_2026-09-25_CN.html"
FIGS = HERE / "figs"
DIAGRAMS = ROOT / "docs" / "refactor" / "diagrams"
DATE = "2026-09-25"

PACKAGES = [
    (
        "src/ic_opt",
        "顶层模块",
        "命令行、spec 模型、参数空间与目标、观测与运行存储、site 限额、配方装载、迁移、文件锁",
        0,
    ),
    (
        "src/ic_opt/blocks",
        "blocks 可组合块",
        "20 个自描述块：spec / points / sim / opt / analyze / netlist / env / em / lib",
        1,
    ),
    (
        "src/ic_opt/recipes",
        "recipes 配方",
        "6 个 Python 配方：optimize、coarse_to_fine、fix_run、signoff、lib_design、lib_signoff",
        1,
    ),
    (
        "src/ic_opt/eval",
        "eval 评估引擎",
        "engine.run：并发调度、预算、资源包络、Ctrl-C 与超时传递",
        1,
    ),
    (
        "src/ic_opt/stages",
        "stages 阶段流水线",
        "spectre 链（netlist → spectre/ocean → 标量）与 EM 链（pcell → emx → bind_nport）",
        1,
    ),
    ("src/ic_opt/executor", "executor 执行器", "本地 / SSH 执行器、进程组与信号转发、命令超时", 1),
    ("src/ic_opt/sim", "sim 电路仿真", "OCEAN 脚本生成与标量解析、网表 deck、角点", 1),
    (
        "src/ic_opt/suggesters",
        "suggesters 建议器",
        "TuRBO、OpenBox（默认）、Sobol / 拉丁超立方 / 随机",
        1,
    ),
    (
        "src/ic_opt/em",
        "em EM 量测",
        "EMX 设置与命令行、Touchstone 读写、nport 网表补丁、measure（sNp → 结果列）",
        0,
    ),
    (
        "src/ic_opt/em/pcell",
        "em/pcell 几何库",
        "六族无源器件生成器、工艺规则 profile、DRC 审计、金属栈、回放、参数参考",
        1,
    ),
    (
        "src/ic_opt/library",
        "library 器件查询库",
        "清单、数据集、StratumGP / ComposedGP、域守卫、query / suggest / region / densify、缓存、阶段",
        1,
    ),
]

PCELL_GROUPS = [
    (
        "生成器（六族）",
        [
            "_pcell_ind_sym.py",
            "_pcell_xfm_bs.py",
            "_pcell_xfm_ms.py",
            "_pcell_xfm_balun.py",
            "_pcell_xfm_tw.py",
            "_pcell_xfm_il.py",
        ],
    ),
    (
        "核心与图元",
        [
            "_pcell_core.py",
            "_pcell_primitives.py",
            "_pcell_guards.py",
            "_pcell_straight_extension.py",
            "_pcell_demo.py",
            "pcell_inductor_port_clean.py",
        ],
    ),
    (
        "插件与契约",
        [
            "generator_plugin.py",
            "base.py",
            "registry.py",
            "fixture.py",
            "pgs.py",
            "connectivity.py",
            "path_safety.py",
        ],
    ),
    (
        "工艺规则",
        [
            "process_rules.py",
            "rule_adapter.py",
            "stack.py",
            "profile_validation.py",
            "proc_file.py",
        ],
    ),
    ("审计与比较", ["drc_audit.py", "gds_compare.py", "render.py", "replay.py", "reference.py"]),
]

COMPONENTS = [
    (
        "designer",
        "设计者 / Agent",
        "skills/ic-opt/SKILL.md · examples/spec.yaml",
        "写 spec.yaml、准备仓库外的工艺 profile，按技能文档下命令",
    ),
    (
        "cli",
        "ic-opt 命令行",
        "src/ic_opt/cli.py",
        "run / call / blocks / describe / doctor / migrate / migrate-store 七个子命令",
    ),
    (
        "recipes",
        "配方 recipes",
        "src/ic_opt/recipes/ · src/ic_opt/recipe.py",
        "用 Python 组合块；Run 对象装载 spec、site.yaml、执行器与运行存储",
    ),
    ("blocks", "可组合块 blocks", "src/ic_opt/blocks/", "20 个块，各自自描述，报告一律严格 JSON"),
    ("suggesters", "建议器", "src/ic_opt/suggesters/", "opt.suggest / opt.optimize 用的取点策略"),
    (
        "engine",
        "评估引擎 + 阶段流水线",
        "src/ic_opt/eval/engine.py · src/ic_opt/stages/",
        "按 spec 的仿真链把点交给执行器，管并发、预算与资源包络；有 devices 的 spec 走 em_circuit 链：pcell → emx → bind_nport → spectre → ocean → extract",
    ),
    (
        "executor",
        "执行器",
        "src/ic_opt/executor/ · src/ic_opt/site.py",
        "本地或 SSH 主机上跑阶段任务；HostLimits 限定线程与内存",
    ),
    (
        "store",
        "运行存储 .icopt/",
        "src/ic_opt/store.py · src/ic_opt/migrate_store.py",
        "观测表、sims 产物、缓存；指纹变更时用 migrate-store 迁移",
    ),
    (
        "pcell",
        "pcell 几何与工艺",
        "src/ic_opt/em/pcell/",
        "生成 GDS + 端口并做三层 DRC 检查；随包只带虚构的 demo_6m profile",
    ),
    (
        "library",
        "器件查询库",
        "src/ic_opt/library/ · docs/em/library.md",
        "只读运行存储建数据集与模型，回答 lib.* 查询；lib_signoff 用真实 EMX 复核并回流",
    ),
    (
        "spectre",
        "Spectre / OCEAN",
        "src/ic_opt/stages/spectre_chain.py · src/ic_opt/sim/",
        "电路仿真与标量抽取（仿真主机）；联合优化时网表里的 nport 实例指向 EMX 的 sNp",
    ),
    (
        "emx",
        "EMX",
        "src/ic_opt/em/emx.py · src/ic_opt/stages/em_chain.py",
        "全波电磁仿真得 sNp，供 bind_nport 绑进电路网表；.proc 工艺文件只在主机上",
    ),
]

AREAS = [
    ("pcell 几何库", lambda p: p.startswith("src/ic_opt/em/pcell")),
    ("器件查询库", lambda p: p.startswith("src/ic_opt/library")),
    ("EM 量测 / EMX", lambda p: p.startswith("src/ic_opt/em/")),
    (
        "块 / 配方 / 命令行",
        lambda p: p.startswith(
            ("src/ic_opt/blocks", "src/ic_opt/recipes", "src/ic_opt/cli.py", "src/ic_opt/recipe.py")
        ),
    ),
    (
        "引擎 / 阶段 / 执行器 / 存储",
        lambda p: p.startswith(
            (
                "src/ic_opt/eval",
                "src/ic_opt/stages",
                "src/ic_opt/executor",
                "src/ic_opt/sim",
                "src/ic_opt/store.py",
                "src/ic_opt/site.py",
                "src/ic_opt/_lock.py",
                "src/ic_opt/migrate",
            )
        ),
    ),
    (
        "spec / 空间 / 建议器",
        lambda p: p.startswith(
            (
                "src/ic_opt/spec.py",
                "src/ic_opt/space.py",
                "src/ic_opt/objective.py",
                "src/ic_opt/observation.py",
                "src/ic_opt/deck.py",
                "src/ic_opt/suggesters",
            )
        ),
    ),
    ("测试", lambda p: p.startswith("tests/")),
    ("报告脚本", lambda p: p.startswith("docs/refactor/reports")),
    (
        "文档 / 计划 / 分析",
        lambda p: p.startswith(("docs/", "README", "CONTRIBUTING", "RELEASE_NOTES")),
    ),
    ("技能 / 脚本 / 其他", lambda p: True),
]


def sh(*args: str) -> str:
    return subprocess.run(
        args, cwd=ROOT, capture_output=True, text=True, check=False
    ).stdout.strip()


def py_files(rel: str, recursive: bool) -> list[Path]:
    d = ROOT / rel
    files = d.rglob("*.py") if recursive else d.glob("*.py")
    return sorted(f for f in files if "__pycache__" not in f.parts)


def lines(files: list[Path]) -> int:
    return sum(len(f.read_text(encoding="utf-8", errors="replace").splitlines()) for f in files)


def area_of(path: str) -> str:
    for name, test in AREAS:
        if test(path):
            return name
    return AREAS[-1][0]


def collect() -> None:
    facts: dict = {"date": DATE}
    facts["git"] = {
        "head": sh("git", "rev-parse", "HEAD"),
        "head_date": sh("git", "log", "-1", "--format=%ci"),
        "origin_main": sh("git", "rev-parse", "origin/main"),
        "ahead": int(sh("git", "rev-list", "--count", "origin/main..HEAD") or 0),
        "tags": sh("git", "tag", "--list", "v*").split(),
        "v030": sh("git", "rev-parse", "v0.3.0^{commit}"),
        "commits_total": int(sh("git", "rev-list", "--count", "HEAD") or 0),
        "commits_since_0922": int(
            sh("git", "rev-list", "--count", "--since=2026-09-22", "HEAD") or 0
        ),
    }
    facts["version"] = re.search(
        r'^version = "([^"]+)"', (ROOT / "pyproject.toml").read_text(encoding="utf-8"), re.MULTILINE
    ).group(1)
    pk = []
    for rel, label, desc, deep in PACKAGES:
        recursive = rel == "src/ic_opt/em/pcell"
        files = py_files(rel, recursive)
        if rel == "src/ic_opt/em":
            files = [f for f in files if f.parent == ROOT / rel]
        pk.append(
            {
                "path": rel,
                "label": label,
                "desc": desc,
                "files": len(files),
                "lines": lines(files),
                "modules": [f.name for f in files if f.name != "__init__.py"],
            }
        )
    facts["packages"] = pk
    src = py_files("src", True)
    tests = py_files("tests", True)
    facts["totals"] = {
        "src_files": len(src),
        "src_lines": lines(src),
        "test_files": len(tests),
        "test_lines": lines(tests),
        "test_functions": sum(
            len(re.findall(r"^\s*def test_", f.read_text(encoding="utf-8"), re.MULTILINE))
            for f in tests
        ),
    }
    tdirs = collections.Counter()
    tlines = collections.Counter()
    for f in tests:
        key = str(f.parent.relative_to(ROOT))
        tdirs[key] += 1
        tlines[key] += len(f.read_text(encoding="utf-8").splitlines())
    facts["test_dirs"] = [
        {"dir": k, "files": v, "lines": tlines[k]} for k, v in sorted(tdirs.items())
    ]
    # blocks / recipes / cli
    blocks = []
    for line in sh(str(ROOT / ".venv" / "bin" / "ic-opt"), "blocks").splitlines():
        m = re.match(r"(\S+)\s+(.*)", line)
        if m:
            blocks.append({"name": m.group(1), "desc": m.group(2).strip()})
    facts["blocks"] = blocks
    recipes = []
    for f in sorted((ROOT / "src/ic_opt/recipes").glob("*.py")):
        if f.name == "__init__.py":
            continue
        m = re.search(r'"""(.+?)(?:\n|""")', f.read_text(encoding="utf-8"))
        recipes.append({"name": f.stem, "desc": (m.group(1).strip() if m else "")})
    facts["recipes"] = recipes
    cli_src = (ROOT / "src/ic_opt/cli.py").read_text(encoding="utf-8")
    cmds = []
    for m in re.finditer(r'@app\.command\((?:"([^"]+)")?\)\s*\ndef (\w+)\(', cli_src):
        name = m.group(1) or m.group(2)
        doc = re.search(r'"""(.+?)(?:\n|""")', cli_src[m.end() : m.end() + 2500])
        cmds.append({"name": name, "desc": doc.group(1).strip() if doc else ""})
    facts["cli"] = cmds
    # docs
    docs = []
    for rel in [
        "README.md",
        "CONTRIBUTING.md",
        "RELEASE_NOTES_v0.3.0.md",
        "docs/em/library.md",
        "docs/em/devices.md",
        "docs/adr/0001-remote-filesystem-boundary.md",
        "skills/ic-opt/SKILL.md",
        "skills/author-process-rule/SKILL.md",
        "src/ic_opt/em/pcell/README.md",
    ]:
        p = ROOT / rel
        if p.exists():
            docs.append({"path": rel, "lines": len(p.read_text(encoding="utf-8").splitlines())})
    plans = []
    for p in sorted((ROOT / "docs/refactor").glob("*.md")):
        text = p.read_text(encoding="utf-8")
        h = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
        plans.append(
            {
                "path": str(p.relative_to(ROOT)),
                "lines": len(text.splitlines()),
                "title": h.group(1).strip() if h else p.stem,
            }
        )
    analysis = [
        {"path": str(p.relative_to(ROOT)), "lines": len(p.read_text(encoding="utf-8").splitlines())}
        for p in sorted((ROOT / "docs/refactor/analysis").rglob("*.md"))
    ]
    facts["docs"] = {"core": docs, "plans": plans, "analysis": analysis}
    # report pages from INDEX_CN.md
    pages = []
    for line in (
        (ROOT / "docs/refactor/reports/INDEX_CN.md").read_text(encoding="utf-8").splitlines()
    ):
        m = re.match(r"\|\s*`([^`]+\.html)`", line)
        if m:
            art = re.search(
                r"\[([A-Za-z0-9]{20,})\]\(https://claude\.ai/artifact/[A-Za-z0-9]+\)", line
            )
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            pages.append(
                {
                    "page": m.group(1),
                    "artifact": art.group(1) if art else None,
                    "note": cells[-1] if cells else "",
                }
            )
    facts["report_pages"] = pages
    # diagrams
    diagrams = []
    for stem, kind, art in [
        ("ic-opt-system-v030", "architecture", "Q2GCRD2WtGwpZHH8LvTFfG"),
        ("device-library-pipeline", "dataflow", "T1NBTvFvkTtoEstp7ZvMqA"),
    ]:
        spec = json.loads((DIAGRAMS / f"{stem}.{kind}.json").read_text(encoding="utf-8"))
        vc = json.loads((DIAGRAMS / f"{stem}.visual-check.json").read_text(encoding="utf-8"))
        diagrams.append(
            {
                "stem": stem,
                "kind": kind,
                "title": spec["meta"]["title"],
                "artifact": art,
                "nodes": len(spec.get("components") or spec.get("nodes") or []),
                "edges": len(spec.get("connections") or spec.get("flows") or []),
                "views": [v["label"] for v in spec["meta"].get("views", [])],
                "visual_check": vc.get("status") or vc.get("result") or "见 json",
            }
        )
    facts["diagrams"] = diagrams
    # graphify
    gdir = ROOT / "graphify-out"
    graph = json.loads((gdir / "graph.json").read_text(encoding="utf-8"))
    labels = json.loads((gdir / ".graphify_labels.json").read_text(encoding="utf-8"))
    report = (gdir / "GRAPH_REPORT.md").read_text(encoding="utf-8")
    nodes = graph["nodes"]
    links = graph["links"]
    csize = collections.Counter(str(n.get("community")) for n in nodes)
    cfiles: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    carea: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    area_nodes = collections.Counter()
    for n in nodes:
        c = str(n.get("community"))
        sf = n.get("source_file") or ""
        cfiles[c][sf.split("/")[-1] or "?"] += 1
        a = area_of(sf)
        carea[c][a] += 1
        area_nodes[a] += 1
    conf = collections.Counter((l.get("confidence") or "?") for l in links)
    rel = collections.Counter((l.get("relation") or "?") for l in links)
    communities = []
    for c, n in csize.most_common():
        communities.append(
            {
                "id": int(c),
                "label": labels.get(c, f"Community {c}"),
                "size": n,
                "files": [f"{f} ({k})" for f, k in cfiles[c].most_common(3)],
                "area": carea[c].most_common(1)[0][0],
            }
        )
    god = [
        {"node": m.group(1), "edges": int(m.group(2))}
        for m in re.finditer(r"^\d+\. `([^`]+)` - (\d+) edges", report, re.MULTILINE)
    ]
    sec = re.search(r"## Surprising Connections.*?\n(.*?)\n## ", report, re.DOTALL)
    surprises = []
    if sec:
        for m in re.finditer(
            r"- `([^`]+)` --(\w+)--> `([^`]+)`\s+\[(\w+)\][^\n]*\n\s+(\S+) → (\S+)", sec.group(1)
        ):
            surprises.append(
                {
                    "a": m.group(1),
                    "rel": m.group(2),
                    "b": m.group(3),
                    "conf": m.group(4),
                    "fa": m.group(5),
                    "fb": m.group(6),
                }
            )
    hyper = [
        {"label": m.group(1), "n": len(m.group(2).split(",")), "conf": m.group(3)}
        for m in re.finditer(r"^- \*\*(.+?)\*\* — (.+?) \[(\w+ [\d.]+)\]", report, re.MULTILINE)
    ]
    qsec = re.search(r"## Suggested Questions\n(.*)", report, re.DOTALL)
    questions = re.findall(r"^- \*\*(.+?)\*\*", qsec.group(1), re.MULTILINE) if qsec else []
    summ = re.search(r"## Summary\n(.*?)\n\n", report, re.DOTALL)
    facts["graphify"] = {
        "files": 267,
        "words": 351279,
        "nodes": len(nodes),
        "edges": len(links),
        "communities": len(csize),
        "thin": sum(1 for v in csize.values() if v < 5),
        "confidence": dict(conf),
        "relations": rel.most_common(8),
        "summary": summ.group(1).strip() if summ else "",
        "communities_top": communities[:40],
        "all_communities": communities,
        "god_nodes": god,
        "surprises": surprises,
        "hyperedges": hyper,
        "questions": questions[:4],
        "area_nodes": area_nodes.most_common(),
        "chunks": 14,
        "input_tokens": 1520202,
        "output_tokens": 158973,
        "cost_note": "分块 01/06/08 的 token 用量未能从会话记录恢复，未计入",
    }
    FACTS.write_text(json.dumps(facts, indent=2, ensure_ascii=False), encoding="utf-8")
    print(
        f"facts -> {FACTS}: {len(pk)} packages, {len(blocks)} blocks, {len(communities)} communities"
    )


# ----------------------------------------------------------------------------------------------- page
def esc(s) -> str:
    return html.escape(str(s))


def b64(path: Path) -> str:
    return "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode("ascii")


def figures(facts: dict) -> dict[str, Path]:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams["font.family"] = ["Noto Sans CJK JP", "DejaVu Sans"]
    FIGS.mkdir(exist_ok=True)
    out = {}
    # 1 package lines
    pk = sorted(facts["packages"], key=lambda p: p["lines"])
    fig, ax = plt.subplots(figsize=(9, 4.6))
    ax.barh([p["label"] for p in pk], [p["lines"] for p in pk], color="#3c8f6e")
    for i, p in enumerate(pk):
        ax.text(
            p["lines"] + 120, i, f"{p['lines']:,} 行 / {p['files']} 文件", va="center", fontsize=9
        )
    ax.set_xlim(0, max(p["lines"] for p in pk) * 1.35)
    ax.set_xlabel("Python 行数（src/ic_opt）")
    ax.set_title("源码包规模")
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    p1 = FIGS / "PROJECT_STRUCTURE_packages.png"
    fig.savefig(p1, dpi=130)
    plt.close(fig)
    out["packages"] = p1
    # 2 community sizes
    top = facts["graphify"]["communities_top"][:30][::-1]
    fig, ax = plt.subplots(figsize=(9, 8.5))
    ax.barh([f"C{c['id']}  {c['label']}" for c in top], [c["size"] for c in top], color="#5b6fc9")
    ax.set_xlabel("节点数")
    ax.set_title("知识图谱：最大的 30 个社区")
    ax.tick_params(axis="y", labelsize=8)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    p2 = FIGS / "PROJECT_STRUCTURE_communities.png"
    fig.savefig(p2, dpi=130)
    plt.close(fig)
    out["communities"] = p2
    # 3 area nodes
    an = facts["graphify"]["area_nodes"][::-1]
    fig, ax = plt.subplots(figsize=(9, 4.2))
    ax.barh([a for a, _ in an], [n for _, n in an], color="#c98a3d")
    for i, (_, n) in enumerate(an):
        ax.text(n + 20, i, f"{n:,}", va="center", fontsize=9)
    ax.set_xlim(0, max(n for _, n in an) * 1.2)
    ax.set_xlabel("图谱节点数（按来源文件所在区域）")
    ax.set_title("知识图谱节点在仓库各区域的分布")
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    p3 = FIGS / "PROJECT_STRUCTURE_areas.png"
    fig.savefig(p3, dpi=130)
    plt.close(fig)
    out["areas"] = p3
    return out


def table(headers: list[str], rows: list[list[str]], cls: str = "") -> str:
    h = "".join(f"<th>{x}</th>" for x in headers)
    b = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f'<div class="tw"><table class="{cls}"><thead><tr>{h}</tr></thead><tbody>{b}</tbody></table></div>'


def page() -> None:
    f = json.loads(FACTS.read_text(encoding="utf-8"))
    rows = json.loads(ROWS.read_text(encoding="utf-8")) if ROWS.exists() else {}
    figs = figures(f)
    g = f["graphify"]
    git = f["git"]
    tot = f["totals"]
    arch_png = DIAGRAMS / "ic-opt-system-v030.visual-check.1440x900.light.png"
    flow_png = DIAGRAMS / "device-library-pipeline.visual-check.1440x900.light.png"
    lib_total = sum(v["rows"] for v in rows.values()) if rows else None

    key_rows = [
        [
            "版本 / 提交",
            f"IC-Opt <b>{esc(f['version'])}</b>；main@<code>{git['head'][:7]}</code>（{esc(git['head_date'][:16])}）；标签 <code>v0.3.0</code>@<code>{git['v030'][:7]}</code>",
        ],
        [
            "自 09-22 重构以来",
            f"{git['commits_since_0922']} 次提交（仓库共 {git['commits_total']} 次）；生成时本地领先 origin/main {git['ahead']} 次提交（main 与标签 v0.3.0 已于 2026-09-25 由 Claude 推送）",
        ],
        [
            "源码规模",
            f"{tot['src_files']} 个 Python 文件 / {tot['src_lines']:,} 行；其中 pcell {next(p['lines'] for p in f['packages'] if p['path'].endswith('pcell')):,} 行、查询库 {next(p['lines'] for p in f['packages'] if p['path'].endswith('library')):,} 行",
        ],
        [
            "测试规模",
            f"{tot['test_files']} 个文件 / {tot['test_lines']:,} 行 / {tot['test_functions']} 个测试函数",
        ],
        [
            "可组合面",
            f"{len(f['blocks'])} 个块、{len(f['recipes'])} 个配方、{len(f['cli'])} 个命令行子命令",
        ],
        [
            "N28 器件查询库（仓库外）",
            (f"{len(rows)} 张表，共 {lib_total:,} 行 ok 观测" if rows else "见 §6"),
        ],
        [
            "知识图谱",
            f"{g['files']} 个文件 / {g['words']:,} 词 → {g['nodes']:,} 节点、{g['edges']:,} 边、{g['communities']} 个社区（{g['thin']} 个不足 5 节点）",
        ],
        [
            "结构图",
            "archify 两张：系统结构（12 组件 / 13 连接）与器件查询库数据流（5 阶段 / 9 节点 / 9 流向），均通过 showcase 校验与浏览器检查",
        ],
    ]

    comp_rows = [
        [f"<b>{esc(n)}</b>", f"<code>{esc(s)}</code>", esc(d)] for _, n, s, d in COMPONENTS
    ]
    pk_rows = [
        [
            f"<code>{esc(p['path'])}</code>",
            esc(p["label"]),
            str(p["files"]),
            f"{p['lines']:,}",
            esc(p["desc"]),
        ]
        for p in f["packages"]
    ]
    pcell_rows = [
        [esc(gname), ", ".join(f"<code>{esc(m)}</code>" for m in mods)]
        for gname, mods in PCELL_GROUPS
    ]
    lib_mods = next(p for p in f["packages"] if p["path"].endswith("library"))["modules"]
    block_rows = [[f"<code>{esc(b['name'])}</code>", esc(b["desc"])] for b in f["blocks"]]
    recipe_rows = [[f"<code>{esc(r['name'])}</code>", esc(r["desc"])] for r in f["recipes"]]
    cli_rows = [[f"<code>ic-opt {esc(c['name'])}</code>", esc(c["desc"])] for c in f["cli"]]
    lib_rows = (
        [
            [
                f"<code>{esc(k)}</code>",
                f"{v['rows']:,}",
                "、".join(f"<code>{esc(pn)}</code> {pr:,}" for pn, pr in v["parts"].items()),
                f"{v['stored_reproduced']:,} / {v['mismatch']}",
            ]
            for k, v in rows.items()
        ]
        if rows
        else []
    )
    comm_rows = [
        [f"C{c['id']}", esc(c["label"]), str(c["size"]), esc(c["area"]), esc("、".join(c["files"]))]
        for c in g["communities_top"][:30]
    ]
    god_rows = [
        [f"<code>{esc(x['node'])}</code>", str(x["edges"]), esc(GOD_NOTES.get(x["node"], ""))]
        for x in g["god_nodes"]
    ]
    sur_rows = [
        [
            f"<code>{esc(s['a'])}</code> → <code>{esc(s['b'])}</code>",
            esc(s["rel"]),
            esc(s["conf"]),
            f"<code>{esc(s['fa'])}</code> ↔ <code>{esc(s['fb'])}</code>",
        ]
        for s in g["surprises"]
    ]
    hyper_rows = [[esc(h["label"]), str(h["n"]), esc(h["conf"])] for h in g["hyperedges"][:14]]
    plan_rows = [
        [f"<code>{esc(p['path'].split('/')[-1])}</code>", esc(p["title"]), str(p["lines"])]
        for p in f["docs"]["plans"]
    ]
    analysis_rows = [
        [f"<code>{esc(p['path'].replace('docs/refactor/analysis/', ''))}</code>", str(p["lines"])]
        for p in f["docs"]["analysis"]
    ]
    core_rows = [[f"<code>{esc(d['path'])}</code>", str(d["lines"])] for d in f["docs"]["core"]]
    page_rows = [
        [
            f"<code>{esc(p['page'])}</code>",
            (
                f'<a href="https://claude.ai/artifact/{p["artifact"]}">{p["artifact"][:8]}…</a>'
                if p["artifact"]
                else "—"
            ),
            esc(p["note"]),
        ]
        for p in f["report_pages"]
    ]
    tdir_rows = [
        [f"<code>{esc(t['dir'])}</code>", str(t["files"]), f"{t['lines']:,}"]
        for t in f["test_dirs"]
    ]
    conf = g["confidence"]
    conf_total = sum(conf.values()) or 1
    conf_txt = "、".join(
        f"{k} {v / conf_total * 100:.0f}%" for k, v in sorted(conf.items(), key=lambda kv: -kv[1])
    )
    rel_txt = "、".join(f"{k} {v:,}" for k, v in g["relations"])

    doc = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>IC-Opt 0.3 结构总览</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Noto+Sans+SC:wght@400;500;700&family=JetBrains+Mono:wght@400;600&display=swap">
<style>
:root {{ --ground:#f6f5f0; --surface:#ffffff; --surface-2:#eef0ea; --ink:#1f2320; --muted:#5f665f; --line:#d9dcd3; --accent:#2f7d5f; --accent-2:#4b5fb8; --warn:#b8611f; --code-bg:#eef0ea; }}
@media (prefers-color-scheme: dark) {{ :root:not([data-theme="light"]) {{ --ground:#141512; --surface:#1d1e1a; --surface-2:#262822; --ink:#e7e8e0; --muted:#a2a497; --line:#383a32; --accent:#7cc0a0; --accent-2:#9aa8ef; --warn:#e3a066; --code-bg:#24261f; }} }}
:root[data-theme="dark"] {{ --ground:#141512; --surface:#1d1e1a; --surface-2:#262822; --ink:#e7e8e0; --muted:#a2a497; --line:#383a32; --accent:#7cc0a0; --accent-2:#9aa8ef; --warn:#e3a066; --code-bg:#24261f; }}
* {{ box-sizing:border-box; }}
html, body {{ margin:0; background:var(--ground); color:var(--ink); }}
body {{ font-family:"Noto Sans SC", "PingFang SC", "Microsoft YaHei", system-ui, sans-serif; font-size:15px; line-height:1.65; padding-block:0 48px; padding-inline:16px; }}
code, pre {{ font-family:"JetBrains Mono", ui-monospace, "SFMono-Regular", Menlo, monospace; font-size:0.86em; }}
code {{ background:var(--code-bg); padding:1px 5px; border-radius:4px; }}
main {{ max-width:1080px; margin:0 auto; }}
header {{ padding-block:36px 8px; border-bottom:1px solid var(--line); margin-bottom:12px; }}
header h1 {{ font-size:2rem; margin:0 0 6px; letter-spacing:-0.01em; text-wrap:balance; }}
header p {{ margin:4px 0; color:var(--muted); }}
.eyebrow {{ text-transform:uppercase; letter-spacing:0.12em; font-size:0.74rem; color:var(--accent); font-weight:600; }}
nav.toc {{ display:flex; flex-wrap:wrap; gap:6px 14px; padding:10px 0 16px; border-bottom:1px solid var(--line); margin-bottom:24px; font-size:0.9rem; }}
nav.toc a {{ color:var(--accent-2); text-decoration:none; }}
h2 {{ font-size:1.35rem; margin:44px 0 10px; padding-top:8px; border-top:2px solid var(--accent); text-wrap:balance; }}
h3 {{ font-size:1.05rem; margin:24px 0 8px; color:var(--ink); }}
p {{ max-width:72ch; }}
.lead {{ font-size:1.05rem; }}
.tw {{ overflow-x:auto; margin:10px 0 18px; border:1px solid var(--line); border-radius:8px; background:var(--surface); }}
table {{ border-collapse:collapse; width:100%; font-size:0.9rem; }}
th, td {{ text-align:left; vertical-align:top; padding:7px 10px; border-bottom:1px solid var(--line); }}
th {{ background:var(--surface-2); font-weight:600; white-space:nowrap; }}
tr:last-child td {{ border-bottom:none; }}
td:first-child {{ white-space:nowrap; }}
table.kv td:first-child {{ width:14em; color:var(--muted); }}
figure {{ margin:14px 0 22px; background:var(--surface); border:1px solid var(--line); border-radius:8px; padding:10px; }}
figure img {{ max-width:100%; height:auto; display:block; border-radius:4px; }}
figcaption {{ font-size:0.86rem; color:var(--muted); margin-top:8px; }}
.grid2 {{ display:grid; grid-template-columns:repeat(auto-fit, minmax(300px, 1fr)); gap:14px; }}
.card {{ background:var(--surface); border:1px solid var(--line); border-radius:8px; padding:12px 14px; }}
.card h4 {{ margin:0 0 6px; font-size:0.98rem; }}
.card p, .card li {{ font-size:0.92rem; }}
ul {{ padding-left:1.3em; }} li {{ margin:3px 0; }}
.note {{ border-left:3px solid var(--warn); background:var(--surface); padding:8px 12px; margin:12px 0; font-size:0.93rem; }}
.path {{ font-size:0.84rem; color:var(--muted); word-break:break-all; }}
a {{ color:var(--accent-2); }}
@media (max-width: 640px) {{ header h1 {{ font-size:1.5rem; }} body {{ font-size:14px; }} }}
</style>
</head>
<body>
<main>
<header>
<div class="eyebrow">ic-opt-modular · 阶段性收尾 · {DATE}</div>
<h1>IC-Opt 0.3 项目结构总览</h1>
<p>用 archify 画出系统结构与器件查询库数据流，用 graphify 把 {
        g["files"]
    } 个源码与文档文件抽成知识图谱，再按包、块、配方、命令行、文档、测试和待办逐层整理。所有路径都以仓库根 <code>/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-modular/</code> 为准。</p>
</header>
<nav class="toc">
<a href="#s1">1 关键数字</a><a href="#s2">2 系统结构</a><a href="#s3">3 一次运行的路径</a><a href="#s4">4 源码包地图</a><a href="#s5">5 块 / 配方 / 命令行</a>
<a href="#s6">6 器件查询库</a><a href="#s7">7 知识图谱</a><a href="#s8">8 文档与报告</a><a href="#s9">9 测试体系</a><a href="#s10">10 状态与待办</a><a href="#s11">11 本次产物与方法</a>
</nav>

<h2 id="s1">1 关键数字</h2>
{table(["项", "值"], key_rows, "kv")}

<h2 id="s2">2 系统结构（archify 架构图）</h2>
<p class="lead">整个系统只有一条主路径：设计者写 <code>spec.yaml</code>，命令行装载配方，配方组合块，块把点交给评估引擎，引擎经执行器把阶段任务送到仿真主机，结果回到运行存储。这个项目的本质不是单独优化电磁器件，而是把真实的电磁器件带进原理图仿真做联合优化：spec 里同时写电路变量和器件几何变量，每个点先由 pcell 生成几何、EMX 全波仿真得到 sNp，再由 bind_nport 把 sNp 绑进电路网表，Spectre / OCEAN 跑出整机指标，优化器据此提下一批点。只到 EMX 为止的 em_only 链用于建器件库；器件查询库只读运行存储建模。</p>
<figure><a href="https://claude.ai/artifact/Q2GCRD2WtGwpZHH8LvTFfG"><img src="{
        b64(arch_png)
    }" alt="IC-Opt 0.3 系统结构图"></a>
<figcaption>archify 架构图（浏览器检查截图，1440×900 浅色）。交互版：<a href="https://claude.ai/artifact/Q2GCRD2WtGwpZHH8LvTFfG">artifact Q2GCRD2W…</a>；源文件 <code>docs/refactor/diagrams/ic-opt-system-v030.architecture.json</code>，渲染 <code>ic-opt-system-v030.html</code>（不入仓库）。三个引导视图：一次优化运行、电磁器件联合优化（pcell → EMX → bind_nport → Spectre）、器件查询库。</figcaption></figure>
{table(["组件", "源码", "职责"], comp_rows)}
<div class="note">仓库内没有任何真实工艺数据：随包只带虚构的 <code>demo_6m</code> profile；N28 的 profile、<code>.proc</code> 与器件库都在仓库外（<code>IC_OPT_PROFILE_DIRS</code>、<code>&lt;ic-opt-library&gt;/n28/</code>）。</div>

<h2 id="s3">3 一次运行怎么流过系统</h2>
<div class="grid2">
<div class="card"><h4>① 定义</h4><p><code>spec.yaml</code> 一份写清问题：变量与网格（space）、目标与约束（objective）、仿真链（simulator / stages）、资源（threads / memory）。<code>spec.load</code> 校验后得到 <code>Spec</code>，其指纹决定运行存储里哪些观测算"同一个问题"。</p></div>
<div class="card"><h4>② 取点</h4><p><code>points.*</code> 给固定点、网格、Sobol；<code>opt.suggest</code> 让建议器（默认 OpenBox，可选 TuRBO）按已有观测提下一批点；<code>opt.optimize</code> 把建议与评估循环到预算用尽。</p></div>
<div class="card"><h4>③ 评估</h4><p><code>sim.evaluate</code> 调 <code>engine.run</code>：按 <code>site.yaml</code> 的 <code>HostLimits</code> 算并发，把每个点的阶段任务交给执行器；Spectre 链是 netlist → spectre/ocean → 标量；有器件的 spec 走 em_circuit 链 pcell → emx → bind_nport → spectre/ocean → extract，器件与电路一起评估；没有 testbench 的 spec 走 em_only 链 pcell → emx → measure（建库、器件表征）。超出资源包络直接拒绝（<code>EnvelopeError</code>）。</p></div>
<div class="card"><h4>④ 执行</h4><p>执行器在本地或 SSH 主机上以进程组跑命令，转发 Ctrl-C 与超时；远程文件系统边界由 ADR-0001 约束（控制端只看自己拿回来的产物）。</p></div>
<div class="card"><h4>⑤ 记录</h4><p><code>RunStore</code> 把观测追加进 <code>.icopt/</code>（观测表 + sims 产物 + 缓存），指纹变更后用 <code>ic-opt migrate-store</code> 迁移；<code>analyze.best</code> / <code>analyze.report</code> 读它出最优点与报告。</p></div>
<div class="card"><h4>⑥ 复用</h4><p>器件查询库把多个运行存储当数据源：<code>lib.load</code> 建数据集，<code>lib.query / suggest / region / densify</code> 用模型回答；<code>lib_signoff</code> 用真实 EMX 复核并把 ok 行回流。</p></div>
</div>

<h2 id="s4">4 源码包地图</h2>
<figure><img src="{
        b64(figs["packages"])
    }" alt="源码包规模柱状图"><figcaption>各包 Python 行数（<code>collect</code> 实测，不含 <code>__pycache__</code>）。pcell 几何库占源码一半以上，是本仓库最厚的一层。</figcaption></figure>
{table(["路径", "包", "文件", "行", "职责"], pk_rows)}
<h3>pcell 几何库的 30 个模块</h3>
{table(["分组", "模块"], pcell_rows)}
<h3>器件查询库的 {len(lib_mods)} 个模块</h3>
<p>{
        "、".join(f"<code>{esc(m)}</code>" for m in lib_mods)
    }。清单（manifest）定义分层、部件、维度与结果列及其建模方式（direct / ratio / resonance）；数据集从 sNp 按清单口径重算；每个结果列一个 StratumGP 或 ComposedGP（低频值 × 谐振因子 × 残差），留出校准让 2σ 区间覆盖 95%；域守卫按凸包判定；模型缓存落盘，线程预算来自 site.yaml。</p>

<h2 id="s5">5 块、配方与命令行</h2>
<h3>{len(f["blocks"])} 个可组合块（<code>ic-opt blocks</code> 实测）</h3>
{table(["块", "说明"], block_rows)}
<h3>{len(f["recipes"])} 个配方</h3>
{table(["配方", "第一行说明"], recipe_rows)}
<h3>{len(f["cli"])} 个命令行子命令</h3>
{table(["命令", "说明"], cli_rows)}

<h2 id="s6">6 器件查询库数据流（archify 数据流图）</h2>
<figure><a href="https://claude.ai/artifact/T1NBTvFvkTtoEstp7ZvMqA"><img src="{
        b64(flow_png)
    }" alt="器件查询库数据流图"></a>
<figcaption>五个阶段：定义 → 生成与仿真 → 存储 → 建模 → 使用与回流。交互版：<a href="https://claude.ai/artifact/T1NBTvFvkTtoEstp7ZvMqA">artifact T1NBTvFv…</a>；源文件 <code>docs/refactor/diagrams/device-library-pipeline.dataflow.json</code>。</figcaption></figure>
<h3>N28 库现状（仓库外 <code>&lt;ic-opt-library&gt;/n28/</code>，<code>lib.load</code> 实测）</h3>
{
        table(["表（stratum）", "ok 行", "部件（part）与行数", "存储值复现 / 不一致"], lib_rows)
        if lib_rows
        else "<p>未附 library_rows.json。</p>"
    }
<p>最近一次变化是 B-12：60 个 <code>lib.densify</code> 选点经真实 EMX 回流进 <code>xfm_bs_ap</code>（1581 → 1641 行），10 个独立测试点的中位误差 9 列全降或持平（页面 <code>XFM_DENSIFY_B12_CN.html</code>）。未采样区域的误差是系统性的，留出校准估不出来，下一轮补点（N-17）待批准。</p>

<h2 id="s7">7 知识图谱（graphify）</h2>
<p class="lead">{g["files"]} 个文件（代码 217、文档 50）经 AST 结构抽取加 14 个语义抽取分块，合成 {
        g["nodes"]:,} 个节点、{g["edges"]:,} 条边、{g["communities"]} 个社区。边的来源：{
        esc(conf_txt)
    }；关系类型前几位：{esc(rel_txt)}。</p>
<figure><img src="{
        b64(figs["areas"])
    }" alt="图谱节点按区域分布"><figcaption>节点按来源文件所在区域计数。测试与 pcell 几何库两块最大，说明这两处既是代码最厚也是知识最密的地方。</figcaption></figure>
<h3>最大的 30 个社区</h3>
<figure><img src="{
        b64(figs["communities"])
    }" alt="最大的 30 个社区"><figcaption>社区由 Leiden 聚类得到，标签为本次手写（281 个社区中 232 个手写，其余按最强节点命名）。</figcaption></figure>
{table(["社区", "标签", "节点", "所在区域", "主要文件"], comm_rows)}
<h3>枢纽节点（连边最多的抽象）</h3>
{table(["节点", "边数", "它为什么是枢纽"], god_rows)}
<h3>跨社区的意外连接（图谱认为值得核对的推断边）</h3>
{table(["连接", "关系", "来源", "文件"], sur_rows)}
<h3>超边（多节点共同构成的概念，节选）</h3>
{table(["概念", "节点数", "置信"], hyper_rows)}
<h3>图谱建议追问的问题</h3>
<ul>{
        "".join(f"<li>{esc(q if len(q) <= 240 else q[:240] + ' …')}</li>" for q in g["questions"])
    }</ul>
<p>完整报告：<code>graphify-out/GRAPH_REPORT.md</code>（281 个社区逐一列出）；交互图 <code>graphify-out/graph.html</code>（超过 5000 节点，导出为社区聚合视图：281 个社区节点、873 条跨社区边）；原始数据 <code>graphify-out/graph.json</code>。<code>graphify-out/</code> 不入仓库。</p>

<h2 id="s8">8 文档与报告体系</h2>
<h3>面向使用者</h3>
{table(["文件", "行"], core_rows)}
<h3>重构计划与记录（<code>docs/refactor/</code>）</h3>
{table(["文件", "标题", "行"], plan_rows)}
<h3>分析文档（<code>docs/refactor/analysis/</code>，重构前对旧工程的评审）</h3>
{table(["文件", "行"], analysis_rows)}
<h3>报告页（<code>docs/refactor/reports/INDEX_CN.md</code> 登记的 {len(f["report_pages"])} 页）</h3>
{table(["页面", "artifact", "备注"], page_rows)}

<h2 id="s9">9 测试体系</h2>
<p>{tot["test_files"]} 个测试文件、{
        tot["test_functions"]
    } 个测试函数。pcell 回归测试（<code>tests/ic_opt/pcell/</code>）与主测试目录各占一半；查询库有 13 个 <code>test_library_*.py</code>；引擎、执行器、存储、锁、迁移、打包各有专门文件。开发约定是只跑定向测试（改哪里跑哪里），提交前不做全量。</p>
{table(["目录", "文件", "行"], tdir_rows)}

<h2 id="s10">10 当前状态与待办</h2>
<ul>
<li><b>已发布</b>：0.3.0（标签 <code>v0.3.0</code>），T16 七个波次全部合入；B-12 真实加密批完成并回流。</li>
<li><b>推送</b>：2026-09-25 由 Claude 推送 <code>origin/main</code>（1128c7f → d512977，58 次提交）与标签 <code>v0.3.0</code>；今后推送由 Claude 负责（RT-5 的 GitHub Release 页仍由用户操作，发布说明用 <code>RELEASE_NOTES_v0.3.0.md</code>）。</li>
<li><b>用户推迟</b>：B-7 … B-11（脱敏后续）与 0.4.0；B-3 维持 TuRBO 方案 b。</li>
<li><b>已做</b>：N-17（中心偏移 8–24 µm、外径比 0.8–1.25 一带，xfm_bs_ap 与 xfm_bs_m10 各 60 选点 + 10 测试点，140/140 EMX 成功并回流；页 <code>reports/library_query/XFM_DENSIFY_N17_CN.html</code>）；N-14；N-16（多圈表 Lp/Ls 改比值 + 无量纲坐标，页 <code>XFM_MS_RATIO_ACCEPTANCE_CN.html</code>）。</li>
<li><b>已做</b>：N-19（单圈表 Q@40 列：角落定向补点两表各 25 行 + Q 列改为 Q 峰值 × 比值建模，页 <code>XFM_DENSIFY_CORNER_CN.html</code>、<code>XFM_BS_Q_RATIO_ACCEPTANCE_CN.html</code>；只换坐标的方案在偏移带过于自信，未采用）。N-18（一次 <code>uv run</code> 把 .venv 装坏）已按文档安装命令恢复并关闭：是操作失误，不是安装法的问题。</li>
<li><b>进行中</b>：N-15（控制端改为用户的 Windows / macOS 笔记本，本机即仿真服务器；验收包 <code>&lt;EDA_AI_AGENT&gt;/ic-opt-accept/n15/</code>，等两份笔记本报告回传后核对）。</li>
</ul>
<p>待办总表：<code>docs/refactor/BACKLOG_CN.md</code>；执行记录：<code>docs/refactor/EXECUTION_PLAN_CN.md</code>。</p>

<h2 id="s11">11 本次产物与方法</h2>
{
        table(
            ["产物", "位置"],
            [
                [
                    "本页",
                    f"<span class='path'>{esc(str(OUT))}</span>（生成脚本 <code>build_structure_report.py</code>，数据 <code>structure_facts.json</code>、<code>library_rows.json</code>，图 <code>figs/</code>）",
                ],
                [
                    "系统结构图",
                    "<span class='path'>/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-modular/docs/refactor/diagrams/ic-opt-system-v030.html</span>（源 <code>.architecture.json</code>，浏览器检查 <code>.visual-check.*</code>）",
                ],
                [
                    "器件查询库数据流图",
                    "<span class='path'>/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-modular/docs/refactor/diagrams/device-library-pipeline.html</span>（源 <code>.dataflow.json</code>）",
                ],
                [
                    "知识图谱",
                    "<span class='path'>/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-modular/graphify-out/</span>：<code>graph.html</code>、<code>GRAPH_REPORT.md</code>、<code>graph.json</code>、<code>manifest.json</code>、<code>cost.json</code>",
                ],
                [
                    "B-12 加密批页面",
                    "<span class='path'>/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-modular/docs/refactor/reports/library_query/XFM_DENSIFY_B12_CN.html</span>",
                ],
            ],
        )
    }
<h3>方法</h3>
<ul>
<li><b>archify</b>：两张图各自 <code>validate --quality showcase</code>（9/9 项、0 错 0 警告）后 <code>deliver</code>，再用 Chromium 做 <code>visual-check</code>（1440×900 与 2048×1320，浅色 / 深色，无横向溢出）。桌面可读性按"投影字号 ≥ 6 px"约束过标签与布局。</li>
<li><b>graphify</b>：<code>.graphifyignore</code> 排除 vendor、报告 JSON / 日志、渲染产物；AST 抽取 4,508 节点；语义抽取分 14 块（前几次长中文文档超出子代理输出上限，改为按文件拆块并限定 40 节点 / 80 边）；合并、Leiden 聚类、手写社区标签、导出。已记录 token：输入 {
        g["input_tokens"]:,}、输出 {g["output_tokens"]:,}（{esc(g["cost_note"])}）。</li>
<li><b>事实核对</b>：包规模、块清单、命令行子命令、提交与标签均由 <code>collect</code> 从仓库与 <code>.venv/bin/ic-opt</code> 实测；库行数由 <code>ic-opt call lib.load</code> 实测。</li>
</ul>
</main>
</body>
</html>
"""
    OUT.write_text(doc, encoding="utf-8")
    print(f"page -> {OUT} ({OUT.stat().st_size / 1024:.0f} KB)")


GOD_NOTES = {
    "Spec": "一份 spec.yaml 的模型，块、引擎、阶段、存储、查询库都从它取问题定义",
    "RunStore": "运行存储，所有观测的读写口；引擎、块、配方、查询库、迁移都经过它",
    "Point": "网格上的一个点，取点、评估、观测、库行都用它做键",
    "FakeSpectreExecutor": "测试假件里的执行器，几乎所有引擎 / 配方 / 库测试都靠它免真实仿真",
    "HostLimits": "site.yaml 的主机限额，引擎并发、库线程预算、命令行都由它推导",
    "_load_plugin()": "pcell 插件加载入口，干净端口生成器的大量测试都从它进入",
    "StageFailure": "阶段失败的统一异常，EM 链与 Spectre 链所有失败路径都汇到它",
    "PortError": "pcell 端口契约的异常，六族生成器与端口审计共用",
    "Deck": "网表 deck 模型，网表导入、Spectre 链与存储都引用",
    "StageContext": "阶段执行上下文，每个阶段拿资源、路径与执行器的地方",
}

if __name__ == "__main__":
    {"collect": collect, "page": page}[sys.argv[1]]()
