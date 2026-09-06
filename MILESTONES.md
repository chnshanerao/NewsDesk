# NEWSDESK 里程碑验收

更新时间：2026-09-06

| 里程碑 | 状态 | 可验证结果 |
| --- | --- | --- |
| 全球信源 | 阶段完成 | 67 个启用信源、55 个 owner；36 条 AI 专线含 21 个一次源、19 个近 30 天活跃一次源和 15 个独立/专业采编源 |
| 事件与证据 | 完成 | Schema v10 为每条引用保存 support/refute/unknown 关系、置信度和方法；4,364 条引用切片与 hash 全量校验，孤儿引用为 0 |
| 检索与监控 | 完成 | 结构化查询、可移除条件、持久监控、未读事件、Webhook 重试队列 |
| 市场上下文 | 阶段完成 | 中港美股指、外汇、美债、贵金属、WTI；历史点位与观察列表；7 天历史需随墙钟时间继续积累 |
| 个人化与多样性 | 完成 | 公共全景/个人推荐分离、owner 30% 上限、多语种混排 |
| 可靠性与运维 | 完成 | 自动刷新、并发锁、探针、指标、迁移、原子备份、恢复演练、结构化日志、CI |
| 实体与证券主数据 | P0 完成 | 148 个 canonical entity、109 家公司，并覆盖模型、实验室、AI 芯片和开源项目；加入上下文消歧及持久证据片段 |
| AI / 科技雷达 | P0 完成 | 六赛道、36 条专线、近 72 小时 350 个 AI 事件，一次源/媒体跟进/独立印证分开展示 |
| 引用研究工作台 | P1 阶段完成 | 抽取式逐句引用研究答案；证据显示支持/反驳/待判定，无引用不输出 |
| 可扩展检索 | 完成 | SQLite FTS5、服务端资产/实体过滤，不再先截断 400 条后过滤 |
| 真实评测工作流 | 待人工标注 | 已冻结 300 对生产 claim–quote 样本，具备双人盲标、第三人仲裁、身份/完整性/快照 hash 校验；尚未把机器标签冒充人工金标 |
| 官方结构化源 | 完成 | 中国政府网 JSON、央行/统计/SEC/EIA、美国国债与 ECB 参考数据 |
| 公网安全 | 完成 | CSP、安全响应头、同源 + 管理令牌双校验、请求体限制、Webhook SSRF/重定向防护、私有数据权限收紧 |

## 验收命令

```bash
python3 -m compileall -q newsdesk tests
node --check web/app.js
python3 -m unittest discover -s tests -v
./bin/newsdesk schema
./bin/newsdesk recovery-drill
./bin/newsdesk quality --strict
./bin/newsdesk evaluate-clustering --strict
./bin/newsdesk benchmark --strict
```

质量门禁也可通过 `/api/quality` 获取；当前实测为 45 pass / 6 warn / 0 fail，93/93 自动化测试通过。`healthy_with_warnings` 表示硬性门禁通过，但国际稿占比、owner 集中度、独立印证率、7 天行情历史和真实人工金标仍有未达目标项。

## 能力边界

NEWSDESK 已达到“个人新闻情报终端”的产品里程碑，但不声称等同 Bloomberg Terminal。当前市场数据来自公开延迟接口，仅用于新闻背景，不适合交易执行。Bloomberg 的专有实时交易所数据、授权 Reuters/AP 内容、定价模型、即时通讯、订单与合规工作流，需要商业数据许可和金融基础设施，不能靠公开抓取合法替代。
