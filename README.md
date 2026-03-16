# A/H 相对价值研究平台

这个仓库已经从原来的美股双边配对 demo，改成了更贴近大陆居民账户约束的A/H 相对价值研究框架（也就是暂时不考虑不允许做空场景）。

主流程现在是：

- 自动拉取同一发行人的 A 股和 H 股历史
- 对内置 registry 里已知的 A/H 代码对做 `same issuer` 校验；像 `601857/00883` 这类已知错配会直接拦下
- 把 H 股价格转换到人民币口径
- 在训练期估计协整关系、截距和 hedge ratio
- 对残差做滚动标准化，生成 z-score
- 默认执行 `long_cheaper_leg_only`
- 只有显式切到 `paired` 时，才双腿同时持仓
- `long_cheaper_leg_only` 在 `--benchmark-mode auto` 下会自动生成内部 A/H 被动 basket benchmark
- 回测里显式计入 lot size、最大持有期和交易成本

## 先看推荐用法

如果你只是想先把流程跑通，推荐直接用安装后的 CLI：

```bash
pairs-trading \
  --a-symbol 601857 \
  --h-symbol 00857 \
  --constant-fx-rate 0.92 \
  --allow-non-coint \
  --output-dir outputs/petrochina_ah
```

这条命令的含义是：

- A/H 历史由 AkShare 在线拉取
- A/H 历史会自动写入并复用 `.cache/ah_pairs_trading`
- FX 不在线拉取，所以这里临时用 `--constant-fx-rate 0.92`
- 即使训练集协整不显著，也继续跑完整条 pipeline
- 没有显式传 `--execution-mode` 时，默认是 `long_cheaper_leg_only`
- 没有显式传 `--benchmark` 时，默认会为 `long_cheaper_leg_only` 生成内部 A/H 被动 benchmark

如果你要跑双腿配对版本，必须显式加上：

```bash
--execution-mode paired
```

也就是：

```bash
pairs-trading \
  --a-symbol 601857 \
  --h-symbol 00857 \
  --constant-fx-rate 0.92 \
  --execution-mode paired \
  --allow-non-coint \
  --output-dir outputs/petrochina_ah
```

如果你还没做 `pip install -e ".[dev]"`，也可以继续用：

```bash
python main.py ...
```

它和 `pairs-trading ...` 走的是同一套 CLI。

## 当前能力

- `data.py`
  负责 A 股、H 股、HKD/CNY 数据接入，按共同交易日对齐，并把 H 股价格转换为人民币口径。
- `analysis.py`
  保留协整、ECM、半衰期、滚动协整、矩阵 OLS、VAR 稳定性诊断。
- `strategy.py`
  支持 `long_cheaper_leg_only` 和 `paired` 两种执行模式，并支持显式成本参数。
- `pipeline.py`
  用训练集估计参数，在全样本上生成滚动 z-score，再分别输出训练集和测试集结果，并支持自动内部 benchmark 与 same-issuer 校验。
- `cli.py`
  提供 A/H、FX、benchmark、same-issuer 校验、缓存和执行模式参数。

## 安装

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

如果你本地用 `uv`：

```bash
uv sync
```

## 快速开始

### 1. 默认推荐入口：联网拉 A/H，FX 用常数

```bash
pairs-trading \
  --a-symbol 601857 \
  --h-symbol 00857 \
  --constant-fx-rate 0.92 \
  --allow-non-coint \
  --output-dir outputs/petrochina_ah
```

适合第一次确认环境、缓存和输出目录都正常。

### 2. 需要双腿同时持仓时，再显式切到 `paired`

```bash
pairs-trading \
  --a-symbol 601857 \
  --h-symbol 00857 \
  --constant-fx-rate 0.92 \
  --execution-mode paired \
  --z-grid 1.0,1.5,2.0 \
  --allow-non-coint \
  --output-dir outputs/petrochina_ah
```

如果你漏掉 `--execution-mode paired`，最终摘要里会显示：

```text
Execution mode: long_cheaper_leg_only
```

这是默认行为，不是程序偷偷改了你的策略。

### 3. 做严格筛选时，再去掉 `--allow-non-coint`

默认 guardrail 是开启的，也就是：

- 训练集协整显著，继续跑
- 训练集协整不显著，直接中止

所以当你看到下面这种报错时：

```text
Training-sample cointegration is not significant ...
```

它不是缓存坏了，也不是命令没生效，而是 guardrail 在工作。此时有两个选择：

- 你只是想先跑通流程或做宽松探索：加 `--allow-non-coint`
- 你要做更严格的标的筛选：不要加，让它中止，然后换 A/H 代码对或重设研究窗口

## 数据输入与缓存

项目支持两种输入方式，但推荐把“在线拉取 A/H + 自动缓存”当成默认入口。

### 在线拉取与自动缓存

- 如果没有显式传 `--a-csv` / `--h-csv`，CLI 会先查 `.cache/ah_pairs_trading`
- 本地已有对应缓存时，直接复用
- 本地没有缓存时，才会用 AkShare 在线拉取 A/H 历史
- 当前只有 A/H 历史支持在线拉取和自动缓存
- FX 不会自动联网拉取，必须自己提供 `--fx-csv` 或 `--constant-fx-rate`

