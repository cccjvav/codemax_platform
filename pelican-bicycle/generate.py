#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
鹈鹕骑自行车 · Pelican Riding a Bicycle
Pure SVG + native SMIL animation, single self-contained HTML file.

Key mechanics (all pre-computed analytically, emitted as SMIL values lists):
  * Wheels rotate at a constant speed that exactly matches the scrolling road
    (2 wheel revs per 1.2 s, circumference 560 px  ->  933.3 px/s ground speed).
  * Crank + pedals rotate once per 1.2 s; each pedal counter-rotates so the
    platform stays horizontal in world space.
  * Legs are solved with two-segment analytic inverse kinematics (thigh +
    shank) for 96 samples of the crank cycle; SMIL interpolates linearly
    between samples, so the webbed foot stays glued to the pedal spindle
    (max deviation is measured by the built-in check, typically < 0.3 px).
  * The rider's hips bob vertically (2 bobs per crank rev); the very same
    sampled bob curve is baked into the IK, so feet never leave the pedals.
  * Every scrolling background layer shifts by an exact integer multiple of
    its tile width per loop -> seamless.

Run:  python3 generate.py            # writes index.html + runs verification
      python3 generate.py --check   # verification only
"""

import math
import sys

# ----------------------------------------------------------------------------
# Geometry constants (bicycle-local coords: origin = bottom bracket, y down)
# ----------------------------------------------------------------------------
T = 1.2                     # crank period (s) — one pedal revolution
N = 96                      # IK samples per crank cycle
VBW, VBH = 960, 640         # viewBox

BBX, BBY = 450.0, 386.9     # bottom bracket position in scene coords
ROAD_TOP = 455.0            # road surface y (wheel contact line)

RW = 560.0 / (2 * math.pi)  # wheel radius: circumference 560
AXR = (-118.0, -21.0)       # rear axle (bike-local)
AXF = (148.0, -21.0)        # front axle
CRANK = 42.0                # crank radius
HIP_N = (-14.0, -170.0)     # near hip
HIP_F = (-18.0, -174.0)     # far hip
L1 = 110.0                  # thigh length
L2 = 110.0                  # shank length
BOB_A = 3.2                 # hip bob amplitude (px)

WHEEL_DEG = 720.0           # wheel rotation per crank loop (2 revs, gear 2:1)
ROAD_SHIFT = 2 * 560.0      # 1120 px per 1.2 s  (== wheel surface speed)
CHAIN_PERIOD = 15.708       # chain dash period (dash 9 + gap 6.708)
CHAIN_TRAVEL = 10 * CHAIN_PERIOD   # chain speed 157.08 px per crank loop

# ----------------------------------------------------------------------------
# Inverse kinematics
# ----------------------------------------------------------------------------

def bob(t):
    """Hip bob: 2 bumps per crank revolution, lowest at each pedal-bottom."""
    return BOB_A * (0.5 - 0.5 * math.cos(4.0 * math.pi * t / T))


def U(a_deg):
    r = math.radians(a_deg)
    return (-math.sin(r), math.cos(r))


def ik(hip, target):
    """Two-segment IK. Returns (a1, a2): absolute SVG rotation angles of the
    thigh (about hip) and shank (about knee). Knee bends forward (+x)."""
    dx = target[0] - hip[0]
    dy = target[1] - hip[1]
    d = math.hypot(dx, dy)
    if d < 1e-9:
        d = 1e-9
    dmax = L1 + L2 - 0.001
    dmin = abs(L1 - L2) + 0.001
    if dmax < d:
        s = dmax / d
        dx, dy, d = dx * s, dy * s, dmax
    elif d < dmin:
        s = dmin / d
        dx, dy, d = dx * s, dy * s, dmin
    a_base = math.degrees(math.atan2(-dx, dy))
    cb = (L1 * L1 + d * d - L2 * L2) / (2.0 * L1 * d)
    beta = math.degrees(math.acos(max(-1.0, min(1.0, cb))))
    a1 = a_base - beta                              # knee toward +x
    knee = (hip[0] + L1 * U(a1)[0], hip[1] + L1 * U(a1)[1])
    a2 = math.degrees(math.atan2(-(target[0] - knee[0]), target[1] - knee[1]))
    return a1, a2


def pedal_pos(theta_deg, far=False):
    th = math.radians(theta_deg + (180.0 if far else 0.0))
    return (CRANK * math.cos(th), CRANK * math.sin(th))


def leg_lists(hip_base, far=False):
    """Sampled angle lists for one leg -> (A1, A2local, AFlocal)."""
    A1, A2, AF = [], [], []
    for i in range(N + 1):
        t = T * i / N
        th = 360.0 * i / N
        hip = (hip_base[0], hip_base[1] + bob(t))
        ped = pedal_pos(th, far)
        a1, a2 = ik(hip, ped)
        aF = 4.0 * math.sin(math.radians(th + (180.0 if far else 0.0)))
        A1.append(a1)
        A2.append(a2 - a1)
        AF.append(aF - a2)
    return A1, A2, AF


LEG_N = leg_lists(HIP_N, far=False)
LEG_F = leg_lists(HIP_F, far=True)
BOB_LIST = [bob(T * i / N) for i in range(N + 1)]


def _interp(vals, t):
    x = ((t / T) % 1.0) * N
    i = min(int(x), N - 1)
    f = x - i
    return vals[i] * (1 - f) + vals[i + 1] * f


def max_foot_error(hip_base, far=False):
    """Rendered foot position (piecewise-linear SMIL) vs exact pedal spindle."""
    A1, A2, _ = leg_lists(hip_base, far)
    worst = 0.0
    steps = 2400
    for k in range(steps):
        t = T * k / steps
        ped = pedal_pos(360.0 * t / T, far)
        hip = (hip_base[0], hip_base[1] + _interp(BOB_LIST, t))
        a1 = _interp(A1, t)
        a2w = a1 + _interp(A2, t)
        ank = (hip[0] + L1 * U(a1)[0] + L2 * U(a2w)[0],
               hip[1] + L1 * U(a1)[1] + L2 * U(a2w)[1])
        worst = max(worst, math.hypot(ank[0] - ped[0], ank[1] - ped[1]))
    return worst


# ----------------------------------------------------------------------------
# Animation plumbing
# ----------------------------------------------------------------------------

REG = []      # every Ax created during the current build
BAKE = None   # None -> emit SMIL;  float -> bake state at that time


class Ax:
    def __init__(self, kind, dur, vals, keyTimes=None, additive=False,
                 begin=0.0, name=None):
        self.kind = kind          # translate|rotate|scale|attr|flagd
        self.name = name          # attribute name for kind == 'attr'
        self.dur = dur
        self.vals = vals
        self.keyTimes = keyTimes
        self.additive = additive
        self.begin = begin

    def val(self, t):
        tt = ((t - self.begin) % self.dur) / self.dur
        n = len(self.vals)
        if tt >= 1.0:
            tt = 0.999999
        ts = self.keyTimes or [i / (n - 1) for i in range(n)]
        for i in range(n - 1):
            if ts[i] <= tt <= ts[i + 1]:
                span = ts[i + 1] - ts[i]
                f = 0.0 if span <= 0 else (tt - ts[i]) / span
                a, b = self.vals[i], self.vals[i + 1]
                if isinstance(a, (tuple, list)):
                    return tuple(a[j] + (b[j] - a[j]) * f for j in range(len(a)))
                return a + (b - a) * f
        return self.vals[-1]


class FlagAx(Ax):
    """d-morph animation between point lists with identical structure."""

    def val(self, t):
        tt = ((t - self.begin) % self.dur) / self.dur
        n = len(self.vals)
        if tt >= 1.0:
            tt = 0.999999
        ts = self.keyTimes or [i / (n - 1) for i in range(n)]
        for i in range(n - 1):
            if ts[i] <= tt <= ts[i + 1]:
                span = ts[i + 1] - ts[i]
                f = 0.0 if span <= 0 else (tt - ts[i]) / span
                a, b = self.vals[i], self.vals[i + 1]
                return [(a[j][0] + (b[j][0] - a[j][0]) * f,
                         a[j][1] + (b[j][1] - a[j][1]) * f) for j in range(len(a))]
        return list(self.vals[-1])


def ax(kind, dur, vals, **kw):
    o = Ax(kind, dur, vals, **kw)
    REG.append(o)
    return o


def f2(x):
    return "%.2f" % x


def _fmt_tf(kind, v):
    if kind == 'rotate':
        return f2(v)
    if kind == 'translate':
        return "%s %s" % (f2(v[0]), f2(v[1]))
    if kind == 'scale':
        return "%.4f %.4f" % (v[0], v[1])
    raise ValueError(kind)


def _fmt_attr(name, v):
    if name == 'opacity':
        return "%.3f" % v
    return "%.2f" % v


def flag_d(pts):
    d = "M%s,%s" % (f2(pts[0][0]), f2(pts[0][1]))
    d += " C%s,%s %s,%s %s,%s" % (f2(pts[1][0]), f2(pts[1][1]),
                                  f2(pts[2][0]), f2(pts[2][1]),
                                  f2(pts[3][0]), f2(pts[3][1]))
    d += " C%s,%s %s,%s %s,%s" % (f2(pts[4][0]), f2(pts[4][1]),
                                  f2(pts[5][0]), f2(pts[5][1]),
                                  f2(pts[6][0]), f2(pts[6][1]))
    return d + " Z"


def smil_child(a):
    if a.kind == 'attr':
        vs = ";".join(_fmt_attr(a.name, v) for v in a.vals)
        s = ('<animate attributeName="%s" values="%s" dur="%gs" '
             'repeatCount="indefinite"' % (a.name, vs, a.dur))
    elif a.kind == 'flagd':
        vs = ";".join(flag_d(v) for v in a.vals)
        s = ('<animate attributeName="d" values="%s" dur="%gs" '
             'repeatCount="indefinite"' % (vs, a.dur))
    else:
        vs = ";".join(_fmt_tf(a.kind, v) for v in a.vals)
        s = ('<animateTransform attributeName="transform" type="%s" values="%s" '
             'dur="%gs" repeatCount="indefinite"' % (a.kind, vs, a.dur))
        if a.additive:
            s += ' additive="sum"'
    if a.keyTimes:
        s += ' keyTimes="%s"' % ";".join("%.4f" % x for x in a.keyTimes)
    if a.begin:
        s += ' begin="%gs"' % a.begin
    return s + '/>'


def TF(base, a):
    """(transform-attr, smil-child) for one element in the current mode."""
    if BAKE is None:
        return (base or ''), smil_child(a)
    v = a.val(BAKE)
    if a.kind == 'rotate':
        s = 'rotate(%s)' % f2(v)
    elif a.kind == 'translate':
        s = 'translate(%s %s)' % (f2(v[0]), f2(v[1]))
    elif a.kind == 'scale':
        s = 'scale(%.4f %.4f)' % (v[0], v[1])
    else:
        raise ValueError(a.kind)
    return ((base + ' ' + s) if base else s), ''


def AT(default, a):
    """(attribute-value, smil-child) for plain attributes (opacity / r / ...)."""
    if BAKE is None:
        return str(default), smil_child(a)
    return _fmt_attr(a.name, a.val(BAKE)), ''


def TATTR(s):
    """Emit a transform attribute only when non-empty."""
    return (' transform="%s"' % s) if s else ''


def PIVOT(px, py, mid_attr, mid_child, content):
    """pivot wrapper: translate(p) -> [animated middle g] -> translate(-p)."""
    return ('<g transform="translate(%s,%s)">'
            '<g%s>%s'
            '<g transform="translate(%s,%s)">%s</g>'
            '</g></g>' % (f2(px), f2(py), TATTR(mid_attr), mid_child,
                          f2(-px), f2(-py), content))


# ----------------------------------------------------------------------------
# Palette
# ----------------------------------------------------------------------------
SKY_TOP = '#7fc4e8'
FRAME = '#2a9d8f'
TIRE = '#2e3239'
RIM = '#e9e2cf'
SPOKE = '#b9b3a4'
BODY = '#fdf8ef'
BODY_SH = '#e8dfcc'
BODY_LN = '#d8ccb4'
WING = '#f3ecdd'
BEAK = '#f6a94f'
BEAK_LN = '#d9822f'
POUCH = '#ffb98a'
POUCH_LN = '#f08e57'
LEG_OR = '#f29b4b'
LEG_OR_LN = '#d97f33'
HELMET = '#e8564f'
HELMET_LN = '#c23f39'
FAR = {'body': '#e6ddca', 'line': '#c9bda6', 'leg': '#d68a3f', 'legln': '#b06f2c'}

# ----------------------------------------------------------------------------
# Background
# ----------------------------------------------------------------------------

def build_defs():
    d = ['<defs>']
    d.append('<linearGradient id="gSky" x1="0" y1="0" x2="0" y2="1">'
             '<stop offset="0" stop-color="#7fc4e8"/>'
             '<stop offset=".42" stop-color="#bfe9f6"/>'
             '<stop offset=".5" stop-color="#f2ecc9"/>'
             '<stop offset="1" stop-color="#f2ecc9"/></linearGradient>')
    d.append('<linearGradient id="gRoad" x1="0" y1="0" x2="0" y2="1">'
             '<stop offset="0" stop-color="#646c78"/>'
             '<stop offset="1" stop-color="#565d68"/></linearGradient>')
    d.append('<radialGradient id="gSun">'
             '<stop offset="0" stop-color="#fff7cf"/>'
             '<stop offset=".65" stop-color="#ffdd66"/>'
             '<stop offset="1" stop-color="#ffc94d"/></radialGradient>')
    # clouds & birds
    d.append('<g id="cloud-s">'
             '<ellipse cx="72" cy="36" rx="55" ry="11" fill="#dceef6"/>'
             '<ellipse cx="42" cy="27" rx="20" ry="16" fill="#ffffff"/>'
             '<ellipse cx="72" cy="17" rx="27" ry="21" fill="#ffffff"/>'
             '<ellipse cx="104" cy="27" rx="19" ry="15" fill="#ffffff"/></g>')
    d.append('<g id="cloud-b">'
             '<ellipse cx="95" cy="47" rx="74" ry="14" fill="#d9ecf5"/>'
             '<ellipse cx="55" cy="36" rx="27" ry="21" fill="#ffffff"/>'
             '<ellipse cx="95" cy="22" rx="36" ry="27" fill="#ffffff"/>'
             '<ellipse cx="138" cy="36" rx="26" ry="20" fill="#ffffff"/></g>')
    d.append('<g id="bird"><path d="M0,0 C2.5,-4.5 5.5,-4.5 8,0 C10.5,-4.5 13.5,-4.5 16,0" '
             'stroke="#5a6b75" stroke-width="2" fill="none" stroke-linecap="round"/></g>')
    d.append('<g id="t-clfar"><use xlink:href="#cloud-s" href="#cloud-s" '
             'transform="translate(30,52) scale(.8)" opacity=".92"/></g>')
    d.append('<g id="t-clnear">'
             '<use xlink:href="#cloud-b" href="#cloud-b" transform="translate(140,88)"/>'
             '<use xlink:href="#bird" href="#bird" transform="translate(52,84) scale(.9)"/>'
             '<use xlink:href="#bird" href="#bird" transform="translate(470,142) scale(.75)"/>'
             '</g>')
    # mountains (base line y=308 = horizon)
    d.append('<g id="t-mtn">'
             '<path d="M-10,308 L92,212 L196,308 Z" fill="#a9c4cd"/>'
             '<path d="M92,212 L116,242 L92,252 L70,240 Z" fill="#eef6f9"/>'
             '<path d="M150,308 L262,190 L378,308 Z" fill="#9db9c4"/>'
             '<path d="M262,190 L290,224 L262,236 L238,222 Z" fill="#eef6f9"/>'
             '<path d="M330,308 L428,232 L540,308 Z" fill="#a9c4cd"/>'
             '<path d="M428,232 L448,256 L428,264 L410,254 Z" fill="#eef6f9"/></g>')
    # rolling hills
    d.append('<g id="t-hill">'
             '<ellipse cx="130" cy="322" rx="205" ry="54" fill="#7fb069"/>'
             '<ellipse cx="432" cy="318" rx="175" ry="42" fill="#74a761"/></g>')
    # trees / fence / bushes tile
    t = ['<g id="t-tree">']
    t.append('<rect x="52" y="286" width="11" height="64" rx="3.5" fill="#8a5a3c"/>')
    t.append('<circle cx="57" cy="266" r="30" fill="#5d9e58"/>'
             '<circle cx="38" cy="280" r="19" fill="#54904f"/>'
             '<circle cx="78" cy="278" r="21" fill="#67a95f"/>'
             '<circle cx="52" cy="254" r="10" fill="#79bb6d"/>')
    t.append('<rect x="146" y="292" width="10" height="58" rx="3" fill="#7c4f34"/>')
    t.append('<path d="M151,214 L118,300 L184,300 Z" fill="#4e8f5b"/>')
    t.append('<path d="M151,246 L126,306 L176,306 Z" fill="#5a9b63"/>')
    t.append('<ellipse cx="248" cy="344" rx="26" ry="15" fill="#6aa84f"/>'
             '<ellipse cx="232" cy="338" rx="13" ry="10" fill="#79bb6d"/>')
    for i in range(11):
        t.append('<rect x="%d" y="402" width="6" height="32" rx="2" fill="#a3835f"/>'
                 % (i * 56))
    t.append('<rect x="0" y="408" width="560" height="4" fill="#b5956f"/>'
             '<rect x="0" y="420" width="560" height="4" fill="#b5956f"/>')
    for cx, cy in [(310, 356), (505, 348), (90, 372), (398, 368)]:
        t.append('<g transform="translate(%d,%d)">'
                 '<path d="M0,0 C-6,-14 -8,-20 -13,-26 M0,0 C0,-16 1,-22 0,-30 '
                 'M0,0 C6,-14 9,-20 13,-26" stroke="#4d8a49" stroke-width="3.5" '
                 'fill="none" stroke-linecap="round"/></g>' % (cx, cy))
    t.append('<circle cx="200" cy="388" r="3.2" fill="#f2a1b5"/>'
             '<circle cx="470" cy="392" r="3" fill="#f7e6c4"/>'
             '<circle cx="60" cy="440" r="2.8" fill="#f2a1b5"/>')
    t.append('</g>')
    d.append("".join(t))
    # road markings tile
    r = ['<g id="t-road">']
    for x0 in (8, 148, 288, 428):
        r.append('<rect x="%d" y="500" width="84" height="9" rx="4.5" fill="#efe5cf" '
                 'opacity=".95"/>' % x0)
    r.append('<ellipse cx="236" cy="470" rx="27" ry="8" fill="#525a66"/>')
    r.append('<path d="M340,530 l13,-6 l9,4" stroke="#4a505c" stroke-width="2" '
             'fill="none" stroke-linecap="round"/>')
    r.append('<circle cx="60" cy="538" r="1.8" fill="#7a8290"/>'
             '<circle cx="392" cy="466" r="1.6" fill="#7a8290"/>'
             '<circle cx="508" cy="534" r="1.8" fill="#7a8290"/>')
    r.append('</g>')
    d.append("".join(r))
    # foreground tile
    f = ['<g id="t-fg">']
    f.append('<g transform="translate(30,640)">'
             '<path d="M0,0 C-6,-26 -8,-36 -15,-46 M0,0 C0,-30 1,-38 0,-54 '
             'M0,0 C6,-26 10,-36 16,-45" stroke="#3f7a3a" stroke-width="4.5" '
             'fill="none" stroke-linecap="round"/></g>')
    f.append('<g transform="translate(148,640)">'
             '<path d="M0,0 C-5,-16 -7,-23 -13,-30 M0,0 C0,-19 0,-24 0,-34 '
             'M0,0 C5,-16 8,-23 13,-29" stroke="#38703a" stroke-width="4" '
             'fill="none" stroke-linecap="round"/></g>')
    f.append('<line x1="88" y1="616" x2="88" y2="626" stroke="#3f7a3a" stroke-width="2"/>'
             '<circle cx="88" cy="612" r="4.5" fill="#f2a1b5"/>'
             '<circle cx="88" cy="612" r="1.8" fill="#f7e6c4"/>')
    f.append('</g>')
    d.append("".join(f))
    d.append('</defs>')
    return "".join(d)


def scroll_layer(tile_id, unit, tiles, dur, shift):
    a = ax('translate', dur, [(0.0, 0.0), (-shift, 0.0)])
    attr, child = TF(None, a)
    uses = "".join('<use xlink:href="#%s" href="#%s" x="%d"/>'
                   % (tile_id, tile_id, i * unit) for i in range(tiles))
    return '<g%s>%s%s</g>' % (TATTR(attr), child, uses)


def build_background(subset):
    s = []
    s.append('<rect x="0" y="0" width="%d" height="%d" fill="url(#gSky)"/>' % (VBW, VBH))
    rays = []
    for k in range(12):
        ang = math.radians(k * 30.0)
        c, si = math.cos(ang), math.sin(ang)
        rays.append('<line x1="%s" y1="%s" x2="%s" y2="%s" stroke="#ffd75e" '
                    'stroke-width="5" stroke-linecap="round" opacity=".8"/>'
                    % (f2(818 + 44 * c), f2(86 + 44 * si),
                       f2(818 + 58 * c), f2(86 + 58 * si)))
    s.append('<circle cx="818" cy="86" r="52" fill="#ffe9a8" opacity=".45"/>')
    s.append("".join(rays))
    s.append('<circle cx="818" cy="86" r="33" fill="url(#gSun)" stroke="#f2b53c" '
             'stroke-width="2.5"/>')
    if subset == 'mech':
        return "".join(s)
    # parallax bands — each loops on its own period, shift % unit == 0
    s.append(scroll_layer('t-clfar', 420, 4, 6.0, 420))
    s.append(scroll_layer('t-clnear', 560, 3, 4.8, 560))
    s.append(scroll_layer('t-mtn', 560, 3, 9.6, 560))
    s.append(scroll_layer('t-hill', 560, 3, 4.8, 560))
    s.append('<rect x="0" y="308" width="%d" height="129" fill="#93c26f"/>' % VBW)
    s.append(scroll_layer('t-tree', 560, 3, 2.4, 560))
    s.append('<rect x="0" y="437" width="%d" height="18" fill="#7bb05f"/>' % VBW)
    s.append('<rect x="0" y="455" width="%d" height="100" fill="url(#gRoad)"/>' % VBW)
    s.append('<rect x="0" y="459" width="%d" height="4" fill="#e8dfc8" opacity=".85"/>'
             % VBW)
    s.append('<rect x="0" y="547" width="%d" height="4" fill="#e8dfc8" opacity=".85"/>'
             % VBW)
    s.append(scroll_layer('t-road', 560, 4, T, ROAD_SHIFT))
    s.append('<rect x="0" y="555" width="%d" height="85" fill="#4f8c47"/>' % VBW)
    return "".join(s)


def build_foreground():
    return scroll_layer('t-fg', 200, 12, T, 1400.0)


# ----------------------------------------------------------------------------
# Bicycle
# ----------------------------------------------------------------------------

def build_wheel(cx, cy, phase=0.0):
    """Complete wheel at (cx,cy); spokes rotate at constant speed.
    `phase` de-syncs the two wheels' marker spokes."""
    rot = ax('rotate', T, [phase, phase + WHEEL_DEG])
    attr, child = TF(None, rot)
    spokes = []
    for k in range(12):
        if k == 0:   # one darker marker spoke makes the rotation readable
            spokes.append('<line x1="0" y1="10" x2="0" y2="76" stroke="#8f897c" '
                          'stroke-width="3.4"/>')
        else:
            spokes.append('<line x1="0" y1="10" x2="0" y2="76" stroke="%s" '
                          'stroke-width="2.6" transform="rotate(%d)"/>'
                          % (SPOKE, k * 30))
    return ('<g transform="translate(%s,%s)">'
            '<circle r="89.13" fill="none" stroke="%s" stroke-width="12"/>'
            '<circle r="79" fill="none" stroke="%s" stroke-width="6.5"/>'
            '<circle r="83.5" fill="none" stroke="#565d68" stroke-width="1.6" opacity=".6"/>'
            '<g%s>%s%s<rect x="-1.6" y="-78" width="3.2" height="8" rx="1.5" '
            'fill="#8f897c"/></g>'
            '<circle r="8.5" fill="%s" stroke="#8f887a" stroke-width="2"/>'
            '</g>') % (f2(cx), f2(cy), TIRE, RIM, TATTR(attr), child,
                       "".join(spokes), RIM)


