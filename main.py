"""指板和弦指法枚举演示 + 内联验证入口。

``uv run main.py`` 是本仓库唯一的验证方式：下面每一段演示都带 ``assert``，
断言失败即视为回归。改动 ``fingering_enumerator.py`` 或 ``playability.py``
（尤其是调权重）后必须跑一遍。
"""

from fingering_enumerator import analyze_barre, enumerate_fingerings, is_redundant_thumb
from playability import plan_fingers, playability_cost, required_pitch_classes
from pytheory import Chord, Fretboard


# 常用指法基准集：吉他教材里人人都会的那几个手型。
# 「更贴合实际」在本仓库里就定义成这张表——新排序模型必须让每个和弦的教科书指法
# 排进前 3。一个和弦列多个是因为常用指法本来就不唯一：C7 的开放型和 3 品横按型
# 都算数，Em7 的 022030 和 020000 也都是教材写法，命中任一即可。
BENCHMARK: list[tuple[str, list[str]]] = [
    ("C", ["x32010"]),
    ("G", ["320003"]),
    ("D", ["xx0232"]),
    ("A", ["x02220"]),
    ("E", ["022100"]),
    ("Am", ["x02210"]),
    ("Em", ["022000"]),
    ("Dm", ["xx0231"]),
    ("F", ["133211"]),
    ("G7", ["320001"]),
    ("C7", ["x32310", "x35353"]),
    ("D7", ["xx0212"]),
    ("E7", ["020100"]),
    ("Am7", ["x02010"]),
    ("Dm7", ["xx0211"]),
    ("Cmaj7", ["x32000"]),
    ("Em7", ["022030", "020000"]),
    ("A7", ["x02020"]),
]

TOP_N = 3
"""基准指法必须排进前几名。"""


def _pitch_class(tone) -> int:
    return tone.midi % 12


def _parse(tab: str) -> tuple[int | None, ...]:
    """``"x32010"`` -> ``(None, 3, 2, 0, 1, 0)``。"""
    return tuple(None if c == "x" else int(c) for c in tab)


def _fmt(positions) -> str:
    return "".join("x" if p is None else str(p) for p in positions)


def _show(title: str, fingerings, root_pc: int, chord_pcs: set[int], open_tones) -> None:
    print(f"\n=== {title} ===")
    print(f"共 {len(fingerings)} 个指法（按可演奏性代价升序）")
    for f in fingerings[:6]:
        tones = f.tones
        lowest = min(tones, key=lambda t: t.midi)
        inv = "原位" if _pitch_class(lowest) == root_pc else "转位"
        plan = plan_fingers(f.positions)
        omitted = chord_pcs - {_pitch_class(t) for t in tones}
        cost = playability_cost(
            f.positions,
            tones,
            root_pc=root_pc,
            n_omitted=len(omitted),
            plan=plan,
            open_tones=open_tones,
        )
        barre = f"横按{plan.barre_fret}品x{plan.barre_width}弦" if plan.barre_fret else "无横按"
        omit = f" 省略{len(omitted)}音" if omitted else ""
        print(
            f"  {_fmt(f.positions)}  代价{cost:6.2f}  [{inv}] "
            f"{len(tones)}弦 {plan.n_fingers}指 {barre}{omit}"
        )


def check_benchmark(gtr) -> None:
    """核心回归：常用指法必须排进前 TOP_N。"""
    print("=== 常用指法基准集 ===")
    failures: list[str] = []
    for symbol, tabs in BENCHMARK:
        wanted = {_parse(t) for t in tabs}
        ranked = enumerate_fingerings(symbol, gtr, max_fret=5, max_stretch=4)
        hit = next(
            (i for i, f in enumerate(ranked) if tuple(f.positions) in wanted), None
        )
        rank = hit + 1 if hit is not None else None
        ok = rank is not None and rank <= TOP_N
        if not ok:
            failures.append(f"{symbol}: 期望 {tabs} 排名 {rank}（共 {len(ranked)} 个）")
        print(
            f"  {'OK ' if ok else 'FAIL'} {symbol:6s} 期望 {'/'.join(tabs):16s} "
            f"实际第 {rank} 名  前三: {[_fmt(f.positions) for f in ranked[:3]]}"
        )
    assert not failures, "常用指法未排进前列:\n  " + "\n  ".join(failures)
    print(f"  断言通过: {len(BENCHMARK)} 个和弦的教科书指法全部排进前 {TOP_N}")


