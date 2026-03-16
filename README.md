# A/H 相对价值研究平台

这是一个面向同一发行人 A/H 标的的相对价值研究框架。默认执行模式是 `long_cheaper_leg_only`，假设港股的交易通路是港股通；`paired` 仅作为研究模式保留，测试因子有效性，需要可做空环境。

## 适用与边界

- 适合做同发行人 A/H 相对价值研究、信号筛选和成本敏感型回测。
- 默认模式是 `long_cheaper_leg_only`，把相对价值信号交易信号。
- 如果你的账户既不能做空，也不能买入 H 股，那么默认模式也只能部分执行。
- 如果需要多空，请显式切到 `--execution-mode paired`；该模式会做空其中一个标的。

## 安装

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

如果你本地使用 `uv`：

```bash
uv sync
```

## 目录约定

- `data/` 保留给手工维护或外部导入的输入文件，例如 `data/fx/*.csv`
- `artifacts/cache/ah_pairs_trading/` 保存自动数据缓存和可选 stage cache
- `artifacts/runs/<run-name>/` 保存每次研究运行产出的 CSV、图表和摘要
- `configs/` 保存固定实验参数模板；当前先作为预设清单，后续 CLI 会支持 `--config`

## 30 秒快速开始

### 1. Smoke test

这条命令适合第一次确认环境、缓存和输出目录都正常：

```bash
pairs-trading \
  --a-symbol 601857 \
  --h-symbol 00857 \
  --constant-fx-rate 0.92 \
  --allow-non-coint \
  --output-dir artifacts/runs/petrochina_ah_smoke
```

说明：

- A/H 历史会由 AkShare 在线拉取，并在 `artifacts/cache/ah_pairs_trading` 下维护按 symbol 的增量主档缓存
- 后续扩大回测窗口时，缓存会优先复用已有覆盖区间，只补抓左侧或右侧缺口，并更新旁边的 JSON manifest
- 运行结果建议统一写到 `artifacts/runs/<run-name>`
- `--constant-fx-rate` 只适合快速验证或原型测试
- `--allow-non-coint` 只适合 smoke test 或宽松探索
- 没有显式传 `--execution-mode` 时，默认是 `long_cheaper_leg_only`
- 对应的固定实验模板见 `configs/petrochina_smoke.toml`

如果你还没安装 CLI，也可以继续用：

```bash
python -m ah_pairs_trading ...
```

### 2. Research run

更严肃的研究口径更推荐提供真实 FX 历史，并保留协整显著性 guardrail：

先生成项目可直接读取的 `HKD/CNY` 历史 CSV：

```bash
python scripts/fetch_fx_history.py \
  --start-date 2018-01-01 \
  --end-date 2024-12-31 \
  --output-csv data/fx/hkdcny_2018_2024.csv
```

再把这份 CSV 喂给主回测：

```bash
pairs-trading \
  --a-symbol 601857 \
  --h-symbol 00857 \
  --fx-csv data/fx/hkdcny_2018_2024.csv \
  --output-dir artifacts/runs/petrochina_ah_research
```

说明：

- 不加 `--allow-non-coint` 时，训练集协整不显著会直接中止
- 正式回测更推荐 `--fx-csv`，而不是 `--constant-fx-rate`
- `scripts/fetch_fx_history.py` 会通过 Frankfurter 拉取 ECB-backed 参考汇率，并输出项目兼容的 `date,fx_rate,...` CSV
- 生成的 FX CSV 仍建议放在 `data/fx/`，因为它属于用户可复现输入，而不是运行产物
- 主回测 CLI 仍然不会自动联网拉 FX；这样做是为了保持输入可复现
- 对应的固定实验模板见 `configs/petrochina_research.toml`

## 执行模式

### `long_cheaper_leg_only`

- `z > 0` 时只买 H 股，`z < 0` 时只买 A 股
- 适合不能做空的账户
- 会混入单边市场、行业和风格暴露
- 在 `--benchmark-mode auto` 下会自动生成内部 A/H 被动 basket benchmark

### `paired`

- 同时持有两条腿
- 会真实做空一条腿
- 仅适用于允许做空或融券的环境
- `--benchmark-mode auto` 下不会自动生成内部 benchmark

## 文档导航

- 策略逻辑与边界：[`docs/strategy_overview.md`](docs/strategy_overview.md)
- 执行模式说明：[`docs/execution_modes.md`](docs/execution_modes.md)
- 数据、FX 与缓存：[`docs/data_and_fx.md`](docs/data_and_fx.md)
- Benchmark 与结果解读：[`docs/benchmark_and_metrics.md`](docs/benchmark_and_metrics.md)
- CLI 参数参考：[`docs/cli_reference.md`](docs/cli_reference.md)
- 常见报错：[`docs/common_errors.md`](docs/common_errors.md)
- 历史设计稿：[`docs/legacy/project_design_v1.0.md`](docs/legacy/project_design_v1.0.md)

## 最常见的坑

- `--allow-non-coint` 只是为了跑通流程或做宽松探索，不是研究默认值。
- `--constant-fx-rate` 只是为了快速原型，不是正式回测默认值。
- `--same-issuer-check strict` 会拦住内置 registry 里已知的跨发行人错配，例如 `601857/00883`。

## 当前实现概览

- 先对齐 A/H/FX，并把 H 股价格转换成人民币口径
- 用训练集估计截距和 `hedge_ratio`
- 用全样本滚动 z-score 生成信号
- 也支持把 `ret_spread` 的 `EMA/SMA` 信号作为主模型，或作为 `z-score` 的入场过滤器
- 在训练集上搜索最佳入场阈值，再分别回测训练集和测试集
- 提供独立 FX 下载脚本，把 Frankfurter 的 ECB-backed 历史汇率落成本地 `fx.csv`
- 输出 scorecard、CSV、图表和结构化摘要

## 说明

- 这个仓库当前主要用于相对价值研究，可做空的市场中性策略目前仍停留在概念阶段。
- `analysis.py` 中的 ECM、半衰期、滚动协整、矩阵 OLS、VAR 稳定性诊断仍然保留，但它们当前属于诊断层，不直接驱动交易执行。
