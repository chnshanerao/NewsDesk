# NEWSDESK

面向普通人的零依赖个人新闻终端：聚合可信来源，把同一事件合并，并用可解释规则区分“值得看、待证、噪音”。

当前交付范围与验收状态见 [MILESTONES.md](MILESTONES.md)。
AI / 科技垂直路线与量化门槛见 [AI_TECH_ROADMAP.md](AI_TECH_ROADMAP.md)。

## 快速开始

需要 Python 3.10+，无需安装第三方包。

```bash
./bin/newsdesk run
./bin/newsdesk serve
```

浏览器打开 <http://localhost:8899>。也可以只用命令行：

首页的“AI 雷达”聚合研究前沿、模型产品、算力芯片、开源生态、治理安全和
AI 产业六条专线，并区分厂商一次发布、论文线索与独立媒体跟进。详细建设路径见
[AI_TECH_ROADMAP.md](AI_TECH_ROADMAP.md)。

```bash
./bin/newsdesk top -n 20
./bin/newsdesk digest -o briefing.md
./bin/newsdesk explain EVENT_ID
./bin/newsdesk audit
```

Web 顶栏的“AI 雷达”汇总近 72 小时 AI 研究、模型产品、算力芯片、开源生态、
治理安全和产业动态，并分开显示一次源、媒体跟进与独立印证。

Web 顶栏的“研究”工作台（快捷键 `e`）提供抽取式研究答案。每条 finding 都绑定
原始引文、来源、字段位置与 quote hash；无引用内容不会作为事实输出。只读 API：
`GET /api/research?q=OpenAI%20模型&limit=10`。

Schema v10 将每条 claim 引用标记为 `support`、`refute` 或 `unknown`，同时保存关系置信度和方法，并校验引文仍与
当前入库标题/摘要的字符切片和 hash 一致。真实评测样本可用
`./bin/newsdesk export-claim-eval` 导出，双人盲标与第三人仲裁完成后用
`./bin/newsdesk validate-claim-eval --strict` 验证；未完成人工流程时质量门禁会保持预警。

启用可选的 LLM 文本质量甄别：

```bash
export DASHSCOPE_API_KEY='你的密钥'
./bin/newsdesk run --llm
```

LLM 默认关闭。关闭时抓取、聚类、证据链、实体关联、检索、告警和 AI 雷达均正常运行；
开启后只对排名靠前事件增加摘要、行动意义、claims、red flags 与核查建议，并以最高
25% 权重修正规则分。LLM 可以更新 claim 文本，但不能创造引用、来源或提升独立印证数；
它不负责判断真假，也不能把官方声明或单一报道升级为独立印证。

## 甄别逻辑

界面中的分数是“证据完整度”，不是事件为真的概率。它由来源权威（40%）、独立集团交叉报道（30%）、内容质量（20%）和时间一致性（10%）组成。官方来源只能证明“该机构确实发布了这项声明”，单一官方稿不会被标为独立确认。相关性根据 `profiles/default.json` 单独计算，避免“真实但对我没用”的内容占据首页。

信源在 `sources.json` 中声明；同集团转载不会被误算成多源印证。无真实发布时间、已停更或无法解析的 feed 不进入新鲜事件窗口。

## 配置

- `NEWSDESK_PORT`：Web 端口，默认 `8899`
- `NEWSDESK_TIMEOUT`：抓取超时秒数
- `NEWSDESK_CLUSTER_WINDOW_H`：聚类时间窗
- `DASHSCOPE_API_KEY`：仅在使用 `--llm` 时需要
- `NEWSDESK_LLM_MODEL`：默认 `qwen-plus`
- `NEWSDESK_WRITE_TOKEN`：写操作管理令牌；公网监听时若未配置，服务会自动生成 `data/admin-token`（权限 `0600`）。所有写 API 必须同时通过同源校验并携带 `X-Newsdesk-Token` 或 Bearer Token；Web 界面会在首次写操作时提示输入，令牌仅保存在当前标签页的 `sessionStorage`
- `NEWSDESK_REFRESH_TOKEN`：可选的刷新接口第二令牌，主要供自动化兼容使用；设置后，`POST /api/refresh` 除上述管理令牌外还需通过 `X-Refresh-Token`（或对应 Bearer Token）校验。交互式 Web 页面通常无需设置此项
- `NEWSDESK_REFRESH_COOLDOWN`：两次刷新之间的最短秒数，默认 `30`
- `NEWSDESK_AUTO_REFRESH_SECONDS`：后台自动刷新间隔，默认 `900`；设为 `0` 关闭
- `NEWSDESK_MARKET_RETENTION_DAYS`：行情快照保留天数，默认 `90`

聚类质量门禁：`bin/newsdesk evaluate-clustering --strict`。当前 v1 基准包含
300 个定向标注 pair，并同时报告 pairwise 与 B-cubed；它是模板化回归集，尚不等同于
双人标注的真实新闻语料，质量接口会明确暴露这一边界。

产品质量门禁：`./bin/newsdesk quality --strict`。硬性正确性失败会返回非零；覆盖深度、
来源集中度、独立印证率、行情历史积累、公司主数据规模与人工金标属于 advisory，
会得到 `healthy_with_warnings`，不会被伪装成已经完成。
- `NEWSDESK_AUTO_BACKUP_SECONDS`：在线备份间隔，默认每天一次
- `NEWSDESK_BACKUP_KEEP`：自动备份保留份数，默认 `7`
- `NEWSDESK_ALERT_WEBHOOK`：可选；刷新后有新增监控匹配时 POST JSON 通知
- `NEWSDESK_ALERT_WEBHOOK_TOKEN`：可选 Webhook Bearer Token
- `NEWSDESK_LOG_FORMAT`：`json`（默认）或 `text`
- `NEWSDESK_LOG_LEVEL`：日志级别，默认 `INFO`

运维探针：`/healthz`、`/readyz`；Prometheus 指标：`/metrics`。
手工备份：`./bin/newsdesk backup`，备份完成后自动执行 SQLite 完整性校验。
恢复演练：`./bin/newsdesk recovery-drill [备份文件]`，在临时目录隔离恢复，校验完整性、迁移版本和关键表计数，不覆盖生产库。数据库变更由有序迁移管理，可用 `./bin/newsdesk schema` 查看台账。

数据库保存在 `data/newsdesk.db`。这是本地单文件 SQLite，可直接备份或删除后重建。
