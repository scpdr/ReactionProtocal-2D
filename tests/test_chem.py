# -*- coding: utf-8 -*-
"""化学引擎单元测试（18 条断言，对应《化学机制设计.md》§6.4）。"""
import math
import os
import random
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from game.chem import ChemConfig, ChemModel, ACID_EQUIV_MMOL  # noqa: E402
from game.reactions import load_equations, check_balance  # noqa: E402
from game.weapons import load_weapons, ttk_seconds, old_ttk_seconds  # noqa: E402


def mmol(x):
    return x / 1000.0


class TestChemModel(unittest.TestCase):
    def setUp(self):
        self.m = ChemModel()

    # 1
    def test_01_initial_ph(self):
        self.assertAlmostEqual(self.m.ph(0, 0), 7.20, delta=0.01)

    # 2
    def test_02_hh_approximation(self):
        pka = self.m.cfg.PKA
        for L in range(-40, 41, 5):
            p = self.m.ph(mmol(max(L, 0)), mmol(max(-L, 0)))
            n_a = 0.050 - mmol(L)
            n_ha = 0.050 + mmol(L)
            hh = pka + math.log10(n_a / n_ha)
            self.assertAlmostEqual(p, hh, delta=0.03, msg="L=%d mmol" % L)

    # 3
    def test_03_acid_equivalence(self):
        self.assertAlmostEqual(self.m.ph(0.050, 0), 4.10, delta=0.05)

    # 4
    def test_04_base_equivalence(self):
        self.assertAlmostEqual(self.m.ph(0, 0.050), 10.10, delta=0.05)

    # 5
    def test_05_lethal_points(self):
        self.assertAlmostEqual(self.m.ph(0.051, 0), 2.997, delta=0.02)
        self.assertAlmostEqual(self.m.ph(0, 0.051), 11.007, delta=0.02)

    # 6
    def test_06_symmetry(self):
        for L in range(5, 45, 5):
            s = self.m.ph(mmol(L), 0) + self.m.ph(0, mmol(L))
            self.assertAlmostEqual(s, 14.40, delta=0.05, msg="L=%d" % L)

    # 7
    def test_07_charge_balance_residual(self):
        cfg = self.m.cfg
        for acid, base, d in [(0, 0, 0), (0.03, 0, 0), (0, 0.04, 0), (0.02, 0, 0.03)]:
            p = self.m.ph(acid, base, d)
            h = 10 ** (-p)
            ct = cfg.CT0 - d
            salt = cfg.HALF - d / 2
            f = h - cfg.KW / h - ct * cfg.KA / (h + cfg.KA) + salt - (acid - base) / cfg.V
            self.assertLess(abs(f), 1e-10)

    # 8
    def test_08_monotonic_no_nan(self):
        last = None
        L = -55.0
        while L <= 55.0:
            acid = mmol(max(L, 0))
            base = mmol(max(-L, 0))
            p = self.m.ph(acid, base)
            self.assertIsNotNone(p)
            self.assertTrue(0.0 <= p <= 14.0)
            if last is not None:
                self.assertLessEqual(p, last + 1e-9)
            last = p
            L += 0.5

    # 9
    def test_09_buffer_capacity(self):
        self.assertAlmostEqual(self.m.buffer_capacity(), 0.0576, delta=0.0576 * 0.01)
        self.assertAlmostEqual(self.m.buffer_capacity(acid=mmol(40)), 0.0207,
                               delta=0.0207 * 0.01)

    # 10
    def test_10_destroyed_combination(self):
        self.assertAlmostEqual(self.m.ph(mmol(20), 0, mmol(60)), 4.30, delta=0.05)
        self.assertAlmostEqual(self.m.ph(mmol(20), 0, mmol(80)), 2.00, delta=0.05)

    # 11
    def test_11_ring_equals_old_damage(self):
        s = self.m.new_state()
        s.apply_damage(22, "acid", 0.0)
        self.assertAlmostEqual(s.ring, 78.0, delta=1.0)
        s2 = self.m.new_state()
        s2.apply_damage(55, "physical", 0.0)
        self.assertAlmostEqual(s2.ring, 45.0, delta=1.0)

    # 12
    def test_12_ttk_equivalence(self):
        data = load_weapons()
        checked = 0
        for w in data["weapons"]:
            if not w.get("old"):
                continue
            old = data["old"]["weapons"][w["old"]]
            t_old = old_ttk_seconds(old)
            t_new = ttk_seconds(w)
            if t_new is None:
                continue
            if t_old == 0.0 and t_new == 0.0:      # 双方都是一击必杀
                checked += 1
                continue
            self.assertFalse(t_old == 0.0 or t_new == 0.0,
                             msg="%s 一击必杀判定不一致" % w["id"])
            rel = abs(t_new - t_old) / t_old
            self.assertLessEqual(rel, 0.05, msg="%s TTK 偏差 %.1f%%" % (w["id"], rel * 100))
            checked += 1
        self.assertEqual(checked, 6)

    # 13
    def test_13_regen(self):
        s = self.m.new_state()
        s.apply_damage(60, "acid", 0.0)          # 30 mmol 酸负荷
        self.assertAlmostEqual(s.ring, 40.0, delta=1.0)
        gained = s.tick_regen(4.0, 3.0)          # 4s 后开始，3s 内清净 30 mmol
        self.assertAlmostEqual(s.ring, 100.0, delta=1.0)
        self.assertAlmostEqual(gained, 60.0, delta=1.5)

        s2 = self.m.new_state()
        s2.apply_damage(40, "physical", 0.0)     # 40 mmol 缓冲剥离
        self.assertAlmostEqual(s2.ring, 60.0, delta=1.0)
        s2.tick_regen(4.0, 2.0)                  # 2s 修 40 mmol D
        self.assertAlmostEqual(s2.ring, 100.0, delta=1.0)

    # 14 中和消耗缓冲对（新机制）
    def test_14_neutralization_heat_p2(self):
        m = ChemModel(ChemConfig(COUNTER_RULES=True, RING_IS_RESERVE=True))
        s = m.new_state()
        s.base = 0.040          # 40 mmol 碱负荷
        s._invalidate()
        s.apply_damage(110, "acid", 0.0)
        # 反向酸先中和碱负荷，等量消耗缓冲对（不是“回血”）
        self.assertGreater(s.d_mmol, 30.0)
        self.assertLess(s.ring, 70.0)
        self.assertLess(s.l_mmol, 0.0)

    # 20 缓冲环=缓冲对储备：酸碱只改 pH，不直接扣环；中和/氧化才扣环
    def test_20_ring_is_reserve(self):
        m = ChemModel(ChemConfig(COUNTER_RULES=True, RING_IS_RESERVE=True))

        acid_only = m.new_state()
        acid_only.apply_damage(100, "acid", 0.0)
        self.assertAlmostEqual(acid_only.ring, 100.0, delta=0.1)   # 环不掉
        self.assertLess(acid_only.ph, 7.0)                          # pH 降
        self.assertAlmostEqual(acid_only.d_mmol, 0.0, delta=0.1)

        ox = m.new_state()
        ox.apply_damage(20, "oxidizing", 0.0)
        self.assertLess(ox.ring, 100.0)                            # 氧化扣环

        neu = m.new_state()
        neu.base = 0.030
        neu._invalidate()
        neu.apply_damage(80, "acid", 0.0)
        self.assertLess(neu.ring, 100.0)                           # 中和扣环


