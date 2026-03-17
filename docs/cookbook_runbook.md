# A/H 相对价值 Cookbook / Runbook

这份文档的目标不是重复参数字典，而是回答两个更实际的问题：

- 第一次跑这个项目，应该按什么顺序做
- 原有功能和新加的执行现实性、ECM、half-life、容量约束该怎么组合

如果你只想查单个参数，请看 [`cli_reference.md`](cli_reference.md)。
如果你想理解策略定位，请看 [`strategy_overview.md`](strategy_overview.md)。
如果你想按研究目标直接选一套对照实验，请看 [`research_playbooks.md`](research_playbooks.md)。

## 1. 先建立正确预期

当前项目是：

- A/H 同发行人相对价值研究框架
- 默认执行模式是 `long_cheaper_leg_only`
- `paired` 只适合允许做空的研究环境

当前项目不是：

- 默认市场中性套利模板
- 高频执行仿真器
- 自动 walk-forward 平台

这会直接影响你怎么选参数、怎么解读结果、怎么比较 benchmark。

## 2. 一次性准备

### 安装依赖

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

或：

```bash
uv sync --extra dev
```

### 目录约定

- `data/` 放手工维护或外部导入的输入文件
- `data/fx/` 放 FX 历史
- `configs/` 放 TOML 预设
- `artifacts/cache/ah_pairs_trading/` 放自动缓存
- `artifacts/runs/<run-name>/` 放每次研究的结果

### 准备 FX 历史

正式研究更推荐先生成 FX CSV：

```bash
python scripts/fetch_fx_history.py \
  --start-date 2018-01-01 \
  --end-date 2024-12-31 \
  --output-csv data/fx/hkdcny_2018_2024.csv
```

如果你只是 smoke test 或环境检查，才建议用 `--constant-fx-rate`。

## 3. 你最常用的四条命令

### 3.1 跑通环境

```bash
pairs-trading --config configs/petrochina_smoke.toml
```

适合确认：

- CLI 能启动
- 在线 A/H 拉数和缓存正常
- 输出目录能落盘
- 结果摘要能生成

### 3.2 跑推荐研究口径

```bash
pairs-trading --config configs/petrochina_research.toml
```

这个 preset 当前已经包含：

- `execution_timing=next_open`
- `hedge_ratio_mode=rolling`
- `cointegration_gate_mode=significant`
- `ecm_gate_mode=significant_negative`
- `half_life_anchor_mode=training`
- `max_adv_fraction=0.05`

也就是当前最接近“研究默认口径”的组合。

### 3.3 研究 paired 模式

```bash
pairs-trading \
  --config configs/petrochina_research.toml \
  --execution-mode paired \
  --benchmark-mode off \
  --output-dir artifacts/runs/petrochina_ah_paired
```

适合做：

- long-only 和 paired 的行为对比
- 借券成本、融资拖累、滑点对结果的敏感性分析

### 3.4 用本地 CSV 做离线复现实验

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
  --output-dir artifacts/runs/petrochina_local_csv
