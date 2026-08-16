# A/H 相对价值策略说明

## 项目定位

本项目实现的是 A/H 同发行人标的的相对价值研究框架。默认执行模式是 `long_cheaper_leg_only`，会把相对价值信号转成可执行的单标的买入决策。`paired` 模式保留给允许做空的环境。

项目有两套回测引擎。一套是逐笔持仓引擎，围绕协整、ECM 和成本模型工作。另一套是从旧仓库迁移过来的滚动 OU MLE 权重引擎，输出连续倾斜权重。两套引擎共用同一份数据、信号和诊断管线。

## 1. 数据口径统一

- 读取 A 股、H 股和 HKD/CNY 历史
- 对齐共同可交易日期
- 将 H 股价格换算成人民币口径
- 最终研究的是 `A_close` 和 `H_close_cny`

数据源可以是 AkShare、Tushare 或离线模拟数据，详见 [`data_and_fx.md`](data_and_fx.md)。

## 2. 训练期估计协整关系

训练集使用 Engle-Granger 协整检验和 OLS 回归来估计：

- `intercept`
- `hedge_ratio`

如果训练期协整不显著，默认会直接中止。只有显式加 `--allow-non-coint` 才会继续跑完整条 pipeline。

## 3. 构造 spread 与 z-score

默认情况下，执行层使用训练期固定参数：

```text
spread = log(A) - intercept - hedge_ratio * log(H)
```

之后对 `spread` 计算滚动均值和滚动标准差，生成滚动 z-score。

如果打开 `--hedge-ratio-mode rolling`，执行层会改用滚动协整窗口估计的 `intercept` 和 `hedge_ratio`，并把最新窗口结果向后沿用到下一次滚动更新。

信号解释如下：

- `z > 0`，H 股相对更便宜
- `z < 0`，A 股相对更便宜
- `z = 0` 附近，没有明显偏离

代码里对应的字段是：

- `cheap_leg`
- `pair_direction`
- `ret_spread`
- `ret_spread_ema`
- `ret_spread_sma`

其中 `pair_direction` 会给出传统多空方向，但是否真的做空取决于执行模式。

执行层支持三种主信号模式：

- `zscore`
- `ret_spread_ema`
- `ret_spread_sma`

后两者会先对收益率价差的均线做滚动标准化，再沿用同一套入场阈值、止盈止损和持有期规则。

## 4. 滚动 OU MLE 诊断

权重引擎和均值回归 gate 都依赖滚动 OU MLE。对每个估计窗口，流程如下：

- 用 Johansen 估计协整 `beta`
- 构造价差 `S = log(A) - beta * log(H)`
- 对 `S` 做 OU 精确离散的 MLE，得到均值回归速度 `k`、长期均值 `L`、扩散强度 `a`
- 用 AR(1) 初值启动 L-BFGS-B 优化
- 估计创新项相关 `rho`
- 由 `k` 换算出半衰期 `half_life = ln(2) / k`
- 对标准化 OU 创新项做 Ljung-Box 检验

这些参数每天一组，保存在 `ou_params.csv`，并进入信号表 `signal_frame.csv` 的 `ou_*` 列。半衰期和 Ljung-Box p 值还参与 `--mean-reversion-gate-mode` 的质量过滤。

## 5. 入场、出场与参数选择

训练期会对候选入场阈值做网格搜索，默认候选值是：

```text
1.5, 2.0, 2.5
```

默认目标函数是 `sharpe_ratio`。

持仓规则：

- `|z|` 回到 `exit_z` 附近时平仓
- `|z|` 扩大到 `stop_z` 时止损
- 超过 `max_holding_days` 时强平
- 到样本最后一天时做 end-of-sample 平仓

执行假设：

- 默认 `execution_timing=next_open`
- 用 `t-1` 的收盘信号在 `t` 的开盘执行，避免同一根 bar 又出信号又成交
- 如果原始历史里有 `volume`，会进一步计算 trailing ADV，用于容量上限和冲击成本
- 如果缺少 `open`，会退化为使用 close 作为执行价

如果主信号仍然是 `zscore`，还可以额外打开 `return_filter_mode`，要求 `ret_spread` 的 EMA 或 SMA 方向先与均值回归方向一致，再允许入场。

