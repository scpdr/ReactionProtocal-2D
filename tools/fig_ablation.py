# -*- coding: utf-8 -*-
"""规则消融图：同一策略在化学承重 vs 线性血条下的胜率与命中通道。"""
import csv
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "Noto Sans CJK SC"]
plt.rcParams["axes.unicode_minus"] = False

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INK = "#e8eef5"; MUTED = "#8fa3b8"; BG = "#0b1015"


def main():
    rows = list(csv.DictReader(open(os.path.join(ROOT, "logs", "ablation.csv"),
                                    encoding="utf-8")))
    models = ["PPO", "BC", "Teacher"]
    chem = [float([r for r in rows if r["model"] == m and r["damage_mode"] == "chem"][0]["winrate"]) for m in models]
    lin = [float([r for r in rows if r["model"] == m and r["damage_mode"] == "linear"][0]["winrate"]) for m in models]
    acid = [int([r for r in rows if r["model"] == m and r["damage_mode"] == "chem"][0]["acid_hits"]) for m in models]
    other = [int([r for r in rows if r["model"] == m and r["damage_mode"] == "chem"][0]["base_hits"]) +
             int([r for r in rows if r["model"] == m and r["damage_mode"] == "chem"][0]["oxidizing_hits"]) for m in models]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12.4, 4.8), dpi=190)
    fig.patch.set_facecolor(BG)
    for ax in (ax1, ax2):
        ax.set_facecolor(BG)
        for sp in ax.spines.values():
            sp.set_color("#26313d")
        ax.tick_params(colors=MUTED, labelsize=10)
        ax.grid(color="#26313d", linewidth=0.6, alpha=0.7)
        ax.set_axisbelow(True)
    x = np.arange(len(models)); w = 0.34
    ax1.bar(x - w / 2, chem, w, color="#7dffb2", label="化学承重")
    ax1.bar(x + w / 2, lin, w, color="#ffc857", label="线性血条")
    ax1.set_xticks(x); ax1.set_xticklabels(["PPO", "BC", "教师"], color=INK)
    ax1.set_ylim(0, 1.0); ax1.set_ylabel("对 FSM 胜率（30 局）", color=MUTED)
    ax1.set_title("去掉化学规则后胜率没有下降", color=INK, fontsize=12.5)
    ax1.legend(facecolor="#121820", edgecolor="#26313d", labelcolor=INK, fontsize=9)
    for i, (a, b) in enumerate(zip(chem, lin)):
        ax1.text(i - w / 2, a + 0.02, "%.0f%%" % (a * 100), ha="center", color=INK, fontsize=9)
        ax1.text(i + w / 2, b + 0.02, "%.0f%%" % (b * 100), ha="center", color=INK, fontsize=9)

    ax2.bar(x, acid, 0.55, color="#ff6b35", label="酸通道命中")
    ax2.bar(x, other, 0.55, bottom=acid, color="#b07cff", label="碱/氧化命中")
    ax2.set_xticks(x); ax2.set_xticklabels(["PPO", "BC", "教师"], color=INK)
    ax2.set_ylabel("命中次数（化学模式 30 局）", color=MUTED)
    ax2.set_title("AI 几乎只用酸枪：8 试剂槽未形成策略", color=INK, fontsize=12.5)
    ax2.legend(facecolor="#121820", edgecolor="#26313d", labelcolor=INK, fontsize=9)
    fig.text(0.5, 0.015, "注：同一策略跨模式评测；线性模式把酸碱/氧化/物理都简化为等量扣 HP。",
             ha="center", color=MUTED, fontsize=9)
    fig.tight_layout(rect=[0, 0.04, 1, 1])
    out = os.path.join(ROOT, "..", "方案", "图", "ai_ablation.png")
    fig.savefig(out, facecolor=BG, bbox_inches="tight")
    print("saved", os.path.abspath(out))


if __name__ == "__main__":
    main()
