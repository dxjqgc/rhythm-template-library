"""3/4 拍号支持回归测试。

验证 3/4 专属模板（532132 分解 / waltz 扫弦 / boom-chick 兜底 / cadence 收束）、
拍号契合筛（/8 模板重罚出局、4/4 同分母模板轻罚兜底）、以及 532132 在 3/4 与 6/8
的孪生成对（同指法跨拍号各自编码）。
"""

import pytest

from pytheory import Fretboard

from rhythm_pattern import (
    STRUM_PATTERNS,
    SelectionContext,
    enumerate_rhythm_patterns,
)
from rhythm_pattern.model import Stroke, StrumPattern


@pytest.fixture(scope="module")
def guitar() -> Fretboard:
    return Fretboard.guitar()


def _pattern_532132_34() -> StrumPattern:
    """3/4 532132：六音 8 分（各 2 tick）填满 3 拍小节。"""
    return next(p for p in STRUM_PATTERNS if p.name == "3/4 532132 (8分)")


def _pattern_532132_68() -> StrumPattern:
    """6/8 532132 孪生：六音 8 分（各 1 tick）填满 2 附点拍小节。"""
    return next(p for p in STRUM_PATTERNS if p.name == "6/8 532132 (8分)")


# ── 时值模型：ticks_per_beat 与 grid 不变量 ──────────────────────────


class TestTimeSignatureModel34:
    def test_34_ticks_per_beat_is_four(self):
        """3/4 拍号 ticks_per_beat = 4（与其他 /4 拍号一致，8 分音符 2 tick）。"""
        assert _pattern_532132_34().ticks_per_beat == 4

    def test_grid_34_total_is_four_times_beats(self):
        """3/4 模板 grid_for 产出栅格总时值 = 4 * beats。"""
        p = _pattern_532132_34()
        for beats in (3, 6):
            grid = p.grid_for(beats)
            assert sum(c.duration for c in grid.cells) == 4 * beats
            assert grid.ticks_per_beat == 4
            assert grid.n_beats == beats

    def test_pattern_34_rejects_wrong_total(self):
        """3/4 motif_beats=3 应总时值 12，传 8 被拒。"""
        with pytest.raises(ValueError):
            StrumPattern(
                name="bad",
                grid_motif=(Stroke("D", 4), Stroke("D", 4)),  # 总时值 8 != 4*3
                motif_beats=3, min_beats=3, ideal_beats=(3,),
                sections=("verse",), style="folk", time_signature=(3, 4),
            )

    def test_34_library_has_exclusive_patterns(self):
        """硬编码库含 3/4 专属模板家族（532132 + waltz + boom-chick + cadence）。"""
        p34 = [p for p in STRUM_PATTERNS if p.time_signature == (3, 4)]
        names = {p.name for p in p34}
        assert {
            "3/4 532132 (8分)",
            "3/4 waltz D-D-DU",
            "3/4 boom-chick",
            "3/4 arpeggio cadence (tail)",
        } <= names
        # 每个含至少一个 strong accent
        for p in p34:
            assert any(getattr(c, "accent", None) == "strong" for c in p.grid_motif), p.name


# ── 孪生模板：532132 跨拍号成对 ──────────────────────────────────────


class TestTwin532132:
    def test_twin_pair_exists(self):
        """532132 在 3/4 与 6/8 各有一份编码（模板 time_signature 单值，跨拍号靠孪生对）。"""
        assert _pattern_532132_34().time_signature == (3, 4)
        assert _pattern_532132_68().time_signature == (6, 8)

    def test_twin_same_string_roles(self):
        """孪生对 grid_motif 的角色序一致（同指法 5-3-2-1-3-2，仅时值编码不同）。"""
        roles34 = [
            getattr(c, "role", None) for c in _pattern_532132_34().grid_motif
            if hasattr(c, "role")
        ]
        roles68 = [
            getattr(c, "role", None) for c in _pattern_532132_68().grid_motif
            if hasattr(c, "role")
        ]
        assert [repr(r) for r in roles34] == [repr(r) for r in roles68]
        assert len(roles34) == 6

    def test_twin_instantiation_same_strings_on_C(self, guitar):
        """孪生对实例化到 C 弦序一致（0=六弦下标：1,3,4,5,3,4 = 五-三-二-一-三-二弦）。"""
        from rhythm_pattern.strum_patterns import instantiate_pattern

        for p, beats in (
            (_pattern_532132_34(), 3),
            (_pattern_532132_68(), 2),
        ):
            ev = instantiate_pattern(p, "C", guitar, beats)
            seq = [c.strings[0] for c in ev.grid.cells if getattr(c, "strings", None)]
            assert seq == [1, 3, 4, 5, 3, 4], (p.name, seq)

    def test_twin_instantiation_remaps_on_G(self, guitar):
        """孪生对换和弦自动重映射（G 上根音移到 6 弦，弦角色对调弦中立的设计意图）。"""
        from rhythm_pattern.strum_patterns import instantiate_pattern

        for p, beats in (
            (_pattern_532132_34(), 3),
            (_pattern_532132_68(), 2),
        ):
            ev = instantiate_pattern(p, "G", guitar, beats)
            seq = [c.strings[0] for c in ev.grid.cells if getattr(c, "strings", None)]
            assert seq == [0, 2, 5, 4, 2, 5], (p.name, seq)  # 6-4-1-3-4-1 弦
            assert None not in seq

    def test_34_accent_marks_beat_heads(self):
        """3/4 532132 的 accent 标三个拍头：第 1/3/5 音为 strong/weak/weak。"""
        accents = [
            getattr(c, "accent", None) for c in _pattern_532132_34().grid_motif
        ]
        assert accents == ["strong", "default", "weak", "default", "weak", "default"]

    def test_68_twin_accent_marks_group_heads(self):
        """6/8 孪生的 accent 标两组组头：第 1 音 strong、第 4 音 weak。"""
        accents = [
            getattr(c, "accent", None) for c in _pattern_532132_68().grid_motif
        ]
        assert accents == ["strong", "default", "default", "weak", "default", "default"]