如果打开 `--cointegration-gate-mode significant`，策略只会在最新滚动窗口协整仍显著时入场。持仓中若 gate 失效，也会触发强平。

如果打开 `--ecm-gate-mode significant_negative`，策略还会要求最新滚动 ECM 的 error-correction speed 为负且显著，否则拒绝开仓并在持仓中触发强平。

如果打开 `--mean-reversion-gate-mode`，策略会用 OU 半衰期区间和 Ljung-Box p 值过滤信号。`half_life_range` 只检查半衰期，`lb_filter` 只检查自相关，`both` 同时检查两者。失效时同样会在持仓中强平。

如果打开 `--half-life-anchor-mode training`，训练期 residual half-life 可以进一步重写：

- `z_window`
- `max_holding_days`

这样执行参数就不再完全依赖固定经验值。

默认参数见 [`cli_reference.md`](cli_reference.md)。

## 6. 两种执行模式

### `long_cheaper_leg_only`

- `z > 0` 时只买 H 股
- `z < 0` 时只买 A 股
- 不做空贵的一条腿

这会让策略接近相对价值信号驱动的单腿买入，适合不能做空的账户。

### `paired`

- `z > 0` 时做 `short_a_long_h`
- `z < 0` 时做 `long_a_short_h`

该模式会真实出现负仓位，只适用于允许做空的环境。

## 7. 仓位与风控

逐笔引擎默认按 `--position-size-fraction` 固定比例开仓。打开 `--position-sizing-mode vol_target` 后，开仓规模会按波动率目标缩放：

```text
h = min(max_leverage, target_vol / 组合年化波动)
```

权重引擎始终使用波动率目标仓位，并叠加两类回撤熔断：

- `--max-drawdown` 触发后，策略回到中性仓位并暂停 `--suspend-days` 天
- `--portfolio-max-drawdown` 触发后，策略永久清仓

## 8. 权重引擎的信号与执行

权重引擎把价差 z-score 映射成连续目标权重：

- `z` 越大，A 股相对越贵，权重越偏向 H 股
- `|z|` 小于 `--exit-z`，或超过 `--stop-z`，或 OU 质量 gate 不过，都回到中性 50/50
- `--min-weight` 限制每条腿的最小权重，防止仓位过度集中

权重在下一根 bar 执行，配合波动率目标杠杆。逐日收益按换手率计成本：

```text
cost = base_cost * turnover + impact_cost * turnover * 总敞口
```

## 9. 为什么默认是 long-only

对不能做空的账户来说，默认 `long_cheaper_leg_only` 比表面中性、实际不可执行的策略更诚实。

但它也有明确边界：

- 它赚的不再纯粹是价差收敛的钱
- 会混入市场 beta、行业 beta 和风格暴露
- 价差回归可能通过相对高估的标的下跌完成，而你持有的标的未必上涨

因此，这个项目更适合表述为 A/H 相对价值研究框架，或 long-only 执行版相对价值策略。

## 10. 诊断层与执行层的区别

代码中保留了相当数量的统计分析模块，例如：

- ECM
- 半衰期估计
- 滚动协整
- 矩阵 OLS
- VAR 稳定性诊断

矩阵 OLS 和 VAR 仍主要用于诊断。滚动协整、rolling ECM、half-life 和滚动 OU MLE 现在都可以进入执行层：

- 训练期协整估计
- 固定或滚动的 spread / hedge ratio
- 全样本滚动 z-score
- 可选 rolling cointegration gate
- 可选 rolling ECM gate
- 可选 OU 均值回归 gate
- 训练期 half-life 驱动的 z-window / max holding
- 训练集阈值搜索
- `long_cheaper_leg_only` 或 `paired` 回测
- 波动率目标仓位与回撤熔断
- 滚动 OU MLE 权重引擎

## 11. 适用与不适用场景

适合：

- A/H 同发行人相对价值研究
- 不能做空的账户做信号筛选
- 需要明确计入成本、lot size、FX、slippage、borrow cost 和容量约束的回测
- 用离线模拟数据验证管线，或研究滚动 OU MLE 估计的稳定性

不适合：

- 把它当成默认市场中性套利模板
- 忽略 FX 历史做正式回测
- 忽略协整显著性检查直接下结论
- 把历史设计文档中的高级方法当成当前已实装能力
