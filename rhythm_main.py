"""扫弦节奏型选型演示 + 内联验证入口。

``uv run rhythm_main.py`` 是节奏型子包的验证方式：下面每段演示都带 ``assert``，
断言失败即视为回归。改 ``rhythm_pattern/strum_patterns.py`` 的模板库或权重
（``pattern_cost`` 的 ``W_*`` 常量）后必须跑一遍。
"""

from rhythm_pattern import STRUM_PATTERNS, enumerate_rhythm_patterns
from pytheory import Fretboard


# 基准集：一组「进行 + 段落 + 风格」配上期望排在前列的模板名。
# 类比 main.py 的常用指法基准集--新排序模型必须让每个进行的首选模板排进前 TOP_N。
# 期望不是「唯一正解」（弹唱节奏型本就不唯一），而是「公认的顺理成章之选」，
# 命中其一即算通过。
BENCHMARK: list[tuple[list[tuple[str, int]], str, str, list[str]]] = [
    # 4 拍副歌流行 -> 经典 4/4 流行扫弦。
    ([("C", 4)], "chorus", "pop", ["pop D-DU-U-DU"]),
    # 每和弦 1 拍的流行副歌 -> 16 分「下下下上」撑满短和弦时间。
    # 注：1 拍和弦时间短，8 分（pop 8th-notes）每拍仅 2 音偏疏，16 分 D-D-DU（密度 1.0）
    # 更贴副歌饱满度；pop 8th-notes 退守 2 拍副歌甜区（见 _show 演示）。
    ([("C", 1), ("G", 1), ("Am", 1), ("F", 1)], "chorus", "pop", ["D-D-DU (1拍16分)"]),
    # 每和弦 1 拍的摇滚副歌 -> 全下扫重拍。
    ([("C", 1), ("G", 1), ("Am", 1), ("F", 1)], "chorus", "rock", ["rock 8th down"]),
    # 主歌民谣 4-2-2：4 拍 C 用 53231323 分解（民谣经典动作），2 拍 G/Am 用 folk D-DU 扫弦。
    # 分解模板引入后，4 拍专属整动机在 verse folk 上合理胜出；2 拍和弦放不下 4 拍分解动机，
    # 退回扫弦。技法基线 None 下扫/拆混排，符合段落级混排预期。
    ([("C", 4), ("G", 2), ("Am", 2)], "verse", "folk", ["53231323 (8分)", "folk D-DU"]),
]

TOP_N = 3
"""期望模板必须排进每个和弦前几名。"""


def _fmt(cells) -> str:
    """栅格可视化：D/U/. 一格一字符，每 4 格（一拍）用空格隔开。"""
    s = "".join(str(c) if c is not None else "." for c in cells)
    return " ".join(s[i : i + 4] for i in range(0, len(s), 4))


def _show(progression, section, style, gtr) -> None:
    """打印一段进行选出的节奏栅格，不参与断言。"""
    print(f"\n=== {section} / {style} / {progression} ===")
    events = enumerate_rhythm_patterns(progression, gtr, section=section, style=style)
    for e in events:
        print(f"  {e.chord:4s} {e.beats}拍 -> {e.pattern.name:16s} [{_fmt(e.grid.cells)}]")


def check_benchmark(gtr) -> None:
    """核心回归：每个进行的公认首选模板必须排进前列。"""
    print("=== 节奏型基准集 ===")
    failures: list[str] = []
    for progression, section, style, wanted in BENCHMARK:
        events = enumerate_rhythm_patterns(progression, gtr, section=section, style=style)
        # 每个和弦都应选到「期望模板集合」之一。这里基准集里的进行要么每和弦同拍、
        # 要么结构简单，公认首选应对所有和弦一致；取第一个和弦的选中模板做代表。
        first = events[0]
        ok = first.pattern.name in wanted
        # 进一步：对多和弦进行，验证所有和弦选中模板都在期望集合内或与其同类。
        all_in = all(e.pattern.name in wanted for e in events)
        if not (ok and all_in):
            failures.append(
                f"{progression} [{section}/{style}]: 期望 {wanted}，"
                f"实际 {[e.pattern.name for e in events]}"
            )
        print(
            f"  {'OK ' if (ok and all_in) else 'FAIL'} {section:8s} {style:5s} "
            f"{str(progression):38s} -> {[e.pattern.name for e in events]}"
        )
    assert not failures, "节奏型首选未排进前列:\n  " + "\n  ".join(failures)
    print(f"  断言通过: {len(BENCHMARK)} 个进行的公认首选模板全部命中")


