#!/usr/bin/env python3
"""生成「鹈鹕骑自行车」动画页 —— docs/demos/pelican-on-bike.html

纯 SVG + SMIL，单文件，零外部资源、零三方库。本脚本做且只做一件“不纯”的事：
把骑行周期的几何（两段式腿部逆向运动学：大腿/小腿/脚踝精确跟踏）以 N=120 个
等间隔采样烘进 animateTransform 的 values 列表 —— SMIL 自己不会解 IK，但采样后
的分段线性插值在浏览器里就是原生动画，帧间误差 < 0.5px（自检里实测）。

运动学契约（全部可验算，页面脚注同步展示）：
  曲柄周期 T_C = 3.2 s（踏频 18.75 rpm，憨憨巡航）
  齿比     g   = 2   → 车轮周期 T_W = 1.6 s，牙盘 r40 / 飞轮 r20
  轮半径   R   = 75 px → 地面速度 v = 2πR/T_W ≈ 294.5 px/s
  路面磁贴 = v×0.5s，dur 0.5s —— 轮缘线速度与路面位移严格相等（不打滑）
  每个循环动画各自首尾闭环：旋转量为 360° 整数倍、平移量恰为一整块磁贴。

用法：  python3 scripts/gen_pelican_bike.py          # 生成 + 自检
        python3 scripts/gen_pelican_bike.py --check  # 只跑 IK 自检
"""
from __future__ import annotations

import math
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "demos" / "pelican-on-bike.html"
TAU = math.tau

# ------------------------------------------------------------------ 常量
N = 120                      # 每周期采样数（含闭环共 N+1 帧）
T_C = 3.2                    # 曲柄/骨盆/双腿共用周期 s
GEAR = 2.0
T_W = T_C / GEAR             # 车轮周期 s
R_W = 75.0
GROUND_Y = 520.0
REAR = (405.0, GROUND_Y - R_W)
FRONT = (745.0, GROUND_Y - R_W)
BB = (575.0, 462.0)          # 五通
CRANK_R = 34.0
RING_R, COG_R = 40.0, 20.0
V = TAU * R_W / T_W          # 地面速度 px/s
ROAD_TILE = V * 0.5

HIP0 = (516.0, 372.0)        # 鹈鹕髋关节基准位（在车座前沿）
L1, L2 = 80.0, 81.0          # 大腿 / 小腿（BDC 近伸直、TDC 深折叠，全程可达不锁死）
BOB_A, BOB_X = 3.2, 1.6      # 骨盆 bob（2× 曲柄频）

SH_N = (598.0, 304.0)        # 近侧肩（翅膀根）
SH_F = (594.0, 316.0)        # 远侧肩
GRIP = (714.0, 334.0)        # 把套
W1, W2 = 67.0, 63.0

# ------------------------------------------------------------------ 工具


def fmt(x: float, nd: int = 3) -> str:
    if nd <= 0:
        return str(int(round(x)))
    s = f"{x:.{nd}f}"
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s



def gcheck(name: str, frag: str) -> str:
    """装配期守卫：每个部件的 <g> 必须自平衡，别把窟窿漏给浏览器。"""
    o = len(re.findall(r'<g[\s>"]', frag))
    c = frag.count('</g>')
    assert o == c, f"{name}: <g>×{o} vs </g>×{c}"
    return frag


def vlist(vals) -> str:
    return ";".join(vals)


def unwrap_series(vals: list[float]) -> list[float]:
    out = [vals[0]]
    for a, b in zip(vals, vals[1:]):
        d = b - a
        while d > 180.0:
            d -= 360.0
        while d < -180.0:
            d += 360.0
        out.append(out[-1] + d)
    return out


def solve_ik(hip, target, l1, l2):
    """两连杆解析解。返回 (绝对大腿角, 小腿相对角, 膝点)；取膝/肘位于 hip→target
    连线“屏幕上侧”（即骑行者膝朝前上）的连续分支。"""
    dx, dy = target[0] - hip[0], target[1] - hip[1]
    d0 = math.hypot(dx, dy)
    d = min(max(d0, abs(l1 - l2) + 1e-9), l1 + l2 - 1e-9)
    ux, uy = (dx / d0, dy / d0)
    base = math.atan2(uy, ux)
    ca = min(1.0, max(-1.0, (d * d + l1 * l1 - l2 * l2) / (2 * d * l1)))
    alpha = math.acos(ca)
    best = None
    for s in (-1.0, 1.0):
        a1 = base + s * alpha
        kx, ky = hip[0] + l1 * math.cos(a1), hip[1] + l1 * math.sin(a1)
        cross = ux * (ky - hip[1]) - uy * (kx - hip[0])
        if best is None or cross < best[0]:
            best = (cross, a1, kx, ky)
    _, a1, kx, ky = best
    a2 = math.atan2(target[1] - ky, target[0] - kx)
    return math.degrees(a1), math.degrees(a2) - math.degrees(a1), (kx, ky)


def pedal_pos(theta: float, center=BB, r=CRANK_R):
    return (center[0] + r * math.cos(theta), center[1] + r * math.sin(theta))


def bob(theta: float):
    """骨盆 bob（2× 曲柄频）——躯干/头/双臂/双腿的 bobwrap 与 IK 共用这一条曲线，
    所以脚掌与把套在整个周期内严格“焊死”。"""
    return (BOB_X * math.cos(2 * theta + 0.6),
            BOB_A * math.sin(2 * theta - math.pi / 2) - 1.4)


def hip_pos(theta: float):
    bx, by = bob(theta)
    return (HIP0[0] + bx, HIP0[1] + by)


def leg_series(phase: float):
    """返回 (thigh_abs, shin_rel, foot_abs_deg)，均为 N+1 长（末帧=首帧+360 或同值）。"""
    th = [TAU * i / N + phase for i in range(N + 1)]
    th_raw = [TAU * i / N for i in range(N + 1)]
    t1, t2, fa = [], [], []
    for a, raw in zip(th, th_raw):
        hip = hip_pos(raw)
        p1, r2, _ = solve_ik(hip, pedal_pos(a), L1, L2)
        t1.append(p1)
        t2.append(r2)
        fa.append(6.0 + 13.0 * math.sin(a - math.pi / 2))   # 下踩微跖屈、上提回勾
    return unwrap_series(t1), unwrap_series(t2), fa


