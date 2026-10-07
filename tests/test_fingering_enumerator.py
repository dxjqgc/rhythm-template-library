"""测试 C 大调常规和弦的指法枚举结果。

覆盖 C 大调的自然音级和弦：C, Dm, Em, F, G, Am, Bdim。
验证经典开放指法、横按检测、冗余大拇指、完整性等核心功能。
"""

import pytest
from chord_fingering import (
    analyze_barre,
    enumerate_fingerings,
    is_redundant_thumb,
    rank_key,
    score_fingering,
    slash_bass_pc,
)
from pytheory import Chord, Fretboard


# ── 共享夹具 ───────────────────────────────────────────────
@pytest.fixture(scope="module")
def guitar() -> Fretboard:
    """标准吉他调弦 (EADGBE)。"""
    return Fretboard.guitar()


# ── C 大调七个自然音级和弦 ────────────────────────────────────
DIATONIC_CHORDS = [
    # (和弦名, 期望根音 midi, 经典开放指法, 预期特性)
    ("C",   0,  (None, 3, 2, 0, 1, 0), "开放原位"),
    ("Dm",  2,  (None, None, 0, 2, 3, 1), "开放原位"),
    ("Em",  4,  (0, 2, 2, 0, 0, 0), "开放原位"),
    ("F",   5,  (1, 3, 3, 2, 1, 1), "横按原位"),
    ("G",   7,  (3, 2, 0, 0, 0, 3), "开放原位"),
    ("Am",  9,  (None, 0, 2, 2, 1, 0), "开放原位"),
    ("Bdim", 11, (None, 2, 3, 4, 3, None), "开放原位"),
]


