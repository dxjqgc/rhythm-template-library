"""扫弦节奏型选型演示 + 内联验证入口。

``uv run rhythm_main.py`` 是节奏型子包的验证方式：下面每段演示都带 ``assert``，
断言失败即视为回归。改 ``pattern_cost`` 的 ``W_*`` 权重后必须跑一遍；改模板这一侧
则直接改 templates.json（模板的唯一真源，本文件从 DB 读）。
"""

from rhythm_pattern import arrange_progression, enumerate_rhythm_patterns, get_pattern_source
from rhythm_pattern.model import Rest
from rhythm_pattern.strum_patterns import SelectionContext
from pytheory import Fretboard

ALL_PATTERNS = get_pattern_source().patterns()
"""模板库当前内容（读 DB，与线上同一份数据）。"""


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
    # 主歌民谣 4-2-2：4 拍 C 用 53231323 分解（民谣经典动作），2 拍 G/Am 也压向分解
    # （arpeggio roll / placeholder / cadence short 等短分解动机）。段落技法先验
    # （W_SECTION_STRUM）把主歌扫弦压成次选——弹唱惯例主歌铺垫用分解；2 拍短和弦
    # 放不下 4 拍分解动机，但 1 拍短分解放得下，不再退回扫弦。技法基线 None + rock
    # 标签（musicnn 强证据）下扫弦仍可胜出（软罚不排除）。
    #
    # 注：期望集里的 arpeggio roll (tail) 是 DB 现状选出来的。arpeggio placeholder
    # 在 web 管理器里被改过（1 拍的 role=None 占位 → Root+TopN(2) 两音动机），密度
    # 0.25 → 0.50，在这个 2 拍槽位的代价从 0.400 升到 0.600（arpeggio roll 仍是
    # 0.400），于是让位。本期望集跟 DB 走，不再硬编码一份独立的事实。
    ([("C", 4), ("G", 2), ("Am", 2)], "verse", "folk",
     ["53231323 (8分)", "arpeggio placeholder", "arpeggio cadence short (tail)",
      "arpeggio roll (tail)"]),
]

TOP_N = 3
"""期望模板必须排进每个和弦前几名。"""


def _fmt(cells) -> str:
    """栅格可视化：把每个动作按其 duration 展开成 16 分字符（D/U/.），每拍 4 格用空格隔开。"""
    s = "".join((str(c) if not isinstance(c, Rest) else ".") * c.duration for c in cells)
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
    """栅格总时值 = 4 × 拍数，每拍 4 个 16 分位置。"""
    print("\n=== 栅格对齐 ===")
    for beats in (1, 2, 3, 4):
        e = enumerate_rhythm_patterns([("C", beats)], gtr, section="chorus", style="pop")[0]
        total = sum(c.duration for c in e.grid.cells)
        assert total == 4 * beats, (
            f"占 {beats} 拍应得总时值 {4 * beats}，实际 {total}"
        )
        assert e.grid.n_beats == beats, f"n_beats 应为 {beats}，实际 {e.grid.n_beats}"
        print(f"  {beats}拍 -> 总时值 {total} OK  [{_fmt(e.grid.cells)}]")
    print("  断言通过: 任意拍数栅格总时值 = 4 × 拍数")


def check_progression_continuity(gtr) -> None:
    """进行级连贯性：整段进行选出的模板风格/密度不出现逐和弦乱跳。"""
    print("\n=== 进行级连贯性 ===")
    # 一段 4-2-2 的主歌民谣，应整段落在 verse 适用的模板里。
    prog = [("C", 4), ("G", 2), ("Am", 2)]
    events = enumerate_rhythm_patterns(prog, gtr, section="verse", style="folk")
    names = [e.pattern.name for e in events]
    verse_patterns = {p.name for p in ALL_PATTERNS if "verse" in p.sections}
    assert all(n in verse_patterns for n in names), (
        f"主歌进行应只选 verse 适用模板，实际 {names}"
    )
    print(f"  {prog} -> {names}  (全部落在 verse 适用模板内)")
    print("  断言通过: 主歌进行不混入副歌专用模板")


