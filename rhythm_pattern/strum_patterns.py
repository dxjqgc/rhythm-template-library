"""扫弦节奏型模板库 + 选型器。

模板**只**存在于文本数据库 ``rhythm_pattern/data/templates.json``（见
:mod:`rhythm_pattern.serialization`），由 web 管理器增删改；代码里没有硬编码模板表。
选型器 :func:`enumerate_rhythm_patterns` 对进行里每个和弦，在其拍数 + 段落 + 风格
约束下的可行模板集合里，按若干维度打连续代价分（越小越靠前，与
``chord_fingering.playability_cost`` 思路一致）排序，输出一串
:class:`~rhythm_pattern.model.RhythmEvent`。

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
   ``"fingerpicking"`` / ``"arpeggio"`` 时扫弦模板罚分、为 ``"strum"`` 时分解/
   琶音模板罚分（``"arpeggio"`` 宽匹配，分解与琶音均不罚）；``"mixed"`` /
   ``None`` 不罚，让密度/段落契合自己选。这是段落级混排的关键维度。此外，
   ``"mixed"`` / ``None`` 下还有**段落技法先验**（软罚）：主歌/尾奏（verse/outro）
   下扫弦模板加 ``W_SECTION_STRUM``——弹唱惯例主歌铺垫用分解、不直接上扫弦；
   软罚只降顺位不排除（强风格证据/实测极密下扫弦仍可胜出），显式基线给出时
   跳过（不叠加）。
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

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol, runtime_checkable

from chord_fingering import count_muted, enumerate_fingerings

from .model import Cell, Pluck, Position, Rest, RhythmEvent, RhythmGrid, Stroke, StrumPattern
from .string_role import VoicingData, voicing_from_fingering

if TYPE_CHECKING:
    from pytheory import Fretboard

    from .serialization import TemplateRepository

_log = logging.getLogger(__name__)


__all__ = [
    "PatternSource",
    "ListPatternSource",
    "DbPatternSource",
    "set_pattern_source",
    "get_pattern_source",
    "enumerate_rhythm_patterns",
    "arrange_progression",
    "plan_song_rhythm",
    "pattern_cost",
    "instantiate_pattern",
    "resolve_voicing",
    "SelectionContext",
    "to_json",
]


# 段落技法基线：musicnn 的整段标签经规则引擎推出，逐段落给选型器提供「该扫还是该拆」倾向。
# - "strum"         倾向全程扫弦（燥/快/摇滚类）；
# - "fingerpicking" 倾向全程分解（柔/慢/抒情类），只匹配分解模板；
# - "arpeggio"      宽匹配：分解与琶音模板都不罚（历史语义——musicnn 的 guitar/slow
#                   段落标签映射到它，拆分技法后保持该链路行为不变）；
# - "mixed"         主歌拆副歌扫之类的混排，不在此层罚分，交由密度/段落契合自选；
# - None            未提供基线（musicnn 未接），选型器退回纯密度行为。
TechniqueBaseline = Literal["strum", "fingerpicking", "arpeggio", "mixed"] | None


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
        ``None`` -> 默认 ``"pop"``。**注意**：当模板带 ``tags`` 且 ctx 给了
        ``musicnn_tags`` 时，风格维度改由标签匹配度接管，``style`` 退回 fallback
        （仅在模板无 tags 或 ctx 无 musicnn_tags 时生效）。
    technique_baseline
        段落技法基线，``"strum" / "fingerpicking" / "arpeggio" / "mixed" / None``。
        基线明确时技法不符的模板罚分：扫弦 vs 拨弦类互斥罚 ``W_TECHNIQUE``；
        ``"fingerpicking"`` 基线额外给真琶音（``technique="arpeggio"``）模板轻罚
        ``W_TECHNIQUE_SOFT``（分解优先，真琶音仍可凭 tail 奖励收束）；
        ``"arpeggio"`` 宽匹配：分解与琶音都不罚；``mixed`` / ``None``（默认）不罚。
        段落级混排的关键维度。**显式基线还压住段落技法先验**：``mixed`` / ``None``
        下 verse/outro 段落的扫弦模板会吃 ``W_SECTION_STRUM`` 软罚（弹唱惯例：
        主歌不直接上扫弦，软罚降顺位不排除），显式给出基线（歌曲侧实测证据）时
        该先验跳过、不叠加。
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
    musicnn_tags
        musicnn 给出的段落风格标签集（带置信度），每项 ``(tag, likelihood)``，
        likelihood ∈ 0..1。非空时启用标签匹配度打分（见 :func:`_tag_mismatch`），
        且 ``style`` 维度降级为 fallback——模板有 ``tags`` 时只比 tags、不比 style。
        ``None``（默认）-> 不介入标签维度，退回 ``style`` 罚分（向后兼容旧调用）。
        这是 musicnn 接入后的主风格维度，比 ``style`` 三值枚举细：musicnn 的
        ``guitar/slow`` 拉向分解模板、``drums/fast/rock`` 拉向扫弦模板。
    onset_density
        该段落音频的实测起音密度 ∈ [0, 1]（onsets / (4 × 拍数)，与模板密度同尺度：
        一次 16 分发音 = 0.25/拍）。给定时不替换、而是与段落静态目标密度**加权融合**
        （实测权重 ``W_ONSET_AUDIO``，默认 0.6）：音频主导（同一 verse 实际疏密不同），
        段落性格保留（chorus 仍偏密）。调用方负责归一化（如段落间 min-max），传值应
        已在静态表值域附近；越界值被 clamp 到 [0, 1]。``None``（默认）-> 完全退回
        静态表，行为与旧版一致（向后兼容）。

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
    musicnn_tags: tuple[tuple[str, float], ...] | None = None
    onset_density: float | None = None

    @property
    def effective_section(self) -> str:
        """``section`` 缺省时降级到 ``"chorus"``。"""
        return "chorus" if self.section is None else self.section

    @property
    def effective_style(self) -> str:
        """``style`` 缺省时降级到 ``"pop"``。"""
        return "pop" if self.style is None else self.style


# --- 数据源（模板的唯一真源：templates.json）-----------------------------
#
# 选型器不直接读文件，而读一个「数据源」协议。默认源是库内自带的
# rhythm_pattern/data/templates.json（web 管理器的 CRUD 目标），每次 patterns() 都
# 重新 load，故编辑即时生效。集成方/测试可用 set_pattern_source 注入自定义源。
#
# DB 缺失或损坏视为部署问题--不静默退回陈旧数据，异常照抛（后端据此把服务标为
# unavailable，前端显示「节奏型服务不可用」）。唯一例外是**瞬时读错**：web 管理器
# 写文件不是原子的，那一瞬可能读到半截 JSON，此时用上一次成功的内存快照顶一下。


@runtime_checkable
class PatternSource(Protocol):
    """节奏型数据源协议：返回当前可用的模板列表。"""

    def patterns(self) -> list[StrumPattern]: ...


class ListPatternSource:
    """背靠一个固定列表的数据源（测试与嵌入方注入用）。"""

    def __init__(self, patterns: Sequence[StrumPattern]) -> None:
        self._patterns = list(patterns)

    def patterns(self) -> list[StrumPattern]:
        return list(self._patterns)


class DbPatternSource:
    """背靠文本数据库的数据源（默认实现）。

    ``patterns()`` 每次调用重新 ``load()``，所以 web 管理器的编辑下一次选型即生效。
    读失败时返回上一次成功的内存快照（web 管理器非原子写入窗口里的半截 JSON 是唯一
    现实成因）；从没成功读过（DB 缺失/损坏/非法记录）则异常照抛，由调用方判为服务
    不可用。
    """

    def __init__(self, repo: TemplateRepository) -> None:
        self._repo = repo
        self._last_good: list[StrumPattern] | None = None

    def patterns(self) -> list[StrumPattern]:
        try:
            loaded = [pattern for _id, pattern in self._repo.load()]
        except Exception as exc:
            if self._last_good is None:
                raise
            _log.warning(
                "模板数据库读取失败（%s）；本次选型沿用上一次成功加载的 %d 个模板",
                exc,
                len(self._last_good),
            )
            return list(self._last_good)
        self._last_good = loaded
        return list(loaded)


# 进程级当前源。None = 尚未取用/被重置，下次 get 时重建默认 DB 源。
_default_source: PatternSource | None = None


def _db_default_source() -> PatternSource:
    """默认数据源：库内自带的 templates.json。"""
    from .serialization import TemplateRepository

    return DbPatternSource(TemplateRepository())


def set_pattern_source(source: PatternSource | None) -> None:
    """注入数据源。``None`` 重置为默认 DB 源（下次取用时重建）。

    进程级全局状态，仅适用于单用户本地工具（如 web 管理器）。多进程并发安全不在
    范围内；gunicorn 各 worker 各配一份，幂等。
    """
    global _default_source
    _default_source = source


def get_pattern_source() -> PatternSource:
    """取当前数据源（默认：库内 templates.json）。"""
    global _default_source
    if _default_source is None:
        _default_source = _db_default_source()
    return _default_source


def _boom_chick_fallback(time_signature: tuple[int, int] = (4, 4)) -> StrumPattern:
    """取兜底模板：优先同拍号的 boom-chick，缺失时取同拍号任意短模板，再缺失则内联构造。

    - 当前数据源有同拍号 boom-chick → 用之；
    - 否则取同拍号任意 ``min_beats=1`` 模板；
    - 若同拍号全无（DB 里没这个拍号的模板），内联构造一个最小 4/4 boom-chick，绝不抛
      ``StopIteration``——极端场景塞个错拍号模板总比崩好。

    注意本函数不吞数据源异常：DB 缺失/损坏时 ``patterns()`` 抛错应当冒泡（那是部署
    问题，不是「没有候选」），由后端判为服务不可用。
    """
    def _find_in(source) -> StrumPattern | None:
        try:
            return next(p for p in source if p.name == "boom-chick" and p.time_signature == time_signature)
        except StopIteration:
            return next((p for p in source if p.time_signature == time_signature and p.min_beats == 1), None)

    found = _find_in(get_pattern_source().patterns())
    if found is not None:
        return found
    # 同拍号全无：内联 4/4 boom-chick 兜底（极端，保不崩）。
    return StrumPattern(
        name="boom-chick",
        grid_motif=(Stroke("D", 4),),
        motif_beats=1,
        min_beats=1,
        ideal_beats=(2, 4),
        sections=("verse",),
        style="folk",
        technique="strum",
        time_signature=(4, 4),
    )


# --- 打分权重（越大越劝退） ----------------------------------------------

W_SECTION = 2.5        # 段落不契合：当前段落不在模板 sections 里时的固定罚分
W_DENSITY = 4.0        # 每偏离目标密度 1.0 的代价（密度差 0..1，故实际惩罚 0..4）
W_IDEAL_BEATS = 1.5    # 拍数不在 ideal_beats 里时的罚分（鼓励「占几拍就用几拍周期」的模板）
W_TRUNCATION = 3.0     # 非整动机截断罚系数：grid_for 尾部截断占动机时值的比例（0..1）乘此系数。
                       # 截断出的「发明节奏」越占得多越罚——整动机对齐的模板优先。量级与
                       # W_DENSITY 同级：截断一半动机（ratio 0.5）≈ 偏离目标密度 0.375 拍的罚。
W_WHOLE_MOTIF = 2.0    # 整动机奖励：beats 恰等于 motif_beats 且 ideal_beats 是单元素 (motif_beats,)
                       # 的模板减分。这类「专属整动机」（如 4 拍周期的 pop D-DU-U-DU、4 拍 53231323
                       # 8 分分解）占满正好一个动机时最顺，奖励压住高密度通用短动机的密度优势。
                       # 只在「占满一个专属动机」时触发，不泛化到任意整数倍，避免跨技法倾斜。
W_STRUM_MUTED = 1.2    # 扫弦可行性：高音侧闷音（丢顶音）每个的罚分
W_INNER_MUTE = 1.0     # 扫弦可行性：内部闷音（扫弦要精确挡）每个的罚分
W_STYLE_MISMATCH = 5.0 # 风格不匹配：模板风格 != 请求风格时的固定罚分（不剔除，仅降级）
W_TAGS = 5.0           # 标签不匹配度（0..1）的代价系数。musicnn 接入后的主风格维度：
                       # 模板有 tags 且 ctx 给了 musicnn_tags 时，按 _tag_mismatch 加权匹配度
                       # 打分（全命中 0、全不匹配 5.0）。与 W_STYLE_MISMATCH 同量级——标签
                       # 维度主导但不一票否决。模板无 tags 或 ctx 无 musicnn_tags 时退回
                       # W_STYLE_MISMATCH（向后兼容）。
W_TECHNIQUE = 6.0      # 技法基线不符：段落技法基线与模板技法不一致时的固定罚分（段落级混排关键维度）
W_SECTION_STRUM = 2.0  # 段落技法先验（软罚）：主歌/尾奏（verse/outro）下扫弦模板的固定罚分。
                       # 弹唱惯例：主歌铺垫用分解、副歌爆发用扫弦，主歌不该直接上扫弦。软罚而非
                       # W_TECHNIQUE 重罚——只降顺位不排除：4 拍长和弦选分解（密度+整动机奖励
                       # 已如此），2 拍短和弦翻向 1 拍短分解动机（arpeggio placeholder 等，
                       # 放得进 2 拍和弦）；强证据下扫弦仍可胜出（musicnn rock/fast 标签、
                       # 实测极密 onset 把 rock 8th down 等重新拉回首位）。
                       # technique_baseline 显式给出（strum/fingerpicking/arpeggio，歌曲侧
                       # 实测证据）时跳过——两个维度不叠加，显式基线优先于静态段落先验；
                       # mixed/None（未提供基线）时正常生效。稀疏扫弦（boom-chick，密度 0.25）
                       # 不豁免：密度维度自然保住它在极疏场景的竞争力。
W_TECHNIQUE_SOFT = 2.0 # 技法基线近邻不符：fingerpicking 基线对真琶音（technique="arpeggio"）模板的
                       # 轻罚。fingerpicking（分解逐弦拨完整律动）与 arpeggio（一串音快速依次拨出的
                       # 单次手势）同为拨弦类但听感/用途不同：分解是段落主体，真琶音是收束手势
                       # （现有 arpeggio 模板都标 positions=("tail",)）。fingerpicking 基线时应优先
                       # 分解模板，但真琶音仍可凭 tail 奖励/段落契合在尾位胜出——轻罚（2.0 <
                       # W_POSITION_TAIL_BONUS+其他契合项）而非同 W_TECHNIQUE 重罚（6.0 会连尾位
                       # 收束也压死，回到「技法基线二元」的旧缺陷）。arpeggio 基线保持宽匹配
                       # （分解/琶音均 0 罚）不变。
W_COHERENCE = 0.8      # 连贯性：与相邻和弦密度变化方向不一致时的罚分
W_TIME_SIG_MISMATCH = 50.0  # 跨拍号借用重罚（分母不同，/4 vs /8）：模板自带 time_signature 与
                            # 请求拍号不一致时加。远大于其他维度总和（~20），让跨拍号模板实际不入候选
                            # （同拍号优先）；仅当同拍号模板全被拍数门槛硬筛掉时才可能被选——极端兜底
                            # 场景下塞个错拍号模板总比崩好（保 boom-chick fallback 不变量）。
                            # 仅 ctx.time_signature 显式给定时触发，4/4 缺省不干预。6/8 歌曲据此
                            # 选 6/8 专属模板、拒 4/4。
W_TIME_SIG_NUMERATOR = 6.0  # 同分母跨分子借用轻罚（如 4/4 模板用于 3/4 歌曲）：tick 单位一致
                            # （/4 一拍 4 tick），短动机栅格平铺到任意拍数都合法，只是周期相位
                            # 会错位（4 拍周期 vs 3 拍小节），降为与 W_TECHNIQUE 同量级的固定罚
                            # 而非重罚。少见拍号（3/4）专属模板有限，4/4 短模板兜底；专属模板
                            # 拍号契合 0 罚仍优先。
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
BPM_LOW_THRESHOLD = 70    # 慢速门槛：bpm 低于此值时，高密度连续扫弦按 W_BPM_LOW 轻微罚分（慢歌分解更顺）。
# 可演奏性按**绝对发音速率**建模（见 pattern_cost 的 BPM 段）。
ONSETS_PER_BEAT_AT_FULL_DENSITY = 4.0  # density 1.0 = 十六分音符 = 4 发音/拍。
MAX_ONSETS_PER_SEC = 5.0   # **分解/拨弦**可持续的发音速率上限（发音/秒）。标定：指弹分解的
                           # 舒适区约 3–4 发音/秒，5.0 之上开始明显吃力（八分音符在 150 BPM
                           # 拍速下正好 5.0；176 BPM 的八分是 5.9，应让位更疏的型）。
MAX_ONSETS_PER_SEC_STRUM = 10.0  # **扫弦**的上限远高于逐弦拨：拨片连续上下扫，十六分音符在
                           # 120 BPM 就是 8 发音/秒，是流行扫弦的常规动作，不能按分解的尺度罚。
                           # 分开标定是必须的——单一上限要么放过 176 BPM 的八分分解，要么把
                           # 常规的十六分扫弦也罚掉（后者会让 onset_density 驱动的密度响应失效）。
W_RATE_HIGH = 2.0          # 超上限每 1 发音/秒的罚分。3/4 分解在 176 BPM 下超出 0.87 → 罚 1.74，
                           # 足以压过 W_SECTION_STRUM(2.0) 给分解的净优势（实测该处约 1.33）。
W_BPM_LOW = 1.5        # 低 BPM 下高密度连续扫弦的罚分。慢歌用分解更顺，连续扫弦在低 BPM 下听起来
                       # 「冲」，与技法基线互补（基线管整段扫/拆，此维度管密度细节）。
W_ONSET_AUDIO = 0.6    # 实测起音密度在目标密度融合里的权重（静态段落表占 1-W=0.4）。音频主导：
                       # 同一 "verse" 标签下实际疏密可以差很远（民谣 verse vs rock verse），静态表
                       # 只表达段落性格先验（chorus 偏密）；0.6 让实测拉动目标但不至于完全接管
                       # （musicnn 段落切片误差、静音边界混入 onsets 都会污染实测值）。


def _beats_per_bar(time_signature: tuple[int, int]) -> int:
    """一小节几拍：``/4`` 拍号 = 分子（4/4→4、3/4→3）；``/8`` 拍号 = 分子 // 3（6/8→2、3/8→1）。

    ``/8`` 拍号下「一拍」= 附点 8 分，一小节的 8 分个数（分子）除以 3 得附点拍数。
    供 :func:`_target_density` 把「满小节」阈值按拍号归一化（6/8 满 2 拍 vs 4/4 满 4 拍）。
    """
    num, den = time_signature
    return num if den == 4 else num // 3


def _target_density(
    section: str,
    beats: int,
    time_signature: tuple[int, int] = (4, 4),
    onset_density: float | None = None,
) -> float:
    """该段落 + 拍数下的目标节奏密度。

    副歌偏密、主歌偏疏；占拍数少时单拍密度略高（要在一拍内把动机弹完），
    占满一小节及以上时略低（可以慢慢扫）。数值经 ``rhythm_main.py`` 基准集标定。

    ``beats >= 一小节拍数`` 的「满小节」阈值按拍号归一化：6/8 满 2 拍、4/4 满 4 拍，
    避免裸 ``beats`` 阈值把 6/8 的 2 拍小节误判为「半小节」而压低密度。``beats <= 1``
    的单拍语义与拍号无关，保持绝对。

    ``onset_density`` 给定时（该段落音频实测起音密度，已由调用方归一化到静态表值域）
    与静态表值加权融合：``W_ONSET_AUDIO * onset + (1-W_ONSET_AUDIO) * d``。音频主导、
    段落性格保留；``None``（默认）时纯静态表，与旧版行为一致。
    """
    base = {"verse": 0.35, "prechorus": 0.5, "chorus": 0.75, "bridge": 0.45, "outro": 0.3}
    d = base.get(section, 0.5)
    # 占拍少 -> 单拍密度略高；占满一小节及以上 -> 略低。
    if beats <= 1:
        d += 0.1
    elif beats >= _beats_per_bar(time_signature):
        d -= 0.05
    if onset_density is not None:
        # 实测起音密度与静态表值域对齐后再融合（调用方归一化失手时不至于爆炸）。
        measured = max(0.0, min(1.0, onset_density))
        d = W_ONSET_AUDIO * measured + (1.0 - W_ONSET_AUDIO) * d
    return max(0.0, min(1.0, d))


def _tag_mismatch(
    pattern_tags: tuple[str, ...],
    musicnn_tags: tuple[tuple[str, float], ...],
) -> float:
    """musicnn 标签集与模板标签集的「不匹配度」∈ [0, 1]。

    0 = 全命中（musicnn 高置信度标签都落在模板 tags 里），1 = 全不匹配。``pattern_cost``
    乘 ``W_TAGS`` 计入代价。

    用置信度加权的 Jaccard 变体：musicnn 给的 ``(tag, likelihood)`` 中 likelihood 高的
    标签权重大。匹配度 = ``sum(likelihood for tag in musicnn if tag in pattern_tags) /
    sum(all likelihood)``，不匹配度 = ``1 - 匹配度``。

    模板标签无权重（``pattern_tags`` 同等重要），musicnn 标签带置信度。这样 musicnn 的
    ``guitar 0.85 + slow 0.7`` 命中标了 ``guitar/slow`` 的分解模板时大幅减罚，而低置信度
    命中影响小——正好实现「慢歌吉他拉分解、有鼓快歌拉扫弦」。不用纯交集大小（偏向标签
    多的模板）也不用纯 Jaccard（忽略置信度）。

    Parameters
    ----------
    pattern_tags
        模板标签集（无权重）。
    musicnn_tags
        musicnn 输出的带置信度标签集，每项 ``(tag, likelihood)``，likelihood ∈ 0..1。
    """
    if not pattern_tags or not musicnn_tags:
        return 1.0  # 任一为空视为全不匹配（调用方 pattern_cost 的分支已挡，不至此）
    pat_set = set(pattern_tags)
    total_w = sum(w for _, w in musicnn_tags)
    if total_w <= 0:
        return 1.0
    hit_w = sum(w for t, w in musicnn_tags if t in pat_set)
    return 1.0 - (hit_w / total_w)


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

    # 风格维度：标签匹配度（musicnn 接入后的主维度）+ style fallback。
    # 模板有 tags 且 ctx 给了 musicnn_tags -> 按标签匹配度打分（_tag_mismatch * W_TAGS），
    #   style 不再介入。musicnn 的 guitar/slow 拉分解、drums/fast/rock 拉扫弦即由此实现。
    # 模板无 tags 或 ctx 无 musicnn_tags -> 退回原 style 罚分（向后兼容旧调用/旧 DB）。
    #   不剔除，只降级（允许跨风格借用，但排在后面）。
    if pattern.tags and ctx.musicnn_tags:
        cost += _tag_mismatch(pattern.tags, ctx.musicnn_tags) * W_TAGS
    elif pattern.style != style:
        cost += W_STYLE_MISMATCH

    # 技法基线（段落级混排关键维度）：基线明确时，技法不符的模板罚分。
    # 不剔除--允许在基线为 strum 时仍选出分解（若它密度/段落契合远胜），只压低顺位。
    # - 扫弦 vs 拨弦类（分解/琶音）互斥，罚满 W_TECHNIQUE；
    # - "fingerpicking" 基线额外给真琶音（technique="arpeggio"）轻罚 W_TECHNIQUE_SOFT：
    #   分解是段落主体、真琶音是收束手势，主体应优先选分解模板，但真琶音仍可凭
    #   tail 奖励/段落契合在尾位胜出（不会被重罚压死）；
    # - "arpeggio" 基线宽匹配：分解与琶音均不罚，仅扫弦罚——保持 musicnn
    #   guitar/slow 段落标签的既有链路行为不变。
    if technique_baseline in ("fingerpicking", "arpeggio") and pattern.is_strum:
        cost += W_TECHNIQUE
    elif technique_baseline == "strum" and not pattern.is_strum:
        cost += W_TECHNIQUE
    elif technique_baseline == "fingerpicking" and pattern.is_arpeggio:
        cost += W_TECHNIQUE_SOFT
    elif (
        technique_baseline in (None, "mixed")
        and section in ("verse", "outro")
        and pattern.is_strum
    ):
        # 段落技法先验（软罚）：无显式基线时，弹唱惯例「主歌/尾奏铺垫用分解、副歌爆发
        # 用扫弦」自动生效——verse/outro 下扫弦模板加 W_SECTION_STRUM 软罚。与上面的
        # 显式基线互斥（elif）：technique_baseline 是歌曲侧实测证据，优先于静态段落
        # 先验，两个维度不叠加——strum 基线的摇滚主歌不被静态先验压、fingerpicking
        # 基线已有 W_TECHNIQUE 重罚不重复加。mixed 语义是「主歌拆副歌扫之类混排，
        # 不在此层罚分」——与 None 同样不干预技法，段落先验正常生效。
        # 软罚降顺位不排除：2 拍短和弦翻向 1 拍短分解动机；强风格证据（musicnn
        # rock/fast）或实测极密 onset 下 rock 8th down 等扫弦仍可胜出。
        cost += W_SECTION_STRUM

    # 密度贴合：用**实例化后**的密度（grid_for(beats) 平铺/截断后的真实输出），
    # 而非动机密度——非整动机拍数下截断前缀的密度与动机密度可能不同，评分必须
    # 对齐实际演奏的栅格。「满小节」阈值按拍号归一化（6/8 满 2 拍 vs 4/4 满 4 拍）。
    # ctx.onset_density 给定时（段落音频实测起音密度）目标密度与静态表融合。
    ts = ctx.time_signature or (4, 4)
    target = _target_density(section, beats, ts, ctx.onset_density)
    density = pattern.instantiated_density(beats)
    cost += abs(density - target) * W_DENSITY

    # 截断罚：beats 非 motif_beats 整数倍时尾部是动机前缀截断（「发明节奏」），
    # 按截断比例罚分——整动机对齐的模板优先，截断占比高的靠后。与
    # W_WHOLE_MOTIF（整动机奖励）互补：那个只覆盖「专属整动机」单元素
    # ideal_beats 的场景，此罚覆盖所有非整倍数截断。
    cost += pattern.truncation_ratio(beats) * W_TRUNCATION

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

    # 拍号契合：模板自带 time_signature，与 ctx 拍号不一致时分两档：
    # - 分母不同（/4 vs /8）：tick 单位不同（16 分位 vs 8 分位），栅格语义不兼容，
    #   维持 W_TIME_SIG_MISMATCH 重罚到实际不入候选；
    # - 分母相同、分子不同（4/4 模板用于 3/4 歌曲）：tick 单位一致，栅格可平铺
    #   （1 拍短动机平铺到任意拍数都干净），降为 W_TIME_SIG_NUMERATOR 轻罚——
    #   3/4 等少见拍号的专属模板有限，4/4 短模板（boom-chick、5323 等）作跨分子
    #   弹药兜底；4 拍周期模板与 3 拍小节的相位错位由 W_TRUNCATION 截断罚表达。
    #   反向（3/4 模板用于 4/4 歌曲）同样吃此罚垫底，4/4 默认选型行为不变。
    # ctx.time_signature 为 None 时按 4/4 处理（与旧式默认调用一致），
    # 故 6/8 模板在无拍号的 4/4 默认选型里吃重罚、不参与——只有显式给 (6,8) 才用 6/8 模板。
    ctx_ts = ctx.time_signature or (4, 4)
    if pattern.time_signature != ctx_ts:
        if pattern.time_signature[1] != ctx_ts[1]:
            cost += W_TIME_SIG_MISMATCH
        else:
            cost += W_TIME_SIG_NUMERATOR

    # BPM 可演奏性：仅 ctx 显式给 bpm 时介入。
    # ★ 高 BPM 侧按**绝对发音速率**（发音/秒）罚，不按相对密度 —— 相对密度会漏掉
    #   最常见的一档：八分音符分解的 density 恰为 0.5，而旧阈值是 `density > 0.5`，
    #   于是 (0.5-0.5)*W = 0，**任何 BPM 下八分音符都零罚分**（实测 3/4 分解
    #   `532132 (8分)` 在 bpm 60/88/120/176 下选型结果完全一样）。可演奏性取决于
    #   每秒拨几次，与记谱单位无关：同一个八分型在 176 BPM 是 5.9 发音/秒（弹不动），
    #   在 88 BPM 只有 2.9（轻松）。故改用 `density × 4 × bpm / 60`（density 1.0
    #   = 十六分 = 4 发音/拍）。
    #   低 BPM 侧保持相对密度语义：那里管的是「慢歌连续扫弦听着冲」，是口味不是极限。
    if ctx.bpm is not None:
        onsets_per_sec = density * ONSETS_PER_BEAT_AT_FULL_DENSITY * ctx.bpm / 60.0
        ceiling = MAX_ONSETS_PER_SEC_STRUM if pattern.is_strum else MAX_ONSETS_PER_SEC
        if onsets_per_sec > ceiling:
            cost += (onsets_per_sec - ceiling) * W_RATE_HIGH
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

    扫弦 (Stroke) 与休止 (Rest) 格不动（duration/accent 原样保留）。Pluck 格：``role`` 非 None
    且 voicing 可用时，调 ``role.resolve(voicing)`` 得弦号填入 ``strings``；解析失败或无
    voicing 时，``strings`` 保持 ``None``（拨弦但弦未定，不阻塞输出）。重建时 ``duration``
    与 ``accent`` 一并保留。模板层原 Pluck 不被修改--本函数返回新栅格，event 持有实例化后的栅格。
    ``ticks_per_beat`` 从输入栅格透传，维持 6/8 等 ``/8`` 拍号栅格的不变量。
    """
    if voicing is None:
        return grid  # 无 voicing，Pluck.strings 保持 None。
    new_cells: list[Cell] = []
    for c in grid.cells:
        if isinstance(c, Pluck) and c.role is not None:
            resolved = c.role.resolve(voicing)
            # resolved 为 None 表示该 role 在此 voicing 上无法解析（如省了五音还取五音）。
            # 保留 role、strings=None，栅格结构不变，仅弦序未定。duration/accent 保留。
            new_cells.append(Pluck(role=c.role, strings=resolved, duration=c.duration, accent=c.accent))
        else:
            new_cells.append(c)
    return RhythmGrid(tuple(new_cells), ticks_per_beat=grid.ticks_per_beat)


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
        段落技法基线，``"strum" / "fingerpicking" / "arpeggio" / "mixed" / None``。基线明确时，
        扫弦 vs 拨弦类不符罚 ``W_TECHNIQUE``；``"fingerpicking"`` 额外给真琶音轻罚
        ``W_TECHNIQUE_SOFT``；``"arpeggio"`` 宽匹配：分解与琶音均不罚；
        ``mixed`` / ``None``（默认）不罚。``ctx`` 给定时此项被覆盖。
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
    targets = [_target_density(eff_section, b, ctx.time_signature or (4, 4), ctx.onset_density) for _, b in progression]
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
            # 退路：用同拍号 boom-chick（min_beats=1）兜底，保证总有输出。
            fallback = _boom_chick_fallback(ctx.time_signature or (4, 4))
            events.append(_instantiate_event(chord, beats, fallback, voicings[i]))
            continue

        # 稳定排序：代价相同时保持数据源里的模板顺序，结果确定。
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
    targets = [_target_density(eff_section, b, ctx.time_signature or (4, 4), ctx.onset_density) for _, b in progression]
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
            # 拍数门槛兜底（理论不会发生）：塞同拍号 boom-chick 单候选，保证 DP 有路径。
            fallback = _boom_chick_fallback(ctx.time_signature or (4, 4))
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


