"""Intra-package import graph for hermes_workflow: fan-in/out, layers, cycles."""
import ast, sys, json
from pathlib import Path
from collections import defaultdict

root = Path(sys.argv[1])
pkg = root / "src" / "hermes_workflow"
mods = {}
for p in pkg.rglob("*.py"):
    rel = p.relative_to(pkg).with_suffix("")
    name = ".".join(rel.parts)
    if name.endswith("__init__"):
        name = name[: -len(".__init__")] if "." in name else "__init__"
    mods[name] = p

def resolve(cur, node):
    out = set()
    if isinstance(node, ast.ImportFrom):
        base = None
        if node.level:
            parts = cur.split(".")[: -node.level] if node.level <= len(cur.split(".")) else []
            # cur is module name relative to pkg; level 1 = same package dir
            cur_parts = cur.split(".")
            pkg_parts = cur_parts[:-1]
            up = node.level - 1
            pkg_parts = pkg_parts[: len(pkg_parts) - up] if up else pkg_parts
            base = ".".join(pkg_parts + ([node.module] if node.module else []))
        elif node.module and node.module.startswith("hermes_workflow"):
            base = node.module[len("hermes_workflow"):].lstrip(".")
        if base is not None:
            if base in mods:
                out.add(base)
            for a in node.names:
                cand = f"{base}.{a.name}" if base else a.name
                if cand in mods:
                    out.add(cand)
    elif isinstance(node, ast.Import):
        for a in node.names:
            if a.name.startswith("hermes_workflow."):
                cand = a.name[len("hermes_workflow."):]
                if cand in mods:
                    out.add(cand)
    return out

deps = defaultdict(set)
loc = {}
lazy = defaultdict(set)
for name, p in mods.items():
    src = p.read_text(encoding="utf-8")
    loc[name] = src.count("\n") + 1
    tree = ast.parse(src)
    top = set()
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            top |= resolve(name, node)
        elif isinstance(node, ast.If):  # TYPE_CHECKING
            for n in ast.walk(node):
                if isinstance(n, (ast.Import, ast.ImportFrom)):
                    top |= resolve(name, n)
    allimp = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            allimp |= resolve(name, node)
    deps[name] = allimp - {name}
    lazy[name] = (allimp - top) - {name}

fanin = defaultdict(set)
for m, ds in deps.items():
    for d in ds:
        fanin[d].add(m)

# layering via longest path on condensed DAG (Tarjan SCC)
index = {}; low = {}; stack = []; onstack = set(); sccs = []; counter = [0]
sys.setrecursionlimit(10000)
def strong(v):
    index[v] = low[v] = counter[0]; counter[0] += 1
    stack.append(v); onstack.add(v)
    for w in deps[v]:
        if w not in index:
            strong(w); low[v] = min(low[v], low[w])
        elif w in onstack:
            low[v] = min(low[v], index[w])
    if low[v] == index[v]:
        comp = []
        while True:
            w = stack.pop(); onstack.discard(w); comp.append(w)
            if w == v: break
        sccs.append(comp)
for m in mods:
    if m not in index:
        strong(m)
comp_of = {m: i for i, c in enumerate(sccs) for m in c}
level = {}
def lvl(i):
    if i in level: return level[i]
    level[i] = 0
    best = 0
    for m in sccs[i]:
        for d in deps[m]:
            j = comp_of[d]
            if j != i:
                best = max(best, lvl(j) + 1)
    level[i] = best
    return best
for i in range(len(sccs)): lvl(i)

print("== cycles (SCC size>1) ==")
for c in sccs:
    if len(c) > 1: print("  ", sorted(c))
print("== modules by layer (0 = leaf) ==")
by = defaultdict(list)
for m in mods: by[level[comp_of[m]]].append(m)
for l in sorted(by):
    print(f"L{l}:")
    for m in sorted(by[l], key=lambda x: -loc[x]):
        print(f"    {m:45s} loc={loc[m]:5d} out={len(deps[m]):2d} in={len(fanin[m]):2d}" + (f" lazy={sorted(lazy[m])}" if lazy[m] else ""))
json.dump({"deps": {k: sorted(v) for k, v in deps.items()}, "fanin": {k: sorted(v) for k, v in fanin.items()}, "loc": loc, "level": {m: level[comp_of[m]] for m in mods}},
          open(Path(sys.argv[2]), "w"), indent=1)