def check_min_beats(gtr) -> None:
    """拍数门槛是硬约束：占 N 拍的和弦不会返回 min_beats > N 的模板。"""
    print("\n=== 拍数门槛 ===")
    # 占 1 拍的和弦，候选模板必须 min_beats <= 1。
    events = enumerate_rhythm_patterns([("C", 1)], gtr, section="chorus", style="pop")
    for e in events:
        assert e.pattern.min_beats <= 1, (
            f"占 1 拍却选了 min_beats={e.pattern.min_beats} 的模板 {e.pattern.name}"
        )
    # 占 4 拍的和弦可命中 min_beats=4 的模板（验证 4 拍经典扫弦能被选中）。
    e4 = enumerate_rhythm_patterns([("C", 4)], gtr, section="chorus", style="pop")[0]
    print(f"  1拍和弦 -> 选中 {events[0].pattern.name} (min_beats={events[0].pattern.min_beats})")
    print(f"  4拍和弦 -> 选中 {e4.pattern.name} (min_beats={e4.pattern.min_beats})")
    # pop D-DU-U-DU 的 min_beats=4，只在占 >=4 拍时才应出现。
    # 反查：占 1 拍时绝不能选到它。
    assert all(e.pattern.name != "pop D-DU-U-DU" for e in events), (
        "占 1 拍不应选到 min_beats=4 的 pop D-DU-U-DU"
    )
    print("  断言通过: 占 1 拍不返回 min_beats>=2 的模板；4 拍周期模板仅占 >=4 拍时出现")


def check_grid_length(gtr) -> None:
    """栅格长度 = 4 × 拍数，每拍 4 个 16 分位置。"""
    print("\n=== 栅格对齐 ===")
    for beats in (1, 2, 3, 4):
        e = enumerate_rhythm_patterns([("C", beats)], gtr, section="chorus", style="pop")[0]
        assert len(e.grid.cells) == 4 * beats, (
            f"占 {beats} 拍应得 {4 * beats} 格，实际 {len(e.grid.cells)}"
        )
        assert e.grid.n_beats == beats, f"n_beats 应为 {beats}，实际 {e.grid.n_beats}"
        print(f"  {beats}拍 -> {len(e.grid.cells)}格 OK  [{_fmt(e.grid.cells)}]")
    print("  断言通过: 任意拍数栅格长度 = 4 × 拍数")


def check_progression_continuity(gtr) -> None:
    """进行级连贯性：整段进行选出的模板风格/密度不出现逐和弦乱跳。"""
    print("\n=== 进行级连贯性 ===")
    # 一段 4-2-2 的主歌民谣，应整段落在 verse 适用的模板里。
    prog = [("C", 4), ("G", 2), ("Am", 2)]
    events = enumerate_rhythm_patterns(prog, gtr, section="verse", style="folk")
    names = [e.pattern.name for e in events]
    verse_patterns = {p.name for p in STRUM_PATTERNS if "verse" in p.sections}
    assert all(n in verse_patterns for n in names), (
        f"主歌进行应只选 verse 适用模板，实际 {names}"
    )
    print(f"  {prog} -> {names}  (全部落在 verse 适用模板内)")
    print("  断言通过: 主歌进行不混入副歌专用模板")