# ── 测试函数 ─────────────────────────────────────────────
class TestCMajorDiatonicChords:
    """C 大调自然音级和弦基本属性测试。"""

    @pytest.mark.parametrize("symbol, root_midi, classic_positions, tag", DIATONIC_CHORDS)
    def test_classic_fingering_found(
        self, guitar, symbol, root_midi, classic_positions, tag
    ):
        """经典开放指法必须在枚举结果中出现。"""
        all_f = enumerate_fingerings(symbol, guitar, max_fret=7, max_stretch=4)
        found = any(tuple(f.positions) == classic_positions for f in all_f)
        assert found, (
            f"{symbol} ({tag}) 的经典指法 {classic_positions} 未在枚举结果中找到。"
            f" 共 {len(all_f)} 个结果。"
        )

    @pytest.mark.parametrize("symbol, root_midi, _, tag", DIATONIC_CHORDS)
    def test_root_present_in_all_fingerings(
        self, guitar, symbol, root_midi, _, tag
    ):
        """每个指法必须包含根音（require_root=True 默认行为）。"""
        chord = Chord.from_symbol(symbol)
        root_pc = chord.root.midi % 12
        all_f = enumerate_fingerings(symbol, guitar, max_fret=7, max_stretch=4)
        assert len(all_f) > 0, f"{symbol} ({tag}) 应至少有一个指法。"
        for f in all_f:
            present_pcs = {t.midi % 12 for t in f.tones}
            assert root_pc in present_pcs, (
                f"{symbol} ({tag}) 指法 {f} 缺少根音 (pc={root_pc})。"
            )

    @pytest.mark.parametrize("symbol, root_midi, _, tag", DIATONIC_CHORDS)
    def test_chord_completeness(self, guitar, symbol, root_midi, _, tag):
        """strict 模式下每个指法必须覆盖所有和弦音级。"""
        chord = Chord.from_symbol(symbol)
        target_pcs = chord.pitch_classes
        all_f = enumerate_fingerings(symbol, guitar, max_fret=7, max_stretch=4)
        for f in all_f:
            present_pcs = {t.midi % 12 for t in f.tones}
            missing = target_pcs - present_pcs
            assert not missing, (
                f"{symbol} ({tag}) 指法 {f} 缺少音级 {missing}。"
                f" 需要 {target_pcs}，实际 {present_pcs}。"
            )

    @pytest.mark.parametrize("symbol, root_midi, _, tag", DIATONIC_CHORDS)
    def test_fingerings_sorted_by_rank(self, guitar, symbol, root_midi, _, tag):
        """legacy 排序下指法按 rank_key 升序排列（原位>转位、把位低>高）。

        ``enumerate_fingerings`` 默认 ``ranking="playable"``（按可演奏性代价排序），
        不保证 ``rank_key`` 单调；这里显式切到 ``"legacy"`` 验证 ``rank_key`` 分层排序。
        """
        chord = Chord.from_symbol(symbol)
        root_pc = chord.root.midi % 12
        all_f = enumerate_fingerings(
            symbol, guitar, max_fret=7, max_stretch=4, ranking="legacy"
        )
        if len(all_f) < 2:
            pytest.skip(f"{symbol} ({tag}) 只有 {len(all_f)} 个指法，跳过排序测试。")
        for i in range(len(all_f) - 1):
            r1 = rank_key(all_f[i], root_pc)
            r2 = rank_key(all_f[i + 1], root_pc)
            assert r1 <= r2, (
                f"{symbol} ({tag}) 排序错误: 索引 {i} 的 rank {r1} > 索引 {i+1} 的 rank {r2}。"
                f"\n  指法 {i}: {all_f[i]}"
                f"\n  指法 {i+1}: {all_f[i+1]}"
            )

    @pytest.mark.parametrize("symbol, root_midi, _, tag", DIATONIC_CHORDS)
    def test_no_redundant_thumb(self, guitar, symbol, root_midi, _, tag):
        """C 大调开放和弦中不应出现冗余大拇指按法（标准调弦、max_fret=7 范围内）。"""
        chord = Chord.from_symbol(symbol)
        open_tones = guitar.tones
        all_f = enumerate_fingerings(symbol, guitar, max_fret=7, max_stretch=4)
        for f in all_f:
            redundant = is_redundant_thumb(f.positions, chord.pitch_classes, open_tones)
            assert not redundant, (
                f"{symbol} ({tag}) 出现冗余大拇指按法: {f}。"
            )

    @pytest.mark.parametrize("symbol, root_midi, _, tag", DIATONIC_CHORDS)
    def test_max_stretch_respected(self, guitar, symbol, root_midi, _, tag):
        """所有指法跨度不超过 max_stretch。"""
        all_f = enumerate_fingerings(symbol, guitar, max_fret=7, max_stretch=4)
        for f in all_f:
            fretted = [p for p in f.positions if p is not None]
            if fretted:
                span = max(fretted) - min(fretted)
                assert span <= 4, (
                    f"{symbol} ({tag}) 指法 {f} 跨度 {span} > max_stretch=4。"
                )


    @pytest.mark.parametrize("symbol, root_midi, _, tag", DIATONIC_CHORDS)
    def test_identify_not_none(self, guitar, symbol, root_midi, _, tag):
        """strict 模式下所有指法 identify() 应返回非空。"""
        all_f = enumerate_fingerings(symbol, guitar, max_fret=7, max_stretch=4)
        for f in all_f:
            ident = f.identify()
            assert ident is not None, f"{symbol} ({tag}) 指法 {f} identify() 返回 None。"
            assert "power" not in ident, (
                f"{symbol} ({tag}) 指法 {f} 被识别为 power chord: {ident}。"
            )


    def test_open_strings_preferred(self, guitar):
        """开放指法（含空弦 0 品）应排在相近把位的高把位指法之前。"""
        all_f = enumerate_fingerings("C", guitar, max_fret=7, max_stretch=4)
        # 第一个指法应包含空弦（经典 x32010 含 D=0, G=0, e=0）
        first = all_f[0]
        has_open = 0 in first.positions
        assert has_open, f"C 大调和弦首位应为开放指法，实际: {first}"


class TestFBarreChord:
    """F 大和弦横按专项测试。"""

    def test_f_barre_detected(self, guitar):
        """F (133211) 应检测为 1 品横按，指头数 4。"""
        f_fing = guitar.fingering(1, 3, 3, 2, 1, 1)
        n_fingers, barre_fret = analyze_barre(f_fing.positions)
        assert n_fingers == 4, f"F 横按指头数应为 4，实际 {n_fingers}"
        assert barre_fret == 1, f"F 横按品应为 1，实际 {barre_fret}"

    def test_f_barre_in_enumeration(self, guitar):
        """F 横按指法应在枚举结果中且识别为横按。"""
        all_f = enumerate_fingerings("F", guitar, max_fret=7, max_stretch=4)
        barre_fs = []
        for f in all_f:
            _, barre = analyze_barre(f.positions)
            if barre is not None:
                barre_fs.append(f)
        assert len(barre_fs) > 0, "F 和弦至少应有一个横按指法。"
        # 经典横按 (133211) 应在横按结果中
        classic = guitar.fingering(1, 3, 3, 2, 1, 1)
        found = any(tuple(f.positions) == tuple(classic.positions) for f in barre_fs)
        assert found, f"经典 F 横按 (133211) 应在横按结果中。"


    def test_f_root_position_ranked_first(self, guitar):
        """F 和弦原位指法（最低音为根音 F）应排在转位之前。"""
        all_f = enumerate_fingerings("F", guitar, max_fret=7, max_stretch=4)
        root_pc = 5  # F
        # 找到第一个转位指法，验证它之前所有指法都是原位
        first_inversion_idx = None
        for i, f in enumerate(all_f):
            if f.tones:
                lowest = min(f.tones, key=lambda t: t.midi)
                if lowest.midi % 12 != root_pc:
                    first_inversion_idx = i
                    break
        if first_inversion_idx is not None:
            for i in range(first_inversion_idx):
                lowest = min(all_f[i].tones, key=lambda t: t.midi)
                assert lowest.midi % 12 == root_pc, (
                    f"索引 {i} 的指法 {all_f[i]} 应为原位，但在第一个转位之前。"
                )


