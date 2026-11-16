# -*- coding: utf-8 -*-
"""化学引擎：pH-缓冲生命系统（纯 Python 标准库，零第三方依赖）。

模型依据《方案/调研/化学机制设计.md》§1：

    玩家 = 1.000 L 虚拟缓冲体系（磷酸盐 H2PO4-/HPO4^2-，pKa=7.20，C_T0=0.100 mol）
    pH   = 电荷守恒 + 一元弱酸解离，用几何二分法精确求解
    缓冲环 E = 2*min(n_A, n_HA)/C_T0 * 100%
    阵亡 = pH <= 3.00 或 pH >= 11.00 或 C_T <= 0

伤害换算（保证旧武器手感）：
    酸/碱    1 点旧伤害 = 0.5 mmol 当量
    物理/氧化 1 点旧伤害 = 1.0 mmol 缓冲对

本模块只做数值计算，不依赖网络/游戏状态，便于单元测试与在服务器/训练环境复用。
性能：状态级缓存，未变化时不重复求解；单次命中约 2 次求解。
"""
import math

__all__ = [
    "ChemConfig", "ChemModel", "ChemState", "CHANNELS",
    "ACID_EQUIV_MMOL", "BUFFER_EQUIV_MMOL",
]

#: 伤害通道
CHANNELS = ("acid", "base", "physical", "oxidizing")

#: 击穿所需当量（mmol）——酸/碱需先中和 50 mmol 共轭碱，再过量 1 mmol 到 pH 3.00
ACID_EQUIV_MMOL = 51.0
#: 物理/氧化需剥离 100 mmol 缓冲对
BUFFER_EQUIV_MMOL = 100.0


class ChemConfig:
    """化学参数（可热调，供“参数面板 B”与消融实验使用）。"""

    __slots__ = (
        "V", "CT0", "HALF", "PKA", "KW",
        "ACID_MMOL_PER_DMG", "DESTROY_MMOL_PER_DMG",
        "PH_LOW", "PH_HIGH",
        "REGEN_DELAY", "L_REGEN_MMOL_S", "D_REGEN_MMOL_S", "E_REGEN_CAP_PCT_S",
        "NEUTRALIZE_HEAT_K", "ITERS", "LINEAR_HP", "COUNTER_RULES",
        "RING_IS_RESERVE",
    )

    def __init__(
        self,
        V=1.000,              # L
        CT0=0.100,            # mol（100 mmol 缓冲对）
        HALF=0.050,           # mol（HA / A- 各一半）
        PKA=7.20,
        KW=1e-14,
        ACID_MMOL_PER_DMG=0.5,
        DESTROY_MMOL_PER_DMG=1.0,
        PH_LOW=3.00,
        PH_HIGH=11.00,
        REGEN_DELAY=4.0,
        L_REGEN_MMOL_S=10.0,
        D_REGEN_MMOL_S=20.0,
        E_REGEN_CAP_PCT_S=20.0,
        NEUTRALIZE_HEAT_K=0.0,  # 0 = 关闭（P0）；0.8 = P2 中和热
        ITERS=40,
        LINEAR_HP=False,     # 消融实验：True=线性血条（关闭化学承重）
        COUNTER_RULES=False,  # 酸碱克制/氧化破甲（线上开启；旧 TTK 单测默认关闭）
        RING_IS_RESERVE=False,  # True：缓冲环=缓冲对储备（酸碱只改 pH，中和/氧化才扣环）
    ):
        self.V = float(V)
        self.CT0 = float(CT0)
        self.HALF = float(HALF)
        self.PKA = float(PKA)
        self.KW = float(KW)
        self.ACID_MMOL_PER_DMG = float(ACID_MMOL_PER_DMG)
        self.DESTROY_MMOL_PER_DMG = float(DESTROY_MMOL_PER_DMG)
        self.PH_LOW = float(PH_LOW)
        self.PH_HIGH = float(PH_HIGH)
        self.REGEN_DELAY = float(REGEN_DELAY)
        self.L_REGEN_MMOL_S = float(L_REGEN_MMOL_S)
        self.D_REGEN_MMOL_S = float(D_REGEN_MMOL_S)
        self.E_REGEN_CAP_PCT_S = float(E_REGEN_CAP_PCT_S)
        self.NEUTRALIZE_HEAT_K = float(NEUTRALIZE_HEAT_K)
        self.ITERS = int(ITERS)
        self.LINEAR_HP = bool(LINEAR_HP)
        self.COUNTER_RULES = bool(COUNTER_RULES)
        self.RING_IS_RESERVE = bool(RING_IS_RESERVE)

    @property
    def KA(self):
        return 10.0 ** (-self.PKA)

    def copy(self):
        return ChemConfig(**self.as_dict())

    def as_dict(self):
        return {k: getattr(self, k) for k in self.__slots__}

    def update(self, **kwargs):
        """就地热调；只接受已知字段。返回 self 便于链式调用。"""
        for k, v in kwargs.items():
            if k not in self.__slots__:
                raise KeyError(k)
            setattr(self, k, type(getattr(self, k))(v))
        return self