def check_technique_baseline(gtr) -> None:
    """技法基线（段落级混排）：musicnn 推出的段落技法倾向驱动扫/拆切换。

    同一段主歌民谣进行：

    - ``technique_baseline="fingerpicking"`` -> 把和弦切到分解模板（middle 位不选
      真琶音——真琶音吃 ``W_TECHNIQUE_SOFT`` 轻罚，tail 位仍可凭位置奖励收束）；
    - ``technique_baseline="arpeggio"`` -> 把所有和弦切到分解/琶音模板（宽匹配，
      分解与真琶音都不罚，仅扫弦罚）；
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

    # fingerpicking 基线：middle 位和弦不选真琶音（technique="arpeggio"）模板——
    # 真琶音是收束手势（都标 positions=("tail",)），轻罚 W_TECHNIQUE_SOFT=2.0 在
    # middle 位无 tail 奖励对冲时足以让位给分解模板。
    fp_events = enumerate_rhythm_patterns(
        prog, gtr, section="verse", style="folk", technique_baseline="fingerpicking"
    )
    fp_middle = fp_events[1].pattern
    assert fp_middle.technique == "fingerpicking", (
        f"fingerpicking 基线 middle 位应选分解，实际 {fp_middle.name}"
        f" (technique={fp_middle.technique})"
    )

    # arpeggio 基线（宽匹配）：分解 + 真琶音模板都合法。
    arp_names = _names("arpeggio")
    arp_patterns = {p.name for p in ALL_PATTERNS if not p.is_strum}
    assert all(n in arp_patterns for n in arp_names), (
        f"arpeggio 基线应整段选分解/琶音模板，实际 {arp_names}"
    )

    # strum 基线：整段压成扫弦。
    strum_names = _names("strum")
    strum_patterns = {p.name for p in ALL_PATTERNS if p.is_strum}
    assert all(n in strum_patterns for n in strum_names), (
        f"strum 基线应整段选扫弦模板，实际 {strum_names}"
    )

    # mixed / None 基线：不干预，允许扫拆混排（不强求全扫或全拆）。
    for base in ("mixed", None):
        names = _names(base)
        print(f"  baseline={base!s:7s} -> {names}  (不干预，自由选型)")

    print(f"  baseline=fingerpicking -> {[e.pattern.name for e in fp_events]}")
    print(f"  baseline=arpeggio -> {arp_names}")
    print(f"  baseline=strum    -> {strum_names}")
    print("  断言通过: fingerpicking 分解优先(真琶音轻罚)；arpeggio 宽匹配切分解；strum 压扫弦；mixed/None 不干预自由选型")


def check_string_roles(gtr) -> None:
    """弦角色实例化：分解模板的 Pluck.role 按和弦 voicing 解析成具体弦号。

    核心回归「5,3,21」：C 和弦根音在 5 弦、顶两弦选 2-1（顶音距合适、丰富）；
    G 和弦根音在 6 弦更低、顶两弦改选 3-2 收窄顶底音距、避免尖锐。同一套弦角色
    (Root→Fifth(avoid_bass)→TopN(2,comfortable)) 在不同和弦上解析出不同弦号，
    证明弦序随和弦走、调弦中立。同时验证 53231323 的**弦形**角色（FromTop）映射：
    低音弦随和弦走（C=5 弦、E=6 弦、D=4 弦），高音三弦位 3-2-3-1-3-2-3 恒定。
    另验证斜杠转位（`"G/B"` 等）的默认 voicing 低音就是斜杠低音，Root() 随之
    落在低音弦上（旧行为：与 `"G"` 同形，低音还是 G）。
    """

    print("\n=== 弦角色实例化 ===")
    from rhythm_pattern import Pluck
    from rhythm_pattern.strum_patterns import _instantiate_plucks, _resolve_voicing

    def gtr_strings(ns):
        """弦号下标 -> 吉他手习惯弦号（0=6弦 → 6，5=1弦 → 1）。"""
        return tuple(6 - n for n in ns) if ns else None

    # 直接实例化 root-5-top2 模板（选型器未必选它，但实例化逻辑独立可测）。
    tpl = next(p for p in ALL_PATTERNS if "root-5-top2" in p.name)
    # C: 5,3,21；G: 6,4,32（根音更低→顶两弦收窄）。末位是模板自带的收尾五音
    # （DB 里这格原是 Rest，后在 web 管理器里被改成 Pluck(Fifth())）。
    cases = {"C": ((5,), (3,), (2, 1), (3,)), "G": ((6,), (4,), (3, 2), (4,))}
    for chord, want in cases.items():
        v = _resolve_voicing(chord, gtr, max_stretch=4)
        grid = _instantiate_plucks(tpl.grid_for(1), v)
        plucks = [c for c in grid.cells if isinstance(c, Pluck) and c.strings]
        got = tuple(gtr_strings(c.strings) for c in plucks)
        assert got == want, f"{chord} 弦角色实例化: 期望 {want}，实际 {got}"
        print(f"  {chord}: {got}  OK")

    # 53231323 的弦形：低音弦 + 高音三弦 3-2-3-1-3-2-3。同一套 FromTop 角色在不同
    # voicing 上解析出不同弦号，但**弦形不变**——C 得 5 起、E 得 6 起。
    tpl5323 = next(p for p in ALL_PATTERNS if p.name == "53231323 (16分)")
    cases5323 = {
        # 和弦: (低音弦号, 完整弦序)
        "C": (5, [5, 3, 2, 3, 1, 3, 2, 3]),   # 经典 53231323
        "E": (6, [6, 3, 2, 3, 1, 3, 2, 3]),   # 经典 63231323（E 根音在 6 弦）
        "D": (4, [4, 3, 2, 3, 1, 3, 2, 3]),   # 经典 43231323
        "Am": (5, [5, 3, 2, 3, 1, 3, 2, 3]),
        "G": (6, [6, 3, 2, 3, 1, 3, 2, 3]),
    }
    for chord, (_, want) in cases5323.items():
        v = _resolve_voicing(chord, gtr, max_stretch=4)
        grid = _instantiate_plucks(tpl5323.grid_for(2), v)
        seq = [gtr_strings(c.strings)[0] for c in grid.cells
               if isinstance(c, Pluck) and c.strings]
        assert seq == want, f"{chord} 上 53231323 弦序应为 {want}，实际 {seq}"
        print(f"  {chord}: 53231323 -> {seq}  OK")
    print("  断言通过: 弦形分解按 voicing 换低音弦，高音三弦位 3-2-3-1-3-2-3 不变")

    # 斜杠转位：默认 voicing 的低音必须就是斜杠低音。回归「谱面标 G/B、指法和
    # 实际发声都是原位 G」——pytheory 不把斜杠低音当约束（`G/B` 与 `G` 的音级集合
    # 相同、`root` 仍是 G），约束由 chord_fingering.enumerate_fingerings 里的
    # slash_bass_pc 施加；弦角色以最低发音音为基准解析，故 Root() 落在低音弦上
    # （拇指拨的正是斜杠低音）。
    for symbol, bass_pc in (("G/B", 11), ("C/E", 4), ("D/F#", 6), ("Am/C", 0)):
        v = _resolve_voicing(symbol, gtr, max_stretch=4)
        assert v is not None, f"{symbol} 应有默认 voicing"
        low_pc = min(m for _, m in v.midi) % 12
        assert low_pc == bass_pc, (
            f"{symbol} 默认 voicing 低音 pc={low_pc}，期望 {bass_pc}（positions={v.positions}）"
        )
        grid = _instantiate_plucks(tpl.grid_for(1), v)
        root_strings = gtr_strings(next(c.strings for c in grid.cells
                                        if isinstance(c, Pluck) and c.strings))
        lowest_string = 6 - min(i for i, p in enumerate(v.positions) if p is not None)
        assert root_strings[0] == lowest_string, (
            f"{symbol} Root() 应落在最低发音弦（斜杠低音），实际 {root_strings}"
        )
        print(f"  {symbol}: voicing={v.positions} 低音弦={lowest_string}  OK")
    print("  断言通过: 斜杠转位低音进默认 voicing")

    # 名字带数字弦形的模板，动机渲染出的弦序必须等于名字本身（C 上即字面数字）。
    # 回归：`5323 (8分)` 曾只写 2 个音，平铺出来是 5-3-5-3——两根弦来回拨。
    for name, beats, want in (
        ("53231323 (16分)", 2, "53231323"),
        ("53231323 (8分)", 4, "53231323"),
        ("5323 (8分)", 2, "5323"),
        ("3/4 532132 (8分)", 3, "532132"),
        ("6/8 532132 (8分)", 2, "532132"),
    ):
        tpl = next(p for p in ALL_PATTERNS if p.name == name)
        v = _resolve_voicing("C", gtr, max_stretch=4)
        grid = _instantiate_plucks(tpl.grid_for(beats), v)
        seq = "".join(
            str(gtr_strings(c.strings)[0]) for c in grid.cells
            if isinstance(c, Pluck) and c.strings
        )
        assert seq == want, f"{name} 动机渲染 {seq}，名字承诺 {want}"
        print(f"  {name}: 动机 -> {seq}  OK")
    print("  断言通过: 名字带数字弦形的模板逐音写满，动机渲染 = 名字")


def check_selection_context(gtr) -> None:
    """SelectionContext 抽象层：拍号、BPM、起音密度维度介入 + 缺省降级。

    验证四件事：

    1. **拍号契合**：``ctx.time_signature=(6,8)`` 下，4/4 模板吃 ``W_TIME_SIG_MISMATCH``
       重罚（量级 50），让位 6/8 专属模板（``time_signature==(6,8)`` 不罚）。6/8 是拍号
       维度真正起作用的场景（有专属模板可替代）；3/4 等无专属模板的拍号下重罚无差别，
       4/4 模板仍可能被选。
    2. **BPM 可演奏性**：按**绝对发音速率**罚（``density × 4 × bpm / 60`` 超出
       ``MAX_ONSETS_PER_SEC`` 的部分乘 ``W_RATE_HIGH``），不是按相对密度 —— 相对密度
       会漏掉最常见的一档：八分音符分解 density 恰为 0.5，旧阈值 ``density > 0.5``
       让它**在任何 BPM 下都零罚分**。3/4 分解 ``532132 (8分)`` 在 176 BPM 是 5.9
       发音/秒（弹不动）应吃罚，在 88 BPM 只有 2.9（轻松）不吃罚。
    3. **缺省降级**：``SelectionContext()`` 全空字段时，输出与旧式调用
       ``enumerate_rhythm_patterns(progression, gtr)`` 完全一致（拍号/BPM 不介入）。
    4. **实测起音密度融合**：``ctx.onset_density`` 给定时与段落静态目标密度加权融合
       （实测疏 -> 选更疏模板，实测密 -> 选更密模板）；``None`` 时完全不介入。
    """
    print("\n=== SelectionContext 抽象层 ===")
    from rhythm_pattern import SelectionContext

    # ── 1. 拍号契合：6/8 拍号下 4/4 模板被重罚让位 6/8 专属模板 ──
    # 占 2 拍（6/8 一小节）的和弦：4/4（缺省）下无 6/8 概念，选 4/4 模板（如 pop 8th-notes
    # 或 D-D-DU）。显式给 (6,8) 拍号后，4/4 模板吃 W_TIME_SIG_MISMATCH 重罚（量级 50，远大于
    # 其他维度总和），让位 6/8 专属模板（time_signature==(6,8) 不罚）。这样 6/8 歌曲选
    # 出地道附点律动模板，拒 4/4 均匀律动。3/4 等无专属模板的拍号下重罚无差别（无替代），
    # 4/4 模板仍可能被选——这是诚实表现，6/8 才是拍号维度真正起作用的场景。
    prog = [("C", 2)]
    e_44 = enumerate_rhythm_patterns(prog, gtr, section="chorus", style="pop")[0]
    e_68 = enumerate_rhythm_patterns(
        prog, gtr, ctx=SelectionContext(section="chorus", style="pop", time_signature=(6, 8))
    )[0]
    print(f"  2拍和弦 4/4(缺省) -> {e_44.pattern.name} (ts={e_44.pattern.time_signature})")
    print(f"  2拍和弦 (6,8)拍号 -> {e_68.pattern.name} (ts={e_68.pattern.time_signature})")
    assert e_44.pattern.time_signature == (4, 4), (
        "4/4 缺省下应选 4/4 模板，实际 " + str(e_44.pattern.time_signature)
    )
    assert e_68.pattern.time_signature == (6, 8), (
        "6/8 拍号下应选 6/8 专属模板（4/4 模板被重罚），实际选了 "
        f"{e_68.pattern.name} (ts={e_68.pattern.time_signature})"
    )

    # ── 2. BPM 可演奏性：按绝对发音速率罚 ──
    # 直接在 pattern_cost 层验证罚分值，不依赖整体选型能否翻盘（BPM 是软罚分，在 chorus
    # 高目标密度段落下，高密度模板即便加罚也仍可能胜出--这是设计预期，不该靠翻盘来验证）。
    # 罚分 = (density*4*bpm/60 - MAX_ONSETS_PER_SEC) * W_RATE_HIGH，超出部分为负时取 0。
    from rhythm_pattern import pattern_cost
    from rhythm_pattern.strum_patterns import (
        MAX_ONSETS_PER_SEC,
        MAX_ONSETS_PER_SEC_STRUM,
        ONSETS_PER_BEAT_AT_FULL_DENSITY,
        W_RATE_HIGH,
    )

    def _close(a, b):
        return abs(a - b) < 1e-9

    def _expected_rate_penalty(density, bpm, is_strum):
        ceiling = MAX_ONSETS_PER_SEC_STRUM if is_strum else MAX_ONSETS_PER_SEC
        rate = density * ONSETS_PER_BEAT_AT_FULL_DENSITY * bpm / 60.0
        return max(0.0, (rate - ceiling) * W_RATE_HIGH)

    def _delta(pattern, bpm, beats):
        common = dict(beats=beats, muted=(0, 0, 0), density_neighbor_delta=None)
        a = pattern_cost(pattern, **common, ctx=SelectionContext(section="verse", style="folk"))
        b = pattern_cost(pattern, **common, ctx=SelectionContext(section="verse", style="folk", bpm=bpm))
        return a, b, b - a

    dense = next(p for p in ALL_PATTERNS if p.name == "53231323 (16分)")
    sparse = next(p for p in ALL_PATTERNS if p.name == "boom-chick")
    eighth = next(p for p in ALL_PATTERNS if p.name == "3/4 532132 (8分)")

    c_dn, c_df, d_dense = _delta(dense, 180, 2)
    c_sn, c_sf, d_sparse = _delta(sparse, 180, 2)
    c_en, c_ef, d_eighth = _delta(eighth, 176, 3)
    c_e88n, c_e88f, d_eighth_88 = _delta(eighth, 88, 3)

    print(f"  53231323(16分) 密度1.0 @180: bpm缺省={c_dn:.2f} bpm=180={c_df:.2f} (差 {d_dense:+.2f})")
    print(f"  boom-chick     密度0.25@180: bpm缺省={c_sn:.2f} bpm=180={c_sf:.2f} (差 {d_sparse:+.2f})")
    print(f"  3/4 532132(8分)密度0.50@176: bpm缺省={c_en:.2f} bpm=176={c_ef:.2f} (差 {d_eighth:+.2f})")
    print(f"  3/4 532132(8分)密度0.50@ 88: bpm缺省={c_e88n:.2f} bpm=88={c_e88f:.2f} (差 {d_eighth_88:+.2f})")

    assert _close(d_dense, _expected_rate_penalty(1.0, 180, dense.is_strum)), (
        f"密度1.0 @180 罚分应为 {_expected_rate_penalty(1.0, 180, dense.is_strum)}，实际 {d_dense}"
    )
    assert d_dense > 0, "高 BPM 下过密分解模板代价应上升"
    assert d_sparse == 0, (
        "低密度模板(0.25 -> 3.0 发音/秒)不应受速率罚分影响，实际差 " f"{d_sparse}"
    )
    # ★ 回归：八分音符分解（density 恰为 0.5）在旧公式 (density-0.5)*W_BPM_HIGH 下
    #   罚分恒为 0 —— 任何 BPM 都零罚，这正是「176 BPM 还选八分分解」的根因。
    assert _close(d_eighth, _expected_rate_penalty(0.5, 176, eighth.is_strum)), (
        f"八分分解 @176 罚分应为 {_expected_rate_penalty(0.5, 176, eighth.is_strum)}，实际 {d_eighth}"
    )
    assert d_eighth > 0, "八分分解在 176 BPM 下必须吃罚（旧公式此处恒为 0）"
    assert d_eighth_88 == 0, "同样的八分分解在 88 BPM 下只有 2.9 发音/秒，不该吃罚"
    # ★ 扫弦与分解的上限必须分开：十六分**扫弦**在 120 BPM 是 8 发音/秒，是流行扫弦的
    #   常规动作；用分解的 5.0 上限去罚它，会把 onset_density 驱动的密度响应整个压平
    #   （后端 test_arrange_onset_density_pulls_target 就是这么红的）。
    strum16 = next(p for p in ALL_PATTERNS if p.name == "D-D-DU (1拍16分)")
    _, _, d_strum16 = _delta(strum16, 120, 2)
    print(f"  D-D-DU(1拍16分) 扫弦  @120: (差 {d_strum16:+.2f})")
    assert strum16.is_strum, "该模板应为扫弦（回归前提）"
    assert d_strum16 == 0, (
        f"十六分扫弦在 120 BPM（8 发音/秒）属常规动作，不该吃速率罚分，实际差 {d_strum16}"
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

    # ── 4. 实测起音密度融合：onset_density 拉动目标密度 ──
    # verse/folk 下实测极疏(0.1)应选出比默认更疏的模板，实测极密(1.0)应更密。
    # 用 3 和弦 middle 位避开单和弦 tail 奖励干扰（同 pytest TestOnsetDensity）。
    from rhythm_pattern.strum_patterns import _target_density, W_ONSET_AUDIO

    mid = lambda ev: ev[1].pattern  # noqa: E731  第 2 个和弦 = middle 位
    e_default = enumerate_rhythm_patterns(
        prog_decay, gtr, ctx=SelectionContext(section="verse", style="folk"),
    )
    e_sparse = enumerate_rhythm_patterns(
        prog_decay, gtr,
        ctx=SelectionContext(section="verse", style="folk", onset_density=0.1),
    )
    e_dense = enumerate_rhythm_patterns(
        prog_decay, gtr,
        ctx=SelectionContext(section="verse", style="folk", onset_density=1.0),
    )
    print(f"  verse默认(无实测)  -> {[e.pattern.name for e in e_default]}")
    print(f"  verse实测疏(0.1)  -> {[e.pattern.name for e in e_sparse]}")
    print(f"  verse实测密(1.0)  -> {[e.pattern.name for e in e_dense]}")
    # 段落技法先验（W_SECTION_STRUM）落下后，verse 2 拍短和弦默认已翻选短分解
    # （arpeggio placeholder，密度 0.25 = 库内最疏档），onset 疏拉不动已贴地的疏度
    # 下限——稳健不变量改为 dense > sparse（极密实测拉开密度差必然可观测）。
    assert mid(e_sparse).density() <= mid(e_default).density(), (
        f"实测疏(0.1)不应选出更密模板: {mid(e_sparse).name}({mid(e_sparse).density()}) "
        f"vs 默认 {mid(e_default).name}({mid(e_default).density()})"
    )
    assert mid(e_dense).density() > mid(e_default).density(), (
        f"实测密(1.0)应选出更密模板: {mid(e_dense).name}({mid(e_dense).density()}) "
        f"vs 默认 {mid(e_default).name}({mid(e_default).density()})"
    )
    # 融合公式本身：fused = W*measured + (1-W)*static（verse 4 拍满小节 static=0.30）。
    static = _target_density("verse", 4, (4, 4))
    fused = _target_density("verse", 4, (4, 4), 0.5)
    assert abs(fused - (W_ONSET_AUDIO * 0.5 + (1 - W_ONSET_AUDIO) * static)) < 1e-9, (
        f"融合公式不符: fused={fused}, 期望 {W_ONSET_AUDIO}*0.5+{1-W_ONSET_AUDIO}*{static}"
    )
    # 缺省不介入：onset_density=None 与不传字段完全等价。
    e_none = enumerate_rhythm_patterns(
        prog_decay, gtr,
        ctx=SelectionContext(section="verse", style="folk", onset_density=None),
    )
    assert [e.pattern.name for e in e_none] == [e.pattern.name for e in e_default], (
        "onset_density=None 应与缺省完全等价"
    )

    print("  断言通过: 拍号/BPM/起音密度介入生效；缺省降级与旧式调用等价")


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
    # 尾和弦应选分解/收束类（非扫弦），不该选扫弦--尾收束偏分解。
    assert not events[-1].pattern.is_strum, (
        f"尾和弦(tail)应倾向分解/收束型，实际选了扫弦 {tail_name}"
    )
    # 中段和弦不应选标了 positions=("tail",) 的琶音收尾模板（非 tail 位置吃 W_POSITION）。
    tail_only = {p.name for p in ALL_PATTERNS
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


def check_68(gtr) -> None:
    """6/8 拍号：模板拍号契合筛 6/8 专属，栅格按附点拍对齐，重音体现强弱分组。

    验证四件事：

    1. **拍号契合**：``ctx.time_signature=(6,8)`` 下选出的模板 ``time_signature==(6,8)``，
       4/4 模板吃 ``W_TIME_SIG_MISMATCH`` 重罚不入选。
    2. **栅格对齐**：6/8 一拍 = 3 tick，栅格总时值 = ``3 * beats``，``n_beats == beats``。
    3. **重音存在**：6/8 专属模板至少含一个 ``strong`` accent（强拍标记）。
    4. **整段编排**：6/8 歌曲的编排也只在 6/8 模板里选，不混入 4/4。
    """
    print("\n=== 6/8 拍号 ===")
    # 1+2. 拍号契合 + 栅格对齐：6/8 副歌 2 拍（1 小节）选 6/8 模板，总时值 = 3*beats。
    for prog, section, style in [
        ([("C", 2)], "chorus", "pop"),
        ([("C", 2), ("G", 2)], "verse", "folk"),
    ]:
        ctx = SelectionContext(section=section, style=style, time_signature=(6, 8))
        events = enumerate_rhythm_patterns(prog, gtr, ctx=ctx)
        for e in events:
            assert e.pattern.time_signature == (6, 8), (
                f"6/8 歌曲应选 6/8 模板，实际选了 {e.pattern.name} (ts={e.pattern.time_signature})"
            )
            total = sum(c.duration for c in e.grid.cells)
            assert total == 3 * e.beats, (
                f"6/8 栅格总时值应=3*beats={3 * e.beats}，实际 {total}（{e.pattern.name}）"
            )
            assert e.grid.ticks_per_beat == 3, f"6/8 栅格 ticks_per_beat 应=3，实际 {e.grid.ticks_per_beat}"
            assert e.grid.n_beats == e.beats, f"n_beats 应={e.beats}，实际 {e.grid.n_beats}"
        print(f"  {section}/{style} {prog} -> {[e.pattern.name for e in events]} (全 6/8, 总时值 3*beats)")

    # 3. 重音存在：6/8 模板至少含一个 strong accent。
    p68 = [p for p in ALL_PATTERNS if p.time_signature == (6, 8)]
    assert p68, "库里应有 6/8 专属模板"
    for p in p68:
        has_strong = any(getattr(c, "accent", None) == "strong" for c in p.grid_motif)
        assert has_strong, f"6/8 模板 {p.name} 应至少含一个 strong accent"
    print(f"  {len(p68)} 个 6/8 模板均含 strong accent: {[p.name for p in p68]}")

    # 4. 整段编排也只在 6/8 模板里选。
    ctx = SelectionContext(section="verse", style="folk", time_signature=(6, 8))
    arranged = arrange_progression([("C", 2), ("G", 2), ("Am", 2), ("F", 2)], gtr, ctx=ctx)
    assert all(e.pattern.time_signature == (6, 8) for e in arranged), (
        "6/8 编排不应混入 4/4 模板，实际 "
        + str([e.pattern.time_signature for e in arranged])
    )
    print(f"  编排 4 和弦 -> {[e.pattern.name for e in arranged]} (全 6/8)")

    # 5. 主歌模板的 tags 必须带 soft —— 6/8 抒情分解在 musicnn 出 soft 标签的主歌里
    #    会被"风格匹配"扣分。6/8 532132 (8分) 曾漏标 soft：其它三个主歌 6/8 模板
    #    (folk D-DU / root-5-top / cadence / roll) 全都有，只有它没有，于是同一语境下
    #    比 root-5-top 贵 1.25，抒情歌主歌永远选不上它。
    verse_targets = [p for p in p68 if "verse" in p.sections]
    assert verse_targets, "6/8 库应有主歌模板"
    for p in verse_targets:
        assert "soft" in p.tags, f"6/8 主歌模板 {p.name} 的 tags 缺 soft: {p.tags}"
    print(f"  {len(verse_targets)} 个 6/8 主歌模板均带 soft: {[p.name for p in verse_targets]}")

    print("  断言通过: 6/8 拍号筛专属模板，栅格按附点拍对齐，重音标注强弱")


def check_34(gtr) -> None:
    """3/4 拍号：专属模板优先、栅格按 4 tick/拍对齐、同分母轻罚兜底、孪生模板成对。

    验证五件事：

    1. **拍号契合**：``ctx.time_signature=(3,4)`` 下选出的模板 ``time_signature==(3,4)``，
       /8 模板吃 ``W_TIME_SIG_MISMATCH`` 重罚不入选。
    2. **栅格对齐**：3/4 一拍 = 4 tick（与其他 /4 拍号一致），栅格总时值 = ``4 * beats``。
    3. **整段编排**：3/4 歌曲的编排只在 3/4 模板里选（主体 532132 + 尾和弦收束）。
    4. **同分母兜底**：4/4 短模板（boom-chick）在 3/4 下吃 ``W_TIME_SIG_NUMERATOR``
       轻罚仍可用（极端兜底场景不崩），但 3/4 专属 boom-chick 0 罚分优先。
    5. **孪生成对**：532132 指法在 3/4 与 6/8 各有一份编码，弦角色实例化到 C 均为
       5-3-2-1-3-2 弦序（0=六弦下标：1,3,4,5,3,4）。
    """
    print("\n=== 3/4 拍号 ===")
    # 1+2. 拍号契合 + 栅格对齐：3/4 各段落 3 拍（1 小节）选 3/4 模板，总时值 = 4*beats。
    for prog, section, style, baseline in [
        ([("C", 3)], "chorus", "folk", None),
        ([("C", 3), ("G", 3)], "verse", "folk", "fingerpicking"),
    ]:
        ctx = SelectionContext(
            section=section, style=style, time_signature=(3, 4),
            technique_baseline=baseline,
        )
        events = enumerate_rhythm_patterns(prog, gtr, ctx=ctx)
        for e in events:
            assert e.pattern.time_signature == (3, 4), (
                f"3/4 歌曲应选 3/4 模板，实际选了 {e.pattern.name} (ts={e.pattern.time_signature})"
            )
            total = sum(c.duration for c in e.grid.cells)
            assert total == 4 * e.beats, (
                f"3/4 栅格总时值应=4*beats={4 * e.beats}，实际 {total}（{e.pattern.name}）"
            )
            assert e.grid.ticks_per_beat == 4, (
                f"3/4 栅格 ticks_per_beat 应=4，实际 {e.grid.ticks_per_beat}"
            )
        print(f"  {section}/{style} {prog} -> {[e.pattern.name for e in events]} (全 3/4, 总时值 4*beats)")

    # 3. 整段编排：主体 3/4 模板 + 尾和弦收束，不混入 4/4。
    ctx = SelectionContext(
        section="verse", style="folk", time_signature=(3, 4),
        technique_baseline="fingerpicking",
    )
    arranged = arrange_progression([("C", 3), ("G", 3), ("Am", 3), ("F", 3)], gtr, ctx=ctx)
    assert all(e.pattern.time_signature == (3, 4) for e in arranged), (
        "3/4 编排不应混入 4/4 模板，实际 "
        + str([e.pattern.time_signature for e in arranged])
    )
    print(f"  编排 4 和弦 -> {[e.pattern.name for e in arranged]} (全 3/4, 尾和弦收束)")

    # 4. 同分母兜底：4/4 boom-chick 在 (3,4) ctx 下吃轻罚仍可用，专属 3/4 boom-chick 更优。
    from rhythm_pattern.strum_patterns import _boom_chick_fallback

    fb34 = _boom_chick_fallback((3, 4))
    assert fb34.name == "3/4 boom-chick" and fb34.time_signature == (3, 4), (
        f"3/4 兜底应取 3/4 boom-chick，实际 {fb34.name} (ts={fb34.time_signature})"
    )
    print(f"  3/4 兜底 -> {fb34.name} (同拍号优先，无跨分子轻罚)")

    # 5. 孪生成对：532132 两拍号各一份，实例化到 C 弦序一致（5-3-2-1-3-2）。
    from rhythm_pattern.strum_patterns import instantiate_pattern

    for name, beats in (("3/4 532132 (8分)", 3), ("6/8 532132 (8分)", 2)):
        p = next(p for p in ALL_PATTERNS if p.name == name)
        ev = instantiate_pattern(p, "C", gtr, beats)
        seq = [c.strings[0] for c in ev.grid.cells if getattr(c, "strings", None)]
        assert seq == [1, 3, 4, 5, 3, 4], (
            f"{name} 实例化到 C 应为 5-3-2-1-3-2（下标 {[1, 3, 4, 5, 3, 4]}），实际 {seq}"
        )
    print("  532132 孪生模板 (3/4 + 6/8) 实例化到 C 均为 5-3-2-1-3-2 弦序")

    # 3/4 专属模板重音存在（强-弱-弱拍头标记）。
    p34 = [p for p in ALL_PATTERNS if p.time_signature == (3, 4)]
    assert p34, "库里应有 3/4 专属模板"
    for p in p34:
        has_strong = any(getattr(c, "accent", None) == "strong" for c in p.grid_motif)
        assert has_strong, f"3/4 模板 {p.name} 应至少含一个 strong accent"
    print(f"  {len(p34)} 个 3/4 模板均含 strong accent: {[p.name for p in p34]}")
    print("  断言通过: 3/4 拍号筛专属模板，同分母轻罚兜底，孪生指法跨拍号成对")


def check_plan_song_rhythm(gtr) -> None:
    """全曲统一选型 ``plan_song_rhythm``：一首歌收敛到 1-2 个基本节奏型。

    验证四件事：

    1. **种类受控**：整首多段落歌曲的模板种类 <= 2 × max_families（基础 + 短，
       每族各一），且远少于逐和弦选型 ``arrange_progression`` 的种类数。
    2. **抒情歌单族**：musicnn guitar/slow/soft 标签 + 低起音密度下扫弦族被裁掉，
       全曲只剩拨弦族——「抒情歌连扫弦都不需要」。
    3. **段落分配**：主歌走拨弦、副歌走扫弦（段落决定用两个基本节奏型里的哪一个）。
    4. **短和弦兜底**：``beats < 基础模板 min_beats`` 的和弦退到同族短模板，
       而不是另选一个新模板；全曲模板总数不因此膨胀。
    """
    print("\n=== 全曲统一选型 plan_song_rhythm ===")
    from rhythm_pattern import SelectionContext, arrange_progression, plan_song_rhythm

    # 一段跨主歌/副歌/尾奏的典型流行歌，和弦拍数含 4/2/6 三种。
    SONG = [
        ("verse", "verse", [("C", 4), ("G", 2), ("Am", 2), ("F", 4), ("C", 2), ("G", 2), ("F", 4), ("F", 2), ("G", 2)]),
        ("chorus", "chorus", [("F", 4), ("G", 4), ("C", 6), ("Am", 2), ("F", 4), ("G", 2), ("C", 4), ("C", 2)]),
        ("verse", "verse", [("C", 4), ("G", 2), ("Am", 2), ("F", 4), ("C", 2), ("G", 2), ("F", 4), ("F", 2), ("G", 2)]),
        ("outro", "outro", [("C", 4), ("G", 4), ("F", 4), ("C", 4)]),
    ]
    POP_TAGS = {
        "verse": (("guitar", 0.8), ("soft", 0.6), ("slow", 0.5)),
        "chorus": (("pop", 0.7), ("drums", 0.65), ("fast", 0.6), ("loud", 0.5)),
        "outro": (("guitar", 0.75), ("slow", 0.6), ("soft", 0.5)),
    }
    POP_ONSET = {"verse": 0.42, "chorus": 0.70, "outro": 0.32}

    def _build(tags_of, onset_of, bpm=92):
        prog, ctxs = [], []
        for _label, sec, chords in SONG:
            for c, b in chords:
                prog.append((c, b))
                ctxs.append(SelectionContext(
                    section=sec, style="pop", bpm=bpm, time_signature=(4, 4),
                    max_stretch=4, musicnn_tags=tags_of[sec], onset_density=onset_of[sec],
                ))
        return prog, ctxs

    # ── 1. 种类受控 + 3. 段落分配（流行歌）──
    prog, ctxs = _build(POP_TAGS, POP_ONSET)
    base = SelectionContext(style="pop", bpm=92, time_signature=(4, 4), max_stretch=4)
    planned = plan_song_rhythm(prog, gtr, ctx=base, chord_ctxs=ctxs)
    assert len(planned) == len(prog), f"输出应与进行等长 {len(prog)}，实际 {len(planned)}"
    names = {e.pattern.name for e in planned}
    assert len(names) <= 4, (  # 2 族 × (基础 + 短)
        f"全曲模板种类应 <= 4（2 族 × 基础/短），实际 {len(names)}: {names}"
    )
    # 对照组：旧路径的等价形态——按段落分组、每组各调一次 arrange_progression
    # （后端 pipeline 的实际做法，见 segment_rhythm_pipeline_service）。同样输入下
    # 它逐和弦 / 逐段落另选模板，种类数应明显多于全曲统一选型。
    greedy_names: set[str] = set()
    for _label, sec, chords in SONG:
        sec_ctx = SelectionContext(
            section=sec, style="pop", bpm=92, time_signature=(4, 4), max_stretch=4,
            musicnn_tags=POP_TAGS[sec], onset_density=POP_ONSET[sec],
        )
        for e in arrange_progression(list(chords), gtr, ctx=sec_ctx):
            greedy_names.add(e.pattern.name)
    assert len(names) < len(greedy_names), (
        f"全曲统一选型种类({len(names)})应少于逐段落选型({len(greedy_names)}): "
        f"{sorted(names)} vs {sorted(greedy_names)}"
    )
    print(f"  流行歌 {len(prog)} 和弦: 全曲统一 {len(names)} 种 {sorted(names)}"
          f"  vs 逐段落 {len(greedy_names)} 种 {sorted(greedy_names)}")

    # 段落分配：主歌拨弦、副歌扫弦。
    fams = {
        "verse": {e.pattern.is_strum for e, (_, sec, _) in zip(planned, _iter_ctx_sec(SONG)) if sec == "verse"},
        "chorus": {e.pattern.is_strum for e, (_, sec, _) in zip(planned, _iter_ctx_sec(SONG)) if sec == "chorus"},
    }
    assert fams["verse"] == {False}, f"主歌应为拨弦（分解），实际 is_strum={fams['verse']}"
    assert fams["chorus"] == {True}, f"副歌应为扫弦，实际 is_strum={fams['chorus']}"
    per_sec: dict[str, set[str]] = {"verse": set(), "chorus": set()}
    for e, (_l, sec, _c) in zip(planned, _iter_ctx_sec(SONG)):
        if sec in per_sec:
            per_sec[sec].add(e.pattern.name)
    print(f"  段落分配: 主歌 -> 拨弦 {sorted(per_sec['verse'])}"
          f"; 副歌 -> 扫弦 {sorted(per_sec['chorus'])}")

    # ── 2. 抒情歌：只剩拨弦族 ──
    BALLAD_TAGS = {sec: (("guitar", 0.85), ("slow", 0.75), ("soft", 0.7), ("classical", 0.5)) for sec in POP_TAGS}
    BALLAD_ONSET = {sec: 0.32 for sec in POP_TAGS}
    prog_b, ctxs_b = _build(BALLAD_TAGS, BALLAD_ONSET, bpm=72)
    base_b = SelectionContext(style="pop", bpm=72, time_signature=(4, 4), max_stretch=4)
    ballad = plan_song_rhythm(prog_b, gtr, ctx=base_b, chord_ctxs=ctxs_b)
    assert all(not e.pattern.is_strum for e in ballad), (
        "抒情歌（guitar/slow/soft 标签 + 低起音密度）应全程分解，实际混入扫弦: "
        f"{sorted({e.pattern.name for e in ballad if e.pattern.is_strum})}"
    )
    ballad_names = {e.pattern.name for e in ballad}
    assert len(ballad_names) <= 2, f"抒情歌应只留 1-2 个分解模板，实际 {ballad_names}"
    print(f"  抒情歌 {len(prog_b)} 和弦: {len(ballad_names)} 种（全拨弦）{sorted(ballad_names)}")

    # ── 4. 短和弦兜底：基础模板放不下的和弦退同族短模板 ──
    # 上例含 2 拍和弦（基础 4 拍分解放不下），应退到同族 min_beats=1 的短模板。
    short_used = {e.pattern.name for e in planned if e.pattern.min_beats <= 1}
    assert short_used, "含 2 拍和弦的歌曲应触发短模板兜底"
    print(f"  短和弦兜底模板: {sorted(short_used)}")

    # ── max_families=1：强制单族 ──
    one = plan_song_rhythm(prog, gtr, ctx=base, chord_ctxs=ctxs, max_families=1)
    assert len({e.pattern.name for e in one}) <= 2, "max_families=1 时全曲应只剩一族（基础+短）"
    print(f"  max_families=1 -> {sorted({e.pattern.name for e in one})}")

    print("  断言通过: 全曲收敛到 1-2 个基本节奏型；抒情歌单族；短和弦同族兜底")


def _iter_ctx_sec(song):
    """按 SONG 结构展开每个和弦的 (label, section, chord)，供断言里对齐 section。"""
    for label, sec, chords in song:
        for c, b in chords:
            yield label, sec, (c, b)


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
    check_plan_song_rhythm(gtr)
    check_68(gtr)
    check_34(gtr)

    # 展示几段典型进行选出的节奏栅格（不参与断言）。
    _show([("C", 4), ("G", 4), ("Am", 4), ("F", 4)], "chorus", "pop", gtr)
    _show([("C", 1), ("G", 1), ("Am", 1), ("F", 1)], "chorus", "rock", gtr)
    _show([("C", 4), ("G", 2), ("Am", 2)], "verse", "folk", gtr)

    print("\n全部断言通过。")


if __name__ == "__main__":
    main()