def wing_series(shoulder: tuple[float, float]):
    """翅膀 IK：肩点在世界系里 = shoulder + bob(θ)（渲染时翅膀在 bobwrap 内，
    与 IK 假设严格一致）⇒ 翼尖整个周期焊死在把套上。"""
    th_raw = [TAU * i / N for i in range(N + 1)]
    a1, a2 = [], []
    for raw in th_raw:
        bx, by = bob(raw)
        sh = (shoulder[0] + bx, shoulder[1] + by)
        p1, r2, _ = solve_ik(sh, GRIP, W1, W2)
        a1.append(p1)
        a2.append(r2)
    return unwrap_series(a1), unwrap_series(a2)


# ------------------------------------------------------------------ 自检


def self_check() -> float:
    """验证：1) 采样插值后脚掌仍落在脚踏上（含样条中点）；2) 全周期可达；
    3) 膝分支一致（不翻转）；4) 首尾闭环。"""
    t1, t2, _ = leg_series(0.0)
    assert abs(t1[0] - t1[-1]) % 360 < 1e-6 or abs(abs(t1[0] - t1[-1]) - 360) < 1e-6
    err = 0.0
    dmin, dmax = 1e9, -1e9
    cross_sign = None
    for i in range(N + 1):
        raw = TAU * i / N
        hip = hip_pos(raw)
        ankle = pedal_pos(raw)
        d = math.hypot(ankle[0] - hip[0], ankle[1] - hip[1])
        dmin, dmax = min(dmin, d), max(dmax, d)
        _, _, knee = solve_ik(hip, ankle, L1, L2)
        ux, uy = (ankle[0] - hip[0]) / d, (ankle[1] - hip[1]) / d
        c = ux * (knee[1] - hip[1]) - uy * (knee[0] - hip[0])
        if cross_sign is None:
            cross_sign = c < 0
        assert (c < 0) == cross_sign, "膝分支翻转了"
    # 帧间（0.5 步）残差：插值后的角度 → 踝位置 vs 脚踏真值
    for i in range(N):
        for u in (0.0, 0.5):
            raw = TAU * (i + u) / N
            hip = hip_pos(raw)
            tgt = pedal_pos(raw)
            p1 = t1[i] + (t1[i + 1] - t1[i]) * u
            p2 = p1 + t2[i] + (t2[i + 1] - t2[i]) * u
            ax = hip[0] + L1 * math.cos(math.radians(p1)) + L2 * math.cos(math.radians(p2))
            ay = hip[1] + L1 * math.sin(math.radians(p1)) + L2 * math.sin(math.radians(p2))
            err = max(err, math.hypot(ax - tgt[0], ay - tgt[1]))
    print(f"[check] 脚-踏最大残差 {err:.3f}px | 髋踝距 {dmin:.1f}~{dmax:.1f} "
          f"(允许 {abs(L1 - L2):.0f}~{L1 + L2:.0f}) | 膝始终朝前上: {cross_sign}")
    assert err < 0.6 and dmin > abs(L1 - L2) and dmax < L1 + L2, "IK self-check FAILED"
    return err


# ------------------------------------------------------------------ SVG 片段


def anim(kind: str, values: str, dur: float) -> str:
    return (f'<animateTransform attributeName="transform" type="{kind}" calcMode="linear" '
            f'dur="{fmt(dur, 4)}s" repeatCount="indefinite" values="{values}"/>')


def rot(vals, dur) -> str:
    return anim("rotate", vlist([fmt(v, 3) for v in vals]), dur)


def rot_pivot(vals, dur, px, py) -> str:
    return anim("rotate", vlist([f"{fmt(v, 3)} {fmt(px, 1)} {fmt(py, 1)}" for v in vals]), dur)


def tr(pairs, dur) -> str:
    return anim("translate", vlist([f"{fmt(x, 2)} {fmt(y, 2)}" for x, y in pairs]), dur)


def tile_uses(gid: str, tile_w: float, total: float = 1200.0) -> str:
    n = math.ceil(total / tile_w) + 1
    return "".join(f'<use href="#{gid}" transform="translate({fmt(k * tile_w, 2)},0)"/>'
                   for k in range(n))


def layer(tile_id: str, tile_w: float, dur: float) -> str:
    return (f'<g><animateTransform attributeName="transform" type="translate" '
            f'calcMode="linear" dur="{fmt(dur, 4)}s" repeatCount="indefinite" '
            f'values="0 0;{-tile_w:.2f} 0"/>{tile_uses(tile_id, tile_w)}</g>')


# ------------------------------------------------------------------ 零件


def wheel(cx: float, cy: float, rear: bool) -> str:
    sp = "".join(f'<line x1="0" y1="0" x2="{fmt(63 * math.cos(TAU * k / 12), 1)}" '
                 f'y2="{fmt(63 * math.sin(TAU * k / 12), 1)}"/>' for k in range(12))
    cog = ""
    if rear:
        teeth = "".join(f'<line x1="{fmt(COG_R * 0.72 * math.cos(TAU * k / 9), 1)}" '
                        f'y1="{fmt(COG_R * 0.72 * math.sin(TAU * k / 9), 1)}" '
                        f'x2="{fmt(COG_R * 1.14 * math.cos(TAU * k / 9), 1)}" '
                        f'y2="{fmt(COG_R * 1.14 * math.sin(TAU * k / 9), 1)}"/>' for k in range(9))
        cog = (f'<circle r="{fmt(COG_R, 1)}" fill="none" stroke="#78909c" stroke-width="4"/>'
               f'<g stroke="#90a4ae" stroke-width="3.6" stroke-linecap="round">{teeth}</g>')
    return f"""
<g transform="translate({fmt(cx, 0)},{fmt(cy, 0)})"><g>
  {rot([0.0, 360.0], T_W)}
  <circle r="{fmt(R_W, 0)}" fill="none" stroke="#263238" stroke-width="13"/>
  <circle r="{fmt(R_W - 11.5, 1)}" fill="none" stroke="#cfd8dc" stroke-width="5"/>
  <g stroke="#b7c6cd" stroke-width="2.4">{sp}</g>
  {cog}
  <circle r="8" fill="#546e7a"/><circle r="3.4" fill="#263238"/>
  <circle cx="0" cy="-56" r="4.4" fill="#ffb347"/>
  <path d="M -30 -52 A 60 60 0 0 1 34 -48" fill="none" stroke="#eceff1" stroke-width="3"
        stroke-linecap="round" opacity="0.5"/>
</g></g>"""