class TestARedundantThumb:
    """A 大和弦冗余大拇指专项测试。"""

    def test_thumb_fingering_excluded(self, guitar):
        """(5,0,2,2,2,0) 不应出现在枚举结果中。"""
        all_f = enumerate_fingerings("A", guitar, max_fret=7, max_stretch=4)
        positions_list = [tuple(f.positions) for f in all_f]
        assert (5, 0, 2, 2, 2, 0) not in positions_list, (
            "冗余大拇指指法 (5,0,2,2,2,0) 不应出现在 A 和弦枚举结果中。"
        )

    def test_classic_open_a_present(self, guitar):
        """经典开放 A (x02220) 应在结果中。"""
        all_f = enumerate_fingerings("A", guitar, max_fret=7, max_stretch=4)
        found = any(tuple(f.positions) == (None, 0, 2, 2, 2, 0) for f in all_f)
        assert found, "经典开放 A 指法 x02220 应在枚举结果中。"

    def test_is_redundant_thumb_logic(self, guitar):
        """直接测试 is_redundant_thumb 的判断逻辑。"""
        chord = Chord.from_symbol("A")
        open_tones = guitar.tones
        thumb_fing = guitar.fingering(5, 0, 2, 2, 2, 0)
        std_fing = guitar.fingering(None, 0, 2, 2, 2, 0)
        assert is_redundant_thumb(thumb_fing.positions, chord.pitch_classes, open_tones), (
            "(5,0,2,2,2,0) 应判定为冗余大拇指。"
        )
        assert not is_redundant_thumb(std_fing.positions, chord.pitch_classes, open_tones), (
            "x02220 不应判定为冗余大拇指。"
        )


class TestBdiminished:
    """Bdim 减三和弦特殊测试。"""

    def test_bdim_found(self, guitar):
        """Bdim 和弦应有至少一个指法。"""
        all_f = enumerate_fingerings("Bdim", guitar, max_fret=7, max_stretch=4)
        assert len(all_f) >0, "Bdim 和弦应至少有一个指法。"

    def test_bdim_contains_diminished_fifth(self, guitar):
        """Bdim 指法应包含减五度音 (F, pc=5)。"""
        chord = Chord.from_symbol("Bdim")
        # Bdim = B D F，减五度是 F (pc=5)
        assert 5 in chord.pitch_classes, "Bdim 应包含 pc=5 (F)。"
        all_f = enumerate_fingerings("Bdim", guitar, max_fret=7, max_stretch=4)
        for f in all_f:
            present_pcs = {t.midi % 12 for t in f.tones}
            assert 5 in present_pcs, f"Bdim 指法 {f} 缺少减五度 F (pc=5)。"


class TestScoreFingering:
    """score_fingering 评分函数测试。"""

    def test_open_chord_ranks_before_barre(self, guitar):
        """开放 C 指法 (x32010) 排序应在同和弦的高把位指法之前。

        rank_key 分层排序中"最低把位优先"是第 2 层，保证开放指法排在高把位之前。
        score_fingering 是线性加权评分，不做分层排序，因此不直接用于最终排序。
        """
        open_c = guitar.fingering(None, 3, 2, 0, 1, 0)
        barre_c = guitar.fingering(8, 10, 10, 9, 8, 8)  # 8 品横按 C
        rank_open = rank_key(open_c, root_pc=0)
        rank_barre = rank_key(barre_c, root_pc=0)
        assert rank_open < rank_barre, (
            f"开放 C rank ({rank_open}) 应小于横按 C rank ({rank_barre})。"
            f" 开放指法: {open_c}，横按指法: {barre_c}"
        )

    def test_root_position_bonus(self, guitar):
        """原位指法评分应高于同指法不加 root_pc 时的评分。"""
        open_c = guitar.fingering(None, 3, 2, 0, 1, 0)
        score_with_root = score_fingering(open_c, root_pc=0)
        score_without_root = score_fingering(open_c)
        assert score_with_root > score_without_root, (
            f"传入 root_pc 应增加原位加分: {score_with_root} vs {score_without_root}。"
        )


