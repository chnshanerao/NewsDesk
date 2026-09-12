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

点开一条事件后，详情页顶部是大号“阅读原始新闻”按钮，往下依次是可追溯断言、
**主要内容**、来源构成与评分依据。“主要内容”展示原文前 4 个自然段、最多 900 字，
只为让你在点原文链接前判断值不值得读；抽不到正文时退回 feed 摘要，并在脚注里注明
读的是哪一种。全文不入库——版权与体积都不允许，原始链接始终保留。

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

### 存疑度：与可信度并列的第二个轴

可信度答的是「证据有多完整」，存疑度答的是「有多少**主动出现的**可疑信号」。两者不是
互为反面：统计局公报只有一家发布，可信度里的交叉印证拿不到分，但没人会说它可疑；一条
被十家门户转载的「某股暴涨」印证项满分，却通篇是拉抬情绪的措辞。所以「缺少印证」这种
证据不足在存疑度里只占很小权重（10 分），它是可信度的活儿。

信号取自反向证据（35）、未证实措辞（无出处 18 / 有出处 9）、标题党词（每个 6，封顶 18）、
情绪化涨跌词（10）、多类措辞同时出现（8，成套话术而非偶然用词）、emoji 与感叹号（5）、
全聚合/UGC 来源（15）、同集团回声（8）、信源健康度降级（6）、旧闻回炒（10）、
未来时间戳（12）、模型标出的疑点（每个 8，封顶 20）。分档 ≥50 高度存疑 / ≥25 有存疑点 /
≥10 轻微存疑。每一分都带一句理由，原样显示在事件详情里。

两个地方刻意放过一次源：官方声明和监管披露的「无第三方核实」不扣分 —— 当事人自己就是
信源，不需要别人证明他是否这么说了。信源评分里同一判断也生效（见下）。

**所有新闻都展示，存疑的不隐藏，只标注。** 视图划分只看可信度与相关性，不按存疑度过滤。
改了权重表后用 `./bin/newsdesk restate-doubt` 重刷全库。

### 信源治理：谁该纳入、谁该重点关注

`./bin/newsdesk source-review`（或 `/api/source-review`、界面上的「信源治理台」）为每个源
出一份档案，六项加权：供稿 20% + 首发 20% + 被印证 15% + 相关 20% + 净度 15% +
低存疑 10%。几条刻意的设计：

- **产出量只做及格线**：供稿项按 log 压缩并在 20 条封顶。本库单一信源已占 19.5% 产出、
  最大集团占 34.8%（HHI 0.072），若按产出线性给分，治理结论会变成「谁刷得多谁重要」，
  而集中度过高本身就是要治的病。
- **首发只在有别的集团也在报的事件里算数**：没人跟不等于抢到独家；且「别家」必须跨集团，
  否则同一集团的滚动版和财经版同秒发同一条稿会被算成两家在赛跑。
- **一次源不用首发和被印证来量**：这两项对它不适用，权重按比例摊到其余项。不修正时
  ecb_press / apple_newsroom / sec_tsmc 全被推进「观察期」，而这些恰是最该留下的源。
- **样本不足两个方向都不定档**：窗口内少于 10 条时压回 standard —— 少量样本的比率是
  噪音，既不能当优点也不能当缺点。
- **零产出单列 dormant**：抓不到是运维问题，不是编辑问题，档案里一并记 transport 状态。
- **低相关语种带警示**：相关度是拿中英文画像匹配的，德语、葡语源天然算不高分（实测
  0.13 / 0.15 对英语 0.29）。降档建议照出，但会标明「先排除是我方尚未接翻译导致的偏差」。

分档结论不自动落地。`sources.json` 的 `focus` 字段（`core` / `standard` / `probation`，
缺省 standard）由人工确认后写入，且**只乘在排序上**（1.15 / 1.0 / 0.85），一个字都不碰
可信度 —— 「我更关注谁」和「谁说的话更可信」是两件事，混在一起等于用偏好污染证据判断。
每次评算在 `source_scorecards` 留一行带时间戳的快照，不覆盖历史：一个源被降档半年后，
要能说清当时是看着什么数据做的决定。

