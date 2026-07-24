"""弦角色（StringRole）：分解模板的「拨哪根弦」用角色表达，运行时按和弦 voicing 实例化。

为什么不用固定弦号
------------------
分解的弦序随和弦走。``5,3,21`` 这套指法是针对 C 和弦（标准调弦）总结的：

- ``5`` = 根音，取最低音区的根音出现（C 在 5 弦 C3）；
- ``3`` = 五音，避开低音区（已有贝斯音，五音取偏高的出现，G3 在 3 弦）；
- ``21`` = 顶两弦一起拨，但具体选 ``21`` 还是 ``32`` 看音距--C 选 21（顶音距合适、
  丰富），G 的根音在 6 弦（更低），再选 21 顶底音距过大、尖锐，故 G 选 32 收窄。

换和弦（G、Am、F）或换调弦（DADGAD），同样的「根音→五音→顶两弦」逻辑要自动映射
到不同弦号。固定弦号做不到。弦角色把「拨什么」抽象成音级 + 音区 + 弦组 + 音距约束，
实例化时按当前 voicing 的音高分布算出具体弦号--天然调弦中立，与扫弦那边一致。

设计
----
``StringRole`` 是基类，每个具体角色带 ``resolve(voicing) -> tuple[int, ...]``，
返回要拨的弦号下标（``0`` = 最低音弦，与 ``chord_fingering`` 一致）。解析失败
（voicing 里没该音级、或音距约束无法满足）时返回 ``None``，由调用方决定降级。

角色原语三类：

1. **音级**：``Root`` / ``Third`` / ``Fifth`` / ``Seventh``，可带音区修饰
   ``region``（``"bass"`` 取最低音区出现 / ``"treble"`` 取最高音区 / ``"avoid_bass"``
   避开最低音区 / ``None`` 取默认）。对应「5=根音(bass)」「3=五音(avoid_bass)」。
2. **弦组**：``TopN(n, span)`` 取最高 N 根发音弦，``span`` 约束「组内最低音-组外最低
   发音弦」的音距落在舒适区间（``"comfortable"``/``"narrow"``/``None``）。
   对应「21 vs 32」的动态选择。
3. **全拨**：``All()`` 拨全部发音弦（相当于用拨的方式扫，用于和弦音同时呈现）。

音距用半音数衡量（基于真实音高 midi，非弦号距离），调弦中立。舒适区间阈值见
``_COMFORTABLE_SPAN``，可按风格调整。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol, runtime_checkable

if TYPE_CHECKING:
    pass


# 音区修饰：约束某音级在 voicing 里取哪个出现位置。
Region = Literal["bass", "treble", "avoid_bass"]
"""音区修饰。

- ``"bass"``      取该音级在 voicing 里音高最低的出现（低音区）；
- ``"treble"``    取音高最高的出现（高音区）；
- ``"avoid_bass"`` 避开最低音区--该音级若有多个出现，取非最低的那个；只有一个则仍取它。
"""

Span = Literal["comfortable", "narrow"]
"""弦组的音距约束（组内最低音与组外最低发音弦的半音距）。

