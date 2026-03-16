# CLI 参数参考

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
  本地 HKD/CNY 历史 CSV；正式回测更推荐这个
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
  缓存目录，默认 `.cache/ah_pairs_trading`
- `--no-cache`
  关闭磁盘缓存
- `--refresh-cache`
  忽略已有缓存并重新拉数/重算
- `--resume-from-cache`
  恢复已完成的 pipeline 阶段，但不会跳过 guardrail
- `--output-dir`
  输出目录，用于保存 CSV、PNG、JSON、Markdown 摘要

## 最常用的两种命令

### Smoke test

```bash
pairs-trading \
  --a-symbol 601857 \
  --h-symbol 00857 \
  --constant-fx-rate 0.92 \
  --allow-non-coint \
  --output-dir outputs/petrochina_ah_smoke
```

### Research run

```bash
pairs-trading \
  --a-symbol 601857 \
  --h-symbol 00857 \
  --fx-csv /path/to/hkdcny.csv \
  --output-dir outputs/petrochina_ah_research
```
