"""弹唱节奏型自动选型与整段编排（扫弦 + 分解）。

两种入口：

- :func:`enumerate_rhythm_patterns` - 逐和弦贪心选型（取每个和弦第 1 名），适合单点查询。
- :func:`arrange_progression` - **整段编排**，Top-K 候选 + DP 选路径，输出连贯不割裂的
  节奏型序列，并按和弦在段落中的位置（首/中/尾）自动应用位置维度（尾和弦收束处理）。

给定一段和弦进行（每个和弦占几拍）与段落/风格，为每个和弦选出一个合适的节奏型，
输出 16 分音符栅格 + 指法动作序列（:func:`fingering_sequence`）：扫弦为下扫/上扫、
分解为 ``Pluck`` 拨弦（带按和弦 voicing 实例化后的具体弦号）。

选型把「拍数门槛」「段落契合」「目标密度」「技法基线」「整动机奖励」「扫弦时闷弦结构」
「拍号」「BPM」「位置」「进行级连贯性」折算成同一个尺度上的连续代价，越小越靠前--思路与
``chord_fingering`` 的 ``playability_cost`` 同构。分解模板用弦角色（:mod:`string_role`）
表达弦序，选型时按当前和弦首选 voicing 实例化成具体弦号，故对任意调弦中立。

公开 API::

    from rhythm_pattern import (
        enumerate_rhythm_patterns, arrange_progression,
        SelectionContext, StrumPattern, RhythmGrid, Pluck, Stroke,
        FingeringAction, fingering_sequence,
        Root, Fifth, TopN,  # 弦角色
    )
"""

from .model import (
    Accent,
    Cell,
    FingeringAction,
    Pluck,
    Position,
    Rest,
    RhythmEvent,
    RhythmGrid,
    Stroke,
    StrumPattern,
    fingering_sequence,
)
from .strum_patterns import (
    STRUM_PATTERNS,
    PatternSource,
    SelectionContext,
    arrange_progression,
    enumerate_rhythm_patterns,
    get_pattern_source,
    instantiate_pattern,
    pattern_cost,
    resolve_voicing,
    set_pattern_source,
    to_json,
)
from .serialization import (
    TemplateRepository,
    dict_to_pattern,
    pattern_to_dict,
    seed_from_hardcoded,
)
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
    "FingeringAction",
    "fingering_sequence",
    "Position",
    "STRUM_PATTERNS",
    "enumerate_rhythm_patterns",
    "arrange_progression",
    "pattern_cost",
    "SelectionContext",
    "to_json",
    # 数据源 seam（可注入，默认硬编码 STRUM_PATTERNS）
    "PatternSource",
    "set_pattern_source",
    "get_pattern_source",
    # 单模板实例化公开 helper（试听等）
    "instantiate_pattern",
    "resolve_voicing",
    # 序列化 + 文本数据库仓库
    "pattern_to_dict",
    "dict_to_pattern",
    "TemplateRepository",
    "seed_from_hardcoded",
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
