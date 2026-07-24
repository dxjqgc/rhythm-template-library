"""扫弦节奏型模板库 + 选型器。

模板库是一个普通列表常量 ``STRUM_PATTERNS``，每加一个模板往表里加一行即可，
不必动选型器。选型器 :func:`enumerate_rhythm_patterns` 对进行里每个和弦，
在其拍数 + 段落 + 风格约束下的可行扫弦模板集合里，按若干维度打连续代价分
（越小越靠前，与 ``chord_fingering.playability_cost`` 思路一致）排序，输出
一串 :class:`~rhythm_pattern.model.RhythmEvent`。

打分维度（代价相加）：

1. **拍数可行性**（硬约束）：``chord_beats < pattern.min_beats`` -> 直接剔除。
2. **段落契合**：当前段落不在 ``pattern.sections`` 里则额外罚分。
3. **密度贴合**：模板密度与该段落 + 和弦位置的目标密度之差。
4. **技法基线**（段落级）：musicnn 给出的「该段落该扫还是该拆」倾向。基线为
   ``"arpeggio"`` 时扫弦模板罚分、为 ``"strum"`` 时分解模板罚分；``"mixed"`` /
   ``None`` 不罚，让密度/段落契合自己选。这是段落级混排的关键维度。
5. **扫弦可行性**（轻量，复用 ``chord_fingering.count_muted``）：取该和弦首选
   voicing 的闷弦结构，全扫模板配高音侧闷音（丢顶音）或内部闷音（扫弦要精确挡）时罚分。
6. **进行级连贯性**：相邻和弦拍数变化时（4->1 收束、1->4 展开），密度方向一致的模板减分。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Literal

from chord_fingering import count_muted, enumerate_fingerings

from .model import Cell, Pluck, RhythmEvent, RhythmGrid, Stroke, StrumPattern

if TYPE_CHECKING:
    from pytheory import Fretboard


__all__ = ["STRUM_PATTERNS", "enumerate_rhythm_patterns", "pattern_cost"]


# 段落技法基线：musicnn 的整段标签经规则引擎推出，逐段落给选型器提供「该扫还是该拆」倾向。
# - "strum"     倾向全程扫弦（燥/快/摇滚类）；
# - "arpeggio"  倾向全程分解（柔/慢/抒情类）；
# - "mixed"     主歌拆副歌扫之类的混排，不在此层罚分，交由密度/段落契合自选；
# - None        未提供基线（musicnn 未接），选型器退回纯密度行为。
TechniqueBaseline = Literal["strum", "arpeggio", "mixed"] | None


# --- 模板库 ---------------------------------------------------------------

D = Stroke("D")
U = Stroke("U")
P = Pluck()  # 占位拨弦：暂不指定弦，仅占住「这一格是个拨弦发音」的语义。
REST: Cell | None = None


STRUM_PATTERNS: list[StrumPattern] = [
    StrumPattern(
        name="boom-chick",
        # 1 拍：下扫（根音区）-休止-休止-休止。最简的根-拍交替，民谣/乡村骨架。
        grid_1beat=(D, REST, REST, REST),
        min_beats=1,
        ideal_beats=(2, 4),
        sections=("verse",),
        style="folk",
    ),
    StrumPattern(
        name="folk D-DU",
        # 2 拍一个完整周期：第 1 拍「下 休 休 休」，第 2 拍「下 休 上 休」。
        # 用基本单元表达时，第 2 拍的单元才含上扫；这里用「下-上」1 拍单元近似，
        # 占 2 拍时自然拼成 D.DU.DU，占 1 拍时退化为单拍下-上。
        grid_1beat=(D, REST, U, REST),
        min_beats=2,
        ideal_beats=(2, 4),
        sections=("verse", "prechorus"),
        style="folk",
    ),
    StrumPattern(
        name="pop 8th-notes",
        # 1 拍：下-上 8 分音符交替，流行副歌最常见。
        grid_1beat=(D, U, D, U),
        min_beats=1,
        ideal_beats=(1, 2, 4),
        sections=("chorus", "prechorus"),
        style="pop",
    ),
    StrumPattern(
        name="rock 8th down",
        # 1 拍：下-休-下-休，全下扫重拍，摇滚 power 思路。
        grid_1beat=(D, REST, D, REST),
        min_beats=1,
        ideal_beats=(1, 2, 4),
        sections=("chorus",),
        style="rock",
    ),
    StrumPattern(
        name="pop D-DU-U-DU",
        # 经典 4/4 流行扫弦 ↓ ↓↑ ↑ ↓↑：4 拍一个完整周期。
        # 用「下-下-上」式单元难以在 1 拍内表达完整动机，故 min_beats=4，
        # 仅占 4 拍的和弦才候选；基本单元取第 1 拍的「下 休 上 休」，
        # 占 4 拍时平铺四遍近似经典动机的节奏重音。
        grid_1beat=(D, REST, U, REST),
        min_beats=4,
        ideal_beats=(4,),
        sections=("chorus",),
        style="pop",
    ),
    StrumPattern(
        name="reggae off-beat",
        # 1 拍：休-上-休-休，反拍上扫，雷鬼/Ska 慢扫。
        grid_1beat=(REST, U, REST, REST),
        min_beats=1,
        ideal_beats=(1, 2),
        sections=("chorus", "bridge"),
        style="rock",
    ),
    StrumPattern(
        name="arpeggio placeholder",
        # 占位分解模板：1 拍拨一根弦-休-休-休。具体拨哪根弦、什么顺序待定，
        # 此处只占住「分解技法」的席位，供技法基线罚分维度与混排选型验证用。
        grid_1beat=(P, REST, REST, REST),
        min_beats=1,
        ideal_beats=(2, 4),
        sections=("verse", "prechorus", "bridge"),
        style="folk",
        technique="arpeggio",
    ),
]


# --- 打分权重（越大越劝退） ----------------------------------------------

W_SECTION = 2.5        # 段落不契合：当前段落不在模板 sections 里时的固定罚分
W_DENSITY = 4.0        # 每偏离目标密度 1.0 的代价（密度差 0..1，故实际惩罚 0..4）
W_IDEAL_BEATS = 1.5    # 拍数不在 ideal_beats 里时的罚分（鼓励「占几拍就用几拍周期」的模板）
W_STRUM_MUTED = 1.2    # 扫弦可行性：高音侧闷音（丢顶音）每个的罚分
W_INNER_MUTE = 1.0     # 扫弦可行性：内部闷音（扫弦要精确挡）每个的罚分
W_STYLE_MISMATCH = 5.0 # 风格不匹配：模板风格 != 请求风格时的固定罚分（不剔除，仅降级）
W_TECHNIQUE = 6.0      # 技法基线不符：段落技法基线与模板技法不一致时的固定罚分（段落级混排关键维度）
W_COHERENCE = 0.8      # 连贯性：与相邻和弦密度变化方向不一致时的罚分


def _target_density(section: str, beats: int) -> float:
    """该段落 + 拍数下的目标节奏密度。

    副歌偏密、主歌偏疏；占拍数少时单拍密度略高（要在一拍内把动机弹完），
    占拍数多时略低（4 拍可以慢慢扫）。数值经 ``rhythm_main.py`` 基准集标定。
    """
    base = {"verse": 0.35, "prechorus": 0.5, "chorus": 0.75, "bridge": 0.45, "outro": 0.3}
    d = base.get(section, 0.5)
    # 占拍少 -> 单拍密度略高；占拍多 -> 略低。
    if beats <= 1:
        d += 0.1
    elif beats >= 4:
        d -= 0.05
    return max(0.0, min(1.0, d))


def pattern_cost(
    pattern: StrumPattern,
    *,
    beats: int,
    section: str,
    style: str,
    muted: tuple[int, int, int],
    density_neighbor_delta: float | None,
    technique_baseline: TechniqueBaseline = None,
) -> float:
    """给一个候选模板打连续代价分，越小越靠前。

    Parameters
    ----------
    pattern
        候选节奏型模板（扫弦或分解）。
    beats
        该和弦占多少拍。
    section
        当前段落。
    style
        请求的风格。
    muted
        该和弦首选 voicing 的闷弦结构 ``(inner, low_side, high_side)``，
        由 :func:`chord_fingering.count_muted` 给出。
    density_neighbor_delta
        相邻和弦目标密度之差（后一个减前一个），正值=乐句在展开（密度上升），
        负值=在收束（密度下降），``None`` 表示无相邻参照（进行首尾或单和弦）。
    technique_baseline
        段落技法基线，``"strum" / "arpeggio" / "mixed" / None``。musicnn 的整段
        标签经规则引擎逐段落推出。基线明确（``strum``/``arpeggio``）时，技法不符的
        模板罚 ``W_TECHNIQUE``；``mixed`` / ``None`` 不罚，交由密度/段落契合自选。
    """
    cost = 0.0

    # 段落契合。
    if section not in pattern.sections:
        cost += W_SECTION

    # 风格不匹配：不剔除，只降级（允许跨风格借用，但排在后面）。
    if pattern.style != style:
        cost += W_STYLE_MISMATCH

    # 技法基线（段落级混排关键维度）：基线明确时，技法不符的模板罚分。
    # 不剔除--允许在基线为 strum 时仍选出分解（若它密度/段落契合远胜），只压低顺位。
    if technique_baseline == "arpeggio" and pattern.is_strum:
        cost += W_TECHNIQUE
    elif technique_baseline == "strum" and pattern.is_arpeggio:
        cost += W_TECHNIQUE

    # 密度贴合。
    target = _target_density(section, beats)
    cost += abs(pattern.density() - target) * W_DENSITY

    # 拍数理想区间：占几拍就用几拍周期的模板最顺。
    if beats not in pattern.ideal_beats:
        cost += W_IDEAL_BEATS

    # 扫弦可行性：全扫模板（密度高）配丢顶音/内部闷音的 voicing 时罚分。
    # 闷掉顶音的扫弦听起来「塌」，内部闷音扫弦要靠指腹精确挡、实战少用。
    # 仅对扫弦模板生效--分解模板逐弦拨，闷音结构不构成同样的「塌」问题。
    if pattern.is_strum:
        inner, _low, high = muted
        density = pattern.density()
        # 密度越高越依赖「全扫」，对闷音越敏感。
        cost += high * W_STRUM_MUTED * density
        cost += inner * W_INNER_MUTE * density

    # 进行级连贯性：相邻拍数变化时，鼓励密度同向移动。
    if density_neighbor_delta is not None and density_neighbor_delta != 0:
        # 该模板密度相对目标密度的偏离方向：偏离 >0 表示比目标更密。
        self_delta = pattern.density() - target
        # 相邻在展开（目标密度上升）时，比自己更密的模板（self_delta>0）更顺，减分；
        # 反之加罚。用两 delta 同号判定方向一致。
        if self_delta * density_neighbor_delta > 0:
            cost -= W_COHERENCE
        else:
            cost += W_COHERENCE

    return cost


def _resolve_muted(chord: str, fretboard: "Fretboard", max_stretch: int) -> tuple[int, int, int]:
    """取该和弦首选指法的闷弦结构 ``(inner, low, high)``。

    扫弦不挑单弦，所以只需知道「哪些弦会被闷掉」即可判断全扫是否好听。
    用 :func:`chord_fingering.enumerate_fingerings` 排序第一的指法作为默认 voicing。
    """
    ranked = enumerate_fingerings(
        chord, fretboard, max_fret=7, max_stretch=max_stretch, limit=1
    )
    if not ranked:
        # 指法库找不到可行 voicing（罕见），按「无闷音」处理，不干预选型。
        return (0, 0, 0)
    positions = ranked[0].positions
    open_tones = fretboard.tones
    return count_muted(positions, open_tones)


def enumerate_rhythm_patterns(
    progression: Sequence[tuple[str, int]],
    fretboard: "Fretboard",
    *,
    section: str = "chorus",
    style: str = "pop",
    technique_baseline: TechniqueBaseline = None,
    max_stretch: int = 4,
    limit: int | None = None,
) -> list[RhythmEvent]:
    """对一段和弦进行，为每个和弦选出一个节奏型（扫弦或分解），按可演奏性/贴合度排序。

    Parameters
    ----------
    progression
        和弦进行，``[(和弦符号, 占拍数), ...]``，如 ``[("C", 4), ("G", 2), ("Am", 2)]``。
    fretboard
        ``pytheory.Fretboard``，用于取每个和弦的首选指法、判断扫弦时闷弦结构。
    section
        当前段落标签，``"verse" / "prechorus" / "chorus" / "bridge" / "outro"`` 之一，
        驱动目标密度与段落契合度。
    style
        请求风格，``"folk" / "pop" / "rock"`` 之一。风格不匹配的模板不剔除、只降级。
    technique_baseline
        段落技法基线，``"strum" / "arpeggio" / "mixed" / None``。musicnn 的整段标签经
        规则引擎推出，逐调用传入。基线明确时，技法不符的模板罚 ``W_TECHNIQUE``；
        ``mixed`` / ``None``（默认）不罚，选型器退回纯密度行为。段落级混排的关键维度。
    max_stretch
        取首选指法时的最大跨度约束，透传给 :func:`chord_fingering.enumerate_fingerings`。
    limit
        若给定，只保留每个和弦排序最靠前的 N 个模板（每个和弦仍产出一个 ``RhythmEvent``，
        即取各自第 1 名；``limit`` 主要供调试查看候选排序时用）。

    Returns
    -------
    list[RhythmEvent]
        与 ``progression`` 等长、同序。每个和弦取代价最低（最贴合）的一个模板，
        平铺成完整栅格后包成 :class:`~rhythm_pattern.model.RhythmEvent`。
    """
    progression = list(progression)

    # 预算每个和弦的目标密度，供连贯性判据用前后相邻差。
    targets = [_target_density(section, b) for _, b in progression]
    # 预算每个和弦首选 voicing 的闷弦结构。
    muted = [_resolve_muted(c, fretboard, max_stretch) for c, _ in progression]

    events: list[RhythmEvent] = []
    for i, (chord, beats) in enumerate(progression):
        # 相邻目标密度差：取与下一个和弦的差；末尾和弦无后继则用与前一个的差。
        if len(progression) > 1:
            j = i + 1 if i + 1 < len(progression) else i - 1
            neighbor_delta = targets[j] - targets[i]
        else:
            neighbor_delta = None

        scored: list[tuple[float, StrumPattern]] = []
        for pattern in STRUM_PATTERNS:
            # 拍数可行性：硬约束，一票否决。
            if beats < pattern.min_beats:
                continue
            cost = pattern_cost(
                pattern,
                beats=beats,
                section=section,
                style=style,
                muted=muted[i],
                density_neighbor_delta=neighbor_delta,
                technique_baseline=technique_baseline,
            )
            scored.append((cost, pattern))

        if not scored:
            # 拍数门槛把所有模板都筛掉了（理论不会发生，最小 min_beats=1）。
            # 退路：用 boom-chick（min_beats=1）兜底，保证总有输出。
            fallback = next(p for p in STRUM_PATTERNS if p.name == "boom-chick")
            grid = fallback.grid_for(beats)
            events.append(RhythmEvent(chord=chord, beats=beats, pattern=fallback, grid=grid))
            continue

        # 稳定排序：代价相同时保持 STRUM_PATTERNS 里的顺序，结果确定。
        scored.sort(key=lambda pair: pair[0])
        if limit is not None:
            scored = scored[:limit]
        best = scored[0][1]
        grid = best.grid_for(beats)
        events.append(RhythmEvent(chord=chord, beats=beats, pattern=best, grid=grid))

    return events
