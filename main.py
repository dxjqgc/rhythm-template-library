"""指板和弦指法枚举演示入口。"""

from fingering_enumerator import analyze_barre, enumerate_fingerings, is_redundant_thumb
from pytheory import Chord, Fretboard


def _pitch_class(tone) -> int:
    return tone.midi % 12


def _show(title: str, fingerings, root_pc: int) -> None:
    print(f"\n=== {title} ===")
    print(f"共 {len(fingerings)} 个指法（按原位>转位、把位低、完整、指头少、跨度小 排序）")
    for f in fingerings[:6]:
        tones = f.tones
        lowest = min(tones, key=lambda t: t.midi)
        inv = "原位" if _pitch_class(lowest) == root_pc else "转位"
        n_fingers, barre = analyze_barre(f.positions)
        barre_tag = f"横按{barre}品" if barre is not None else "无横按"
        n_str = len(tones)
        pos = f.positions
        fretted = [p for p in pos if p is not None]
        span = max(fretted) - min(fretted) if fretted else 0
        print(f"  {f}  ->  {f.identify():10s}  [{inv}] {n_str}弦 "
              f"{n_fingers}指 {barre_tag} 跨度{span}")


def main() -> None:
    gtr = Fretboard.guitar()

    # 1) 标准调弦：C 大和弦。验证经典开放指法 (x32010) 排最前。
    all_c = enumerate_fingerings("C", gtr, max_fret=7, max_stretch=4, limit=10)
    _show("标准调弦 / C major", all_c, root_pc=0)
    full_c = enumerate_fingerings("C", gtr, max_fret=7, max_stretch=4)
    classic_present = any(tuple(f.positions) == (None, 3, 2, 0, 1, 0) for f in full_c)
    print(f"  经典开放 C 指法 (x32010) 是否找到: {classic_present}")

    # 2) F 大横按：验证横按 (133211) 评估为 4 指而非 6 指。
    f_fing = gtr.fingering(1, 3, 3, 2, 1, 1)
    nf, bf = analyze_barre(f_fing.positions)
    print(f"\n  F 横按 (133211): {f_fing.identify()}, 指头数={nf}, 横按品={bf}")
    assert nf == 4 and bf == 1, "F 横按指头数应为 4 (横按1指+3单音)"
    print(f"  断言通过: 横按感知指头数 = {nf} (而非逐弦计数的 6)")

    # 3) 冗余大拇指按法：A 大和弦 (5,0,2,2,2,0) 的 6 弦 5 品低八度根音冗余。
    a_chord = Chord.from_symbol("A")
    a_thumb = gtr.fingering(5, 0, 2, 2, 2, 0)
    redundant = is_redundant_thumb(a_thumb.positions, a_chord.pitch_classes, gtr.tones)
    a_std = gtr.fingering(None, 0, 2, 2, 2, 0)
    std_redundant = is_redundant_thumb(a_std.positions, a_chord.pitch_classes, gtr.tones)
    print(f"\n  A (5,0,2,2,2,0): 冗余大拇指={redundant} (6弦5品根音被5弦0品空弦代)")
    print(f"  A x02220:       冗余大拇指={std_redundant} (标准开放, 无大拇指)")
    assert redundant and not std_redundant, "冗余大拇指判定应区分两者"
    print("  断言通过: 冗余大拇指判据正确识别 (5,0,2,2,2,0) 并放过 x02220")

    # 4) DADGAD 调弦：D 大和弦 -- pytheory 内置查表在此失效，枚举器仍可工作。
    dad = Fretboard.guitar(tuning="dadgad")
    ds = enumerate_fingerings("D", dad, max_fret=7, max_stretch=4, limit=10)
    _show("DADGAD 调弦 / D major", ds, root_pc=2)

    # 5) 尤克里里（4 弦，GCEA）：C 大和弦。
    uk = Fretboard.ukulele()
    us = enumerate_fingerings("C", uk, max_fret=5, max_stretch=4, limit=10)
    _show("尤克里里 / C major", us, root_pc=0)

    # 6) 完全自定义调弦（C-G-D-A，类提琴）：G 大和弦。
    #    第 0 弦是 C4（高音），原位检测靠 min(midi) 找最低音，不依赖弦序。
    custom = Fretboard.guitar(tuning=("C4", "G3", "D3", "A2"))
    gs = enumerate_fingerings("G", custom, max_fret=7, max_stretch=4, limit=10)
    _show("自定义调弦 C-G-D-A / G major", gs, root_pc=7)


if __name__ == "__main__":
    main()