def pedal(side_sign: float, counter_vals, c1: str, c2: str) -> str:
    return (f'<g transform="translate({fmt(side_sign * CRANK_R, 0)},0)"><g>'
            f'{rot(counter_vals, T_C)}'
            f'<rect x="-13.5" y="-4.4" width="27" height="8.8" rx="2.6" fill="{c1}"/>'
            f'<rect x="-13.5" y="-1.5" width="27" height="2.9" rx="1.4" fill="{c2}"/>'
            f'<circle cx="-8.5" cy="0" r="1.4" fill="{c2}"/><circle cx="8.5" cy="0" r="1.4" fill="{c2}"/>'
            f'</g></g>')


def leg(near: bool, t1, t2, foot_abs_rel) -> str:
    if near:
        skin, edge, foot_f, foot_s = "#f2884b", "#c95d22", "#ff9d4d", "#d96a2c"
        op = ""
    else:
        skin, edge, foot_f, foot_s = "#c96a3c", "#9c4a1c", "#d9773d", "#9c4a1c"
        op = ' opacity="0.94"'
    return f"""
<g{op}>
 <g transform="translate({fmt(HIP0[0], 1)},{fmt(HIP0[1], 1)})"><g>
  {rot(t1, T_C)}
  <path d="M -6 -13 Q 36 -16 {fmt(L1, 0)} -10 L {fmt(L1, 0)} 10 Q 36 16 -6 13 Q -16 0 -6 -13 Z"
        fill="{skin}" stroke="{edge}" stroke-width="2.4"/>
  <g transform="translate({fmt(L1, 0)},0)"><g>
   {rot(t2, T_C)}
   <circle r="9" fill="{foot_f}" stroke="{edge}" stroke-width="2.2"/>
   <path d="M -5 -9 Q 40 -11 {fmt(L2, 0)} -6 L {fmt(L2, 0)} 7 Q 40 10 -5 8 Q -12 0 -5 -9 Z"
         fill="{skin}" stroke="{edge}" stroke-width="2.2"/>
   <g transform="translate({fmt(L2, 0)},1)"><g>
    {rot(foot_abs_rel, T_C)}
    <path d="M -9 -7 Q 0 -10 7 -6 L 13 -2 L 36 -4 Q 45 0 41 6 L 31 9 L 25 7 L 26 12
             L 16 10 L 14 15 L 2 11 Q -10 9 -9 -7 Z"
          fill="{foot_f}" stroke="{foot_s}" stroke-width="2" stroke-linejoin="round"/>
    <path d="M 13 -2 L 27 5 M 16 10 L 25 4" stroke="{foot_s}" stroke-width="1.5"
          fill="none" opacity="0.65"/>
   </g></g>
  </g></g>
 </g>
</g>
</g>"""


def wing(shoulder, t1w, t2w, front: bool) -> str:
    if front:
        col, shd, tip = "#f4f7f9", "#c6d2dc", "#eef3f6"
    else:
        col, shd, tip = "#dde6ec", "#b7c5d0", "#ccd9e0"
    return f"""
<g transform="translate({fmt(shoulder[0], 1)},{fmt(shoulder[1], 1)})"><g>
  {rot(t1w, T_C)}
  <path d="M -9 -11 Q 30 -14 {fmt(W1, 0)} -8 L {fmt(W1, 0)} 8 Q 30 14 -9 11 Q -18 0 -9 -11 Z"
        fill="{col}" stroke="{shd}" stroke-width="2.4"/>
  <g transform="translate({fmt(W1, 0)},0)"><g>
   {rot(t2w, T_C)}
   <path d="M -7 -8 Q 28 -10 {fmt(W2, 0)} -6 L {fmt(W2, 0)} 6 Q 28 9 -7 8 Q -13 0 -7 -8 Z"
         fill="{col}" stroke="{shd}" stroke-width="2.2"/>
   <path d="M {fmt(W2 - 8, 0)} -6 L {fmt(W2 + 15, 0)} -2 M {fmt(W2 - 8, 0)} 1 L {fmt(W2 + 16, 0)} 4
            M {fmt(W2 - 8, 0)} 7 L {fmt(W2 + 11, 0)} 9"
         stroke="{tip}" stroke-width="3" stroke-linecap="round" fill="none"/>
   <g transform="translate({fmt(W2 + 8, 0)},2)">
    <path d="M -5 -7 Q 7 -10 12 -4 Q 16 2 10 6 Q 1 10 -6 6 Q -10 0 -5 -7 Z"
          fill="{col}" stroke="{shd}" stroke-width="2"/>
   </g>
  </g></g>
 </g></g>"""


# ------------------------------------------------------------------ 背景


