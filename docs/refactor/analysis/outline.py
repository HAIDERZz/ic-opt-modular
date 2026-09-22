"""Print public outline (top-level classes/functions with signature + size) of given modules."""
import ast, sys
from pathlib import Path
for f in sys.argv[1:]:
    p = Path(f); src = p.read_text(encoding="utf-8"); tree = ast.parse(src)
    print(f"\n##### {p.name}  ({src.count(chr(10))+1} loc)")
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            size = node.end_lineno - node.lineno + 1
            if node.name.startswith("_") and size < 40: continue
            args = [a.arg for a in node.args.posonlyargs + node.args.args] + (["*"] if node.args.kwonlyargs else []) + [a.arg for a in node.args.kwonlyargs]
            ret = ast.unparse(node.returns) if node.returns else ""
            print(f"  def {node.name}({', '.join(args)}) -> {ret}   [{size}L @{node.lineno}]")
        elif isinstance(node, ast.ClassDef):
            size = node.end_lineno - node.lineno + 1
            bases = ",".join(ast.unparse(b) for b in node.bases)
            meths = [n.name for n in node.body if isinstance(n, (ast.FunctionDef,)) and not n.name.startswith("__")]
            fields = [n.target.id for n in node.body if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)]
            print(f"  class {node.name}({bases})   [{size}L @{node.lineno}]")
            if fields: print(f"      fields: {', '.join(fields[:30])}{' ...' if len(fields)>30 else ''}")
            if meths: print(f"      methods: {', '.join(meths[:30])}{' ...' if len(meths)>30 else ''}")
