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
5. **整动机奖励**：``beats`` 恰等于 ``motif_beats`` 且 ``ideal_beats`` 是单元素
   ``(motif_beats,)`` 的专属整动机模板减分。占满一个专属动机时最顺，奖励压住高密度
   通用短动机的密度优势。只在「占满一个专属动机」触发，不泛化到任意整数倍，避免长动机
   跨技法倾斜。
6. **扫弦可行性**（轻量，复用 ``chord_fingering.count_muted``）：取该和弦首选
   voicing 的闷弦结构，全扫模板配高音侧闷音（丢顶音）或内部闷音（扫弦要精确挡）时罚分。
7. **进行级连贯性**：相邻和弦拍数变化时（4->1 收束、1->4 展开），密度方向一致的模板减分。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Literal

from chord_fingering import count_muted, enumerate_fingerings

from .model import Cell, Pluck, RhythmEvent, RhythmGrid, Stroke, StrumPattern
from .string_role import (
    All,
    Fifth,
    Root,
    Seventh,
    Third,
    TopN,
    VoicingData,
    voicing_from_fingering,
)

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
REST: Cell | None = None


STRUM_PATTERNS: list[StrumPattern] = [
    # ── 扫弦模板 ──────────────────────────────────────────────
    StrumPattern(
        name="boom-chick",
        # 1 拍动机：下扫（根音区）-休止-休止-休止。最简的根-拍交替，民谣/乡村骨架。
        grid_motif=(D, REST, REST, REST),
        motif_beats=1,
        min_beats=1,
        ideal_beats=(2, 4),
        sections=("verse",),
        style="folk",
    ),
    StrumPattern(
        name="folk D-DU",
        # 2 拍动机：第 1 拍「下 休 休 休」，第 2 拍「下 休 上 休」。完整 D.DU 周期。
        grid_motif=(D, REST, REST, REST, D, REST, U, REST),
        motif_beats=2,
        min_beats=2,
        ideal_beats=(2, 4),
        sections=("verse", "prechorus"),
        style="folk",
    ),
    StrumPattern(
        name="pop 8th-notes",
        # 1 拍动机：下-上 8 分音符交替，流行副歌最常见。
        grid_motif=(D, U, D, U),
        motif_beats=1,
        min_beats=1,
        ideal_beats=(1, 2, 4),
        sections=("chorus", "prechorus"),
        style="pop",
    ),
    StrumPattern(
        name="rock 8th down",
        # 1 拍动机：下-休-下-休，全下扫重拍，摇滚 power 思路。
        grid_motif=(D, REST, D, REST),
        motif_beats=1,
        min_beats=1,
        ideal_beats=(1, 2, 4),
        sections=("chorus",),
        style="rock",
    ),
    StrumPattern(
        name="pop D-DU-U-DU",
        # 经典 4/4 流行扫弦 ↓ ↓↑ ↑ ↓↑：4 拍一个完整周期动机。
        # 每拍第 1 个 16 分为强拍下扫，弱拍加下扫/上扫回扫，构成「下 下上 上 下上」。
        grid_motif=(
            D, REST, REST, REST,   # 1 拍：下
            D, REST, U, REST,      # 2 拍：下-上
            U, REST, REST, REST,   # 3 拍：上
            D, REST, U, REST,      # 4 拍：下-上
        ),
        motif_beats=4,
        min_beats=4,
        ideal_beats=(4,),
        sections=("chorus",),
        style="pop",
    ),
    StrumPattern(
        name="D-D-DU (1拍16分)",
        # 「下 下下上」1 拍 16 分版：4 个动作挤在一拍内，节奏紧凑、推动力强，
        # 常作副歌收束或过门。区别于 pop 8th-notes 的均匀 DUDU，这里第二拍密度更高。
        grid_motif=(D, D, D, U),
        motif_beats=1,
        min_beats=1,
        ideal_beats=(1, 2),
        sections=("chorus", "prechorus"),
        style="pop",
    ),
    StrumPattern(
        name="reggae off-beat",
        # 1 拍动机：休-上-休-休，反拍上扫，雷鬼/Ska 慢扫。
        grid_motif=(REST, U, REST, REST),
        motif_beats=1,
        min_beats=1,
        ideal_beats=(1, 2),
        sections=("chorus", "bridge"),
        style="rock",
    ),
    # ── 分解模板（Pluck 带 StringRole，选型时按 voicing 实例化弦号）─────────
    StrumPattern(
        name="root-5-top2 (1拍)",
        # 「5,3,21」式 1 拍动机：根音(16分)-五音(16分)-顶两弦同拨(8分)。
        # 顶两弦用 TopN(2,'comfortable')，按 voicing 动态选：C 选 2-1 弦（顶音距合适、
        # 丰富），G 根音在 6 弦更低，选 3-2 弦收窄顶底音距、避免尖锐。一次拨多根弦
        # 靠 Pluck.strings 长度>1 表达。弦序随和弦走，固定弦号做不到。
        grid_motif=(Pluck(role=Root()), Pluck(role=Fifth("avoid_bass")), Pluck(role=TopN(2, "comfortable")), REST),
        motif_beats=1,
        min_beats=1,
        ideal_beats=(1, 2, 4),
        sections=("verse", "prechorus", "bridge"),
        style="folk",
        technique="arpeggio",
    ),
    StrumPattern(
        name="53231323 (16分)",
        # 经典民谣分解 5-3-2-3-1-3-2-3，8 个音各占 1 个 16 分位置 = 2 拍动机。
        # C 和弦 x32010 各弦音级：5弦C=根音、4弦E=三音、3弦G=五音、2弦C=根音(高八度)、
        # 1弦E=三音(高八度)。故 5-3-2-3-1-3-2-3 = root-fifth-root(treble)-fifth-
        # third(treble)-fifth-root(treble)-fifth。音级角色随和弦走，换和弦自动映射弦号。
        grid_motif=(
            Pluck(role=Root()), Pluck(role=Fifth()), Pluck(role=Root("treble")), Pluck(role=Fifth()),
            Pluck(role=Third("treble")), Pluck(role=Fifth()), Pluck(role=Root("treble")), Pluck(role=Fifth()),
        ),
        motif_beats=2,
        min_beats=2,
        ideal_beats=(2, 4),
        sections=("verse", "prechorus", "bridge"),
        style="folk",
        technique="arpeggio",
    ),
    StrumPattern(
        name="53231323 (8分)",
        # 同一指法 5-3-2-3-1-3-2-3 的 8 分版：8 个音各占 2 个 16 分位置 = 4 拍动机。
        # 比 16 分版舒缓，适合慢板抒情段落。每个 Pluck 后跟一个 REST 占住 8 分时值。
        # 音级角色同 16 分版（见上）。
        grid_motif=(
            Pluck(role=Root()), REST, Pluck(role=Fifth()), REST, Pluck(role=Root("treble")), REST, Pluck(role=Fifth()), REST,
            Pluck(role=Third("treble")), REST, Pluck(role=Fifth()), REST, Pluck(role=Root("treble")), REST, Pluck(role=Fifth()), REST,
        ),
        motif_beats=4,
        min_beats=4,
        ideal_beats=(4,),
        sections=("verse", "bridge"),
        style="folk",
        technique="arpeggio",
    ),
    StrumPattern(
        name="5323 (8分)",
        # 53231323 的前半截 5-3-2-3，4 个音各占 8 分 = 1 拍动机，循环两遍即 5323-5323。
        # 适合拍数不定的短和弦或快段落的分解。
        grid_motif=(Pluck(role=Root()), REST, Pluck(role=Fifth()), REST),
        motif_beats=1,
        min_beats=1,
        ideal_beats=(1, 2, 4),
        sections=("verse", "prechorus"),
        style="folk",
        technique="arpeggio",
    ),
    StrumPattern(
        name="arpeggio placeholder",
        # 最简占位分解：1 拍拨一弦-休-休-休。role=None 的 Pluck，弦序不指定，
        # 兼作技法基线测试用（technique_baseline 切分解时兜底）。
        grid_motif=(Pluck(role=None), REST, REST, REST),
        motif_beats=1,
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
W_WHOLE_MOTIF = 2.0    # 整动机奖励：beats 恰等于 motif_beats 且 ideal_beats 是单元素 (motif_beats,)
                       # 的模板减分。这类「专属整动机」（如 4 拍周期的 pop D-DU-U-DU、4 拍 53231323
                       # 8 分分解）占满正好一个动机时最顺，奖励压住高密度通用短动机的密度优势。
                       # 只在「占满一个专属动机」时触发，不泛化到任意整数倍，避免跨技法倾斜。
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

    # 整动机奖励：beats 恰等于 motif_beats，且 ideal_beats 是单元素 (motif_beats,) 的
    # 「专属整动机」模板减分。这类模板（4 拍 pop D-DU-U-DU、4 拍 53231323 8分分解等）
    # 占满正好一个动机时最顺，奖励压住高密度通用短动机的密度优势。只在「占满一个专属动机」
    # 时触发，不泛化到任意整数倍，避免长动机在多拍段落跨技法倾斜。
    if (
        beats == pattern.motif_beats
        and len(pattern.ideal_beats) == 1
        and pattern.ideal_beats[0] == pattern.motif_beats
    ):
        cost -= W_WHOLE_MOTIF

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


def _resolve_voicing(chord: str, fretboard: "Fretboard", max_stretch: int) -> VoicingData | None:
    """取该和弦首选指法的 voicing（供扫弦闷音判定 + 分解弦角色实例化）。

    用 :func:`chord_fingering.enumerate_fingerings` 排序第一的指法作默认 voicing。
    返回 :class:`string_role.VoicingData`（含 positions 与 (弦号, midi)）；指法库找不到
    可行 voicing（罕见）时返回 ``None``，由调用方按「无闷音、角色不实例化」降级。
    """
    ranked = enumerate_fingerings(
        chord, fretboard, max_fret=7, max_stretch=max_stretch, limit=1
    )
    if not ranked:
        return None
    f = ranked[0]
    return voicing_from_fingering(f.positions, f.tones)


def _voicing_muted(voicing: VoicingData | None, fretboard: "Fretboard") -> tuple[int, int, int]:
    """从 voicing 算闷弦结构 ``(inner, low, high)``；voicing 为 None 时按无闷音降级。"""
    if voicing is None:
        return (0, 0, 0)
    return count_muted(voicing.positions, fretboard.tones)


def _instantiate_plucks(grid: RhythmGrid, voicing: VoicingData | None) -> RhythmGrid:
    """把栅格里 Pluck 的 role 按 voicing 实例化成具体弦号，填进 Pluck.strings。

    扫弦 (Stroke) 与休止 (None) 格不动。Pluck 格：``role`` 非 None 且 voicing 可用时，
    调 ``role.resolve(voicing)`` 得弦号填入 ``strings``；解析失败或无 voicing 时，
    ``strings`` 保持 ``None``（拨弦但弦未定，不阻塞输出）。模板层原 Pluck 不被修改--
    本函数返回新栅格，event 持有实例化后的栅格。
    """
    if voicing is None:
        return grid  # 无 voicing，Pluck.strings 保持 None。
    new_cells: list[Cell] = []
    for c in grid.cells:
        if isinstance(c, Pluck) and c.role is not None:
            resolved = c.role.resolve(voicing)
            # resolved 为 None 表示该 role 在此 voicing 上无法解析（如省了五音还取五音）。
            # 保留 role、strings=None，栅格结构不变，仅弦序未定。
            new_cells.append(Pluck(role=c.role, strings=resolved))
        else:
            new_cells.append(c)
    return RhythmGrid(tuple(new_cells))


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
    # 预算每个和弦首选 voicing：扫弦用其闷弦结构，分解用其实例化弦角色。
    voicings = [_resolve_voicing(c, fretboard, max_stretch) for c, _ in progression]
    muted = [_voicing_muted(v, fretboard) for v in voicings]

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
            grid = _instantiate_plucks(fallback.grid_for(beats), voicings[i])
            events.append(RhythmEvent(chord=chord, beats=beats, pattern=fallback, grid=grid))
            continue

        # 稳定排序：代价相同时保持 STRUM_PATTERNS 里的顺序，结果确定。
        scored.sort(key=lambda pair: pair[0])
        if limit is not None:
            scored = scored[:limit]
        best = scored[0][1]
        grid = _instantiate_plucks(best.grid_for(beats), voicings[i])
        events.append(RhythmEvent(chord=chord, beats=beats, pattern=best, grid=grid))

    return events