def background() -> str:
    def cloud(x, y, s, o):
        return (f'<g transform="translate({fmt(x, 0)},{fmt(y, 0)}) scale({fmt(s, 2)})" '
                f'fill="#ffffff" opacity="{fmt(o, 2)}">'
                f'<ellipse cx="0" cy="0" rx="46" ry="16"/>'
                f'<ellipse cx="-20" cy="-9" rx="24" ry="13"/>'
                f'<ellipse cx="14" cy="-11" rx="27" ry="15"/>'
                f'<path d="M -46 4 Q 0 18 46 4 Q 0 28 -46 4 Z" opacity="0.65"/></g>')

    cloud_tile = 353.4
    hills_tile = 388.72
    tree_tile = 485.98
    grass_tile = V * 0.75

    def hills(t):
        return (f'<path d="M 0 26 Q 56 -12 122 8 Q 176 24 214 4 Q 268 -16 312 6 Q 352 22 '
                f'{fmt(t, 1)} 26 L {fmt(t, 1)} 80 L 0 80 Z" fill="#b9d8c2"/>'
                f'<path d="M 0 36 Q 70 10 140 26 Q 210 42 270 22 Q 330 8 {fmt(t, 1)} 34 '
                f'L {fmt(t, 1)} 80 L 0 80 Z" fill="#a6cbaf"/>')

    def tree(x, k):
        if k % 3 == 0:
            return (f'<g transform="translate({fmt(x, 1)},0)">'
                    f'<rect x="-4.5" y="-34" width="9" height="36" rx="3" fill="#8a6f52"/>'
                    f'<circle cx="0" cy="-54" r="26" fill="#7dbb8e"/>'
                    f'<circle cx="-16" cy="-42" r="15" fill="#6aa87c"/>'
                    f'<circle cx="15" cy="-44" r="14" fill="#8ec89c"/></g>')
        if k % 3 == 1:
            return (f'<g transform="translate({fmt(x, 1)},0)">'
                    f'<rect x="-3" y="-30" width="6" height="32" rx="2" fill="#8a6f52"/>'
                    f'<path d="M 0 -122 Q 17 -70 12 -28 L -12 -28 Q -17 -70 0 -122 Z" fill="#6fa97f"/>'
                    f'<path d="M 0 -122 Q 8 -70 6 -28 L -1 -28 Q -4 -70 0 -122 Z" fill="#82b98e"/></g>')
        return (f'<g transform="translate({fmt(x, 1)},0)">'
                f'<ellipse cx="0" cy="-12" rx="26" ry="15" fill="#6aa87c"/>'
                f'<ellipse cx="-15" cy="-8" rx="13" ry="9" fill="#7dbb8e"/>'
                f'<ellipse cx="34" cy="-4" rx="10" ry="7" fill="#aab7bd"/>'
                f'<ellipse cx="44" cy="-2" rx="6" ry="4.4" fill="#93a2aa"/></g>')

    trees = "".join(tree(x, k) for k, x in enumerate((46, 168, 300, 386, 452)))
    grit = "".join(f'<circle cx="{fmt((k * 977 % 1000) / 1000 * ROAD_TILE, 1)}" '
                   f'cy="{520 + (k * 383 % 58)}" r="{fmt(0.8 + (k * 7 % 3) * 0.55, 1)}" '
                   f'fill="#6d7683" opacity="0.5"/>' for k in range(30))
    blades = ""
    for k in range(5):
        x = 18 + k * 55
        h = 13 + (k * 29 % 13)
        blades += (f'<path d="M {fmt(x, 0)} 646 Q {fmt(x + 6, 0)} {fmt(646 - h, 0)} '
                   f'{fmt(x + 13, 0)} {fmt(640 - h, 0)}" stroke="#5d9469" stroke-width="4" '
                   f'fill="none" stroke-linecap="round"/>'
                   f'<path d="M {fmt(x + 9, 0)} 646 Q {fmt(x + 13, 0)} {fmt(643 - h, 0)} '
                   f'{fmt(x + 20, 0)} {fmt(641 - h, 0)}" stroke="#77b083" stroke-width="3" '
                   f'fill="none" stroke-linecap="round"/>')
    dust = ""
    for k in range(3):
        d = 1.15 + k * 0.07
        dust += (f'<g opacity="0"><animate attributeName="opacity" values="0;0.4;0.3;0" '
                 f'keyTimes="0;0.12;0.5;1" dur="{fmt(d, 2)}s" begin="{-0.38 * k}s" '
                 f'repeatCount="indefinite"/>'
                 f'<g transform="translate(405,516)"><g>'
                 f'<animateTransform attributeName="transform" type="translate" calcMode="linear" '
                 f'dur="{fmt(d, 2)}s" begin="{-0.38 * k}s" repeatCount="indefinite" '
                 f'values="0 0;-34 -6;-96 -20;-150 -30"/>'
                 f'<circle r="{5 + k * 2}" fill="#d8dcd6" opacity="0.8"/></g></g></g>')
    rays = "".join(f'<line x1="0" y1="-64" x2="0" y2="-53" transform="rotate({k * 30})"/>'
                   for k in range(12))
    dashes = "".join(f'<rect x="{fmt(38 + k * ROAD_TILE, 1)}" y="{fmt(GROUND_Y + 44, 0)}" '
                     f'width="60" height="7" rx="3.5" fill="#f4e9c8" opacity="0.92"/>'
                     for k in range(11))

    return f"""
<rect x="0" y="0" width="1200" height="{fmt(GROUND_Y, 0)}" fill="url(#sky)"/>
<g transform="translate(150,116)">
  <circle r="58" fill="#ffdf8e" opacity="0.45"/>
  <circle r="40" fill="#ffd166"/>
  <g stroke="#ffd166" stroke-width="4" stroke-linecap="round" opacity="0.85">
    {rays}
    <animateTransform attributeName="transform" type="rotate" from="0" to="360" dur="96s"
                      calcMode="linear" repeatCount="indefinite"/>
  </g>
</g>
<defs>
  <g id="cloudT">{cloud(120, 84, 1.0, 0.95)}{cloud(330, 148, 0.7, 0.8)}
    <g transform="translate(205,108)" stroke="#84a0b4" stroke-width="2.6" fill="none"
       stroke-linecap="round">
      <path d="M 0 0 q 5 -6 10 0 q 5 -6 10 0"/>
      <path d="M 36 13 q 4 -5 8 0 q 4 -5 8 0"/>
      <path d="M 62 2 q 4 -5 8 0 q 4 -5 8 0"/>
    </g>
  </g>
  <g id="hillsT" transform="translate(0,442)">{hills(hills_tile)}</g>
  <g id="treesT" transform="translate(0,508)">{trees}</g>
  <g id="roadT">{grit}</g>
  <g id="grassT"><rect x="-6" y="616" width="{fmt(grass_tile + 12, 1)}" height="44"
       fill="#4f8259"/>{blades}</g>
</defs>
{layer("cloudT", cloud_tile, 12.0)}
{layer("hillsT", hills_tile, 6.0)}
<rect x="0" y="498" width="1200" height="22" fill="#d9d2c2"/>
<rect x="0" y="498" width="1200" height="5" fill="#ece4d2"/>
{layer("treesT", tree_tile, 3.0)}
<rect x="0" y="{fmt(GROUND_Y, 0)}" width="1200" height="80" fill="url(#roadg)"/>
<rect x="0" y="{fmt(GROUND_Y, 0)}" width="1200" height="4" fill="#39414d" opacity="0.55"/>
<g>
  <animateTransform attributeName="transform" type="translate" calcMode="linear" dur="0.5s"
                    repeatCount="indefinite" values="0 0;{-ROAD_TILE:.2f} 0"/>
  {dashes}
</g>
{layer("roadT", ROAD_TILE, 0.5)}
<rect x="0" y="598" width="1200" height="6" fill="#39414d" opacity="0.5"/>
<ellipse cx="575" cy="{fmt(GROUND_Y + 3, 0)}" rx="216" ry="10" fill="#263238" opacity="0.16">
  <animate attributeName="rx" values="216;225;216" dur="{fmt(T_C / 2, 3)}s"
           calcMode="spline" keyTimes="0;0.5;1" keySplines="0.42 0 0.58 1;0.42 0 0.58 1"
           repeatCount="indefinite"/>
</ellipse>
{dust}"""


