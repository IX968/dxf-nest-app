"""航模 DXF 排版 —— 后处理：微连接（全水口/半水口）、板号、零件编号，以及导出。"""
import math, collections
import ezdxf
from ezdxf import path as dpath
from ezdxf.math import Matrix44
from shapely.geometry import LineString, Point, Polygon, box
from shapely.ops import nearest_points
from shapely.strtree import STRtree
from shapely.prepared import prep
from shapely import affinity
from nest_core import TEXTS

LAY_CUT, LAY_HALF, LAY_SHEET, LAY_NUM, LAY_FRAME = '切割', '半水口', '板号', '零件编号', '板框'
CLEAR = 2.0            # 微连接离拐角（直边两端）的最小距离 mm

# 单线字体：每个字 4 宽 × 6 高的格子，激光走一遍即可刻出
FONT = {
    '0': [[(0, 0), (4, 0), (4, 6), (0, 6), (0, 0)]],
    '1': [[(1, 5), (2, 6), (2, 0)], [(1, 0), (3, 0)]],
    '2': [[(0, 6), (4, 6), (4, 3), (0, 3), (0, 0), (4, 0)]],
    '3': [[(0, 6), (4, 6), (4, 0), (0, 0)], [(0, 3), (4, 3)]],
    '4': [[(0, 6), (0, 3), (4, 3)], [(4, 6), (4, 0)]],
    '5': [[(4, 6), (0, 6), (0, 3), (4, 3), (4, 0), (0, 0)]],
    '6': [[(4, 6), (0, 6), (0, 0), (4, 0), (4, 3), (0, 3)]],
    '7': [[(0, 6), (4, 6), (4, 0)]],
    '8': [[(0, 0), (4, 0), (4, 6), (0, 6), (0, 0)], [(0, 3), (4, 3)]],
    '9': [[(4, 3), (0, 3), (0, 6), (4, 6), (4, 0), (0, 0)]],
    'A': [[(0, 0), (0, 4), (2, 6), (4, 4), (4, 0)], [(0, 3), (4, 3)]],
    'B': [[(0, 0), (0, 6), (3, 6), (4, 5), (4, 4), (3, 3), (0, 3)], [(3, 3), (4, 2), (4, 1), (3, 0), (0, 0)]],
    'C': [[(4, 6), (0, 6), (0, 0), (4, 0)]],
    'D': [[(0, 0), (0, 6), (2, 6), (4, 4), (4, 2), (2, 0), (0, 0)]],
    'E': [[(4, 6), (0, 6), (0, 0), (4, 0)], [(0, 3), (3, 3)]],
    'F': [[(4, 6), (0, 6), (0, 0)], [(0, 3), (3, 3)]],
    'G': [[(4, 6), (0, 6), (0, 0), (4, 0), (4, 3), (2, 3)]],
    'H': [[(0, 6), (0, 0)], [(4, 6), (4, 0)], [(0, 3), (4, 3)]],
    'I': [[(1, 6), (3, 6)], [(2, 6), (2, 0)], [(1, 0), (3, 0)]],
    'J': [[(4, 6), (4, 0), (0, 0), (0, 2)]],
    'K': [[(0, 6), (0, 0)], [(4, 6), (0, 3), (4, 0)]],
    'L': [[(0, 6), (0, 0), (4, 0)]],
    'M': [[(0, 0), (0, 6), (2, 3), (4, 6), (4, 0)]],
    'N': [[(0, 0), (0, 6), (4, 0), (4, 6)]],
    'O': [[(0, 0), (4, 0), (4, 6), (0, 6), (0, 0)]],
    'P': [[(0, 0), (0, 6), (4, 6), (4, 3), (0, 3)]],
    'Q': [[(0, 0), (4, 0), (4, 6), (0, 6), (0, 0)], [(2, 2), (4, 0)]],
    'R': [[(0, 0), (0, 6), (4, 6), (4, 3), (0, 3), (4, 0)]],
    'S': [[(4, 6), (0, 6), (0, 3), (4, 3), (4, 0), (0, 0)]],
    'T': [[(0, 6), (4, 6)], [(2, 6), (2, 0)]],
    'U': [[(0, 6), (0, 0), (4, 0), (4, 6)]],
    'V': [[(0, 6), (2, 0), (4, 6)]],
    'W': [[(0, 6), (1, 0), (2, 4), (3, 0), (4, 6)]],
    'X': [[(0, 6), (4, 0)], [(0, 0), (4, 6)]],
    'Y': [[(0, 6), (2, 3), (4, 6)], [(2, 3), (2, 0)]],
    'Z': [[(0, 6), (4, 6), (0, 0), (4, 0)]],
}


