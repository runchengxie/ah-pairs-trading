# 数据、FX 与缓存

## 目录约定

- `data/` 保留给手工维护或外部导入的输入文件，例如 `data/fx/*.csv`
- `artifacts/cache/ah_pairs_trading/` 保存自动数据缓存和可选 stage cache
- `artifacts/runs/<run-name>/` 保存每次运行生成的 CSV、图表和摘要
- `configs/` 保存可直接传给 `--config` 的 TOML 预设，文件里的相对路径会相对 TOML 文件自身解析

## 数据来源

`--data-provider` 支持三种数据源：

### `akshare`，默认值

在线拉取 A 股、H 股和 benchmark 历史，并按 symbol 维护增量缓存。适合正式研究和有网络的环境。

### `tushare`

使用 Tushare 拉取 A/H 历史。需要 `--tushare-token` 或环境变量 `TUSHARE_TOKEN`。A 股代码用 `600036.SH` 这类 `ts_code`，H 股代码用 `03968.HK` 这类 `ts_code`。如果你在 Tushare 没有 H 股数据权限，程序会给出报错提示。

### `simulated`

离线模拟数据，不需要联网也不需要 token。模拟器生成两条具有已知协整结构的行情，价差服从 OU 过程。适合跑通完整链路、写测试，或验证滚动 OU MLE 与权重引擎。

```bash
pairs-trading \
  --data-provider simulated \
  --constant-fx-rate 0.92 \
  --start-date 2018-01-01 \
  --end-date 2024-12-31 \
  --train-end-date 2022-12-31 \
  --allow-non-coint
```

## 数据输入优先级

项目支持三种数据入口：

- 显式传本地 CSV
- 在线或模拟拉取 A/H 历史
- 读取本地增量缓存

优先级规则如下：

- 如果显式传了 `--a-csv` / `--h-csv` / `--fx-csv`，程序会直接使用这些文件
- 如果没有显式传 A/H CSV，程序会先查本地增量主档缓存
- 本地主档没有覆盖请求区间时，才会通过数据源在线补抓缺口

主回测 CLI 不会自动联网拉取 FX，必须自己提供：

- `--fx-csv`
- 或 `--constant-fx-rate`
- 或使用 `simulated` 数据源时，程序会默认填入一个静态汇率

如果你缺少 `fx.csv`，仓库提供了一个独立脚本，可以先下载再回测：

```bash
python scripts/fetch_fx_history.py \
  --start-date 2018-01-01 \
  --end-date 2024-12-31 \
  --output-csv data/fx/hkdcny_2018_2024.csv
```

这个脚本通过 Frankfurter 拉取 ECB 参考汇率，用 `EUR->CNY` 和 `EUR->HKD` 交叉换算成项目需要的 `HKD/CNY` 历史。建议把这类 FX CSV 放在 `data/fx/`，因为它是用户可复现输入，而不是自动生成的运行产物。

## A/H 与 FX 的价格口径

项目最终研究的是人民币口径下的 H 股价格：

```text
h_close_cny = h_close_hkd * fx_rate * share_ratio
```

其中：

- `fx_rate` 是 HKD/CNY
- `share_ratio` 用于处理 A/H 股数换算关系，默认是 `1.0`

这一步是 A/H 相对价值研究的基础。

## 日期对齐

项目会对齐 A 股、H 股和 FX 历史，并在共同可研究日期上构建价格序列。

当前实现的要点：

- A/H 历史各自先标准化
- 再按共同日期做对齐
- FX 在对齐后按日期前向填充

这也意味着 FX CSV 不需要覆盖每个自然日，只要工作日参考汇率能覆盖研究窗口，程序会在对齐后前向填充。

## `--constant-fx-rate` 与 `--fx-csv`

### `--constant-fx-rate`

适合：

- smoke test
- 快速原型验证
- 环境检查

不适合正式回测和长样本的结果解释。

### `--fx-csv`

更适合：

- 正式回测
- 复现实验
- 需要保留历史汇率波动影响的研究

脚本产出的 CSV 结构示例：

```text
date,fx_rate,eur_cny,eur_hkd
2018-01-02,0.7834,7.8023,9.9594
...
```

其中 `fx_rate` 是项目实际读取的 `HKD/CNY`，`eur_cny` 和 `eur_hkd` 只是为了追溯换算来源，主回测会忽略它们。

## Same-Issuer 校验

项目内置了一份人工维护的 A/H same-issuer registry，用于拦截明显错配。

默认行为是 `--same-issuer-check strict`，会拦住已知错配，例如：

- `601857/00883`

该组合会被识别为 `601857` 对应 PetroChina，`00883` 对应 CNOOC。

可选行为：

- `warn`
- `off`

如果你要故意做跨公司配对研究，可以手动放宽。

## 数据缓存与阶段缓存

这里有两类不同缓存：

- 数据缓存，A/H 与 benchmark 的原始历史主档缓存，默认会自动参与
- 阶段缓存，pipeline 中间计算结果缓存，只有显式加 `--resume-from-cache` 才会恢复

### 数据缓存现在怎么工作

- A/H 与 online benchmark 不再按某次请求区间的结果整块缓存
- 缓存单位改成了 symbol 级主档，例如某个 A 股代码会在 `artifacts/cache/ah_pairs_trading/data/a_share_history/` 下维护一份累计历史
- 每个主档旁边都有一份 JSON manifest，记录 `coverage_start`、`coverage_end`、`observation_count`、最近一次请求窗口等元数据
- 如果新的回测窗口被现有主档完全覆盖，程序不会重新抓数
- 如果新的窗口只在左边或右边超出已缓存区间，程序只会补抓缺口，再合并回主档
- `--refresh-cache` 会重建该 symbol 已知覆盖范围内的主档，不会把主档缩成当前请求子区间

### 当前存储分层

- 原始市场数据主档使用 `pickle + json manifest`，默认位于 `artifacts/cache/ah_pairs_trading/data/...`
- pipeline stage cache 也使用 `pickle`，默认位于 `artifacts/cache/ah_pairs_trading/pipeline/...`
- 每次显式指定 `--output-dir` 时，运行产物更推荐统一写到 `artifacts/runs/<run-name>/`
- 这样做的目的是保持依赖轻量，仓库当前没有引入 Parquet 或 DuckDB 运行时依赖
- 如果后面要做更大规模的多标的研究，再把原始数据主档迁到 Parquet，让 DuckDB 直接查询会更合适

`--resume-from-cache` 的作用是复用已经完成的 pipeline 阶段。

它不会做这些事：

- 跳过训练集协整显著性校验
- 改写数据源优先级
- 把不存在的本地 CSV 自动变成在线拉取
- 改变原始市场数据的增量缓存策略

## 本地 CSV 的使用建议

显式传本地 CSV 适合：

- 离线研究
- 使用自己清洗过的数据
- 固定输入以便复现实验

注意：

- 路径不存在时会立刻报错
- 仓库不附带示例 CSV
- 需要把占位路径替换成你机器上的真实文件
