# 证据脚本

运行方式（新开发区没有 venv，借旧 dev 仓库的 venv，`PYTHONPATH` 指向本树）：

```bash
cd <repo>
VENV=../ic-auto-opt-workflow/.venv/bin/python

# 包内 import 分层 / 扇入扇出 / 环
python3.11 docs/refactor/analysis/import_graph.py . docs/refactor/analysis/import_graph.json

# 111 个文件契约产物名 -> 引用模块
python3.11 docs/refactor/analysis/file_contracts.py .

# 缺陷复现：native_turbo.py:1765 只检查最后一个 corner 的返回码（1 failed = 缺陷成立）
PYTHONPATH=src:. $VENV -m pytest docs/refactor/analysis/test_repro_corner_rc_check.py -q -p no:cacheprovider --rootdir=. -o testpaths=

# 单个候选点评估期间全套配置的重载次数（黑板模式证据）
PYTHONPATH=src:. $VENV -W ignore docs/refactor/analysis/count_config_reloads.py tt,ss,ff
```

结果记录见 `../REFACTOR_PLAN_CN.md` 1.2 节 P5 与附录 B。
