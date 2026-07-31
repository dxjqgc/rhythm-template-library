"""6/8 拍号支持回归测试。

验证时值参数化（``ticks_per_beat`` 按拍号分母 4/3）、拍号契合重罚筛 6/8 专属模板、
重音 (accent) 字段往返与试听 velocity 映射。
"""

import pytest

from pytheory import Fretboard

from rhythm_pattern import (
    STRUM_PATTERNS,
    SelectionContext,
    enumerate_rhythm_patterns,
)
from rhythm_pattern.model import Pluck, RhythmGrid, Stroke, StrumPattern


@pytest.fixture(scope="module")
def guitar() -> Fretboard:
    return Fretboard.guitar()


def _pattern_68() -> StrumPattern:
    """6/8 folk D-DU：D(3,strong) + U(3,weak)，2 拍动机 = 1 小节。"""
    return next(p for p in STRUM_PATTERNS if p.name == "6/8 folk D-DU")


# ── 时值模型：ticks_per_beat 与 grid 不变量 ──────────────────────────


class TestTimeSignatureModel:
    def test_68_ticks_per_beat_is_three(self):
        """6/8 拍号 ticks_per_beat = 3（附点 8 分拍）。"""
        assert _pattern_68().ticks_per_beat == 3

    def test_44_ticks_per_beat_is_four(self):
        """4/4 拍号 ticks_per_beat = 4（不变）。"""
        p44 = next(p for p in STRUM_PATTERNS if p.time_signature == (4, 4))
        assert p44.ticks_per_beat == 4

    def test_grid_68_total_is_three_times_beats(self, guitar):
        """6/8 模板 grid_for 产出栅格总时值 = 3 * beats。"""
        p = _pattern_68()
        for beats in (2, 4):
            grid = p.grid_for(beats)
            assert sum(c.duration for c in grid.cells) == 3 * beats
            assert grid.ticks_per_beat == 3
            assert grid.n_beats == beats

    def test_grid_68_accepts_three_total(self):
        """6/8 下总时值 3 是合法 1 拍（对比 4/4 下 3 被拒）。"""
        # 6/8：tpb=3，总时值 3 合法
        g68 = RhythmGrid((Stroke("D", 3),), ticks_per_beat=3)
        assert g68.n_beats == 1
        # 4/4：tpb=4（默认），总时值 3 仍被拒
        with pytest.raises(ValueError):
            RhythmGrid((Stroke("D", 1), Stroke("U", 1), Stroke("D", 1)))

    def test_pattern_68_rejects_wrong_unit(self):
        """6/8 motif_beats=1 应总时值 3，传总时值 4 被拒。"""
        with pytest.raises(ValueError):
            StrumPattern(
                name="bad",
                grid_motif=(Stroke("D", 4),),  # 总时值 4 != 3 * 1
                motif_beats=1, min_beats=1, ideal_beats=(1,),
                sections=("verse",), style="folk", time_signature=(6, 8),
            )

    def test_unsupported_denominator_rejected(self):
        """不支持的拍号分母（如 /2）构造时被拒。"""
        with pytest.raises(ValueError):
            StrumPattern(
                name="bad",
                grid_motif=(Stroke("D", 2),),
                motif_beats=1, min_beats=1, ideal_beats=(1,),
                sections=("verse",), style="folk", time_signature=(2, 2),
            )

    def test_metadata_consistency_all_patterns(self):
        """所有模板（4/4 与 6/8）grid_motif 总时值 = ticks_per_beat * motif_beats。"""
        for p in STRUM_PATTERNS:
            assert sum(c.duration for c in p.grid_motif) == p.ticks_per_beat * p.motif_beats, (
                f"{p.name}: 总时值 {sum(c.duration for c in p.grid_motif)} "
                f"!= tpb({p.ticks_per_beat})*{p.motif_beats}"
            )


# ── 选型：拍号契合重罚 ────────────────────────────────────────────────


class TestSelection68:
    def test_68_context_picks_68_patterns(self, guitar):
        """ctx=(6,8) 下选出的模板 time_signature 全为 (6,8)。"""
        ctx = SelectionContext(section="chorus", style="pop", time_signature=(6, 8))
        events = enumerate_rhythm_patterns([("C", 2)], guitar, ctx=ctx)
        assert all(e.pattern.time_signature == (6, 8) for e in events), (
            [e.pattern.name for e in events]
        )

    def test_44_default_rejects_68_patterns(self, guitar):
        """4/4 默认（无 ctx 拍号）下不选 6/8 模板——6/8 吃重罚。"""
        events = enumerate_rhythm_patterns([("C", 4)], guitar, section="chorus", style="pop")
        assert all(e.pattern.time_signature == (4, 4) for e in events), (
            [e.pattern.name for e in events]
        )

    def test_44_pop_ddu_still_chosen_for_4_beats(self, guitar):
        """4/4 下 4 拍和弦仍选 pop D-DU-U-DU（未受 6/8 改造影响）。"""
        e = enumerate_rhythm_patterns([("C", 4)], guitar, section="chorus", style="pop")[0]
        assert e.pattern.name == "pop D-DU-U-DU"

    def test_68_library_has_exclusive_patterns(self):
        """硬编码库含 6/8 专属模板。"""
        p68 = [p for p in STRUM_PATTERNS if p.time_signature == (6, 8)]
        assert len(p68) >= 3
        # 每个含至少一个 strong accent
        for p in p68:
            assert any(getattr(c, "accent", None) == "strong" for c in p.grid_motif), p.name


# ── 重音字段往返与试听 velocity ──────────────────────────────────────


class TestAccent:
    def test_accent_field_roundtrip(self):
        """accent 字段经 pattern_to_dict/dict_to_pattern 往返保留。"""
        from rhythm_pattern.serialization import dict_to_pattern, pattern_to_dict

        p = StrumPattern(
            name="accent-rt",
            grid_motif=(Stroke("D", 3, "strong"), Pluck(role=None, duration=3, accent="weak")),
            motif_beats=2, min_beats=2, ideal_beats=(2,),
            sections=("verse",), style="folk", technique="arpeggio", time_signature=(6, 8),
        )
        d = pattern_to_dict(p)
        assert d["grid_motif"][0]["accent"] == "strong"
        assert d["grid_motif"][1]["accent"] == "weak"
        p2 = dict_to_pattern(d)
        assert p2.grid_motif[0].accent == "strong"
        assert p2.grid_motif[1].accent == "weak"

    def test_old_cell_without_accent_defaults(self):
        """旧 cell dict（无 accent 键）读回 'default'。"""
        from rhythm_pattern.serialization import _cell_from_dict

        c = _cell_from_dict({"type": "stroke", "direction": "D", "duration": 4})
        assert c.accent == "default"

    def test_notelist_velocity_by_accent(self, guitar):
        """6/8 模板 notelist 的 velocity 按 accent 映射（strong>default>weak）。"""
        from web_manager.adapter import pattern_to_notelist

        p = _pattern_68()
        nl = pattern_to_notelist(p, "C", guitar, 2)
        assert nl["ticks_per_beat"] == 3
        assert nl["total_tick"] == 6
        # D(3,strong) velocity=110、U(3,weak) velocity=60
        strong_vel = next(n["velocity"] for n in nl["notes"] if n["start_tick"] == 0)
        weak_vel = next(n["velocity"] for n in nl["notes"] if n["start_tick"] == 3)
        assert strong_vel == 110
        assert weak_vel == 60
        # grid 字段带 accent
        assert nl["grid"][0]["accent"] == "strong"
        assert nl["grid"][1]["accent"] == "weak"
