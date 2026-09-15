"""事件聚类：把各家对同一件事的报道并成一个 event cluster。

为什么必须聚类：交叉印证是可信度的核心。不聚类就无法回答
『这条只有一家在说，还是六家独立在说』——那正是用户要的甄别能力。

算法：字符二元组倒排索引召回候选 → Jaccard/包含度/simhash 三路判定 → 并查集合并。
单链聚类（single-link）容易链式漂移，所以合并时额外要求与簇种子的相似度达标。
"""
from collections import defaultdict

from . import config
from .crosslingual import bridge_score, different_periods, features
from .normalize import hamming, jaccard, overlap


class DSU:
    def __init__(self, n: int):
        self.p = list(range(n))

    def find(self, x: int) -> int:
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[max(ra, rb)] = min(ra, rb)   # 小索引（更早）当根 → 簇 id 稳定


NUM_TOKEN_RE = __import__("re").compile(r"^\d+(?:\.\d+)?%?$")


def _clust_title(item: dict) -> str:
    """用于聚类/实体抽取的标题：外文条目优先用英文 canonical，其余用原文。

    grams/simhash 在入库时已按同一 canonical 算过（见 translate.enrich），
    这里让 features 也吃 canonical，两路信号才对齐。canonical 缺失 → 原文，
    等价于翻译层关闭时的原行为，零回归。
    """
    return item.get("canonical_title") or item["title"]


def _nums(item: dict) -> set[str]:
    return {g for g in item["grams"] if NUM_TOKEN_RE.match(g)}


def conflicting(a: dict, b: dict, rare: dict[str, set[str]]) -> str | None:
    """模板化新闻的杀手：标题几乎一样，只有关键实体/数字不同。

    实测两类误合并：
      『纽约股市三大股指12日上涨』vs『…13日上涨』  → 数字集合互斥
      『贵州省委主要负责同志职务调整』vs『天津市委…』 → 稀有实体词互斥
    这两类不拦住，交叉印证数就是假的，整个可信度体系跟着失真。
    """
    fa = a.get("xl_features") or features(_clust_title(a))
    fb = b.get("xl_features") or features(_clust_title(b))
    na, nb = _nums(a), _nums(b)
    cross_script = ((fa.has_cjk and fb.has_latin) or
                    (fb.has_cjk and fa.has_latin))
    # Raw headline tokens represent 5亿 as ``5`` and 500 million as ``500``.
    # Use normalized magnitudes across scripts, while retaining the stricter raw
    # comparison for same-language dates and counts.
    if cross_script:
        if fa.numbers and fb.numbers and not (fa.numbers & fb.numbers):
            return "数字/日期互斥"
    elif na and nb and not (na & nb):
        return "数字/日期互斥"
    if fa.entities and fb.entities and not (fa.entities & fb.entities):
        return "命名实体互斥"
    # A shared entity is not enough: materially different known actions must
    # never become one event merely because the surrounding template is alike.
    if fa.events and fb.events and not (fa.events & fb.events):
        return "事件类型互斥"
    if fa.objects and fb.objects and not (fa.objects & fb.objects):
        return "事件客体互斥"
    cross = bridge_score(fa, fb)
    ra, rb = rare.get(a["id"], set()), rare.get(b["id"], set())
    # Different scripts naturally have disjoint character grams. Once the strict
    # structured bridge succeeds, those grams are not evidence of entity conflict.
    if not cross and ra and rb and not (ra & rb):
        return "关键实体词互斥"
    return None


def similarity(a: dict, b: dict) -> float:
    ga, gb = a["grams"], b["grams"]
    fa = a.get("xl_features") or features(_clust_title(a))
    fb = b.get("xl_features") or features(_clust_title(b))
    cross = bridge_score(fa, fb)
    if not ga or not gb:
        return cross
    j = jaccard(ga, gb)
    ov = overlap(ga, gb)
    h = hamming(a["simhash"], b["simhash"]) if a["simhash"] and b["simhash"] else 64

    if j >= config.JACCARD_THRESHOLD:
        return max(j, 0.6)
    if ov >= 0.68 and min(len(ga), len(gb)) >= 8:
        return max(ov * 0.9, 0.55)
    if h <= config.SIMHASH_MAX_DIST and j >= 0.25:
        return max(0.5, 1 - h / 12)
    # 改写标题的同一件事就落在这一档：同一家、同一类动作、词面确有重合，
    # 但重合度够不到上面的门槛。实测（72h 生产语料，1963 对同语言同实体配对）
    # 同题稿集中在 j 0.26–0.45，异题稿在 0.15 以下，中间有可用空档：
    #   TechCrunch「Altman says it would be 'ill-advised' to go public in 2026」
    #   Tech Xplore「Altman tells Fortune OpenAI will not go public in 2026」
    #   j=0.353 —— 两个独立集团报同一件事，却各自成簇、各自 n_groups=1。
    # 跨脚本不走这条：zh↔en 词面本来就不重合，j 没有鉴别力，那由 bridge_score
    # 和 canonical 译文负责；这里只补同脚本（en↔de、en↔en、zh↔zh）的召回。
    # 结构化否决与跨脚本桥接保持同一套：不同期次（8 月更新 vs 9 月更新）、
    # 互斥取值，都不能因为「同一家 + 同一类动作 + 词面有点像」就变成一件事。
    if (j >= config.CLUSTER_SAME_SCRIPT_FLOOR
            and fa.has_cjk == fb.has_cjk
            and (fa.entities & fb.entities) and (fa.events_broad & fb.events_broad)
            and not different_periods(fa.periods, fb.periods)
            and not (fa.numbers and fb.numbers and not (fa.numbers & fb.numbers))):
        return 0.6
    return max(j, cross)


