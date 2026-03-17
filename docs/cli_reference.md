# CLI 参数参考

## 配置来源与优先级

当前 CLI 支持三层配置来源，优先级从低到高如下：

- 内置默认值
- `--config path/to/preset.toml`
- 显式 CLI 参数

`configs/` 里的 TOML 预设使用的就是当前 CLI 的扁平键名，因此 `pairs-trading --config configs/petrochina_smoke.toml --output-dir ...` 这种覆盖方式是官方支持的。

## 路径约定

- `data/` 保留给手工维护或外部导入的输入文件，例如 `data/fx/*.csv`
- `artifacts/cache/ah_pairs_trading/` 是默认缓存目录
- `artifacts/runs/<run-name>/` 是推荐的运行结果目录
- `configs/` 保存可直接传给 `--config` 的 TOML 预设

## 配置入口

- `--config`
  TOML 预设文件路径。文件里的键名应与 CLI 参数名对应，例如 `a_symbol`、`fx_csv`、`benchmark_mode`。相对路径会相对该 TOML 文件所在目录解析。

## 标的与日期

- `--a-symbol`
  A 股代码，默认 `600036`
- `--h-symbol`
  H 股代码，默认 `03968`
- `--start-date`
  回测起始日期，默认 `2018-01-01`
- `--end-date`
  回测结束日期，默认 `2024-12-31`
- `--train-end-date`
  训练集截止日期，默认 `2022-12-31`

## 数据输入

- `--a-csv`
  本地 A 股历史 CSV；传入后只用这个文件
- `--h-csv`
  本地 H 股历史 CSV；传入后只用这个文件
- `--fx-csv`
  本地 HKD/CNY 历史 CSV；正式回测更推荐这个。没有现成文件时，可以先运行 `python scripts/fetch_fx_history.py ...`
- `--benchmark-csv`
  本地 benchmark 历史 CSV
- `--constant-fx-rate`
  静态 HKD/CNY 汇率；适合 smoke test 或快速原型
- `--share-ratio`
  A/H 股数换算比例，默认 `1.0`

## 执行模式与信号参数

- `--execution-mode`
  `long_cheaper_leg_only` 或 `paired`；默认 `long_cheaper_leg_only`
- `--z-window`
  z-score 滚动窗口，默认 `120`
- `--z-min-periods`
  计算滚动 z-score 所需的最小样本数，默认 `60`
- `--entry-signal-mode`
  主信号模式，支持 `zscore`、`ret_spread_ema`、`ret_spread_sma`
- `--hedge-ratio-mode`
  spread 构造和 `paired` 仓位 sizing 所用的对冲系数；支持 `training`、`rolling`
- `--return-filter-mode`
  只在 `entry_signal_mode=zscore` 下生效；支持 `off`、`ema`、`sma`
- `--cointegration-gate-mode`
  滚动协整 gate；支持 `off`、`significant`。`significant` 只在最新滚动窗口协整仍显著时允许交易，并在失效时强平
- `--return-filter-window`
  return-spread 平滑窗口，默认 `10`
- `--return-filter-min-periods`
  return-spread 过滤器所需的最小样本数；默认跟随 `return-filter-window`
- `--z-grid`
  训练集上搜索的入场阈值，默认 `1.5,2.0,2.5`
- `--exit-z`
  平仓阈值，默认 `0.5`
- `--stop-z`
  止损阈值，默认 `3.0`
- `--max-holding-days`
  最大持有期，默认 `15`
- `--position-size-fraction`
  每次开仓可用资本比例，默认 `0.95`
- `--initial-capital`
  回测初始资金，默认 `100000`
- `--objective`
  训练集网格搜索目标，支持 `sharpe_ratio`、`annual_return`、`final_capital`、`total_return`、`win_rate`
- `--a-lot-size`
  A 股 lot size，默认 `100`
- `--h-lot-size`
  H 股 lot size，默认 `100`

## Benchmark 与校验

- `--benchmark`
  外部 benchmark 代码
- `--benchmark-market`
  benchmark 市场，支持 `a` 和 `h`
- `--benchmark-mode`
  `auto`、`external`、`internal`、`off`；默认 `auto`
- `--internal-benchmark-weighting`
  内部 benchmark 权重口径，支持 `hedge_ratio` 和 `equal_weight`
- `--allow-non-coint`
  允许训练集协整不显著时继续跑完整 pipeline
- `--same-issuer-check`
  `strict`、`warn`、`off`；默认 `strict`

## 成本参数

- `--a-buy-cost-bps`
  A 股买入成本，默认 `2.0`
- `--a-sell-cost-bps`
  A 股卖出成本，默认 `2.0`
- `--h-buy-cost-bps`
  H 股买入基础成本，默认 `8.0`
- `--h-sell-cost-bps`
  H 股卖出基础成本，默认 `8.0`
- `--h-stamp-duty-bps`
  H 股印花税假设，默认 `10.0`
- `--fx-conversion-bps`
  H 股交易相关的 FX 换汇成本，默认 `2.0`

## 缓存与输出

- `--cache-dir`
  缓存目录，默认 `artifacts/cache/ah_pairs_trading`。其中原始市场数据会按 symbol 维护增量主档，pipeline stage cache 也会放在这里
- `--no-cache`
  关闭磁盘缓存
- `--refresh-cache`
  重建原始市场数据主档，并忽略本次运行可用的 stage cache
- `--resume-from-cache`
  只恢复已完成的 pipeline 阶段；不会改变原始市场数据的自动增量缓存逻辑，也不会跳过 guardrail
- `--output-dir`
  输出目录，用于保存 CSV、PNG、JSON、Markdown 摘要；更推荐写到 `artifacts/runs/<run-name>`

## 示例入口

- Smoke preset：`pairs-trading --config configs/petrochina_smoke.toml`
- Research preset：`pairs-trading --config configs/petrochina_research.toml`
- 如果你需要测试入口和覆盖率命令，见 [`development.md`](development.md)
- 如果你需要数据、FX 和缓存解释，见 [`data_and_fx.md`](data_and_fx.md)
