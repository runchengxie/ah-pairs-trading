# A/H 相对价值研究平台

这是一个面向同一发行人 A/H 标的的相对价值研究框架。默认执行模式是 `long_cheaper_leg_only`，把相对价值信号转成可执行的单标的买入决策；`paired` 保留给允许做空环境下的双标的多空研究。

## 项目定位

- 适合做同发行人 A/H 相对价值研究、信号筛选和成本敏感型回测。
- 默认模式是 `long_cheaper_leg_only`，不是默认市场中性统计套利模板。
- 如果需要多空模式，请显式切到 `--execution-mode paired`；该模式会真实做空其中一条腿。
- 如果你的账户既不能做空，也不能买入 H 股，那么默认模式也只能部分执行。

## 安装

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

如果你本地使用 `uv`：

```bash
uv sync --extra dev
```

## 目录约定

- `data/` 保留给手工维护或外部导入的输入文件，例如 `data/fx/*.csv`
- `artifacts/cache/ah_pairs_trading/` 保存自动数据缓存和可选 stage cache
- `artifacts/runs/<run-name>/` 保存每次研究运行产出的 CSV、图表和摘要
- `configs/` 保存可直接传给 `--config` 的 TOML 预设；显式 CLI 参数会覆盖文件里的同名键，预设里的相对路径会相对 TOML 文件自身解析

## 30 秒快速开始

### 1. 测试运行

第一次确认环境、缓存和输出目录时，直接跑：

```bash
pairs-trading --config configs/petrochina_smoke.toml
```

如果你想临时改输出目录或其他参数，可以在 `--config` 后继续覆写：

```bash
pairs-trading \
  --config configs/petrochina_smoke.toml \
  --output-dir artifacts/runs/petrochina_ah_smoke_local
```

说明：

- `--constant-fx-rate` 只适合 smoke test、环境检查或快速原型。
- `--allow-non-coint` 只适合跑通流程或宽松探索，不是研究默认值。
- 没有显式传 `--execution-mode` 时，默认是 `long_cheaper_leg_only`。
- A/H 历史会由 AkShare 在线拉取，并在 `artifacts/cache/ah_pairs_trading/` 下维护按 symbol 的增量主档缓存。

### 2. 研究模式

更严肃的研究口径更推荐先生成真实 FX 历史，再跑主回测：

```bash
python scripts/fetch_fx_history.py \
  --start-date 2018-01-01 \
  --end-date 2024-12-31 \
  --output-csv data/fx/hkdcny_2018_2024.csv
```

```bash
pairs-trading --config configs/petrochina_research.toml
```

如果你想保留真实 FX 和研究期参数，但不希望因为训练集协整不显著直接中止，可以改用：

```bash
pairs-trading --config configs/petrochina_exploratory.toml
```

说明：

- 正式回测更推荐 `--fx-csv`，而不是 `--constant-fx-rate`。
- `configs/petrochina_research.toml` 是严格版；训练集协整不显著会直接中止。
- `configs/petrochina_exploratory.toml` 只额外打开 `allow_non_coint = true`，适合宽松探索，不适合正式结论。
- `pairs-trading --config ... --some-flag ...` 的优先级是：内置默认值 < TOML 预设 < 显式 CLI 参数。
- 如果你还没安装 CLI，也可以继续用 `python -m ah_pairs_trading --config configs/petrochina_research.toml`。

## 测试

标准入口是 `scripts/test.sh`：

```bash
bash scripts/test.sh
bash scripts/test.sh unit
bash scripts/test.sh integration
bash scripts/test.sh coverage
```

说明：

- 默认模式会跑完整 pytest 套件。
- `unit` 会跳过标记为 `integration` 的端到端 pipeline 测试。
- `integration` 只跑端到端 pipeline 测试。
- `coverage` 会生成终端覆盖率摘要，并把 XML 报告写到 `artifacts/test/coverage.xml`。
- 如果 `coverage` 提示缺少 `pytest-cov`，先重新同步开发依赖：`pip install -e ".[dev]"` 或 `uv sync --extra dev`。
- README 里的核心命令和文档导航现在有契约测试，避免示例命令静默漂移。

## 最常见的坑

- `--allow-non-coint` 只是为了跑通流程或做宽松探索，不是研究默认值。
- `--constant-fx-rate` 只是为了快速原型，不是正式回测默认值。
- `--same-issuer-check strict` 会拦住内置 registry 里已知的跨发行人错配，例如 `601857/00883`。
- `--config` 负责加载 TOML 预设，但显式 CLI 参数永远优先。

## 文档导航

- 完整使用手册：[`docs/cookbook_runbook.md`](docs/cookbook_runbook.md)
- 按研究目标分类的剧本：[`docs/research_playbooks.md`](docs/research_playbooks.md)
- 策略逻辑与边界：[`docs/strategy_overview.md`](docs/strategy_overview.md)
- 执行模式说明：[`docs/execution_modes.md`](docs/execution_modes.md)
- 数据、FX 与缓存：[`docs/data_and_fx.md`](docs/data_and_fx.md)
- Benchmark 与结果解读：[`docs/benchmark_and_metrics.md`](docs/benchmark_and_metrics.md)
- CLI 参数参考：[`docs/cli_reference.md`](docs/cli_reference.md)
- 常见报错：[`docs/common_errors.md`](docs/common_errors.md)
- 开发与测试：[`docs/development.md`](docs/development.md)
- 历史设计稿：[`docs/legacy/project_design_v1.0.md`](docs/legacy/project_design_v1.0.md)

更完整的执行模式、benchmark、数据口径和参数说明都放在 `docs/`，README 只保留最短上手路径和最高频限制。
