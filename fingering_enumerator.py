"""指板和弦指法枚举器。

pytheory 内置的 ``Fretboard.chord(name)`` 只返回标准调弦下的一个默认指法，
换调弦后查表失效，且不会枚举所有可行 voicing。本模块在 pytheory 之上补足
这一层：给定一个和弦与指板（任意调弦），枚举指板上所有能弹出该和弦的指法，
并按可演奏性评分排序。

核心思路是组合搜索：对每根弦，找出 0..max_fret 中所有能发出和弦内音的把位
（含"闷音"选项），做笛卡尔积，再按物理可行性（最大跨度、指头数、是否含根音）
剪枝。底层音高计算由 pytheory 的 ``Tone.transpose`` 完成，与调弦无关，因此
对任意调弦都成立。
"""

from __future__ import annotations

from collections.abc import Iterable
from itertools import product
from typing import TYPE_CHECKING, Literal

import pytheory

from playability import plan_fingers, playability_cost, required_pitch_classes

if TYPE_CHECKING:
    from pytheory import Chord, Fingering, Fretboard, Tone


__all__ = ["enumerate_fingerings", "score_fingering", "rank_key", "analyze_barre", "is_redundant_thumb"]


def _pitch_class(tone: "Tone") -> int:
    """音高类（0..11）。pytheory 的 Tone 没有 pitch_class 属性，用 midi 折算。"""
    return tone.midi % 12


def _fretted_positions(positions: Iterable[int | None]) -> list[int]:
    """从指法把位序列中挑出实际按下的（非闷音）把位。"""
    return [p for p in positions if p is not None]


def score_fingering(fingering: "Fingering", root_pc: int | None = None) -> float:
    """给一个指法打可演奏性分，越高越好。

    评分目标是一个"节奏模板库"会想要的指法：**完整覆盖和弦音**优先于"少按"，
    其次才是跨度小、按弦指头少。因此闷音（不弹的弦）扣分最重，避免出现
    "只弹 3 根弦就排第一"的退化结果。

    Parameters
    ----------
    fingering
        ``pytheory.Fingering`` 实例。
    root_pc
        和弦根音的音高类（0..11）。传入后会检测指法的实际最低音是否为根音
        （即原位 voicing），是则加分。最低音用 ``min(tones, key=midi)`` 确定，
        **不依赖弦序**，因此在自定义调弦（第 0 弦未必是最低音）下同样成立。
    """
    positions = fingering.positions
    fretted = _fretted_positions(positions)
    if not fretted:
        return float("-inf")

    span = max(fretted) - min(fretted)
    # 空弦（0 品）不需要按弦手指，只算非零把位。
    n_fingers = sum(1 for p in fretted if p > 0)
    n_muted = sum(1 for p in positions if p is None)

    tones = fingering.tones
    n_sounded = len(tones)

    score = 0.0
    # 覆盖度：闷音越少越好（权重最高）。完整 voicing 应优先于"弹得少"。
    score -= n_muted * 3.0
    # 物理可达性：跨度与指头数次之。
    score -= span * 2.0
    score -= n_fingers * 1.0
    # 略微鼓励更多发音弦，倾向饱满 voicing。
    score += n_sounded * 0.5

    # 原位检测：仅当传入根音时进行。用实际最低音（按音高，非弦序）判断。
    if root_pc is not None and tones:
        lowest = min(tones, key=lambda t: t.midi)
        if _pitch_class(lowest) == root_pc:
            score += 2.0
    return score