# --- 全曲统一选型（「一首歌 1-2 个基本节奏型」）────────────────────────────

_FAMILY_STRUM = "strum"
"""技法族「扫弦」。"""
_FAMILY_PLUCK = "pluck"
"""技法族「拨弦」：分解（fingerpicking）与琶音（arpeggio）同族。"""

W_FAMILY_MARGIN = 2.0
"""技法族保留门槛：某族在任一段落里落后该段最优族不超过此值时保留。

全曲统一选型的目标是「一首歌最多 1-2 个基本节奏型」（一分解一扫弦），而不是
逐和弦 / 逐段落另选新模板。技法族先按此门槛裁剪：抒情歌（musicnn 的
guitar/slow/soft 标签 + 低起音密度）下扫弦族处处落后拨弦族超过此值 -> 整族被
裁掉，全曲只留一个分解模板。量级与 ``W_SECTION_STRUM`` 同阶（软性偏好而非
一票否决），调此值即调「多容易上第二族」。"""

W_SHORT_FALLBACK = 1.0
"""短和弦兜底罚：基础模板放不下的和弦（``beats < min_beats``）改用同族短模板时
每个和弦的代价。让「覆盖更多和弦」的基础模板在分数接近时胜出——基础模板覆盖
得越全，全曲实际用到的模板数越少。"""