def check_technique_baseline(gtr) -> None:
    """技法基线（段落级混排）：musicnn 推出的段落技法倾向驱动扫/拆切换。

    同一段主歌民谣进行：

    - ``technique_baseline="arpeggio"`` -> 把所有和弦切到分解模板；
    - ``"strum"`` -> 把所有和弦压成扫弦模板；
    - ``"mixed"`` / ``None`` -> 不干预，选型器自由选（可能扫拆混排，如 4 拍 C 选
      分解、2 拍 G/Am 选扫弦）。

    ``W_TECHNIQUE`` 罚分足够大，能压过密度/段落契合的小差异，实现段落级「整段扫 vs
    整段拆」的切换；``mixed``/``None`` 则暴露选型器自身的扫/拆倾向。
    """
    print("\n=== 技法基线（段落级混排）===")
    prog = [("C", 4), ("G", 2), ("Am", 2)]

    def _names(base):
        ev = enumerate_rhythm_patterns(
            prog, gtr, section="verse", style="folk", technique_baseline=base
        )
        return [e.pattern.name for e in ev]

    # arpeggio 基线：整段切分解。
    arp_names = _names("arpeggio")
    arp_patterns = {p.name for p in STRUM_PATTERNS if p.is_arpeggio}
    assert all(n in arp_patterns for n in arp_names), (
        f"arpeggio 基线应整段选分解模板，实际 {arp_names}"
    )

    # strum 基线：整段压成扫弦。
    strum_names = _names("strum")
    strum_patterns = {p.name for p in STRUM_PATTERNS if p.is_strum}
    assert all(n in strum_patterns for n in strum_names), (
        f"strum 基线应整段选扫弦模板，实际 {strum_names}"
    )

    # mixed / None 基线：不干预，允许扫拆混排（不强求全扫或全拆）。
    for base in ("mixed", None):
        names = _names(base)
        print(f"  baseline={base!s:7s} -> {names}  (不干预，自由选型)")

    print(f"  baseline=arpeggio -> {arp_names}")
    print(f"  baseline=strum    -> {strum_names}")
    print("  断言通过: arpeggio 切分解；strum 压扫弦；mixed/None 不干预自由选型")


def check_string_roles(gtr) -> None:
    """弦角色实例化：分解模板的 Pluck.role 按和弦 voicing 解析成具体弦号。

    核心回归「5,3,21」：C 和弦根音在 5 弦、顶两弦选 2-1（顶音距合适、丰富）；
    G 和弦根音在 6 弦更低、顶两弦改选 3-2 收窄顶底音距、避免尖锐。同一套弦角色
    (Root→Fifth(avoid_bass)→TopN(2,comfortable)) 在不同和弦上解析出不同弦号，
    证明弦序随和弦走、调弦中立。同时验证 53231323 的音级角色映射。
    """
    print("\n=== 弦角色实例化 ===")
    from rhythm_pattern import Pluck, STRUM_PATTERNS
    from rhythm_pattern.strum_patterns import _instantiate_plucks, _resolve_voicing

    def gtr_strings(ns):
        """弦号下标 -> 吉他手习惯弦号（0=6弦 → 6，5=1弦 → 1）。"""
        return tuple(6 - n for n in ns) if ns else None

    # 直接实例化 root-5-top2 模板（选型器未必选它，但实例化逻辑独立可测）。
    tpl = next(p for p in STRUM_PATTERNS if "root-5-top2" in p.name)
    # C: 5,3,21；G: 6,4,32（根音更低→顶两弦收窄）。
    cases = {"C": (5, 3, (2, 1)), "G": (6, 4, (3, 2))}
    for chord, (root, fifth, top2) in cases.items():
        v = _resolve_voicing(chord, gtr, max_stretch=4)
        grid = _instantiate_plucks(tpl.grid_for(1), v)
        plucks = [c for c in grid.cells if isinstance(c, Pluck) and c.strings]
        got = tuple(gtr_strings(c.strings) for c in plucks)
        # 期望：(根音弦号, 五音弦号, 顶两弦元组)。
        want = ((root,), (fifth,), top2)
        assert got == want, (
            f"{chord} 弦角色实例化: 期望 {want}，实际 {got}"
        )
        print(f"  {chord}: Root={root} Fifth={fifth} TopN(2,comfortable)={top2}  OK")

    # 53231323 在 C 上应实例化出 5-3-2-3-1-3-2-3 的弦序（音级角色映射）。
    tpl5323 = next(p for p in STRUM_PATTERNS if p.name == "53231323 (16分)")
    v_c = _resolve_voicing("C", gtr, max_stretch=4)
    grid_c = _instantiate_plucks(tpl5323.grid_for(2), v_c)
    seq = [gtr_strings(c.strings)[0] for c in grid_c.cells
           if isinstance(c, Pluck) and c.strings]
    assert seq == [5, 3, 2, 3, 1, 3, 2, 3], (
        f"C 上 53231323 弦序应为 [5,3,2,3,1,3,2,3]，实际 {seq}"
    )
    print(f"  C: 53231323 -> {seq}  OK (音级角色还原经典指法)")
    print("  断言通过: 弦角色按 voicing 实例化，C 选 21 / G 选 32 自适应")


