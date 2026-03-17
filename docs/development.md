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

- `all`：完整 pytest 套件，也是默认模式
- `unit`：跳过标记为 `integration` 的测试
- `integration`：只跑端到端 pipeline 测试
- `coverage`：跑完整套件并输出覆盖率，XML 报告会写到 `artifacts/test/coverage.xml`；如果本地还没装 `pytest-cov`，脚本会直接给出安装提示

脚本会优先复用项目自己的 `.venv/bin/python`；如果本地没有项目虚拟环境，再回退到 `uv run --extra dev pytest ...`，最后才使用系统 `python -m pytest`。

## 文档契约测试

仓库现在有一组轻量契约测试，专门盯住最容易漂移的高频入口：

- README 里的 `pairs-trading --config ...` 示例
- README 里的 `scripts/test.sh` 标准入口
- README 文档导航里引用的页面
- `python scripts/fetch_fx_history.py ...` 的核心参数形式

这样做的目标不是测试文案，而是确保高频命令和导航路径不会悄悄失效。

## Warnings 策略

pytest 现在默认把 warnings 当成失败处理，只对白名单里的已知 `statsmodels` 噪音做忽略。这样后面如果引入新的 warning，测试会尽早暴露问题，而不是把它埋在通过结果后面。

## `--config` 的使用建议

`pairs-trading --config path/to/preset.toml` 现在已经支持。当前实现遵循三层优先级：

- 内置默认值
- `--config` TOML 预设
- 显式 CLI 参数

也就是说，TOML 适合保存稳定实验参数，而一次性微调仍然建议直接加在命令行里。
