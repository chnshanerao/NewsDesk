"""事件聚类：把各家对同一件事的报道并成一个 event cluster。

为什么必须聚类：交叉印证是可信度的核心。不聚类就无法回答
『这条只有一家在说，还是六家独立在说』——那正是用户要的甄别能力。

算法：字符二元组倒排索引召回候选 → Jaccard/包含度/simhash 三路判定 → 并查集合并。
单链聚类（single-link）容易链式漂移，所以合并时额外要求与簇种子的相似度达标。
"""
from collections import defaultdict

from . import config
from .crosslingual import bridge_score, features
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


def _nums(item: dict) -> set[str]:
    return {g for g in item["grams"] if NUM_TOKEN_RE.match(g)}


def conflicting(a: dict, b: dict, rare: dict[str, set[str]]) -> str | None:
    """模板化新闻的杀手：标题几乎一样，只有关键实体/数字不同。

    实测两类误合并：
      『纽约股市三大股指12日上涨』vs『…13日上涨』  → 数字集合互斥
      『贵州省委主要负责同志职务调整』vs『天津市委…』 → 稀有实体词互斥
    这两类不拦住，交叉印证数就是假的，整个可信度体系跟着失真。
    """
    fa = a.get("xl_features") or features(a["title"])
    fb = b.get("xl_features") or features(b["title"])
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
    cross = bridge_score(a.get("xl_features") or features(a["title"]),
                         b.get("xl_features") or features(b["title"]))
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
    return max(j, cross)


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
        it["xl_features"] = features(it["title"])

    seed_of: dict[int, int] = {}   # root -> seed item index
    rejected = 0

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

        best_j, best_sim = -1, 0.0
        for j, shared in cand.items():
            if shared < 2:
                continue
            sim = similarity(it, items[j])
            if sim > best_sim and not conflicting(it, items[j], rare_tokens):
                best_j, best_sim = j, sim
            elif sim >= 0.5:
                rejected += 1

        if best_j >= 0 and best_sim >= 0.5:
            root = dsu.find(best_j)
            seed = seed_of.get(root, root)
            # 防链式漂移：必须也像簇种子，且与种子无实体/数字冲突
            if seed == best_j or (similarity(it, items[seed]) >= 0.42
                                  and not conflicting(it, items[seed], rare_tokens)):
                dsu.union(i, best_j)
                new_root = dsu.find(i)
                seed_of[new_root] = min(seed, i)

        for g in keys:
            index[g].append(i)
        for key in it["xl_features"].bridge_keys:
            xl_index[key].append(i)

    build.last_rejected = rejected

    groups: dict[int, list[dict]] = defaultdict(list)
    for i, it in enumerate(items):
        groups[dsu.find(i)].append(it)

    return {items[root]["id"]: members for root, members in groups.items()}