这意味着：

- 现在通常不需要一个单独的“先下载再运行”步骤
- 也不需要仓库内置 `data/600036.csv` 这种示例文件才能使用主流程

### 显式传本地 CSV 时的行为

如果你显式传了 `--a-csv` / `--h-csv` / `--fx-csv`：

- 程序会直接读取这个路径
- 路径不存在时会立刻报错
- 不会自动回退到 AkShare
- 也不会改去读缓存里的远端历史

所以本地 CSV 现在更适合：

- 你要离线研究
- 你已经有自己清洗过的数据
- 你要复现实验，避免线上数据源变化

### `--resume-from-cache` 和数据缓存不是一回事

这个项目里有两种缓存：

- 数据缓存：A/H 历史行情缓存，默认就会自动使用
- 阶段缓存：pipeline 中间计算结果缓存，只有显式加 `--resume-from-cache` 才会参与恢复

`--resume-from-cache` 的作用是：

- 长任务中断后，复用已经完成的 pipeline 阶段

它的作用不是：

- 绕过训练集协整显著性检查
- 改变数据源优先级
- 把不存在的 `--a-csv` / `--h-csv` 自动变成在线拉取

例如下面这条命令是合理的：

```bash
pairs-trading \
  --a-symbol 601857 \
  --h-symbol 00857 \
  --constant-fx-rate 0.92 \
  --resume-from-cache \
  --allow-non-coint \
  --output-dir outputs/petrochina_ah
```

## 本地 CSV 用法

仓库不附带 `data/600036.csv`、`data/03968.csv`、`data/hkdcny.csv` 这类示例文件。下面只是占位符，必须替换成你自己的真实路径：

```bash
pairs-trading \
  --a-symbol 600036 \
  --h-symbol 03968 \
  --a-csv /path/to/600036.csv \
  --h-csv /path/to/03968.csv \
  --fx-csv /path/to/hkdcny.csv \
  --train-end-date 2022-12-30 \
  --execution-mode long_cheaper_leg_only \
  --z-grid 1.5,2.0,2.5 \
  --exit-z 0.5 \
  --stop-z 3.0 \
  --output-dir outputs/cmb_ah
```

CSV 只要能识别日期列和收盘价列即可；英文列如 `date` / `close`，或常见中文列如 `日期` / `收盘` 都支持。

## 常见报错

### 1. `FileNotFoundError: ... data/600036.csv`

原因：

- 你显式传了 `--a-csv data/600036.csv`
- 但仓库里并没有这个示例文件

解决：

- 如果你本来就想走推荐主流程，直接去掉 `--a-csv` / `--h-csv`，让程序自动在线拉取并复用缓存
- 如果你就是要用本地文件，把路径换成你机器上的真实 CSV

### 2. `Training-sample cointegration is not significant ...`

原因：

- 默认 `require_significant_cointegration` 是开启的
- 当前训练窗口里，这对 A/H 标的没有通过协整显著性检查

解决：

- 想先跑通：加 `--allow-non-coint`
- 想保持严格筛选：不要加，换标的、换日期窗口或重新核对 A/H 代码对

### 3. 加了 `--resume-from-cache` 还是报协整不显著

原因：

- `--resume-from-cache` 只恢复中间计算阶段
- 它不会跳过协整显著性校验

解决：

- 继续保留 guardrail：不要加 `--allow-non-coint`
- 宽松跑完整流程：显式加 `--allow-non-coint`

### 4. 输出显示的是 `Execution mode: long_cheaper_leg_only`

原因：

- 你没有显式传 `--execution-mode paired`

解决：

- 如果你要双腿模式，请把 `--execution-mode paired` 写回命令里

### 5. `601857/00883` 这种代码对直接被拦下

原因：

- 内置 A/H registry 识别出这是已知的跨发行人错配
- `601857` 对应 PetroChina，`00883` 对应 CNOOC

解决：

- 如果你要做“同发行人 A/H 相对价值”，把 H 股改成正确映射
- 如果你有意做跨公司板块配对研究，可以临时加 `--same-issuer-check warn` 或 `--same-issuer-check off`

## 重要参数

- `--a-symbol` / `--h-symbol`
  A/H 两条腿，必须对应同一发行人。
- `--start-date` / `--end-date`
  分析窗口；当前 CLI 默认是 `2018-01-01` 到 `2024-12-31`。
- `--train-end-date`
  训练集截止日期；当前 CLI 默认是 `2022-12-31`。
- `--constant-fx-rate`
  没有 FX 历史时使用的静态 HKD/CNY 汇率，只适合快速验证或原型测试。
- `--fx-csv`
  你自己的 HKD/CNY 历史；正式回测更推荐这个。
- `--execution-mode`
  `long_cheaper_leg_only` 或 `paired`；默认是 `long_cheaper_leg_only`。
- `--same-issuer-check`
  `strict` / `warn` / `off`；默认 `strict`，会拦住内置 registry 里已知的跨发行人错配。
