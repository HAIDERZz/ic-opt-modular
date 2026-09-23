"""Render T13_LIBRARY_MODULE_PLAN_CN.md as the review page, with the archify architecture and plan diagrams embedded.

Usage: t13_plan_page.py PLAN_MD ARCH_DIR OUT_HTML
The diagrams are the archify visual-check screenshots (1440x900, light); the interactive HTML sits next to them in ARCH_DIR.
"""
from __future__ import annotations

import base64
import html
import re
import sys
from pathlib import Path

plan_md, arch_dir, out = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
FIGURES = {  # heading prefix -> (screenshot, interactive html, caption)
    "2. 模块设计": ("t13-library-module.architecture.visual-check.1440x900.light.png", "t13-library-module.architecture.html",
                "模块架构（archify）：主路径是正向查询；左上是用户提供的工艺文件与 validate_profile，上方是实造与代理流水线，下方是 EMX 复核与回流；库与清单在仓库外。"),
    "4. 任务分解": ("t13-dev-plan.workflow.visual-check.1440x900.light.png", "t13-dev-plan.workflow.html",
                "开发方案（archify）：查询主线 T13.1 → T13.6，两道离线金标准门（G1、G2）与一道确认门（真实 EMX）；第三条泳道是工艺接入 T13.11 → T13.10 → T13.9 与扩展。"),
}


def inline(text: str) -> str:
    s = html.escape(text)
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    return re.sub(r"`([^`]+)`", r"<code>\1</code>", s)


def figure(heading: str) -> str:
    for prefix, (png, live, caption) in FIGURES.items():
        if heading.startswith(prefix):
            data = base64.b64encode((arch_dir / png).read_bytes()).decode()
            return (f"<figure><img src='data:image/png;base64,{data}' alt='{html.escape(caption)}'>"
                    f"<figcaption>{html.escape(caption)} 可交互版本：<code>{html.escape(str((arch_dir / live).resolve()))}</code></figcaption></figure>")
    return ""


def md_to_html(text: str) -> tuple[str, str]:
    lines, out_parts, title, i = text.splitlines(), [], "", 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("```"):
            j = i + 1
            while j < len(lines) and not lines[j].startswith("```"):
                j += 1
            out_parts.append("<pre><code>" + html.escape("\n".join(lines[i + 1:j])) + "</code></pre>")
            i = j + 1
            continue
        m = re.match(r"^(#{1,3}) (.*)$", line)
        if m:
            level, body = len(m.group(1)), m.group(2)
            if level == 1:
                title = body
            else:
                out_parts.append(f"<h{level}>{inline(body)}</h{level}>" + (figure(body) if level == 2 else ""))
            i += 1
            continue
        if line.startswith("|"):
            block = []
            while i < len(lines) and lines[i].startswith("|"):
                block.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            head, body = block[0], [r for r in block[1:] if not all(set(c) <= {"-", " ", ":"} for c in r)]
            out_parts.append("<div class='table-wrap'><table><thead><tr>" + "".join(f"<th>{inline(c)}</th>" for c in head) + "</tr></thead><tbody>"
                             + "".join("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in r) + "</tr>" for r in body) + "</tbody></table></div>")
            continue
        if re.match(r"^(- |\d+\. )", line):
            ordered = bool(re.match(r"^\d+\. ", line))
            items = []
            while i < len(lines) and re.match(r"^(- |\d+\. )", lines[i]):
                items.append(re.sub(r"^(- |\d+\. )", "", lines[i]))
                i += 1
            tag = "ol" if ordered else "ul"
            out_parts.append(f"<{tag}>" + "".join(f"<li>{inline(it)}</li>" for it in items) + f"</{tag}>")
            continue
        if line.strip():
            para = [line]
            i += 1
            while i < len(lines) and lines[i].strip() and not re.match(r"^(#|\||- |\d+\. |```)", lines[i]):
                para.append(lines[i])
                i += 1
            out_parts.append(f"<p>{inline(' '.join(para))}</p>")
            continue
        i += 1
    return title, "".join(out_parts)


title, body = md_to_html(plan_md.read_text(encoding="utf-8"))
page = f"""<title>T13 查询库嵌入方案</title>
<style>
:root {{ --ground:#f2f3f5; --surface:#fff; --surface-2:#e5e8ee; --ink:#171a21; --muted:#5a6070; --line:#cfd4de; --accent:#3057a8; --code-bg:#e8ebf1;
  --sans:"Noto Sans SC","PingFang SC","Microsoft YaHei",sans-serif; --mono:"IBM Plex Mono",Menlo,Consolas,monospace; }}
@media (prefers-color-scheme: dark) {{ :root:not([data-theme="light"]) {{ --ground:#12141a; --surface:#1b1e26; --surface-2:#242833; --ink:#e4e7ee; --muted:#9aa1b3; --line:#343a48; --accent:#8fb0f0; --code-bg:#232733; }} }}
:root[data-theme="dark"] {{ --ground:#12141a; --surface:#1b1e26; --surface-2:#242833; --ink:#e4e7ee; --muted:#9aa1b3; --line:#343a48; --accent:#8fb0f0; --code-bg:#232733; }}
body {{ background:var(--ground); color:var(--ink); font-family:var(--sans); font-size:15px; line-height:1.72; margin:0; padding-block:0 64px; padding-inline:16px; }}
.wrap {{ max-width:1080px; margin:0 auto; }}
header {{ padding-block:36px 16px; border-bottom:2px solid var(--accent); margin-bottom:8px; }}
.eyebrow {{ font-family:var(--mono); font-size:12px; letter-spacing:.08em; text-transform:uppercase; color:var(--muted); }}
h1 {{ font-size:30px; margin:8px 0 6px; text-wrap:balance; }}
h2 {{ font-size:21px; margin:36px 0 10px; text-wrap:balance; }}
h3 {{ font-size:16.5px; margin:22px 0 8px; }}
p, li {{ max-width:86ch; }}
p {{ margin:0 0 12px; }}
ul, ol {{ padding-left:22px; margin:0 0 12px; }}
code {{ font-family:var(--mono); font-size:.86em; background:var(--code-bg); padding:1px 5px; border-radius:3px; }}
pre {{ background:var(--code-bg); border:1px solid var(--line); border-radius:6px; padding:12px 14px; overflow-x:auto; font-size:12.5px; line-height:1.55; }}
pre code {{ background:none; padding:0; }}
.table-wrap {{ overflow-x:auto; border:1px solid var(--line); border-radius:6px; background:var(--surface); margin:0 0 14px; }}
table {{ border-collapse:collapse; width:100%; font-size:13.5px; }}
th, td {{ text-align:left; padding:7px 11px; border-bottom:1px solid var(--line); vertical-align:top; }}
th {{ background:var(--surface-2); font-weight:500; white-space:nowrap; }}
figure {{ margin:4px 0 16px; background:var(--surface); border:1px solid var(--line); border-radius:6px; padding:8px; }}
figure img {{ width:100%; max-width:100%; height:auto; display:block; border-radius:4px; }}
figcaption {{ font-size:12.5px; color:var(--muted); margin-top:6px; }}
</style>
<div class="wrap">
<header><div class="eyebrow">ic-opt-modular · T13 · 已批准 · 执行中</div><h1>{inline(title)}</h1></header>
{body}
</div>
"""
out.write_text(page, encoding="utf-8")
print(out, f"{out.stat().st_size / 1e6:.2f} MB")