def text_width(s, h): return (len(s) * 6 - 2) * h / 6


def text_strokes(s, x, y, h):
    k = h / 6; out = []
    for n, ch in enumerate(s):
        cx = x + n * 6 * k
        for st in FONT.get(ch.upper(), []): out.append([(cx + px * k, y + py * k) for px, py in st])
    return out


def sheet_name(i):
    s = ''; i += 1
    while i: i, r = divmod(i - 1, 26); s = chr(65 + r) + s
    return s


def _xf(M, p):
    v = M.transform((p[0], p[1], 0)); return (v.x, v.y)


# ---------------------------------------------------------------- 零件拆成导出单元
def _bw(aci, tc):
    """黑/白色：ACI 7（白底显示黑、黑底显示白），或真彩色纯黑、纯白。“切割”层就是这个颜色。"""
    return tc in (0x000000, 0xFFFFFF) if tc is not None else aci == 7


def part_prims(part):
    """零件图元 → 导出单元（零件局部坐标）。直线单独拿出来，以便加微连接。
    每项 [kind, a/ent, b, layer, aci, 是否外轮廓, 真彩色, 是否放进切割层]。"""
    if getattr(part, '_prims', None) is not None: return part._prims
    ext = part.poly.exterior; prims = []; lens = []
    def on_ext(pts): return all(ext.distance(Point(q)) < 0.05 for q in (pts[0], pts[len(pts) // 2], pts[-1]))
    def add_line(a, b, lay, aci, tc):
        if math.dist(a, b) > 1e-6:
            prims.append(['line', a, b, lay, aci, on_ext([a, ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2), b]), tc])
            lens.append(math.dist(a, b))
    for e, lay, aci, tc, clip in part.ents:
        if clip is not None:
            for q in clip:
                for a, b in zip(q[:-1], q[1:]): add_line(a, b, lay, aci, tc)
            continue
        subs = [e]
        if e.dxftype() in ('LWPOLYLINE', 'POLYLINE'):            # 只拆外轮廓上的多段线（要加微连接）
            try:
                pts = [_xf(part.mloc, (v.x, v.y)) for v in dpath.make_path(e).flattening(0.1)]
                if any(ext.distance(Point(q)) < 0.05 for q in pts[::max(1, len(pts) // 8)]): subs = list(e.virtual_entities())
            except Exception: subs = [e]
        for s in subs:
            if s.dxftype() == 'LINE':
                add_line(_xf(part.mloc, s.dxf.start), _xf(part.mloc, s.dxf.end), lay, aci, tc); continue
            try: pts = [_xf(part.mloc, (v.x, v.y)) for v in dpath.make_path(s).flattening(0.1)]
            except Exception: pts = None
            prims.append(['ent', s, None, lay, aci, bool(pts) and len(pts) >= 2 and on_ext(pts), tc])
            lens.append(sum(math.dist(p, q) for p, q in zip(pts, pts[1:])) if pts else 0.0)
    # 哪些线放进“切割”层（按长度算，占外轮廓 10% 以上才算外轮廓的主要图层 / 颜色）：
    # · 外轮廓上的线：图层是外轮廓的主要图层就切。
    # · 零件内部的线：图层和颜色都要和外轮廓一致才切（如减重孔）。同图层、不同颜色的当刻线，
    #   保留原图层和颜色 —— LightBurn、RDWorks 等激光软件按颜色区分切割和雕刻。
    by_lay, by_col = collections.Counter(), collections.Counter()
    for p, n in zip(prims, lens):
        if p[5]: by_lay[p[3]] += n; by_col[(p[3], p[4], p[6])] += n
    tot = sum(by_lay.values())
    lays = {k for k, v in by_lay.items() if v > 0.1 * tot}
    cols = {k for k, v in by_col.items() if v > 0.1 * tot}
    for p in prims: p.append(p[3] in lays if p[5] else (p[3], p[4], p[6]) in cols)
    geo = [p for p in prims if p[0] == 'line' or p[1].dxftype() not in TEXTS]
    part._kept = sum(1 for p in geo if not p[5] and not p[7] and p[3] in lays)    # 同图层、不同颜色，按刻线保留
    # 原图切割线不是黑/白色，零件上却有黑/白色的刻线：导出后切割层是黑/白色，按颜色分加工的软件里会混在一起
    part._clash = not any(_bw(c[1], c[2]) for c in cols) and any(not p[7] and _bw(p[4], p[6]) for p in geo)
    part._prims = prims
    return prims


# ---------------------------------------------------------------- 微连接
def plan_tabs(part, tab, spacing):
    """返回 {直线序号: [中心位置 u, ...]}；零件本身带缺口返回 {}；没有合适直边返回 None。"""
    if getattr(part, 'has_gaps', False): return {}
    prims = part_prims(part); ext = part.poly.exterior; P = ext.length
    cands = [(k, p) for k, p in enumerate(prims) if p[0] == 'line' and p[5] and math.dist(p[1], p[2]) >= tab + 2 * CLEAR]
    if not cands: return None
    n = max(1, min(16, round(P / spacing)))
    chosen, used_s = {}, []
    for j in range(n):
        t = (j + 0.5) * P / n; T = ext.interpolate(t); best = None
        for k, p in cands:
            a, b = p[1], p[2]; L = math.dist(a, b); dx, dy = (b[0] - a[0]) / L, (b[1] - a[1]) / L
            u = min(max((T.x - a[0]) * dx + (T.y - a[1]) * dy, CLEAR + tab / 2), L - CLEAR - tab / 2)
            if any(abs(u - v) < tab + 2 * CLEAR for v in chosen.get(k, [])): continue
            s = ext.project(Point(a[0] + dx * u, a[1] + dy * u))
            if n > 1 and any(min(abs(s - v), P - abs(s - v)) < P / (2.5 * n) for v in used_s): continue
            d = abs(s - t); d = min(d, P - d)
            if best is None or d < best[0]: best = (d, k, u, s)
        if best: chosen.setdefault(best[1], []).append(best[2]); used_s.append(best[3])
    return chosen


def tab_points(part, plan):
    """微连接中心点（零件局部坐标），预览用。"""
    pts = []
    for k, us in (plan or {}).items():
        p = part_prims(part)[k]; a, b = p[1], p[2]; L = math.dist(a, b)
        for u in us: pts.append((a[0] + (b[0] - a[0]) * u / L, a[1] + (b[1] - a[1]) * u / L))
    return pts


# ---------------------------------------------------------------- 板号、零件编号
def plan_marks(polys, rect, letter, letter_h, num_h, do_letter, do_nums, outside_ok=False):
    """polys：本张板各零件的实际外形（世界坐标，已按阅读顺序排好）。rect：可用范围 (x0,y0,x1,y1)。
    返回 (板号笔画, 编号笔画, 没地方放的编号个数)。"""
    polys = [Polygon(g.exterior) for g in polys]          # 孔也算禁放区（孔里是废料）
    obst = [g.buffer(0.6) for g in polys]
    tree = STRtree(obst) if obst else None
    area = prep(box(*rect)); placed = []
    def free(geom, skip=-1):
        if not area.contains(geom): return False
        if tree is not None and any(j != skip and obst[j].intersects(geom) for j in tree.query(geom)): return False
        return not any(q.intersects(geom) for q in placed)
    marks, nums, miss = [], [], 0
    if do_letter:
        w, h = text_width(letter, letter_h), letter_h; x0, y0, x1, y1 = rect; m = 1.0
        spots = [(x0 + m, y0 + m), (x0 + m, y1 - h - m), (x1 - w - m, y1 - h - m), (x1 - w - m, y0 + m)]
        step = max(2.0, h / 2)
        spots += [(x0 + m + i * step, y0 + m + j * step) for j in range(int((y1 - y0) / step)) for i in range(int((x1 - x0) / step))]
        pos = None
        for sx, sy in spots:
            B = box(sx - 0.5, sy - 0.5, sx + w + 0.5, sy + h + 0.5)
            if free(B): pos = (sx, sy); placed.append(B); break
        if pos is None and outside_ok: pos = (x0, y1 + 2.0)
        if pos is not None: marks = text_strokes(letter, pos[0], pos[1], h)
    if do_nums:
        h = num_h
        for idx, g in enumerate(polys):
            s = str(idx + 1); w = text_width(s, h); r = math.hypot(w, h) / 2; done = False
            for d in (2.5, 4, 7, 11, 16, 24, 35, 50):                  # 离零件至少 2 mm，留出箭头
                ring = g.buffer(d + r, quad_segs=4).exterior; L = ring.length; ns = max(16, min(72, int(L / 3)))
                for t in range(ns):
                    c = ring.interpolate(t * L / ns)
                    B = box(c.x - w / 2 - 0.3, c.y - h / 2 - 0.3, c.x + w / 2 + 0.3, c.y + h / 2 + 0.3)
                    if not free(B): continue
                    pb, pp = nearest_points(B, g); dd = pb.distance(pp)
                    if dd < 2.0: continue
                    tip = (pp.x + (pb.x - pp.x) * 0.5 / dd, pp.y + (pb.y - pp.y) * 0.5 / dd)   # 箭头尖离零件 0.5 mm
                    ln = LineString([(pb.x, pb.y), tip])
                    if not free(ln.buffer(0.15), skip=idx): continue
                    nums += text_strokes(s, c.x - w / 2, c.y - h / 2, h)
                    nums.append([(pb.x, pb.y), tip])                        # 箭头
                    ang = math.atan2(tip[1] - pb.y, tip[0] - pb.x); al = min(1.2, (dd - 0.5) / 2)
                    for sgn in (1, -1):
                        a2 = ang + math.pi - sgn * math.radians(28)
                        nums.append([tip, (tip[0] + al * math.cos(a2), tip[1] + al * math.sin(a2))])
                    placed.append(ln.buffer(0.3))
                    placed.append(B); done = True; break
                if done: break
            if not done: miss += 1
    return marks, nums, miss


def reading_order(items):
    """零件按从上到下、从左到右排序（编号顺序）。items: [(poly, ...)]"""
    def key(it):
        c = it[0].centroid; return (-round(c.y / 25), c.x)
    return sorted(items, key=key)


def placed_poly(part, rot, tx, ty):
    return affinity.translate(affinity.rotate(part.poly, rot, origin=(0, 0)), tx, ty)


# ---------------------------------------------------------------- 导出
def _layer(doc, name, color, rgb=None):
    if name in doc.layers: return
    l = doc.layers.add(name, color=color)
    if rgb is not None: l.rgb = rgb


def export_sheet(fn, items, sheet_wh, frame=False, texts=False, tabs=None, half=False, tab_len=1.0,
                 marks=(), nums=()):
    """items: [(part, rot, tx, ty)]，tx/ty 已含边距偏移。tabs: {id(part): plan}。"""
    doc = ezdxf.new('R2010', setup=True); doc.units = ezdxf.units.MM; msp = doc.modelspace()
    _layer(doc, LAY_CUT, 7)                    # 7 = 黑/白：白底显示黑色，黑底显示白色（不要写死 RGB 黑）
    if frame:
        _layer(doc, LAY_FRAME, 8)
        W, H = sheet_wh
        msp.add_lwpolyline([(0, 0), (W, 0), (W, H), (0, H)], close=True, dxfattribs={'layer': LAY_FRAME})
    if half and tabs: _layer(doc, LAY_HALF, 1)
    if marks: _layer(doc, LAY_SHEET, 5)
    if nums: _layer(doc, LAY_NUM, 3)
    for part, rot, tx, ty in items:
        prims = part_prims(part); plan = (tabs or {}).get(id(part)) or {}
        ca, sa = math.cos(math.radians(rot)), math.sin(math.radians(rot))
        W_ = lambda p: (p[0] * ca - p[1] * sa + tx, p[0] * sa + p[1] * ca + ty)
        M = Matrix44.chain(part.mloc, Matrix44.z_rotate(math.radians(rot)), Matrix44.translate(tx, ty, 0))
        for k, p in enumerate(prims):
            kind, a, b, lay, aci, ext, tc, cut = p
            if cut: L_, col, tc = LAY_CUT, 256, None
            else:
                L_, col = lay, aci
                if L_ not in doc.layers:
                    try: _layer(doc, L_, aci)
                    except Exception: L_ = '0'
            at = {'layer': L_, 'color': col}
            if tc is not None: at['true_color'] = tc
            if kind == 'line':
                Lg = math.dist(a, b); d = ((b[0] - a[0]) / Lg, (b[1] - a[1]) / Lg)
                pt = lambda u: (a[0] + d[0] * u, a[1] + d[1] * u); s0 = 0.0
                for u in sorted(plan.get(k, [])):
                    if u - tab_len / 2 > s0 + 1e-6: msp.add_line(W_(pt(s0)), W_(pt(u - tab_len / 2)), dxfattribs=at)
                    if half: msp.add_line(W_(pt(u - tab_len / 2)), W_(pt(u + tab_len / 2)), dxfattribs={'layer': LAY_HALF})
                    s0 = u + tab_len / 2
                if Lg - s0 > 1e-6: msp.add_line(W_(pt(s0)), W_(b), dxfattribs=at)
                continue
            e = a
            if not texts and e.dxftype() in TEXTS: continue
            try:
                c = e.copy(); c.transform(M)
                c.dxf.layer = L_; c.dxf.color = col
                if tc is None: c.dxf.discard('true_color')         # 真彩色会盖过图层颜色，切割线要去掉
                else: c.dxf.true_color = tc
                if c.dxf.hasattr('linetype'): c.dxf.linetype = 'BYLAYER'
                if c.dxf.hasattr('style'): c.dxf.style = 'Standard'
                msp.add_foreign_entity(c, copy=False)
            except Exception:
                try: dpath.render_splines_and_polylines(msp, [dpath.make_path(e).transform(M)], dxfattribs=at)
                except Exception: pass
    for q in marks: msp.add_lwpolyline(q, dxfattribs={'layer': LAY_SHEET})
    for q in nums: msp.add_lwpolyline(q, dxfattribs={'layer': LAY_NUM})
    W, H = sheet_wh                                      # 写入图形范围，打开时直接显示整张板
    try:                                                  # 保存时 ezdxf 用模型空间的范围写文件头（全 0 会被当成没有）
        from ezdxf import bbox
        e = bbox.extents(msp, fast=True)
        if e.has_data: msp.dxf.extmin = (e.extmin.x, e.extmin.y, 0); msp.dxf.extmax = (e.extmax.x, e.extmax.y, 0)
    except Exception: pass
    msp.dxf.limmin = (0, 0); msp.dxf.limmax = (W, H)
    try:
        from ezdxf import zoom
        zoom.window(msp, (-0.03 * W, -0.03 * H), (1.03 * W, 1.03 * H))
    except Exception: pass
    doc.saveas(fn)