```

适合：

- 离线研究
- 固定输入复现
- 替换成你自己清洗过的数据

## 4. 推荐操作顺序

建议按下面顺序，而不是一开始就调一堆参数。

### 第一步：先 smoke test

先跑：

```bash
pairs-trading --config configs/petrochina_smoke.toml
```

确认你没有以下基础问题：

- 依赖没装好
- 无法写 `artifacts/`
- 无法联网拉 A/H 历史
- FX 文件还没准备好

### 第二步：跑研究 preset

```bash
pairs-trading --config configs/petrochina_research.toml
```

先不要急着改参数。先看当前默认研究口径在你的环境里是否稳定可复现。

### 第三步：看 `summary.md` 和 `summary.json`

先看三件事：

- 训练期协整是否显著
- 最终采用的 `entry_z`、`z_window`、`max_holding_days` 是什么
- 成本拆分里 transaction/slippage/borrow/financing 各自占多少

### 第四步：再看 CSV 细节

优先级建议：

1. `signal_frame.csv`
2. `test_trades.csv`
3. `test_equity_curve.csv`
4. `rolling_cointegration.csv`
5. `rolling_ecm.csv`
6. `train_grid_search.csv`

### 第五步：最后再做参数实验

只有在你已经确认：

- 数据没问题
- 协整 guardrail 没问题
- 成本模型口径没问题
- 基准流程能稳定跑通

之后，再去比较不同参数组合。

## 5. 研究输出怎么读

指定 `--output-dir` 后，常见产物包括：

- `summary.json`
- `summary.md`
- `signal_frame.csv`
- `rolling_cointegration.csv`
- `rolling_ecm.csv`
- `train_grid_search.csv`
- `train_equity_curve.csv`
- `test_equity_curve.csv`
- `train_trades.csv`
- `test_trades.csv`

推荐阅读顺序：

### `summary.md`

这是最快的总览入口。适合先看：

- 样本窗口
- execution mode
- execution timing
- 训练期协整结果
- 选中的 `entry_z`
- effective `z_window`
- effective `max_holding_days`
- 成本拆分
- benchmark 对比

### `signal_frame.csv`

这是最关键的“策略输入中间表”。常见字段包括：

- `spread`
- `zscore`
- `intercept`
- `hedge_ratio`
- `cheap_leg`
- `ret_spread`
- `ret_spread_ema_zscore`
- `ret_spread_sma_zscore`
- `cointegration_gate_pass`
- `ecm_speed`
- `ecm_p_value`
- `ecm_gate_pass`
- `a_open`
- `h_open`
- `a_adv`
- `h_adv`

当你怀疑“为什么这天没开仓/这天突然被强平”时，优先查这里。

### `test_equity_curve.csv`

适合回答：

- 每天策略到底持不持仓
- 当天信号强度和持仓方向是什么
- 每天成本是怎么累计进权益曲线的

常见字段包括：

- `signal_score`
- `position`
- `gross_exposure`
- `daily_transaction_cost`
- `daily_slippage_cost`
- `daily_borrow_cost`
- `daily_financing_cost`

### `test_trades.csv`

适合回答：

- 每笔交易到底买了哪条腿
- 是正常 exit、stop、max holding 还是 gate 触发平仓
- 成本到底吃掉了多少

现在单笔交易记录会拆出：

- entry/exit transaction cost
- entry/exit slippage cost
- borrow cost
- financing cost

### `rolling_cointegration.csv`

适合看：

- rolling 协整关系是否稳定
- gate 失效是否和交易回撤同步

### `rolling_ecm.csv`

适合看：

- error-correction speed 是否长期为负
- p-value 是否经常失效
- ECM gate 是否过于严格

### `train_grid_search.csv`

适合看：

- `entry_z` 候选值的训练期表现差异
- 当前目标函数是否把你推向过于稀疏或过于拥挤的交易频率

## 6. 最重要的参数怎么选

这一节不是“唯一正确答案”，而是一个可执行的默认框架。

### `execution_mode`

`long_cheaper_leg_only` 适合：

- 不能做空
- 想做更诚实的可执行研究
- 想先验证相对价值信号是否有用

`paired` 适合：

- 有做空能力
- 想研究更传统的 pairs trading 结构
- 能接受 borrow/financing/capacity 假设的重要性显著上升

### `execution_timing`

推荐默认：

```text
next_open
```

原因：

- 避免同一根 bar 又出信号又按同一根 bar 价格成交
- 更接近日频研究里的基本现实性要求

只有在你明确知道自己想研究“close-to-close 假设”时，才建议改成 `close`。

### `hedge_ratio_mode`

`training` 适合：

- 想做更稳定、可解释的基线
- 样本不长
- 不想让 rolling 参数引入太多自由度

`rolling` 适合：

- 你怀疑协整关系时变
- 你愿意接受更高的模型复杂度
- 你会同时查看 `rolling_cointegration.csv`

如果你打开 `rolling`，就不要只盯最终收益，必须一起看 rolling 稳定性。

### `entry_signal_mode`

默认从 `zscore` 开始。

只有在你想测试“收益率价差平滑后的入场”时，再试：

- `ret_spread_ema`
- `ret_spread_sma`

`zscore` 的优点是解释最直接，也最容易和协整、ECM、half-life 放在同一框架里。

### `return_filter_mode`

只在 `entry_signal_mode=zscore` 下有意义。

推荐顺序：

1. 先用 `off`
2. 再试 `ema`
3. 最后才试 `sma`

原因是你应该先确认核心均值回归信号是否成立，再加过滤器，而不是一开始就把信号链条叠太厚。

### `cointegration_gate_mode`

推荐研究默认：

```text
significant
```

它的作用是：

- 只有 rolling 协整仍显著时才允许开仓
- 持仓中如果 rolling 协整失效，也会触发强平

这通常比完全关闭 gate 更稳。

### `ecm_gate_mode`

推荐作为第二层 gate，而不是主信号：

```text
significant_negative
```

它要求 rolling ECM 的 error-correction speed：

- 为负
- 且显著

建议用法：

- 先比较只有 cointegration gate 的结果
- 再比较 cointegration + ECM 双 gate
- 不建议一开始就只开 ECM gate 而不看 rolling cointegration

### `half_life_anchor_mode`

推荐研究默认：

```text
training
```

它的用途不是“让模型更高级”，而是让两个关键参数更少拍脑袋：

- `z_window`
- `max_holding_days`

推荐起点：

- `half_life_z_window_multiplier=2.0`
- `half_life_max_holding_multiplier=1.5`

如果你发现：

- 交易太少
- 持有期太短
- `effective_max_holding_days` 过于紧

再逐步放大 multiplier。

### `z_grid`

建议不要把候选集开得太密。先从：

```text
1.5,2.0,2.5
```

开始足够。

如果你一开始就把阈值搜索开得很细，很容易把训练期噪音当成“优化结果”。

### `max_adv_fraction`

推荐保留：

```text
0.05
```

意义是：

- 单腿订单名义规模不超过 trailing ADV 的 5%
- 防止仓位 sizing 比流动性现实更乐观

如果你只是 smoke test，可以临时关掉：

```bash
--max-adv-fraction -1
```

但正式研究不建议长期关闭。

## 7. 成本模型该怎么用

当前成本层分四类：

- 显式手续费
- base slippage
- 基于 ADV 的 impact
- `paired` 下的 borrow / financing carry

### 一个实用原则

先接受“保守一点”，不要先追求“看起来收益更高”。

### 基础手续费

默认成本已经包含：

- A 股买卖成本
- H 股买卖基础成本
- H 股印花税
- FX conversion

如果你有更接近自己券商或交易通道的数字，优先改这些。

### 滑点与冲击

新增参数主要是：

- `--a-slippage-bps`
- `--h-slippage-bps`
- `--a-impact-bps-per-100pct-adv`
- `--h-impact-bps-per-100pct-adv`

推荐做两组实验：

1. 当前默认值
2. 更悲观的一组，例如把 slippage 和 impact 都上调 50% 到 100%

如果策略只有在极度乐观的执行假设下才成立，那比任何 ECM 调优都更值得先面对。

### Borrow 与 financing

这些参数只在 `paired` 下会显著影响结果：

- `--a-short-borrow-apr-bps`
- `--h-short-borrow-apr-bps`
- `--a-long-financing-apr-bps`
- `--h-long-financing-apr-bps`

推荐做三组 paired 压测：

1. 默认值
2. borrow 翻倍
3. borrow 翻倍且 financing 非零

如果 paired 收益对 borrow 非常脆弱，就不要把它当成“稳定可执行”的结果。

## 8. Benchmark 怎么配

### long-only

默认建议：

```text
--benchmark-mode auto
```

这样在 `long_cheaper_leg_only` 下会自动生成内部 A/H basket benchmark。

你真正该问的是：

> 这个信号驱动买入，是否优于被动持有同一发行人的 A/H 篮子？

### paired

默认 `auto` 下不会自动给你内部 benchmark。

你有三种常见做法：

1. `--benchmark-mode off`
2. 显式提供外部 benchmark
3. 手动切到 `--benchmark-mode internal`

如果你要评估传统多空配对，很多时候直接先看自身收益、回撤、成本和 beta 就够了。

## 9. 五套可直接照抄的研究配方

### 配方 A：最小可运行 smoke test

```bash
pairs-trading --config configs/petrochina_smoke.toml
```

用途：

- 检查安装
- 检查联网
- 检查缓存
- 检查输出目录

不适合：

- 正式结论

### 配方 B：推荐的 long-only 研究基线

```bash
pairs-trading --config configs/petrochina_research.toml
```

用途：

- 当前推荐研究口径
- 包含 next-open、rolling hedge ratio、双 gate、half-life anchor、ADV cap

### 配方 C：对比“有没有 ECM gate”

基线：

```bash
pairs-trading \
  --config configs/petrochina_research.toml \
  --ecm-gate-mode off \
  --output-dir artifacts/runs/petrochina_no_ecm_gate
