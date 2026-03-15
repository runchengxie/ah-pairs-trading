# 配对交易项目重构说明

这是一个基于协整分析（Cointegration）的配对交易示例项目，已经从原先的单文件 `main.py` 重构为 `src/` 包结构。新的实现把数据获取、统计分析、回测、指标计算、绘图和 CLI 入口拆分成独立模块，便于维护、测试和后续扩展。

项目默认使用可口可乐 `KO` 与百事可乐 `PEP` 作为配对，使用 `SPY` 作为基准指数。策略核心仍然围绕以下流程展开：

- 获取并对齐历史价格数据
- 对数价格协整检验与 OLS 对冲比率估计
- ECM 误差修正模型与残差均值回归半衰期分析
- 训练集上搜索最优入场阈值 `Z*`
- 在训练集和测试集上分别执行固定对冲比率回测
- 计算滚动 Sharpe、滚动 Beta、超额收益等风险收益指标

## 目录结构

```text
.
├── main.py
├── pyproject.toml
├── README.md
├── docs/
│   └── project_design_v1.0.md
├── scripts/
│   └── test.sh
├── src/
│   └── ah_pairs_trading/
│       ├── __init__.py
│       ├── __main__.py
│       ├── analysis.py
│       ├── cli.py
│       ├── config.py
│       ├── data.py
│       ├── metrics.py
│       ├── pipeline.py
│       ├── plotting.py
│       └── strategy.py
└── tests/
    ├── conftest.py
    ├── test_analysis.py
    └── test_strategy.py
```

## 模块职责

- `data.py`：负责通过 AkShare 拉取行情、对齐交易日、构造价格矩阵、拆分训练集和测试集。
- `analysis.py`：负责 Engle-Granger 协整检验、ECM、半衰期估计、分段分析、滚动协整、矩阵 OLS、VAR 稳定性诊断。
- `strategy.py`：负责固定对冲比率策略回测、交易记录生成和 `Z` 网格搜索。
- `metrics.py`：负责最大回撤、滚动 Sharpe、滚动 Beta 和策略/基准对比数据的计算。
- `plotting.py`：负责把关键结果保存为图像文件。
- `pipeline.py`：负责把整个研究流程串起来，并在指定输出目录中写出 CSV、PNG、JSON 和 TXT 结果。
- `cli.py`：负责命令行参数解析。
- 根目录 `main.py`：只保留为一个很薄的启动入口，方便继续使用 `python main.py`。

## 安装方式

建议使用虚拟环境：

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

如果你只想安装运行所需依赖，也可以执行：

```bash
pip install -e .
```

## 运行方式

### 方式一：直接运行根目录入口

```bash
python main.py --output-dir outputs/ko_pep
```

### 方式二：使用安装后的命令

```bash
pairs-trading --output-dir outputs/ko_pep
```

### 常用参数

```bash
python main.py \
  --dependent KO \
  --independent PEP \
  --benchmark SPY \
  --start-date 2014-01-01 \
  --end-date 2024-12-31 \
  --train-end-date 2020-12-31 \
  --z-grid 0.5,1.0,1.5,2.0 \
  --exit-z 0.2 \
  --objective sharpe_ratio \
  --output-dir outputs/ko_pep
```

参数说明：

- `--dependent`：协整回归中的因变量。
- `--independent`：协整回归中的自变量。
- `--benchmark`：用于滚动 Beta 的基准标的。
- `--z-grid`：训练集上搜索的入场阈值列表。
- `--exit-z`：平仓阈值。
- `--output-dir`：如果提供，会自动输出研究结果文件。

## 输出内容

当指定 `--output-dir` 后，程序会自动生成一组分析产物，例如：

- `prices.csv`、`log_prices.csv`
- `segment_analysis.csv`
- `rolling_cointegration.csv`
- `train_grid_search.csv`
- `train_equity_curve.csv`、`test_equity_curve.csv`
- `train_trades.csv`、`test_trades.csv`
- `summary.json`
- `full_sample_ols_summary.txt`、`train_ols_summary.txt`
- 多张 `.png` 图表，例如滚动协整、净值曲线、滚动 Sharpe、滚动 Beta、超额收益图

## 测试

测试使用合成数据，不依赖在线行情下载，因此更适合本地开发和 CI。

```bash
scripts/test.sh
```

或者直接执行：

```bash
PYTHONPATH=src python -m pytest
```

## 说明

- 代码中的注释和文档字符串已统一为英文，便于包级维护和后续协作。
- 中文说明主要保留在本 `README.md` 中，面向项目使用者。
- 原始设计文档仍保留在 `docs/project_design_v1.0.md`，可作为策略背景参考。
