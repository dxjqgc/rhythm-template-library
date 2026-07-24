"""指法可演奏性模型：手指分配可行性 + 连续代价评分。

``fingering_enumerator`` 原有的做法是「硬过滤（能不能弹）+ 分层排序（好不好弹）」，
但两边都太粗：

- 硬过滤只看跨度 ``max(fret) - min(fret) <= max_stretch``，不检查这些音**能不能
  用四根手指按出来**。跨度 3 的 ``(1,4,x,4,x,4)`` 需要五根手指分别落在四个品上，
  跨度检查放它过关。
- 分层排序把「把位低」放在「指头少」之前，永远优先低把位；而真实吉他手的取舍是
  连续的——为了少一次横按、少一根内部闷弦，愿意换两个把位。分层字典序表达不了
  这种交换，只能在同一层内比较。

本模块给出两件事：

1. :func:`plan_fingers` —— 把按弦位置真的分配给食/中/无名/小四根手指（可含横按），
   分配不出来就是弹不了，作为**硬约束**。
2. :func:`playability_cost` —— 连续代价（越小越顺手），把手型难度、内部闷音、
   把位高度、空弦收益、省略和弦音的代价放在同一个尺度上加权比较。

权重都提在模块顶部常量里，标定依据是 ``main.py`` 的常用指法基准集：改权重后必须
重跑 ``uv run main.py``，确认教科书指法仍排在前列。
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from itertools import combinations, product

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pytheory import Chord, Tone


__all__ = [
    "FingerPlan",
    "plan_fingers",
    "playability_cost",
    "required_pitch_classes",
    "count_muted",
]


# --- 手部几何 -------------------------------------------------------------

MAX_FINGERS = 4
"""可用按弦手指数（食/中/无名/小）。本模型不使用大拇指绕琴颈按弦。"""

MAX_NON_INDEX_BARRE = 3
"""非食指横按（无名指小横按等）最多覆盖几根弦。

A 大和弦 ``x02220`` 用无名指一根平压 D/G/B 三弦是常见按法，故上限取 3；
再宽就成了「小指横按四弦」这种极少出现的手型。
"""

_REACH_BY_POSITION = ((2, 1.0), (5, 4 / 3), (9, 5 / 3))
"""相邻手指编号之间能跨的品数，随把位升高而放宽。