def build_crank(far):
    rot = ax('rotate', T, [0.0, 360.0])
    attr, child = TF(None, rot)
    counter = ax('rotate', T, [0.0, -360.0], additive=True)
    sx = -1.0 if far else 1.0
    pattr, pchild = TF('translate(%s,0)' % f2(CRANK * sx), counter)
    arm_col = '#3d444f' if far else '#4a5260'
    pedal_col = '#2c333e' if far else '#37404e'
    return ('<g%s>%s'
            '<line x1="0" y1="0" x2="%s" y2="0" stroke="%s" stroke-width="8" '
            'stroke-linecap="round"/>'
            '<g transform="%s">%s'
            '<rect x="-17" y="10" width="36" height="8" rx="3" fill="%s" '
            'stroke="#232933" stroke-width="1.5"/>'
            '<rect x="-13" y="12" width="6" height="4" rx="1" fill="#f2c14e"/>'
            '<circle r="4.2" fill="#9aa1ad"/>'
            '</g></g>') % (TATTR(attr), child, f2(CRANK * sx), arm_col,
                           pattr, pchild, pedal_col)


def build_frame():
    s = []
    tube = lambda d, w: ('<path d="%s" fill="none" stroke="%s" stroke-width="%s" '
                         'stroke-linecap="round"/>' % (d, FRAME, w))

    def fender(cx, cy, a0, a1):
        r = 96.0
        p0 = (cx + r * math.cos(math.radians(a0)), cy + r * math.sin(math.radians(a0)))
        p1 = (cx + r * math.cos(math.radians(a1)), cy + r * math.sin(math.radians(a1)))
        dd = 'M%s,%s A%s,%s 0 1 1 %s,%s' % (f2(p0[0]), f2(p0[1]), f2(r), f2(r),
                                            f2(p1[0]), f2(p1[1]))
        return ('<path d="%s" fill="none" stroke="#1f8f84" stroke-width="10" '
                'stroke-linecap="round"/>'
                '<path d="%s" fill="none" stroke="#bfe8dd" stroke-width="3" '
                'stroke-linecap="round" opacity=".9"/>' % (dd, dd))

    s.append(fender(AXR[0], AXR[1], 170, 10))
    s.append(fender(AXF[0], AXF[1], 170, 10))
    s.append(tube('M0,0 L126,-128', 10))
    s.append(tube('M-46,-148 L114,-160', 9))
    s.append(tube('M0,0 L-46,-148', 10))
    s.append(tube('M-118,-21 L-46,-148', 7))
    s.append(tube('M0,2 L-118,-21', 7))
    s.append('<path d="M126,-128 C138,-90 144,-55 148,-21" fill="none" stroke="%s" '
             'stroke-width="9" stroke-linecap="round"/>' % FRAME)
    s.append('<line x1="114" y1="-160" x2="126" y2="-128" stroke="%s" stroke-width="12" '
             'stroke-linecap="round"/>' % FRAME)
    s.append('<line x1="116" y1="-158" x2="108" y2="-186" stroke="%s" stroke-width="7" '
             'stroke-linecap="round"/>' % FRAME)
    s.append('<path d="M108,-186 C102,-197 92,-199 82,-197 C70,-195 66,-190 67,-183" '
             'fill="none" stroke="#3c4450" stroke-width="6.5" stroke-linecap="round"/>')
    s.append('<circle cx="67.5" cy="-182" r="5.6" fill="#8a5a33"/>')
    s.append('<circle cx="96" cy="-196" r="4.6" fill="#f2c14e" stroke="#c99a2e" '
             'stroke-width="1.6"/>')
    s.append('<circle cx="133" cy="-138" r="5.4" fill="#f2c14e" stroke="#c99a2e" '
             'stroke-width="1.8"/>')
    s.append('<line x1="-46" y1="-148" x2="-50" y2="-158" stroke="#9aa1ad" '
             'stroke-width="6" stroke-linecap="round"/>')
    s.append('<path d="M-84,-163 Q-88,-170 -78,-171 L-34,-171 Q-14,-171 -10,-163 '
             'Q-14,-157 -34,-156 L-76,-156 Q-84,-156 -84,-163 Z" fill="#b5893d" '
             'stroke="#8a642a" stroke-width="2"/>')
    # drivetrain
    s.append('<circle cx="%s" cy="%s" r="11" fill="#8b857a" stroke="#5f5a50" '
             'stroke-width="2"/>' % (f2(AXR[0]), f2(AXR[1])))
    chain = ax('attr', T, [CHAIN_TRAVEL, 0.0], name='stroke-dashoffset')
    for d in ('M%s,%s L0,-26' % (f2(AXR[0]), f2(AXR[1] - 11)),
              'M0,26 L%s,%s' % (f2(AXR[0]), f2(AXR[1] + 11))):
        oat, ochild = AT(0, chain)
        s.append('<path d="%s" fill="none" stroke="#454b57" stroke-width="4.5" '
                 'stroke-dasharray="9 6.708" stroke-dashoffset="%s">%s</path>'
                 % (d, oat, ochild))
    s.append('<circle r="27.5" fill="none" stroke="#6d675a" stroke-width="3" '
             'stroke-dasharray="2.5 2.5"/>')
    s.append('<circle r="25" fill="#cfc8b4" stroke="#6d675a" stroke-width="3"/>')
    for k in range(5):
        a = math.radians(90 + k * 72)
        s.append('<circle cx="%s" cy="%s" r="3.6" fill="#8b857a"/>'
                 % (f2(16 * math.cos(a)), f2(16 * math.sin(a))))
    return "".join(s)


