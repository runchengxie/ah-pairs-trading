# 常见报错与处理

当前目录约定：

- `data/` 保留给手工维护或外部导入的输入文件
- `artifacts/cache/ah_pairs_trading/` 是默认缓存目录
- `artifacts/runs/<run-name>/` 是推荐的运行结果目录

## 1. `Training-sample cointegration is not significant ...`

原因：

- 默认要求训练集协整显著
- 当前训练窗口里，这对 A/H 标的没有通过协整显著性检查

处理：

- 如果你只是想先跑通流程或做宽松探索：加 `--allow-non-coint`
- 如果你在做严肃研究：不要加，换标的、换窗口，或重新核对 A/H 代码对

## 2. 加了 `--resume-from-cache` 还是报协整不显著

原因：

- `--resume-from-cache` 只恢复中间阶段
- 它不会绕过协整显著性校验

处理：

- 保持 guardrail：不要加 `--allow-non-coint`
- 宽松跑完整流程：显式加 `--allow-non-coint`

## 3. `FileNotFoundError: ... data/xxxx.csv`

原因：

- 你显式传了本地 CSV 路径
- 但那个文件在本机上并不存在

处理：

- 如果你本来想走推荐主流程，直接去掉 `--a-csv` / `--h-csv`
- 如果你就是要用本地文件，把路径换成真实文件

## 4. `An FX series is required for A/H normalization ...`

原因：

- A/H 研究需要 FX 才能把 H 股价格转换成人民币口径
- 你没有提供 `--fx-csv`，也没有提供 `--constant-fx-rate`

处理：

- 正式回测：提供 `--fx-csv`
- 如果你手头没有 FX CSV，可以先运行：

```bash
python scripts/fetch_fx_history.py \
  --start-date 2018-01-01 \
  --end-date 2024-12-31 \
  --output-csv data/fx/hkdcny_2018_2024.csv
```

- 这类 FX CSV 仍建议保留在 `data/fx/`，主回测结果则统一写到 `artifacts/runs/<run-name>/`
- 快速验证：临时提供 `--constant-fx-rate`

## 5. `601857/00883` 这种代码对直接被拦下

原因：

- `--same-issuer-check strict` 会拦住已知的跨发行人错配
- `601857` 对应 PetroChina，`00883` 对应 CNOOC

处理：

- 如果你在做同发行人 A/H 相对价值，把 H 股代码改成正确映射
- 如果你在做跨公司研究，可以改成 `--same-issuer-check warn` 或 `off`

## 6. 输出显示的是 `Execution mode: long_cheaper_leg_only`

原因：

- 你没有显式传 `--execution-mode paired`

处理：

- 如果你要双腿多空模式，把 `--execution-mode paired` 写回命令

## 7. `paired` 模式下没有自动 benchmark

原因：

- `--benchmark-mode auto` 只会在 `long_cheaper_leg_only` 下自动生成内部 benchmark

处理：

- 如果你要比较 benchmark，显式传 `--benchmark` 或 `--benchmark-csv`
- 或把 `--benchmark-mode` 改成 `internal`

## 8. 无法在线拉取 A/H 历史

原因：

- 本地没有安装依赖
- 或在线数据源不可用

处理：

- 先安装项目依赖
- 或改为显式传本地 CSV
- 如果你怀疑 `artifacts/cache/ah_pairs_trading/` 下的 symbol 主档已经过期或不一致，可以加 `--refresh-cache` 强制重建
