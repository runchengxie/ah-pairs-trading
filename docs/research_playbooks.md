# 按研究目标分类的 Research Playbooks

这份文档不重复解释全部参数，而是把常见研究目标拆成一组可直接照抄的剧本。

如果你还没准备环境、FX 数据和基础目录，先看 [`cookbook_runbook.md`](cookbook_runbook.md)。
如果你需要完整参数字典，直接看 [`cli_reference.md`](cli_reference.md)。

## 1. 先决定你现在要回答什么问题

| 你现在的问题 | 直接用哪个剧本 |
| --- | --- |
| 我只想确认环境、联网、缓存和输出链路都正常 | 剧本 A |
| 我想先拿到一套当前推荐的 long-only 研究基线 | 剧本 B |
| 我想判断 paired 有没有研究价值 | 剧本 C |
| 我想知道结果是不是被乐观成本假设撑出来的 | 剧本 D |
| 我想知道 cointegration / ECM gate 到底有没有帮助 | 剧本 E |
| 我想把 half-life 真正变成参数锚点，而不是只看诊断 | 剧本 F |
| 我想比较 z-score 和 return-spread 这两类信号 | 剧本 G |
| 我想比较 training 和 rolling hedge ratio 谁更稳 | 剧本 H |
| 我想换 benchmark 口径，确认结论是不是只对某个对照成立 | 剧本 I |
| 我想做离线复现、共享数据集或本地 CSV 研究 | 剧本 J |

下面所有剧本默认都基于：

- 已安装依赖
- 已准备 `data/fx/hkdcny_2018_2024.csv`
- 默认研究标的是石油 A/H 示例
- 你会把结果写到 `artifacts/runs/`

## 2. 剧本 A：先跑通整条链路

目标：
确认 CLI、在线拉数、缓存、输出目录和摘要生成都正常。

```bash
pairs-trading --config configs/petrochina_smoke.toml
```

你应该先看：

- `artifacts/runs/petrochina_ah_smoke/summary.md`
- `artifacts/runs/petrochina_ah_smoke/summary.json`

判断标准：

- 能正常结束，不报参数、数据或联网错误
- `summary.md` 能生成
- `summary.json` 里有训练和测试摘要

不要在这个剧本上做正式结论，因为它默认允许非协整、也允许常数汇率。

## 3. 剧本 B：建立推荐的 long-only 研究基线

目标：
拿到当前最接近“研究默认口径”的结果，后面所有对照实验都从这里偏离。

```bash
pairs-trading --config configs/petrochina_research.toml
```

这个基线默认已经打开：

- `execution_timing=next_open`
- `hedge_ratio_mode=rolling`
- `cointegration_gate_mode=significant`
- `ecm_gate_mode=significant_negative`
- `half_life_anchor_mode=training`
- `max_adv_fraction=0.05`

优先看这些文件：

- `artifacts/runs/petrochina_ah_research/summary.md`
- `artifacts/runs/petrochina_ah_research/signal_frame.csv`
- `artifacts/runs/petrochina_ah_research/rolling_cointegration.csv`
- `artifacts/runs/petrochina_ah_research/rolling_ecm.csv`
- `artifacts/runs/petrochina_ah_research/test_trades.csv`

最先回答四个问题：

- 相对内部 benchmark 有没有稳定超额
- 交易次数是不是太少，少到结果不稳
- 成本拆分里 `transaction/slippage/borrow/financing` 哪块最大
- gate 是在减少噪音，还是只是把交易数压到几乎没法研究

## 4. 剧本 C：判断 paired 有没有研究价值

目标：
不要先假设 `paired` 更高级，而是先看它在更真实成本下是否仍然站得住。

```bash
pairs-trading \
  --config configs/petrochina_research.toml \
  --execution-mode paired \
  --benchmark-mode off \
  --output-dir artifacts/runs/petrochina_ah_paired
```

优先看：

- `artifacts/runs/petrochina_ah_paired/summary.md`
- `artifacts/runs/petrochina_ah_paired/test_trades.csv`

重点比较：

- 与剧本 B 相比，`annual_return`、`max_drawdown`、`sharpe_ratio` 是否真的改善
- `borrow_cost` 和 `financing_cost` 是否开始主导成本
- `time_in_market` 和 `trade_count` 是否显著变化

如果 paired 只在很乐观的 borrow 假设下才显得好看，就不要急着把它当主路径。

## 5. 剧本 D：做成本现实性压测

目标：
确认收益是否只是建立在默认成本假设偏松的前提上。

基线：

```bash
pairs-trading \
  --config configs/petrochina_research.toml \
  --output-dir artifacts/runs/petrochina_cost_base
```

悲观版：

```bash
pairs-trading \
  --config configs/petrochina_research.toml \
  --a-slippage-bps 6 \
  --h-slippage-bps 12 \
  --a-impact-bps-per-100pct-adv 30 \
  --h-impact-bps-per-100pct-adv 50 \
  --max-adv-fraction 0.02 \
  --output-dir artifacts/runs/petrochina_cost_stress
```