def build_flag():
    pole = ('<line x1="-118" y1="-25" x2="-198" y2="-172" stroke="#7a6a55" '
            'stroke-width="3" stroke-linecap="round"/>'
            '<circle cx="-118" cy="-25" r="3.4" fill="#7a6a55"/>')
    p0 = [(-196, -178), (-178, -184), (-160, -182), (-150, -174),
          (-160, -168), (-178, -164), (-196, -166)]
    p1 = [(-196, -178), (-178, -186), (-162, -186), (-148, -180),
          (-158, -170), (-178, -163), (-196, -166)]
    p2 = [(-196, -178), (-178, -180), (-158, -176), (-148, -168),
          (-162, -170), (-178, -166), (-196, -166)]
    fa = FlagAx('flagd', 0.6, [p0, p1, p2, p0])
    REG.append(fa)
    if BAKE is None:
        d_attr, child = flag_d(p0), smil_child(fa)
        flag = ('<path d="%s" fill="%s" stroke="%s" stroke-width="2" '
                'stroke-linejoin="round">%s</path>'
                % (d_attr, HELMET, HELMET_LN, child))
    else:
        flag = ('<path d="%s" fill="%s" stroke="%s" stroke-width="2" '
                'stroke-linejoin="round"/>' % (flag_d(fa.val(BAKE)), HELMET, HELMET_LN))
    return pole + flag


