# -*- coding: utf-8 -*-
"""生成 AI 评测图：winrate/K-D + 不同难度分组柱状图 + PPO 更新曲线。"""
import csv
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "Noto Sans CJK SC"]
plt.rcParams["axes.unicode_minus"] = False

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INK = "#e8eef5"
MUTED = "#8fa3b8"
BG = "#0b1015"
GRID = "rgba(255,255,255,0.10)"


def read_eval():
    rows = []
    with open(os.path.join(ROOT, "logs", "eval_ai.csv"), encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    return rows


def main():
    rows = read_eval()
    models = ["Random", "BC", "DAgger", "PPO"]
    colors = {"Random": "#6b7c8f", "BC": "#ffc857", "DAgger": "#39c7d8", "PPO": "#7dffb2"}
    fig, axes = plt.subplots(1, 2, figsize=(12.6, 5.0), dpi=190)
    fig.patch.set_facecolor(BG)
    for ax in axes:
        ax.set_facecolor(BG)
        for sp in ax.spines.values():
            sp.set_color("#26313d")
        ax.tick_params(colors=MUTED, labelsize=10)
        ax.grid(color="#26313d", linewidth=0.6, alpha=0.7)
        ax.set_axisbelow(True)

    # 左：normal 难度 winrate / K/D
    ax = axes[0]
    norm = {r["model"]: r for r in rows if r["difficulty"] == "normal"}
    xs = np.arange(len(models))
    wr = [float(norm[m]["winrate"]) for m in models]
    bars = ax.bar(xs, wr, color=[colors[m] for m in models], width=0.62, zorder=3)
    ax.set_xticks(xs)
    ax.set_xticklabels(["随机", "BC", "DAgger", "PPO"], color=INK, fontsize=11)
    ax.set_ylim(0, 1.0)
    ax.set_ylabel("对 FSM(normal) 胜率", color=MUTED, fontsize=11)
    ax.set_title("本地训练：不同阶段对 FSM 胜率（50 局）", color=INK, fontsize=12.5, pad=10)
    for b, v in zip(bars, wr):
        ax.text(b.get_x() + b.get_width() / 2, v + 0.025, "%.0f%%" % (v * 100),
                ha="center", color=INK, fontsize=11, fontweight="bold")

    # 右：三档难度分组
    ax = axes[1]
    diffs = ["easy", "normal", "hard"]
    idx = {r["difficulty"]: r for r in rows if r["model"] == "PPO"}
    width = 0.24
    for i, name in enumerate(["BC", "DAgger", "PPO"]):
        vals = []
        for d in diffs:
            r = [x for x in rows if x["model"] == name and x["difficulty"] == d][0]
            vals.append(float(r["winrate"]))
        ax.bar(np.arange(3) + (i - 1) * width, vals, width, color=colors[name],
               alpha=0.9, label=name, zorder=3)
    ax.set_xticks(np.arange(3))
    ax.set_xticklabels(["简单", "普通", "困难"], color=INK, fontsize=11)
    ax.set_ylim(0, 1.0)
    ax.set_ylabel("胜率", color=MUTED, fontsize=11)
    ax.set_title("三档机器人难度（各 50 局）", color=INK, fontsize=12.5, pad=10)
    ax.legend(facecolor="#121820", edgecolor="#26313d", labelcolor=INK,
              fontsize=10, loc="upper right")

    fig.text(0.5, 0.015,
             "注：BC=行为克隆；DAgger=策略访问状态+教师标注；PPO=短程自博弈微调；Random=随机动作基线。",
             ha="center", color=MUTED, fontsize=9)
    fig.tight_layout(rect=[0, 0.045, 1, 1])
    out = os.path.join(ROOT, "..", "方案", "图", "ai_eval.png")
    fig.savefig(out, facecolor=BG, bbox_inches="tight")
    print("saved", os.path.abspath(out))


if __name__ == "__main__":
    main()
