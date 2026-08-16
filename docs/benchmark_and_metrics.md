# Benchmark 与结果解读

## 为什么要强调 benchmark

默认执行模式 `long_cheaper_leg_only` 不是市场中性策略，因此不能只看绝对收益。

当前设计思路：

- long-only 模式默认对比同一发行人的内部 A/H 被动 basket
- paired 模式默认不自动引入内部 benchmark
- 权重引擎额外输出买入持有、常数混合和波动率目标混合等基准

## `--benchmark-mode`

支持四种模式：

- `auto`
- `external`
- `internal`
- `off`

### `auto`

- 如果显式提供了外部 benchmark，就优先使用外部 benchmark
- 如果没有显式提供，那么在 `long_cheaper_leg_only` 模式下会自动生成内部 A/H basket
- 在 `paired` 下如果没有显式 benchmark，则保持为空

### `external`

只接受显式提供的外部 benchmark：

- `--benchmark`
- `--benchmark-csv`

如果没提供会报错。

### `internal`

强制使用内部 A/H 被动 basket benchmark。

### `off`

关闭 benchmark 比较。

## 内部 benchmark 的含义

内部 benchmark 用来回答一个更贴近当前策略定位的问题：

> 既然你已经承认自己不是市场中性策略，那么你的信号驱动买入，是否优于被动持有同一发行人的 A/H 篮子？

当前支持两种内部权重口径：

- `hedge_ratio`
- `equal_weight`

## 权重引擎的基准

`--backtest-engine weight` 运行时，除了主回测，还会在测试集上生成几组对照基准，写入 `test_bm_*.csv`：

- `bm_hold_5050`，买入持有，初始 50/50 权重后不再平衡
- `bm_mix_5050`，常数混合，每日再平衡回 50/50
- `bm_mix_5050_risk`，常数混合加上波动率目标
- `bm_hold_a`，只买入 A 股
- `bm_hold_h`，只买入 H 股

这些基准和策略净值会画在同一张 `test_weight_nav_vs_benchmarks.png` 里，方便判断信号倾斜是否真的贡献了超额。

## 输出结果里会看到什么

终端 scorecard 和输出文件会包含三层信息：

- 策略自身表现
- 相对 benchmark 的比较
- 稳定性与滚动指标

常见核心指标包括：

- `total_return`
- `annual_return`
- `annual_volatility`
- `sharpe_ratio`
- `sortino_ratio`
- `max_drawdown`
- `calmar_ratio`
- `trade_count`
- `win_rate`
- `profit_factor`
- `avg_holding_days`
- `total_costs`
- `time_in_market`

如果有 benchmark，还会额外看到：

- `benchmark_total_return`
- `benchmark_annual_return`
- `excess_total_return`
- `relative_return`
- `tracking_error`
- `information_ratio`
- `beta`
- `alpha`

如果没有解析到 benchmark，终端 scorecard 里的 `Rolling Beta` 会明确显示 unavailable。

权重引擎启用后，scorecard 末尾还会显示 `Rolling OU MLE` 一栏，包含半衰期、均值回归速度和 Ljung-Box p 值等汇总。

## 输出文件

指定 `--output-dir` 后，常见产物包括：

- `summary.json`
- `summary.md`
- `train_equity_curve.csv`
- `test_equity_curve.csv`
- `train_trades.csv`
- `test_trades.csv`
- `train_grid_search.csv`
- `signal_frame.csv`
- `rolling_cointegration.csv`
- `ou_params.csv`
- `test_bm_hold_5050.csv` 等权重引擎基准
- 诊断图表

## 怎么解读 long-only 结果

对 `long_cheaper_leg_only`，建议优先看：

- 是否稳定优于内部 A/H basket benchmark
- 交易成本是否吞噬了信号优势
- 收益是否过度依赖少数交易
- 在不同阶段是否仍然保留相对优势