def check_selection_context(gtr) -> None:
    """SelectionContext 抽象层：拍号、BPM 维度介入 + 缺省降级。

    验证三件事：

    1. **拍号契合**：``ctx.time_signature=(3,4)`` 下，``motif_beats=4`` 的 4 拍周期
       模板（pop D-DU-U-DU）吃 ``W_TIME_SIG`` 罚分，应让位能整周期对齐的短动机模板。
    2. **BPM 可演奏性**：``ctx.bpm=180``（高 BPM）下，``53231323 (16分)`` 这类密度 1.0
       的过密分解模板吃 ``W_BPM_HIGH`` 罚分，让位低密度模板。
    3. **缺省降级**：``SelectionContext()`` 全空字段时，输出与旧式调用
       ``enumerate_rhythm_patterns(progression, gtr)`` 完全一致（拍号/BPM 不介入）。
    """
    print("\n=== SelectionContext 抽象层 ===")
    from rhythm_pattern import SelectionContext

    # ── 1. 拍号契合：3/4 拍号下 4 拍周期模板被罚分让位 ──
    # 占 4 拍的和弦：4/4（缺省）下 pop D-DU-U-DU（motif_beats=4）靠整动机奖励 + ideal_beats
    # 命中胜出。显式给 (3,4) 拍号后，该模板吃 W_TIME_SIG 罚分（4 拍动机在 3/4 拍号下不周期
    # 对齐），应被压下让位其他模板。注意：min_beats=4 不会被拍号筛掉（仍占 4 拍），靠罚分降级。
    prog = [("C", 4)]
    e_44 = enumerate_rhythm_patterns(prog, gtr, section="chorus", style="pop")[0]
    e_34 = enumerate_rhythm_patterns(
        prog, gtr, ctx=SelectionContext(section="chorus", style="pop", time_signature=(3, 4))
    )[0]
    print(f"  4拍和弦 4/4(缺省) -> {e_44.pattern.name}")
    print(f"  4拍和弦 (3,4)拍号 -> {e_34.pattern.name}")
    assert e_44.pattern.name == "pop D-DU-U-DU", (
        "4/4 缺省下 4 拍和弦应选 pop D-DU-U-DU（整动机奖励），实际 "
        f"{e_44.pattern.name}"
    )
    assert e_34.pattern.name != "pop D-DU-U-DU", (
        "3/4 拍号下 4 拍周期模板 pop D-DU-U-DU 应被拍号罚分压下，实际仍被选中"
    )

    # ── 2. BPM 可演奏性：高 BPM 下过密模板吃罚分 ──
    # 直接在 pattern_cost 层验证罚分值，不依赖整体选型能否翻盘（BPM 是软罚分，在 chorus
    # 高目标密度段落下，高密度模板即便加罚也仍可能胜出--这是设计预期，不该靠翻盘来验证）。
    # 53231323 (16分) 密度 1.0，180 BPM 下应吃 (1.0-0.5)*W_BPM_HIGH = 1.5 罚分；
    # 低密度模板（boom-chick 0.25）不受 BPM 影响。
    from rhythm_pattern import pattern_cost
    dense = next(p for p in STRUM_PATTERNS if p.name == "53231323 (16分)")
    sparse = next(p for p in STRUM_PATTERNS if p.name == "boom-chick")
    common = dict(beats=2, muted=(0, 0, 0), density_neighbor_delta=None)
    ctx_no_bpm = SelectionContext(section="verse", style="folk")
    ctx_fast = SelectionContext(section="verse", style="folk", bpm=180)
    cost_dense_no = pattern_cost(dense, **common, ctx=ctx_no_bpm)
    cost_dense_fast = pattern_cost(dense, **common, ctx=ctx_fast)
    cost_sparse_no = pattern_cost(sparse, **common, ctx=ctx_no_bpm)
    cost_sparse_fast = pattern_cost(sparse, **common, ctx=ctx_fast)
    print(f"  53231323(16分) 密度1.0: bpm缺省={cost_dense_no:.2f} bpm=180={cost_dense_fast:.2f} (差 {cost_dense_fast-cost_dense_no:+.2f})")
    print(f"  boom-chick     密度0.25: bpm缺省={cost_sparse_no:.2f} bpm=180={cost_sparse_fast:.2f} (差 {cost_sparse_fast-cost_sparse_no:+.2f})")
    assert cost_dense_fast > cost_dense_no, "高 BPM 下过密分解模板代价应上升"
    assert abs((cost_dense_fast - cost_dense_no) - 1.5) < 1e-9, (
        "过密模板(密度1.0)在高 BPM 下罚分应为 (1.0-0.5)*W_BPM_HIGH=1.5，"
        f"实际差 {cost_dense_fast - cost_dense_no}"
    )
    assert cost_sparse_fast == cost_sparse_no, (
        "低密度模板(密度0.25<=0.5)不应受 BPM 罚分影响，实际差 "
        f"{cost_sparse_fast - cost_sparse_no}"
    )

    # ── 3. 缺省降级：SelectionContext() 全空 与 旧式默认调用 等价 ──
    prog_decay = [("C", 4), ("G", 2), ("Am", 2)]
    e_old = enumerate_rhythm_patterns(prog_decay, gtr)  # 旧式默认：section=chorus, style=pop
    e_ctx = enumerate_rhythm_patterns(
        prog_decay, gtr, ctx=SelectionContext()  # 全空 -> 降级到 chorus/pop，拍号/BPM 不介入
    )
    old_names = [e.pattern.name for e in e_old]
    ctx_names = [e.pattern.name for e in e_ctx]
    print(f"  旧式默认调用      -> {old_names}")
    print(f"  SelectionContext()-> {ctx_names}")
    assert old_names == ctx_names, (
        f"SelectionContext() 全空应与旧式默认调用等价，实际\n  旧式={old_names}\n  ctx ={ctx_names}"
    )

    print("  断言通过: 拍号/BPM 介入生效；缺省降级与旧式调用等价")


