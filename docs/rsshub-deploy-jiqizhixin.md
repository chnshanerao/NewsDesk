# 交接文档：日本 ECS 自建 RSSHub，为 NewsDesk 接入「机器之心」

> 作者：AoneD（无 ECS SSH 权限，本文供另一个有权限的 agent 接力执行）
> 目标：在日本 ECS 上自建一个 RSSHub 实例，把没有官方 RSS 的「机器之心」代理成标准 RSS，
> 供同机运行的 NewsDesk 抓取，为跨语言 AI 印证补上中文侧最深的一路采编。
> 前置背景见仓库根 `CLAUDE.md` 与 `docs/zh-ai-sources-proposal.md`。

---

## 0. 为什么是「同机自建」

- NewsDesk 本身就永久托管在**日本 ECS `8.209.202.129`**（`newsdesk.cloudivine.top`，每小时整点
  从 GitHub `chnshanerao/NewsDesk` main 自动拉取并重启）。
- RSSHub 若跑在**同一台 ECS**，NewsDesk 就能用 `http://127.0.0.1:1200/...` 直连，
  **不必把 RSSHub 暴露到公网**（RSSHub 默认无鉴权，公网暴露有滥用风险）。
- 用户已明确选「方案 B：日本 ECS 自建 RSSHub」，而非公共 `rsshub.app`（后者会限频/偶发 403，不稳）。

---

## 1. 部署 RSSHub（在日本 ECS 上执行）

### 1.1 确认 Docker 可用

```bash
docker version || echo "需要先装 Docker"
```

若没有 Docker，用官方脚本装：`curl -fsSL https://get.docker.com | sh`（需该 ECS 能出网）。

### 1.2 启动 RSSHub 容器

**关键：端口只绑到 `127.0.0.1`，不暴露公网。**

```bash
docker run -d \
  --name rsshub \
  --restart always \
  -p 127.0.0.1:1200:1200 \
  -e NODE_ENV=production \
  -e CACHE_TYPE=memory \
  diygod/rsshub
```

说明：
- `--restart always`：ECS 重启后容器自动拉起。
- `-p 127.0.0.1:1200:1200`：**只监听本机回环**，公网/其他机器访问不到，NewsDesk 同机可达。
- `CACHE_TYPE=memory`：省一个 Redis 依赖；单实例低频抓取够用。如需更强缓存再上 Redis。

启动后等约 10~20 秒让服务就绪：

```bash
sleep 15
docker logs --tail 30 rsshub
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:1200/   # 期望 200
```

---

## 2. 确认「机器之心」路由（这一步必须实测，别照抄）

⚠️ **我（AoneD）无法从沙箱访问 RSSHub 文档/源码（统一 403），所以下面的路由是「最可能」而非「已确认」。
以你在 ECS 上实测到的、能真正返回条目的路由为准。**

优先尝试（按可能性排序）：

```bash
# 候选 1（最可能）：机器之心 namespace 根路由
curl -s http://127.0.0.1:1200/jiqizhixin | head -60

# 候选 2：带分类（资讯）
curl -s http://127.0.0.1:1200/jiqizhixin/1 | head -60
```

判断标准：
- **成功** = 返回 `<rss>...<channel>` 且 `<item>` 里有近几天的真实标题（不是空 channel、不是报错页）。
- **404 / 报错** = 路由不对或该 route 需要额外配置。此时去 <https://docs.rsshub.app>
  搜「机器之心」拿当前正确路径（RSSHub 路由偶有变更），或看容器日志 `docker logs rsshub` 找具体报错。

把**最终能出条目的完整路径**记下来（下面第 3 步要用），例如实测确认为 `/jiqizhixin`，
则 NewsDesk 侧 URL 就是 `http://127.0.0.1:1200/jiqizhixin`。

> 若机器之心 route 因反爬短期抓不到，别硬上：先跳过，把量子位/雷峰网（已在库、走官方 /feed）跑稳，
> 机器之心等 RSSHub 侧稳定了再接。**不要为了凑一路源而接一个时灵时不灵的 feed。**

---

## 3. 把「机器之心」接入 NewsDesk

在仓库根 `sources.json` 里，**新增**下面这条（放在 `qbitai`/`leiphone` 附近，中文 AI 源那一组）。
把 `url` 换成第 2 步**实测确认**的路径：

