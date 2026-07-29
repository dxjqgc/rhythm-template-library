"""扫弦节奏型模板库 + 选型器。

模板库是一个普通列表常量 ``STRUM_PATTERNS``，每加一个模板往表里加一行即可，
不必动选型器。选型器 :func:`enumerate_rhythm_patterns` 对进行里每个和弦，
在其拍数 + 段落 + 风格约束下的可行扫弦模板集合里，按若干维度打连续代价分
（越小越靠前，与 ``chord_fingering.playability_cost`` 思路一致）排序，输出
一串 :class:`~rhythm_pattern.model.RhythmEvent`。

选择因素抽象
------------
所有「来自歌曲属性的选择因素」收敛进 :class:`SelectionContext`：段落、风格、
段落技法基线、拍号、BPM、可演奏性约束。字段全可选--未填的维度退回默认行为
（``section`` 默认 ``"chorus"``、``style`` 默认 ``"pop"``、拍号/BPM 缺省即
不参与打分），与既有 ``technique_baseline=None`` 的降级思路一致。这样选型器
对「有歌曲属性分析组件接入」与「裸调用」两种场景中立，新维度（换和弦频率、
主旋律密度等）只需在 ``SelectionContext`` 加字段、在 ``pattern_cost`` 加对应
罚分段，不必改公开契约。

打分维度（代价相加）：

1. **拍数可行性**（硬约束）：``chord_beats < pattern.min_beats`` -> 直接剔除。
2. **段落契合**：当前段落不在 ``pattern.sections`` 里则额外罚分。
3. **风格匹配**：模板风格 != 请求风格时固定罚分（不剔除，允许跨风格借用降级）。
4. **技法基线**（段落级）：musicnn 给出的「该段落该扫还是该拆」倾向。基线为
   ``"arpeggio"`` 时扫弦模板罚分、为 ``"strum"`` 时分解模板罚分；``"mixed"`` /
   ``None`` 不罚，让密度/段落契合自己选。这是段落级混排的关键维度。
5. **密度贴合**：模板密度与该段落 + 和弦位置的目标密度之差。
6. **整动机奖励**：``beats`` 恰等于 ``motif_beats`` 且 ``ideal_beats`` 是单元素
   ``(motif_beats,)`` 的专属整动机模板减分。占满一个专属动机时最顺，奖励压住高密度
   通用短动机的密度优势。只在「占满一个专属动机」触发，不泛化到任意整数倍，避免长动机
   跨技法倾斜。
7. **拍号契合**：``ctx.time_signature`` 给定且分子非 4 时，对 ``motif_beats=4`` 的
   4 拍周期模板罚分（3/4 拍下 4 拍动机天然不周期对齐）。仅显式给拍号时介入，4/4 缺省
   不干预。
8. **BPM 可演奏性**：``ctx.bpm`` 给定时，高 BPM 下过密模板罚分（手指/拨片极限）、
   低 BPM 下高密度连续扫弦轻微罚分（慢歌分解更顺）。``None`` 时不介入。
9. **扫弦可行性**（轻量，复用 ``chord_fingering.count_muted``）：取该和弦首选
   voicing 的闷弦结构，全扫模板配高音侧闷音（丢顶音）或内部闷音（扫弦要精确挡）时罚分。
10. **进行级连贯性**：相邻和弦拍数变化时（4->1 收束、1->4 展开），密度方向一致的模板减分。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol, runtime_checkable

from chord_fingering import count_muted, enumerate_fingerings

from .model import Cell, Pluck, Position, RhythmEvent, RhythmGrid, Stroke, StrumPattern
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


__all__ = [
    "STRUM_PATTERNS",
    "PatternSource",
    "set_pattern_source",
    "get_pattern_source",
    "enumerate_rhythm_patterns",
    "arrange_progression",
    "pattern_cost",
    "instantiate_pattern",
    "resolve_voicing",
    "SelectionContext",
    "to_json",
]


# 段落技法基线：musicnn 的整段标签经规则引擎推出，逐段落给选型器提供「该扫还是该拆」倾向。
# - "strum"     倾向全程扫弦（燥/快/摇滚类）；
# - "arpeggio"  倾向全程分解（柔/慢/抒情类）；
# - "mixed"     主歌拆副歌扫之类的混排，不在此层罚分，交由密度/段落契合自选；
# - None        未提供基线（musicnn 未接），选型器退回纯密度行为。
TechniqueBaseline = Literal["strum", "arpeggio", "mixed"] | None


@dataclass(frozen=True)
class SelectionContext:
    """节奏型选择的「歌曲属性上下文」--所有来自歌曲侧的选择因素收敛于此。

    选型不是死的，而是随歌曲属性变化：同一和弦走向，副歌/主歌、快/慢、3/4/4/4 拍下
    合适的节奏型不同。本类把这些因素显式建模成一个数据结构，``pattern_cost`` 消费它
    决定各维度罚分。字段全可选--未填的维度退回默认行为（``section`` 默认 ``"chorus"``、
    ``style`` 默认 ``"pop"``、拍号/BPM 缺省即不参与打分），与既有
    ``technique_baseline=None`` 的降级思路一致。这样对「有歌曲属性分析组件接入」与
    「裸调用」两种场景中立。

    新增选择因素时，在此加字段、在 :func:`pattern_cost` 加对应罚分段即可，不必改
    :func:`enumerate_rhythm_patterns` 的公开契约。

    Attributes
    ----------
    section
        当前段落标签，``"verse" / "prechorus" / "chorus" / "bridge" / "outro"`` 之一。
        驱动目标密度与段落契合。``None`` -> 默认 ``"chorus"``。
    style
        请求风格，``"folk" / "pop" / "rock"`` 之一。风格不匹配的模板不剔除、只降级。
        ``None`` -> 默认 ``"pop"``。
    technique_baseline
        段落技法基线，``"strum" / "arpeggio" / "mixed" / None``。基线明确时技法不符
        的模板罚分；``mixed`` / ``None``（默认）不罚。段落级混排的关键维度。
    time_signature
        拍号 ``(分子, 分母)``，如 ``(4, 4)`` / ``(3, 4)`` / ``(6, 8)``。分子非 4 时，
        对 ``motif_beats=4`` 的 4 拍周期模板罚分（3/4 拍下 4 拍动机天然不周期对齐）。
        ``None``（默认）-> 按 4/4 处理且**不介入**拍号罚分，即「显式给非默认拍号才干预」。
    bpm
        速度（每分钟拍数）。高 BPM（> ``BPM_HIGH_THRESHOLD``）下过密模板罚分（手指/拨片
        极限），低 BPM（< ``BPM_LOW_THRESHOLD``）下高密度连续扫弦轻微罚分（慢歌分解更顺）。
        ``None``（默认）-> 不介入 BPM 维度，退回纯段落/密度行为。
    position
        和弦在段落中的位置，``"head" / "middle" / "tail"`` 之一（见 :data:`Position`）。
        重点在 ``"tail"`` 收束处理：标 ``positions=("tail",)`` 的模板（如琶音收尾）仅在
        tail 位置 0 罚分，其他位置吃 ``W_POSITION``。``"head"`` 一般不做特殊处理。
        ``None``（默认）-> 不介入位置维度，所有模板按位置中立处理。``arrange_progression``
        会按和弦在段落里的下标自动填此项，调用方通常无需手填。
    max_stretch
        取首选指法时的最大跨度约束，透传给 :func:`chord_fingering.enumerate_fingerings`。

    Notes
    -----
    以下因素为预留扩展位，本阶段不实现打分逻辑，待歌曲属性分析组件接入后补：

    - ``chord_change_rate`` - 换和弦频率（快换和弦时节奏型须简化）；
    - ``melody_density`` - 主旋律密度（与伴奏配比，主旋律密则伴奏疏）。
    """

    section: str | None = None
    style: str | None = None
    technique_baseline: TechniqueBaseline = None
    time_signature: tuple[int, int] | None = None
    bpm: int | None = None
    position: Position | None = None
    max_stretch: int = 4

    @property
    def effective_section(self) -> str:
        """``section`` 缺省时降级到 ``"chorus"``。"""
        return "chorus" if self.section is None else self.section

    @property
    def effective_style(self) -> str:
        """``style`` 缺省时降级到 ``"pop"``。"""
        return "pop" if self.style is None else self.style


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
        # 1 拍动机：下-上 8 分音符交替（下8分-休-上8分-休），流行副歌最常见。
        # 8 分 = 每拍 2 个音，故栅格 D. U.（发音格后跟休止占住 8 分时值）。
        grid_motif=(D, REST, U, REST),
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
    StrumPattern(
        name="arpeggio cadence (tail)",
        # 段落末和弦收束琶音：4 拍动机，低音->五音->三音->全拨收束。
        # 每个音占 1 拍（4 个 16 分位置，发音 1 个 + 休止 3 个），舒缓收尾。
        # 末拍用 All() 拨全部发音弦，相当于「拨弦版扫弦」做终止感。标 positions=("tail",)--
        # 仅在段落末和弦 0 罚分，其他位置吃 W_POSITION 被压下。
        grid_motif=(
            Pluck(role=Root()), REST, REST, REST,
            Pluck(role=Fifth("avoid_bass")), REST, REST, REST,
            Pluck(role=Third()), REST, REST, REST,
            Pluck(role=All()), REST, REST, REST,
        ),
        motif_beats=4,
        min_beats=4,
        ideal_beats=(4,),
        sections=("verse", "bridge", "outro"),
        style="folk",
        technique="arpeggio",
        positions=("tail",),
    ),
    StrumPattern(
        name="arpeggio cadence short (tail)",
        # 段落末和弦短收束琶音：2 拍动机，低音->五音->全拨收束。每个音占 8 分（2 个 16 分位置）。
        # 补 4 拍版的缺口--段落尾和弦常是 2 拍甚至更短，4 拍 cadence 的 min_beats=4 进不了候选，
        # 此版 min_beats=2 覆盖短尾和弦。末拍 All() 拨全部弦做终止感。标 positions=("tail",)。
        grid_motif=(
            Pluck(role=Root()), REST, Pluck(role=Fifth("avoid_bass")), REST,
            Pluck(role=All()), REST, Pluck(role=All()), REST,
        ),
        motif_beats=2,
        min_beats=2,
        ideal_beats=(2, 4),
        sections=("verse", "bridge", "outro"),
        style="folk",
        technique="arpeggio",
        positions=("tail",),
    ),
]


# --- 数据源 seam（可注入，默认硬编码）-----------------------------------
#
# 选型器不直接读模块级 STRUM_PATTERNS，而读一个「数据源」协议。默认源背靠硬编码列表
# （集成项目无感），web 管理器启动时调 set_pattern_source 注入数据库源，使编辑后的模板
# 立即生效。STRUM_PATTERNS 常量始终保留，作为兜底与未注入源时的默认行为。


@runtime_checkable
class PatternSource(Protocol):
    """节奏型数据源协议：返回当前可用的模板列表。

    默认实现背靠硬编码 :data:`STRUM_PATTERNS`；web 管理器提供数据库源实现注入。
    """

    def patterns(self) -> list[StrumPattern]: ...


class _ListPatternSource:
    """背靠一个固定列表的数据源（默认实现）。"""

    def __init__(self, patterns: list[StrumPattern]) -> None:
        self._patterns = patterns

    def patterns(self) -> list[StrumPattern]:
        return self._patterns


# 进程级默认源。set_pattern_source 是全局状态，仅适用于单用户本地工具（如 web 管理器）。
_default_source: PatternSource = _ListPatternSource(STRUM_PATTERNS)


def set_pattern_source(source: PatternSource | None) -> None:
    """注入数据源。``None`` 重置为硬编码 :data:`STRUM_PATTERNS` 默认源。

    进程级全局状态：web 管理器在启动时调一次注入数据库源；普通集成项目无需调用，
    自动用硬编码默认源，行为与改 seam 前完全一致。多进程并发安全不在范围内。
    """
    global _default_source
    _default_source = source if source is not None else _ListPatternSource(STRUM_PATTERNS)


def get_pattern_source() -> PatternSource:
    """取当前数据源（主要用于测试与自省）。"""
    return _default_source


def _boom_chick_fallback() -> StrumPattern:
    """取兜底 boom-chick：优先当前数据源，缺失时退回硬编码列表，再缺失则内联构造，
    保证总不崩。

    - 当前数据源有 boom-chick → 用之；
    - 否则退回硬编码 :data:`STRUM_PATTERNS`；
    - 若硬编码也被改/删（极端），内联构造一个最小 boom-chick，绝不抛 ``StopIteration``。
    """
    try:
        return next(p for p in _default_source.patterns() if p.name == "boom-chick")
    except StopIteration:
        pass
    try:
        return next(p for p in STRUM_PATTERNS if p.name == "boom-chick")
    except StopIteration:
        # 硬编码也被改：内联兜底，兑现「保证总不崩」。
        return StrumPattern(
            name="boom-chick",
            grid_motif=(Stroke("D"), None, None, None),
            motif_beats=1,
            min_beats=1,
            ideal_beats=(2, 4),
            sections=("verse",),
            style="folk",
            technique="strum",
        )


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
W_TIME_SIG = 2.5       # 拍号不契合：非 4/X 拍号下选用 motif_beats=4 的 4 拍周期模板的固定罚分
                       # （与 W_SECTION 同量级：3/4 拍下 4 拍动机天然不周期对齐，属结构性错配）。
                       # 仅 ctx.time_signature 显式给定且分子非 4 时触发，4/4 缺省不干预。
W_POSITION = 2.5       # 位置不契合：模板声明了 positions（非空）但当前位置不在其中时的固定罚分
                       # （与 W_SECTION 同量级）。位置中立的模板（positions 为空）不罚。
                       # 重点在 tail 收束处理：标 positions=("tail",) 的琶音收尾模板在非 tail 位置被压下。
W_POSITION_TAIL_BONUS = 2.0  # 位置 tail 奖励：标 positions=("tail",) 的模板在 tail 位置减分。
                       # 仅有 W_POSITION（免罚）不够--位置中立的扫弦模板（folk D-DU 等）在 tail
                       # 不罚分，靠密度/段落契合就能压过收束型。故给收束模板在 tail 正向奖励，
                       # 让「尾和弦倾向收束」真正生效。取 2.0 > W_CONTINUITY(1.5)：尾收束倾向应
                       # 压过 DP「同模板延续」的连贯性代价（即便要换模板，尾和弦也该收束）。
                       # 与整动机奖励（W_WHOLE_MOTIF）同属减分类机制。
W_CONTINUITY = 1.5     # 整段编排 DP 的模板延续性罚分：相邻和弦换模板（name 不同）时加。
                       # 防止整段逐和弦乱跳模板导致割裂。量级小于 W_TECHNIQUE_CONTIGUITY--
                       # 技法突变比同技法换模板更刺耳，故技法连贯性罚得更重。
W_TECHNIQUE_CONTIGUITY = 4.0  # 整段编排 DP 的技法连贯性罚分：相邻和弦扫/拆技法突变时加。
                       # 避免「扫弦-分解-扫弦」反复跳，保留段落内技法统一感。
BPM_HIGH_THRESHOLD = 140  # 高速门槛：bpm 高于此值时，过密模板按 W_BPM_HIGH 罚分（手指/拨片极限）。
BPM_LOW_THRESHOLD = 70    # 慢速门槛：bpm 低于此值时，高密度连续扫弦按 W_BPM_LOW 轻微罚分（慢歌分解更顺）。
W_BPM_HIGH = 3.0       # 高 BPM 下每超出密度阈值 1.0 的代价。密度越高的模板越受罚，模拟可演奏性边界。
                       # 阈值标定：16 分音符密集分解（53231323 16分，密度 1.0）在 180 BPM 下一拍内
                       # 4 次拨弦已接近指弹极限，需让位低密度模板。
W_BPM_LOW = 1.5        # 低 BPM 下高密度连续扫弦的罚分。慢歌用分解更顺，连续扫弦在低 BPM 下听起来
                       # 「冲」，与技法基线互补（基线管整段扫/拆，此维度管密度细节）。


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
    muted: tuple[int, int, int],
    density_neighbor_delta: float | None,
    ctx: SelectionContext,
) -> float:
    """给一个候选模板打连续代价分，越小越靠前。

    Parameters
    ----------
    pattern
        候选节奏型模板（扫弦或分解）。
    beats
        该和弦占多少拍。
    muted
        该和弦首选 voicing 的闷弦结构 ``(inner, low_side, high_side)``，
        由 :func:`chord_fingering.count_muted` 给出。
    density_neighbor_delta
        相邻和弦目标密度之差（后一个减前一个），正值=乐句在展开（密度上升），
        负值=在收束（密度下降），``None`` 表示无相邻参照（进行首尾或单和弦）。
    ctx
        选择上下文（:class:`SelectionContext`）：收敛段落、风格、技法基线、拍号、BPM
        等歌曲属性。``section``/``style`` 缺省时降级到 ``"chorus"``/``"pop"``；拍号/BPM
        缺省（``None``）时对应维度不介入。详见 :class:`SelectionContext`。
    """
    section = ctx.effective_section
    style = ctx.effective_style
    technique_baseline = ctx.technique_baseline
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

    # 拍号契合：仅在 ctx 显式给拍号且分子非 4 时介入。4 拍周期模板（motif_beats=4）
    # 在 3/4、6/8 等拍号下天然不周期对齐（4 拍动机填不进 3 拍小节），罚固定分。
    # 4/4 缺省不干预，避免所有模板都吃一次拍号罚分。
    if ctx.time_signature is not None and ctx.time_signature[0] != 4:
        if pattern.motif_beats == 4:
            cost += W_TIME_SIG

    # BPM 可演奏性：仅 ctx 显式给 bpm 时介入。
    # 高 BPM（> BPM_HIGH_THRESHOLD）：过密模板按超出密度阈值罚分，模拟手指/拨片极限。
    # 低 BPM（< BPM_LOW_THRESHOLD）：高密度连续扫弦轻微罚分，慢歌分解更顺。
    density = pattern.density()
    if ctx.bpm is not None:
        if ctx.bpm > BPM_HIGH_THRESHOLD and density > 0.5:
            cost += (density - 0.5) * W_BPM_HIGH
        if ctx.bpm < BPM_LOW_THRESHOLD and pattern.is_strum and density > 0.5:
            cost += (density - 0.5) * W_BPM_LOW

    # 位置契合：仅 ctx 显式给 position 且模板声明了 positions（非空）时介入。
    # 模板 positions 为空 = 位置中立，不罚（避免强迫所有模板标位置）。重点在 tail 收束：
    # - 当前位置不在模板 positions 里 -> 罚 W_POSITION（收束模板在非 tail 位置被压下）；
    # - 当前位置是 tail 且模板声明含 tail -> 减 W_POSITION_TAIL_BONUS（正向奖励，让尾和弦
    #   真正倾向收束型，而非被位置中立的扫弦模板靠密度契合压过）。
    # head 一般不做特殊处理（无模板标 head，也无 head 奖励）。
    if ctx.position is not None and pattern.positions:
        if ctx.position not in pattern.positions:
            cost += W_POSITION
        elif ctx.position == "tail":
            cost -= W_POSITION_TAIL_BONUS

    # 扫弦可行性：全扫模板（密度高）配丢顶音/内部闷音的 voicing 时罚分。
    # 闷掉顶音的扫弦听起来「塌」，内部闷音扫弦要靠指腹精确挡、实战少用。
    # 仅对扫弦模板生效--分解模板逐弦拨，闷音结构不构成同样的「塌」问题。
    if pattern.is_strum:
        inner, _low, high = muted
        # 密度越高越依赖「全扫」，对闷音越敏感。
        cost += high * W_STRUM_MUTED * density
        cost += inner * W_INNER_MUTE * density

    # 进行级连贯性：相邻拍数变化时，鼓励密度同向移动。
    if density_neighbor_delta is not None and density_neighbor_delta != 0:
        # 该模板密度相对目标密度的偏离方向：偏离 >0 表示比目标更密。
        self_delta = density - target
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


def _chord_candidates(
    *,
    beats: int,
    muted: tuple[int, int, int],
    density_neighbor_delta: float | None,
    ctx: SelectionContext,
) -> list[tuple[float, StrumPattern]]:
    """对单个和弦，算出所有可行模板的 ``(代价, 模板)`` 列表（未排序）。

    拍数硬约束（``beats < min_beats``）剔除，其余维度由 :func:`pattern_cost` 打分。
    供 :func:`enumerate_rhythm_patterns`（取第 1 名）与 :func:`arrange_progression`
    （取 Top-K 做 DP）共用，避免候选生成逻辑重复。
    """
    scored: list[tuple[float, StrumPattern]] = []
    for pattern in _default_source.patterns():
        if beats < pattern.min_beats:
            continue
        cost = pattern_cost(
            pattern,
            beats=beats,
            muted=muted,
            density_neighbor_delta=density_neighbor_delta,
            ctx=ctx,
        )
        scored.append((cost, pattern))
    return scored


def _instantiate_event(
    chord: str, beats: int, pattern: StrumPattern, voicing: VoicingData | None
) -> RhythmEvent:
    """把选中的模板平铺成栅格、实例化 Pluck 弦号，包成 :class:`RhythmEvent`。"""
    grid = _instantiate_plucks(pattern.grid_for(beats), voicing)
    return RhythmEvent(chord=chord, beats=beats, pattern=pattern, grid=grid)


def enumerate_rhythm_patterns(
    progression: Sequence[tuple[str, int]],
    fretboard: "Fretboard",
    *,
    section: str = "chorus",
    style: str = "pop",
    technique_baseline: TechniqueBaseline = None,
    max_stretch: int = 4,
    ctx: SelectionContext | None = None,
    limit: int | None = None,
) -> list[RhythmEvent]:
    """对一段和弦进行，为每个和弦选出一个节奏型（扫弦或分解），按可演奏性/贴合度排序。

    选择因素以两种方式传入，**向后兼容**：

    - 旧式：直接用 ``section`` / ``style`` / ``technique_baseline`` / ``max_stretch``
      关键字参数（现有调用全部不破）。
    - 新式：传一个 :class:`SelectionContext` 进 ``ctx``，享受拍号、BPM 等新维度。
      ``ctx`` 非 ``None`` 时**覆盖**旧关键字参数（显式优先），其 ``max_stretch`` 优先于
      函数的 ``max_stretch`` 形参。

    Parameters
    ----------
    progression
        和弦进行，``[(和弦符号, 占拍数), ...]``，如 ``[("C", 4), ("G", 2), ("Am", 2)]``。
    fretboard
        ``pytheory.Fretboard``，用于取每个和弦的首选指法、判断扫弦时闷弦结构。
    section
        当前段落标签，``"verse" / "prechorus" / "chorus" / "bridge" / "outro"`` 之一，
        驱动目标密度与段落契合度。``ctx`` 给定时此项被覆盖。
    style
        请求风格，``"folk" / "pop" / "rock"`` 之一。风格不匹配的模板不剔除、只降级。
        ``ctx`` 给定时此项被覆盖。
    technique_baseline
        段落技法基线，``"strum" / "arpeggio" / "mixed" / None``。基线明确时，技法不符的
        模板罚 ``W_TECHNIQUE``；``mixed`` / ``None``（默认）不罚。``ctx`` 给定时此项被覆盖。
    max_stretch
        取首选指法时的最大跨度约束，透传给 :func:`chord_fingering.enumerate_fingerings`。
        ``ctx`` 给定时以其 ``max_stretch`` 为准。
    ctx
        :class:`SelectionContext`，收敛所有歌曲属性选择因素。``None``（默认）时按上述
        旧关键字参数组装一个等价的上下文。传入后启用拍号、BPM 等新维度。
    limit
        若给定，只保留每个和弦排序最靠前的 N 个模板（每个和弦仍产出一个 ``RhythmEvent``，
        即取各自第 1 名；``limit`` 主要供调试查看候选排序时用）。

    Returns
    -------
    list[RhythmEvent]
        与 ``progression`` 等长、同序。每个和弦取代价最低（最贴合）的一个模板，
        平铺成完整栅格后包成 :class:`~rhythm_pattern.model.RhythmEvent`。
    """
    # 统一收敛到 SelectionContext：ctx 优先（显式优先），否则由旧关键字参数组装。
    if ctx is None:
        ctx = SelectionContext(
            section=section,
            style=style,
            technique_baseline=technique_baseline,
            max_stretch=max_stretch,
        )
    stretch = ctx.max_stretch
    eff_section = ctx.effective_section

    progression = list(progression)

    # 预算每个和弦的目标密度，供连贯性判据用前后相邻差。
    targets = [_target_density(eff_section, b) for _, b in progression]
    # 预算每个和弦首选 voicing：扫弦用其闷弦结构，分解用其实例化弦角色。
    voicings = [_resolve_voicing(c, fretboard, stretch) for c, _ in progression]
    muted = [_voicing_muted(v, fretboard) for v in voicings]

    events: list[RhythmEvent] = []
    for i, (chord, beats) in enumerate(progression):
        # 相邻目标密度差：取与下一个和弦的差；末尾和弦无后继则用与前一个的差。
        if len(progression) > 1:
            j = i + 1 if i + 1 < len(progression) else i - 1
            neighbor_delta = targets[j] - targets[i]
        else:
            neighbor_delta = None

        scored = _chord_candidates(
            beats=beats,
            muted=muted[i],
            density_neighbor_delta=neighbor_delta,
            ctx=ctx,
        )

        if not scored:
            # 拍数门槛把所有模板都筛掉了（理论不会发生，最小 min_beats=1）。
            # 退路：用 boom-chick（min_beats=1）兜底，保证总有输出。
            fallback = _boom_chick_fallback()
            events.append(_instantiate_event(chord, beats, fallback, voicings[i]))
            continue

        # 稳定排序：代价相同时保持 STRUM_PATTERNS 里的顺序，结果确定。
        scored.sort(key=lambda pair: pair[0])
        if limit is not None:
            scored = scored[:limit]
        best = scored[0][1]
        events.append(_instantiate_event(chord, beats, best, voicings[i]))

    return events


def _position_for(index: int, n: int) -> Position:
    """按和弦在段落里的下标判定位置：第 0 个=head，最后一个=tail，其余=middle。

    单和弦段落（``n==1``）判 tail--独和弦按收束处理更合理，且避免 head/tail 混淆。
    """
    if n <= 1:
        return "tail"
    if index == 0:
        return "head"
    if index == n - 1:
        return "tail"
    return "middle"


def _transition_cost(a: StrumPattern, b: StrumPattern, b_position: Position | None = None) -> float:
    """整段编排 DP 的相邻和弦转移代价：模板延续性 + 技法连贯性。

    - 模板延续性：同模板（name 相同）0 罚分，换模板罚 ``W_CONTINUITY``。
    - 技法连贯性：扫/拆技法突变罚 ``W_TECHNIQUE_CONTIGUITY``（比换模板更重--技法跳变更刺耳）。

    ``b_position == "tail"`` 时**技法跳变豁免**：段落尾和弦常需收束处理（如中段扫弦、
    尾琶音收束），必然有一次扫/拆切换，此处不罚--「尾收束优先于技法连贯」。模板延续性
    罚分保留（换模板仍罚，但不因技法跳变额外加罚）。``b_position`` 为 ``None``（位置未介入，
    如 ``enumerate_rhythm_patterns`` 逐和弦无状态场景）时不豁免。
    """
    cost = 0.0
    if a.name != b.name:
        cost += W_CONTINUITY
    if a.technique != b.technique and b_position != "tail":
        cost += W_TECHNIQUE_CONTIGUITY
    return cost


def arrange_progression(
    progression: Sequence[tuple[str, int]],
    fretboard: "Fretboard",
    *,
    ctx: SelectionContext | None = None,
    k: int = 3,
    section: str = "chorus",
    style: str = "pop",
    technique_baseline: TechniqueBaseline = None,
    max_stretch: int = 4,
) -> list[RhythmEvent]:
    """对一段和弦进行做**整段编排**：Top-K 候选 + DP 选路径，输出连贯的节奏型序列。

    与 :func:`enumerate_rhythm_patterns`（逐和弦贪心取第 1 名）不同，本函数为每个和弦
    保留 Top-K 个候选，再用动态规划在这些候选里选一条**总代价最低**的路径，转移代价
    约束**模板延续性**（避免逐和弦乱跳模板）与**技法连贯性**（避免扫/拆反复突变）。
    结果是整段连贯、不割裂的节奏型序列。

    位置维度在此入口自动生效：按和弦在段落里的下标判定 ``head/middle/tail``（见
    :func:`_position_for`），逐和弦用带位置的 :class:`SelectionContext` 打分。这样
    段落末和弦自然倾向收束型（如琶音收尾）。调用方按段落整段传入即可，无需手填位置。

    Parameters
    ----------
    progression
        和弦进行，``[(和弦符号, 占拍数), ...]``，**按段落组织**--位置判定依赖段内下标。
    fretboard
        ``pytheory.Fretboard``。
    ctx
        :class:`SelectionContext`，歌曲属性上下文。``None`` 时由 ``section``/``style``/
        ``technique_baseline``/``max_stretch`` 组装。注意：``ctx.position`` **会被逐和弦
        覆盖**（按段内下标判定），调用方设的 position 不生效。
    k
        每个和弦保留的候选数（Top-K），DP 在 K 个候选里选路径。越大越接近全局最优但
        越慢（复杂度 O(和弦数 × K²)）。默认 3。
    section / style / technique_baseline / max_stretch
        ``ctx`` 为 ``None`` 时的降级参数，语义同 :func:`enumerate_rhythm_patterns`。

    Returns
    -------
    list[RhythmEvent]
        与 ``progression`` 等长、同序。整段总代价最低（含转移代价）的模板序列，每个
        和弦平铺成栅格、实例化弦号后包成 :class:`RhythmEvent`。
    """
    if ctx is None:
        ctx = SelectionContext(
            section=section, style=style,
            technique_baseline=technique_baseline, max_stretch=max_stretch,
        )
    stretch = ctx.max_stretch
    eff_section = ctx.effective_section

    progression = list(progression)
    n = len(progression)
    if n == 0:
        return []

    # 预算目标密度、voicing、闷音（与 enumerate_rhythm_patterns 同构）。
    targets = [_target_density(eff_section, b) for _, b in progression]
    voicings = [_resolve_voicing(c, fretboard, stretch) for c, _ in progression]
    muted = [_voicing_muted(v, fretboard) for v in voicings]

    # 第一趟：每个和弦算 Top-K 候选（按段内位置打分）。
    # 逐和弦构造带 position 的 ctx：用 dataclasses.replace 保持其他字段，仅覆盖 position。
    from dataclasses import replace

    positions = [_position_for(i, n) for i in range(n)]
    candidates: list[list[tuple[float, StrumPattern]]] = []
    for i, (_chord, beats) in enumerate(progression):
        if n > 1:
            j = i + 1 if i + 1 < n else i - 1
            neighbor_delta = targets[j] - targets[i]
        else:
            neighbor_delta = None
        pos_ctx = replace(ctx, position=positions[i])
        scored = _chord_candidates(
            beats=beats, muted=muted[i],
            density_neighbor_delta=neighbor_delta, ctx=pos_ctx,
        )
        if not scored:
            # 拍数门槛兜底（理论不会发生）：塞 boom-chick 单候选，保证 DP 有路径。
            fallback = _boom_chick_fallback()
            scored = [(0.0, fallback)]
        scored.sort(key=lambda pair: pair[0])
        scored = scored[:k]
        candidates.append(scored)

    # 第二趟：DP 选总代价最低路径。
    # dp[i][j] = 选第 i 和弦的第 j 个候选时，前 i+1 个和弦的最小总代价。
    # parent[i][j] = 使 dp[i][j] 最优的 i-1 和弦候选下标，用于回溯。
    dp = [[cand[0] for cand in candidates[i]] for i in range(n)]
    parent: list[list[int]] = [[-1] * len(candidates[i]) for i in range(n)]
    for i in range(1, n):
        for j, (cost_j, pat_j) in enumerate(candidates[i]):
            best_prev = 0
            best_total = float("inf")
            for p, (cost_p, pat_p) in enumerate(candidates[i - 1]):
                # 转移代价按后一和弦(i)的位置：tail 时技法跳变豁免（尾收束优先于技法连贯）。
                total = dp[i - 1][p] + _transition_cost(pat_p, pat_j, positions[i]) + cost_j
                if total < best_total:
                    best_total = total
                    best_prev = p
            dp[i][j] = best_total
            parent[i][j] = best_prev

    # 回溯：从末和弦最优候选往前找回路径。
    last = min(range(len(candidates[n - 1])), key=lambda j: dp[n - 1][j])
    chosen_idx = [0] * n
    chosen_idx[n - 1] = last
    for i in range(n - 1, 0, -1):
        chosen_idx[i - 1] = parent[i][chosen_idx[i]]

    # 装事件。
    events = [
        _instantiate_event(
            progression[i][0], progression[i][1],
            candidates[i][chosen_idx[i]][1], voicings[i],
        )
        for i in range(n)
    ]
    return events


# --- 单模板实例化公开 helper（供 web 试听等场景，不碰私有内部）────────────


def resolve_voicing(
    chord: str, fretboard: "Fretboard", max_stretch: int = 4
) -> VoicingData | None:
    """取某和弦首选指法的 voicing（公开版 :func:`_resolve_voicing`）。

    供需要弦→midi 映射的调用方（如 web 试听提取音符）使用，无需触及私有内部函数。
    返回 :class:`VoicingData`（含 ``positions`` 与 ``(弦号, midi)``）；指法库找不到
    可行 voicing 时返回 ``None``。
    """
    return _resolve_voicing(chord, fretboard, max_stretch)


def instantiate_pattern(
    pattern: StrumPattern,
    chord: str,
    fretboard: "Fretboard",
    beats: int,
    *,
    max_stretch: int = 4,
) -> RhythmEvent:
    """把**单个**模板在某和弦上实例化成 :class:`RhythmEvent`（供试听等单点场景）。

    与 :func:`enumerate_rhythm_patterns` / :func:`arrange_progression`（整段选型）不同，
    本函数不做选型，直接把给定 ``pattern`` 平铺到 ``beats`` 拍、按该和弦首选 voicing
    实例化 Pluck 弦号。是 :func:`_instantiate_event` 的公开薄包装：先解析 voicing，
    再平铺栅格、实例化弦角色。

    Parameters
    ----------
    pattern
        要实例化的模板。
    chord
        和弦符号，如 ``"C"`` / ``"G"``。
    fretboard
        ``pytheory.Fretboard``，标准调弦用 ``Fretboard.guitar()``。
    beats
        和弦占拍数；模板平铺/截断到 ``4 * beats`` 格。
    max_stretch
        取首选指法时的最大跨度约束。

    Returns
    -------
    RhythmEvent
        ``.grid`` 已实例化 Pluck 弦号（解析失败的 role 保持 ``None``），``.fingering``
        派生指法动作序列。
    """
    voicing = _resolve_voicing(chord, fretboard, max_stretch)
    return _instantiate_event(chord, beats, pattern, voicing)


def to_json(
    events: Sequence[RhythmEvent],
    *,
    indent: int | None = None,
    ensure_ascii: bool = True,
) -> str:
    """把整段编排结果转成 JSON 字符串，供转谱项目跨语言消费。

    等价于 ``json.dumps([e.to_dict() for e in events], ...)``，封装一次省得调用方
    重复写。每个 event 的结构见 :meth:`RhythmEvent.to_dict`：和弦符号、拍数、模板名、
    技法、指法动作序列。

    Parameters
    ----------
    events
        :func:`enumerate_rhythm_patterns` 或 :func:`arrange_progression` 的返回值。
    indent
        缩进格数，``None``（默认）= 紧凑单行；``2`` = 美化缩进，便于人读。
    ensure_ascii
        是否转义非 ASCII 字符。模板名是中文（如 ``"53231323 (8分)"``），需保留中文
        可读性时传 ``False``（默认 ``True``，纯 ASCII 输出）。

    Returns
    -------
    str
        JSON 字符串，``[ {...}, {...}, ... ]``，与 events 等长同序。
    """
    import json

    return json.dumps(
        [e.to_dict() for e in events],
        indent=indent,
        ensure_ascii=ensure_ascii,
    )