def _drift_ok(it: dict, items: list[dict], best_j: int, root: int,
              seed_of: dict, rare: dict) -> bool:
    """合并前的防链式漂移复检：新成员必须也像簇种子（最早那篇），且与种子无冲突。

    single-link 的老毛病是 A~B、B~C、C~D 一路连下去，最后 A 和 D 毫无关系，
    这道复检就是拦它的。曾试过两种更宽的口径（种子不像时改看「该簇已有 >=2 篇
    成员各自与本条够格」，或「与全簇 gram 并集的包含度」），72h 生产语料实测：
    多合并 20–60 个簇，AI 印证率一动不动（0.0259 → 0.0260）。
    放宽没有收益就只是风险，所以两种都没留。
    """
    seed = seed_of.get(root, root)
    return seed == best_j or (similarity(it, items[seed]) >= 0.42
                              and not conflicting(it, items[seed], rare))


def build(items: list[dict]) -> dict[str, list[dict]]:
    """items 需按时间升序。返回 {cluster_id: [item, ...]}。"""
    n = len(items)
    if n == 0:
        return {}
    dsu = DSU(n)

    # 倒排索引：过滤掉过于常见的 gram（否则候选集爆炸且无区分度）
    df: dict[str, int] = defaultdict(int)
    for it in items:
        for g in it["grams"]:
            df[g] += 1
    df_cap = max(8, int(n * 0.12))
    entity_cap = max(2, int(n * 0.004))     # df 极低 = 实体词/专有名词
    index: dict[str, list[int]] = defaultdict(list)
    rare_tokens: dict[str, set[str]] = {
        it["id"]: {g for g in it["grams"] if df[g] <= entity_cap} for it in items
    }
    xl_index: dict[str, list[int]] = defaultdict(list)
    for it in items:
        it["xl_features"] = features(_clust_title(it))

    seed_of: dict[int, int] = {}   # root -> seed item index
    rejected = 0                   # 相似度够但被 conflicting 否决
    drifted = 0                    # 相似度够但被防漂移复检否决

    for i, it in enumerate(items):
        cand: dict[int, int] = defaultdict(int)
        keys = [g for g in it["grams"] if df[g] <= df_cap]
        for g in keys:
            for j in index[g]:
                cand[j] += 1
        # Cross-language titles share no character grams. Specific entity:event
        # keys provide candidates; generic country words never create such keys.
        for key in it["xl_features"].bridge_keys:
            for j in xl_index[key]:
                cand[j] += 2

        # 汇总稿（一条标题装 N 件事）只允许跟同一媒体集团自己的稿子合并。
        # 跨集团合并会把不相干的事粘成一簇，还会伪造出「两家独立报道」：
        # 实测有个 n_items=8 / n_groups=2 的簇，印证完全是 IT早报 粘出来的。
        digest_i = it["xl_features"].digest
        ranked: list[tuple[float, int]] = []
        for j, shared in cand.items():
            if shared < 2:
                continue
            other = items[j]
            if digest_i or other["xl_features"].digest:
                if (it.get("grp") or "") != (other.get("grp") or ""):
                    continue
            sim = similarity(it, other)
            if sim < 0.5:
                continue
            if conflicting(it, other, rare_tokens):
                rejected += 1
                continue
            ranked.append((sim, j))
        ranked.sort(reverse=True)

        # 按相似度从高到低逐个试，直到有一个通过防漂移复检。
        # 旧写法只取最优候选：它一旦被复检挡下，这条稿子就直接放弃归属，
        # 哪怕第二优的候选完全合规。
        for best_sim, best_j in ranked[:6]:
            root = dsu.find(best_j)
            if _drift_ok(it, items, best_j, root, seed_of, rare_tokens):
                seed = seed_of.get(root, root)
                dsu.union(i, best_j)
                seed_of[dsu.find(i)] = min(seed, i)
                break
            drifted += 1

        for g in keys:
            index[g].append(i)
        for key in it["xl_features"].bridge_keys:
            xl_index[key].append(i)

    build.last_rejected = rejected
    build.last_drifted = drifted

    groups: dict[int, list[dict]] = defaultdict(list)
    for i, it in enumerate(items):
        groups[dsu.find(i)].append(it)

    return {items[root]["id"]: members for root, members in groups.items()}
