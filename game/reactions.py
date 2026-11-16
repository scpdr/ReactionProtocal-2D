# -*- coding: utf-8 -*-
"""配平方程式的服务器端校验（原子守恒 + 电荷守恒 + 最简整数比）。

P2 技能用；先做成可独立测试的纯函数。
"""
import json
import os
import re
from functools import reduce
from math import gcd

__all__ = ["parse_formula", "check_balance", "load_equations"]

_DEFAULT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "data", "reactions.json")
_ELEM = re.compile(r"([A-Z][a-z]?)(\d*)")


def parse_formula(formula):
    """把化学式解析成元素计数，例如 Fe2O3 -> {'Fe': 2, 'O': 3}。

    只支持本课题题库用到的单层化学式（无括号、无结晶水、无电荷）。
    """
    counts = {}
    pos = 0
    for m in _ELEM.finditer(formula):
        if m.start() != pos:
            raise ValueError("无法解析化学式: %r" % (formula,))
        elem, num = m.group(1), m.group(2)
        counts[elem] = counts.get(elem, 0) + (int(num) if num else 1)
        pos = m.end()
    if pos != len(formula) or not counts:
        raise ValueError("无法解析化学式: %r" % (formula,))
    return counts


def _side_counts(species, coeffs):
    total = {}
    for sp, c in zip(species, coeffs):
        for elem, n in parse_formula(sp).items():
            total[elem] = total.get(elem, 0) + n * c
    return total


def check_balance(eq, coeffs):
    """校验系数是否配平。

    返回 {"ok": bool, "reason": str}。
    """
    try:
        coeffs = list(coeffs)
        n = len(eq["left"]) + len(eq["right"])
        if len(coeffs) != n:
            return {"ok": False, "reason": "系数个数不对"}
        for c in coeffs:
            if not isinstance(c, int) or c <= 0:
                return {"ok": False, "reason": "系数必须为正整数"}
        if reduce(gcd, coeffs) != 1:
            return {"ok": False, "reason": "系数不是最简整数比"}
        left = _side_counts(eq["left"], coeffs[:len(eq["left"])])
        right = _side_counts(eq["right"], coeffs[len(eq["left"]):])
        if left != right:
            return {"ok": False, "reason": "原子不守恒"}
        return {"ok": True, "reason": "配平正确"}
    except (KeyError, ValueError, TypeError) as e:
        return {"ok": False, "reason": "解析失败: %s" % (e,)}


def load_equations(path=None):
    with open(path or _DEFAULT, "r", encoding="utf-8") as f:
        data = json.load(f)
    data["by_id"] = {e["id"]: e for e in data["equations"]}
    return data