- ``"comfortable"`` 落在 ``_COMFORTABLE_SPAN`` 区间，丰富不尖锐；
- ``"narrow"``     偏窄，收顶底音距；
- ``None``         不约束，取最高 N 根。
"""


# 舒适音距区间（半音）。组内最低音与组外最低发音弦的半音距落在此区间算「舒适」。
# 标定依据：C 和弦根音 C3(48)，顶两弦 2+1 弦 C4(60)+E4(64)，组外最低发音弦是 5 弦 C3，
# 组内最低音 C4 与之距 12 半音 = 一个八度，舒适。G 和弦根音 G2(43)，若选 2+1 弦
# D4(62)+G4(67)，组内最低 D4 与最低发音弦 6 弦 G2 距 19 半音 > 一个半八度，尖锐；
# 改选 3+2 弦 G3(55)+D4(62)，距 12 半音，舒适。故区间约 [10, 15]。
_COMFORTABLE_SPAN = (10, 15)


@runtime_checkable
class Voicing(Protocol):
    """一个和弦在某指板上的具体按法视图，供角色解析用。

    属性
    ----------
    positions
        按弦号下标排的把位序列（``0`` = 最低音弦），``None`` 闷音，``0`` 空弦。
        与 ``chord_fingering`` 的 ``Fingering.positions`` 同序。
    midi
        每根发音弦的真实音高 midi（按弦后），与弦号下标对齐--``midi[i]`` 是第 ``i``
        弦发音音高，第 ``i`` 弦若闷音则不出现在这里。由 ``Fingering.tones`` 配
        ``positions`` 重建：发音弦按弦号顺序，音高取 ``tone.midi``。
    """

    positions: tuple[int | None, ...]
    midi: tuple[tuple[int, int], ...]  # (弦号下标, midi)，按弦号升序，仅发音弦


@dataclass(frozen=True)
class VoicingData:
    """``Voicing`` 协议的具体实现，由 :func:`voicing_from_fingering` 构造。"""

    positions: tuple[int | None, ...]
    midi: tuple[tuple[int, int], ...]


def voicing_from_fingering(
    positions: tuple[int | None, ...], tones: tuple["object", ...]
) -> VoicingData:
    """从 ``Fingering.positions`` + ``Fingering.tones`` 构造 ``VoicingData``。

    ``tones`` 只含发音弦音高、与发音弦按弦号顺序对齐（pytheory 保证）。本函数把它和
    ``positions`` 配起来，得到 ``(弦号下标, midi)`` 列表，供角色解析按音高/弦号查询。

    Parameters
    ----------
    positions
        按弦号下标排的把位序列，``None`` 闷音。
    tones
        发音弦的音高序列（``Fingering.tones``），按弦号升序、仅发音弦。需有 ``midi``
        属性（pytheory 的 ``Tone`` 有）。
    """
    sounding_idx = [i for i, p in enumerate(positions) if p is not None]
    if len(sounding_idx) != len(tones):
        # 理论不会发生：tones 应与发音弦一一对应。防御性报错，避免错位静默通过。
        raise ValueError(
            f"发音弦数 {len(sounding_idx)} 与 tones 数 {len(tones)} 不符"
        )
    midi = tuple((idx, t.midi) for idx, t in zip(sounding_idx, tones))
    return VoicingData(positions=positions, midi=midi)


# ── 角色基类与具体角色 ──────────────────────────────────────


class StringRole:
    """弦角色基类。子类实现 :meth:`resolve`，把 voicing 解析成具体弦号下标。

    所有角色基于音高（midi）而非弦号距离判定音区/音距，故对任意调弦中立。
    """

    def resolve(self, voicing: Voicing) -> tuple[int, ...] | None:
        """解析出要拨的弦号下标元组，``0`` = 最低音弦。无法满足约束时返回 ``None``。"""
        raise NotImplementedError


@dataclass(frozen=True)
class _DegreeRole(StringRole):
    """音级角色基类：root/third/fifth/seventh。"""

    degree: int  # 0=root, 1=third, 2=fifth, 3=seventh（相对根音的音级序号）
    region: Region | None = None

    def resolve(self, voicing: Voicing) -> tuple[int, ...] | None:
        hits = _degree_hits(voicing, self.degree)
        if not hits:
            return None
        chosen = _apply_region(hits, self.region)
        return (chosen[0],)


# 音级到根音的半音偏移索引（仅用于 _DEGREE_PCS 的键）。
# 实际匹配用 _DEGREE_PCS 里的集合，覆盖大小三度/增减五/大小七的差异。
_DEGREE_OFFSET = {0: 0, 1: 4, 2: 7, 3: 10}
# 各音级可匹配的半音偏移集合（含大小/增减变体）。
_DEGREE_PCS = {
    0: {0},
    1: {3, 4},      # 小三度 / 大三度
    2: {7, 6, 8},   # 纯五 / 减五 / 增五
    3: {10, 11, 9}, # 小七 / 大七 / 减七
}


def _degree_hits(voicing: Voicing, degree: int) -> list[tuple[int, int]]:
    """voicing 里属于某音级的所有发音弦（弦号, midi）。覆盖大小/增减变体。

    根音以 voicing 的最低发音弦为准（原位判定，与 ``chord_fingering`` 的
    ``lowest = min(tones, key=midi)`` 一致）。注意：这判的是「该音相对最低音是第几音级」，
    与和弦符号标注的音级在非原位 voicing 下可能不同--分解弦序本就以实际发音为准。
    """
    if not voicing.midi:
        return []
    lowest_midi = min(m for _, m in voicing.midi)
    root_pc = lowest_midi % 12
    target_pcs = {(root_pc + off) % 12 for off in _DEGREE_PCS[degree]}
    return [(s, m) for s, m in voicing.midi if m % 12 in target_pcs]


def _apply_region(
    hits: list[tuple[int, int]], region: Region | None
) -> tuple[int, int]:
    """按音区修饰从该音级的出现里选一个 (弦号, midi)。"""
    if region == "bass":
        return min(hits, key=lambda sm: sm[1])
    if region == "treble":
        return max(hits, key=lambda sm: sm[1])
    if region == "avoid_bass":
        if len(hits) >= 2:
            # 避开最低音区的出现，取其余里最低的（仍偏中低，但不叠在贝斯音上）。
            sorted_hits = sorted(hits, key=lambda sm: sm[1])
            return sorted_hits[1]
        return hits[0]
    # None：默认取最低音区出现（根音/五音常用低音区）。
    return min(hits, key=lambda sm: sm[1])


# ── 具体音级角色（便捷别名）──────────────────────────────────


class Root(_DegreeRole):
    """根音。默认取最低音区出现。``region="treble"`` 取高八度根音。"""

    def __init__(self, region: Region | None = None) -> None:
        super().__init__(degree=0, region=region)


class Third(_DegreeRole):
    """三音。"""

    def __init__(self, region: Region | None = None) -> None:
        super().__init__(degree=1, region=region)


class Fifth(_DegreeRole):
    """五音。``region="avoid_bass"`` 避开低音区（已有贝斯音时取偏高的五音）。"""

    def __init__(self, region: Region | None = None) -> None:
        super().__init__(degree=2, region=region)


class Seventh(_DegreeRole):
    """七音。"""

    def __init__(self, region: Region | None = None) -> None:
        super().__init__(degree=3, region=region)


@dataclass(frozen=True)
class TopN(StringRole):
    """取最高 N 根发音弦一起拨，可带音距约束。

    对应 ``21`` / ``32`` 这类「顶两弦」选择。``span`` 约束「组内最低音与组外最低发音弦」
    的半音距：``"comfortable"`` 落舒适区间（丰富不尖锐），``"narrow"`` 偏窄收顶底距，
    ``None`` 直接取最高 N 根不约束。C 选 21、G 选 32 就是 ``TopN(2, "comfortable")``
    在两个 voicing 上解析出不同弦号的结果。
    """

    n: int
    span: Span | None = None

    def resolve(self, voicing: Voicing) -> tuple[int, ...] | None:
        sounding = sorted(voicing.midi, key=lambda sm: sm[1])  # 按音高升序
        if len(sounding) < self.n:
            return None
        if self.span is None:
            # 取最高 N 根。
            top = sounding[-self.n:]
            return tuple(s for s, _ in sorted(top))
        # 带 span 约束：在所有「最高若干弦的连续 N 根」候选里，挑音距满足约束的。
        # 候选 = 从高音端起的连续 N 根弦（按弦号邻近，保证手能同时拨）。
        candidates = self._candidates(sounding)
        if not candidates:
            return None
        # 组外最低发音弦 = 该候选之外音高最低的发音弦。
        best = self._pick_by_span(candidates, sounding)
        if best is None:
            # 无候选满足约束，退化为不约束的最高 N 根。
            top = sounding[-self.n:]
            return tuple(s for s, _ in sorted(top))
        return tuple(s for s, _ in sorted(best))

    def _candidates(
        self, sounding: list[tuple[int, int]]
    ) -> list[list[tuple[int, int]]]:
        """按弦号邻近的连续 N 根发音弦，作为「能同时拨」的候选组。"""
        # 按弦号升序的发音弦。
        by_string = sorted(sounding, key=lambda sm: sm[0])
        out: list[list[tuple[int, int]]] = []
        for i in range(len(by_string) - self.n + 1):
            window = by_string[i : i + self.n]
            # 连续性：弦号相邻（差 1），允许中间夹闷音弦--用弦号是否连续递增判定。
            idxs = [s for s, _ in window]
            if all(idxs[j + 1] - idxs[j] == 1 for j in range(len(idxs) - 1)):
                out.append(window)
        return out

    def _pick_by_span(
        self,
        candidates: list[list[tuple[int, int]]],
        all_sounding: list[tuple[int, int]],
    ) -> list[tuple[int, int]] | None:
        """按 span 约束从候选里挑一组。组外最低发音弦 = 候选外音高最低的。"""
        lo, hi = _COMFORTABLE_SPAN if self.span == "comfortable" else (0, _COMFORTABLE_SPAN[0])
        for cand in candidates:  # 候选按弦号从低到高，优先偏低音的组（更稳）
            cand_set = {s for s, _ in cand}
            outside = [m for s, m in all_sounding if s not in cand_set]
            if not outside:
                continue  # 组覆盖全部发音弦，无组外参照，跳过
            lowest_outside = min(outside)
            lowest_inside = min(m for _, m in cand)
            span = lowest_inside - lowest_outside
            if lo <= span <= hi:
                return cand
        return None


@dataclass(frozen=True)
class All(StringRole):
    """拨全部发音弦（用拨的方式同时呈现整个和弦，相当于「拨弦版扫弦」）。"""

    def resolve(self, voicing: Voicing) -> tuple[int, ...] | None:
        if not voicing.midi:
            return None
        return tuple(s for s, _ in sorted(voicing.midi, key=lambda sm: sm[0]))
