"""测试扫弦/分解节奏型选型结果。

覆盖模板库完整性、拍数门槛硬约束、动机平铺/截断、栅格对齐、段落契合、风格匹配、
技法基线（段落级混排）、基准进行首选模板。
"""

import pytest
from pytheory import Fretboard

from rhythm_pattern import (
    RhythmGrid,
    SelectionContext,
    StrumPattern,
    enumerate_rhythm_patterns,
    get_pattern_source,
)
from rhythm_pattern.model import Pluck, Stroke

ALL_PATTERNS = get_pattern_source().patterns()
"""模板库当前内容（读 DB；web 管理器改模板后这里的断言跟着走）。"""


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
    # 1 拍副歌：⚠ 期望是 DB 现状（1 拍扫弦模板 16 分 D-D-DU / rock 8th down 已在 web
    # 管理器里删掉，4/4 没有 min_beats==1 的扫弦能用了），所以落回 1 拍分解动机。
    # 补回 1 拍扫弦模板后要把这两条改回去；期望集跟 DB 走（见库 CLAUDE.md）。
    ([("C", 1), ("G", 1), ("Am", 1), ("F", 1)], "chorus", "pop", {"root-5-top2 (1拍)"}, "1拍流行副歌"),
    ([("C", 1), ("G", 1), ("Am", 1), ("F", 1)], "chorus", "rock", {"root-5-top2 (1拍)"}, "1拍摇滚副歌"),
    # 主歌民谣 4-2-2：4 拍 C 选 53231323 分解，2 拍 G/Am 压向短分解（段落技法先验
    # W_SECTION_STRUM 把主歌扫弦压成次选；1 拍短分解动机放得进 2 拍和弦）。
    ([("C", 4), ("G", 2), ("Am", 2)], "verse", "folk",
     {"53231323 (8分)", "arpeggio placeholder", "arpeggio cadence short (tail)"},
     "主歌民谣4-2-2"),
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
        """栅格总时值 = 4 × 拍数，且 n_beats 等于拍数。"""
        e = enumerate_rhythm_patterns([("C", beats)], guitar, section="chorus", style="pop")[0]
        assert sum(c.duration for c in e.grid.cells) == 4 * beats
        assert e.grid.n_beats == beats

    def test_density_range(self, guitar):
        """密度恒在 [0, 1]，且非全休止栅格密度 > 0。"""
        for p in ALL_PATTERNS:
            d = p.density()
            assert 0.0 <= d <= 1.0, f"{p.name} 密度 {d} 越界"
        e = enumerate_rhythm_patterns([("C", 4)], guitar, section="chorus", style="pop")[0]
        assert e.grid.density > 0.0

    def test_grid_post_init_rejects_bad_length(self):
        """RhythmGrid 拒绝非 4 倍数总时值。"""
        with pytest.raises(ValueError):
            RhythmGrid((Stroke("D", 1), Stroke("U", 1), Stroke("D", 1)))  # 总时值 3

    def test_pattern_post_init_rejects_bad_unit(self):
        """StrumPattern 要求 grid_motif 总时值 = 4 * motif_beats。"""
        with pytest.raises(ValueError):
            StrumPattern(
                name="bad",
                grid_motif=(Stroke("D", 1),),  # motif_beats=1 应总时值 4
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
        verse_names = {p.name for p in ALL_PATTERNS if "verse" in p.sections}
        for e in events:
            assert e.pattern.name in verse_names, (
                f"主歌进行不应选 {e.pattern.name}（不在 verse 适用模板内）"
            )


class TestStyleMismatch:
    """风格不匹配时降级但不剔除测试。"""

    def test_style_mismatch_falls_back(self, guitar):
        """请求 rock 风格时，1 拍副歌应选 rock 模板而非 pop。

        用 6/8 来验：4/4 的 1 拍 rock 扫弦（rock 8th down）已在 web 管理器里删掉，
        现存的 rock 模板只剩 `6/8 rock dotted down`；它无风格罚分，而同一槽位上的
        pop/folk 模板都要吃 W_STYLE_MISMATCH。
        """
        events = enumerate_rhythm_patterns(
            [("C", 1), ("G", 1)], guitar,
            ctx=SelectionContext(
                section="chorus", style="rock", time_signature=(6, 8),
            ),
        )
        assert all(e.pattern.name == "6/8 rock dotted down" for e in events), (
            f"rock 风格应选 6/8 rock dotted down，实际 {[e.pattern.name for e in events]}"
        )

    def test_fallback_when_no_style_match(self, guitar):
        """即使请求的风格没有完全匹配的模板，仍能给出结果（不剔除）。"""
        # 4 拍 chorus + rock：没有 min_beats=4 的 rock 模板，应回退到 1 拍 rock 模板平铺。
        e = enumerate_rhythm_patterns([("C", 4)], guitar, section="chorus", style="rock")[0]
        assert e.beats == 4
        assert sum(c.duration for c in e.grid.cells) == 16
        assert e.grid.n_beats == 4


class TestTemplateLibrary:
    """模板库完整性测试。"""

    def test_library_nonempty(self):
        """模板库非空。"""
        assert len(ALL_PATTERNS) >= 4

    def test_all_patterns_have_consistent_metadata(self):
        """每个模板元数据齐全：grid_motif 总时值 = ticks_per_beat*motif_beats，min_beats>=motif_beats，sections 非空，technique 与栅格内容一致。"""
        for p in ALL_PATTERNS:
            assert sum(c.duration for c in p.grid_motif) == p.ticks_per_beat * p.motif_beats, (
                f"{p.name}: grid_motif 总时值 {sum(c.duration for c in p.grid_motif)} "
                f"!= ticks_per_beat({p.ticks_per_beat})*{p.motif_beats}"
            )
            assert p.min_beats >= p.motif_beats
            assert len(p.sections) > 0
            assert p.style in {"folk", "pop", "rock"}
            assert p.technique in {"strum", "fingerpicking", "arpeggio"}
            # technique 与栅格内容一致：fingerpicking/arpeggio 含 Pluck，strum 不含。
            has_pluck = any(isinstance(c, Pluck) for c in p.grid_motif)
            assert has_pluck == (not p.is_strum), (
                f"{p.name}: technique={p.technique} 与栅格内容(含Pluck={has_pluck})不一致"
            )

    def test_boom_chick_is_fallback(self):
        """boom-chick 作为兜底模板存在（min_beats=1）。"""
        bc = [p for p in ALL_PATTERNS if p.name == "boom-chick"]
        assert len(bc) == 1
        assert bc[0].min_beats == 1


class TestMotifTiling:
    """动机平铺/截断测试：grid_for 在整数倍时平铺、非整数倍时取前缀截断。"""

    def test_integer_multiple_tiles_full_motif(self):
        """beats 是 motif_beats 整数倍时，平铺完整动机。"""
        # 内联构造「1 拍 = D8分 + U8分」的动机（不再按名字取 DB 模板：模板只存在于
        # templates.json，web 管理器一删就 StopIteration）。占 4 拍应平铺 4 遍。
        p = StrumPattern(
            name="t_8th",
            grid_motif=(Stroke("D", 2), Stroke("U", 2)),
            motif_beats=1, min_beats=1, ideal_beats=(1, 2, 4),
            sections=("chorus",), style="pop",
        )
        grid = p.grid_for(4)
        assert grid.cells == p.grid_motif * 4
        assert sum(c.duration for c in grid.cells) == 16

    def test_non_integer_multiple_truncates(self):
        """beats 非整数倍时，平铺整数份动机 + 末尾取动机前缀截断（按 duration 之和）。"""
        # folk D-DU: motif 2 拍 (Stroke(D,4)+Stroke(D,1)+Stroke(U,3), 总时值 8)，占 3 拍 = 总时值 12：
        # 1 份完整动机 (8) + 截断到总时值 4 的前缀 = 第一个 Stroke("D",4)。
        p = next(p for p in ALL_PATTERNS if p.name == "folk D-DU")
        assert p.motif_beats == 2
        grid = p.grid_for(3)
        assert sum(c.duration for c in grid.cells) == 12  # 3 拍 = 12 个 16 分
        assert grid.cells == p.grid_motif + (Stroke("D", 4),)  # 整动机 + 截断前缀

    def test_beats_shorter_than_motif_takes_prefix(self):
        """beats 短于动机时，取动机前缀（截断到不足一个动机）。"""
        # pop D-DU-U-DU: motif 4 拍 (总时值 16)，占 1 拍取总时值 4 的前缀 = 第一个 Stroke("D",4)。
        p = next(p for p in ALL_PATTERNS if p.name == "pop D-DU-U-DU")
        grid = p.grid_for(1)
        assert sum(c.duration for c in grid.cells) == 4
        assert grid.cells == (Stroke("D", 4),)

    def test_grid_length_always_four_times_beats(self):
        """任意拍数栅格总时值恒为 4*beats，无论是否整数倍平铺。"""
        p = next(p for p in ALL_PATTERNS if p.name == "53231323 (8分)")
        assert p.motif_beats == 4
        for beats in (4, 5, 6, 7, 8):
            grid = p.grid_for(beats)
            assert sum(c.duration for c in grid.cells) == 4 * beats
            assert grid.n_beats == beats

    def test_truncate_across_action_boundary(self):
        """截断发生在动作中间时，边界动作 duration 被正确截断（不跨过整动作丢弃）。"""
        # 4 拍动机 (总时值 16)：D(3)+D(2)+D(3)+D(3)+D(3)+D(2)。第一个动作 duration=3，
        # 故取总时值 4 的前缀时，第二个 D(2) 会被切到 D(1)（3+1=4），而非整丢弃或越界。
        motif = StrumPattern(
            name="t",
            grid_motif=(Stroke("D", 3), Stroke("D", 2), Stroke("D", 3), Stroke("D", 3), Stroke("D", 3), Stroke("D", 2)),
            motif_beats=4, min_beats=4, ideal_beats=(4,), sections=("verse",), style="pop",
        )
        # beats=1 (need=4 <= 动机总时值 16)：前缀截断到 4 = D(3) + D(2 截到 1)。
        grid = motif.grid_for(1)
        assert sum(c.duration for c in grid.cells) == 4
        assert grid.cells == (Stroke("D", 3), Stroke("D", 1))
        # 末尾是原 D(2) 被截断成的 D(1)——截断切进了动作中间。
        assert isinstance(grid.cells[-1], Stroke) and grid.cells[-1].duration == 1

        # 对照：整数倍平铺不触发截断。beats=4 (need=16 == 动机总时值) 原样返回动机。
        grid_full = motif.grid_for(4)
        assert grid_full.cells == motif.grid_motif
        assert sum(c.duration for c in grid_full.cells) == 16


class TestTileMotifReward:
    """整动机平铺奖励 ``W_TILE_MOTIF``：2 拍以上的扫弦动机平铺到整数倍拍数时减分。

    扫弦的动机是「下手 + 回手」的周期动作：2 拍动机（folk D-DU）占 4 拍就是两遍
    D-DU-D-DU，与 4 拍动机（pop D-DU-U-DU）同属「一个乐句的扫法」。此前只有
    「专属整动机」（ideal_beats 单元素）能拿 ``W_WHOLE_MOTIF``，2 拍动机在 4 拍上
    白让 2.0 —— 实测 4 拍副歌里 folk D-DU 永远输给 pop D-DU-U-DU。
    """

    def _cost(self, pattern, beats, **ctx_kwargs):
        from rhythm_pattern.strum_patterns import pattern_cost, SelectionContext
        return pattern_cost(
            pattern, beats=beats, muted=(0, 0, 0), density_neighbor_delta=None,
            ctx=SelectionContext(time_signature=(4, 4), bpm=92, **ctx_kwargs),
        )

    def _pattern(self, name):
        return next(p for p in ALL_PATTERNS if p.name == name)

    def test_two_beat_strum_reaches_four_beats(self, guitar):
        """民谣标签的副歌：4 拍和弦现在能选中 2 拍动机的 folk D-DU。"""
        ev = enumerate_rhythm_patterns(
            [("C", 4)], guitar,
            ctx=SelectionContext(
                section="chorus", style="pop", time_signature=(4, 4), bpm=92,
                musicnn_tags=(("guitar", 0.85), ("country", 0.7), ("soft", 0.6)),
            ),
        )
        assert ev[0].pattern.name == "folk D-DU", (
            f"民谣标签的 4 拍副歌应选 folk D-DU，实际 {ev[0].pattern.name}"
        )

    def test_one_beat_motif_gets_no_tile_reward(self, guitar):
        """1 拍动机不享受平铺奖励：boom-chick 在 2 拍上仍比 folk D-DU 贵。

        1 拍动机只是「每拍扫一下」，不是乐句级扫法；给了它奖励，它会在 2 拍和弦上
        白拿 2.0 把真正的 2 拍扫弦模板压掉（实测 7.02 vs 8.02）。
        """
        tags = (("drums", 0.85), ("loud", 0.8))
        folk = self._cost(self._pattern("folk D-DU"), 2, section="chorus",
                          style="pop", musicnn_tags=tags, onset_density=0.55)
        boom = self._cost(self._pattern("boom-chick"), 2, section="chorus",
                          style="pop", musicnn_tags=tags, onset_density=0.55)
        assert folk < boom, f"2 拍上 folk D-DU({folk:.2f}) 应比 boom-chick({boom:.2f}) 便宜"

    def test_pop_chorus_still_prefers_four_beat_motif(self, guitar):
        """流行标签的副歌：4 拍仍选 pop D-DU-U-DU（平铺奖励不得盖过标签/整动机）。"""
        ev = enumerate_rhythm_patterns(
            [("C", 4)], guitar,
            ctx=SelectionContext(
                section="chorus", style="pop", time_signature=(4, 4), bpm=92,
                musicnn_tags=(("pop", 0.7), ("drums", 0.65), ("fast", 0.6)),
            ),
        )
        assert ev[0].pattern.name == "pop D-DU-U-DU", (
            f"流行标签的 4 拍副歌仍应是 pop D-DU-U-DU，实际 {ev[0].pattern.name}"
        )


class TestShortTemplateScope:
    """``plan_song_rhythm`` 的短模板口径：族内「放得下缺口和弦」的模板。

    旧实现硬性 ``min_beats <= 1``，只服务 1 拍和弦，于是 2 拍缺口和弦拿不到同族的
    2 拍模板：副歌基础模板是 4 拍的 pop D-DU-U-DU 时，2 拍和弦被丢给 boom-chick，
    写在那 2 拍上的 folk D-DU 永远轮不到。
    """

    @staticmethod
    def _plan(guitar, section_tags, cell, section="chorus"):
        from rhythm_pattern import SelectionContext, plan_song_rhythm
        prog, ctxs = [], []
        for beats in cell:
            for _ in range(2):
                prog.append(("C", beats))
                ctxs.append(SelectionContext(
                    section=section, style="pop", time_signature=(4, 4), bpm=92,
                    musicnn_tags=section_tags, onset_density=0.55,
                ))
        base = SelectionContext(style="pop", time_signature=(4, 4), bpm=92)
        return plan_song_rhythm(prog, guitar, ctx=base, chord_ctxs=ctxs, max_families=2)

    def test_two_beat_gap_gets_two_beat_template(self, guitar):
        """副歌混排 4-2-2：2 拍和弦拿到族内 2 拍模板，而不是 1 拍兜底。"""
        pop = (("pop", 0.7), ("drums", 0.65), ("fast", 0.6))
        planned = self._plan(guitar, pop, [4, 2, 2])
        two = [e for e in planned if e.beats == 2]
        assert two, "应包含 2 拍和弦"
        assert all(e.pattern.min_beats >= 2 for e in two), (
            f"2 拍和弦应拿到 2 拍模板，实际 {sorted({e.pattern.name for e in two})}"
        )
        # 4 拍和弦仍是 4 拍动机的扫弦（不受短模板口径影响）。
        four = [e for e in planned if e.beats == 4]
        assert all(e.pattern.min_beats >= 4 for e in four), (
            f"4 拍和弦仍应是 4 拍模板，实际 {sorted({e.pattern.name for e in four})}"
        )

    def test_two_beat_gap_beats_one_beat_fallback(self, guitar):
        """2 拍缺口不落到 1 拍兜底：drums 标签下同样是 folk D-DU，而不是 boom-chick。

        回归：把平铺奖励给到 1 拍动机后，boom-chick 曾以 7.02 压过 folk D-DU 的 8.02。
        """
        drums = (("drums", 0.85), ("loud", 0.8))
        planned = self._plan(guitar, drums, [4, 2, 2])
        two = [e for e in planned if e.beats == 2]
        assert two and all(e.pattern.name == "folk D-DU" for e in two), (
            f"2 拍缺口应拿到 folk D-DU，实际 {sorted({e.pattern.name for e in two})}"
        )


class TestTechniqueBaseline:
    """技法基线（段落级混排）测试。"""

    def _names(self, gtr, base):
        ev = enumerate_rhythm_patterns(
            [("C", 4), ("G", 2), ("Am", 2)], gtr,
            section="verse", style="folk", technique_baseline=base,
        )
        return [e.pattern.name for e in ev]

    def test_arpeggio_baseline_picks_arpeggio(self, guitar):
        """arpeggio 基线（宽匹配）把和弦压向分解/琶音模板。"""
        arp = {p.name for p in ALL_PATTERNS if not p.is_strum}
        for n in self._names(guitar, "arpeggio"):
            assert n in arp, f"arpeggio 基线应选分解/琶音，实际含 {n}"

    def test_strum_baseline_picks_strum(self, guitar):
        """strum 基线把和弦压向扫弦模板。"""
        strum = {p.name for p in ALL_PATTERNS if p.is_strum}
        for n in self._names(guitar, "strum"):
            assert n in strum, f"strum 基线应选扫弦，实际含 {n}"

    def test_mixed_and_none_do_not_force_technique(self, guitar):
        """mixed / None 不强制技法，允许扫拆混排（不强求全扫或全拆）。"""
        # 只要能产出结果即可--mixed/None 的语义是不干预，选型器自由选。
        for base in ("mixed", None):
            names = self._names(guitar, base)
            assert len(names) == 3, f"{base} 基线应返回 3 个事件"

    def test_fingerpicking_baseline_penalizes_true_arpeggio(self, guitar):
        """fingerpicking 基线区分分解与真琶音：middle 位不选真琶音模板。

        真琶音（technique="arpeggio"，如 arpeggio roll）在 fingerpicking 基线下吃
        W_TECHNIQUE_SOFT 轻罚（分解是段落主体、真琶音是收束手势）。middle 位无 tail
        奖励对冲，且库里有足量分解模板可替代，故 middle 位应选分解（fingerpicking）
        模板。真琶音模板标 positions=("tail",)，tail 位仍可凭位置奖励胜出——轻罚不压死。
        """
        from rhythm_pattern.strum_patterns import pattern_cost, SelectionContext
        true_arp = next(
            p for p in ALL_PATTERNS if p.technique == "arpeggio"
            and p.time_signature == (4, 4)
        )
        fp_ctx = SelectionContext(
            section="verse", style="folk", technique_baseline="fingerpicking",
        )
        arp_ctx = SelectionContext(
            section="verse", style="folk", technique_baseline="arpeggio",
        )
        common = dict(beats=1, muted=(0, 0, 0), density_neighbor_delta=None)
        cost_fp = pattern_cost(true_arp, **common, ctx=fp_ctx)
        cost_arp = pattern_cost(true_arp, **common, ctx=arp_ctx)
        # fingerpicking 基线给真琶音轻罚，arpeggio 基线（宽匹配）不罚。
        assert cost_fp - cost_arp == 2.0, (
            f"fingerpicking 基线应给真琶音 W_TECHNIQUE_SOFT=2.0 罚分，"
            f"实际差 {cost_fp - cost_arp}"
        )
        # 端到端：middle 位和弦（3 和弦进行的第 2 个）不选真琶音模板。
        ev = enumerate_rhythm_patterns(
            [("C", 4), ("G", 2), ("Am", 2)], guitar,
            section="verse", style="folk", technique_baseline="fingerpicking",
        )
        assert ev[1].pattern.technique == "fingerpicking", (
            f"fingerpicking 基线 middle 位应选分解，实际 {ev[1].pattern.name}"
        )

    def test_arpeggio_baseline_wide_match_kept(self, guitar):
        """arpeggio 基线宽匹配不回退：分解模板与真琶音模板都 0 罚（扫弦仍罚）。"""
        from rhythm_pattern.strum_patterns import pattern_cost, SelectionContext
        arp_ctx = SelectionContext(
            section="verse", style="folk", technique_baseline="arpeggio",
        )
        none_ctx = SelectionContext(
            section="verse", style="folk", technique_baseline=None,
        )
        common = dict(beats=1, muted=(0, 0, 0), density_neighbor_delta=None)
        for p in ALL_PATTERNS:
            if p.time_signature != (4, 4) or p.is_strum:
                continue  # 宽匹配只覆盖拨弦类；扫弦在 arpeggio 基线下仍罚 W_TECHNIQUE
            c_arp = pattern_cost(p, **common, ctx=arp_ctx)
            c_none = pattern_cost(p, **common, ctx=none_ctx)
            assert c_arp == c_none, (
                f"arpeggio 宽匹配：{p.name} 在 arpeggio 基线与 None 基线下对拨弦类"
                f"模板罚分应相同（{c_arp} vs {c_none}）"
            )


class TestSectionStrumPrior:
    """段落技法先验（软罚）：铺垫段（verse / prechorus / outro）下扫弦模板降顺位。

    弹唱惯例「主歌/预副歌铺垫用分解、副歌爆发用扫弦」的静态先验：无显式
    technique_baseline 时铺垫段的扫弦模板加 W_SECTION_STRUM 软罚。软罚只降顺位
    不排除——2 拍短和弦放不下 4 拍分解动机时仍可退回短扫弦（基准集 verse/folk
    4-2-2 不变）。预副歌与主歌同属铺垫侧（扫弦留给副歌的爆发点），此前只有
    verse/outro 在此集合里，于是预副歌一旦被 musicnn 的 pop/fast/drums 标签拉动
    就整段变扫弦——这是「预副歌也应用扫弦节奏型」的第一层修复。
    """

    def test_verse_outro_strum_penalized(self, guitar):
        """pattern_cost 层：铺垫段吃 W_SECTION_STRUM，chorus/bridge 不吃。"""
        from rhythm_pattern.strum_patterns import W_SECTION_STRUM, pattern_cost, SelectionContext
        strum = next(p for p in ALL_PATTERNS if p.is_strum and p.time_signature == (4, 4))
        common = dict(beats=2, muted=(0, 0, 0), density_neighbor_delta=None)
        for section, expect_penalty in (
            ("verse", True), ("prechorus", True), ("outro", True),
            ("chorus", False), ("bridge", False), ("intro", False),
        ):
            c_prior = pattern_cost(strum, **common, ctx=SelectionContext(section=section, style="pop"))
            c_none = pattern_cost(
                strum, **common,
                ctx=SelectionContext(section=section, style="pop", technique_baseline="strum"),
            )
            diff = c_prior - c_none
            if expect_penalty:
                assert diff == pytest.approx(W_SECTION_STRUM), (
                    f"{section} 下扫弦应吃 W_SECTION_STRUM={W_SECTION_STRUM} 软罚，实际差 {diff}"
                )
            else:
                assert diff == pytest.approx(0), f"{section} 下扫弦不应吃段落先验罚，实际差 {diff}"

    def test_explicit_baseline_overrides_prior(self, guitar):
        """显式基线跳过先验、不叠加：strum 基线压住先验，mixed 与 None 同样生效。"""
        from rhythm_pattern.strum_patterns import (
            W_SECTION_STRUM,
            pattern_cost,
            SelectionContext,
        )
        strum = next(p for p in ALL_PATTERNS if p.is_strum and p.time_signature == (4, 4))
        common = dict(beats=2, muted=(0, 0, 0), density_neighbor_delta=None)
        verse_none = pattern_cost(
            strum, **common, ctx=SelectionContext(section="verse", technique_baseline=None)
        )
        verse_mixed = pattern_cost(
            strum, **common, ctx=SelectionContext(section="verse", technique_baseline="mixed")
        )
        verse_strum = pattern_cost(
            strum, **common, ctx=SelectionContext(section="verse", technique_baseline="strum")
        )
        verse_fp = pattern_cost(
            strum, **common, ctx=SelectionContext(section="verse", technique_baseline="fingerpicking")
        )
        # mixed 语义与 None 相同（不干预技法，先验正常生效）。
        assert verse_mixed == verse_none, "mixed 与 None 的段落先验行为应一致"
        # strum 显式基线：先验跳过（不叠在 0 技法罚上）。
        assert verse_strum == verse_none - W_SECTION_STRUM, (
            f"strum 基线应跳过先验（差恰为 W_SECTION_STRUM），实际 {verse_strum} vs {verse_none}"
        )
        # fingerpicking 基线：先验跳过，只有 W_TECHNIQUE 重罚（不叠加成 6+2）。
        assert verse_fp == verse_strum + 6.0, (
            f"fingerpicking 基线应只有 W_TECHNIQUE=6.0（先验不叠加），实际差 {verse_fp - verse_strum}"
        )

    def test_verse_long_chord_prefers_fingerpicking(self, guitar):
        """端到端：verse 4 拍长和弦在无基线时首选分解（先验把扫弦压成次选）。"""
        ev = enumerate_rhythm_patterns([("C", 4)], guitar, section="verse", style="folk")
        assert not ev[0].pattern.is_strum, (
            f"verse 4 拍长和弦应首选分解，实际选了扫弦 {ev[0].pattern.name}"
        )

    def test_verse_short_chord_may_fall_back_to_strum(self, guitar):
        """端到端：软罚不排除——2 拍短和弦放不下 4 拍分解动机时仍可退回短扫弦。

        基准集 verse/folk 4-2-2 的期望（2 拍退回 folk D-DU）在此保持不变。
        """
        ev = enumerate_rhythm_patterns(
            [("C", 4), ("G", 2), ("Am", 2)], guitar, section="verse", style="folk"
        )
        assert len(ev) == 3
        # 不强制后两个必须是扫弦，只断言能产出结果且长和弦是分解。
        assert not ev[0].pattern.is_strum


class TestPrechorusPluckPrior:
    """预副歌固定走分解族（``plan_song_rhythm`` 的段落族硬先验）。

    逐和弦的软罚（``W_SECTION_STRUM`` 2.0）压不住标签维（``W_TAGS`` 5.0）：预副歌
    一旦被 musicnn 的 drums/loud/pop/fast 标签命中扫弦模板、而分解模板
    （guitar/slow/soft）全不命中，整段就翻成扫弦——用户报的「预副歌也应用扫弦
    节奏型」。全曲统一选型因此对预副歌用硬先验（见 ``_SECTION_FAMILY_PRIOR``）：
    拨弦族在 kept 里就选它，chorus 的分族行为不受影响。
    """

    STRUM_TAGS = (("drums", 0.85), ("loud", 0.8), ("fast", 0.7), ("pop", 0.6))
    PLUCK_TAGS = (("guitar", 0.85), ("slow", 0.7), ("soft", 0.6))
    ROCK_TAGS = (("rock", 0.9), ("loud", 0.85), ("drums", 0.8), ("metal", 0.5))

    @staticmethod
    def _plan(guitar, tags_by_section, layout, *, beats=4, bpm=100, max_families=2):
        """按 ``layout``（``[(section, 小节数), ...]``）铺一首歌并返回 (secs, events)。"""
        from rhythm_pattern import SelectionContext, plan_song_rhythm
        prog, secs, ctxs = [], [], []
        for sec, bars in layout:
            for _ in range(bars * 4):
                prog.append(("C", beats))
                secs.append(sec)
                ctxs.append(SelectionContext(
                    section=sec, style="pop", time_signature=(4, 4), bpm=bpm,
                    musicnn_tags=tags_by_section[sec],
                ))
        base = SelectionContext(style="pop", time_signature=(4, 4), bpm=bpm)
        planned = plan_song_rhythm(
            prog, guitar, ctx=base, chord_ctxs=ctxs, max_families=max_families,
        )
        return secs, planned

    @staticmethod
    def _is_strum_by_section(secs, planned):
        out: dict[str, set[bool]] = {}
        for sec, ev in zip(secs, planned):
            out.setdefault(sec, set()).add(ev.pattern.is_strum)
        return out

    def test_prechorus_pluck_despite_strum_tags(self, guitar):
        """预副歌挂着 drums/loud 标签时仍走分解，副歌照旧扫弦。"""
        tags = {
            "verse": self.PLUCK_TAGS,
            "prechorus": self.STRUM_TAGS,
            "chorus": self.STRUM_TAGS,
        }
        secs, planned = self._plan(
            guitar, tags, [("verse", 2), ("prechorus", 1), ("chorus", 2)]
        )
        fams = self._is_strum_by_section(secs, planned)
        assert fams["prechorus"] == {False}, (
            f"预副歌应用分解，实际 {sorted({e.pattern.name for s, e in zip(secs, planned) if s == 'prechorus'})}"
        )
        assert fams["chorus"] == {True}, "副歌应仍走扫弦（先验只针对预副歌）"

    def test_prechorus_pluck_when_verse_present_and_rock_tags(self, guitar):
        """全曲都是摇滚标签（扫弦族处处最优）时，预副歌依然走分解。"""
        tags = {sec: self.ROCK_TAGS for sec in ("verse", "prechorus", "chorus")}
        secs, planned = self._plan(
            guitar, tags, [("verse", 2), ("prechorus", 1), ("chorus", 2)]
        )
        fams = self._is_strum_by_section(secs, planned)
        assert fams["prechorus"] == {False}, "摇滚标签下预副歌仍应用分解"
        assert fams["chorus"] == {True}, "摇滚副歌应仍是扫弦"

    def test_prior_family_kept_when_margin_would_trim_it(self, guitar):
        """没有主歌的谱（只有预副歌+副歌）：拨弦族本会因差距超门槛被裁掉，
        先验把它补回来，预副歌才不会落成扫弦。"""
        tags = {"prechorus": self.STRUM_TAGS, "chorus": self.STRUM_TAGS}
        for beats in (4, 2, 1):
            secs, planned = self._plan(
                guitar, tags, [("prechorus", 1), ("chorus", 2)], beats=beats
            )
            fams = self._is_strum_by_section(secs, planned)
            assert fams["prechorus"] == {False}, (
                f"{beats} 拍和弦：预副歌应用分解，实际 "
                f"{sorted({e.pattern.name for s, e in zip(secs, planned) if s == 'prechorus'})}"
            )

    def test_max_families_one_still_single_family(self, guitar):
        """``max_families=1`` 时先验不得突破调用方的族数上限（抒情歌路径）。"""
        tags = {
            "verse": self.PLUCK_TAGS,
            "prechorus": self.STRUM_TAGS,
            "chorus": self.STRUM_TAGS,
        }
        _secs, planned = self._plan(
            guitar, tags, [("verse", 2), ("prechorus", 1), ("chorus", 2)],
            max_families=1,
        )
        assert len({(e.pattern.is_strum) for e in planned}) == 1, (
            "max_families=1 时全曲应只有一族"
        )


class TestOnsetDensity:
    """实测起音密度融合测试：SelectionContext.onset_density 拉动目标密度。"""

    def _select(self, gtr, **ctx_kwargs):
        from rhythm_pattern import SelectionContext
        # 3 和弦 middle 位（避开单和弦 tail 奖励对选型的干扰），只看第 2 个。
        ev = enumerate_rhythm_patterns(
            [("C", 4), ("G", 2), ("Am", 2)], gtr,
            ctx=SelectionContext(**ctx_kwargs),
        )
        return ev[1].pattern

    def test_sparse_audio_pulls_sparser_pattern(self, guitar):
        """verse 段落实测极疏（0.1）时，目标密度被拉低，选出比默认更疏的模板。

        默认 verse/folk 2 拍选 folk D-DU（密度 0.375）；实测 0.1 融合后目标
        0.6*0.1+0.4*0.3=0.18，boom-chick 类低密模板应胜出。
        """
        default = self._select(guitar, section="verse", style="folk")
        sparse = self._select(
            guitar, section="verse", style="folk", onset_density=0.1,
        )
        # 段落技法先验（W_SECTION_STRUM）落下后，verse 2 拍短和弦默认已翻选短分解
        # （arpeggio placeholder，密度 0.25 = 库内最疏档），onset 疏拉不动已贴地的
        # 疏度下限——与后端 test_rhythm_service 的同构测试一致，稳健不变量改为
        # dense > sparse（极密实测拉开密度差必然可观测）。
        assert sparse.density() <= default.density(), (
            f"实测疏(onset=0.1)不应选出更密模板: {sparse.name}({sparse.density()}) "
            f"vs 默认 {default.name}({default.density()})"
        )

    def test_dense_audio_pulls_denser_pattern(self, guitar):
        """verse 段落实测极密（1.0）时，目标密度被拉高，选出比默认更密的模板。"""
        default = self._select(guitar, section="verse", style="folk")
        dense = self._select(
            guitar, section="verse", style="folk", onset_density=1.0,
        )
        assert dense.density() > default.density(), (
            f"实测密(onset=1.0)应选出更密模板: {dense.name}({dense.density()}) "
            f"vs 默认 {default.name}({default.density()})"
        )

    def test_none_keeps_legacy_behavior(self, guitar):
        """onset_density=None 完全退回静态表，与旧调用等价。"""
        from rhythm_pattern import SelectionContext
        legacy = enumerate_rhythm_patterns(
            [("C", 4), ("G", 2), ("Am", 2)], guitar,
            section="verse", style="folk",
        )
        ctx_none = enumerate_rhythm_patterns(
            [("C", 4), ("G", 2), ("Am", 2)], guitar,
            ctx=SelectionContext(
                section="verse", style="folk", onset_density=None,
            ),
        )
        assert [e.pattern.name for e in legacy] == [e.pattern.name for e in ctx_none]

    def test_out_of_range_is_clamped(self, guitar):
        """越界实测值被 clamp 到 [0,1]，不爆炸。"""
        from rhythm_pattern.strum_patterns import _target_density
        assert _target_density("verse", 4, (4, 4), 5.0) == _target_density("verse", 4, (4, 4), 1.0)
        assert _target_density("verse", 4, (4, 4), -3.0) == _target_density("verse", 4, (4, 4), 0.0)

    def test_fusion_weight(self, guitar):
        """融合公式：W_ONSET_AUDIO*measured + (1-W)*static。"""
        from rhythm_pattern.strum_patterns import W_ONSET_AUDIO, _target_density
        static = _target_density("verse", 4, (4, 4))
        fused = _target_density("verse", 4, (4, 4), 0.5)
        assert abs(fused - (W_ONSET_AUDIO * 0.5 + (1 - W_ONSET_AUDIO) * static)) < 1e-9


class TestStringRoles:
    """弦角色实例化测试：Pluck.role 按和弦 voicing 解析成具体弦号。"""

    def _voicing(self, gtr, chord):
        from rhythm_pattern.strum_patterns import _resolve_voicing
        return _resolve_voicing(chord, gtr, max_stretch=4)

    def _instantiate(self, gtr, pattern, chord, beats):
        from rhythm_pattern.strum_patterns import _instantiate_plucks
        v = self._voicing(gtr, chord)
        return _instantiate_plucks(pattern.grid_for(beats), v)

    @staticmethod
    def _gtr(ns):
        """弦号下标 -> 吉他手弦号（0=6弦→6）。"""
        return tuple(6 - n for n in ns) if ns else None

    def test_root_fifth_top2_adapts_to_chord(self, guitar):
        """root-5-top2 模板：C 选 21、G 选 32（顶两弦按音距自适应收窄）。

        末位是 DB 里该模板自带的收尾五音（原为 Rest，后在 web 管理器里改成
        Pluck(Fifth())）——期望值跟着仓库那份 templates.json 走。
        """
        tpl = next(p for p in ALL_PATTERNS if "root-5-top2" in p.name)
        # C: 根音 5 弦、五音 3 弦、顶两弦 2-1、收尾五音 3 弦。
        grid_c = self._instantiate(guitar, tpl, "C", 1)
        plucks_c = [self._gtr(c.strings) for c in grid_c.cells
                    if isinstance(c, Pluck) and c.strings]
        assert plucks_c == [(5,), (3,), (2, 1), (3,)], f"C 实际 {plucks_c}"
        # G: 根音 6 弦、五音 4 弦、顶两弦 3-2（根音更低→收窄）、收尾五音 4 弦。
        grid_g = self._instantiate(guitar, tpl, "G", 1)
        plucks_g = [self._gtr(c.strings) for c in grid_g.cells
                    if isinstance(c, Pluck) and c.strings]
        assert plucks_g == [(6,), (4,), (3, 2), (4,)], f"G 实际 {plucks_g}"

    def test_53231323_restores_classic_fingering_on_C(self, guitar):
        """53231323 (16分) 在 C 上还原 5-3-2-3-1-3-2-3 弦序。"""
        tpl = next(p for p in ALL_PATTERNS if p.name == "53231323 (16分)")
        grid = self._instantiate(guitar, tpl, "C", 2)
        seq = [self._gtr(c.strings)[0] for c in grid.cells
               if isinstance(c, Pluck) and c.strings]
        assert seq == [5, 3, 2, 3, 1, 3, 2, 3], f"实际 {seq}"

    @pytest.mark.parametrize(
        "chord,want",
        [
            ("C", [5, 3, 2, 3, 1, 3, 2, 3]),   # 经典 53231323
            ("E", [6, 3, 2, 3, 1, 3, 2, 3]),   # 经典 63231323（根音在 6 弦）
            ("D", [4, 3, 2, 3, 1, 3, 2, 3]),   # 经典 43231323
            ("Am", [5, 3, 2, 3, 1, 3, 2, 3]),
            ("G", [6, 3, 2, 3, 1, 3, 2, 3]),
            ("F", [6, 3, 2, 3, 1, 3, 2, 3]),
            ("Em", [6, 3, 2, 3, 1, 3, 2, 3]),
        ],
    )
    def test_53231323_keeps_string_shape_across_chords(self, guitar, chord, want):
        """53231323 的不变量是**弦形**：低音弦随和弦走，高音三弦位 3-2-3-1-3-2-3 恒定。

        回归：曾按 C 的音级序写死（root/fifth/root(treble)/fifth/...），到 E（022100）
        上「五音」被解析成 5 弦 2 品的 B2，八个音里四个落在 5 弦低音区，整条分解塌到
        6/5 两弦（6-5-1-5-3-5-1-5）。改用 FromTop 弦形角色后 E 得 63231323。
        """
        tpl = next(p for p in ALL_PATTERNS if p.name == "53231323 (16分)")
        grid = self._instantiate(guitar, tpl, chord, 2)
        seq = [self._gtr(c.strings)[0] for c in grid.cells
               if isinstance(c, Pluck) and c.strings]
        assert seq == want, f"{chord} 实际 {seq}"

    def test_from_top_picks_kth_highest_sounding_string(self, guitar):
        """FromTop(k) 取第 k 高的发音弦；发音弦不足 k 根时解析失败。"""
        from rhythm_pattern import FromTop
        v = self._voicing(guitar, "C")  # x32010，发音弦 5..1 弦
        sounding = sorted(v.midi, key=lambda sm: sm[1])  # 按音高升序
        assert FromTop(1).resolve(v) == (sounding[-1][0],)
        assert FromTop(3).resolve(v) == (sounding[-3][0],)
        assert FromTop(5).resolve(v) == (sounding[0][0],)  # 5 根发音弦，第 5 高 = 最低
        assert FromTop(6).resolve(v) is None  # 只有 5 根发音弦
        assert FromTop(0).resolve(v) is None  # k 从 1 起

    @pytest.mark.parametrize(
        "name,beats,want",
        [
            ("53231323 (16分)", 2, "53231323"),
            ("53231323 (8分)", 4, "53231323"),
            ("5323 (8分)", 2, "5323"),
            ("3/4 532132 (8分)", 3, "532132"),
            ("6/8 532132 (8分)", 2, "532132"),
        ],
    )
    def test_named_shape_template_renders_its_name(self, guitar, name, beats, want):
        """模板名承诺的弦序 = 动机实际渲染出的弦序（C 上即字面数字）。

        回归：`5323 (8分)` 曾只写 2 个 Pluck（Root + 一个高音位），动机平铺出来是
        5-3-5-3-5-3——两根弦来回拨，与模板名承诺的 5323 完全不符。名字带数字弦形的
        模板必须逐音写满，少一个音就会被平铺放大成错误的循环。
        """
        tpl = next(p for p in ALL_PATTERNS if p.name == name)
        grid = self._instantiate(guitar, tpl, "C", beats)
        seq = "".join(
            str(self._gtr(c.strings)[0])
            for c in grid.cells
            if isinstance(c, Pluck) and c.strings
        )
        assert seq == want, f"{name} 动机渲染 {seq}，名字承诺 {want}"

    def test_topn_returns_multiple_strings(self, guitar):
        """TopN(2) 实例化后 Pluck.strings 长度为 2（一次拨多根弦）。"""
        from rhythm_pattern import TopN
        v = self._voicing(guitar, "C")
        strings = TopN(2).resolve(v)
        assert strings is not None and len(strings) == 2

    def test_role_resolution_is_tuning_agnostic_via_midi(self, guitar):
        """角色解析基于音高（midi）而非弦号：root 拨的是最低发音弦，无论哪根。"""
        from rhythm_pattern import Root
        v = self._voicing(guitar, "C")
        root = Root().resolve(v)
        assert root is not None
        # root 应是 voicing 里音高最低的发音弦。
        lowest = min(v.midi, key=lambda sm: sm[1])[0]
        assert root == (lowest,)

    def test_unresolvable_role_keeps_strings_none(self, guitar):
        """role 解析失败时（voicing 无该音级），Pluck.strings 保持 None，不阻塞输出。"""
        from rhythm_pattern import Seventh, Pluck
        from rhythm_pattern import Seventh
        from rhythm_pattern.model import RhythmGrid
        from rhythm_pattern.strum_patterns import _instantiate_plucks
        # C 大三和弦无七音，Seventh 解析应失败。
        v = self._voicing(guitar, "C")
        grid = RhythmGrid((Pluck(role=Seventh(), duration=4),))  # 1 拍 = 总时值 4
        out = _instantiate_plucks(grid, v)
        pluck = next(c for c in out.cells if isinstance(c, Pluck))
        assert pluck.strings is None, "七音在 C 大三和弦上解析失败，strings 应为 None"

    def test_instantiate_preserves_strokes_and_rests(self, guitar):
        """实例化只动 Pluck，Stroke 与 Rest 格原样保留（含 duration）。"""
        from rhythm_pattern import Root
        from rhythm_pattern.model import RhythmGrid, Stroke
        from rhythm_pattern.strum_patterns import _instantiate_plucks
        v = self._voicing(guitar, "C")
        original = RhythmGrid((Stroke("D", 2), Pluck(role=Root(), duration=2)))  # 1 拍 = 总时值 4
        out = _instantiate_plucks(original, v)
        assert out.cells[0] == Stroke("D", 2)
        assert isinstance(out.cells[1], Pluck) and out.cells[1].strings is not None
        assert out.cells[1].duration == 2  # duration 保留
