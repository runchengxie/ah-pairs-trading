# A/H 相对价值策略说明

## 项目定位

本项目当前实现的是 A/H 同发行人标的的相对价值研究框架，不是默认可落地的市场中性统计套利模板。

- 默认执行模式是 `long_cheaper_leg_only`
- `paired` 仅作为双腿研究模式保留
- 对大陆居民或受限账户而言，重点是把相对价值信号转成可执行的 long-only 决策

## 1. 数据口径统一

- 读取 A 股、H 股和 HKD/CNY 历史
- 对齐共同可交易日期
- 将 H 股价格换算成人民币口径
- 最终研究的是 `A_close` 和 `H_close_cny`

## 2. 训练期估计协整关系

训练集使用 Engle-Granger 协整检验和 OLS 回归来估计：

- `intercept`
- `hedge_ratio`

如果训练期协整不显著，默认会直接中止；只有显式加 `--allow-non-coint` 才会继续跑完整条 pipeline。

## 3. 构造 spread 与 z-score

当前实现的核心信号是：

```text
spread = log(A) - intercept - hedge_ratio * log(H)
```

之后对 `spread` 计算滚动均值和滚动标准差，生成滚动 z-score。

信号解释如下：

- `z > 0`：H 股相对更便宜
- `z < 0`：A 股相对更便宜
- `z = 0` 附近：没有明显偏离

代码里对应的字段是：

- `cheap_leg`
- `pair_direction`
- `ret_spread`
- `ret_spread_ema`
- `ret_spread_sma`

其中 `pair_direction` 仍然会给出传统多空方向，但是否真的做空，取决于执行模式。

现在的执行层支持三种主信号模式：

- `zscore`
- `ret_spread_ema`
- `ret_spread_sma`

其中后两者会先对收益率价差的均线做滚动标准化，再沿用同一套入场阈值、止盈止损和持有期规则。

## 4. 入场、出场与参数选择

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

如果主信号仍然是 `zscore`，还可以额外打开 `return_filter_mode`，要求 `ret_spread` 的 `EMA/SMA` 方向先与均值回归方向一致，再允许入场。

默认参数见 [`cli_reference.md`](cli_reference.md)。

## 5. 两种执行模式

### `long_cheaper_leg_only`

- `z > 0` 时只买 H 股
- `z < 0` 时只买 A 股
- 不做空贵的一条腿

这让策略更接近“相对价值信号驱动的单腿买入”，而不是市场中性套利。

### `paired`

- `z > 0` 时做 `short_a_long_h`
- `z < 0` 时做 `long_a_short_h`

该模式会真实出现负仓位，只适用于允许做空的环境。

## 6. 为什么默认是 long-only

对不能做空的账户来说，默认 `long_cheaper_leg_only` 比“表面中性、实际不可执行”的策略更诚实。

但它也带来明确边界：

- 它不再是纯粹赚价差收敛的钱
- 会混入市场 beta、行业 beta 和风格暴露
- 价差回归可能靠贵腿下跌完成，而你持有的便宜腿未必上涨

因此，这个项目更适合被表述为：

- A/H 相对价值研究框架
- long-only 执行版相对价值策略

而不是：

- 默认市场中性统计套利模板

## 7. 诊断层与执行层的区别

当前代码中仍然保留了不少统计分析模块，例如：

- ECM
- 半衰期估计
- 滚动协整
- 矩阵 OLS
- VAR 稳定性诊断

这些内容当前主要用于诊断和研究，不直接驱动交易引擎。当前交易执行层的核心仍然是：

- 训练期协整估计
- 全样本滚动 z-score
- 训练集阈值搜索
- `long_cheaper_leg_only` 或 `paired` 回测

## 8. 适用与不适用场景

适合：

- A/H 同发行人相对价值研究
- 不能做空的账户做信号筛选
- 需要明确计入成本、lot size 和 FX 的回测

不适合：

- 把它宣传成默认市场中性套利模板
- 忽略 FX 历史做正式回测
- 忽略协整显著性检查直接下结论
- 把历史设计文档中的高级方法误认为当前已实装能力
