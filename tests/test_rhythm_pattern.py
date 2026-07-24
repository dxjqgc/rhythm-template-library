"""测试扫弦/分解节奏型选型结果。

覆盖模板库完整性、拍数门槛硬约束、动机平铺/截断、栅格对齐、段落契合、风格匹配、
技法基线（段落级混排）、基准进行首选模板。
"""

import pytest
from pytheory import Fretboard

from rhythm_pattern import (
    STRUM_PATTERNS,
    RhythmGrid,
    StrumPattern,
    enumerate_rhythm_patterns,
)
from rhythm_pattern.model import Pluck, Stroke


# ── 共享夹具 ───────────────────────────────────────────────
@pytest.fixture(scope="module")
def guitar() -> Fretboard:
    """标准吉他调弦 (EADGBE)。"""
    return Fretboard.guitar()


# ── 候选进行表（类比 DIATONIC_CHORDS）────────────────────────
# expected: 首和弦应选到的模板集合（弹唱节奏型不唯一，命中其一即可）。
PROGRESSIONS = [
    # (进行, 段落, 风格, 期望首和弦模板集合(其一), 标签)
    ([("C", 4)], "chorus", "pop", {"pop D-DU-U-DU"}, "4拍副歌流行"),
    ([("C", 1), ("G", 1), ("Am", 1), ("F", 1)], "chorus", "pop", {"pop 8th-notes"}, "1拍流行副歌"),
    ([("C", 1), ("G", 1), ("Am", 1), ("F", 1)], "chorus", "rock", {"rock 8th down"}, "1拍摇滚副歌"),
    # 主歌民谣 4-2-2：4 拍 C 选 53231323 分解（民谣经典动作），2 拍 G/Am 选 folk D-DU 扫弦。
    # 分解模板引入后，4 拍专属整动机在 verse folk 上合理胜出；2 拍放不下 4 拍分解动机，退回扫弦。
    ([("C", 4), ("G", 2), ("Am", 2)], "verse", "folk", {"53231323 (8分)", "folk D-DU"}, "主歌民谣4-2-2"),
]


# ── 测试函数 ─────────────────────────────────────────────
class TestStrumPatternSelection:
    """节奏型选型基本属性测试。"""

    @pytest.mark.parametrize("progression,section,style,expected,tag", PROGRESSIONS)
    def test_first_chord_pattern(
        self, guitar, progression, section, style, expected, tag
    ):
        """进行首和弦应选到期望模板集合之一。"""
        events = enumerate_rhythm_patterns(progression, guitar, section=section, style=style)
        assert len(events) > 0, f"{tag} 应至少返回一个事件。"
        assert events[0].pattern.name in expected, (
            f"{tag} ({section}/{style}) 首和弦应在 {expected}，"
            f"实际 {events[0].pattern.name}。"
            f" 全部: {[e.pattern.name for e in events]}"
        )

    @pytest.mark.parametrize("progression,section,style,expected,tag", PROGRESSIONS)
    def test_event_count_matches_progression(
        self, guitar, progression, section, style, expected, tag
    ):
        """输出事件数与输入进行等长、同序。"""
        events = enumerate_rhythm_patterns(progression, guitar, section=section, style=style)
        assert len(events) == len(progression), (
            f"{tag} 应返回 {len(progression)} 个事件，实际 {len(events)}。"
        )
        for e, (chord, beats) in zip(events, progression):
            assert e.chord == chord, f"{tag} 事件和弦不匹配: {e.chord} != {chord}"
            assert e.beats == beats, f"{tag} 事件拍数不匹配: {e.beats} != {beats}"


class TestMinBeats:
    """拍数门槛硬约束测试。"""

    def test_one_beat_never_picks_high_min_beats(self, guitar):
        """占 1 拍的和弦不会选 min_beats >= 2 的模板。"""
        events = enumerate_rhythm_patterns([("C", 1)], guitar, section="chorus", style="pop")
        for e in events:
            assert e.pattern.min_beats <= 1, (
                f"占 1 拍却选了 min_beats={e.pattern.min_beats} 的 {e.pattern.name}"
            )

    def test_four_beat_allows_four_beat_pattern(self, guitar):
        """占 4 拍可命中 min_beats=4 的 pop D-DU-U-DU。"""
        e = enumerate_rhythm_patterns([("C", 4)], guitar, section="chorus", style="pop")[0]
        assert e.pattern.name == "pop D-DU-U-DU"
        assert e.pattern.min_beats == 4

    def test_two_beat_excludes_four_beat_pattern(self, guitar):
        """占 2 拍不会选 min_beats=4 的模板。"""
        events = enumerate_rhythm_patterns([("C", 2)], guitar, section="chorus", style="pop")
        assert all(e.pattern.name != "pop D-DU-U-DU" for e in events), (
            "占 2 拍不应选到 min_beats=4 的 pop D-DU-U-DU"
        )