def check_arrange_progression(gtr) -> None:
    """整段编排入口 arrange_progression：位置维度 + DP 连贯性 + 指法序列。

    验证三件事：

    1. **位置维度自动生效**：尾和弦（tail 位置）倾向选收束型模板（如琶音收尾），
       中段和弦不选它。位置由 arrange_progression 按段内下标自动判定，无需调用方传入。
    2. **DP 连贯性优于贪心**：构造贪心会「扫-拆-扫」跳变的进行，DP 应选出技法连贯的路径。
    3. **指法序列输出**：event.fingering 把 grid 正确转成 FingeringAction 序列。
    """
    print("\n=== 整段编排 arrange_progression ===")
    from rhythm_pattern import (
        SelectionContext,
        arrange_progression,
        enumerate_rhythm_patterns,
        fingering_sequence,
        FingeringAction,
    )

    # ── 1. 位置维度：尾和弦倾向收束型 ──
    # verse folk 4-2-2 进行，末和弦（Am，tail 位置）应倾向琶音收尾或分解收束，
    # 而非中段扫弦。arpeggio cadence (tail) 标了 positions=("tail",)，tail 位置 0 罚分。
    prog = [("C", 4), ("G", 2), ("Am", 2)]
    ctx = SelectionContext(section="verse", style="folk")
    events = arrange_progression(prog, gtr, ctx=ctx, k=3)
    tail_name = events[-1].pattern.name
    middle_name = events[1].pattern.name
    print(f"  4-2-2 verse folk: head={events[0].pattern.name} middle={middle_name} tail={tail_name}")
    # 尾和弦应选分解/收束类（arpeggio），不该选扫弦--尾收束偏分解。
    assert events[-1].pattern.is_arpeggio, (
        f"尾和弦(tail)应倾向分解/收束型，实际选了扫弦 {tail_name}"
    )
    # 中段和弦不应选标了 positions=("tail",) 的琶音收尾模板（非 tail 位置吃 W_POSITION）。
    tail_only = {p.name for p in __import__("rhythm_pattern").STRUM_PATTERNS
                 if p.positions == ("tail",)}
    assert middle_name not in tail_only, (
        f"中段和弦(middle)不应选 tail 专属模板 {tail_only}，实际选了 {middle_name}"
    )

    # ── 2. DP 连贯性：比贪心更连贯 ──
    # 构造一个贪心容易「扫-拆-扫」跳变的进行：chorus pop，每和弦 1 拍。
    # 贪心逐和弦取第 1 名可能各不相同；DP 应选技法连贯的路径（同技法延续）。
    prog_jump = [("C", 1), ("G", 1), ("Am", 1), ("F", 1)]
    ctx_pop = SelectionContext(section="chorus", style="pop")
    greedy = enumerate_rhythm_patterns(prog_jump, gtr, ctx=ctx_pop)
    arranged = arrange_progression(prog_jump, gtr, ctx=ctx_pop, k=3)

    def _tech_changes(evs):
        return sum(1 for a, b in zip(evs, evs[1:]) if a.pattern.technique != b.pattern.technique)

    def _template_changes(evs):
        return sum(1 for a, b in zip(evs, evs[1:]) if a.pattern.name != b.pattern.name)

    g_tech = _tech_changes(greedy)
    a_tech = _tech_changes(arranged)
    g_tpl = _template_changes(greedy)
    a_tpl = _template_changes(arranged)
    print(f"  贪心: 技法跳变={g_tech} 模板跳变={g_tpl} -> {[e.pattern.name for e in greedy]}")
    print(f"  DP:   技法跳变={a_tech} 模板跳变={a_tpl} -> {[e.pattern.name for e in arranged]}")
    # DP 的技法跳变数应 <= 贪心（DP 全局优化连贯性）。
    assert a_tech <= g_tech, (
        f"DP 技法跳变数({a_tech})应 <= 贪心({g_tech})，DP 更连贯"
    )

    # ── 3. 指法序列输出（带 duration 时值）──
    e = events[0]
    fs = e.fingering
    print(f"  C 4拍 {e.pattern.name} 指法序列: {[(a.kind, a.duration) for a in fs]}")
    # duration 之和 = 4 * beats（16 分栅格总数），时间轴完整对齐。
    total_dur = sum(a.duration for a in fs)
    assert total_dur == 4 * e.beats, (
        f"指法序列 duration 之和应=4*beats={4 * e.beats}，实际 {total_dur}"
    )
    # 每个动作 kind 合法、strings 类型对（pluck 可有弦号，stroke/rest 为 None）、duration>=1。
    for a in fs:
        assert a.kind in ("stroke_down", "stroke_up", "pluck", "rest"), f"非法 kind {a.kind}"
        assert a.duration >= 1, f"{a.kind} duration 应>=1，实际 {a.duration}"
        if a.kind in ("stroke_down", "stroke_up", "rest"):
            assert a.strings is None, f"{a.kind} 的 strings 应为 None，实际 {a.strings}"
    # fingering_sequence 函数与 event.fingering 属性一致。
    assert fs == fingering_sequence(e.grid), "event.fingering 应与 fingering_sequence(grid) 一致"

    print("  断言通过: 位置自动生效；DP 连贯性不劣于贪心；指法序列正确")


def main() -> None:
    gtr = Fretboard.guitar()

    check_benchmark(gtr)
    check_min_beats(gtr)
    check_grid_length(gtr)
    check_progression_continuity(gtr)
    check_technique_baseline(gtr)
    check_string_roles(gtr)
    check_selection_context(gtr)
    check_arrange_progression(gtr)

    # 展示几段典型进行选出的节奏栅格（不参与断言）。
    _show([("C", 4), ("G", 4), ("Am", 4), ("F", 4)], "chorus", "pop", gtr)
    _show([("C", 1), ("G", 1), ("Am", 1), ("F", 1)], "chorus", "rock", gtr)
    _show([("C", 4), ("G", 2), ("Am", 2)], "verse", "folk", gtr)

    print("\n全部断言通过。")


if __name__ == "__main__":
    main()
