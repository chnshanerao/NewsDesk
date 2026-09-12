#!/usr/bin/env python3
"""验证「先翻译再聚类」能不能修好跨语言印证 —— 用人工译文隔离变量。

为什么用人工译文而不是 MT：要回答的问题是「聚类器在同语言下能不能合上」，
如果直接上机器翻译，合不上时分不清是聚类阈值的问题还是译文质量的问题。
人工译文给出的是这条路线的**上界**；MT 只会比它差，不会比它好。

样本来自实测发现的漏合并簇对（严格判据：≥2 共同实体 或 1 实体+共同数字，±12h），
只取人工确认确属同一事件的 9 对，剔除检测器自身的误报
（例如 "arm" 被 "BARMM polls" 的子串命中）。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from newsdesk import cluster, config                      # noqa: E402
from newsdesk.normalize import gram_set, simhash, tokens  # noqa: E402

# (中文原标题, 人工英译, 对应英文簇的真实标题)
CASES = [
    ("英伟达黄仁勋祝贺 OpenAI 团队发布 Astra，称“AGI 已经到来”",
     "Nvidia's Jensen Huang congratulates the OpenAI team on releasing Astra, says 'AGI has arrived'",
     "Nvidia's Jensen Huang says 'AGI has arrived' and congratulates OpenAI"),
    ("高通与亚马逊达成多代合作 共推 AI 数据中心定制芯片",
     "Qualcomm and Amazon reach multi-generational deal to jointly advance custom AI data center chips",
     "Qualcomm Signs Multi-Generational Amazon Deal for AI Data Center Silicon"),
    ("高通与亚马逊达成合作，共同打造 AI 定制芯片与 1.6T 光互联解决方案",
     "Qualcomm and Amazon partner to jointly build custom AI chips and 1.6T optical interconnect solutions",
     "Qualcomm Signs Multi-Generational Amazon Deal for AI Data Center Silicon"),
    ("英伟达与 Palantir 达成合作，将主权 AI 引入关键供应链",
     "Nvidia and Palantir announce a partnership to bring sovereign AI to critical supply chains",
     "NVIDIA and Palantir Announce Sovereign AI Stack for Supply Chains"),
    ("OpenAI 最强 AI 生图模型：ChatGPT Images 2.5 登场，延迟降低 50%、新增 Sketch 草图",
     "OpenAI's most powerful image model: ChatGPT Images 2.5 arrives with Sketch, 50% lower latency",
     "OpenAI Releases ChatGPT Images 2.5 With Sketch and Two New API Models"),
    ("ChatGPT Images 2.5 发布，OpenAI 把图像生成的重注押在了编辑上",
     "ChatGPT Images 2.5 released, OpenAI bets big on editing in image generation",
     "OpenAI Releases ChatGPT Images 2.5 With Sketch and Two New API Models"),
    ("苹果官方详解 Apple Watch Ultra 4 手表：日常续航超两天，国行 6499 元起",
     "Apple details the Apple Watch Ultra 4: over two days of everyday battery life, from 6499 yuan",
     "Apple unveils Apple Watch Ultra 4"),
    ("苹果官方详解 Apple Watch Series 12 智能手表：全新健康传感器、S11 芯片，2999 元起",
     "Apple details the Apple Watch Series 12: all-new health sensors, S11 chip, from 2999 yuan",
     "Introducing Apple Watch Series 12, with the all-new Health Sensing System"),
    ("Arm 发布 Mali G2-Ultra GPU，配 NX 神经加速器",
     "Arm releases the Mali G2-Ultra GPU with an NX neural accelerator",
     "Arm Unveils Mali G2-Ultra NX, Its First AI-Native Mobile GPU"),
]


def item(title: str, iid: str) -> dict:
    return {"id": iid, "title": title, "grams": gram_set(title),
            "simhash": simhash(tokens(title))}


def verdict(a: dict, b: dict) -> tuple[bool, float, str]:
    sim = cluster.similarity(a, b)
    conflict = cluster.conflicting(a, b, {a["id"]: set(), b["id"]: set()})
    return (sim >= 0.5 and not conflict), sim, conflict or "-"


def main() -> None:
    print(f"阈值 JACCARD={config.JACCARD_THRESHOLD} SIMHASH_MAX_DIST={config.SIMHASH_MAX_DIST}\n")
    before = after = 0
    for i, (zh, en_mt, en_real) in enumerate(CASES):
        b_ok, b_sim, _ = verdict(item(zh, f"zh{i}"), item(en_real, f"en{i}"))
        a_ok, a_sim, a_conf = verdict(item(en_mt, f"mt{i}"), item(en_real, f"en{i}"))
        before += b_ok
        after += a_ok
        print(f"[{i}] 翻译前 sim={b_sim:.2f} {'合' if b_ok else '不合'}   "
              f"翻译后 sim={a_sim:.2f} {'合' if a_ok else '不合'}"
              f"{'' if a_ok else '  阻断=' + a_conf}")
        print(f"    译文 {en_mt[:76]}")
        print(f"    英文 {en_real[:76]}")
    print(f"\n翻译前合上 {before}/{len(CASES)}   翻译后合上 {after}/{len(CASES)}")


if __name__ == "__main__":
    main()
