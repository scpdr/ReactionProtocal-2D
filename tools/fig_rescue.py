# -*- coding: utf-8 -*-
"""救援行为图：胜率 / 场均救援 / 队友存活率。"""
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
    rows = list(csv.DictReader(open(os.path.join(ROOT, "logs", "rescue_eval.csv"),
                                    encoding="utf-8")))
    names = [r["model"] for r in rows]
    win = [float(r["winrate"]) for r in rows]
    rescue = [float(r["rescue"]) for r in rows]
    surv = [float(r["ally_survival"]) for r in rows]
    x = np.arange(len(names))
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12.4, 4.8), dpi=190)
    fig.patch.set_facecolor(BG)
    for ax in (ax1, ax2):
        ax.set_facecolor(BG)
        for sp in ax.spines.values():
            sp.set_color("#26313d")
        ax.tick_params(colors=MUTED, labelsize=10)
        ax.grid(color="#26313d", linewidth=0.6, alpha=0.7)
        ax.set_axisbelow(True)
    ax1.bar(x, win, 0.55, color="#7dffb2")
    ax1.set_xticks(x); ax1.set_xticklabels(names, color=INK)
    ax1.set_ylim(0, 1.0); ax1.set_ylabel("2v2 胜率", color=MUTED)
    ax1.set_title("2v2 胜率（50 局）", color=INK, fontsize=12.5)
    for i, v in enumerate(win):
        ax1.text(i, v + 0.02, "%.0f%%" % (v * 100), ha="center", color=INK, fontsize=10)

    ax2.bar(x - 0.18, rescue, 0.34, color="#39c7d8", label="场均救援")
    ax2.bar(x + 0.18, surv, 0.34, color="#ffc857", label="队友存活率")
    ax2.set_xticks(x); ax2.set_xticklabels(names, color=INK)
    ax2.set_ylabel("次数 / 比例", color=MUTED)
    ax2.set_title("救援行为与队友存活", color=INK, fontsize=12.5)
    ax2.legend(facecolor="#121820", edgecolor="#26313d", labelcolor=INK, fontsize=9)
    fig.text(0.5, 0.015, "注：救援 = 中和/维C 事件；随机策略也会误打队友，数值仅作参照。",
             ha="center", color=MUTED, fontsize=9)
    fig.tight_layout(rect=[0, 0.04, 1, 1])
    out = os.path.join(ROOT, "..", "方案", "图", "ai_rescue.png")
    fig.savefig(out, facecolor=BG, bbox_inches="tight")
    print("saved", os.path.abspath(out))


if __name__ == "__main__":
    main()