class ChemModel:
    """pH 求解与状态工厂。"""

    def __init__(self, cfg=None):
        self.cfg = cfg or ChemConfig()

    # ---- 核心：电荷守恒 + 几何二分 ----
    def ph(self, acid, base, destroyed=0.0):
        """返回 pH；缓冲对被完全剥离（C_T<=0）时返回 None。"""
        return self.ph_ring(acid, base, destroyed)[0]

    def _n_a(self, h, ct):
        ka = self.cfg.KA
        return ct * ka / (h + ka)

    def ph_ring(self, acid, base, destroyed=0.0):
        """一次求解同时返回 (pH, 缓冲环 E%)。"""
        cfg = self.cfg
        ct = cfg.CT0 - destroyed
        if ct <= 0.0:
            return None, 0.0
        salt = max(0.0, cfg.HALF - destroyed / 2.0)
        L = acid - base
        ka = cfg.KA
        lo, hi = 1e-14, 1.0
        for _ in range(cfg.ITERS):
            h = math.sqrt(lo * hi)
            f = h - cfg.KW / h - ct * ka / (h + ka) + salt - L / cfg.V
            if f > 0.0:
                hi = h
            else:
                lo = h
        h = math.sqrt(lo * hi)
        p = -math.log10(h)
        n_a = self._n_a(h, ct)
        n_ha = ct - n_a
        ring = 200.0 * min(n_a, n_ha) / cfg.CT0
        return p, ring

    def ring(self, acid, base, destroyed=0.0):
        return self.ph_ring(acid, base, destroyed)[1]

    def buffer_capacity(self, acid=0.0, base=0.0, destroyed=0.0):
        """缓冲容量 beta = 2.303 * C_T * x(1-x)，单位 mol/(L*pH)。"""
        cfg = self.cfg
        ct = cfg.CT0 - destroyed
        if ct <= 0.0:
            return 0.0
        h = 10.0 ** (-self.ph(acid, base, destroyed))
        x = self._n_a(h, ct) / ct
        return 2.303 * (ct / cfg.V) * x * (1.0 - x)

    def lethal(self, acid, base, destroyed=0.0, ph=None):
        """返回 (是否阵亡, 原因)。原因: acidosis / alkalosis / buffer_destroyed。"""
        cfg = self.cfg
        if cfg.CT0 - destroyed <= 0.0:
            return True, "buffer_destroyed"
        if ph is None:
            ph = self.ph(acid, base, destroyed)
        if ph is None:
            return True, "buffer_destroyed"
        if ph <= cfg.PH_LOW:
            return True, "acidosis"
        if ph >= cfg.PH_HIGH:
            return True, "alkalosis"
        return False, None

    def new_state(self, now=0.0):
        return ChemState(self, now)