# ── 选型：拍号契合与同分母兜底 ────────────────────────────────────────


class TestSelection34:
    def test_34_context_picks_34_patterns(self, guitar):
        """ctx=(3,4) 下选出的模板 time_signature 全为 (3,4)。"""
        for section, style in (("chorus", "folk"), ("verse", "folk")):
            ctx = SelectionContext(section=section, style=style, time_signature=(3, 4))
            events = enumerate_rhythm_patterns([("C", 3)], guitar, ctx=ctx)
            assert all(e.pattern.time_signature == (3, 4) for e in events), (
                section,
                [e.pattern.name for e in events],
            )

    def test_34_rejects_68_patterns(self, guitar):
        """(3,4) ctx 下 6/8 模板吃分母重罚不入选（tick 单位不同）。"""
        ctx = SelectionContext(section="chorus", style="folk", time_signature=(3, 4))
        events = enumerate_rhythm_patterns([("C", 3)], guitar, ctx=ctx)
        assert all(e.pattern.time_signature != (6, 8) for e in events)

    def test_44_default_rejects_34_patterns(self, guitar):
        """4/4 默认（无 ctx 拍号）下不选 3/4 模板。"""
        events = enumerate_rhythm_patterns([("C", 4)], guitar, section="chorus", style="pop")
        assert all(e.pattern.time_signature == (4, 4) for e in events), (
            [e.pattern.name for e in events]
        )

    def test_44_template_costs_light_penalty_in_34_ctx(self, guitar):
        """4/4 模板在 (3,4) ctx 吃 W_TIME_SIG_NUMERATOR 轻罚而非 W_TIME_SIG_MISMATCH 重罚。

        探针对：除 time_signature 外元数据全同（同栅格同段落同风格），价差即纯拍号罚分。
        分母不同（/8）被拒的行为由 test_34_rejects_68_patterns 覆盖。
        """
        from rhythm_pattern.strum_patterns import (
            W_TIME_SIG_NUMERATOR,
            _resolve_voicing,
            pattern_cost,
        )
        from chord_fingering import count_muted

        ctx = SelectionContext(section="chorus", style="folk", time_signature=(3, 4))
        voicing = _resolve_voicing("C", guitar, 4)
        muted = count_muted(voicing.positions)

        base = dict(
            grid_motif=(Stroke("D", 4, "strong"),),
            motif_beats=1, min_beats=1, ideal_beats=(1, 3),
            sections=("chorus", "verse", "bridge", "outro"),
            style="folk",
            tags=("guitar", "slow", "soft"),
        )
        p34 = StrumPattern(name="probe-34", time_signature=(3, 4), **base)
        p44 = StrumPattern(name="probe-44", time_signature=(4, 4), **base)
        kw = dict(beats=3, muted=muted, density_neighbor_delta=0.0, ctx=ctx)
        cost34 = pattern_cost(p34, **kw)
        cost44 = pattern_cost(p44, **kw)
        # 同分母跨分子：价差恰为轻罚值（浮点容差内）
        assert abs((cost44 - cost34) - W_TIME_SIG_NUMERATOR) < 1e-6, (
            cost44 - cost34
        )

    def test_boom_chick_fallback_prefers_same_signature(self, guitar):
        """_boom_chick_fallback 在 3/4 下取 3/4 专属 boom-chick。"""
        from rhythm_pattern.strum_patterns import _boom_chick_fallback

        fb = _boom_chick_fallback((3, 4))
        assert fb.name == "3/4 boom-chick"
        assert fb.time_signature == (3, 4)

    def test_arrange_progression_34(self, guitar):
        """3/4 整段编排只在 3/4 模板里选。"""
        from rhythm_pattern import arrange_progression

        ctx = SelectionContext(
            section="verse", style="folk", time_signature=(3, 4),
            technique_baseline="fingerpicking",
        )
        arranged = arrange_progression(
            [("C", 3), ("G", 3), ("Am", 3), ("F", 3)], guitar, ctx=ctx
        )
        assert all(e.pattern.time_signature == (3, 4) for e in arranged), (
            [e.pattern.name for e in arranged]
        )

    def test_arrange_progression_34_strum(self, guitar):
        """3/4 副歌扫弦编排（无技法基线）也在 3/4 模板里选。"""
        from rhythm_pattern import arrange_progression

        ctx = SelectionContext(section="chorus", style="folk", time_signature=(3, 4))
        arranged = arrange_progression(
            [("C", 3), ("G", 3), ("Am", 3), ("F", 3)], guitar, ctx=ctx
        )
        assert all(e.pattern.time_signature == (3, 4) for e in arranged), (
            [e.pattern.name for e in arranged]
        )