# ----------------------------------------------------------------------------
# Pelican
# ----------------------------------------------------------------------------

def build_leg(hip, lists, far):
    """Two-segment IK leg: hip -> thigh -> shank -> webbed foot."""
    A1, A2, AF = lists
    a1 = ax('rotate', T, A1, additive=True)
    a2 = ax('rotate', T, A2, additive=True)
    aF = ax('rotate', T, AF, additive=True)
    bobA = ax('translate', T, [(0.0, b) for b in BOB_LIST])
    body_c = FAR['body'] if far else BODY
    line_c = FAR['line'] if far else BODY_LN
    leg_c = FAR['leg'] if far else LEG_OR
    leg_l = FAR['legln'] if far else LEG_OR_LN
    b_attr, b_child = TF(None, bobA)
    t_attr, t_child = TF('translate(%s,%s)' % (f2(hip[0]), f2(hip[1])), a1)
    s_attr, s_child = TF('translate(0,%s)' % f2(L1), a2)
    f_attr, f_child = TF('translate(0,%s)' % f2(L2), aF)
    thigh = ('<line x1="0" y1="-6" x2="0" y2="%s" stroke="%s" stroke-width="26" '
             'stroke-linecap="round"/>' % (f2(L1 - 6), body_c))
    if not far:
        thigh += ('<path d="M-9,72 Q0,66 9,72 M-8,86 Q0,80 8,86 M-7,99 Q0,94 7,99" '
                  'fill="none" stroke="#e0d6c0" stroke-width="2.5" '
                  'stroke-linecap="round"/>')
    shank = ('<circle cx="0" cy="0" r="12.5" fill="%s" stroke="%s" stroke-width="2"/>'
             '<circle cx="-1.5" cy="-4" r="8.5" fill="%s"/>'
             '<line x1="0" y1="0" x2="0" y2="%s" stroke="%s" stroke-width="9.5" '
             'stroke-linecap="round"/>'
             % (body_c, line_c, BODY if not far else FAR['body'], f2(L2), leg_c))
    foot = ('<path d="M-10,-1 C-16,3 -15,10 -6,12 C2,14 10,14 18,12 C28,12 36,8 40,2 '
            'C37,-2 30,-4 24,-3 L4,-6 C-2,-6 -7,-5 -10,-1 Z" fill="%s" stroke="%s" '
            'stroke-width="2" stroke-linejoin="round"/>'
            '<path d="M20,12 L17,5 M30,10 L26,3 M2,11 Q12,4 26,2" stroke="%s" '
            'stroke-width="2" fill="none" stroke-linecap="round"/>'
            % (leg_c, leg_l, leg_l))
    return ('<g%s>%s'                                        # bob root
            '<g transform="%s">%s%s'                        # hip / thigh
            '<g transform="%s">%s%s'                        # knee / shank
            '<g transform="%s">%s%s</g>'                    # ankle / foot
            '</g></g></g>'
            % (TATTR(b_attr), b_child,
               t_attr, t_child, thigh,
               s_attr, s_child, shank,
               f_attr, f_child, foot))