def analyze_barre(positions: Iterable[int | None]) -> tuple[int, int | None]:
    """计算横按感知的指头数。

    横按（barre）：食指平压在最低按弦品 ``barre_fret``，覆盖一段**连续弦区间**
    ``[lo, hi]``。区间内每根弦要么等于 ``barre_fret``（被食指直接压响），要么
    高于 ``barre_fret``（被其他指头按更高品发音，食指仍平压在此弦的
    ``barre_fret`` 位置）。区间内不得出现闷音（``None``）或空弦（``0``）——
    前者食指需抬起绕过、后者该弦不按，二者都会打断食指的平压。

    判定规则：
    1. ``barre_fret`` 取所有按弦（>0）品的最低值。
    2. 严格等于 ``barre_fret`` 的弦至少 2 根，否则无法横按，退化为逐弦计数。
    3. ``[lo, hi]`` 区间内每根弦必须 >0 且 >= ``barre_fret``（即不得 None/0/更低品）。
       满足则食指横按压住该区间，区间内 > ``barre_fret`` 的弦为单音，
       区间外 > ``barre_fret`` 的弦亦为单音。

    这正确识别了标准横按和弦：F 大和弦 ``(1,3,3,2,1,1)`` 中食指横按 1 品压
    E/B/e 三弦，A/D/G 为高品单音，故指头数 = 1(横按) + 3(单音) = 4，而非逐弦
    计数所得的 6。

    Parameters
    ----------
    positions
        指法把位序列（低音弦 -> 高音弦），``None`` 表闷音，``0`` 表空弦。

    Returns
    -------
    (n_fingers, barre_fret)
        ``n_fingers`` 为含横按优化的指头数；``barre_fret`` 为横按品，无横按时为
        ``None``。若该指法可横按，区间内 ``= barre_fret`` 的弦不再各算一个指头。
    """
    fretted = [(i, p) for i, p in enumerate(positions) if p is not None and p > 0]
    if not fretted:
        return 0, None
    barre_fret = min(p for _, p in fretted)
    barre_strings = [i for i, p in fretted if p == barre_fret]
    if len(barre_strings) < 2:
        # 最低品只有 1 根弦，无法横按；逐弦计数（每个 >0 品算 1 指）。
        return len(fretted), None
    lo, hi = min(barre_strings), max(barre_strings)
    for i in range(lo, hi + 1):
        p = positions[i]
        if p is None or p == 0 or p < barre_fret:
            # 区间被打断（闷音/空弦/更低品），无法平压，退化为逐弦计数。
            return len(fretted), None
    # 横按成立：食指覆盖 [lo, hi]，区间内 = barre_fret 的弦由食指压响，
    # 其余 > barre_fret 的弦（区间内外）为单音。
    single = [i for i, p in fretted if p > barre_fret]
    return 1 + len(single), barre_fret


def is_redundant_thumb(
    positions: Iterable[int | None],
    chord_pcs: set[int],
    open_tones: Iterable["Tone"],
) -> bool:
    """检测"冗余大拇指按法"并建议排除。

    背景：吉他低音弦（最粗那根）若单独按在孤立高把位，需用大拇指绕过琴颈按；
    而同时下方（高音弦）还在按弦时，大拇指够不到高把位，物理上不可行或不自然。
    典型案例：A 大和弦的 ``(5,0,2,2,2,0)``——6 弦 5 品要按出低八度根音 A2，
    但 5 弦 0 品已是根音 A2，故 6 弦 5 品冗余；标准指法 ``x02220`` 闷掉它即可。

    判据（两者同时满足）：
    1. **大拇指按法**：最低音按弦弦的把位，比相邻最近的按弦弦高 **2 品以上**
       （``lowest_fret > nearest_fret + 2``）。只与相邻按弦弦比较，避免被
       高音弦的同把位蒙蔽（如 ``(5,0,2,2,2,5)`` 中 e 弦 5 品把其他弦最大把位
       拉高，但 6 弦 5 品仍需大拇指）。
    2. **冗余**：闷掉最低音按弦弦后，指法发音的音高类集合仍是目标和弦的超集
       （即该按弦提供的音级被其他弦重复提供，闷掉不损完整性）。

    横按和弦不会被误判：横按的最低品就是横按品，多根弦在该品，最低音弦不孤立。
    八度重复（开放和弦天然有重复音级）也不会误判：判据 1 的"孤立高把位"约束
    避开了正常八度重复。

    Parameters
    ----------
    positions
        指法把位序列（低音弦 -> 高音弦）。
    chord_pcs
        目标和弦的音高类集合。
    open_tones
        指板各弦空弦音高（``Fretboard.tones``），与 ``positions`` 同序。

    Returns
    -------
    bool
        为 True 表示该指法是冗余大拇指按法，建议在排序中降级或排除。
    """
    positions = list(positions)
    open_tones = list(open_tones)
    fretted = [(i, p) for i, p in enumerate(positions) if p is not None and p > 0]
    if len(fretted) < 2:
        return False
    fretted.sort()  # 按弦号升序（低音 -> 高音）
    lowest_fret = fretted[0][1]
    nearest_fret = fretted[1][1]  # 相邻最近的按弦弦
    if lowest_fret <= nearest_fret + 2:
        return False
    # 闷掉最低音按弦弦，检查是否仍完整。
    lowest_idx = min(i for i, _ in fretted)
    muted = list(positions)
    muted[lowest_idx] = None
    present = {
        _pitch_class(open_tones[i].transpose(p))
        for i, p in enumerate(muted)
        if p is not None
    }
    return chord_pcs.issubset(present)