class TestGridAlignment:
    """栅格对齐与密度测试。"""

    @pytest.mark.parametrize("beats", [1, 2, 3, 4, 5])
    def test_grid_length_is_four_times_beats(self, guitar, beats):
        """栅格长度 = 4 × 拍数，且 n_beats 等于拍数。"""
        e = enumerate_rhythm_patterns([("C", beats)], guitar, section="chorus", style="pop")[0]
        assert len(e.grid.cells) == 4 * beats
        assert e.grid.n_beats == beats

    def test_density_range(self, guitar):
        """密度恒在 [0, 1]，且非全休止栅格密度 > 0。"""
        for p in STRUM_PATTERNS:
            d = p.density()
            assert 0.0 <= d <= 1.0, f"{p.name} 密度 {d} 越界"
        e = enumerate_rhythm_patterns([("C", 4)], guitar, section="chorus", style="pop")[0]
        assert e.grid.density > 0.0

    def test_grid_post_init_rejects_bad_length(self):
        """RhythmGrid 拒绝非 4 倍数长度。"""
        with pytest.raises(ValueError):
            RhythmGrid((Stroke("D"), Stroke("U"), Stroke("D")))  # 3 格

    def test_pattern_post_init_rejects_bad_unit(self):
        """StrumPattern 要求 grid_motif 长度 = 4 * motif_beats。"""
        with pytest.raises(ValueError):
            StrumPattern(
                name="bad",
                grid_motif=(Stroke("D"),),  # motif_beats=1 应为 4 格
                motif_beats=1,
                min_beats=1,
                ideal_beats=(1,),
                sections=("verse",),
                style="pop",
            )


class TestSectionCoherence:
    """段落契合测试。"""

    def test_verse_progression_stays_in_verse_patterns(self, guitar):
        """主歌进行只选 verse 适用的模板，不混入副歌专用模板。"""
        prog = [("C", 4), ("G", 2), ("Am", 2)]
        events = enumerate_rhythm_patterns(prog, guitar, section="verse", style="folk")
        verse_names = {p.name for p in STRUM_PATTERNS if "verse" in p.sections}
        for e in events:
            assert e.pattern.name in verse_names, (
                f"主歌进行不应选 {e.pattern.name}（不在 verse 适用模板内）"
            )


class TestStyleMismatch:
    """风格不匹配时降级但不剔除测试。"""

    def test_style_mismatch_falls_back(self, guitar):
        """请求 rock 风格时，1 拍副歌应选 rock 模板而非 pop。"""
        events = enumerate_rhythm_patterns(
            [("C", 1), ("G", 1)], guitar, section="chorus", style="rock"
        )
        # rock 8th down 在 rock 风格下应被偏好（无风格罚分）。
        assert all(e.pattern.name == "rock 8th down" for e in events), (
            f"rock 风格应选 rock 8th down，实际 {[e.pattern.name for e in events]}"
        )

    def test_fallback_when_no_style_match(self, guitar):
        """即使请求的风格没有完全匹配的模板，仍能给出结果（不剔除）。"""
        # 4 拍 chorus + rock：没有 min_beats=4 的 rock 模板，应回退到 1 拍 rock 模板平铺。
        e = enumerate_rhythm_patterns([("C", 4)], guitar, section="chorus", style="rock")[0]
        assert e.beats == 4
        assert len(e.grid.cells) == 16
        assert e.grid.n_beats == 4