def build_body():
    bobA = ax('translate', T, [(0.0, b) for b in BOB_LIST])
    b_attr, b_child = TF(None, bobA)
    # tail feathers (flutter about their root)
    tailA = ax('rotate', 0.6, [0.0, 2.6, 0.0, -2.2, 0.0],
               keyTimes=[0, .25, .5, .75, 1])
    t_attr, t_child = TF(None, tailA)
    tail = PIVOT(-78, -220, t_attr, t_child,
                 '<path d="M-78,-224 L-142,-244 L-118,-226 Z" fill="%s" stroke="%s" '
                 'stroke-width="2"/>'
                 '<path d="M-78,-220 L-148,-226 L-116,-213 Z" fill="%s" stroke="%s" '
                 'stroke-width="2"/>'
                 '<path d="M-78,-216 L-140,-199 L-114,-206 Z" fill="%s" stroke="%s" '
                 'stroke-width="2"/>'
                 % (BODY, BODY_LN, WING, BODY_LN, '#eee4d0', BODY_LN))
    # plump body
    body = ('<path d="M-86,-214 C-88,-248 -64,-266 -34,-268 C-6,-270 22,-258 32,-238 '
            'C40,-224 42,-208 34,-196 C22,-178 -6,-166 -38,-164 C-70,-162 -84,-186 '
            '-86,-214 Z" fill="%s" stroke="%s" stroke-width="2.5"/>' % (BODY, BODY_LN))
    body += ('<path d="M-70,-206 Q-74,-186 -54,-172" fill="none" stroke="%s" '
             'stroke-width="5" stroke-linecap="round" opacity=".8"/>' % BODY_SH)
    body += ('<path d="M-30,-172 Q-4,-166 18,-184" fill="none" stroke="%s" '
             'stroke-width="4" stroke-linecap="round" opacity=".7"/>' % BODY_SH)
    # neck
    neck_d = 'M6,-250 C30,-262 44,-282 50,-304 C54,-320 62,-332 74,-338'
    neck = ('<path d="%s" fill="none" stroke="%s" stroke-width="24" '
            'stroke-linecap="round"/>' % (neck_d, BODY_SH))
    neck += ('<path d="%s" fill="none" stroke="%s" stroke-width="21" '
             'stroke-linecap="round"/>' % (neck_d, BODY))
    # ---- head group ----
    headA = ax('rotate', T, [0.0, 1.3, 0.0, -1.0, 0.0], keyTimes=[0, .25, .5, .75, 1])
    h_attr, h_child = TF(None, headA)
    h = []
    h.append('<circle cx="76" cy="-332" r="25" fill="%s" stroke="%s" '
             'stroke-width="2.5"/>' % (BODY, BODY_LN))
    h.append('<path d="M46,-346 Q40,-356 46,-362 M54,-344 Q52,-354 58,-358" '
             'stroke="%s" stroke-width="3" fill="none" stroke-linecap="round"/>'
             % BODY_LN)
    # gular pouch (wobbling scale about the beak base)
    pouchA = ax('scale', 0.6, [(1, 1), (1, 1.045), (1, 1.06), (1, 1.045), (1, 1)])
    p_attr, p_child = TF(None, pouchA)
    h.append(PIVOT(100, -320, p_attr, p_child,
                   '<path d="M99,-322 C102,-290 116,-264 148,-256 C186,-247 222,-268 '
                   '241,-297 C243,-301 240,-304 236,-301 C218,-276 188,-262 156,-268 '
                   'C126,-274 110,-294 103,-317 C101,-320 100,-320 99,-322 Z" '
                   'fill="%s" stroke="%s" stroke-width="2.5"/>'
                   '<path d="M116,-290 Q140,-262 172,-258 M112,-300 Q130,-278 150,-270" '
                   'stroke="#f4a06f" stroke-width="2" fill="none" '
                   'stroke-linecap="round"/>' % (POUCH, POUCH_LN)))
    # open mouth gap
    h.append('<path d="M97,-327 L247,-303.5 L244,-297.5 L99,-319 Z" fill="#7d3b2b"/>')
    # fish tail poking out of the beak
    fishA = ax('rotate', 0.6, [0.0, 8.0, 0.0, -6.0, 0.0], keyTimes=[0, .3, .55, .8, 1])
    f_attr, f_child = TF(None, fishA)
    h.append(PIVOT(150, -315, f_attr, f_child,
                   '<path d="M150,-313 C145,-318 146,-325 140,-331 L147,-328 L145,-339 '
                   'L152,-330 L155,-335 C156,-326 155,-319 152,-313 Z" fill="#5cb6c9" '
                   'stroke="#3e8ba0" stroke-width="1.8" stroke-linejoin="round"/>'))
    # huge upper mandible
    h.append('<path d="M94,-344 C148,-334 202,-320 250,-311 C253,-309 252,-304 '
             '248,-303.5 C200,-312 146,-324 92,-328 C90,-334 91,-340 94,-344 Z" '
             'fill="%s" stroke="%s" stroke-width="2.2" stroke-linejoin="round"/>'
             % (BEAK, BEAK_LN))
    h.append('<line x1="104" y1="-337" x2="122" y2="-334" stroke="%s" '
             'stroke-width="2.2" stroke-linecap="round"/>' % BEAK_LN)
    # derpy eye
    pupilA = ax('translate', T, [(0, 0), (0, 0), (2, 1.2), (2, 1.2), (0, 0), (0, 0)],
                keyTimes=[0, .5, .58, .8, .88, 1])
    pu_attr, pu_child = TF(None, pupilA)
    blinkA = ax('attr', 4.8, [0, 0, 9.2, 9.2, 0, 0],
                keyTimes=[0, .9, .93, .95, .98, 1], name='r')
    bl_attr, bl_child = AT(0, blinkA)
    h.append('<circle cx="87" cy="-331" r="9" fill="#ffffff" stroke="%s" '
             'stroke-width="1.6"/>' % BODY_LN)
    h.append('<circle cx="89" cy="-332" r="4.1" fill="#23201d"%s>%s</circle>'
             % (TATTR(pu_attr), pu_child))
    h.append('<circle cx="90.6" cy="-333.6" r="1.4" fill="#ffffff"/>')
    h.append('<circle cx="87" cy="-331" r="%s" fill="%s">%s</circle>'
             % (bl_attr, BODY, bl_child))
    h.append('<circle cx="64" cy="-322" r="4.6" fill="#f6b7a0" opacity=".6"/>')
    # helmet
    h.append('<path d="M50,-338 A26 26 0 1 1 102,-338 Z" fill="%s" stroke="%s" '
             'stroke-width="2.2"/>' % (HELMET, HELMET_LN))
    h.append('<path d="M100,-340 Q116,-338 112,-330 L100,-334 Z" fill="%s"/>'
             % HELMET_LN)
    h.append('<path d="M62,-354 Q66,-358 70,-359 M78,-360 Q84,-359 88,-354" '
             'stroke="#f7e6c4" stroke-width="3" fill="none" stroke-linecap="round"/>')
    h.append('<circle cx="57" cy="-340" r="2.2" fill="#f7e6c4"/>'
             '<circle cx="95" cy="-340" r="2.2" fill="#f7e6c4"/>')
    head = PIVOT(52, -306, h_attr, h_child, "".join(h))
    return '<g%s>%s%s%s%s%s</g>' % (TATTR(b_attr), b_child, tail, body, neck, head)


