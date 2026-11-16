# -*- coding: utf-8 -*-
"""滴定校验（Plan B）：游戏电荷平衡模型 vs Henderson–Hasselbalch 教学近似。

注意：这不是真实实验数据；真实 pH 计滴定按《实验/滴定实验方案.md》在 11/2 后完成。
本脚本用于检查“游戏里的缓冲曲线”是否符合教科书公式，并给出误差随偏离半中和点的变化。
"""
import csv
import math
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from game.chem import ChemModel, ChemConfig  # noqa: E402

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "Noto Sans CJK SC"]
plt.rcParams["axes.unicode_minus"] = False


def main():
    m = ChemModel()
    cfg = m.cfg
    ct = cfg.CT0
    half = ct / 2.0
    rows = []
    # 在缓冲区内加入强碱（base，mol），与 HH 近似比较
    for i in range(0, 46):
        base = 0.001 * i
        ph = m.ph(0.0, base)
        # 半中和点附近：pH ≈ pKa + log10((n_A- + OH-)/(n_HA - OH-))
        ratio = (half + base) / max(1e-9, half - base)
        ph_hh = cfg.PKA + math.log10(ratio)
        rows.append((base, ph, ph_hh, ph_hh - ph))
    rmse = math.sqrt(sum((r[3] ** 2 for r in rows)) / len(rows))
    maxerr = max(abs(r[3]) for r in rows)
    os.makedirs(os.path.join(ROOT, "logs"), exist_ok=True)
    out_csv = os.path.join(ROOT, "logs", "titration_validation.csv")
    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["base_mol", "pH_model", "pH_HH", "error"])
        for r in rows:
            w.writerow([r[0], round(r[1], 4), round(r[2], 4), round(r[3], 4)])

    fig, ax = plt.subplots(figsize=(7.8, 4.6), dpi=190)
    fig.patch.set_facecolor("#0b1015")
    ax.set_facecolor("#0b1015")
    for sp in ax.spines.values():
        sp.set_color("#26313d")
    ax.tick_params(colors="#8fa3b8")
    ax.grid(color="#26313d", lw=0.6, alpha=0.7)
    xs = [r[0] * 1000 for r in rows]
    ax.plot(xs, [r[1] for r in rows], color="#39c7d8", lw=2.4, label="游戏模型（电荷平衡）")
    ax.plot(xs, [r[2] for r in rows], color="#ffc857", lw=1.6, ls="--", label="Henderson–Hasselbalch 近似")
    ax.set_xlabel("加入强碱 n(OH-) / mmol", color="#8fa3b8")
    ax.set_ylabel("pH", color="#8fa3b8")
    ax.set_title("缓冲曲线一致性校验（Plan B，非真实实验）", color="#e7eef6", fontsize=12)
    ax.legend(facecolor="#121820", edgecolor="#26313d", labelcolor="#e7eef6", fontsize=9)
    fig.tight_layout()
    out_png = os.path.join(ROOT, "..", "方案", "图", "titration_validation.png")
    fig.savefig(out_png, facecolor=fig.get_facecolor(), bbox_inches="tight")
    print("RMSE=%.4f pH, maxerr=%.4f pH" % (rmse, maxerr))
    print("saved", out_csv, out_png)


if __name__ == "__main__":
    main()
