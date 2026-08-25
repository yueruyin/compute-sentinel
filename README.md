# Compute Sentinel / 算力哨兵

面向 Token 算力产业链的 Codex 原生股票监控技能。项目覆盖云厂商、大模型、光模块与光通信、AI 芯片、先进封装、存储芯片、PCB 和 AI 储能，抓取 A 股及港股行情与日 K 数据，计算 MA10、MA20、MA60、ATR、参考区间、目标位和风险收益比，并输出板块强弱、盈亏比决策矩阵及中文风险监控报告。

项目只用于市场研究和风险观察，不执行自动交易，也不构成投资建议。

## 当前能力

- 监控 14 只 A 股及港股标的，关注池可配置。
- 批量抓取行情，并保留数据源、行情时间和失败原因。
- 计算 MA10、MA20、MA60、ATR、支撑区、止损参考、目标参考及风险收益比。
- 汇总云厂商、大模型、光模块/光通信、AI 芯片、先进封装、存储芯片、PCB 和 AI 储能板块。
- 输出六级盈亏比决策矩阵，空层级也会保留。
- 支持 Codex 桌面端的盘前、盘中定时任务。

## 安装为 Codex 技能

```bash
git clone https://github.com/yueruyin/compute-sentinel.git
cd compute-sentinel
mkdir -p ~/.codex/skills
ln -s "$(pwd)/codex-skills/token-compute-watch" ~/.codex/skills/token-compute-watch
```

安装后可在 Codex 中直接输入：

```text
使用 $token-compute-watch 运行一次 Token 算力链盘中监控。
```

## 直接运行行情脚本

```bash
python3 codex-skills/token-compute-watch/scripts/price_monitor.py --format markdown
python3 codex-skills/token-compute-watch/scripts/price_monitor.py --format json
```

关注池位于：

```text
codex-skills/token-compute-watch/references/watchlist.json
```

## 测试

```bash
python3 -m unittest codex-skills/token-compute-watch/scripts/test_price_monitor.py
```

## 数据与限制

A 股行情与日 K 主要来自东方财富公开接口，行情失败时可退回新浪；港股行情和日 K 来自腾讯财经公开接口。日 K 沿用对应市场现有前复权口径，并以同一次返回结果的 `last_bar_date` 作为 MA10、MA20 和 MA60 的统一截止日期。免费接口可能延迟、限流或发生变更，报告会显式保留异常，不会用历史配置伪装成实时数据。

MA60 是按交易日期排序后最近 60 根有效日 K 收盘价的算术平均值，即 `MA60 = sum(最近60根收盘价) / 60`，不是 60 个自然日。有效历史不足 60 根时，JSON 输出 `null`，Markdown 显示历史不足，不会用较短窗口伪造数值。MA60 仅用于展示，不参与现有参考区、目标位、风险收益比、信号或决策矩阵判定。

风险收益比仅衡量脚本定义的价格几何关系，不代表上涨概率，不能替代基本面、公告、流动性和个人风险承受能力判断。

## 来源与许可

本项目由 `hermes-stock-watchers` 的监控思路改造成 Codex 原生技能。第三方来源及原始 MIT 许可见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。本仓库其余内容按 [Apache License 2.0](LICENSE) 发布。