信源在 `sources.json` 中声明；同集团转载不会被误算成多源印证。无真实发布时间、已停更或无法解析的 feed 不进入新鲜事件窗口。中文视角之外收录了联合早报、韩联社、共同社、ANSA、Rappler、VnExpress 等编辑独立的境外来源；国有宣传机构不收，因为它们无法承担“独立印证”的角色。单个源可以用 `"body_extract": false` 声明不抽正文（付费墙或聚合页）。

### 语种台与所有权

现有 163 个源分布在五个语种台：英 103 / 中 36 / 德 13 / 法 11 / 葡 1。语种台的意义在于
同一事件能被不同语言的独立集团各自报一次；因此 `tests/test_source_registry.py` 要求每个
语种至少有两个不同 owner —— 单一 owner 的语种不是一个台，是一个源。`lang` 必须显式声明，
缺省会退成 `zh`，外语源就会被算进中文的相关度基线里。

**owner 与 group 必须一一对应。** `group` 是判「独立印证」的单位（`clusters.n_groups >= 2`），
`owner` 用于集中度统计和证据署名。一个所有者被拆成两个 group，同一家公司的两个编辑部
报同一件事就会被算成「2 个独立集团」，直接把可信度推进「已确认」档。Ars Technica 与
WIRED 同属 Condé Nast、GitHub 属 Microsoft、MIT Tech Review 属 MIT，这三处此前各占一个
group，已合并并由测试锁住。判据取所有权而非编辑独立性 —— 编辑部各自独立不等于所有权
独立，这里宁可少给分。

**加源之前先跑探针**：`python3 tools/probe_candidates.py 候选文件.json`，三条判据是拿得到
内容、解析出条目、条目带真实发布时间；再人工看一眼「最新一条多久以前」——
europarl 的 top-stories feed 一切正常但最新一条停在 2023 年，只有这一列能看出来。
实测 175 个候选只有约 51 个在本机可达，Guardian /
AP / Le Monde / Spiegel / FAZ / heise / FT / 半岛 / DW 等高知名度源全部被出网侧拦掉
（403 或 35s 硬超时，串行控制组证明是按 host 封而非并发问题）。挂一堆零产出的源等于污染
治理台，所以宁可少收。低频官方源要把 `freshness_hours` 放宽（Riksbank 两三周一发，给到
720h）—— 低频不是不健康。

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
- `NEWSDESK_BODY_MAX_PER_RUN`：每轮抓多少篇正文预览，默认 `80`；设 `0` 完全关闭
- `NEWSDESK_BODY_MAX_PARAGRAPHS` / `NEWSDESK_BODY_MAX_CHARS`：预览段数与字数上限，默认 `4` / `900`
- `NEWSDESK_BODY_TIMEOUT`：单篇正文抓取超时秒数，默认 `12`
- `NEWSDESK_BODY_THIN_SUMMARY_CHARS`：摘要短于此字数的稿件优先抽正文，默认 `80`。摘要为空的源（如联合早报）没有退路，必须排在配额前面

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

**改了判定逻辑后必须重启常驻进程再看库里的数据。** `serve` 进程自带后台自动刷新
（`NEWSDESK_AUTO_REFRESH_SECONDS`，默认 900 秒），它持有的是启动那一刻的模块。曾出现过
一个 supervisor 托管的进程连续两天用旧分类器每 15 分钟重写一遍标签，CLI 里修好的判定在
页面上完全看不到，排查方向被带偏到分类器本身。顺序是：`supervisorctl restart newsdesk`
→ 重刷（`refresh-relations` / `restate-doubt`）→ 再核对结果。

运维探针：`/healthz`、`/readyz`；Prometheus 指标：`/metrics`。
手工备份：`./bin/newsdesk backup`，备份完成后自动执行 SQLite 完整性校验。
恢复演练：`./bin/newsdesk recovery-drill [备份文件]`，在临时目录隔离恢复，校验完整性、迁移版本和关键表计数，不覆盖生产库。数据库变更由有序迁移管理，可用 `./bin/newsdesk schema` 查看台账。

数据库保存在 `data/newsdesk.db`。这是本地单文件 SQLite，可直接备份或删除后重建。
