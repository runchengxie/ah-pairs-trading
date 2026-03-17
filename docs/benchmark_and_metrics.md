# Benchmark 与结果解读

## 为什么要强调 benchmark

默认执行模式 `long_cheaper_leg_only` 不是市场中性策略，因此不能只看绝对收益。

项目当前的设计思路是：

- long-only 模式默认对比同一发行人的内部 A/H 被动 basket
- paired 模式默认不自动引入内部 benchmark

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

内部 benchmark 的目标不是模拟市场指数，而是回答一个更贴近当前策略定位的问题：

> 既然你已经承认自己不是市场中性策略，那么你的信号驱动买入，是否优于被动持有同一发行人的 A/H 篮子？

当前支持两种内部权重口径：

- `hedge_ratio`
- `equal_weight`

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

如果没有解析到 benchmark，终端 scorecard 里的 `Rolling Beta` 会明确显示 unavailable，而不是输出一串 `n/a`。

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
- 诊断图表

## 怎么解读 long-only 结果

对 `long_cheaper_leg_only`，建议优先看：

- 是否稳定优于内部 A/H basket benchmark
- 交易成本是否吞噬了信号优势
- 收益是否过度依赖少数交易
- 在不同阶段是否仍然保留相对优势