class TestWeaponsAndReactions(unittest.TestCase):
    # 15
    def test_15_balance_checker(self):
        eq = load_equations()["by_id"]["hcl_naoh"]
        self.assertTrue(check_balance(eq, [1, 1, 1, 1])["ok"])
        self.assertFalse(check_balance(eq, [1, 1, 1, 2])["ok"])
        self.assertFalse(check_balance(eq, [2, 2, 2, 2])["ok"])
        self.assertFalse(check_balance(eq, [0, 1, 1, 1])["ok"])
        self.assertFalse(check_balance(eq, [1.5, 1.5, 1.5, 1.5])["ok"])

        eq2 = load_equations()["by_id"]["na_water"]
        self.assertTrue(check_balance(eq2, [2, 2, 2, 1])["ok"])
        eq3 = load_equations()["by_id"]["aluminothermic"]
        self.assertTrue(check_balance(eq3, [2, 1, 2, 1])["ok"])
        eq4 = load_equations()["by_id"]["h2o2_decomp"]
        self.assertTrue(check_balance(eq4, [2, 2, 1])["ok"])

    # 16
    def test_16_random_states(self):
        rng = random.Random(42)
        m = ChemModel()
        for _ in range(10000):
            acid = rng.uniform(0, 0.06)
            base = rng.uniform(0, 0.06)
            d = rng.uniform(0, 0.1)
            p = m.ph(acid, base, d)
            self.assertIsNotNone(p)
            self.assertTrue(0.0 <= p <= 14.0)
            ct = 0.1 - d
            self.assertTrue(0.0 <= ct <= 0.1)

    # 17
    def test_17_determinism(self):
        rng = random.Random(7)
        seq = [(rng.uniform(0, 0.002), rng.choice(("acid", "base", "physical", "oxidizing")))
               for _ in range(200)]

        def run():
            s = ChemModel().new_state()
            out = []
            t = 0.0
            for dmg, ch in seq:
                t += 0.1
                s.apply_damage(dmg, ch, t)
                out.append((s.snapshot()["ph"], s.snapshot()["ring"], s.snapshot()["d_mmol"]))
            return out

        self.assertEqual(run(), run())

    # 18
    def test_18_performance(self):
        m = ChemModel()
        t0 = time.perf_counter()
        for i in range(10000):
            m.ph((i % 100) * 1e-5, 0.0)
        dt = time.perf_counter() - t0
        self.assertLess(dt, 0.5, msg="10000 次求解耗时 %.3f s" % dt)

        s = m.new_state()
        states = [m.new_state() for _ in range(30)]
        t0 = time.perf_counter()
        for f in range(5):                       # 30 人 × 5 次命中 = 150 次
            for st in states:
                st.apply_damage(11, "acid", f * 0.033)
        dt2 = time.perf_counter() - t0
        self.assertLess(dt2, 0.05, msg="150 次命中耗时 %.4f s" % dt2)


    # 19 克制规则：满缓冲保护、同向补伤、反向减伤、氧化破甲效率
    def test_19_counter_rules(self):
        from game.chem import ChemConfig, ChemModel

        def make(counter, acid=0.0, destroyed=0.0):
            st = ChemModel(ChemConfig(COUNTER_RULES=counter)).new_state()
            st.acid = acid
            st.destroyed = destroyed
            st._invalidate()
            return st

        # 满缓冲：酸碱伤害被缓冲“顶住”，只剩 70%
        a = make(True).apply_damage(20, "acid", 0.0)["mmol"]
        b = make(False).apply_damage(20, "acid", 0.0)["mmol"]
        self.assertAlmostEqual(a / b, 0.70, places=2)

        # 缓冲被削 + 已有酸负荷：酸同向 +12%，碱反向 −15%
        acid_a = make(True, acid=0.010, destroyed=0.040).apply_damage(20, "acid", 0.0)["mmol"]
        acid_b = make(False, acid=0.010, destroyed=0.040).apply_damage(20, "acid", 0.0)["mmol"]
        self.assertAlmostEqual(acid_a / acid_b, 1.12, places=2)
        base_a = make(True, acid=0.010, destroyed=0.040).apply_damage(20, "base", 0.0)["mmol"]
        base_b = make(False, acid=0.010, destroyed=0.040).apply_damage(20, "base", 0.0)["mmol"]
        self.assertAlmostEqual(base_a / base_b, 0.85, places=2)

        # 氧化破甲：满缓冲 125%，缓冲 <25% 时 85%
        ox_hi = make(True).apply_damage(20, "oxidizing", 0.0)["mmol"]
        ox_hi0 = make(False).apply_damage(20, "oxidizing", 0.0)["mmol"]
        self.assertAlmostEqual(ox_hi / ox_hi0, 1.25, places=2)
        ox_lo = make(True, destroyed=0.080).apply_damage(20, "oxidizing", 0.0)["mmol"]
        ox_lo0 = make(False, destroyed=0.080).apply_damage(20, "oxidizing", 0.0)["mmol"]
        self.assertAlmostEqual(ox_lo / ox_lo0, 0.85, places=2)

if __name__ == "__main__":
    unittest.main(verbosity=2)
