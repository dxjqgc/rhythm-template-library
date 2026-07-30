"""扫弦节奏型的数据模型。

所有节奏型落在 **16 分音符时值**上：一拍 = 4 个 16 分位置，占 N 拍的和弦对应总时值 4N。
栅格里每个动作（扫弦/拨弦/休止）自带 ``duration``（占多少个 16 分位置），直接表达
该动作的时值，不再用空格位置推断。

栅格里每格取值是三种「右手动作/休止」之一：

- ``Stroke("D")`` - 下扫（down），由低音弦往高音弦扫，最常用的强拍动作；
- ``Stroke("U")`` - 上扫（up），高音弦往低音弦回扫，常落在弱拍的「与」上；
- ``Pluck(...)`` - 拨弦/琶音（arpeggio），一次拨一根或几根弦。分解节奏型的基本
  动作。与扫弦是**不同**的右手动作，故单立一个类型，不挤进 ``Stroke``；二者可混排
  在同一栅格里（段落级混排）。
- ``Rest(...)`` - 休止（真静默），该时段不发声。休止本身也是一种「音符」，有自己的时值。

每个动作的 ``duration`` 显式记录它占多少个 16 分位置：发音动作的 duration = 音持续响多久，
休止的 duration = 静默多久。延续与断音都由动作自身的 duration 控制，符合乐理。

一个节奏型模板以「完整动机」（``grid_motif``，跨 ``motif_beats`` 拍）存储，选型时按
和弦拍数平铺/截断成完整栅格。动机可跨 1/2/4 拍--1 拍动机（总时值 4）对扫弦小单元够用，
2 拍动机（总时值 8）表达 53231323 这类 8 音分解，4 拍动机（总时值 16）表达 pop D-DU-U-DU
完整周期。占拍数不够 ``min_beats`` 的模板由拍数门槛一票否决。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from .string_role import StringRole


Position = Literal["head", "middle", "tail"]
"""和弦在段落中的位置。

- ``"head"``    段落首和弦；实际演奏中一般**不做特殊处理**，故无模板标 ``positions=("head",)``，
              head 位置对所有模板位置中立。
- ``"middle"``  段落中间和弦。
- ``"tail"``   段落末和弦；常有收束处理（如琶音收尾），是位置维度的重点。

