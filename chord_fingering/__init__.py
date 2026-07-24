"""指板和弦指法枚举与可演奏性评分。

给定一个和弦与任意调弦的指板，枚举指板上所有能弹出该和弦的指法，并按
可演奏性排序。底层音高计算依赖 ``pytheory`` 的 ``Tone.transpose``，
与调弦无关，因此对标准调弦、开放调弦、尤克里里及完全自定义调弦均成立。

公开 API::

    from chord_fingering import enumerate_fingerings, plan_fingers
"""

from .fingering_enumerator import (
    analyze_barre,
    enumerate_fingerings,
    is_redundant_thumb,
    rank_key,
    score_fingering,
)
from .playability import (
    FingerPlan,
    count_muted,
    plan_fingers,
    playability_cost,
    required_pitch_classes,
)

__all__ = [
    "analyze_barre",
    "enumerate_fingerings",
    "is_redundant_thumb",
    "rank_key",
    "score_fingering",
    "FingerPlan",
    "count_muted",
    "plan_fingers",
    "playability_cost",
    "required_pitch_classes",
]