class TestTemplateLibrary:
    """模板库完整性测试。"""

    def test_library_nonempty(self):
        """模板库非空。"""
        assert len(STRUM_PATTERNS) >= 4

    def test_all_patterns_have_consistent_metadata(self):
        """每个模板元数据齐全：grid_motif = 4*motif_beats 格，min_beats>=motif_beats，sections 非空，technique 与栅格内容一致。"""
        for p in STRUM_PATTERNS:
            assert len(p.grid_motif) == 4 * p.motif_beats, (
                f"{p.name}: grid_motif 长度 {len(p.grid_motif)} != 4*{p.motif_beats}"
            )
            assert p.min_beats >= p.motif_beats
            assert len(p.sections) > 0
            assert p.style in {"folk", "pop", "rock"}
            assert p.technique in {"strum", "arpeggio"}
            # technique 与栅格内容一致：arpeggio 含 Pluck，strum 不含。
            has_pluck = any(c is not None and not isinstance(c, Stroke) for c in p.grid_motif)
            assert has_pluck == p.is_arpeggio, (
                f"{p.name}: technique={p.technique} 与栅格内容(含Pluck={has_pluck})不一致"
            )

    def test_boom_chick_is_fallback(self):
        """boom-chick 作为兜底模板存在（min_beats=1）。"""
        bc = [p for p in STRUM_PATTERNS if p.name == "boom-chick"]
        assert len(bc) == 1
        assert bc[0].min_beats == 1


class TestMotifTiling:
    """动机平铺/截断测试：grid_for 在整数倍时平铺、非整数倍时取前缀截断。"""

    def test_integer_multiple_tiles_full_motif(self):
        """beats 是 motif_beats 整数倍时，平铺完整动机。"""
        # pop 8th-notes: motif 1 拍 (D,U,D,U)，占 4 拍应平铺 4 遍。
        p = next(p for p in STRUM_PATTERNS if p.name == "pop 8th-notes")
        grid = p.grid_for(4)
        assert grid.cells == p.grid_motif * 4
        assert len(grid.cells) == 16

    def test_non_integer_multiple_truncates(self):
        """beats 非整数倍时，平铺整数份动机 + 末尾取动机前缀截断。"""
        # folk D-DU: motif 2 拍 (8 格)，占 3 拍 = 12 格：1 份完整动机 (8) + 动机前 4 格前缀。
        p = next(p for p in STRUM_PATTERNS if p.name == "folk D-DU")
        assert p.motif_beats == 2
        grid = p.grid_for(3)
        assert len(grid.cells) == 12  # 3 拍 = 12 格
        assert grid.cells == p.grid_motif + p.grid_motif[:4]  # 整动机 + 前缀截断

    def test_beats_shorter_than_motif_takes_prefix(self):
        """beats 短于动机时，取动机前缀（截断到不足一个动机）。"""
        # pop D-DU-U-DU: motif 4 拍 (16 格)，占 1 拍取前 4 格。
        p = next(p for p in STRUM_PATTERNS if p.name == "pop D-DU-U-DU")
        grid = p.grid_for(1)
        assert len(grid.cells) == 4
        assert grid.cells == p.grid_motif[:4]

    def test_grid_length_always_four_times_beats(self):
        """任意拍数栅格长度恒为 4*beats，无论是否整数倍平铺。"""
        p = next(p for p in STRUM_PATTERNS if p.name == "53231323 (8分)")
        assert p.motif_beats == 4
        for beats in (4, 5, 6, 7, 8):
            grid = p.grid_for(beats)
            assert len(grid.cells) == 4 * beats
            assert grid.n_beats == beats


class TestTechniqueBaseline:
    """技法基线（段落级混排）测试。"""

    def _names(self, gtr, base):
        ev = enumerate_rhythm_patterns(
            [("C", 4), ("G", 2), ("Am", 2)], gtr,
            section="verse", style="folk", technique_baseline=base,
        )
        return [e.pattern.name for e in ev]

    def test_arpeggio_baseline_picks_arpeggio(self, guitar):
        """arpeggio 基线把和弦压向分解模板。"""
        arp = {p.name for p in STRUM_PATTERNS if p.is_arpeggio}
        for n in self._names(guitar, "arpeggio"):
            assert n in arp, f"arpeggio 基线应选分解，实际含 {n}"

    def test_strum_baseline_picks_strum(self, guitar):
        """strum 基线把和弦压向扫弦模板。"""
        strum = {p.name for p in STRUM_PATTERNS if p.is_strum}
        for n in self._names(guitar, "strum"):
            assert n in strum, f"strum 基线应选扫弦，实际含 {n}"

    def test_mixed_and_none_do_not_force_technique(self, guitar):
        """mixed / None 不强制技法，允许扫拆混排（不强求全扫或全拆）。"""
        # 只要能产出结果即可--mixed/None 的语义是不干预，选型器自由选。
        for base in ("mixed", None):
            names = self._names(guitar, base)
            assert len(names) == 3, f"{base} 基线应返回 3 个事件"
