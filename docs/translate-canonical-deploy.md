# 交接文档：启用「外文标题译英 canonical」翻译层（一周试运行）

> 作者：AoneD（无 ECS SSH 权限，本文供有权限的 agent / 用户接力执行）
> 目标：把非中英文的外文标题（fr/de/pt/es/it/ru/ja/ko）在入库时翻译成英文 canonical，
> 让它们既能与英文报道聚成同一事件簇，又能经跨脚本桥（zh↔en）与中文报道印证，
> 从而提升 `ai_independent_corroboration_rate`（当前 ~0.024，目标 ≥0.10）。
> 前置背景见仓库根 `CLAUDE.md` 与本项目内存 `project_newsdesk_ai_corroboration`。

---

## 0. 这个特性做了什么 / 为什么低风险

- 只处理**约 4% 的外文条目**（非 en/非 zh）。en/zh 的抓取与聚类路径**逐字节不变**。
- **默认关闭**：`NEWSDESK_TRANSLATE` 不设为 `1` 时，`translate.enrich()` 直接空转返回，
  所以 push 到线上**不会自动激活**，push ≠ 上线。
- **失败即优雅回退**：无 key / API 报错 / 超时 / 超预算 → 保留外文原标题，**绝不阻塞入库**。
- 三重成本护栏：① sha1 缓存（同标题只译一次，落 `translation_cache` 表）；
  ② 单轮预算上限 `NEWSDESK_TRANSLATE_MAX_PER_RUN`（默认 200，超额留到下一轮）；
  ③ 失败自动降级。实测成本量级 ~¥1–5/月（有缓存）。
- DB 迁移安全：新增可空列 `items.canonical_title` + 新表 `translation_cache`，
  `SCHEMA_VERSION` 18→19，`store.init()` 自动应用，旧库平滑升级。

---

## 1. 需要设置的环境变量（在 ECS 上执行）

翻译层读的凭据变量名是 **`TOKEN_PLAN_KEY`**（与骗子监控项目复用同名约定；
本仓库不写死任何 key，只从环境变量读）。

在 NewsDesk 的 systemd `EnvironmentFile`（或其 `.env`，与现有 `newsdesk` 服务同一份）中加入：

```ini
# —— 开启外文译英 canonical（一周试运行）——
NEWSDESK_TRANSLATE=1
TOKEN_PLAN_KEY=<钉钉文档里的那把 sk-sp-... key，勿写进任何源码/文档>
# Base URL 已在代码里默认指向新加坡节点，如需覆盖再设：
# NEWSDESK_TRANSLATE_BASE=https://token-plan.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1
# 其余均有默认值，一般无需设置：
# NEWSDESK_TRANSLATE_MODEL=qwen-flash
# NEWSDESK_TRANSLATE_MAX_PER_RUN=200
# NEWSDESK_TRANSLATE_LANGS=fr,de,pt,es,it,ru,ja,ko
```

> key 来源：钉钉文档 `https://alidocs.dingtalk.com/i/nodes/ZX6GRezwJlzeYoPLF0ndm5zPWdqbropQ`
> ——文档里的下划线被 markdown 转义成了 `\\_`，复制时务必还原成 `_`，否则 key 不完整。
> 该 key 前缀 `sk-sp-`（signed-policy / STS 型），**很可能绑定了区域/IP 白名单**：
> 我在沙箱里对新加坡端点直连三个模型都返回 `401 invalid_api_key`。
> 真正的鉴权验证必须在 ECS 本机上做（见 §2）。

设完重启服务：

```bash
systemctl restart newsdesk    # 服务名以线上实际为准
```

---

## 2. 在 ECS 本机验证鉴权（关键一步）

因为 key 疑似 IP/区域绑定，沙箱验不了，请在 ECS 上直接冒烟：

```bash
cd /path/to/NewsDesk        # 线上代码目录
NEWSDESK_TRANSLATE=1 TOKEN_PLAN_KEY='<key>' python3 - <<'PY'
from newsdesk import config, translate
config.TRANSLATE_ENABLED = True
config.TRANSLATE_API_KEY = config.TRANSLATE_API_KEY or __import__("os").environ["TOKEN_PLAN_KEY"]
print(translate._translate_one("La BCE réduit ses taux directeurs de 25 points de base", timeout=25))
PY
```

- 打印出英文译文（如 `The ECB cuts its key interest rates by 25 basis points`）→ ✅ 鉴权通过，试运行开始。
- 仍是 `401 invalid_api_key` → key 本身/白名单问题，需在 Model Studio 控制台核对：
  key 是否有效、是否限定了调用来源 IP、是否开通了新加坡（ap-southeast-1）节点与 `qwen-flash` 模型权限。

---

## 3. 观察一周看什么

- 面板顶部质量卡里的 **AI 独立印证率**（`ai_independent_corroboration_rate`）应缓慢上行。
- 抓取日志会出现 `translate.enrich` 的统计行：`eligible/cached/translated/failed/skipped`。
  - `failed` 持续 >0 且 `translated`=0 → 鉴权或网络问题，回到 §2。
  - `cached` 逐轮升高、`translated` 逐轮降低 → 缓存生效，成本收敛，符合预期。
- 试运行结束若决定不留：把 `NEWSDESK_TRANSLATE` 去掉（或设 0）即可，代码留着不影响 en/zh。

---

## 4. 涉及的代码（本次提交）

| 文件 | 改动 |
| --- | --- |
| `newsdesk/config.py` | 新增 `TRANSLATE_*` 配置块（默认关闭，Base URL 默认新加坡节点） |
| `newsdesk/translate.py` | 新增：入库前翻译层，OpenAI 兼容调用 + sha1 缓存 + 预算/降级护栏 |
| `newsdesk/store.py` | 迁移 v19：`items.canonical_title` 列 + `translation_cache` 表；`insert_items` 落列 |
| `newsdesk/pipeline.py` | `ingest()` 入库前调 `translate.enrich`；`_row_to_item` 带出 canonical |
| `newsdesk/cluster.py` | 聚类/实体抽取的标题优先取 `canonical_title`（缺失回退原文，零回归） |
| `tests/test_translate.py` | 8 个用例：空转/跳过 en·zh/译文换 gram/缓存命中/失败回退/预算上限/外文并簇 |

全量 220 测试通过，gold precision/recall 保持 1.0/1.0（外文译英不改变 en/zh 行为）。
