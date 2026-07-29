"""测试单模板实例化公开 helper + note-list 提取（试听核心数据路径）。"""

import pytest
from pytheory import Fretboard

from rhythm_pattern import (
    STRUM_PATTERNS,
    instantiate_pattern,
    resolve_voicing,
    set_pattern_source,
)
from rhythm_pattern.model import Pluck, Stroke
from rhythm_pattern.serialization import TemplateRepository, seed_from_hardcoded
from web_manager.adapter import DbPatternSource, pattern_to_notelist


@pytest.fixture(scope="module")
def guitar() -> Fretboard:
    return Fretboard.guitar()


def _arpeggio_pattern():
    """取一个分解模板（root-5-top2）。"""
    return next(p for p in STRUM_PATTERNS if p.name == "root-5-top2 (1拍)")


# ── instantiate_pattern ─────────────────────────────────────────────


def test_instantiate_strum_returns_events(guitar):
    p = next(p for p in STRUM_PATTERNS if p.name == "boom-chick")
    ev = instantiate_pattern(p, "C", guitar, 4)
    # 含 stroke 动作；fingering duration 之和 = 4*beats。
    kinds = {a.kind for a in ev.fingering}
    assert "stroke_down" in kinds
    assert sum(a.duration for a in ev.fingering) == 4 * 4


def test_instantiate_arpeggio_resolves_strings(guitar):
    """分解模板在 G 上 Pluck.strings 非 None，且与 C 不同（角色实例化生效）。"""
    p = _arpeggio_pattern()
    ev_c = instantiate_pattern(p, "C", guitar, 4)
    ev_g = instantiate_pattern(p, "G", guitar, 4)
    plucks_c = [c for c in ev_c.grid.cells if isinstance(c, Pluck)]
    plucks_g = [c for c in ev_g.grid.cells if isinstance(c, Pluck)]
    assert all(c.strings is not None for c in plucks_g)
    strings_c = [c.strings for c in plucks_c]
    strings_g = [c.strings for c in plucks_g]
    assert strings_c != strings_g  # TopN 在 C/G 解析不同弦组


def test_resolve_voicing_returns_midi_map(guitar):
    v = resolve_voicing("C", guitar)
    assert v is not None
    assert len(v.midi) >= 3  # 三和弦至少 3 发音弦
    # midi 元组是 (弦号, midi)。
    for s, m in v.midi:
        assert isinstance(s, int) and isinstance(m, int) and 0 <= s <= 5


# ── note-list 提取 ────────────────────────────────────────────────────


def test_notelist_strum_shape(guitar):
    p = next(p for p in STRUM_PATTERNS if p.name == "boom-chick")
    nl = pattern_to_notelist(p, "C", guitar, 4, bpm=90)
    assert nl["total_tick"] == 16
    assert nl["ticks_per_beat"] == 4
    assert nl["bpm"] == 90
    # 每个 note 的字段齐全。
    for n in nl["notes"]:
        assert {"midi", "start_tick", "duration_tick", "velocity"} <= set(n)
        assert 0 <= n["start_tick"] < 16
    # 扫弦格都落在整数拍（tick 0/4/8/12）。
    grid_ticks = {g["tick"] for g in nl["grid"]}
    assert grid_ticks <= {0, 4, 8, 12}


def test_notelist_arpeggio_has_pluck_strings(guitar):
    p = _arpeggio_pattern()
    nl = pattern_to_notelist(p, "C", guitar, 4, bpm=90)
    # grid 里有 pluck 动作且 strings 非空。
    plucks = [g for g in nl["grid"] if g["kind"] == "pluck"]
    assert plucks
    assert any(g["strings"] for g in plucks)
    # 音符数 >= pluck 动作数（每个 pluck 至少一音，平铺后多次出现）。
    assert len(nl["notes"]) >= len(plucks)


def test_db_source_injection_makes_selection_use_db(guitar, tmp_path):
    """注入 DB 源后，enumerate/arrange 读到的模板集与 DB 一致。"""
    path = seed_from_hardcoded(tmp_path / "db.json")
    repo = TemplateRepository(path)
    src = DbPatternSource(repo)
    set_pattern_source(src)
    try:
        assert len(src.patterns()) == len(STRUM_PATTERNS)
    finally:
        set_pattern_source(None)  # 重置回硬编码默认，避免污染其他测试


def test_notelist_unresolvable_chord_raises(guitar):
    """pytheory 对非法和弦符号直接抛 ValueError（server 层会兜底返回 400）。

    这里只断言底层会抛，确认 server 的 try/except 是必要的。
    """
    p = next(p for p in STRUM_PATTERNS if p.name == "boom-chick")
    with pytest.raises(ValueError):
        pattern_to_notelist(p, "Z#bogus", guitar, 4)


def test_boom_chick_fallback_never_crashes():
    """boom-chick 被数据源和硬编码列表同时删除时，内联兜底仍返回有效模板，绝不抛 StopIteration。"""
    from rhythm_pattern.strum_patterns import _boom_chick_fallback

    class _EmptySource:
        def patterns(self):
            return []

    # 临时把硬编码 STRUM_PATTERNS 也清空（再恢复），模拟极端场景。
    from rhythm_pattern import strum_patterns as sp
    saved = list(sp.STRUM_PATTERNS)
    sp.STRUM_PATTERNS.clear()
    set_pattern_source(_EmptySource())
    try:
        fb = _boom_chick_fallback()
        assert fb.name == "boom-chick"
        assert fb.min_beats == 1  # 可作 fallback（min_beats=1）
        assert len(fb.grid_motif) == 4
    finally:
        sp.STRUM_PATTERNS[:] = saved
        set_pattern_source(None)  # 重置默认源