def rank_key(fingering: "Fingering", root_pc: int | None = None) -> tuple:
    """指法排序键，越小越靠前。采用分层（字典序）而非线性加权。

    分层排序按优先级逐级比较，避免权重冲突导致的退化结果（例如高把位 6 弦
    横按因"弦多"压过经典 5 弦开放指法、或单弦指法因"指头少"排到最前）：

    1. **原位优先**：最低音为根音（原位）的指法排在转位之前。最低音用
       ``min(tones, key=midi)`` 确定，**不依赖弦序**，在自定义调弦
       （第 0 弦未必是最低音）下同样成立。
    2. **最低把位优先**：按弦品最小值升序，开放/低把位指法优于高把位横按。
       这是吉他指法的核心惯例：能用开放和弦就不去高把位横按。
    3. **完整覆盖优先**：发音弦数多的指法排前（饱满 voicing 优于闷音简化）。
       置于把位之后，避免"凑满 6 弦的高把位横按"压过低把位开放指法。
    4. **可演奏性**：横按感知指头数少（见 :func:`analyze_barre`）、
       跨度小的排前。

    Parameters
    ----------
    fingering
        ``pytheory.Fingering`` 实例。
    root_pc
        和弦根音音高类。若为 ``None`` 则跳过原位判定（第 1 层恒为 0）。
    """
    positions = fingering.positions
    fretted = _fretted_positions(positions)
    if not fretted:
        return (1, 99, 0, 99, 99)  # 极端靠后

    tones = fingering.tones
    n_sounded = len(tones)

    # 第 1 层：原位 = 0，转位 = 1
    if root_pc is not None and tones:
        lowest = min(tones, key=lambda t: t.midi)
        is_root_position = 0 if _pitch_class(lowest) == root_pc else 1
    else:
        is_root_position = 0

    min_fret = min(fretted)
    # 横按感知指头数（取代逐弦计数）。
    n_fingers, _barre = analyze_barre(positions)
    span = max(fretted) - min(fretted)

    # 越小越好：原位(0)<转位(1)；把位低优先；发音多(-n_sounded 小)优先；指头少优先；跨度小优先
    return (is_root_position, min_fret, -n_sounded, n_fingers, span)


