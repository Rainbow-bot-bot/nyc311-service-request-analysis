# NYC 311 服务请求分析

本仓库公开项目的最终交付材料。项目基于 NYC Open Data 的 311 Service Requests 数据，围绕住宅噪声、违规停车、地区差异、异常关闭记录与预测扩展进行分析。

## 建议阅读顺序

1. [项目报告 PDF](交付成果/NYC311分析报告.pdf)
2. [开始阅读](交付成果/开始阅读.html)
3. [探索过程](交付成果/探索过程/)
4. [自动化实现](交付成果/自动化实现/)
5. Power BI 成果报表：见本仓库 **Releases → v1.0-delivery**

## 交付内容

- `NYC311分析报告.pdf` / `.docx`：完整项目报告
- `探索过程/`：数据摸底 Notebook、SQL 分析 Notebook 与批量 SQL
- `自动化实现/`：自动化入库脚本、字段说明、数据质量规则及运行说明
- `Power BI结果展示/`：PBIX 下载说明；完整文件放在 GitHub Release 中

## 数据与边界

原始数据来自 NYC Open Data 311 Service Requests。本地项目使用 2026-09-07 的快照，25 份 Parquet 共 7,525,498 条记录；原始 Parquet 不随本仓库发布。项目报告中的工单量表示提交记录数，关闭时长表示系统记录的创建至关闭自然时间，不将其解释为独立现实事件数或真实问题解决耗时。

## Power BI

完整 `NYC311成果展示.pbix` 约 569 MB，不进入 Git 历史。请从 [v1.0-delivery Release](https://github.com/Rainbow-bot-bot/nyc311-service-request-analysis/releases/tag/v1.0-delivery) 下载。
