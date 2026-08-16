# 仓库维护说明

## 定位

本仓库是 A/H 相对价值研究平台，整合了从 `wu-pairs-spread-arbitrage` 迁移过来的滚动 OU MLE 权重引擎。新增研究功能放入本仓库，旧仓库 `wu-pairs-spread-arbitrage` 已归档，只接受归档声明相关的修改。

## 修改要求

- 禁止提交 token、密码、代理地址和真实账户信息。
- 保持 `src/ah_pairs_trading/` 下的公开函数与 CLI 参数名稳定，调整语义时补充回归测试。
- 测试不得访问真实网络，`simulated` 数据源用于离线跑通完整管线。
- 新增数据源或回测引擎时，同步更新 `README.md`、`docs/` 和 `tests/test_docs_contract.py`。
- 运行 `uv run pytest -q` 确认全部通过后再结束任务。
- 文档以中文为主，使用中文标点，保留必要的行内代码引用。
- 文本尽量写成中文母语读者通顺易懂的形式，避免翻译腔和过深术语。
- 删除旧功能前确认其能力已被 `src/ah_pairs_trading/` 覆盖。

## 并行开发流程

多个 agent 可能同时修改本仓库。为避免竞争，所有改动都在独立 worktree 上完成：

1. 在仓库外新建 worktree 和分支，例如 `git worktree add ../ah-pairs-trading-wt -b feat/xxx`。
2. 只在对应的 worktree 里修改并提交，主工作区保持 main 分支的干净状态。
3. push 分支并开 PR 到 main。
4. PR 合并后删除远端和本地分支。
5. 用 `git worktree remove ../ah-pairs-trading-wt` 删除 worktree。
6. 提交前运行 `uv run pytest -q` 确认全部通过。

## 研究约束

任何收益结论都需要在统一的数据口径、历史股票池和真实成本下重新计算。当前回测输出只用于代码诊断和研究探索。