class ChemState:
    """一个玩家的化学状态（可变）。"""

    __slots__ = ("model", "acid", "base", "destroyed", "last_damage_t", "death_cause",
                 "linear_hp", "_ckey", "_cph", "_cring")

    def __init__(self, model, now=0.0):
        self.model = model
        self.reset(now)

    # ---- 生命周期 ----
    def reset(self, now=0.0):
        self.acid = 0.0       # mol
        self.base = 0.0       # mol
        self.destroyed = 0.0  # mol
        self.linear_hp = 100.0  # 消融模式：线性血条
        self.last_damage_t = now
        self.death_cause = None
        self._invalidate()

    # ---- 只读属性（带缓存）----
    @property
    def cfg(self):
        return self.model.cfg

    def _calc(self):
        if self.cfg.LINEAR_HP:
            return 7.0, self.linear_hp
        key = (self.acid, self.base, self.destroyed)
        if key != self._ckey:
            p, r = self.model.ph_ring(self.acid, self.base, self.destroyed)
            if self.cfg.RING_IS_RESERVE:
                # 缓冲环 = 缓冲对还剩多少（不是酸碱比例），酸碱只改 pH
                r = 100.0 * max(0.0, self.cfg.CT0 - self.destroyed) / self.cfg.CT0
            self._ckey = key
            self._cph = p
            self._cring = r
        return self._cph, self._cring

    def _invalidate(self):
        self._ckey = None
        self._cph = None
        self._cring = 0.0

    @property
    def net_acid(self):
        return self.acid - self.base

    @property
    def l_mmol(self):
        return self.net_acid * 1000.0

    @property
    def d_mmol(self):
        return self.destroyed * 1000.0

    @property
    def ct_mmol(self):
        return (self.cfg.CT0 - self.destroyed) * 1000.0

    @property
    def ph(self):
        return self._calc()[0]

    @property
    def ring(self):
        return self._calc()[1]

    @property
    def alive(self):
        return self.death_cause is None

    # ---- 伤害 ----
    def apply_damage(self, old_damage, channel, now):
        """按旧版“伤害点”施加一次命中，返回事件报告。

        channel: acid / base / physical / oxidizing
        """
        if channel not in CHANNELS:
            raise ValueError("unknown channel: %r" % (channel,))
        cfg = self.cfg
        before = self.snapshot()
        dmg = float(old_damage)

        if cfg.LINEAR_HP:      # 消融：线性血条，所有通道等价扣 HP
            self.linear_hp = max(0.0, self.linear_hp - dmg)
            self.last_damage_t = now
            if self.linear_hp <= 0.0:
                self.death_cause = "hp"
            self._invalidate()
            self._refresh()
            return {
                "channel": channel, "mmol": round(dmg, 2),
                "before": before, "after": self.snapshot(),
                "died": self.death_cause is not None, "cause": self.death_cause,
            }

        if channel in ("acid", "base"):
            mmol = dmg * cfg.ACID_MMOL_PER_DMG
            delta = mmol / 1000.0
            factor = 1.0
            # 克制规则：同向补伤 +12%，反向（先中和对方负荷）−15%；
            # 满缓冲（>60%）时酸碱伤害再降 30%：逼出“氧化开甲→酸碱补刀”
            if cfg.COUNTER_RULES:
                L = self.net_acid
                if channel == "acid":
                    factor = 1.12 if L > 1e-6 else (0.85 if L < -1e-6 else 1.0)
                else:
                    factor = 1.12 if L < -1e-6 else (0.85 if L > 1e-6 else 1.0)
                if self.ring > 60.0:
                    factor *= 0.70
                delta *= factor
                mmol = delta * 1000.0
            # 中和消耗缓冲对：反向试剂先与现有负荷反应，按反应的 mmol 扣缓冲对
            # （同向试剂不直接剥离缓冲环，只把 pH 继续推向酸/碱）
            neutralized = 0.0
            if cfg.COUNTER_RULES:
                if channel == "acid" and self.net_acid < -1e-6:
                    neutralized = min(delta, -self.net_acid)
                    self.base -= neutralized
                    self.destroyed = min(cfg.CT0, self.destroyed + neutralized)
                    delta -= neutralized
                elif channel == "base" and self.net_acid > 1e-6:
                    neutralized = min(delta, self.net_acid)
                    self.acid -= neutralized
                    self.destroyed = min(cfg.CT0, self.destroyed + neutralized)
                    delta -= neutralized
            if channel == "acid":
                self.acid += delta
            else:
                self.base += delta
        else:
            mmol = dmg * cfg.DESTROY_MMOL_PER_DMG
            factor = 1.0
            # 氧化破甲：满缓冲效率 125%，缓冲削到 <25% 后效率 85%
            if cfg.COUNTER_RULES:
                if self.ring >= 60.0:
                    factor = 1.25
                elif self.ring < 25.0:
                    factor = 0.85
            self.destroyed = min(cfg.CT0, self.destroyed + mmol * factor / 1000.0)
            mmol *= factor

        self.last_damage_t = now
        self._invalidate()
        self._refresh()
        return {
            "channel": channel,
            "mmol": round(mmol, 2),
            "before": before,
            "after": self.snapshot(),
            "died": self.death_cause is not None,
            "cause": self.death_cause,
        }

    # ---- 还原/支援：修复被剥离的缓冲对 ----
    def repair_buffer(self, mmol, now):
        """返还缓冲对（维C 还原剂），返回恢复的缓冲环百分点。"""
        if mmol <= 0.0:
            return 0.0
        before = self.ring
        if self.cfg.LINEAR_HP:
            self.linear_hp = min(100.0, self.linear_hp + mmol)
            self.last_damage_t = now
            self._invalidate()
            self._refresh()
            return self.ring - before
        self.destroyed = max(0.0, self.destroyed - mmol / 1000.0)
        self.last_damage_t = now
        self._invalidate()
        self._refresh()
        return self.ring - before

    # ---- 队友中和（中和救援）----
    def neutralize(self, old_damage, channel, now):
        """对队友使用反向试剂：把酸/碱负荷拉回中性，最多到 0（不会造成伤害）。

        返回 (实际中和 mmol, 缓冲环恢复百分点, 中和后 pH)。
        """
        if channel not in ("acid", "base"):
            return 0.0, 0.0, self.ph
        if self.cfg.LINEAR_HP:
            return 0.0, 0.0, self.ph
        cfg = self.cfg
        before = self.ring
        L = self.net_acid
        delta = float(old_damage) * cfg.ACID_MMOL_PER_DMG / 1000.0
        if channel == "acid" and L < 0.0:
            m = min(delta, -L)
            self.base -= m
            if cfg.COUNTER_RULES:      # 中和反应消耗缓冲对
                self.destroyed = min(cfg.CT0, self.destroyed + m)
        elif channel == "base" and L > 0.0:
            m = min(delta, L)
            self.acid -= m
            if cfg.COUNTER_RULES:
                self.destroyed = min(cfg.CT0, self.destroyed + m)
        else:
            return 0.0, 0.0, self.ph
        self.last_damage_t = now
        self._invalidate()
        self._refresh()
        return m * 1000.0, self.ring - before, self.ph

    # ---- 脱战代偿 / 补给 ----
    def tick_regen(self, now, dt):
        """脱战 4s 后代偿：先清净酸/碱负荷，再修复缓冲对；总 E 恢复上限 20%/s。"""
        if not self.alive or dt <= 0.0:
            return 0.0
        cfg = self.cfg
        if now - self.last_damage_t < cfg.REGEN_DELAY:
            return 0.0
        if cfg.LINEAR_HP:       # 线性血条：脱战回血（原版 20 HP/s）
            before = self.linear_hp
            self.linear_hp = min(100.0, self.linear_hp + 20.0 * dt)
            self._invalidate()
            return self.linear_hp - before
        e_before = self.ring
        budget = cfg.E_REGEN_CAP_PCT_S * dt      # 本帧可恢复的 E 百分点

        # 1) 先清净负荷：每 mmol L 约 2 E
        net_mmol = self.net_acid * 1000.0
        if net_mmol != 0.0 and budget > 0.0:
            cap_mmol = min(abs(net_mmol), cfg.L_REGEN_MMOL_S * dt * 1000.0, budget / 2.0)
            step = cap_mmol / 1000.0
            if net_mmol > 0:
                self.acid -= step
            else:
                self.base -= step
            budget -= cap_mmol * 2.0

        # 2) 再修缓冲对：每 mmol D 约 1 E
        if self.destroyed > 0.0 and budget > 0.0:
            cap_mmol = min(self.destroyed * 1000.0,
                           cfg.D_REGEN_MMOL_S * dt * 1000.0, budget)
            self.destroyed -= cap_mmol / 1000.0
            if self.destroyed < 0.0:
                self.destroyed = 0.0
            budget -= cap_mmol

        self._invalidate()
        self._refresh()
        return self.ring - e_before

    # ---- 快照 ----
    def snapshot(self):
        p, r = self._calc()
        return {
            "ph": None if p is None else round(p, 3),
            "ring": round(r, 1),
            "l_mmol": round(self.l_mmol, 1),
            "d_mmol": round(self.d_mmol, 1),
            "ct_mmol": round(self.ct_mmol, 1),
            "alive": self.alive,
            "cause": self.death_cause,
        }

    def _refresh(self):
        if self.cfg.LINEAR_HP:
            if self.linear_hp <= 0.0 and self.death_cause is None:
                self.death_cause = "hp"
            return
        p, _ = self._calc()
        dead, cause = self.model.lethal(self.acid, self.base, self.destroyed, ph=p)
        if dead and self.death_cause is None:
            self.death_cause = cause