def build_wing():
    bobA = ax('translate', T, [(0.0, b) for b in BOB_LIST])
    b_attr, b_child = TF(None, bobA)
    fluff = ('<ellipse cx="-16" cy="-166" rx="21" ry="16" fill="%s"/>'
             '<path d="M-30,-160 Q-16,-152 0,-158" fill="none" stroke="%s" '
             'stroke-width="3" opacity=".7"/>' % (BODY, BODY_SH))
    wing = ('<path d="M-16,-248 C18,-250 46,-240 62,-220 C72,-207 74,-196 68,-188 '
            'C63,-182 55,-182 49,-188 C38,-200 18,-214 -8,-222 C-18,-226 -22,-240 '
            '-16,-248 Z" fill="%s" stroke="%s" stroke-width="2.5"/>' % (WING, BODY_LN))
    wing += ('<path d="M2,-234 Q28,-226 46,-206 M-6,-224 Q18,-216 34,-200 M-12,-214 '
             'Q6,-208 20,-196" fill="none" stroke="%s" stroke-width="2" '
             'stroke-linecap="round"/>' % BODY_LN)
    wing += ('<circle cx="64" cy="-187" r="8.6" fill="%s" stroke="%s" stroke-width="2"/>'
             '<path d="M58,-193 Q64,-186 61,-178 M66,-194 Q71,-187 68,-179" fill="none" '
             'stroke="%s" stroke-width="2" stroke-linecap="round"/>'
             % (WING, BODY_LN, BODY_LN))
    return '<g%s>%s%s%s</g>' % (TATTR(b_attr), b_child, fluff, wing)


def build_bike():
    s = ['<g transform="translate(%s,%s)">' % (f2(BBX), f2(BBY))]
    s.append(build_crank(True))                       # far crank + pedal
    s.append(build_leg(HIP_F, LEG_F, far=True))       # far leg (IK)
    s.append(build_wheel(AXR[0], AXR[1], 0.0))
    s.append(build_wheel(AXF[0], AXF[1], 180.0))
    s.append(build_flag())
    s.append(build_frame())
    s.append(build_body())
    s.append(build_crank(False))                      # near crank + pedal
    s.append(build_leg(HIP_N, LEG_N, far=False))      # near leg (IK)
    s.append(build_wing())
    s.append('<circle r="5.5" fill="#2f3540"/>')      # BB bolt
    s.append('</g>')
    return "".join(s)


def build_shadow():
    return ('<ellipse cx="465" cy="508" rx="250" ry="13" fill="#1c2833" opacity=".10"/>'
            '<ellipse cx="332" cy="506" rx="66" ry="9" fill="#1c2833" opacity=".13"/>'
            '<ellipse cx="598" cy="506" rx="66" ry="9" fill="#1c2833" opacity=".13"/>')


def build_streaks():
    out = []
    cfg = [((40, 150), (150, 150), 5.0, 0.0),
           ((20, 252), (120, 252), 4.0, -0.2),
           ((84, 318), (194, 318), 4.5, -0.4),
           ((128, 212), (258, 212), 5.0, -0.1)]
    for (x1, y1), (x2, y2), w, beg in cfg:
        mv = ax('translate', 0.6, [(0, 0), (-260, 0)], begin=beg)
        op = ax('attr', 0.6, [0.0, 0.72, 0.0], keyTimes=[0, .45, 1],
                begin=beg, name='opacity')
        m_attr, m_child = TF(None, mv)
        o_attr, o_child = AT(0, op)
        out.append('<g%s>%s<line x1="%s" y1="%s" x2="%s" y2="%s" stroke="#ffffff" '
                   'stroke-width="%s" stroke-linecap="round" opacity="%s">%s</line></g>'
                   % (TATTR(m_attr), m_child, x1, y1, x2, y2, w, o_attr, o_child))
    return "".join(out)


# ----------------------------------------------------------------------------
# SVG document
# ----------------------------------------------------------------------------