```

对照：

```bash
pairs-trading \
  --config configs/petrochina_research.toml \
  --ecm-gate-mode significant_negative \
  --output-dir artifacts/runs/petrochina_with_ecm_gate
```

重点比较：

- trade count
- win rate
- max drawdown
- total costs
- `rolling_ecm.csv` 里的 gate 稳定性

### 配方 D：对比 `training` 和 `rolling` hedge ratio

```bash
pairs-trading \
  --config configs/petrochina_research.toml \
  --hedge-ratio-mode training \
  --cointegration-gate-mode off \
  --ecm-gate-mode off \
  --output-dir artifacts/runs/petrochina_training_hr
```

```bash
pairs-trading \
  --config configs/petrochina_research.toml \
  --hedge-ratio-mode rolling \
  --cointegration-gate-mode significant \
  --ecm-gate-mode significant_negative \
  --output-dir artifacts/runs/petrochina_rolling_hr
```

重点不是只看谁收益高，而是看 rolling 版本是否真的更稳。

### 配方 E：paired 压测

```bash
pairs-trading \
  --config configs/petrochina_research.toml \
  --execution-mode paired \
  --a-short-borrow-apr-bps 500 \
  --h-short-borrow-apr-bps 300 \
  --a-long-financing-apr-bps 100 \
  --h-long-financing-apr-bps 100 \
  --output-dir artifacts/runs/petrochina_paired_stress
