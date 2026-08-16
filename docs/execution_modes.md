# 执行模式说明

## 总览

项目支持两种执行模式：

- `long_cheaper_leg_only`
- `paired`

两者共享同一套信号生成逻辑，但执行层含义不同。

## `long_cheaper_leg_only`

### 行为

- `z > 0` 时只买 H 股
- `z < 0` 时只买 A 股
- 不做空另一个标的

### 适用场景

- 不能做空的账户
- 想把 A/H 相对价值信号转成单腿买入决策的使用者

### 优点

- 可执行性更强
- 与大陆居民常见账户约束更匹配
- 文档和回测口径更诚实

### 代价

- 不再市场中性
- 会承受单边市场、行业、风格和流动性暴露
- 价差收敛不一定直接转化为持仓盈利

### Benchmark 行为

在 `--benchmark-mode auto` 下，项目会自动生成内部 A/H 被动 basket benchmark，用来衡量策略是否优于被动持有同一发行人 A/H 篮子。

## `paired`

### 行为

- `z > 0` 时做 `short_a_long_h`
- `z < 0` 时做 `long_a_short_h`

### 适用场景

- 允许做空、融券或具备双边持仓能力的环境
- 想研究更接近传统 pairs trading 的收益结构

### 代价

- 真实存在负仓位
- 对账户权限和交易基础设施要求更高
- 对成本、借券可得性和融资拖累更敏感

当前回测会显式计入：

- 基础手续费
- H 股印花税与 FX 换汇成本
- base slippage 与基于 ADV 的冲击成本
- `paired` 下的 short borrow 与 long financing carry

### Benchmark 行为

在 `--benchmark-mode auto` 下，如果你没有显式提供 benchmark，项目不会为 `paired` 自动生成内部 benchmark。

## 与回测引擎的关系

`--execution-mode` 决定逐笔引擎里如何处理信号，`--backtest-engine` 决定用哪套回测逻辑。两套引擎都接受这两种执行模式的信号语义：

- `trade` 引擎按模式决定是否真实做空
- `weight` 引擎输出双腿连续权重，适合在 `paired` 语义下观察倾斜方向

## 如何选择

如果你是大陆居民、账户不能做空，默认应该从 `long_cheaper_leg_only` 开始。

如果你具备可做空环境，且目标更接近传统双腿配对交易，再使用 `paired`。

## 重要提醒

- `paired` 模式会真实做空。
- `long_cheaper_leg_only` 本质上是买入相对低估的标的。
- 如果你的账户连 H 股也不能买，那么默认模式也不能完整落地。
