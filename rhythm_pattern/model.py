"""扫弦节奏型的数据模型。

所有节奏型都落在 **16 分音符栅格** 上：一拍 = 4 格，占 N 拍的和弦对应 4N 格。
这样无论和弦占几拍，模板都能用「按拍切片/平铺」对齐，拍数门槛筛选用整数比较即可。

栅格里每格取值有两种「右手技法」之一，或休止：

- ``Stroke("D")`` - 下扫（down），由低音弦往高音弦扫，最常用的强拍动作；
- ``Stroke("U")`` - 上扫（up），高音弦往低音弦回扫，常落在弱拍的「与」上；
- ``Pluck(...)`` - 拨弦/琶音（arpeggio），一次拨一根或几根弦。分解节奏型的基本
  动作。与扫弦是**不同**的右手动作，故单立一个类型，不挤进 ``Stroke``；二者可混排
  在同一栅格里（段落级混排）。
- ``None``    - 休止，该 16 分位置不发声。

一个节奏型模板以「完整动机」（``grid_motif``，跨 ``motif_beats`` 拍）存储，选型时按
和弦拍数平铺/截断成完整栅格。动机可跨 1/2/4 拍--1 拍动机（4 格）对扫弦小单元够用，
2 拍动机（8 格）表达 53231323 这类 8 音分解，4 拍动机（16 格）表达 pop D-DU-U-DU 完整周期。
占拍数不够 ``min_beats`` 的模板由拍数门槛一票否决。
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
    """

    direction: Literal["D", "U"]

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
    """

    role: "StringRole | None" = None
    strings: tuple[int, ...] | None = None

    def __str__(self) -> str:
        return "P"


# 栅格一格的取值：扫弦动作 / 拨弦动作 / 休止。``Stroke`` 与 ``Pluck`` 是不同类型，
# 混排栅格里 isinstance 分流。放此处定义使两者皆已声明。
Cell = Stroke | Pluck | None


@dataclass(frozen=True)
class RhythmGrid:
    """一段节奏型在 16 分栅格上的表达。

    每格代表一个 16 分音符；位置由在 ``cells`` 中的下标决定。``cells`` 长度必为 4 的
    整数倍（整数拍）。``None`` 格为休止，其余为一次扫弦动作。

    Attributes
    ----------
    cells
        按时间顺序的栅格内容。下标 0 = 第一拍第一个 16 分位置。
    """

    cells: tuple[Cell, ...]

    def __post_init__(self) -> None:
        if len(self.cells) % 4 != 0:
            raise ValueError(
                f"栅格长度 {len(self.cells)} 不是 4 的整数倍（每拍 4 个 16 分位置）"
            )

    @property
    def n_beats(self) -> int:
        """栅格跨多少拍（= 格数 / 4）。"""
        return len(self.cells) // 4

    @property
    def n_strokes(self) -> int:
        """非休止格数（实际发音次数，扫弦或拨弦都算）。"""
        return sum(1 for c in self.cells if c is not None)

    @property
    def density(self) -> float:
        """节奏密度 = 发音格数 / 总格数，``0..1``。

        副歌通常偏高、主歌偏低；用于按段落目标密度给模板排序。全休止栅格密度为 0。
        扫弦与拨弦混排栅格按「是否发音」统一计密度，不区分技法。
        """
        n = len(self.cells)
        return self.n_strokes / n if n else 0.0


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
        if len(self.grid_motif) != expected:
            raise ValueError(
                f"grid_motif 长度应为 4 * motif_beats = {expected} 格，"
                f"实际 {len(self.grid_motif)}"
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
                f"扫弦模板 {self.name} 的 grid_motif 不得含 Pluck（应全为 Stroke/None）"
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
        """把动机平铺/截断成 ``beats`` 拍的完整栅格（``4 * beats`` 格）。

        ``beats < min_beats`` 时调用方应已通过拍数门槛剔除；此处不重复检查。
        平铺规则：

        - ``beats`` 是 ``motif_beats`` 的整数倍 -> 完整动机重复 ``beats // motif_beats`` 遍；
        - 非整数倍 -> 取动机前缀截断到 ``4 * beats`` 格（如 2 拍动机用在 3 拍和弦上，
          取动机前 3 拍）。保证任意正整数拍都有确定输出，整数倍时退化为干净平铺。
        """
        if beats < 1:
            raise ValueError(f"拍数必须为正整数，实际 {beats}")
        need = 4 * beats
        if need <= len(self.grid_motif):
            # 和弦短于或等于动机：取动机前缀。
            cells = self.grid_motif[:need]
        else:
            # 和弦长于动机：整数倍平铺 + 末尾不足一动机的前缀截断。
            full_copies = need // len(self.grid_motif)
            remainder = need % len(self.grid_motif)
            cells = self.grid_motif * full_copies + self.grid_motif[:remainder]
        return RhythmGrid(cells)

    def density(self) -> float:
        """基本动机的密度（平铺后密度不变，直接取动机算）。"""
        return RhythmGrid(self.grid_motif).density


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
    吉他扫/拨弦后弦持续振动，栅格里的 ``.``（不拨弦格）在多数模板里表示「该发音
    的延续」而非「真静默」。故：

    - **发音动作**（stroke/pluck）的 ``duration`` = 从该发音格到**下一个发音格**的距离
      （含中间的 ``.`` 格），表示这个音持续响多久。如 ``D . . .`` -> 一个 ``stroke_down``
      ``duration=4``（扫一下持续一拍）。
    - **rest** 只在「真静默」时出现：即发音动作**之前**的留白（如反拍起拍前的小静默，
      ``REST U REST REST`` -> ``rest(1) + stroke_up(3)``）。发音后的 ``.`` 被发音吸收，
      不再单列 rest。

    这样 ``rest`` 严格表示静默休止，发音动作的 ``duration`` 严格表示音的持续时长，
    转谱侧无需自己推算时值。

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
    """把 16 分栅格聚合成带 ``duration`` 的 :class:`FingeringAction` 序列。

    时值模型（见 :class:`FingeringAction`）：

    - **发音动作**（Stroke/Pluck）的 ``duration`` = 到下一个发音格的距离（含中间的 ``.`` 格），
      表示该音持续响多久。发音后的 ``.`` 被发音吸收，不单列 rest。
    - **rest** 只在「真静默」时出现：发音动作**之前**的留白（如反拍起拍前的小静默）。
      连续静默格合并成一个 rest，``duration`` = 静默格数。

    序列所有动作 ``duration`` 之和 = 栅格总格数（``4 × beats``），时间轴完整对齐。

    示例
    ----
    - ``D . . .`` -> ``stroke_down(duration=4)``（扫一下持续一拍，无 rest）
    - ``P . P .`` -> ``pluck(2) pluck(2)``（8 分分解，音持续 8 分）
    - ``P P P P`` -> ``pluck(1)×4``（16 分分解，音各 16 分）
    - ``REST U REST REST`` -> ``rest(1) stroke_up(3)``（反拍前静默 1 格，上扫持续到拍末）
    """
    cells = grid.cells
    n = len(cells)
    actions: list[FingeringAction] = []
    i = 0
    # 先吃掉开头的静默（发音前的真休止），逐段处理。
    while i < n:
        cell = cells[i]
        if cell is None:
            # 静默段：连续的 None 合并成一个 rest，duration = 连续格数。
            j = i
            while j < n and cells[j] is None:
                j += 1
            actions.append(FingeringAction(kind="rest", strings=None, duration=j - i))
            i = j
        else:
            # 发音格：duration = 到下一个发音格的距离（含中间的 None，它们是延续而非静默）。
            if isinstance(cell, Stroke):
                kind = "stroke_down" if cell.direction == "D" else "stroke_up"
                strings = None
            else:  # Pluck
                kind = "pluck"
                strings = cell.strings
            j = i + 1
            while j < n and cells[j] is None:
                j += 1
            actions.append(FingeringAction(kind=kind, strings=strings, duration=j - i))
            i = j
    return tuple(actions)