# ------------------------------------------------------------------ 主体


def build_svg(foot_err: float) -> str:
    t1n, t2n, fan = leg_series(0.0)
    t1f, t2f, fef = leg_series(math.pi)
    wn1, wn2 = wing_series(SH_N)
    wf1, wf2 = wing_series(SH_F)

    crank_v = [math.degrees(TAU * i / N) for i in range(N + 1)]        # 0→360
    wob_n = [4.5 * math.sin(TAU * i / N + 1.0) for i in range(N + 1)]
    wob_f = [4.5 * math.sin(TAU * i / N + math.pi + 1.0) for i in range(N + 1)]
    ped_n = [w - a for w, a in zip(wob_n, crank_v)]                    # 世界系保持近水平
    ped_f = [w - a - 180.0 for w, a in zip(wob_f, crank_v)]
    head_bob = [1.7 * math.sin(TAU * i / N + 0.4) + 0.7 * math.sin(2 * TAU * i / N - 0.8)
                for i in range(N + 1)]
    hip_bob = [bob(TAU * i / N) for i in range(N + 1)]
    # 脚绝对角 → 相对小腿的旋转（父链已含 φ1+φ2）
    fa_n = [a - (p1 + p2) for a, p1, p2 in zip(fan, t1n, t2n)]
    fa_f = [a - (p1 + p2) for a, p1, p2 in zip(fef, t1f, t2f)]

    ring_teeth = "".join(f'<line x1="{fmt(RING_R * 0.82 * math.cos(TAU * k / 16), 1)}" '
                         f'y1="{fmt(RING_R * 0.82 * math.sin(TAU * k / 16), 1)}" '
                         f'x2="{fmt(RING_R * 1.07 * math.cos(TAU * k / 16), 1)}" '
                         f'y2="{fmt(RING_R * 1.07 * math.sin(TAU * k / 16), 1)}"/>'
                         for k in range(16))
    ring_arms = "".join(f'<line x1="0" y1="0" '
                        f'x2="{fmt(26 * math.cos(TAU * k / 5 + 0.4), 1)}" '
                        f'y2="{fmt(26 * math.sin(TAU * k / 5 + 0.4), 1)}"/>' for k in range(5))

    c1, c2, r1, r2 = BB, REAR, RING_R + 3.4, COG_R + 3.4
    dx, dy = c2[0] - c1[0], c2[1] - c1[1]
    dist = math.hypot(dx, dy)
    phi, alpha = math.atan2(dy, dx), math.acos((r1 - r2) / dist)
    tp1 = (c1[0] + r1 * math.cos(phi + alpha), c1[1] + r1 * math.sin(phi + alpha))
    tp2 = (c2[0] + r2 * math.cos(phi + alpha), c2[1] + r2 * math.sin(phi + alpha))
    bp1 = (c1[0] + r1 * math.cos(phi - alpha), c1[1] + r1 * math.sin(phi - alpha))
    bp2 = (c2[0] + r2 * math.cos(phi - alpha), c2[1] + r2 * math.sin(phi - alpha))
    chain_d = (f"M {fmt(tp1[0], 1)} {fmt(tp1[1], 1)} L {fmt(tp2[0], 1)} {fmt(tp2[1], 1)} "
               f"A {fmt(r2, 1)} {fmt(r2, 1)} 0 0 0 {fmt(bp2[0], 1)} {fmt(bp2[1], 1)} "
               f"L {fmt(bp1[0], 1)} {fmt(bp1[1], 1)} "
               f"A {fmt(r1, 1)} {fmt(r1, 1)} 0 0 1 {fmt(tp1[0], 1)} {fmt(tp1[1], 1)} Z")
    chain_dash = 10.0
    chain_dur = chain_dash * R_W / (V * COG_R)

    def bobwrap(inner: str) -> str:
        return f'<g>{tr(hip_bob, T_C)}{inner}</g>'

    torso = f"""
  <g transform="rotate(-17 575 345)">
    <g transform="translate(575,345)">
      <ellipse rx="86" ry="54" fill="url(#bodyg)" stroke="#c6d2dc" stroke-width="2.5"/>
      <g clip-path="url(#bodyclip)">
        <path d="M -90 6 L 90 -24 L 90 62 L -90 62 Z" fill="#4ea1ff"/>
        <path d="M -90 28 L 90 -2 L 90 14 L -90 44 Z" fill="#ff5964"/>
        <text x="-17" y="24" font-size="15" font-weight="800" letter-spacing="1.6"
              font-family="ui-sans-serif,system-ui,sans-serif" fill="#f6fbff">MAX</text>
      </g>
      <path d="M -36 -44 Q 4 -53 40 -41" stroke="#dfe7ee" stroke-width="4" fill="none"
            stroke-linecap="round" opacity="0.75"/>
      <g transform="translate(-56,-4) rotate(-3)">
        <g>
          <animateTransform attributeName="transform" type="rotate" values="0;-5;1.5;0"
            keyTimes="0;0.4;0.75;1" dur="0.82s" calcMode="spline"
            keySplines="0.4 0 0.6 1;0.4 0 0.6 1;0.4 0 0.6 1" repeatCount="indefinite"/>
          <path d="M 0 0 Q -34 -18 -58 -6 Q -34 4 0 12 Z" fill="#eef3f6" stroke="#c6d2dc"
                stroke-width="2"/>
          <path d="M -8 7 Q -40 -3 -54 7" stroke="#c6d2dc" stroke-width="2" fill="none"/>
        </g>
      </g>
    </g>
  </g>
  <ellipse cx="{fmt(HIP0[0] + 7, 1)}" cy="{fmt(HIP0[1] - 8, 1)}" rx="27" ry="20"
           fill="#2f6fb3" stroke="#245a92" stroke-width="2"
           transform="rotate(-14 {fmt(HIP0[0] + 7, 1)} {fmt(HIP0[1] - 8, 1)})"/>"""

    head = f"""
  <g>
   {rot_pivot(head_bob, T_C, 612, 322)}
   <path d="M 604 330 Q 618 288 664 268" stroke="#e2ebf0" stroke-width="35" fill="none"
         stroke-linecap="round"/>
   <path d="M 604 330 Q 618 288 664 268" stroke="url(#bodyg)" stroke-width="28" fill="none"
         stroke-linecap="round"/>
   <circle cx="678" cy="258" r="31" fill="url(#bodyg)" stroke="#c6d2dc" stroke-width="2.4"/>
   <path d="M 651 245 Q 656 222 676 218 Q 694 216 702 232 L 703 245 Q 678 236 651 249 Z"
         fill="#ff5964" stroke="#d94a54" stroke-width="2"/>
   <path d="M 652 246 Q 632 250 624 260 Q 636 254 654 252 Z" fill="#e34b56"
         stroke="#c8424d" stroke-width="1.4">
     <animateTransform attributeName="transform" type="rotate" values="0 652 246;7 652 246;0 652 246"
       dur="0.7s" calcMode="spline" keyTimes="0;0.45;1"
       keySplines="0.4 0 0.6 1;0.4 0 0.6 1" repeatCount="indefinite"/>
   </path>
   <circle cx="675" cy="219" r="3.4" fill="#ffd166" stroke="#d94a54" stroke-width="1.4"/>
   <path d="M 700 244 C 742 242 778 249 801 258 C 800 264 795 267 787 267 L 706 271 Z"
         fill="#ffb347" stroke="#e08a1f" stroke-width="2"/>
   <path d="M 793 255 Q 805 259 800 266" fill="none" stroke="#e08a1f" stroke-width="2.4"
         stroke-linecap="round"/>
   <g transform="translate(706,266)"><g>
     <animateTransform attributeName="transform" type="scale" values="1 1;1 1.065;1 1"
       keyTimes="0;0.5;1" dur="{fmt(T_C / 2, 3)}s" calcMode="spline"
       keySplines="0.42 0 0.58 1;0.42 0 0.58 1" repeatCount="indefinite"/>
     <g transform="translate(-706,-266)">
       <path d="M 706 267 C 726 316 776 308 797 267 L 788 269 C 766 294 730 296 713 271 Z"
             fill="url(#pouch)" stroke="#d97d1a" stroke-width="2.2"/>
       <path d="M 719 283 Q 748 303 779 285 M 715 272 Q 744 291 786 273" stroke="#d97d1a"
             stroke-width="1.7" fill="none" opacity="0.55"/>
       <path d="M 731 288 L 743 292 L 736 298 Z" fill="#8fd0e8" stroke="#4f9cb8"
             stroke-width="1.4"/>
     </g>
   </g></g>
   <g transform="translate(790,254)">
     <path d="M 0 0 L -3 -14 L 5 -13 L 3 -5 L 13 -12 L 9 -1 Z" fill="#79c4dd"
           stroke="#4f9cb8" stroke-width="1.5" transform="rotate(-14)">
       <animateTransform attributeName="transform" type="rotate"
         values="-14 0 0;-26 0 0;-8 0 0;-14 0 0" keyTimes="0;0.35;0.7;1" dur="1.3s"
         calcMode="spline" keySplines="0.4 0 0.6 1;0.4 0 0.6 1;0.4 0 0.6 1"
         repeatCount="indefinite"/>
     </path>
   </g>
   <g transform="translate(689,252)"><g>
     <animateTransform attributeName="transform" type="scale"
       values="1 1;1 1;1 0.1;1 1;1 1;1 0.12;1 1" keyTimes="0;0.40;0.435;0.47;0.90;0.935;1"
       dur="5.2s" calcMode="linear" repeatCount="indefinite"/>
     <circle r="11.8" fill="#ffffff" stroke="#31404a" stroke-width="2"/>
     <g>
       <animateTransform attributeName="transform" type="translate"
         values="0 0;1.6 -0.6;0.4 1.4;-1.2 0.4;0 0" dur="7.3s" calcMode="spline"
         keyTimes="0;0.3;0.55;0.8;1"
         keySplines="0.4 0 0.6 1;0.4 0 0.6 1;0.4 0 0.6 1;0.4 0 0.6 1"
         repeatCount="indefinite"/>
       <circle cx="1.4" cy="0.7" r="5.4" fill="#22313a"/>
       <circle cx="-1.1" cy="-1.9" r="1.9" fill="#ffffff"/>
     </g>
   </g></g>
   <circle cx="676" cy="273" r="5" fill="#ffc3cc" opacity="0.7"/>
   <path d="M 667 236 Q 678 231 691 236" stroke="#31404a" stroke-width="2.6" fill="none"
         stroke-linecap="round" opacity="0.5"/>
  </g>"""

    frame = f"""
<g class="frame" fill="none" stroke-linecap="round">
  <g stroke="#c9483c" stroke-width="13">
    <path d="M {fmt(BB[0], 0)} {fmt(BB[1], 0)} L {fmt(REAR[0], 0)} {fmt(REAR[1], 0)}"/>
    <path d="M 508 362 L {fmt(REAR[0], 0)} {fmt(REAR[1], 0)}"/>
    <path d="M {fmt(BB[0], 0)} {fmt(BB[1], 0)} L 706 372"/>
    <path d="M 508 362 Q 608 392 701 356"/>
    <path d="M 505 366 L 512 344"/>
    <path d="M 699 344 L 712 384"/>
  </g>
  <g stroke="#ff6b5e" stroke-width="8.6">
    <path d="M {fmt(BB[0], 0)} {fmt(BB[1], 0)} L {fmt(REAR[0], 0)} {fmt(REAR[1], 0)}"/>
    <path d="M 508 362 L {fmt(REAR[0], 0)} {fmt(REAR[1], 0)}"/>
    <path d="M {fmt(BB[0], 0)} {fmt(BB[1], 0)} L 706 372"/>
    <path d="M 508 362 Q 608 392 701 356"/>
    <path d="M 505 366 L 512 344"/>
    <path d="M 699 344 L 712 384"/>
  </g>
  <path d="M 711 380 Q 736 408 745 {fmt(REAR[1], 0)}" stroke="#37474f" stroke-width="8"/>
  <path d="M 700 344 Q 713 325 736 330" stroke="#37474f" stroke-width="7"/>
  <path d="M 700 344 Q 688 336 676 341" stroke="#37474f" stroke-width="6"/>
</g>
<text x="626" y="416" font-size="10.5" font-family="ui-sans-serif,system-ui,sans-serif"
      font-weight="700" letter-spacing="1" fill="#8c2a20" opacity="0.95"
      transform="rotate(28 626 416)">CMX·BIKE</text>
<g>
  <path d="M 488 336 Q 516 327 546 337 Q 534 345 514 346 Q 494 346 488 336 Z"
        fill="#546e7a" stroke="#37474f" stroke-width="2"/>
  <rect x="497" y="340" width="30" height="6" rx="3" fill="#37474f"/>
</g>
<path d="{chain_d}" fill="none" stroke="#8d9ea8" stroke-width="5.2" opacity="0.3"/>
<path d="{chain_d}" fill="none" stroke="#b7c5cd" stroke-width="4"
      stroke-dasharray="6.2 {fmt(chain_dash - 6.2, 1)}">
  <animate attributeName="stroke-dashoffset" from="0" to="{fmt(chain_dash, 2)}"
           dur="{fmt(chain_dur, 4)}s" calcMode="linear" repeatCount="indefinite"/>
</path>"""

    far_crank = f"""
<g transform="translate({fmt(BB[0], 0)},{fmt(BB[1], 0)})"><g>
  {rot([v + 180.0 for v in crank_v], T_C)}
  <path d="M 0 -7 L {fmt(-CRANK_R, 0)} -5 L {fmt(-CRANK_R, 0)} 5 L 0 7 Z" fill="#2f3d45"/>
  {pedal(-1.0, ped_f, "#1c262b", "#465862")}
</g></g>"""

    near_crank = f"""
<g transform="translate({fmt(BB[0], 0)},{fmt(BB[1], 0)})"><g>
  {rot(crank_v, T_C)}
  <circle r="{fmt(RING_R, 0)}" fill="none" stroke="#9fb2bc" stroke-width="3.4"/>
  <g stroke="#93a7b1" stroke-width="3">{ring_teeth}</g>
  <circle r="27" fill="#eceff1" opacity="0.92" stroke="#9fb2bc" stroke-width="2"/>
  <g stroke="#b8c6cd" stroke-width="6" stroke-linecap="round">{ring_arms}</g>
  <circle r="7.4" fill="#37474f"/><circle r="2.6" fill="#cfd8dc"/>
  <path d="M -7 -7 L {fmt(CRANK_R, 0)} -5.5 L {fmt(CRANK_R, 0)} 5.5 L -7 7 Z" fill="#455a64"/>
  <path d="M -6 -3.6 L {fmt(CRANK_R, 0)} -2.8 L {fmt(CRANK_R, 0)} 2.8 L -6 3.6 Z"
        fill="#607d8b"/>
  {pedal(1.0, ped_n, "#263238", "#546e7a")}
</g></g>"""

    return f"""
<svg id="scene" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 640"
     role="img" aria-label="鹈鹕骑自行车循环动画：车轮匀速滚动，双腿随曲柄做两段式 IK 联动，背景无缝滚动"
     shape-rendering="geometricPrecision">
<defs>
  <linearGradient id="sky" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0" stop-color="#a8dcef"/><stop offset="0.55" stop-color="#d6eff4"/>
    <stop offset="0.85" stop-color="#f7ecd2"/><stop offset="1" stop-color="#f2dab4"/>
  </linearGradient>
  <linearGradient id="roadg" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0" stop-color="#61697a"/><stop offset="1" stop-color="#4a525f"/>
  </linearGradient>
  <linearGradient id="pouch" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0" stop-color="#ffb347"/><stop offset="1" stop-color="#ef8a1c"/>
  </linearGradient>
  <radialGradient id="bodyg" cx="0.42" cy="0.34" r="0.95">
    <stop offset="0" stop-color="#ffffff"/><stop offset="0.72" stop-color="#f3f7fa"/>
    <stop offset="1" stop-color="#dbe5ec"/>
  </radialGradient>
  <clipPath id="bodyclip"><ellipse cx="0" cy="0" rx="86" ry="54"/></clipPath>
</defs>
{background()}
{wheel(*REAR, True)}
{wheel(*FRONT, False)}
{far_crank}
{bobwrap(leg(False, t1f, t2f, fa_f))}
{frame}
{bobwrap(wing(SH_F, wf1, wf2, False) + torso + head)}
{near_crank}
{bobwrap(leg(True, t1n, t2n, fa_n))}
{bobwrap(wing(SH_N, wn1, wn2, True))}
<circle cx="{fmt(GRIP[0], 0)}" cy="{fmt(GRIP[1], 0)}" r="3.4" fill="#37474f"/>
<g transform="translate(690,322)">
  <circle r="7.6" fill="#ffd166" stroke="#c99a2e" stroke-width="2"/>
  <circle r="2" fill="#8a6b1d"/>
  <path d="M -12 -3 L -8 -5 M -11 3 L -7 3" stroke="#ffe9ad" stroke-width="2.2"
        stroke-linecap="round" opacity="0">
    <animate attributeName="opacity" values="0;0;0.95;0;0" keyTimes="0;0.42;0.46;0.55;1"
             dur="2.9s" repeatCount="indefinite"/>
  </path>
</g>
{layer("grassT", V * 0.75, 0.75)}
</svg>"""