# ── 斜杠转位：低音硬约束 ──────────────────────────────────────
# pytheory 不把斜杠低音当约束（`G/B` 与 `G` 的音级集合完全相同，`root` 仍是 G），
# 且 playability_cost 因「最低音不是根音」给转位形态加 W_INVERSION 罚分 —— 若不
# 自己施加约束，`G/B` 排名第一必然低音为 G 的原位指法（谱面标 G/B、听起来是 G）。
SLASH_CASES = [
    # (斜杠符号, 低音音高类)
    ("G/B", 11),
    ("C/E", 4),
    ("D/F#", 6),
    ("Am/G", 7),
    ("Am/C", 0),
]


class TestSlashBass:
    """斜杠转位（`"G/B"`）的低音约束。"""

    @pytest.mark.parametrize("symbol, bass_pc", SLASH_CASES)
    def test_lowest_sounding_note_is_the_slash_bass(self, guitar, symbol, bass_pc):
        """约束后：每个结果的最低发音音就是斜杠低音。"""
        results = enumerate_fingerings(symbol, guitar, max_fret=7, max_stretch=4, limit=3)
        assert results, f"{symbol} 应有转位指法。"
        for f in results:
            lowest_pc = min(t.midi for t in f.tones) % 12
            assert lowest_pc == bass_pc, (
                f"{symbol} 指法 {tuple(f.positions)} 最低音 pc={lowest_pc}，期望 {bass_pc}。"
            )

    def test_bass_tone_slash_does_not_mirror_root_position(self, guitar):
        """核心回归：G/B 不再等于原位 G（旧行为两者逐位相同）。"""
        plain = enumerate_fingerings("G", guitar, max_fret=7, max_stretch=4, limit=1)
        slash = enumerate_fingerings("G/B", guitar, max_fret=7, max_stretch=4, limit=1)
        assert tuple(slash[0].positions) != tuple(plain[0].positions)
        assert tuple(plain[0].positions) == (3, 2, 0, 0, 0, 3), "原位 G 不受影响。"

    def test_limit_applies_after_the_bass_constraint(self, guitar):
        """limit=1 也必须拿到转位形态（约束在排序后、截断前施加）。"""
        top = enumerate_fingerings("G/B", guitar, max_fret=7, max_stretch=4, limit=1)
        assert min(t.midi for t in top[0].tones) % 12 == 11

    def test_plain_symbols_unchanged(self, guitar):
        """无斜杠标签行为逐位不变（教材基准指法仍排第一）。"""
        expected = {
            "G": (3, 2, 0, 0, 0, 3),
            "C": (None, 3, 2, 0, 1, 0),
            "F": (1, 3, 3, 2, 1, 1),
            "Am": (None, 0, 2, 2, 1, 0),
        }
        for symbol, want in expected.items():
            top = enumerate_fingerings(symbol, guitar, max_fret=7, max_stretch=4, limit=1)
            assert tuple(top[0].positions) == want, f"{symbol} 首选指法变了。"

    def test_falls_back_when_no_bass_matching_shape_exists(self, guitar):
        """该低音确实没有可行 voicing 时退回完整列表（宁可不动，也不返回空）。"""
        constrained = enumerate_fingerings("G/B", guitar, max_fret=1, max_stretch=4)
        unconstrained = enumerate_fingerings(
            Chord.from_symbol("G/B"), guitar, max_fret=1, max_stretch=4
        )
        assert constrained, "约束不该把结果清空。"
        assert [tuple(f.positions) for f in constrained] == [
            tuple(f.positions) for f in unconstrained
        ]

    def test_chord_object_has_no_bass_constraint(self, guitar):
        """传 Chord 对象时低音已被 pytheory 丢掉，退回无约束行为。"""
        by_object = enumerate_fingerings(
            Chord.from_symbol("G/B"), guitar, max_fret=7, max_stretch=4, limit=1
        )
        by_plain = enumerate_fingerings("G", guitar, max_fret=7, max_stretch=4, limit=1)
        assert tuple(by_object[0].positions) == tuple(by_plain[0].positions)


class TestSlashBassParsing:
    """`slash_bass_pc` 解析（colon / compact / 带修饰的写法）。"""

    @pytest.mark.parametrize(
        "symbol, want",
        [
            ("G/B", 11),
            ("Gmaj/B", 11),
            ("G:maj/Bb", 10),
            ("C/E", 4),
            ("D/F#", 6),
            ("Am/G", 7),
            ("G/B7", 11),      # 低音带修饰：只取音名
            ("G", None),       # 无斜杠
            ("Am", None),
            ("N.C.", None),
            ("G/", None),      # 空低音
        ],
    )
    def test_parsing(self, symbol, want):
        assert slash_bass_pc(symbol) == want