- `--benchmark-mode`
  `auto` / `external` / `internal` / `off`；默认 `auto`。`auto` 会优先使用显式传入的 benchmark，否则在 `long_cheaper_leg_only` 下自动生成内部 A/H 被动 benchmark。
- `--internal-benchmark-weighting`
  内部 benchmark 的权重口径；支持 `hedge_ratio` 和 `equal_weight`。
- `--z-window`
  滚动标准化窗口，默认 `120`。
- `--z-grid`
  训练集上搜索的入场阈值。
- `--exit-z`
  均值回归后的平仓阈值。
- `--stop-z`
  价差继续恶化时的止损阈值。
- `--max-holding-days`
  最大持有天数。
- `--allow-non-coint`
  允许训练集协整不显著时继续跑完整 pipeline。
- `--refresh-cache`
  忽略已有缓存，重新拉数并重算。
- `--resume-from-cache`
  复用已完成的 pipeline 阶段；不会跳过 guardrail。
- `--cache-dir`
  指定缓存目录，默认 `.cache/ah_pairs_trading`。
- `--no-cache`
  关闭磁盘缓存。

## 输出内容

每次 CLI run 结束后，终端会直接打印一份完整 scorecard，至少包括：

- 基础信息：A/H 标的、pair validation 状态、时间区间、train/test 切分、execution mode、best entry z、benchmark
- Train/Test 回测核心指标：`total_return`、`annual_return`、`annual_volatility`、`sharpe_ratio`、`sortino_ratio`、`max_drawdown`、`calmar_ratio`
- 交易与成本指标：`trade_count`、`win_rate`、`profit_factor`、`payoff_ratio`、`avg_trade_pnl`、`avg_holding_days`、`total_costs`、`cost_to_gross_pnl`
- 稳定性指标：`time_in_market`、`max_consecutive_losses`、`monthly_win_rate`、rolling Sharpe 摘要，以及在提供 benchmark 时的 rolling beta 摘要

指定 `--output-dir` 后还会输出：

- `aligned_prices.csv`
- `model_prices.csv`
- `signal_frame.csv`
- `segment_analysis.csv`
- `rolling_cointegration.csv`
- `train_grid_search.csv`
- `train_equity_curve.csv` / `test_equity_curve.csv`
- `train_trades.csv` / `test_trades.csv`
- `summary.json`
- `summary.md`
- 多张诊断图，包括 `log_prices.png`、`rolling_cointegration.png`、`z_grid_search.png`

其中：

- `summary.json`
  是机器可读的结构化摘要，顶层分成 `run_meta`、`instrument_meta`、`strategy_params`、`cost_assumptions`、`train_metrics`、`test_metrics`、`benchmark_metrics`、`rolling_metrics`、`diagnostics`；其中 `instrument_meta` 里会写出 `pair_validation`、`benchmark_source`
- `summary.md`
  是和终端 scorecard 同口径的人类可读版本，适合直接打开看结果，不需要翻 CSV

## Benchmark 约定

- `paired` 模式下，benchmark 默认不是核心；如果没显式传 benchmark，`--benchmark-mode auto` 不会自动生成内部基准
- `long_cheaper_leg_only` 下，`--benchmark-mode auto` 会生成 `Internal A/H Basket (hedge_ratio)`，用于和被动持有同一发行人 A/H 篮子做对比
- 如果你已经有市场 benchmark，例如 `CSI300` 或 `HSCEI`，显式传 `--benchmark` 或 `--benchmark-csv` 后，外部 benchmark 会优先覆盖自动内部基准

## 数据 QA / 双源 spot-check

不建议把 AkShare/efinance 双源校验塞进每次回测主流程，但建议做成单独 QA。仓库现在提供了一个轻量 spot-check 脚本，适合对两份本地 CSV 做覆盖率和收盘价偏差检查：

```bash
python scripts/compare_histories.py \
  --left-csv /path/to/akshare_601857.csv \
  --right-csv /path/to/efinance_601857.csv \
  --left-label akshare \
  --right-label efinance
```

它会直接输出：

- 日期重叠覆盖率
- 双边独有日期数量
- 收盘价最大/平均绝对偏差
- 收盘价最大/平均相对偏差

## 测试

测试使用合成 A/H 数据，不依赖联网，并覆盖：

- same-issuer registry 校验
- `long_cheaper_leg_only` 自动内部 benchmark
- summary schema / scorecard 持久化
- CLI 参数透传
- 数据 QA spot-check

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run pytest
```

如果你更习惯仓库内脚本，也可以用：

```bash
scripts/test.sh
```

脚本会优先走 `uv run pytest`，没有 `uv` 时再回退到 `python -m pytest`。

## 说明

- 这个仓库现在更适合作为 **A/H relative value research platform**，不是“默认可做空的市场中性模板”。
- `analysis.py` 的统计分析层基本保留；数据层、配置层和回测执行层已经按 A/H 约束重写。
- 原始设计文档还在 [docs/project_design_v1.0.md](/home/richard/code/ah-pairs-trading/docs/project_design_v1.0.md)，但它不再代表当前主流程。