def _technique_family(pattern: StrumPattern) -> str:
    """模板归属的技法族：扫弦一族、分解与琶音合为拨弦一族。

    两族对应「一种扫弦 + 一种分解」的基本节奏型二分。琶音（arpeggio）与分解
    （fingerpicking）同为拨弦类、听感与用途接近，合成一族才能让「抒情歌只留
    一个分解模板」真正落到一个模板上。
    """
    return _FAMILY_STRUM if pattern.is_strum else _FAMILY_PLUCK


def plan_song_rhythm(
    progression: Sequence[tuple[str, int]],
    fretboard: "Fretboard",
    *,
    ctx: SelectionContext | None = None,
    chord_ctxs: Sequence[SelectionContext] | None = None,
    max_families: int = 2,
    section: str = "chorus",
    style: str = "pop",
    technique_baseline: TechniqueBaseline = None,
    max_stretch: int = 4,
) -> list[RhythmEvent]:
    """**全曲统一选型**：为整首歌选一套（至多两族各一个）基本节奏型。

    与 :func:`arrange_progression`（逐和弦 Top-K + DP）的根本区别在**作用域**：
    后者在每个和弦上独立挑最优模板，DP 的转移代价（换模板 1.5、技法跳变 4.0）
    压不住密度 / 截断 / 标签这些逐和弦维度（量级更大），于是同一段落的和弦会
    翻出好几个模板——4 拍和弦用完整分解、2 拍和弦翻短分解、段尾再翻收束琶音，
    一首歌下来 5-7 种节奏型。本函数反过来：**先定全曲的节奏型集合，再把和弦
    分配到集合里的某一个**，从机制上保证输出种类受控。

    三步：

    1. **裁技法族**（``max_families``，默认 2）。按 :func:`_technique_family`
       把模板分成扫弦族与拨弦族，逐段落算各族的最优分；某族只要在**某个**段落
       里能追平该段最优族（差距 <= ``W_FAMILY_MARGIN``）就保留——两族本就分工
       （主歌分解、副歌扫弦），各自在对方的主场落后是正常的。抒情歌（musicnn
       guitar/slow/soft 标签 + 低起音密度）下扫弦族在每个段落都追不平，整族
       被裁掉——全曲只剩分解，正是「抒情歌连扫弦都不需要」。
    2. **段落选族 + 各族选基础模板**。段落按「族在该段的代表分」选族；基础模板
       只在**该族实际要弹的和弦**（其被分配到的段落）上评分——在族用不到的段落
       上评分会把基础模板拽向别处的口味。覆盖不到的和弦（``beats < min_beats``）
       改用该族短模板计分并吃 ``W_SHORT_FALLBACK``，故覆盖越全的基础模板越占优。
    3. **逐和弦分配**。取该段所辖族的基础模板；和弦拍数放不下它时
       （``beats < min_beats``）退到该族的**短模板**（``min_beats == 1``，任何
       拍数都放得下）——短模板全曲复用同一个，不是每个短和弦另选。

    因此全曲实际用到的模板数最多为 ``2 × max_families``（基础 + 短，每族各一），
    常见为 2（一分解一扫弦）或 1（纯分解）。

    Parameters
    ----------
    progression
        整首歌的和弦进行 ``[(和弦符号, 占拍数), ...]``，**按时间顺序跨段落**传入
        （不是逐段调用——作用域是全曲，逐段调用等于退回段落级选型）。
    fretboard
        ``pytheory.Fretboard``。
    ctx
        全局上下文（拍号 / BPM / 风格 / 跨度约束）。``None`` 时由 ``section`` /
        ``style`` / ``technique_baseline`` / ``max_stretch`` 组装。
    chord_ctxs
        逐和弦上下文，与 ``progression`` 等长同序，承载该和弦所属段落的
        ``section`` / ``musicnn_tags`` / ``onset_density``。段落差异（主歌该分解、
        副歌该扫弦）全靠它表达——这正是「段落决定用两个基本节奏型里的哪一个」。
        ``None`` 时全曲用同一个 ``ctx``。
    max_families
        保留的技法族上限，默认 2（一分解一扫弦）。传 1 则强制全曲单一技法族。
    section / style / technique_baseline / max_stretch
        ``ctx`` 为 ``None`` 时的降级参数，语义同 :func:`arrange_progression`。

    Returns
    -------
    list[RhythmEvent]
        与 ``progression`` 等长、同序。
    """
    if ctx is None:
        ctx = SelectionContext(
            section=section, style=style,
            technique_baseline=technique_baseline, max_stretch=max_stretch,
        )
    if max_families < 1:
        raise ValueError(f"max_families 至少为 1，实际 {max_families}")
    progression = list(progression)
    n = len(progression)
    if n == 0:
        return []

    if chord_ctxs is None:
        ctxs: list[SelectionContext] = [ctx] * n
    else:
        ctxs = list(chord_ctxs)
        if len(ctxs) != n:
            raise ValueError(
                f"chord_ctxs 长度 {len(ctxs)} 与 progression 长度 {n} 不一致"
            )

    stretch = ctx.max_stretch
    ts = ctx.time_signature or (4, 4)
    beats_of = [b for _, b in progression]

    # 候选池：同拍号 + 位置中立的模板。全曲统一选型下没有「跨拍号借用兜底」的必要
    # ——同拍号模板必然存在（4/4 有 boom-chick，6/8、3/4 各有专属），故直接硬筛，
    # 省得让 W_TIME_SIG_MISMATCH 的重罚在均值里制造噪声。极端情况（数据源里没有
    # 同拍号模板）退回全量，交由 pattern_cost 的拍号罚分排序。
    #
    # 排除标了 positions 的模板（如 arpeggio cadence (tail)）：那是段落末和弦的
    # **收束手势**，不是基本节奏型。放进基础模板候选会让全曲基调变成一个收尾动作
    # （实测：抒情歌的 verse/outro 整段被 cadence 占满），恰恰与「全曲统一到一两个
    # 基本节奏型」的目标相反。收束手势属于「点缀」，本函数只负责定基调。
    pool = [
        p for p in _default_source.patterns()
        if p.time_signature == ts and not p.positions
    ]
    if not pool:
        pool = [p for p in _default_source.patterns() if not p.positions]
    if not pool:
        pool = list(_default_source.patterns())
    if not pool:
        return [
            _instantiate_event(c, b, _boom_chick_fallback(ts), None)
            for c, b in progression
        ]

    voicings = [_resolve_voicing(c, fretboard, stretch) for c, _ in progression]
    muted = [_voicing_muted(v, fretboard) for v in voicings]

    def mean_cost(pat: StrumPattern, idxs: Sequence[int]) -> float:
        """``pat`` 在给定和弦下标集合上的平均代价；空集为 ``inf``。

        全曲统一选型不逐和弦看邻居（``density_neighbor_delta=None``）——连贯性
        由「全曲就这几个模板」这一结构性约束保证，不再需要逐和弦的密度方向项。
        """
        if not idxs:
            return float("inf")
        return sum(
            pattern_cost(
                pat,
                beats=beats_of[i],
                muted=muted[i],
                density_neighbor_delta=None,
                ctx=ctxs[i],
            )
            for i in idxs
        ) / len(idxs)

    all_idx = list(range(n))

    fam_pool: dict[str, list[StrumPattern]] = {_FAMILY_PLUCK: [], _FAMILY_STRUM: []}
    for p in pool:
        fam_pool[_technique_family(p)].append(p)
    fam_pool = {f: ps for f, ps in fam_pool.items() if ps}
    if not fam_pool:
        fallback = _boom_chick_fallback(ts)
        return [
            _instantiate_event(c, b, fallback, voicings[i])
            for i, (c, b) in enumerate(progression)
        ]

    # 段落分组（保持出现顺序，保证结果确定）。
    sections_in_order: list[str] = []
    idxs_by_section: dict[str, list[int]] = {}
    for i in all_idx:
        sec = ctxs[i].effective_section
        if sec not in idxs_by_section:
            idxs_by_section[sec] = []
            sections_in_order.append(sec)
        idxs_by_section[sec].append(i)

    def pick_short(fam: str, idxs: Sequence[int]) -> StrumPattern | None:
        """族内 ``min_beats==1`` 的模板中，在 ``idxs`` 上平均代价最低者。

        ``min_beats==1`` 保证它放得进任何拍数，是「基础模板放不下的短和弦」的
        兜底。族内若无此类模板（数据源不全）返回 ``None``。
        """
        cands = [p for p in fam_pool[fam] if p.min_beats <= 1]
        if not cands or not idxs:
            return None
        return min(cands, key=lambda p: (mean_cost(p, idxs), p.name))

    # ── 1. 裁技法族：在某段落里与最优族差距不超过 W_FAMILY_MARGIN 即保留 ──
    # fam_section_cost[fam][sec] = 该族在该段落里最好的模板分（代表该族在此的上限）。
    fam_section_cost: dict[str, dict[str, float]] = {}
    for fam, cands in fam_pool.items():
        per_sec: dict[str, float] = {}
        for sec, idxs in idxs_by_section.items():
            # 只看能覆盖该段落至少一个和弦的模板（min_beats 硬门槛）。
            per_sec[sec] = min(
                (
                    mean_cost(p, [i for i in idxs if beats_of[i] >= p.min_beats])
                    for p in cands
                    if any(beats_of[i] >= p.min_beats for i in idxs)
                ),
                default=float("inf"),
            )
        fam_section_cost[fam] = per_sec

    def best_sec_gap(fam: str) -> float:
        """该族相对逐段最优族的**最小**落后幅度（即它最拿手的段落差多少）。

        保留判据看这个值而非最差段落：两个基本节奏型本就是分工的——主歌该分解、
        副歌该扫弦，扫弦族在主歌落后、分解族在副歌落后都是**正常**的。只要某族
        在**某个**段落里能追平最优族（gap <= ``W_FAMILY_MARGIN``），它就值得保留
        （那个段落用它）。反之，抒情歌里扫弦族在**每个**段落都大幅落后拨弦族，
        最小 gap 也超阈值 -> 整族裁掉，全曲只剩一个分解模板。
        """
        gaps = []
        for sec in sections_in_order:
            best = min(
                (c.get(sec, float("inf")) for c in fam_section_cost.values()),
                default=float("inf"),
            )
            gaps.append(fam_section_cost[fam].get(sec, float("inf")) - best)
        return min(gaps, default=float("inf"))

    kept = [f for f in fam_section_cost if best_sec_gap(f) <= W_FAMILY_MARGIN]
    if not kept:
        # 保底：全族都不达标时留最拿手段落差距最小的一族。
        kept = [min(fam_section_cost, key=lambda f: (best_sec_gap(f), f))]

    # 族数上限：超出时按最拿手段落差距裁掉较差的族（确定性排序）。
    if len(kept) > max_families:
        kept.sort(key=lambda f: (best_sec_gap(f), f))
        kept = kept[:max_families]

    # ── 2. 段落选族：段落决定用两个基本节奏型里的哪一个 ──
    section_family: dict[str, str] = {
        sec: min(kept, key=lambda f: (fam_section_cost[f].get(sec, float("inf")), f))
        for sec in sections_in_order
    }
    # 该族实际要弹的和弦（其被分配到的段落的全部和弦）。基础模板只在这些和弦上
    # 评分——在族根本用不到的段落上评分，会把基础模板拽向别处的口味（实测：副歌
    # 扫弦族的基础模板被主歌和弦拖成 verse 向的 folk D-DU）。
    fam_idx: dict[str, list[int]] = {f: [] for f in kept}
    for sec, fam in section_family.items():
        fam_idx[fam].extend(idxs_by_section[sec])

    # ── 3. 基础模板 + 短模板：族内按「实际要弹的和弦」选 ──
    base_of: dict[str, StrumPattern] = {}
    short_of: dict[str, StrumPattern] = {}
    for fam in kept:
        idxs = fam_idx[fam]
        cands = fam_pool[fam]
        short = pick_short(fam, idxs)

        def base_score(p: StrumPattern, idxs=idxs, short=short) -> float:
            covered = [i for i in idxs if beats_of[i] >= p.min_beats]
            if not covered:
                return float("inf")
            score = mean_cost(p, covered)
            missing = [i for i in idxs if beats_of[i] < p.min_beats]
            if missing and short is not None:
                # 放不下的和弦按短模板计分，再按缺口比例吃 W_SHORT_FALLBACK——
                # 覆盖越全的基础模板越占优，全曲模板数因此更少。
                fallback = mean_cost(short, missing)
                score = (
                    score * len(covered) + fallback * len(missing)
                ) / len(idxs) + W_SHORT_FALLBACK * (len(missing) / len(idxs))
            return score

        base = min(cands, key=lambda p: (base_score(p), p.name))
        base_of[fam] = base
        # 短模板定稿：按基础模板实际覆盖不到的和弦重选，贴合它要顶替的那些和弦。
        gap = [i for i in idxs if beats_of[i] < base.min_beats]
        chosen = pick_short(fam, gap) if gap else None
        if chosen is not None:
            short_of[fam] = chosen

    # ── 4. 逐和弦分配：段落选族，族内取基础模板，放不下退同族短模板 ──
    events: list[RhythmEvent] = []
    for i, (chord, beats) in enumerate(progression):
        fam = section_family[ctxs[i].effective_section]
        base = base_of[fam]
        pat = base if beats >= base.min_beats else short_of.get(fam, base)
        events.append(_instantiate_event(chord, beats, pat, voicings[i]))
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