模板的 ``positions`` 标签为空时表示位置中立（所有位置都不罚）；非空时仅在这些位置
0 罚分、其他位置罚 ``W_POSITION``。这样不强迫所有模板都标位置，与 ``sections`` 同构。
"""


@dataclass(frozen=True)
class Stroke:
    """一次右手扫弦动作。

    Attributes
    ----------
    direction
        ``"D"`` 下扫（低->高音弦，强拍常用）/ ``"U"`` 上扫（高->低音弦，弱拍回扫）。
        首期不含切音、闷音、击勾等高级技法--只此两种。
    duration
        该动作占多少个 **16 分音符位置**（1=16分、2=8分、4=四分...）。发音持续这么久。
    """

    direction: Literal["D", "U"]
    duration: int = 1

    def __str__(self) -> str:
        return self.direction


@dataclass(frozen=True)
class Pluck:
    """一次右手拨弦/琶音动作。

    与 :class:`Stroke` 是**不同的右手动作**：扫弦是「一次扫过多根弦」，拨弦是
    「一次拨一根或几根指定的弦」。分解节奏型的基本单元。

    弦序用「弦角色」(``role``) 表达，而非固定弦号--分解的弦序随和弦走，固定弦号
    只对单一和弦准。模板里只填 ``role``（``Root()`` / ``Fifth('avoid_bass')`` /
    ``TopN(2,'comfortable')`` 等），选型时由 :func:`rhythm_pattern.string_role`
    按当前和弦 voicing 实例化成具体弦号填进 ``strings``。详见 :mod:`string_role`。

    Attributes
    ----------
    role
        弦角色，描述「拨什么」(音级/音区/弦组/音距约束)。模板层填写。
        ``None`` 表示占位拨弦（不指定弦），仅用于节奏骨架测试模板。
    strings
        实例化后的具体弦号下标元组（``0`` = 最低音弦，与 :mod:`chord_fingering`
        ``Fingering.positions`` 同序）。模板层为 ``None``；选型器实例化后填入。
        运行时若 ``role`` 解析失败（voicing 无该音级等），保持 ``None`` 表示「拨但弦未定」。
    duration
        该动作占多少个 **16 分音符位置**。
    """

    role: "StringRole | None" = None
    strings: tuple[int, ...] | None = None
    duration: int = 1

    def __str__(self) -> str:
        return "P"


@dataclass(frozen=True)
class Rest:
    """一次休止（真静默），占 ``duration`` 个 16 分音符位置。

    休止本身就是一种「音符」--有自己的时值、不发声。栅格层用 ``Rest`` 显式表达，
    不再用 ``None`` 格兼表「延续/休止」两种语义。延续（音持续）由发音动作自身的
    ``duration`` 表达，休止由 ``Rest`` 表达，职责单一、符合乐理。
    """

    duration: int = 1

    def __str__(self) -> str:
        return "."


# 栅格一格的取值：扫弦动作 / 拨弦动作 / 休止。``Stroke`` 与 ``Pluck`` 是不同类型，
# 混排栅格里 isinstance 分流。放此处定义使三者皆已声明。
Cell = Stroke | Pluck | Rest


@dataclass(frozen=True)
class RhythmGrid:
    """一段节奏型在 16 分栅格上的表达。

    每格代表一个动作（扫弦/拨弦/休止），自带 ``duration``（占多少个 16 分位置）。
    栅格所有动作 ``duration`` 之和必为 4 的整数倍（整数拍）。

    Attributes
    ----------
    cells
        按时间顺序的动作序列。下标 0 = 第一拍第一个 16 分位置。
    """

    cells: tuple[Cell, ...]

    def __post_init__(self) -> None:
        if not self.cells:
            raise ValueError("栅格不能为空")
        for c in self.cells:
            if c.duration < 1:
                raise ValueError(f"每个动作的 duration 必须 >=1，实际 {c.duration}")
        total = sum(c.duration for c in self.cells)
        if total % 4 != 0:
            raise ValueError(
                f"栅格总时值 {total} 不是 4 的整数倍（每拍 4 个 16 分位置）"
            )

    @property
    def n_beats(self) -> int:
        """栅格跨多少拍（= 总时值 / 4）。"""
        return sum(c.duration for c in self.cells) // 4

    @property
    def n_strokes(self) -> int:
        """发音次数（非 :class:`Rest` 的动作数 = 攻击点数，扫弦或拨弦都算）。"""
        return sum(1 for c in self.cells if not isinstance(c, Rest))

    @property
    def density(self) -> float:
        """节奏密度 = 发音次数 / 总 16 分格数，``0..1``。

        副歌通常偏高、主歌偏低；用于按段落目标密度给模板排序。全休止栅格密度为 0。
        扫弦与拨弦混排栅格按「是否发音」统一计密度，不区分技法。与旧「非 None 格数 /
        格数」数值等价（旧 len(cells) 即总 16 分格数 = 新 sum(duration)）。
        """
        total = sum(c.duration for c in self.cells)
        return self.n_strokes / total if total else 0.0


@dataclass(frozen=True)
class StrumPattern:
    """一个节奏型模板（扫弦或分解）。

    名字沿用 ``StrumPattern``（历史原因，公开 API 已暴露），实际同时承载扫弦与分解
    两种技法模板，靠 ``technique`` 字段区分。混排场景下扫弦模板和分解模板进同一个
    选型器排序，musicnn 给出的段落技法倾向作为罚分项把不合基线的模板往后压。

    模板以「完整动机」（``grid_motif``，跨 ``motif_beats`` 拍）存储，选型时按和弦
    实际拍数 ``beats`` 平铺/截断成 4*beats 格的栅格。``motif_beats`` 是动机的天然
    周期：1 拍动机（4 格）对 boom-chick/pop 8th 这类够用；2 拍动机（8 格）能表达
    53231323 这类 8 个音的分解；4 拍动机（16 格）表达 pop D-DU-U-DU 的完整周期。
    这比原来「一律 1 拍单元 + min_beats 卡门槛」更贴合分解与多拍扫弦的真实形态。

    Attributes
    ----------
    name
        人类可读名称，如 ``"pop D-DU-U-DU"``。
    grid_motif
        完整动机栅格，长度 = ``4 * motif_beats``（每拍 4 个 16 分位置）。选型时按
        和弦拍数平铺/截断。格内可为 ``Stroke``（扫弦）、``Pluck``（拨弦）或 ``None``。
    motif_beats
        动机跨多少拍，决定 ``grid_motif`` 的长度（``4 * motif_beats`` 格）。也是
        平铺的周期单位。占拍数不是 ``motif_beats`` 整数倍时取动机前缀截断。
    min_beats
        占拍数门槛：和弦拍数小于此值的模板不适用（一票否决）。通常 ``>= motif_beats``，
        但可设更大（如 4 拍动机要求占满 4 拍才用）。
    ideal_beats
        最佳拍数区间，命中时排序加分。手填，与 ``motif_beats`` 互补：后者管周期对齐，
        前者管「这个模板特别偏好某拍数」的微调。
    sections
        适用段落标签，``{"verse","chorus","prechorus","bridge","outro"}`` 的子集。
    style
        风格，``{"folk","pop","rock"}`` 之一。选型时与请求风格一致才候选。
    technique
        该模板的右手技法，``"strum"``（扫弦）/ ``"arpeggio"``（分解）。
        由 ``is_strum`` / ``is_arpeggio`` 派生。musicnn 的段落技法基线据此罚分。
    """

    name: str
    grid_motif: tuple[Cell, ...]
    motif_beats: int
    min_beats: int
    ideal_beats: tuple[int, ...]
    sections: tuple[str, ...]
    style: str
    technique: Literal["strum", "arpeggio"] = "strum"
    positions: tuple[Position, ...] = ()
    """适用位置标签，:data:`Position` 的子集。空（默认）= 位置中立，所有位置都不罚分；
    非空时仅在这些位置 0 罚分、其他位置罚 ``W_POSITION``。只有需要特殊位置处理的模板
    （如琶音收尾）才填，如 ``("tail",)``。head 一般不做特殊处理，故无模板标 ``("head",)``。"""

    def __post_init__(self) -> None:
        expected = 4 * self.motif_beats
        total = sum(c.duration for c in self.grid_motif)
        if total != expected:
            raise ValueError(
                f"grid_motif 总时值应为 4 * motif_beats = {expected}，实际 {total}"
            )
        if self.motif_beats < 1:
            raise ValueError("motif_beats 至少为 1")
        if self.min_beats < self.motif_beats:
            raise ValueError(
                f"min_beats ({self.min_beats}) 不应小于 motif_beats "
                f"({self.motif_beats})--动机自身就跨这么多拍"
            )
        # 技法与栅格内容一致性：arpeggio 模板至少含一个 Pluck，strum 模板不得含 Pluck。
        has_pluck = any(isinstance(c, Pluck) for c in self.grid_motif)
        if self.technique == "arpeggio" and not has_pluck:
            raise ValueError(
                f"分解模板 {self.name} 的 grid_motif 必须含至少一个 Pluck"
            )
        if self.technique == "strum" and has_pluck:
            raise ValueError(
                f"扫弦模板 {self.name} 的 grid_motif 不得含 Pluck（应全为 Stroke/Rest）"
            )

    @property
    def is_strum(self) -> bool:
        """是否扫弦模板。"""
        return self.technique == "strum"

    @property
    def is_arpeggio(self) -> bool:
        """是否分解模板。"""
        return self.technique == "arpeggio"

    def grid_for(self, beats: int) -> RhythmGrid:
        """把动机平铺/截断成 ``beats`` 拍的完整栅格（总时值 ``4 * beats``）。

        ``beats < min_beats`` 时调用方应已通过拍数门槛剔除；此处不重复检查。
        平铺规则（基于 ``duration`` 之和，不再按格数切片）：

        - ``4*beats <= 动机总时值`` -> 取动机前缀截断到 ``4*beats``（截断边界动作的 duration）；
        - 否则整数份平铺 + 末尾不足一动机的前缀截断（同样截断边界动作 duration）。
        保证任意正整数拍都有确定输出，整数倍时退化为干净平铺。
        """
        if beats < 1:
            raise ValueError(f"拍数必须为正整数，实际 {beats}")
        need = 4 * beats
        motif = self.grid_motif
        motif_total = sum(c.duration for c in motif)  # == 4 * motif_beats
        if need <= motif_total:
            return RhythmGrid(_truncate_to_duration(motif, need))
        out: list[Cell] = []
        full_copies = need // motif_total
        remainder = need % motif_total
        for _ in range(full_copies):
            out.extend(motif)
        if remainder > 0:
            out.extend(_truncate_to_duration(motif, remainder))
        return RhythmGrid(tuple(out))

    def density(self) -> float:
        """基本动机的密度（平铺后密度不变，直接取动机算）。"""
        return RhythmGrid(self.grid_motif).density


def _with_duration(cell: Cell, duration: int) -> Cell:
    """返回与 ``cell`` 同类型、同其余字段、``duration`` 改为给定值的副本。"""
    if isinstance(cell, Stroke):
        return Stroke(cell.direction, duration)
    if isinstance(cell, Pluck):
        return Pluck(role=cell.role, strings=cell.strings, duration=duration)
    return Rest(duration)  # isinstance(cell, Rest)


def _truncate_to_duration(cells: tuple[Cell, ...], target: int) -> tuple[Cell, ...]:
    """取 ``cells`` 的前缀使其 ``duration`` 之和恰为 ``target``，必要时截断边界动作。

    ``target >= 1``。若某动作跨过截断点，把它重建为更短 duration 的同类动作；
    其后的动作全部丢弃。用于 :meth:`StrumPattern.grid_for` 的非整数倍平铺末尾。
    """
    out: list[Cell] = []
    acc = 0
    for c in cells:
        if acc + c.duration <= target:
            out.append(c)
            acc += c.duration
        else:
            remaining = target - acc
            if remaining > 0:
                out.append(_with_duration(c, remaining))
                acc = target
            break
        if acc == target:
            break
    if acc != target:
        # 防御：动机总时值恒为 4 的倍数，target < 动机总时值时前缀总能填满，此处不该到。
        raise ValueError(f"动机前缀无法填满 {target}（实际填到 {acc}）")
    return tuple(out)


@dataclass(frozen=True)
class RhythmEvent:
    """一个和弦的选型结果：用了哪个模板、平铺后的栅格、以及对应哪个和弦。

    ``enumerate_rhythm_patterns`` 对进行里每个和弦产出一个 ``RhythmEvent``，
    顺序与输入的进行一致。
    """

    chord: str
    beats: int
    pattern: StrumPattern
    grid: RhythmGrid

    @property
    def fingering(self) -> "tuple[FingeringAction, ...]":
        """该和弦节奏型的指法动作序列，由 :func:`fingering_sequence` 从 ``grid`` 派生。

        转谱项目可直接消费此序列渲染到吉他谱，无需自行解释 ``grid.cells``。
        """
        return fingering_sequence(self.grid)

    def to_dict(self) -> dict:
        """转 JSON 友好的 dict，只暴露转谱项目需要的字段。

        结构::

            {
              "chord": "C",            # 和弦符号
              "beats": 4,              # 占拍数
              "pattern": "folk D-DU",  # 节奏型模板名（人类可读）
              "technique": "strum",    # 技法大类 "strum" / "arpeggio"
              "fingering": [          # 指法动作序列（同 self.fingering，逐项 to_dict）
                {"kind": "stroke_down", "strings": null},
                {"kind": "rest", "strings": null},
                ...
              ]
            }

        不暴露 ``grid.cells`` 原始栅格与模板内部结构（``grid_motif``/权重等）--转谱侧
        只需指法动作序列。``json.dumps(event.to_dict(), ensure_ascii=False)`` 即得完整 JSON。
        """
        return {
            "chord": self.chord,
            "beats": self.beats,
            "pattern": self.pattern.name,
            "technique": self.pattern.technique,
            "fingering": [a.to_dict() for a in self.fingering],
        }


@dataclass(frozen=True)
class FingeringAction:
    """一个吉他指法动作（转谱项目消费的最小单元）。

    把 :class:`RhythmGrid` 的 16 分栅格聚合成动作序列：连续的「发音延续」格合并进
    发音动作，``duration`` 显式记录该动作占多少个 16 分位置。

    时值模型（关键）
    ----------------
    栅格层每个动作（:class:`Stroke`/:class:`Pluck`/:class:`Rest`）已自带 ``duration``，
    本类是其转谱输出投影，``duration`` 直接取动作自身时值：

    - **发音动作**（stroke/pluck）的 ``duration`` = 该动作的持续时值（如 ``D`` duration=4 =
      扫一下持续一拍）。
    - **rest** 的 ``duration`` = 该休止的静默时值。

    延续（音持续）与休止（真静默）都是动作自身的时值属性，符合乐理；不再由 ``None`` 格
    位置推断。序列所有动作 ``duration`` 之和 = 栅格总时值（``4 × beats``），时间轴完整对齐。

    Attributes
    ----------
    kind
        动作类型：

        - ``"stroke_down"`` - 下扫（低->高音弦，强拍常用）；
        - ``"stroke_up"``   - 上扫（高->低音弦，弱拍回扫）；
        - ``"pluck"``       - 拨弦/琶音（一次拨指定弦号，可多根）；
        - ``"rest"``        - 休止（真静默，该时段不发声）。
    strings
        弦号下标元组（``0`` = 最低音弦，与 :mod:`chord_fingering` ``Fingering.positions``
        同序）。``stroke_down``/``stroke_up`` 时为 ``None``（扫弦扫的是「当时按住的弦组」，
        由和弦 voicing 决定，不在动作层指定）；``pluck`` 时为拨弦号（实例化后填入，
        未实例化时为 ``None`` 表示「拨但弦未定」）；``rest`` 时为 ``None``。
    duration
        该动作占多少个 **16 分音符位置**（1=16分、2=8分、4=四分、8=二分...）。
        发音动作的 duration 含其后的延续格；rest 的 duration 为静默时长。序列所有动作
        duration 之和 = 栅格总格数（``4 × beats``），时间轴完整对齐。

    Notes
    -----
    后续可扩展力度、闷音/切音等修饰--加字段即可，``kind`` 已区分动作大类。
    """

    kind: Literal["stroke_down", "stroke_up", "pluck", "rest"]
    strings: tuple[int, ...] | None
    duration: int = 1

    def to_dict(self) -> dict:
        """转 JSON 友好的 dict（``kind`` + ``strings`` + ``duration``）。

        供转谱项目跨语言消费：``json.dumps(action.to_dict())`` 即得
        ``{"kind": ..., "strings": ..., "duration": ...}``。``strings`` 为 ``None`` 时输出 null。
        """
        return {
            "kind": self.kind,
            "strings": list(self.strings) if self.strings is not None else None,
            "duration": self.duration,
        }


def fingering_sequence(grid: RhythmGrid) -> tuple[FingeringAction, ...]:
    """把栅格转成带 ``duration`` 的 :class:`FingeringAction` 序列。

    栅格层每个动作（Stroke/Pluck/Rest）已自带 ``duration``，故本函数为近恒等映射：
    逐格映射成对应的 :class:`FingeringAction`，``duration`` 直接取动作自身的值。

    - :class:`Stroke` → ``stroke_down``/``stroke_up``（``duration`` = 动作时值）；
    - :class:`Pluck` → ``pluck``（``strings`` 取实例化后的弦号，``duration`` = 动作时值）；
    - :class:`Rest` → ``rest``（``duration`` = 休止时值）。

    序列所有动作 ``duration`` 之和 = 栅格总时值（``4 × beats``，由
    :class:`RhythmGrid` 不变量保证），时间轴完整对齐。时值是动作自身的属性，
    不再由 ``None`` 格位置推断。
    """
    actions: list[FingeringAction] = []
    for c in grid.cells:
        if isinstance(c, Stroke):
            kind = "stroke_down" if c.direction == "D" else "stroke_up"
            actions.append(FingeringAction(kind=kind, strings=None, duration=c.duration))
        elif isinstance(c, Pluck):
            actions.append(FingeringAction(kind="pluck", strings=c.strings, duration=c.duration))
        else:  # Rest
            actions.append(FingeringAction(kind="rest", strings=None, duration=c.duration))
    return tuple(actions)
