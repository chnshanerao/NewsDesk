# 中文 AI 源候选清单（待评审）

> 目标：为跨语言印证提供"中文 AI 媒体"侧，让同一件全球 AI 大事有中英文独立报道可互相印证。
> 现状：57 个 AI 源里 0 个中文；且 48 个英文 AI 源当前也只产出 **6 条被印证**——印证机制本身很吝啬。

## 关键前提（已用生产数据核实）
- ECS 抓取用 **Chrome UA**，已稳定在线抓取 ithome(60)/infoq(20)/geekpark(30)/solidot(20)/tmtpost(18)/oschina(50) 等中文科技源。
  → 我这边沙箱爬虫对这些站统一 403，是我 IP/UA 被拦，**不代表 ECS 抓不到**。这一类源可达性已证实。
- 中文源目前 **0 个带 ai_ 话题**：ai_ 话题按标题相关度在"条目级"打标，与源无关。
  纯 AI 媒体(量子位/机器之心)标题几乎全是 AI → ai_ 命中率高；综合科技源(ithome)被稀释。

## 候选清单

| # | 媒体 | id | feed URL | 形态 | AI 密度 | tier | source_role | 抓取信心 | 备注 |
|---|------|-----|----------|------|---------|------|-------------|----------|------|
| 1 | 量子位 QbitAI | `qbitai` | https://www.qbitai.com/feed | WordPress RSS | 极高（纯 AI） | 2 | reporting | **高** | WP /feed 标准接口，覆盖全球 AI 大事 |
| 2 | 雷峰网 Leiphone | `leiphone` | https://www.leiphone.com/feed | WordPress RSS | 高（AI/机器人/芯片） | 2 | reporting | **高** | 自有采编，垂直 AI 频道 |
| 3 | 机器之心 Synced | `jiqizhixin` | 无官方 RSS，需 RSSHub `/jiqizhixin/1` | RSSHub 代理 | 极高（纯 AI，最深） | 2 | reporting | **中** | 内容最佳，但依赖 RSSHub（本项目尚未接入，公共实例不稳） |
| 4 | 36 氪 36Kr | `36kr` | https://36kr.com/feed | 已在库，**disabled** | 高（创投+AI） | 2 | reporting | 待验证 | 之前被禁用（疑似 feed 失败），重启需 ECS 实测 |
| 5 | 品玩 PingWest | `pingwest` | https://www.pingwest.com/feed | WordPress RSS | 中高（AI/科技） | 2 | reporting | 中 | 备选 |
| — | 新智元 AI Era | — | 以微信公众号为主，无稳定 RSS | — | 极高 | — | — | **暂不加**：只能走 RSSHub 微信路由，极脆弱 |

## 两个需你拍板的点
1. **机器之心怎么进**：内容最好但没有官方 RSS。
   - A. 走公共 rsshub.app（省事，但会限频/偶发 403，不稳）
   - B. 在日本 ECS 自建 RSSHub（稳，但要额外部署一个服务）
   - C. 先不加，用 1/2/4 顶上，机器之心以后再说
2. **是否同时上跨语言 AI 词表**：加中文源只是"有了印证的对象"，真要让 `OpenAI发布GPT-5` ↔ `OpenAI launches GPT-5` 合成一簇，还得靠 crosslingual 词表补上 AI 实体（anthropic/claude/gpt/qwen…）+ 事件（open_source/benchmark/funding/launch）。词表已实测过 gold 精度 1.0 不掉。**建议源+词表一起上**，否则源加了也桥不起来。

## 上线与验证路径
1. 先加 **1 量子位 + 2 雷峰网**（可达性最高）+ 重启 **4 36氪**（顺带验证），source_role=reporting、独立 owner/group（各算独立信源）。
2. 同时 re-apply 跨语言 AI 词表。
3. push → ECS 下一轮抓取后，用 `/api/sources` 看这几个源 `ok/last_items` 是否真到货；活的留，死的禁用/回退。
4. 重跑快照 measure 印证率是否真涨；**守不住就回退**。

> 诚实的预期：英文侧 48 源才 6 条印证，机制很吝啬。加中文源+词表大概率能把 2.1% 往上抬，但能否到 0.10 门槛不打包票——以真实 measure 为准。
