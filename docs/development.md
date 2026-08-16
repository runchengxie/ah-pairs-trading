# 开发与测试

## 安装开发依赖

如果你用 `pip`：

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

如果你用 `uv`：

```bash
uv sync --extra dev
```

## 标准测试入口

项目统一通过 `scripts/test.sh` 调 pytest：

```bash
bash scripts/test.sh
bash scripts/test.sh unit
bash scripts/test.sh integration
bash scripts/test.sh coverage
```

模式说明：

- `all`，完整 pytest 套件，也是默认模式
- `unit`，跳过标记为 `integration` 的测试
- `integration`，只跑端到端 pipeline 测试
- `coverage`，跑完整套件并输出覆盖率，XML 报告会写到 `artifacts/test/coverage.xml`。如果本地还没装 `pytest-cov`，脚本会直接给出安装提示

脚本会优先复用项目自己的 `.venv/bin/python`。如果本地没有项目虚拟环境，再回退到 `uv run --extra dev pytest ...`，最后才使用系统 `python -m pytest`。

## 测试覆盖

测试按模块拆分：

- `tests/test_analysis.py`，协整、ECM、半衰期、VAR 诊断
- `tests/test_ou.py`，滚动 OU MLE 与均值回归 gate
- `tests/test_strategy.py`，逐笔回测、gate、波动率目标与回撤熔断
- `tests/test_weight_strategy.py`，权重引擎与基准
- `tests/test_data.py`，数据加载、FX、缓存与三种数据源
- `tests/test_pipeline.py`，端到端 pipeline
- `tests/test_cli.py`，CLI 参数解析
- `tests/test_docs_contract.py`，文档契约

`simulated` 数据源让这些测试可以完全离线运行，不需要真实网络。

## 文档契约测试

仓库有一组轻量契约测试，专门盯住最容易漂移的高频入口：

- README 里的 `pairs-trading --config ...` 示例
- README 里的 `scripts/test.sh` 标准入口
- README 文档导航里引用的页面
- `python scripts/fetch_fx_history.py ...` 的核心参数形式

这样做的目标是确保高频命令和导航路径不会悄悄失效。

## Warnings 策略

pytest 默认把 warnings 当成失败处理，只对白名单里的已知 `statsmodels` 噪音做忽略。这样后面如果引入新的 warning，测试会尽早暴露问题，而不是把它埋在通过结果后面。

## `--config` 的使用建议

`pairs-trading --config path/to/preset.toml` 已经支持。当前实现遵循三层优先级：

- 内置默认值
- `--config` TOML 预设
- 显式 CLI 参数

也就是说，TOML 适合保存稳定实验参数，而一次性微调仍然建议直接加在命令行里。

## 代码结构

- `src/ah_pairs_trading/ou.py`，滚动 OU MLE、Johansen beta、Ljung-Box 检验
- `src/ah_pairs_trading/weight_strategy.py`，权重引擎与基准
- `src/ah_pairs_trading/strategy.py`，逐笔持仓回测引擎
- `src/ah_pairs_trading/analysis.py`，协整、ECM、半衰期、VAR 诊断
- `src/ah_pairs_trading/data.py`，数据加载、FX 与三种数据源
- `src/ah_pairs_trading/pipeline.py`，端到端编排
- `src/ah_pairs_trading/cli.py`，命令行入口

## 修改约定

- 修改公开函数或 CLI 参数名时，同步更新 `README.md`、`docs/` 和契约测试。
- 新增数据源或回测引擎时，补充离线可运行的测试。
- 提交前运行 `uv run pytest -q` 确认全部通过。