def check_finger_assignment(gtr) -> None:
    """手指分配是硬约束：跨度合法但四根手指按不出来的手型必须被剔除。"""
    print("\n=== 手指分配可行性 ===")

    # 跨度 3（<= max_stretch），但 6 弦 1 品与 2 弦 1 品要两根手指，中间夹着
    # 4 弦 3 品、3 弦 2 品，手指绕不过去——真弹 F 只能靠食指横按。
    impossible = (1, 0, 3, 2, 1, None)
    assert plan_fingers(impossible) is None, "同品两指之间夹着更高品，应判为弹不出"
    print(f"  {_fmt(impossible)}: 弹不出 (同品两指之间夹着更高品的按弦)")

    # 跨度 4（仍在 max_stretch 内），但五个互不相连的按弦位置要五根手指。
    too_many = (1, 2, 3, 4, 5, None)
    assert plan_fingers(too_many) is None, "需要五根手指，应判为弹不出"
    print(f"  {_fmt(too_many)}: 弹不出 (需要 5 根手指)")

    # 对照：F 横按跨度更小且合法。
    f_plan = plan_fingers(_parse("133211"))
    assert f_plan is not None and f_plan.barre_fret == 1
    print(f"  133211: 可弹, {f_plan.n_fingers}指, 食指横按 {f_plan.barre_fret} 品")

    # 旧的横按感知计数仍然有效（保留 legacy 排序时要用）。
    n_fingers, barre = analyze_barre(_parse("133211"))
    assert (n_fingers, barre) == (4, 1), "F 横按指头数应为 4 (横按1指+3单音)"
    print(f"  analyze_barre('133211') = {n_fingers} 指 / 横按 {barre} 品（而非逐弦的 6）")

    # 冗余大拇指判据（低音弦孤立高把位、闷掉后仍完整）。
    a_pcs = set(Chord.from_symbol("A").pitch_classes)
    thumb = is_redundant_thumb(_parse("502220"), a_pcs, gtr.tones)
    std = is_redundant_thumb(_parse("x02220"), a_pcs, gtr.tones)
    assert thumb and not std, "冗余大拇指判定应区分 502220 与 x02220"
    print("  502220 冗余大拇指=True / x02220=False（6弦5品的根音被5弦空弦顶替）")


def check_omission(gtr) -> None:
    """接受微量音不一致：七和弦及以上允许省五音，三和弦不许省。"""
    print("\n=== 和弦音省略 ===")

    c7 = Chord.from_symbol("C7")
    c7_pcs = set(c7.pitch_classes)
    assert required_pitch_classes(c7) == c7_pcs - {7}, "C7 应只允许省五音 G"
    print(f"  C7 音级 {sorted(c7_pcs)} -> 必需 {sorted(required_pitch_classes(c7))}（五音 G 可省）")

    c_major = Chord.from_symbol("C")
    assert required_pitch_classes(c_major) == set(c_major.pitch_classes), "三和弦不允许省略"
    print(f"  C  音级 {sorted(c_major.pitch_classes)} -> 必需全含（三和弦不省）")

    # 开放 C7 x32310 就是省了五音的：C-E-Bb，没有 G。允许省略才找得到它。
    open_c7 = _parse("x32310")
    with_omission = enumerate_fingerings("C7", gtr, max_fret=5, max_stretch=4)
    without = enumerate_fingerings(
        "C7", gtr, max_fret=5, max_stretch=4, allow_omissions=False
    )
    assert any(tuple(f.positions) == open_c7 for f in with_omission), "开放 C7 应被找到"
    assert not any(tuple(f.positions) == open_c7 for f in without), (
        "关掉省略后开放 C7 应消失（它缺五音）"
    )
    print(f"  开放 C7 x32310 缺五音: allow_omissions=True 时找到, False 时消失")
    print(f"  候选数 {len(with_omission)} -> {len(without)}（关掉省略后变少）")


def compare_ranking(gtr) -> None:
    """新旧排序对照：旧的分层排序把「把位低」放在一切之前，会放行内部闷音手型。"""
    print("\n=== playable vs legacy 排序（C major 前 5）===")
    playable = enumerate_fingerings("C", gtr, max_fret=7, max_stretch=4, limit=5)
    legacy = enumerate_fingerings(
        "C", gtr, max_fret=7, max_stretch=4, ranking="legacy", limit=5
    )
    print(f"  playable: {[_fmt(f.positions) for f in playable]}")
    print(f"  legacy  : {[_fmt(f.positions) for f in legacy]}")
    assert tuple(playable[0].positions) == _parse("x32010"), "playable 首选应是经典开放 C"
    print("  断言通过: playable 首选 = 经典开放 C 指法 x32010")


def main() -> None:
    gtr = Fretboard.guitar()

    check_benchmark(gtr)
    check_finger_assignment(gtr)
    check_omission(gtr)
    compare_ranking(gtr)

    # 非标准调弦：pytheory 内置查表在此全部失效，枚举器仍然工作。
    # 「最低音 / 原位」一律用 min(midi) 判定，不依赖弦序。
    dad = Fretboard.guitar(tuning="dadgad")
    _show(
        "DADGAD 调弦 / D major",
        enumerate_fingerings("D", dad, max_fret=7, max_stretch=4, limit=6),
        root_pc=2,
        chord_pcs=set(Chord.from_symbol("D").pitch_classes),
        open_tones=dad.tones,
    )

    uk = Fretboard.ukulele()
    _show(
        "尤克里里 GCEA / C major",
        enumerate_fingerings("C", uk, max_fret=5, max_stretch=4, limit=6),
        root_pc=0,
        chord_pcs=set(Chord.from_symbol("C").pitch_classes),
        open_tones=uk.tones,
    )

    # 完全自定义调弦（C4-G3-D3-A2，第 0 弦反而是最高音）。
    custom = Fretboard.guitar(tuning=("C4", "G3", "D3", "A2"))
    _show(
        "自定义调弦 C-G-D-A / G major",
        enumerate_fingerings("G", custom, max_fret=7, max_stretch=4, limit=6),
        root_pc=7,
        chord_pcs=set(Chord.from_symbol("G").pitch_classes),
        open_tones=custom.tones,
    )

    print("\n全部断言通过。")


if __name__ == "__main__":
    main()