paired 悲观版：

```bash
pairs-trading \
  --config configs/petrochina_research.toml \
  --execution-mode paired \
  --benchmark-mode off \
  --a-slippage-bps 6 \
  --h-slippage-bps 12 \
  --a-impact-bps-per-100pct-adv 30 \
  --h-impact-bps-per-100pct-adv 50 \
  --a-short-borrow-apr-bps 500 \
  --h-short-borrow-apr-bps 300 \
  --a-long-financing-apr-bps 100 \
  --h-long-financing-apr-bps 100 \
  --max-adv-fraction 0.02 \
  --output-dir artifacts/runs/petrochina_cost_stress_paired
```

比较时重点盯住：

- `total_costs`
- `slippage_costs`
- `borrow_costs`
- `financing_costs`
- `trade_count`
- `annual_return`
- `max_drawdown`

如果小幅上调成本就把收益打穿，这个策略的可交易性就值得怀疑。

## 6. 剧本 E：做 gate 消融实验

目标：
把 cointegration gate 和 ECM gate 分开看，别把“加了两个 gate 后结果变了”混成一个结论。

无 gate：

```bash
pairs-trading \
  --config configs/petrochina_research.toml \
  --cointegration-gate-mode off \
  --ecm-gate-mode off \
  --output-dir artifacts/runs/petrochina_gate_none
```

只有 cointegration gate：

```bash
pairs-trading \
  --config configs/petrochina_research.toml \
  --cointegration-gate-mode significant \
  --ecm-gate-mode off \
  --output-dir artifacts/runs/petrochina_gate_coint_only
```

两个 gate 都开：

```bash
pairs-trading \
  --config configs/petrochina_research.toml \
  --cointegration-gate-mode significant \
  --ecm-gate-mode significant_negative \
  --output-dir artifacts/runs/petrochina_gate_coint_ecm
```

优先比较：

- `trade_count`
- `win_rate`
- `max_drawdown`
- `time_in_market`
- `rolling_cointegration.csv`
- `rolling_ecm.csv`

推荐结论口径：

- 如果 gate 降低了回撤，同时没有把交易数压到几乎为零，它有研究价值
- 如果 gate 只是把坏阶段全删掉，但样本剩太少，先不要把它当成稳健性胜利

## 7. 剧本 F：让 half-life 真正参与参数选择

目标：
比较“固定经验参数”和“训练期 half-life 锚定参数”两种方式。

固定窗口：

```bash
pairs-trading \
  --config configs/petrochina_research.toml \
  --half-life-anchor-mode off \
  --z-window 120 \
  --max-holding-days 15 \
  --output-dir artifacts/runs/petrochina_half_life_off
```

half-life 锚定：

```bash
pairs-trading \
  --config configs/petrochina_research.toml \
  --half-life-anchor-mode training \
  --half-life-z-window-multiplier 2.0 \
  --half-life-max-holding-multiplier 1.5 \
  --output-dir artifacts/runs/petrochina_half_life_on
```

你要看的是：

- `summary.md` 里最终生效的 `z_window` 和 `max_holding_days`
- 交易频率有没有变得更合理
- `avg_holding_days` 是否更贴近均值回归节奏

这一步的目标不是追求某一次收益更高，而是看参数是否更有统计锚点。

## 8. 剧本 G：比较两类主信号

目标：
区分“价差 z-score”与“return-spread”家族是否在这个标的上表现不同。

z-score 基线：

```bash
pairs-trading \
  --config configs/petrochina_research.toml \
  --entry-signal-mode zscore \
  --return-filter-mode ema \
  --output-dir artifacts/runs/petrochina_signal_zscore
```

return-spread EMA：

```bash
pairs-trading \
  --config configs/petrochina_research.toml \
  --entry-signal-mode ret_spread_ema \
  --return-filter-mode off \
  --output-dir artifacts/runs/petrochina_signal_ret_ema
```

return-spread SMA：

```bash
pairs-trading \
  --config configs/petrochina_research.toml \
  --entry-signal-mode ret_spread_sma \
  --return-filter-mode off \
  --output-dir artifacts/runs/petrochina_signal_ret_sma
```

比较时优先看：

- `trade_count`
- `win_rate`
- `profit_factor`
- `avg_holding_days`
- `signal_frame.csv` 里对应的信号列是否过度噪音化

注意：
`return_filter_mode` 只在 `entry_signal_mode=zscore` 下有效，不要混着开。

## 9. 剧本 H：比较 training 和 rolling hedge ratio

目标：
确认 rolling 是否真的提高稳健性，而不是只是多加了一层时间变化自由度。

training：

