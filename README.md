# A/H 相对价值研究平台

这个仓库已经从原来的美股双边配对 demo，改成了更贴近大陆居民账户约束的 **A/H 相对价值研究框架**。

核心假设不再是 textbook 式的市场中性，而是：

- 先把同一家公司 A 股和 H 股价格统一到人民币口径
- 用训练期协整回归估计截距和 hedge ratio
- 对残差做滚动标准化，生成 z-score
- 默认执行 `long_cheaper_leg_only`
- 只有显式切到 `paired` 时，才做双腿同时持仓
- 回测里显式计入 lot size、最大持有期和交易成本假设

## 当前能力

- `data.py`
  负责 A 股、H 股、HKD/CNY 数据接入，按共同交易日对齐，并把 H 股价格转换为人民币口径。
- `analysis.py`
  保留协整、ECM、半衰期、滚动协整、矩阵 OLS、VAR 稳定性诊断。
- `strategy.py`
  支持 `long_cheaper_leg_only` 和 `paired` 两种执行模式，并支持显式成本参数。
- `pipeline.py`
  用训练集估计参数，在全样本上生成滚动 z-score，再分别输出训练集和测试集结果。
- `cli.py`
  提供 A/H、FX、成本和执行模式参数。

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

## 数据输入

项目支持两种方式：

1. 直接传本地 CSV
2. 通过 AkShare 拉 A 股和 H 股，再额外提供 FX 序列

注意：

- A/H 价差研究必须有 **HKD/CNY** 序列，不能把 H 股港币价直接拿来和 A 股比较。
- 如果你没有单独的 FX 历史，可以临时用 `--constant-fx-rate` 做原型，但这只适合快速验证代码，不适合正式回测。
- CLI 默认会使用 `.cache/ah_pairs_trading` 做本地缓存。
  如果显式传了 `--a-csv` / `--h-csv` / `--fx-csv`，优先读本地文件；否则会先查缓存，再决定是否在线拉取。
  如果你希望中途中断后继续复用已经完成的计算阶段，可以加 `--resume-from-cache`。

CSV 只要能识别日期列和收盘价列即可；英文列如 `date` / `close`，或常见中文列如 `日期` / `收盘` 都支持。

## 运行方式

### 用本地 CSV

```bash
python main.py \
  --a-symbol 600036 \
  --h-symbol 03968 \
  --a-csv data/600036.csv \
  --h-csv data/03968.csv \
  --fx-csv data/hkdcny.csv \
  --train-end-date 2022-12-30 \
  --execution-mode long_cheaper_leg_only \
  --z-grid 1.5,2.0,2.5 \
  --exit-z 0.5 \
  --stop-z 3.0 \
  --output-dir outputs/cmb_ah
```

### 用 AkShare 拉 A/H，手动给 FX 常数

```bash
pairs-trading \
  --a-symbol 601857 \
  --h-symbol 00883 \
  --constant-fx-rate 0.92 \
  --execution-mode paired \
  --z-grid 1.0,1.5,2.0 \
  --output-dir outputs/cnooc_ah
```

### 缓存与断点恢复

同一个命令就够了，不需要额外拆一个“先下载再运行”的入口。

- 默认缓存目录是 `.cache/ah_pairs_trading`
- `--refresh-cache`
  忽略已有缓存，重新拉数并重算
- `--resume-from-cache`
  复用已经完成的 pipeline 阶段，适合长任务中断后继续
- `--cache-dir /path/to/cache`
  指定缓存目录
- `--no-cache`
  完全关闭缓存

例如：

```bash
pairs-trading \
  --a-symbol 601857 \
  --h-symbol 00883 \
  --constant-fx-rate 0.92 \
  --resume-from-cache \
  --output-dir outputs/cnooc_ah
```

## 重要参数

- `--a-symbol` / `--h-symbol`
  A/H 两条腿。
- `--execution-mode`
  `long_cheaper_leg_only` 或 `paired`。
- `--share-ratio`
  股份换算系数，默认 `1.0`。
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
- `--a-buy-cost-bps` / `--a-sell-cost-bps`
  A 股单边成本假设。
- `--h-buy-cost-bps` / `--h-sell-cost-bps`
  H 股基础交易成本。
- `--h-stamp-duty-bps`
  H 股印花税假设。
- `--fx-conversion-bps`
  汇兑隐性成本假设。
- `--allow-non-coint`
  默认训练集协整不显著会直接中止；加上这个参数则继续跑。

## 输出内容

指定 `--output-dir` 后会输出：

- `aligned_prices.csv`
- `model_prices.csv`
- `signal_frame.csv`
- `segment_analysis.csv`
- `rolling_cointegration.csv`
- `train_grid_search.csv`
- `train_equity_curve.csv` / `test_equity_curve.csv`
- `train_trades.csv` / `test_trades.csv`
- `summary.json`
- 多张诊断图，包括 `log_prices.png`、`rolling_cointegration.png`、`z_grid_search.png`

## 测试

测试使用合成 A/H 数据，不依赖联网。

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run pytest
```

## 说明

- 这个仓库现在更适合作为 **A/H relative value research platform**，不是“默认可做空的市场中性模板”。
- `analysis.py` 的统计分析层基本保留；数据层、配置层和回测执行层已经按 A/H 约束重写。
- 原始设计文档还在 [docs/project_design_v1.0.md](/home/richard/code/ah-pairs-trading/docs/project_design_v1.0.md)，但它不再代表当前主流程。