```

适合检验：

- paired 的收益是否只是建立在乐观 borrow 假设上

## 10. 配置文件怎么写

推荐做法是：

1. 先用 `configs/petrochina_research.toml` 复制一份
2. 在 `configs/` 下改成你自己的文件
3. 平时只在 CLI 上覆盖少数临时参数

例如：

```toml
a_symbol = "600036"
h_symbol = "03968"
start_date = "2018-01-01"
end_date = "2024-12-31"
train_end_date = "2022-12-31"
fx_csv = "../data/fx/hkdcny_2018_2024.csv"
execution_mode = "long_cheaper_leg_only"
execution_timing = "next_open"
hedge_ratio_mode = "rolling"
cointegration_gate_mode = "significant"
ecm_gate_mode = "significant_negative"
half_life_anchor_mode = "training"
half_life_z_window_multiplier = 2.0
half_life_max_holding_multiplier = 1.5
max_adv_fraction = 0.05
output_dir = "../artifacts/runs/my_pair_research"
```

然后临时覆盖：

```bash
pairs-trading \
  --config configs/my_pair_research.toml \
  --execution-mode paired \
  --output-dir artifacts/runs/my_pair_research_paired
```

优先级始终是：

```text
内置默认值 < TOML 预设 < 显式 CLI 参数
```

## 11. 推荐的研究节奏

如果你要认真做一个新标的，建议按这个节奏。

### Phase 1: 数据与口径确认

- 跑 smoke test
- 准备 FX CSV
- 确认 A/H 代码映射
- 不要先开 `allow_non_coint`

### Phase 2: 基线结果

- 跑推荐 research preset
- 读 `summary.md`
- 查训练期协整与阈值搜索

### Phase 3: 稳定性诊断

- 看 `rolling_cointegration.csv`
- 看 `rolling_ecm.csv`
- 看 `test_trades.csv` 中强平原因

### Phase 4: 成本压测

- 上调 slippage
- 上调 impact
- paired 时上调 borrow / financing

### Phase 5: 参数对照

一次只改一类参数，例如：

- 是否开 ECM gate
- 是否开 half-life anchor
- `training` vs `rolling` hedge ratio
- long-only vs paired

不要一次改五六个参数，不然最后你无法归因。

## 12. 什么时候该怀疑结果

出现下面这些情况时，先别急着讲策略逻辑。

### 收益很好，但交易极少

先看：

- `trade_count`
- `win_rate`
- `profit_factor`
- 单笔大赚是否主导了结果

### paired 很强，但 borrow 几乎没影响

先确认：

- 你是不是根本没开 `paired`
- 借券参数是不是仍接近零
- 样本期是不是持有期过短导致 carry 不显著

### 开了 gate 后回测变得很好看

先确认：

- 交易是不是被压得太少
- gate 是否主要在回撤后才触发
- `rolling_ecm.csv` 是否只是偶然把坏样本过滤掉

### half-life anchor 后结果大变

先看 `summary.md` 里的 effective 参数：

- `effective_z_window`
- `effective_max_holding_days`

有时不是 half-life 本身有魔法，而是它把你的窗口和持有期改到了完全不同的量级。

## 13. 常见命令模板

### 临时关闭 stage cache 复跑

```bash
pairs-trading \
  --config configs/petrochina_research.toml \
  --refresh-cache \
  --output-dir artifacts/runs/petrochina_refresh