def enumerate_fingerings(
    chord: "str | Chord",
    fretboard: "Fretboard",
    *,
    max_fret: int = 12,
    max_stretch: int = 4,
    require_root: bool = True,
    mute_allowed: bool = True,
    strict: bool = True,
    ranking: Literal["playable", "legacy"] = "playable",
    allow_omissions: bool = True,
    limit: int | None = None,
) -> list["Fingering"]:
    """枚举指板上所有能弹出 ``chord`` 的指法。

    Parameters
    ----------
    chord
        和弦名（如 ``"C"``、``"Am7"``、``"F#maj7"``）或 ``pytheory.Chord`` 对象。
    fretboard
        ``pytheory.Fretboard`` 实例，可为任意调弦（``Fretboard.guitar(tuning=...)``）。
    max_fret
        最高品，限制搜索范围。
    max_stretch
        指法最大跨度（最高品与最低品之差），物理可达性约束。
    require_root
        是否要求和弦根音必须出现在指法中。绝大多数情况下应保持 True。
    mute_allowed
        是否允许闷音（不弹某些弦）。允许则结果更多样，禁则只保留跨全部弦的指法。
    strict
        为 True 时施加音乐完整性过滤（必需音级齐全、非 power chord、非冗余大拇指）。
        False 则保留所有和弦内音组合。在 ``"playable"`` 模式下，物理可行性（手指
        分配）**不受 strict 控制**：``strict=False`` 也不会放行按不出来的手型。
    ranking
        ``"playable"``（默认）用 :func:`playability.playability_cost` 的连续代价模型
        排序，并把手指分配可行性作为硬约束，结果更接近吉他手实际会用的指法；
        ``"legacy"`` 走旧的 ``identify()`` 硬检查 + :func:`rank_key` 分层排序。
    allow_omissions
        仅 ``"playable"`` 模式有效。允许按吉他惯例省略和弦音（完全五音、
        音数 >= 5 时的十一音），省一个音在排序里计一次
        :data:`playability.W_OMISSION` 惩罚。三和弦永远不省，
        详见 :func:`playability.required_pitch_classes`。
    limit
        若给定，只返回排序最靠前的 N 个指法。

    Returns
    -------
    list[Fingering]
        排序后的指法列表。``"playable"`` 下按可演奏性代价升序（越顺手越靠前），
        ``"legacy"`` 下按 :func:`rank_key` 分层排序。
    """
    if isinstance(chord, str):
        chord = pytheory.Chord.from_symbol(chord)

    target_pcs = set(chord.pitch_classes)
    root_pc = _pitch_class(chord.root)
    open_tones = fretboard.tones               # 低音弦 -> 高音弦

    # 必需音级：playable 模式下允许按惯例省略五音等，legacy 模式要求全含。
    if ranking == "playable" and allow_omissions:
        required_pcs = required_pitch_classes(chord)
    else:
        required_pcs = target_pcs

    # 每根弦可选把位：None(闷音) 或落在和弦内音上的品
    options: list[list[int | None]] = []
    for open_tone in open_tones:
        opts: list[int | None] = []
        if mute_allowed:
            opts.append(None)
        for fret in range(0, max_fret + 1):
            if _pitch_class(open_tone.transpose(fret)) in target_pcs:
                opts.append(fret)
        options.append(opts)

    results: list["Fingering"] = []
    scored: list[tuple[float, "Fingering"]] = []
    for combo in product(*options):
        if all(p is None for p in combo):
            continue

        fretted = _fretted_positions(combo)
        if fretted and (max(fretted) - min(fretted)) > max_stretch:
            continue

        present_pcs = {
            _pitch_class(open_tones[i].transpose(p))
            for i, p in enumerate(combo)
            if p is not None
        }
        if require_root and root_pc not in present_pcs:
            continue

        if ranking == "playable":
            if strict:
                # 必需音级齐全。省略只发生在 required_pitch_classes 允许的音上，
                # 且 options 只取和弦内音，故不会引入非和弦音——E sus4(E,A,B) 想冒充
                # A major 仍会因缺三音 C# 被拒。
                if not required_pcs.issubset(present_pcs):
                    continue
                if is_redundant_thumb(combo, required_pcs, open_tones):
                    continue
            # 物理硬约束：四根手指真的按得出来。跨度检查放行、手指不够的手型在此剔除。
            plan = plan_fingers(combo)
            if plan is None:
                continue
            fingering = fretboard.fingering(*combo)
            cost = playability_cost(
                combo,
                fingering.tones,
                root_pc=root_pc,
                n_omitted=len(target_pcs - present_pcs),
                plan=plan,
                open_tones=open_tones,
            )
            scored.append((cost, fingering))
            continue

        fingering = fretboard.fingering(*combo)

        if strict:
            # 完整性硬检查：指法发音的音高类集合必须是目标和弦音级集合的超集，
            # 即所有和弦音（根、三、五及扩展音）都必须被覆盖。这能过滤掉只含根音
            # 和部分和弦音、却混入非和弦音的"伪 voicing"——例如把 E sus4（E,A,B）
            # 误当作 A major（A,C#,E）输出，因为它虽含根音 A、却缺三音 C# 且多了 B。
            present_pcs = {_pitch_class(t) for t in fingering.tones}
            if not target_pcs.issubset(present_pcs):
                continue

            identified = fingering.identify()
            if identified is None:
                continue
            # 排除 power chord 与空识别。
            if "power" in identified or identified in ("None", ""):
                continue
            # 排除冗余大拇指按法：低音弦孤立高把位且闷掉后仍完整。
            # 如 A 大和弦 (5,0,2,2,2,0) 中 6 弦 5 品的低八度根音冗余、需大拇指。
            if is_redundant_thumb(combo, target_pcs, open_tones):
                continue

        results.append(fingering)

    if ranking == "playable":
        # 稳定排序：代价相同时保持 product 的生成顺序（把位由低到高），结果确定。
        scored.sort(key=lambda pair: pair[0])
        results = [f for _, f in scored]
    else:
        results.sort(key=lambda f: rank_key(f, root_pc=root_pc))

    if limit is not None:
        results = results[:limit]

    return results
