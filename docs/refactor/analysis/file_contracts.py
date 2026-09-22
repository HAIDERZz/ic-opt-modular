"""Map project-dir file-contract artifacts (string literals that look like project-relative paths) to modules."""
import ast, re, sys
from pathlib import Path
from collections import defaultdict
pkg = Path(sys.argv[1]) / "src" / "hermes_workflow"
pat = re.compile(r'^(reports|state|ledger|config|runs|execution_package|netlists|history|logs)/[\w./{}\-<>*]+$|^[\w\-]+\.(json|yaml|csv|md|html|jsonl)$')
art2mod = defaultdict(set)
for p in sorted(pkg.rglob("*.py")):
    tree = ast.parse(p.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            s = node.value.strip()
            if pat.match(s) and len(s) < 90:
                art2mod[s].add(p.stem)
groups = defaultdict(list)
for art, modsx in art2mod.items():
    top = art.split("/")[0] if "/" in art else "(bare)"
    groups[top].append((art, sorted(modsx)))
for top in ["config", "state", "ledger", "reports", "runs", "execution_package", "netlists", "history", "(bare)"]:
    items = sorted(groups.get(top, []))
    print(f"\n=== {top}  ({len(items)} artifacts) ===")
    for art, m in items:
        print(f"  {art:62s} <- {len(m):2d}: {', '.join(m[:7])}{' ...' if len(m)>7 else ''}")