```

### 只恢复已完成的 pipeline 阶段

```bash
pairs-trading \
  --config configs/petrochina_research.toml \
  --resume-from-cache
```

### 宽松探索，不因训练期协整失败中止

```bash
pairs-trading \
  --config configs/petrochina_research.toml \
  --allow-non-coint \
  --output-dir artifacts/runs/petrochina_allow_non_coint
```

这只适合：

- 流程排查
- 宽松探索

不适合正式结论。

### 关闭容量约束做对照

```bash
pairs-trading \
  --config configs/petrochina_research.toml \
  --max-adv-fraction -1 \
  --output-dir artifacts/runs/petrochina_no_adv_cap
```

适合检验：

- 你的结果是否严重依赖乐观容量假设

## 14. 一页版 runbook

如果你只想要最短流程，就按下面做：

1. 安装依赖。
2. 运行 `pairs-trading --config configs/petrochina_smoke.toml`。
3. 下载 FX 历史到 `data/fx/`。
4. 运行 `pairs-trading --config configs/petrochina_research.toml`。
5. 先看 `summary.md`，确认 execution timing、gate、effective 参数和成本拆分。
6. 再看 `signal_frame.csv`、`rolling_cointegration.csv`、`rolling_ecm.csv`、`test_trades.csv`。
7. 做对照实验时一次只改一类参数。
8. 正式研究优先压测 slippage、impact、borrow，而不是先把模型堆得更复杂。

## 15. 相关阅读

- 按研究目标分类的剧本：[`research_playbooks.md`](research_playbooks.md)
- 策略定位与逻辑：[`strategy_overview.md`](strategy_overview.md)
- 执行模式：[`execution_modes.md`](execution_modes.md)
- 数据、FX 与缓存：[`data_and_fx.md`](data_and_fx.md)
- benchmark 与结果解读：[`benchmark_and_metrics.md`](benchmark_and_metrics.md)
- CLI 参数字典：[`cli_reference.md`](cli_reference.md)
- 常见报错：[`common_errors.md`](common_errors.md)