```bash
pairs-trading \
  --config configs/petrochina_research.toml \
  --hedge-ratio-mode training \
  --cointegration-gate-mode off \
  --ecm-gate-mode off \
  --output-dir artifacts/runs/petrochina_hr_training
```

rolling：

```bash
pairs-trading \
  --config configs/petrochina_research.toml \
  --hedge-ratio-mode rolling \
  --cointegration-gate-mode significant \
  --ecm-gate-mode significant_negative \
  --output-dir artifacts/runs/petrochina_hr_rolling
```

比较点：

- 收益有没有更稳，而不是只看单一终点收益
- `signal_frame.csv` 里的 `hedge_ratio` 是否波动得过于剧烈
- rolling 版本是否明显降低结构失效阶段的暴露

如果 rolling 只带来更少交易和更复杂归因，却没有换来更低回撤或更稳定表现，它不一定值得成为默认值。

## 10. 剧本 I：换 benchmark 口径

目标：
确认 long-only 结论不是只对某一个 benchmark 口径成立。

内部 benchmark，按 hedge ratio：

```bash
pairs-trading \
  --config configs/petrochina_research.toml \
  --benchmark-mode internal \
  --internal-benchmark-weighting hedge_ratio \
  --output-dir artifacts/runs/petrochina_benchmark_hr
```

内部 benchmark，等权：

```bash
pairs-trading \
  --config configs/petrochina_research.toml \
  --benchmark-mode internal \
  --internal-benchmark-weighting equal_weight \
  --output-dir artifacts/runs/petrochina_benchmark_eq
```

关闭 benchmark：

```bash
pairs-trading \
  --config configs/petrochina_research.toml \
  --benchmark-mode off \
  --output-dir artifacts/runs/petrochina_benchmark_off
```

优先比较：

- `excess_total_return`
- `relative_return`
- `information_ratio`
- `tracking_error`
- `beta`
- `alpha`

如果你在 long-only 下只看绝对收益，不看内部 benchmark，对策略定位的解读会偏松。

## 11. 剧本 J：做离线复现和本地 CSV 研究

目标：
固定输入数据，减少在线拉数差异，方便团队内复现。

```bash
pairs-trading \
  --a-csv /path/to/a_share.csv \
  --h-csv /path/to/h_share.csv \
  --fx-csv data/fx/hkdcny_2018_2024.csv \
  --a-symbol 601857 \
  --h-symbol 00857 \
  --start-date 2018-01-01 \
  --end-date 2024-12-31 \
  --train-end-date 2022-12-31 \
  --execution-timing next_open \
  --hedge-ratio-mode rolling \
  --cointegration-gate-mode significant \
  --ecm-gate-mode significant_negative \
  --half-life-anchor-mode training \
  --output-dir artifacts/runs/petrochina_offline_repro
```

这个剧本适合：

- 固定研究样本
- 做代码改动前后的回归对照
- 跟别人共享同一份输入数据

如果你要做更正式的复现实验，建议连 benchmark 也改成 `--benchmark-csv` 固定输入。

## 12. 推荐执行顺序

如果你是第一次系统跑这个项目，建议按这个顺序：

1. 剧本 A，确认环境可用。
2. 剧本 B，拿到 long-only 基线。
3. 剧本 D，先做成本现实性压测。
4. 剧本 E，判断 gate 是否真的有帮助。
5. 剧本 F，确认 half-life 锚定是否比固定参数更合理。
6. 剧本 H，比较 training 和 rolling hedge ratio。
7. 剧本 C，只在前面都站得住时再评估 paired。
8. 剧本 I，最后再换 benchmark 口径看结论是否稳。

这条顺序的核心思想是：
先补执行现实性，再讨论模型复杂度；先有基线，再做消融；先确认 long-only 逻辑站得住，再决定是否升级到 paired。

## 13. 建议你自己留一张对照表

每跑完一个剧本，至少记录这些字段：

- `run_name`
- `execution_mode`
- `entry_signal_mode`
- `hedge_ratio_mode`
- `cointegration_gate_mode`
- `ecm_gate_mode`
- `half_life_anchor_mode`
- `max_adv_fraction`
- `annual_return`
- `sharpe_ratio`
- `max_drawdown`
- `trade_count`
- `avg_holding_days`
- `total_costs`
- `slippage_costs`
- `borrow_costs`
- `financing_costs`

你后面真正要看的不是“哪次回测最高”，而是：

- 哪些结论在多组假设下仍然成立
- 哪些收益一旦把成本和容量假设调严就消失
- 哪些功能是在降噪，哪些功能只是把样本切得更少

## 14. 相关阅读

- 总手册：[`cookbook_runbook.md`](cookbook_runbook.md)
- 策略定位：[`strategy_overview.md`](strategy_overview.md)
- 执行模式：[`execution_modes.md`](execution_modes.md)
- benchmark 解释：[`benchmark_and_metrics.md`](benchmark_and_metrics.md)
- CLI 参数：[`cli_reference.md`](cli_reference.md)
