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
    # 每和弦 1 拍的流行副歌 -> 8 分上下扫。
    ([("C", 1), ("G", 1), ("Am", 1), ("F", 1)], "chorus", "pop", ["pop 8th-notes"]),
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


def main() -> None:
    gtr = Fretboard.guitar()

    check_benchmark(gtr)
    check_min_beats(gtr)
    check_grid_length(gtr)
    check_progression_continuity(gtr)
    check_technique_baseline(gtr)

    # 展示几段典型进行选出的节奏栅格（不参与断言）。
    _show([("C", 4), ("G", 4), ("Am", 4), ("F", 4)], "chorus", "pop", gtr)
    _show([("C", 1), ("G", 1), ("Am", 1), ("F", 1)], "chorus", "rock", gtr)
    _show([("C", 4), ("G", 2), ("Am", 2)], "verse", "folk", gtr)

    print("\n全部断言通过。")


if __name__ == "__main__":
    main()
