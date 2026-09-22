# NYC 311 服务请求分析

这是项目1的公开仓库。项目基于 NYC Open Data 的 311 Service Requests 数据，主要分析住宅噪声、违规停车、地区差异和异常关闭记录，并做了一个预测扩展。

## 项目索引

| 项目 | 内容与入口 |
| --- | --- |
| [项目1：NYC311 服务请求分析](https://github.com/Rainbow-bot-bot/nyc311-service-request-analysis) | 当前仓库：数据采集、清洗、SQL、专题核验、Power BI 与项目报告。 |
| [项目2：AI 自主交付评测](https://github.com/Rainbow-bot-bot/nyc311-ai-agent-evaluation) | 使用项目1的原始数据快照，比较 AI 的分析交付质量、执行可靠性、耗时与成本。 |

项目2使用项目1采集并保存的 NYC311 数据，共25份Parquet、7,525,498条记录。原始Parquet未上传，文件清单和哈希见[项目2输入清单](https://github.com/Rainbow-bot-bot/nyc311-ai-agent-evaluation/blob/main/input/data_manifest.json)。

## 建议阅读顺序

1. [项目报告 PDF](交付成果/NYC311分析报告.pdf)
2. [开始阅读](交付成果/开始阅读.html)
3. [探索过程](交付成果/探索过程/)
4. [自动化实现](交付成果/自动化实现/)
5. [Power BI 成果报表](https://github.com/Rainbow-bot-bot/nyc311-service-request-analysis/releases/tag/v1.0-delivery)：`NYC311.pbix`，约569 MiB

## 交付内容

1. `NYC311分析报告.pdf` / `.docx`：项目报告
2. `探索过程/`：数据摸底 Notebook、SQL 分析 Notebook、批量 SQL
3. `自动化实现/`：自动化入库脚本、字段说明、数据质量规则、运行说明
4. `Power BI结果展示/`：PBIX 下载说明

## 数据说明

数据来自 NYC Open Data 311 Service Requests。项目使用 2026-09-07 的本地快照，共25份 Parquet、7,525,498条记录；原始 Parquet 不上传。

报告里的工单量指提交记录数，关闭时长指系统记录中的创建到关闭时长，不等同于独立现实事件数或实际问题解决耗时。

## Power BI

`NYC311.pbix`，约569 MiB：[v1.0-delivery Release](https://github.com/Rainbow-bot-bot/nyc311-service-request-analysis/releases/tag/v1.0-delivery)