def build_svg(bake=None, subset='all', vb=None):
    global BAKE, REG
    BAKE = bake
    REG = []
    vb = vb or (0, 0, VBW, VBH)
    parts = ['<svg id="scene" xmlns="http://www.w3.org/2000/svg" '
             'xmlns:xlink="http://www.w3.org/1999/xlink" '
             'viewBox="%d %d %d %d" role="img" '
             'aria-label="一只戴着头盔的呆萌鹈鹕骑着自行车，车轮匀速滚动，'
             '腿部随踏板联动，背景无缝滚动">'
             '><title>鹈鹕骑自行车</title>' % vb]
    parts.append(build_defs())
    parts.append(build_background(subset))
    if subset == 'all':
        parts.append(build_streaks())
    parts.append(build_shadow())
    parts.append(build_bike())
    if subset == 'all':
        parts.append(build_foreground())
    parts.append('</svg>')
    return "".join(parts)


# ----------------------------------------------------------------------------
# HTML wrapper
# ----------------------------------------------------------------------------

HTML_HEAD = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>🚲 鹈鹕骑自行车 · 纯 SVG/SMIL 动画</title>
<style>
  :root { color-scheme: light; }
  * { box-sizing: border-box; }
  body {
    margin: 0; min-height: 100vh;
    display: flex; align-items: center; justify-content: center;
    padding: 28px 16px;
    background: linear-gradient(180deg, #d9eef7 0%, #eef6ee 60%, #f7f4ea 100%);
    font-family: system-ui, -apple-system, "Segoe UI", "PingFang SC",
                 "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
    color: #22343e;
  }
  .wrap { width: min(1060px, 100%); }
  header h1 { margin: 0 0 6px; font-size: clamp(22px, 3.4vw, 32px); letter-spacing: .5px; }
  header p  { margin: 0 0 16px; color: #58707e; font-size: 14px; }
  .stage {
    background: #fff; border-radius: 18px; overflow: hidden;
    box-shadow: 0 14px 44px rgba(25, 60, 80, .18), 0 2px 8px rgba(25,60,80,.08);
  }
  .stage svg { display: block; width: 100%; height: auto; cursor: pointer; }
  .controls { display: flex; align-items: center; gap: 10px; margin-top: 16px; flex-wrap: wrap; }
  button {
    appearance: none; border: 0; cursor: pointer;
    padding: 9px 20px; border-radius: 999px;
    font-size: 15px; font-weight: 600; letter-spacing: .5px;
    color: #fff; background: #2a9d8f;
    box-shadow: 0 4px 14px rgba(42, 157, 143, .35);
    transition: transform .12s ease, background .12s ease, box-shadow .12s ease;
  }
  button:hover  { background: #238e81; transform: translateY(-1px); }
  button:active { transform: translateY(0); }
  button:focus-visible { outline: 3px solid #bfe8dd; outline-offset: 2px; }
  button.ghost { background: #fff; color: #2a6d84; box-shadow: inset 0 0 0 2px #bcd7de; }
  button.ghost:hover { background: #eef7f5; }
  .hint { color: #58707e; font-size: 13px; }
  kbd {
    background: #fff; border: 1px solid #c3d4da; border-bottom-width: 2px;
    border-radius: 6px; padding: 1px 7px; font-size: 12px; font-family: inherit;
    color: #2a6d84;
  }
  footer { margin-top: 14px; color: #7d919c; font-size: 12.5px; line-height: 1.7; }
  footer code { background: #eef4f3; padding: 0 5px; border-radius: 5px; color: #2a6d84; }
  .chips { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 10px; }
  .chip { font-size: 12px; padding: 3px 12px; border-radius: 999px;
          background: #e7f2ef; color: #2a6d84; }
</style>
</head>
<body>
<main class="wrap">
  <header>
    <h1>🚲 鹈鹕骑自行车</h1>
    <p>纯 SVG + 原生 SMIL 动画 · 车轮 / 踏板 / 地面速度严格同步 · 两段式逆向运动学腿部解算 · 无缝循环</p>
  </header>
  <figure class="stage" style="margin:0">
"""

HTML_TAIL = """  </figure>
  <div class="controls">
    <button id="btn-pause" aria-pressed="false">⏸ 暂停</button>
    <button id="btn-reset" class="ghost">↻ 重置</button>
    <span class="hint"><kbd>空格</kbd> 暂停 / 播放 · 点击画面同样有效</span>
  </div>
  <footer>
    车轮每 1.2 秒匀速转两圈，与路面、前景的滚动速度精确一致；曲柄带动双侧踏板水平自转，
    鹈鹕的大腿与小腿由两段式 IK（逆向运动学）解算，脚掌全程贴合脚踏；髋部随踩踏起伏，
    喉囊与尾羽同步颤动。所有动画均以 <code>repeatCount="indefinite"</code> 循环且首尾帧相同，任意时刻平滑闭环。
    <div class="chips">
      <span class="chip">单文件 · 零依赖</span>
      <span class="chip">SMIL + 少量原生 JS 控制</span>
      <span class="chip">视差背景无缝滚动</span>
      <span class="chip">空格 / 按钮 / 点击暂停</span>
    </div>
  </footer>
</main>
<script>
(function () {
  'use strict';
  var svg = document.getElementById('scene');
  var btn = document.getElementById('btn-pause');
  var rst = document.getElementById('btn-reset');
  var paused = false;
  function setPaused(p) {
    paused = p;
    if (p) { svg.pauseAnimations(); } else { svg.unpauseAnimations(); }
    btn.textContent = paused ? '▶ 播放' : '⏸ 暂停';
    btn.setAttribute('aria-pressed', paused ? 'true' : 'false');
  }
  btn.addEventListener('click', function (e) { e.stopPropagation(); setPaused(!paused); btn.blur(); });
  rst.addEventListener('click', function (e) { e.stopPropagation(); svg.setCurrentTime(0); rst.blur(); });
  document.addEventListener('keydown', function (e) {
    if (e.code === 'Space' || e.key === ' ') {
      e.preventDefault();
      if (!e.repeat) { setPaused(!paused); }
    }
  });
  svg.addEventListener('click', function () { setPaused(!paused); });
  if (window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches) {
    setPaused(true);
  }
})();
</script>
</body>
</html>
"""


def build_html():
    return HTML_HEAD + build_svg(bake=None) + HTML_TAIL


# ----------------------------------------------------------------------------
# Verification
# ----------------------------------------------------------------------------

def _mod_near(x, m, tol=1e-6):
    r = abs(x) % m
    return r < tol or (m - r) < tol


def _closes(a):
    v0, v1 = a.vals[0], a.vals[-1]
    if a.kind == 'rotate':
        return _mod_near(v1 - v0, 360.0)
    if a.kind == 'flagd':
        return list(v0) == list(v1)
    if a.kind == 'attr' and a.name == 'stroke-dashoffset':
        return _mod_near(v1 - v0, CHAIN_PERIOD, 1e-3)
    if a.kind == 'translate' and isinstance(v0, tuple) and v1[0] < 0:
        return True   # scroll layer — validated separately by tile math
    if isinstance(v0, tuple):
        return len(v0) == len(v1) and all(abs(x - y) < 1e-9 for x, y in zip(v0, v1))
    return abs(v0 - v1) < 1e-9


def verify():
    ok = True

    def check(cond, msg):
        nonlocal ok
        print(("  [PASS] " if cond else "  [FAIL] ") + msg)
        if not cond:
            ok = False

    print("== 1. IK numerics ==")
    for name, hip, far in (("near", HIP_N, False), ("far", HIP_F, True)):
        dmin, dmax = 1e9, -1e9
        for i in range(N + 1):
            hip_t = (hip[0], hip[1] + bob(T * i / N))
            ped = pedal_pos(360.0 * i / N, far)
            d = math.hypot(ped[0] - hip_t[0], ped[1] - hip_t[1])
            dmin, dmax = min(dmin, d), max(dmax, d)
        check((L1 + L2) - dmax > 0.5 and dmin - abs(L1 - L2) > 0.5,
              "%s leg reach: d in [%.1f, %.1f], L1+L2=%.0f, margins hi/lo %.1f/%.1f px"
              % (name, dmin, dmax, L1 + L2, (L1 + L2) - dmax, dmin - abs(L1 - L2)))
    for name, hip, far in (("near", HIP_N, False), ("far", HIP_F, True)):
        e = max_foot_error(hip, far)
        check(e < 0.6, "%s foot tracking error max %.3f px (< 0.6)" % (name, e))

    print("== 2. Loop closure & seams (math) ==")
    build_svg(bake=None)
    reg = list(REG)
    bad = [a for a in reg if not _closes(a)]
    check(not bad, "every animation loops seamlessly (%d anims, %d bad)"
          % (len(reg), len(bad)))
    # structural SMIL check: any animateTransform attached to a group that has a
    # base transform attribute MUST be additive="sum" (else it would replace it)
    import re
    svg_out = build_svg(bake=None)
    pairs = re.findall(r'<g transform="([^"]+)"><animateTransform([^>]*)/>', svg_out)
    nonadd = [p for p in pairs if 'additive="sum"' not in p[1]]
    check(len(pairs) == 8 and not nonadd,
          "%d base-transform+animateTransform pairs, all additive=sum (%d bad)"
          % (len(pairs), len(nonadd)))
    shifts = [(420, 420.0), (560, 560.0), (560, 560.0), (560, 560.0), (560, 560.0),
              (560, ROAD_SHIFT), (200, 1400.0)]
    check(all(s % u == 0 for u, s in shifts),
          "every scroll layer shifts an exact multiple of its tile width")
    check(abs((ROAD_SHIFT / T) - (WHEEL_DEG / 360.0) * (2 * math.pi * RW) / T) < 1e-6,
          "wheel surface speed == road scroll speed (%.1f px/s)" % (ROAD_SHIFT / T))
    check(abs((CHAIN_TRAVEL / T) - (2 * math.pi * 25.0) / T) < 0.5,
          "chain dash speed ~= chainring rim speed")

    print("== 3. Rendered checks ==")
    try:
        import resvg_py
        from PIL import Image
        import io
    except ImportError:
        print("  [SKIP] resvg/PIL not installed")
        return ok
    a = resvg_py.svg_to_bytes(build_svg(bake=0.0, subset='mech'))
    b = resvg_py.svg_to_bytes(build_svg(bake=T, subset='mech'))
    check(a == b, "mech subset: render(t=0) == render(t=T), byte-identical loop")
    full = build_svg(bake=0.0)
    try:
        png = resvg_py.svg_to_bytes(full)
        check(len(png) > 40000, "full scene renders OK (%d byte PNG)" % len(png))
    except Exception as e:
        check(False, "full scene renders: %s" % e)
        return ok

    print("== 4. Pixel probes ==")
    img = Image.open(io.BytesIO(png)).convert('RGB')
    check(img.size == (VBW, VBH), "render size %dx%d" % img.size)

    def hex2rgb(h):
        h = h.lstrip('#')
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))

    def probe_any(x, y, color, radius, label, tol=40):
        tgt = hex2rgb(color)
        found = False
        for dx in range(-radius, radius + 1):
            for dy in range(-radius, radius + 1):
                r, g, bb = img.getpixel((int(x + dx), int(y + dy)))
                if (abs(r - tgt[0]) <= tol and abs(g - tgt[1]) <= tol
                        and abs(bb - tgt[2]) <= tol):
                    found = True
                    break
            if found:
                break
        check(found, "probe %-20s @(%d,%d) expects %s" % (label, x, y, color))

    probe_any(60, 24, '#7fc4e8', 6, 'sky gradient')
    probe_any(480, 530, '#5c6470', 6, 'road asphalt')
    probe_any(100, 600, '#4f8c47', 6, 'foreground grass')
    probe_any(int(BBX - 30), int(BBY - 214), '#fdf8ef', 8, 'pelican body')
    probe_any(int(BBX + 76), int(BBY - 350), '#e8564f', 8, 'helmet')
    probe_any(int(BBX + 170), int(BBY - 270), '#ffb98a', 10, 'gular pouch')
    probe_any(int(BBX + 200), int(BBY - 318), '#f6a94f', 10, 'upper beak')
    probe_any(int(BBX - 30), int(BBY - 150), '#2a9d8f', 10, 'frame tube')
    probe_any(int(BBX - 60), int(BBY - 29), '#454b57', 7, 'chain')
    probe_any(int(BBX - 118), int(BBY - 110), '#2e3239', 6, 'rear tire top')
    # composition probes (positions computed from the design geometry)
    probe_any(526, 23, '#e8564f', 6, 'helmet top edge')
    probe_any(693, 73, '#f6a94f', 8, 'beak near tip')
    probe_any(270, 212, '#e8564f', 8, 'waving pennant')
    probe_any(597, 52, '#5cb6c9', 6, 'fish tail')
    probe_any(514, 200, '#f3ecdd', 6, 'wing mitt on grip')
    probe_any(398, 222, '#b5893d', 6, 'saddle')
    probe_any(337, 156, '#fdf8ef', 7, 'tail feather')
    probe_any(480, 249, '#fdf8ef', 8, 'near thigh @t0')
    probe_any(459, 261, '#e6ddca', 8, 'far thigh @t0')
    probe_any(264, 298, '#1f8f84', 7, 'rear fender')
    probe_any(587, 312, '#2a9d8f', 7, 'fork')
    probe_any(535, 190, '#3c4450', 6, 'handlebar')
    probe_any(517, 205, '#8a5a33', 5, 'grip')
    probe_any(57, 266, '#5d9e58', 8, 'tree canopy @t0')
    probe_any(262, 250, '#9db9c4', 6, 'mountain @t0')
    probe_any(130, 302, '#7fb069', 6, 'hill @t0')
    probe_any(50, 504, '#efe5cf', 6, 'center dash @t0')
    probe_any(87, 65, '#ffffff', 8, 'cloud @t0')
    probe_any(88, 612, '#f2a1b5', 5, 'fg flower @t0')

    foot_hits, pedal_hits = 0, 0
    for k in range(8):
        t = T * k / 8
        th = 360.0 * t / T
        im = Image.open(io.BytesIO(
            resvg_py.svg_to_bytes(build_svg(bake=t)))).convert('RGB')
        px = (BBX + CRANK * math.cos(math.radians(th)),
              BBY + CRANK * math.sin(math.radians(th)))
        ff = fp = False
        for dx in range(-16, 17, 2):
            for dy in range(-16, 17, 2):
                c = im.getpixel((int(px[0] + dx), int(px[1] + dy)))
                for col, hit in (('#f29b4b', 'f'), ('#37404e', 'p')):
                    tgt = hex2rgb(col)
                    if (abs(c[0] - tgt[0]) <= 34 and abs(c[1] - tgt[1]) <= 34
                            and abs(c[2] - tgt[2]) <= 34):
                        if hit == 'f':
                            ff = True
                        else:
                            fp = True
        foot_hits += ff
        pedal_hits += fp
    check(foot_hits == 8, "near foot covers pedal spindle at 8/8 crank phases")
    check(pedal_hits == 8, "pedal/crank visible at spindle at 8/8 crank phases")

    print("== result: %s ==" % ("ALL PASS" if ok else "FAILURES PRESENT"))
    return ok


def main():
    if '--check' in sys.argv:
        sys.exit(0 if verify() else 1)
    out = 'index.html'
    if len(sys.argv) > 1 and not sys.argv[1].startswith('-'):
        out = sys.argv[1]
    html = build_html()
    with open(out, 'w', encoding='utf-8') as f:
        f.write(html)
    print("wrote %s (%.1f KB)" % (out, len(html.encode()) / 1024.0))
    sys.exit(0 if verify() else 1)


if __name__ == '__main__':
    main()
