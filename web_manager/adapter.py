"""web 管理器与 rhythm_pattern 的唯一桥接层。

只导入 rhythm_pattern 的公开符号，绝不触及 ``_`` 前缀私有内部，保证 rhythm_pattern
模块后续集成到别的项目时，本 web 管理器可作为可丢弃的独立组件。

职责：
- :class:`DbPatternSource` —— 数据库源的 :class:`PatternSource` 实现，供选型器注入。
- :func:`pattern_to_notelist` —— 把「模板 + 和弦 + 拍数 + BPM」展开成浏览器可合成的
  音符列表 JSON（按 16 分 tick 计时），是试听的核心数据契约。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from rhythm_pattern import (
    PatternSource,
    StrumPattern,
    TemplateRepository,
    dict_to_pattern,
    instantiate_pattern,
    pattern_to_dict,
    resolve_voicing,
    set_pattern_source,
)
from rhythm_pattern.model import RhythmEvent

if TYPE_CHECKING:
    from pytheory import Fretboard


class DbPatternSource:
    """数据库源的 :class:`PatternSource` 实现。

    ``patterns()`` 从仓库加载全部模板，每条经 ``dict_to_pattern`` 构造校验。仓库
    读到的非法记录会在 load 阶段抛错，不会进选型器。
    """

    def __init__(self, repo: TemplateRepository) -> None:
        self._repo = repo

    def patterns(self) -> list[StrumPattern]:
        return [p for _id, p in self._repo.load()]


def install_db_source(repo: TemplateRepository) -> None:
    """注入数据库源到 rhythm_pattern 选型器（进程级全局）。"""
    set_pattern_source(DbPatternSource(repo))


# ── 音符列表提取 ──────────────────────────────────────────────────────


def _voicing_midi_map(voicing: object) -> dict[int, int]:
    """``voicing.midi`` = ((弦号, midi), ...) → {弦号: midi} 映射。"""
    return {s: m for s, m in voicing.midi}  # type: ignore[attr-defined]


def pattern_to_notelist(
    pattern: StrumPattern,
    chord: str,
    fretboard: "Fretboard",
    beats: int,
    bpm: int = 90,
    *,
    max_stretch: int = 4,
) -> dict[str, Any]:
    """把模板在某和弦上实例化，展开成浏览器 Web Audio 可合成的音符列表。

    流程：
    1. ``resolve_voicing`` 取该和弦首选指法的 voicing（弦号→midi 映射）。
    2. ``instantiate_pattern`` 把模板平铺到 ``beats`` 拍、实例化 Pluck 弦号。
    3. 走 ``event.fingering``（已有正确时值模型：发音 duration=到下个发音距离，
       rest=发音前真静默），按 16 分 tick 游标展开成音符。

    扫弦动作（stroke_down/up）发出 voicing 全部发音弦的音；拨弦动作（pluck）发出
    ``strings`` 对应弦的 midi（解析失败 strings=None 则跳过该拨弦）；rest 不发音。

    返回::

        {
          "bpm": 90, "ticks_per_beat": 4,
          "notes": [{"midi":48,"start_tick":0,"duration_tick":4,"velocity":80}, ...],
          "total_tick": 16,                       # = 4 * beats
          "grid":  [{"tick":0,"kind":"stroke_down"}, {"tick":4,"kind":"pluck","strings":[3,2]}, ...]
        }

    浏览器换算 ``秒 = tick * 60 / bpm / 4``。``total_tick`` 为 0（voicing 解析失败）
    时 ``notes`` 为空，浏览器静默。
    """
    voicing = resolve_voicing(chord, fretboard, max_stretch)
    event: RhythmEvent = instantiate_pattern(
        pattern, chord, fretboard, beats, max_stretch=max_stretch
    )
    total_tick = 4 * beats
    notes: list[dict[str, Any]] = []
    grid: list[dict[str, Any]] = []

    if voicing is None:
        # 无 voicing：扫弦/拨弦都无音高，仍给空 grid 占位。
        return {"bpm": bpm, "ticks_per_beat": 4, "notes": [], "total_tick": total_tick, "grid": []}

    string_to_midi = _voicing_midi_map(voicing)
    # 扫弦发出全部发音弦的音；预计算一次。
    all_voicing_midis = [m for _s, m in voicing.midi]  # type: ignore[attr-defined]

    cursor = 0  # 16 分 tick 游标
    for action in event.fingering:
        dur = action.duration
        if action.kind == "rest":
            cursor += dur
            continue
        if action.kind in ("stroke_down", "stroke_up"):
            midis = all_voicing_midis
            kind = "stroke_down" if action.kind == "stroke_down" else "stroke_up"
            grid.append({"tick": cursor, "kind": kind})
        elif action.kind == "pluck":
            strings = action.strings or ()
            # notes 与 grid 保持一致：只记录能映射到 midi 的弦（拨到闷音弦时 grid 与 notes 同步省略）。
            kept = [s for s in strings if s in string_to_midi]
            midis = [string_to_midi[s] for s in kept]
            grid.append(
                {"tick": cursor, "kind": "pluck", "strings": list(kept)}
            )
        else:
            cursor += dur
            continue
        for m in midis:
            notes.append(
                {
                    "midi": m,
                    "start_tick": cursor,
                    "duration_tick": dur,
                    "velocity": 80,
                }
            )
        cursor += dur

    return {
        "bpm": bpm,
        "ticks_per_beat": 4,
        "notes": notes,
        "total_tick": total_tick,
        "grid": grid,
    }


def template_dict_to_pattern(d: dict[str, object]) -> StrumPattern:
    """从 dict 构造模板（带校验）；供 server 在存库/预览前调用。"""
    return dict_to_pattern(d)


def template_to_dict(pattern: StrumPattern) -> dict[str, object]:
    """模板 → dict（不含 id）。"""
    return pattern_to_dict(pattern)
