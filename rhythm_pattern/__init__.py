"""弹唱节奏型自动选型（扫弦 + 分解）。

给定一段和弦进行（每个和弦占几拍）与段落/风格，为每个和弦选出一个合适的节奏型，
输出 16 分音符栅格 + 右手动作序列：扫弦为 ``D`` 下扫 / ``U`` 上扫，分解为 ``Pluck``
拨弦（带按和弦 voicing 实例化后的具体弦号）。

选型把「拍数门槛」「段落契合」「目标密度」「技法基线」「整动机奖励」「扫弦时闷弦结构」
「进行级连贯性」折算成同一个尺度上的连续代价，越小越靠前--思路与 ``chord_fingering``
的 ``playability_cost`` 同构。分解模板用弦角色（:mod:`string_role`）表达弦序，选型时
按当前和弦首选 voicing 实例化成具体弦号，故对任意调弦中立。

公开 API::

    from rhythm_pattern import (
        enumerate_rhythm_patterns, StrumPattern, RhythmGrid, Pluck, Stroke,
        Root, Fifth, TopN,  # 弦角色
    )
"""

from .model import Cell, Pluck, RhythmEvent, RhythmGrid, Stroke, StrumPattern
from .strum_patterns import STRUM_PATTERNS, enumerate_rhythm_patterns, pattern_cost
from .string_role import (
    All,
    Fifth,
    Root,
    Seventh,
    StringRole,
    Third,
    TopN,
    VoicingData,
    voicing_from_fingering,
)

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
    # 弦角色（分解模板的弦序表达，按 voicing 实例化）
    "StringRole",
    "Root",
    "Third",
    "Fifth",
    "Seventh",
    "TopN",
    "All",
    "VoicingData",
    "voicing_from_fingering",
]
