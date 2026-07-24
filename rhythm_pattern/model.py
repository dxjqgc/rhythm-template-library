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

一个节奏型模板以「1 拍基本单元」（4 格）存储，选型时按和弦拍数平铺成完整栅格。
天然 2 拍或 4 拍一个周期的模板，靠 ``min_beats`` 直接卡掉拍数不够的和弦。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


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
    「一次拨一根或几根指定的弦」。分解节奏型的基本单元。具体拨哪根弦、按什么顺序，
    首期不建模（``strings=None`` 即可），只占住「这一格是个拨弦发音」的语义；
    后续要精确表达分解形态时再给 ``strings`` 填 ``tuple[int, ...]``。

    Attributes
    ----------
    strings
        拨哪几根弦（``0`` = 最高音弦，与 :mod:`chord_fingering` 一致）。
        ``None`` 表示「拨弦但暂不指定弦」，占位用；不参与指法可行性判定。
    """

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

    模板以「1 拍基本单元」（4 格）存储，选型时按和弦实际拍数 ``beats`` 平铺成
    ``beats`` 个基本单元拼接的完整栅格。天然是 2/4 拍一个周期的模板把
    ``min_beats`` 设为 2，占 1 拍的和弦会直接被拍数门槛剔除。

    Attributes
    ----------
    name
        人类可读名称，如 ``"pop D-DU-U-DU"``。
    grid_1beat
        1 拍基本单元，恰好 4 格。选型时按和弦拍数平铺。格内可为 ``Stroke``（扫弦）、
        ``Pluck``（拨弦）或 ``None``（休止）。
    min_beats
        占拍数门槛：和弦拍数小于此值的模板不适用（一票否决）。
    ideal_beats
        最佳拍数区间，命中时排序加分（一个和弦占 4 拍时，4 拍周期的模板最顺）。
    sections
        适用段落标签，``{"verse","chorus","prechorus","bridge","outro"}`` 的子集。
    style
        风格，``{"folk","pop","rock"}`` 之一。选型时与请求风格一致才候选。
    technique
        该模板的右手技法，``"strum"``（扫弦）/ ``"arpeggio"``（分解）。
        由 ``is_strum`` / ``is_arpeggio`` 派生。musicnn 的段落技法基线据此罚分。
    """

    name: str
    grid_1beat: tuple[Cell, ...]
    min_beats: int
    ideal_beats: tuple[int, ...]
    sections: tuple[str, ...]
    style: str
    technique: Literal["strum", "arpeggio"] = "strum"

    def __post_init__(self) -> None:
        if len(self.grid_1beat) != 4:
            raise ValueError(
                f"grid_1beat 必须恰为 4 格（1 拍），实际 {len(self.grid_1beat)}"
            )
        if self.min_beats < 1:
            raise ValueError("min_beats 至少为 1")
        # 技法与栅格内容一致性：arpeggio 模板至少含一个 Pluck，strum 模板不得含 Pluck。
        has_pluck = any(isinstance(c, Pluck) for c in self.grid_1beat)
        if self.technique == "arpeggio" and not has_pluck:
            raise ValueError(
                f"分解模板 {self.name} 的 grid_1beat 必须含至少一个 Pluck"
            )
        if self.technique == "strum" and has_pluck:
            raise ValueError(
                f"扫弦模板 {self.name} 的 grid_1beat 不得含 Pluck（应全为 Stroke/None）"
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
        """把 1 拍基本单元平铺成 ``beats`` 拍的完整栅格。

        ``beats < min_beats`` 时调用方应已通过拍数门槛剔除；此处不重复检查，
        只做平铺，保证任意正整数拍都能生成确定长度的栅格。
        """
        if beats < 1:
            raise ValueError(f"拍数必须为正整数，实际 {beats}")
        cells = self.grid_1beat * beats
        return RhythmGrid(cells)

    def density(self) -> float:
        """基本单元的密度（平铺后密度不变，直接取基本单元算）。"""
        return RhythmGrid(self.grid_1beat).density


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