```json
{
  "id": "jiqizhixin",
  "name": "机器之心",
  "tier": 2,
  "group": "jiqizhixin",
  "kind": "rss",
  "url": "http://127.0.0.1:1200/jiqizhixin",
  "lang": "zh",
  "topics": ["ai_models", "ai_research", "ai_industry"],
  "enabled": true,
  "source_role": "reporting",
  "note": "中文 AI 最深垂媒，无官方 RSS，经本机自建 RSSHub(127.0.0.1:1200) 代理。source_role=reporting 计入跨语言独立印证"
}
```

要点：
- `source_role: "reporting"` —— 计入独立印证集团（这正是加它的目的）。
- `group: "jiqizhixin"` 独立成组 —— 与量子位/雷峰网算不同信源，才能相互印证。
- `tier: 2` —— 垂直原创媒体，权威度按二档。
- `enabled: true` —— **仅在第 2 步实测通过后**才设 true；没跑通就先 `false` 或干脆先不加。

改完本地自检（`sources.json` 必须是合法 JSON）：

```bash
cd /path/to/NewsDesk        # 仓库根
python3 -c "import json; d=json.load(open('sources.json')); print('sources:', len(d)); \
  assert any(s['id']=='jiqizhixin' for s in d), '未找到 jiqizhixin'; print('OK')"
python3 -m pytest tests/ -q   # 期望仍全绿（当前基线 212 passed）
```

---

## 4. 上线（两条路，任选其一）

NewsDesk 的部署是 **GitHub push → ECS 每小时整点自动拉取重启**。所以「改 sources.json」要生效，
必须让改动进到 GitHub `chnshanerao/NewsDesk` 的 main。

- **路线 A（你有正常 git push 权限）**：直接 `git add sources.json && git commit && git push origin main`。
- **路线 B（沙箱环境，DNS 解不出 github.com）**：用仓库内 `.git_api_push.py`，它走 `api.github.com`
  （Git Data API）推送，token 从 `~/.gh_push_token` 读。执行 `python3 .git_api_push.py`。
  ⚠️ 该脚本会按 `origin/main..main` 重放 commit；若本地 tracking ref 陈旧会重放很多条且易超时，
  更稳的做法是「只把变更文件做一次树对账 commit」——参考本仓库 AoneD 推送 `ade2687d` 时用的最小化推法
  （GET 远端 ref+tree → 上传变更 blob → base_tree 建树 → 建 commit → PATCH ref）。

推送后，**等下一个整点**（部署延迟 ≤ 1 小时），再验证。

---

## 5. 验证 NewsDesk 真的抓到了

部署 + 一轮抓取后（约 1 小时内），查线上 `/api/sources`：

```bash
curl -s https://newsdesk.cloudivine.top/api/sources | \
  python3 -c "import sys,json; \
    d=json.load(sys.stdin); \
    s=[x for x in (d.get('sources') or d) if x.get('id')=='jiqizhixin']; \
    print(json.dumps(s, ensure_ascii=False, indent=2))"
```

判断：
- `ok: true` 且 `last_items > 0` → **成功**，机器之心已进池。
- `ok: false` / `last_items: 0` → RSSHub 侧或路由有问题。回到第 2 步排查；排查期间把该源 `enabled:false`
  推一次回退，别让一个死源拖累抓取轮次。

---

## 6. 运维 & 回退

```bash
# 看 RSSHub 是否健康
docker ps --filter name=rsshub
docker logs --tail 50 rsshub
# 重启
docker restart rsshub
# 升级到新版镜像
docker pull diygod/rsshub && docker rm -f rsshub && <重跑第 1.2 的 docker run>
# 彻底回退（连带停用 NewsDesk 侧源）：把 sources.json 里 jiqizhixin 的 enabled 改 false 并推送
```

---

## 7. 交接清单（给接力 agent）

- [ ] 1. ECS 上起 RSSHub 容器（第 1 步），`curl :1200/` 返回 200
- [ ] 2. 实测确认机器之心路由能出真实条目（第 2 步），记下确切路径
- [ ] 3. 把确认后的 URL 填进 `sources.json` 的 jiqizhixin 条目，`enabled:true`（第 3 步）
- [ ] 4. 本地 JSON 自检 + `pytest` 全绿（第 3 步）
- [ ] 5. push 到 GitHub main（第 4 步）
- [ ] 6. 等整点部署后查 `/api/sources` 确认 `ok:true / last_items>0`（第 5 步）
- [ ] 7. 若失败 → `enabled:false` 回退并把失败现象反馈回来

> 完成后请把「最终确认的路由路径」+「/api/sources 里 jiqizhixin 的状态」回传，
> AoneD 会据此复核跨语言 AI 印证率是否因此上升（`/api/quality` 的 `ai_independent_corroboration_rate`）。
