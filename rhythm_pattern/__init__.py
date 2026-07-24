"""弹唱扫弦节奏型自动选型。

给定一段和弦进行（每个和弦占几拍）与段落/风格，为每个和弦选出一个合适的扫弦
节奏型，输出 16 分音符栅格 + 右手动作序列（``D`` 下扫 / ``U`` 上扫 / 休止）。

选型把「拍数门槛」「段落契合」「目标密度」「扫弦时闷弦结构」「进行级连贯性」
折算成同一个尺度上的连续代价，越小越靠前--思路与 ``chord_fingering`` 的
``playability_cost`` 同构。扫弦不挑单弦，故右手可行性判断很轻，仅复用
``chord_fingering.count_muted`` 取首选 voicing 的闷弦结构。

公开 API::

    from rhythm_pattern import enumerate_rhythm_patterns, StrumPattern, RhythmGrid
"""

from .model import Cell, Pluck, RhythmEvent, RhythmGrid, Stroke, StrumPattern
from .strum_patterns import STRUM_PATTERNS, enumerate_rhythm_patterns, pattern_cost

__all__ = [
    "Stroke",
    "Pluck",
    "Cell",
    "RhythmGrid",
    "StrumPattern",
    "RhythmEvent",
    "STRUM_PATTERNS",
    "enumerate_rhythm_patterns",
    "pattern_cost",
]