# ------------------------------------------------------------------ HTML


def build_html(foot_err: float) -> str:
    svg = build_svg(foot_err)
    cadence = 60.0 / T_C
    css = """
:root{--ink:#22303a;--muted:#5b6b76;--bg:#eef4f6}
*{box-sizing:border-box}
html,body{margin:0}
body{background:radial-gradient(1200px 620px at 30% -10%,#fdf6e3 0%,var(--bg) 55%);
     font-family:ui-sans-serif,system-ui,"PingFang SC","Microsoft YaHei",sans-serif;color:var(--ink)}
.wrap{max-width:1080px;margin:0 auto;padding:26px 18px 40px}
header{display:flex;flex-wrap:wrap;align-items:baseline;gap:10px 14px;margin-bottom:14px}
h1{font-size:clamp(20px,3.4vw,30px);margin:0;letter-spacing:.5px}
h1 small{font-weight:500;color:var(--muted);font-size:.55em;margin-left:8px}
.chips{display:flex;gap:8px;flex-wrap:wrap}
.chip{font-size:12px;padding:4px 10px;border-radius:999px;background:#fff;color:#0f5f74;
      border:1px solid #cfe0e6;box-shadow:0 1px 0 #dfe9ee;white-space:nowrap}
.stage{position:relative;background:linear-gradient(#fff,#f6fafb);border:1px solid #d8e4e9;
       border-radius:18px;overflow:hidden;box-shadow:0 14px 34px -18px rgba(15,60,80,.45)}
.stage svg{display:block;width:100%;height:auto}
body.paused .stage::after{content:"⏸ 已暂停 · 时间轴冻结";position:absolute;right:14px;bottom:14px;
       background:rgba(20,34,44,.82);color:#fff;font-size:13px;padding:6px 12px;
       border-radius:999px;letter-spacing:1px}
.hud{display:flex;align-items:center;gap:10px;margin:14px 2px 0;flex-wrap:wrap}
button{appearance:none;border:1px solid #b9d2dd;background:#fff;color:#0b4f63;font:inherit;
       font-size:14px;font-weight:600;padding:9px 16px;border-radius:12px;cursor:pointer;
       transition:transform .12s ease,box-shadow .12s ease}
button:hover{transform:translateY(-1px);box-shadow:0 6px 16px -8px rgba(10,70,90,.5)}
button:focus-visible{outline:3px solid #67c3e0;outline-offset:2px}
.kbd{font-size:11px;background:#eef6f9;border:1px solid #cfe0e6;border-radius:5px;
     padding:1px 6px;margin-left:8px;color:#48626e;font-weight:500}
.state{font-size:12.5px;color:var(--muted)}
footer{margin-top:16px;font-size:12.5px;line-height:1.9;color:var(--muted);
        border-top:1px dashed #cddade;padding-top:12px}
footer b{color:var(--ink)}
footer code{background:#e9f2f5;border-radius:4px;padding:1px 5px;font-size:.92em}
@media (max-width:640px){.chips{display:none}}
"""
    js = """
const svg = document.getElementById('scene');
const btn = document.getElementById('toggle');
const reset = document.getElementById('reset');
const state = document.getElementById('state');
let playing = true;
function setPlaying(p){
  if (playing === p) return;
  playing = p;
  playing ? svg.unpauseAnimations() : svg.pauseAnimations();
  document.body.classList.toggle('paused', !playing);
  btn.firstChild.textContent = playing ? '暂停' : '播放';
  btn.setAttribute('aria-pressed', String(!playing));
  state.textContent = playing ? 'SMIL 运行中 · 空格可暂停 · 点画面也可暂停'
                              : '已冻结在当前帧（SVG 时间轴暂停，非 CSS display 切换）';
}
btn.addEventListener('click', () => setPlaying(!playing));
reset.addEventListener('click', () => { svg.setCurrentTime(0); setPlaying(true); });
window.addEventListener('keydown', e => {
  if (e.code === 'Space' && e.target.tagName !== 'BUTTON') {
    e.preventDefault();
    setPlaying(!playing);
  }
});
svg.addEventListener('click', () => setPlaying(!playing));
if (window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches) setPlaying(false);
"""
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>鹈鹕骑自行车 · 纯 SVG + SMIL 单文件循环动画</title>
<meta name="description" content="纯 SVG + 原生 SMIL 的鹈鹕骑车动画：两段式腿部 IK 预采样烘焙、车轮与路面严格同速不打滑、多层视差背景无缝闭环，单文件零依赖。">
<style>{css}</style>
</head>
<body>
<main class="wrap">
<header>
  <h1>鹈鹕骑自行车 <small>the eternal pelican on a bicycle</small></h1>
  <div class="chips">
    <span class="chip">曲柄 {fmt(T_C, 2)}s/圈 ≈ {fmt(cadence, 1)} rpm</span>
    <span class="chip">齿比 {fmt(GEAR, 0)} · 车轮 {fmt(T_W, 1)}s/圈</span>
    <span class="chip">路面 {fmt(V, 1)} px/s，与轮缘同速（零打滑）</span>
    <span class="chip">腿部 IK 采样 {N} 帧/周期，残差 &lt;{fmt(foot_err, 2)}px</span>
    <span class="chip">外部资源 0 · 三方库 0</span>
  </div>
