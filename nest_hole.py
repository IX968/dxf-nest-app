"""航模 DXF 排版 —— 镂空：认出零件上哪些孔能排进别的零件，以及往孔里排（在子进程里跑）。

spyrrow 不支持带孔零件，所以分两步：先在这里把小零件塞进大零件的孔里，
再把“大零件 + 孔里的零件”当成一个整体交给排版求解（外形还是大零件的外轮廓）。"""
import math, time, traceback
from collections import Counter
import numpy as np
import shapely
from shapely.geometry import Polygon
from shapely import affinity
from shapely.prepared import prep
import nest_post as P

ON_RING = 0.12                               # 线到孔轮廓的距离小于此值，算组成这圈孔的线 mm
ROT_FREE = [15.0 * k for k in range(24)]     # “任意角度”时往孔里排用的角度


# ---------------------------------------------------------------- 孔的分类
def part_holes(part):
    """零件的每个内孔，顺序同 part.poly.interiors。每项：
    ring 孔的多边形（零件局部坐标）；key 记住用户选择用；cut 轮廓会切穿；mark 是刻线框（不切穿）；
    busy 孔里有东西（小孔、刻线、文字、小件）—— 这块料可能是要用的零件，不往里排。"""
    if getattr(part, '_holes', None) is not None: return part._holes
    prims = P.part_prims(part); lens = part._lens
    pts = []
    for p in prims:
        q = P.prim_points(part, p)
        pts.append(np.asarray(q[::max(1, len(q) // 24)] + q[-1:], float) if q else None)
    out = []
    for r in part.poly.interiors:
        g = Polygon(r); band = r.buffer(ON_RING); inner = g.buffer(-0.1)
        shapely.prepare(band)
        if not inner.is_empty: shapely.prepare(inner)
        x0, y0, x1, y1 = g.bounds
        on = cut = 0.0; busy = False
        for p, q, n in zip(prims, pts, lens):
            if q is None or q[:, 0].max() < x0 - 1 or q[:, 0].min() > x1 + 1 or q[:, 1].max() < y0 - 1 or q[:, 1].min() > y1 + 1:
                continue
            if shapely.contains_xy(band, q[:, 0], q[:, 1]).all():          # 这条线是孔轮廓的一部分
                on += n; cut += n if p[7] else 0.0
            elif not inner.is_empty and shapely.contains_xy(inner, q[:, 0], q[:, 1]).any(): busy = True
        busy = busy or any(g.intersects(i) for i in part.islands)
        whole = on >= 0.5 * r.length                       # 找到了组成这圈孔的线
        c = g.centroid
        out.append(dict(ring=g, key=(round(c.x, 1), round(c.y, 1), round(g.area)),
                        cut=whole and cut >= 0.5 * on, mark=whole and cut < 0.5 * on, busy=busy))
    part._holes = out
    return out


def container(ring, gap):
    """孔往里缩 gap/2 + 0.05。零件用外扩 gap/2 + 0.05 的形状（nest_core.item_shape）放进去，
    这样离孔边、零件之间都至少一个零件间距，和板上排版的规则一样。"""
    c = ring.buffer(-(gap / 2 + 0.05))
    return None if c.is_empty else c


def dims(g):
    """最小外接矩形的 (短边, 长边)。"""
    c = list(g.minimum_rotated_rectangle.exterior.coords)
    return tuple(sorted((math.dist(c[0], c[1]), math.dist(c[1], c[2]))))


def fits_some(ring, shapes, gap):
    """粗判孔里放不放得下某个零件。shapes：[(外扩外形, dims)]。只看面积和最小外接矩形，真正能不能放以排的结果为准。"""
    c = container(ring, gap)
    if c is None: return False
    cw, ch = dims(c); A = c.area
    return any(s.area <= A and d[0] <= cw + 1e-6 and d[1] <= ch + 1e-6 for s, d in shapes)


# ---------------------------------------------------------------- 往孔里排
def _grid(x0, y0, nx, ny, g):
    return np.meshgrid(x0 + (np.arange(nx) + 0.5) * g, y0 + (np.arange(ny) + 0.5) * g)


def _mask(shape, r, g, S):
    """零件转 r 度后的栅格：格子碰到零件就算占用（偏保守）。返回 (形状, 栅格原点 x, y, 栅格)；比孔大返回 None。"""
    s = affinity.rotate(shape, r, origin=(0, 0)) if r else shape
    a, b, c, d = s.bounds
    ox, oy = a - g, b - g                                   # 四周留一格
    mw, mh = int(math.ceil((c - ox) / g)) + 1, int(math.ceil((d - oy) / g)) + 1
    if mh > S[0] or mw > S[1]: return None
    X, Y = _grid(ox, oy, mw, mh, g)
    sb = s.buffer(0.75 * g, quad_segs=4); shapely.prepare(sb)     # 0.75 格 > 半条对角线：碰到格子的都算上
    return s, ox, oy, shapely.contains_xy(sb, X, Y)


def _fill_one(cont, shp, order, rem, rots, budget):
    """往一个孔（往里缩好的容器 cont）里排零件：大的优先，每件取顶边最低、再靠左的位置。
    用 FFT 一次算出所有不重叠的位置，再用 shapely 精确复核。
    返回 [(pid, 角度, x, y)]：零件绕原点转这个角度后平移 (x, y)，坐标是大零件的局部坐标。"""
    t_end = time.time() + budget
    x0, y0, x1, y1 = cont.bounds
    g = max(0.5, max(x1 - x0, y1 - y0) / 400)               # 栅格边长 mm：孔大就放粗，控制计算量
    nx, ny = int(math.ceil((x1 - x0) / g)) + 1, int(math.ceil((y1 - y0) / g)) + 1
    X, Y = _grid(x0, y0, nx, ny, g)
    inner = cont.buffer(-0.75 * g)                          # 整格都在孔里才算空格（偏保守）
    if inner.is_empty: return []
    shapely.prepare(inner)
    occ = ~shapely.contains_xy(inner, X, Y)
    if occ.all(): return []
    S = occ.shape; exact = prep(cont)
    F = np.fft.rfft2(occ.astype(float))
    dead, masks, placed, out, used, fails = set(), {}, [], [], Counter(), 0
    while time.time() < t_end:
        free_area = (~occ).sum() * g * g; best = None
        for i in order:
            if rem.get(i, 0) - used[i] <= 0 or shp[i].area > free_area: continue
            for r in rots:
                if (i, r) in dead: continue
                if (i, r) not in masks: masks[(i, r)] = _mask(shp[i], r, g, S)
                if masks[(i, r)] is None: dead.add((i, r)); continue
                M = masks[(i, r)][3]; mh, mw = M.shape
                Mp = np.zeros(S); Mp[:mh, :mw] = M
                hit = np.fft.irfft2(F * np.conj(np.fft.rfft2(Mp)), S)[:S[0] - mh + 1, :S[1] - mw + 1] > 0.5
                js, iss = np.nonzero(~hit)
                if not len(js): dead.add((i, r)); continue      # 孔只会越排越满：现在放不下，以后也放不下
                k = np.lexsort((iss, js + mh))[0]
                c = (int(js[k] + mh), int(iss[k]), i, r, int(js[k]))
                if best is None or c[:2] < best[:2]: best = c
            if best is not None: break                          # 大的放得下就放大的
        if best is None: break
        _, ii, i, r, j = best
        s, ox, oy, M = masks[(i, r)]
        dx, dy = x0 + ii * g - ox, y0 + j * g - oy
        Pg = affinity.translate(s, dx, dy)
        if exact.contains(Pg) and not any(Pg.intersects(q) for q in placed):
            placed.append(Pg); out.append((i, float(r), float(dx), float(dy))); used[i] += 1
            b = Pg.buffer(0.75 * g, quad_segs=4); shapely.prepare(b)
            occ |= shapely.contains_xy(b, X, Y)
        else:                                                   # 栅格和精确判断不一致（很少见）：堵住这个位置再找
            u, v = np.argwhere(M)[0]; occ[j + u, ii + v] = True; fails += 1
            if fails > 20: break
        F = np.fft.rfft2(occ.astype(float))
    return out


def fill_holes(job, q=None):
    """job：hosts=[{pid, qty, holes: [孔外圈坐标, ...]}]（大零件局部坐标），shapes={pid: 外扩外形坐标}，
    qty={pid: 数量}，gap，rot（角度列表，None = 任意角度）。
    孔从大到小排；有“可排入”孔的零件本身不排进别的孔（只嵌套一层）。
    返回 [(大零件 pid, ((pid, 角度, x, y), ...)), ...]，每项是一个装了零件的大零件副本。"""
    rots = ROT_FREE if job['rot'] is None else (list(job['rot']) or [0.0])
    hosts = {h['pid'] for h in job['hosts']}
    shp = {i: Polygon(s) for i, s in job['shapes'].items() if i not in hosts}
    rem = {i: c for i, c in job['qty'].items() if i in shp and c > 0}
    order = sorted(rem, key=lambda i: -shp[i].area)
    slots = []
    for h in job['hosts']:
        conts = [container(Polygon(r), job['gap']) for r in h['holes']]
        for n in range(h['qty']):
            slots += [(c.area, h['pid'], n, k, c) for k, c in enumerate(conts) if c is not None]
    slots.sort(key=lambda t: -t[0])
    got, memo, total = {}, {}, 0
    t_end = time.time() + job.get('budget', 60.0)
    for si, (_, hp, n, k, c) in enumerate(slots):
        if q is not None: q.put(('hprog', si + 1, len(slots), total))
        left = t_end - time.time()
        if left <= 0 or not any(rem.values()): break
        last = memo.get((hp, k))
        if last is not None and all(rem.get(i, 0) >= v for i, v in Counter(p[0] for p in last).items()):
            pl = last                                           # 同一种零件的同一个孔：照抄上一个副本
        else: pl = _fill_one(c, shp, order, rem, rots, min(job.get('per_hole', 3.0), left))
        memo[(hp, k)] = pl
        for i, *_ in pl: rem[i] -= 1
        if pl: got.setdefault((hp, n), []).extend(pl); total += len(pl)
    return [(hp, tuple(sorted(v))) for (hp, n), v in sorted(got.items())]


def hole_worker(job, q):
    try: q.put(('hdone', fill_holes(job, q)))
    except Exception: q.put(('error', traceback.format_exc()))