品格宽度沿指板递减：1 品处食指与小指几乎只能跨 3 品，12 品处同样的手张开能跨 6 品。
用「每个手指编号间隔能跨多少品」表达，查表按最低按弦品取值，表外（>=10 品）取 2.0。
"""


def _reach(base_fret: int) -> float:
    for upper, reach in _REACH_BY_POSITION:
        if base_fret <= upper:
            return reach
    return 2.0


# --- 代价权重（越大越劝退） -----------------------------------------------

W_FINGER = 1.0            # 每根按弦手指
W_INDEX_BARRE = 1.4       # 食指横按（要平压且不能有空弦穿过）
W_OTHER_BARRE = 1.8       # 非食指小横按（比食指横按更费力）
W_SPAN = 0.5              # 每品跨度
W_LOW_POSITION_SPAN = 0.25  # 低把位（<=2 品）跨度的额外惩罚，品格宽、手更难张开
W_MIN_FRET = 0.35         # 把位高度：同等手型下低把位更常用
W_INNER_MUTE = 3.5        # 内部闷音：被发音弦夹住的闷弦，扫弦时要靠指腹精确挡住
W_LOW_MUTE = 0.55         # 低音侧闷音的**平方**系数，见下
W_HIGH_MUTE = 2.0         # 高音侧闷音的平方系数：闷掉顶音比闷掉低音严重得多
W_SOUNDED = -0.5          # 每根发音弦（负数 = 奖励饱满 voicing）
W_OPEN = -0.2             # 每根空弦（不占手指、音响亮，开放和弦常用的原因）
W_OMISSION = 1.6          # 每省略一个和弦音（微量音不一致的代价）
W_INVERSION = 3.0         # 最低音不是根音

# 外侧闷音按 W * n^2 计，边际递增：少扫 1 根弦（x32010 的 6 弦）是常态，少扫 3 根就
# 不再是「和弦」而是三音组骨架了。用线性权重无论怎么调都压不住 ``xxx010``（C 只按
# 2 弦 1 品，1 根手指）这种退化解——它省下的手指和跨度总能抵掉三次线性惩罚。
#
# 低音侧和高音侧分开计权，因为二者的音乐后果完全不同：闷掉低音弦只是换了个低音
# （x32010、xx0232 都是这么来的，是常用指法的常态），闷掉最高音弦则丢掉和弦的顶音，
# 听感上直接塌掉。尤克里里 C 就卡在这里：``000x`` 比标准的 ``0003`` 少一根手指、
# 少一个把位，两侧同权时反而排第一，按顶音加权后才回到 ``0003``。



@dataclass(frozen=True)
class FingerPlan:
    """一种可行的手指分配方案。

    Attributes
    ----------
    n_fingers
        用到的手指数（横按算 1 根）。
    barre_fret
        食指横按所在品；无食指横按为 ``None``。
    barre_width
        食指横按覆盖的弦数。
    n_other_barres
        非食指的小横按个数。
    hand_cost
        纯手型代价（手指数 + 横按 + 跨度 + 把位），不含闷音/省略等音乐层面的项。
    """

    n_fingers: int
    barre_fret: int | None
    barre_width: int
    n_other_barres: int
    hand_cost: float


@dataclass(frozen=True)
class _Unit:
    """一个「手指单元」：一根手指负责的一个品上的一段连续弦。"""

    fret: int
    lo: int
    hi: int
    is_barre: bool
    is_index_barre: bool


def _index_barre_range(positions: Sequence[int | None]) -> tuple[int, int] | None:
    """食指横按能覆盖的弦区间，判据与 ``analyze_barre`` 一致。

    食指平压最低按弦品 ``bf`` 上的一段连续弦；区间内的弦允许按在**更高**的品
    （由其他手指按，食指仍压在下面），但不允许出现闷音或空弦——那会打断平压。
    """
    fretted = [(i, p) for i, p in enumerate(positions) if p is not None and p > 0]
    if not fretted:
        return None
    bf = min(p for _, p in fretted)
    at_bf = [i for i, p in fretted if p == bf]
    if len(at_bf) < 2:
        return None
    lo, hi = min(at_bf), max(at_bf)
    for i in range(lo, hi + 1):
        p = positions[i]
        if p is None or p == 0 or p < bf:
            return None
    return lo, hi


def _runs(strings: Sequence[int]) -> list[tuple[int, int]]:
    """把弦号列表切成极大连续区间。"""
    out: list[tuple[int, int]] = []
    for s in sorted(strings):
        if out and s == out[-1][1] + 1:
            out[-1] = (out[-1][0], s)
        else:
            out.append((s, s))
    return out


def _unit_partitions(
    positions: Sequence[int | None], use_index_barre: bool
) -> list[list[_Unit]]:
    """枚举把按弦位置划分成手指单元的所有候选方案。

    同一品上的一段连续弦，要么每弦一根手指，要么一根手指小横按压下去——这两种
    都要试，因为哪种更省手指取决于其他弦的位置。规模上每个和弦最多两三段连续弦，
    枚举量是个位数。

    非食指的小横按额外要求：覆盖区间内**所有**弦都恰好按在同一品。食指横按可以
    「压在下面」让别的手指按更高品，中指之后的手指没有这个便利。
    """
    fretted = {i: p for i, p in enumerate(positions) if p is not None and p > 0}
    if not fretted:
        return [[]]

    fixed: list[_Unit] = []
    if use_index_barre:
        rng = _index_barre_range(positions)
        if rng is None:
            return []
        lo, hi = rng
        bf = min(fretted.values())
        fixed.append(_Unit(bf, lo, hi, is_barre=True, is_index_barre=True))
        rest = {i: p for i, p in fretted.items() if p > bf}
    else:
        rest = dict(fretted)

    # 剩余位置按品分组，每组切成连续弦段
    by_fret: dict[int, list[int]] = {}
    for i, p in rest.items():
        by_fret.setdefault(p, []).append(i)

    choices: list[list[list[_Unit]]] = []
    for fret, strings in sorted(by_fret.items()):
        for lo, hi in _runs(strings):
            width = hi - lo + 1
            per_string = [
                _Unit(fret, s, s, is_barre=False, is_index_barre=False)
                for s in range(lo, hi + 1)
            ]
            if width == 1:
                choices.append([per_string])
                continue
            opts = [per_string]
            if width <= MAX_NON_INDEX_BARRE and all(
                positions[s] == fret for s in range(lo, hi + 1)
            ):
                opts.append([_Unit(fret, lo, hi, is_barre=True, is_index_barre=False)])
            choices.append(opts)

    # choices 为空（食指横按吃掉了全部按弦）时 product() 产出一个空组合，正好得到 [fixed]
    return [
        fixed + [u for group in combo for u in group] for combo in product(*choices)
    ]


def _same_fret_reachable(
    units: Sequence[_Unit], positions: Sequence[int | None]
) -> bool:
    """同一品上的两根手指之间，不能夹着按在更高品的弦。

    手指是从指板上方压下去的：两根手指落在同一品的不同弦上时，它们中间若有一根弦被
    按在更高品，那根手指得从更高品的手指「上面」绕过去，实际弹不出来——真吉他手遇到
    这种情况只能改用食指横按压在下面。

    反例（这条规则加进来之前排在 F 前面的假指法）：``10321x``，6 弦 1 品和 2 弦 1 品
    要两根手指，中间夹着 4 弦 3 品和 3 弦 2 品；而 F 的 ``133211`` 用食指横按解决同一
    问题，所以合法。同为「同品两指」的 G ``320003`` 则没问题——6 弦 3 品和 1 弦 3 品
    之间夹的全是空弦，手指从上方跨过去不碰任何东西。
    """
    for a, b in combinations(sorted(units, key=lambda u: u.lo), 2):
        if a.fret != b.fret or a.is_barre or b.is_barre:
            continue
        for s in range(a.hi + 1, b.lo):
            p = positions[s]
            if p is not None and p > a.fret:
                return False
    return True


def _assign_fingers(units: Sequence[_Unit], base_fret: int) -> bool:
    """这些手指单元能否分配给递增的手指编号并满足张开距离。

    手指不交叉：品越高的单元用编号越大的手指。因此把单元按品升序排好后，只需从
    1..4 中挑一个递增子序列，检查任意两单元的品距不超过「手指编号间隔 × 每指跨度」。
    单元数 <= 4，候选组合最多 6 种，直接枚举。
    """
    if not units:
        return True
    if len(units) > MAX_FINGERS:
        return False
    ordered = sorted(units, key=lambda u: (u.fret, u.lo))
    reach = _reach(base_fret)
    k = len(ordered)
    for combo in combinations(range(1, MAX_FINGERS + 1), k):
        if all(
            ordered[j].fret - ordered[i].fret <= (combo[j] - combo[i]) * reach + 1e-9
            for i in range(k)
            for j in range(i + 1, k)
        ):
            return True
    return False


def plan_fingers(positions: Iterable[int | None]) -> FingerPlan | None:
    """求最省力的手指分配方案；分配不出来返回 ``None``（= 弹不了）。

    这是原来 ``max_stretch`` 跨度检查够不到的一层约束。反例：``(1,4,None,4,None,4)``
    跨度只有 3，但 1 品和 4 品上共有四段互不相连的弦，需要五根手指，跨度检查放行、
    手指分配拒绝。

    Parameters
    ----------
    positions
        指法把位序列（按弦号，与 ``Fretboard.tones`` 同序），``None`` 闷音，``0`` 空弦。

    Returns
    -------
    FingerPlan | None
        代价最低的可行方案；无可行方案为 ``None``。全空弦指法返回 0 指方案。
    """
    positions = list(positions)
    fretted = [p for p in positions if p is not None and p > 0]
    if not fretted:
        return FingerPlan(0, None, 0, 0, 0.0)

    base_fret = min(fretted)
    span = max(fretted) - base_fret

    best: FingerPlan | None = None
    # 有横按可能时，「用食指横按」和「逐弦按」都要试：横按省手指但本身有代价，
    # 例如 A 的 x02220 逐弦三指比横按更常用。
    for use_barre in (True, False):
        for units in _unit_partitions(positions, use_index_barre=use_barre):
            if not _same_fret_reachable(units, positions):
                continue
            if not _assign_fingers(units, base_fret):
                continue
            n_fingers = len(units)
            index_barre = next((u for u in units if u.is_index_barre), None)
            others = [u for u in units if u.is_barre and not u.is_index_barre]
            cost = n_fingers * W_FINGER
            if index_barre is not None:
                cost += W_INDEX_BARRE
            cost += len(others) * W_OTHER_BARRE
            cost += span * W_SPAN
            if base_fret <= 2:
                cost += span * W_LOW_POSITION_SPAN
            cost += base_fret * W_MIN_FRET
            if best is None or cost < best.hand_cost:
                best = FingerPlan(
                    n_fingers=n_fingers,
                    barre_fret=index_barre.fret if index_barre else None,
                    barre_width=(index_barre.hi - index_barre.lo + 1)
                    if index_barre
                    else 0,
                    n_other_barres=len(others),
                    hand_cost=cost,
                )
    return best


def count_muted(
    positions: Sequence[int | None], open_tones: Sequence["Tone"] | None = None
) -> tuple[int, int, int]:
    """闷弦数，拆成 (内部, 低音侧, 高音侧)。

    内部闷音指被发音弦夹在中间的闷弦，扫弦时必须靠指腹精确挡住，实战里几乎只出现在
    刻意设计的 voicing 里；外侧闷音（``x32010`` 的 6 弦、``xx0232`` 的 6/5 弦）只是
    少扫两根，代价小得多。三者用同一个权重会让 ``(x,3,2,0,x,0)`` 这类假指法混进前列。

    内部/外侧按**弦号**判定（扫弦时挡不挡得住是物理位置问题），高音侧/低音侧则按
    **空弦音高**判定——自定义调弦下弦号 0 未必是最低音（如 ``C4-G3-D3-A2``），
    只有比音高才不会把顶音弦错当成低音弦。``open_tones`` 省略时全部算低音侧。
    """
    sounding = [i for i, p in enumerate(positions) if p is not None]
    if not sounding:
        return 0, 0, 0
    lo, hi = min(sounding), max(sounding)
    inner = sum(1 for i in range(lo, hi + 1) if positions[i] is None)
    outer = [i for i, p in enumerate(positions) if p is None and not lo <= i <= hi]
    if open_tones is None:
        return inner, len(outer), 0
    top = max(open_tones[i].midi for i in sounding)
    high = sum(1 for i in outer if open_tones[i].midi > top)
    return inner, len(outer) - high, high


def required_pitch_classes(chord: "Chord") -> set[int]:
    """和弦里**不能省**的音级。

    吉他只有六根弦、四根手指，扩展和弦装不下全部音，实战按固定优先级取舍：
    根音、三音（或 sus 的二/四音）、七音、九音这些决定和弦性质的音必须在，
    **完全五音最先省**——它对和弦色彩贡献最小，且已在根音的泛音列里。
    十一音在音数 >= 5 的和弦里也可省（它和三音相差半音，堆在一起反而浑浊）。

    减五、增五（root+6 / root+8）不在可省之列：它们正是 ``dim`` / ``aug`` 的
    定性音，省掉 Cm7b5 的 Gb 就退化成 Cm7，属于换了个和弦而不是「微量不一致」。

    三和弦（音级数 <= 3）不允许任何省略：省掉五音只剩根+三两个音级，识别度太低，
    ``C`` 会和 ``Am7`` 之类共用同一个手型。
    """
    pcs = set(chord.pitch_classes)
    if len(pcs) <= 3:
        return pcs
    root = chord.root.midi % 12
    optional = set()
    fifth = (root + 7) % 12
    if fifth in pcs:
        optional.add(fifth)
    if len(pcs) >= 5:
        eleventh = (root + 5) % 12
        if eleventh in pcs:
            optional.add(eleventh)
    return pcs - optional


def playability_cost(
    positions: Sequence[int | None],
    tones: Sequence["Tone"],
    *,
    root_pc: int | None = None,
    n_omitted: int = 0,
    plan: FingerPlan | None = None,
    open_tones: Sequence["Tone"] | None = None,
) -> float:
    """指法的总代价，越小越顺手、越像常用指法。

    在 :attr:`FingerPlan.hand_cost`（手型难度）之上叠加音乐层面的代价：闷音、
    发音弦数、空弦数、省略的和弦音、是否转位。

    与旧的 :func:`fingering_enumerator.rank_key` 的区别是**可交换**：低把位不再
    无条件压过一切，而是折算成 ``W_MIN_FRET`` 的分数，可以被「少一次横按」或
    「少一根内部闷弦」换掉。

    Parameters
    ----------
    positions, tones
        指法的把位序列与发音音符（``Fingering.positions`` / ``.tones``）。
    root_pc
        和弦根音音高类；给出时检测最低音是否为根音（用 ``min(midi)``，不依赖弦序）。
    n_omitted
        相对目标和弦省掉了几个音级。
    plan
        已算好的手指方案，避免重复计算；``None`` 时内部调用 :func:`plan_fingers`。
    open_tones
        指板各弦空弦音（``Fretboard.tones``）。给出时才能区分闷掉的是低音弦还是
        顶音弦，见 :func:`count_muted`。
    """
    if plan is None:
        plan = plan_fingers(positions)
    if plan is None:
        return float("inf")

    inner_mute, low_mute, high_mute = count_muted(positions, open_tones)
    n_open = sum(1 for p in positions if p == 0)

    cost = plan.hand_cost
    cost += inner_mute * W_INNER_MUTE
    cost += low_mute**2 * W_LOW_MUTE
    cost += high_mute**2 * W_HIGH_MUTE
    cost += len(tones) * W_SOUNDED
    cost += n_open * W_OPEN
    cost += n_omitted * W_OMISSION

    if root_pc is not None and tones:
        lowest = min(tones, key=lambda t: t.midi)
        if lowest.midi % 12 != root_pc:
            cost += W_INVERSION
    return cost