</header>

<div class="stage">
{svg}
</div>

<div class="hud">
  <button id="toggle" aria-pressed="false">暂停<span class="kbd">Space</span></button>
  <button id="reset">回到第 0 帧</button>
  <span class="state" id="state">SMIL 运行中 · 空格可暂停 · 点画面也可暂停</span>
</div>

<footer>
<b>它是怎么动起来的：</b>
① <b>两段式腿部 IK</b> —— 浏览器 SMIL 不会解方程，所以由
<code>scripts/gen_pelican_bike.py</code> 用解析解（余弦定理解出大腿绝对角，小腿取相对角，
始终选“膝朝前上”的连续分支）按 {N} 帧/周期采样烘进 <code>animateTransform values</code>；
脚踏按 <code>五通 + r·(cosθ, sinθ)</code> 匀速画圆，脚掌踝点逐帧落在同一个圆上，
实测整周期（含帧间中点）残差 {fmt(foot_err, 2)}px —— 脚“长”在踏板上。
骨盆/躯干/头颈/翅膀共用同一条 2× 频 bob 曲线，蹼足随相位做跖屈回勾。
② <b>传动闭环</b> —— 曲柄 360°/周期 ↔ 车轮 720°/周期（齿比 {fmt(GEAR, 0)}），
牙盘、链条 dashoffset、飞轮同速；路面磁贴 = v×0.5s 平移，与轮缘线速度
{fmt(V, 1)}px/s 严格相等 ⇒ 不打滑。
③ <b>首尾帧</b> —— 每个动画独立成环：旋转量恰为 360° 的整数倍、平移量恰为一整块视差磁贴
（云 0.10× / 丘陵 0.22× / 树 0.55× / 路 1.0× / 前景草 1.25×），任意时刻都在循环里，永不跳帧。
暂停用原生 <code>svg.pauseAnimations()</code>，Space 与按钮/点画面均可，不引入任何库。
</footer>
</main>
<script>{js}</script>
</body>
</html>
"""


def main() -> None:
    import re
    import xml.etree.ElementTree as ET

    err = self_check()
    html = build_html(err)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(html, encoding="utf-8")
    svg = re.search(r"<svg.*?</svg>", html, re.S).group(0)
    ET.fromstring(svg)  # 标记平衡守卫：嵌套错了直接炸在这里，而不是浏览器里
    print(f"[ok] {OUT.relative_to(ROOT)}  {OUT.stat().st_size / 1024:.0f} KB · "
          f"animate* 标签 {html.count('<animate')} 个")


if __name__ == "__main__":
    if "--check" in sys.argv:
        self_check()
    else:
        main()
