"""航模 DXF 排版 v0.2.0 —— 核心：读 DXF → 零件，排版求解（子进程，实时进度）。导出见 nest_post.py。"""
import math, os, re, time, threading, traceback
import ezdxf
from ezdxf import path as dpath, disassemble, recover
from ezdxf.math import Matrix44
from shapely.geometry import Polygon, LineString, Point, MultiLineString
from shapely.ops import unary_union, polygonize, linemerge
import shapely
from shapely import affinity
from shapely.prepared import prep
from shapely.strtree import STRtree

UNIT = {0: 1.0, 1: 25.4, 2: 304.8, 4: 1.0, 5: 10.0, 6: 1000.0}   # $INSUNITS → mm
FLAT = 0.05            # 曲线离散精度 mm（只用于排版计算，导出保留原图元）
SNAP = 0.05            # 散线端点拼接容差 mm
GRID = 0.01            # 求交精度：端点落在别的线段中间（T 字形）也能接上
CURVES = {'LINE', 'ARC', 'CIRCLE', 'ELLIPSE', 'SPLINE', 'LWPOLYLINE', 'POLYLINE'}
TEXTS = {'TEXT', 'MTEXT'}
FRAME_LAYERS = {'FRAME', 'BORDER', '板框', '边框', '外框'}


class Part:
    def __init__(self, name, src, poly, ents, mloc, org=(0.0, 0.0)):
        self.name, self.src, self.poly, self.ents, self.mloc = name, src, poly, ents, mloc
        self.org = org                     # 零件局部原点在原图里的位置（预览用）
        self.qty = 1; self.copies = [org]  # 同形零件在原图里的各个位置
        b = poly.bounds; self.w, self.h = b[2] - b[0], b[3] - b[1]
        self.area = poly.area
        self.islands = []                  # 镂空里的独立小件（如拆出来的撑杆），跟着本零件走


KIND_CN = {'open': '未闭合', 'loose': '散线', 'text': '散落文字', 'frame': '板框', 'thin': '极窄区域'}
EDGE_TOL = 0.03        # 线在零件边界上的判定距离 mm
GAP_MAX = 3.0          # 缺口不超过此值的断开轮廓按“未闭合”列出（常见微连接 1～2 mm）
THIN = 0.3             # 平均宽度小于此值的闭合区域不当零件（两条线几乎重合）


class Issue:
    """导入时发现的问题。state: todo 未处理 / ignore 忽略(板框=去掉) / part 当零件放行"""
    def __init__(self, kind, key, lines, ends, gap, recs):
        self.kind, self.key, self.lines, self.ends, self.gap, self.recs = kind, key, lines, ends, gap, recs
        self.state = 'todo'

    def text(self):
        if self.kind == 'open': return f'未闭合轮廓，缺口 {self.gap:.2f} mm'
        if self.kind == 'loose': return f'散线，{len(self.recs)} 个图元'
        if self.kind == 'text': return f'零件外的文字，{len(self.recs)} 处'
        if self.kind == 'thin': return f'极窄区域，宽约 {self.gap:.2f} mm，两条线几乎重合，已排除'
        return '板框'


class FileData:
    def __init__(self, fn, parts, issues, notes, bounds):
        self.fn, self.parts, self.issues, self.notes, self.bounds = fn, parts, issues, notes, bounds
        self.name = os.path.basename(fn)


# ---------------------------------------------------------------- 读 DXF
def _aci(e, doc):
    c = e.dxf.get('color', 256)
    if c == 256:
        try: c = doc.layers.get(e.dxf.get('layer', '0')).color
        except Exception: c = 7
    if c in (0, 256) or c < 0: c = 7
    return abs(c)


def _tc(e):
    """真彩色（24 位 RGB 整数），没设就是 None。有些软件只用真彩色区分切割和雕刻。"""
    try: return e.dxf.get('true_color')
    except Exception: return None


def _snap_lines(lines):
    """端点小于 SNAP 的当成同一点（只是数值误差，不算补缝）。"""
    grid = {}
    def snap(p):
        gx, gy = int(math.floor(p[0] / SNAP)), int(math.floor(p[1] / SNAP))
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for q in grid.get((gx + dx, gy + dy), ()):
                    if abs(q[0] - p[0]) <= SNAP and abs(q[1] - p[1]) <= SNAP: return q
        grid.setdefault((gx, gy), []).append(p); return p
    out = []
    for pts in lines:
        pts = list(pts); pts[0] = snap(pts[0]); pts[-1] = snap(pts[-1])
        if len(pts) >= 2 and LineString(pts).length > 1e-6: out.append(LineString(pts))
    return out


class _Rec:
    __slots__ = ('e', 'pts', 'rep', 'lay', 'aci', 'tc')
    def __init__(self, e, pts, rep, lay, aci, tc):
        self.e, self.pts, self.rep, self.lay, self.aci, self.tc = e, pts, rep, lay, aci, tc


def _rnd(p, k=2): return (round(p[0], k), round(p[1], k))


def load_dxf(fn, opts=None):
    """读一个 DXF。opts: keep_frame(板框当零件) / as_part(当零件放行的问题 key) / ignore(已忽略的问题 key)。"""
    opts = opts or {}
    keep_frame = opts.get('keep_frame', False); as_part = opts.get('as_part', set()); ignore = opts.get('ignore', set())
    notes = []
    try: doc = ezdxf.readfile(fn)
    except Exception: doc, _ = recover.readfile(fn)
    sc = UNIT.get(doc.header.get('$INSUNITS', 0), 1.0)
    S = Matrix44.scale(sc, sc, 1)
    recs, seen, dup, skipped = [], set(), 0, 0
    for e in disassemble.recursive_decompose(doc.modelspace()):
        t = e.dxftype()
        if t == 'INSERT': continue
        lay = e.dxf.get('layer', '0')
        if t in CURVES:
            try: pts = [(v.x * sc, v.y * sc) for v in dpath.make_path(e).flattening(FLAT)]
            except Exception: skipped += 1; continue
            if len(pts) < 2: continue
            mx = sum(p[0] for p in pts) / len(pts); my = sum(p[1] for p in pts) / len(pts)
            k = ('c', frozenset({_rnd(pts[0]), _rnd(pts[-1])}), _rnd((mx, my)), round(LineString(pts).length, 2))
            if k in seen: dup += 1; continue
            seen.add(k); recs.append(_Rec(e, pts, pts[len(pts) // 2], lay, _aci(e, doc), _tc(e)))
        elif t in TEXTS:
            try: ip = e.dxf.insert
            except Exception: continue
            k = ('t', t, e.dxf.get('text', '') if t == 'TEXT' else e.text, _rnd((ip.x, ip.y)))
            if k in seen: dup += 1; continue
            seen.add(k); recs.append(_Rec(e, None, (ip.x * sc, ip.y * sc), lay, _aci(e, doc), _tc(e)))
        else: skipped += 1
    if dup: notes.append(f'已删除重复线 {dup} 条')
    if skipped: notes.append(f'已忽略不支持的图元 {skipped} 个（填充、标注等）')

    # ---- 所有闭合环
    cand = []
    opens = []
    for r in recs:
        if r.pts is None: continue
        if len(r.pts) >= 4 and math.dist(r.pts[0], r.pts[-1]) < 0.02: cand.append((Polygon(r.pts), r.lay))
        else: opens.append(r.pts)
    if opens:
        for f in polygonize(shapely.union_all(_snap_lines(opens), grid_size=GRID)): cand.append((Polygon(f.exterior), ''))
    polys, keys = [], set()
    for g, lay in cand:
        if not g.is_valid: g = g.buffer(0)
        if g.geom_type == 'MultiPolygon': g = max(g.geoms, key=lambda q: q.area)
        if g.is_empty or g.area < 0.5: continue
        g = Polygon(g.exterior); c = g.centroid
        key = (round(g.area, 1), round(c.x, 1), round(c.y, 1))
        if key in keys: continue
        keys.add(key); polys.append([g, lay])
    thin = [p[0] for p in polys if 2 * p[0].area / p[0].length < THIN]
    polys = [p for p in polys if 2 * p[0].area / p[0].length >= THIN]
    if thin and polys:                                   # 零件内部的窄条（刻字笔画等）不报
        hold = STRtree([p[0] for p in polys]); tp = [prep(p[0].buffer(0.05)) for p in polys]
        thin = [t for t in thin if not any(tp[j].contains(t) for j in hold.query(t))]
    polys.sort(key=lambda r: -r[0].area)
    n = len(polys)
    big = [prep(p[0].buffer(0.05)) for p in polys]
    parent = [-1] * n
    for i in range(n):
        for j in range(i - 1, -1, -1):
            if polys[j][0].area > polys[i][0].area and big[j].contains(polys[i][0]): parent[i] = j; break
    kids = [[] for _ in range(n)]
    for i, p in enumerate(parent):
        if p >= 0: kids[p].append(i)
    frame = [False] * n
    for i in range(n):
        g, lay = polys[i]
        if lay.upper() in FRAME_LAYERS and kids[i]: frame[i] = True
        elif parent[i] < 0 and len(kids[i]) >= 2 and g.area / g.minimum_rotated_rectangle.area > 0.999 and \
                (sum(polys[k][0].area for k in kids[i]) > 0.3 * g.area or any(kids[k] for k in kids[i])):
            frame[i] = True                    # 矩形里装着好几个东西，占得多或者里面的东西自己还有孔 → 板框
    # 板框补充规则：①矩形里散布着大量线条（零件轮廓没闭合时 kids 很少）；②和已认出的板框同尺寸的矩形
    def rect_dims(g):
        c = list(g.minimum_rotated_rectangle.exterior.coords)
        return sorted((round(math.dist(c[0], c[1])), round(math.dist(c[1], c[2]))))
    for i in range(n):
        g = polys[i][0]
        if frame[i] or parent[i] >= 0 or g.area / g.minimum_rotated_rectangle.area <= 0.999: continue
        inner = prep(g.buffer(-0.5))
        pts = [r.rep for r in recs if inner.contains(Point(r.rep))]
        if len(pts) >= 10:
            xs, ys = [p[0] for p in pts], [p[1] for p in pts]; b = g.bounds
            if (max(xs) - min(xs)) > 0.5 * (b[2] - b[0]) or (max(ys) - min(ys)) > 0.5 * (b[3] - b[1]): frame[i] = True
    fd_ = [rect_dims(polys[i][0]) for i in range(n) if frame[i]]
    for i in range(n):
        g = polys[i][0]
        if not frame[i] and parent[i] < 0 and g.area / g.minimum_rotated_rectangle.area > 0.999 and \
                any(abs(a[0] - d[0]) <= 1 and abs(a[1] - d[1]) <= 1 for d in fd_ for a in [rect_dims(g)]): frame[i] = True
    frames = [i for i in range(n) if frame[i]]
    if keep_frame: frame = [False] * n
    depth = [0] * n
    for i in range(n):
        d, p = 0, parent[i]
        while p >= 0:
            if not frame[p]: d += 1
            p = parent[p]
        depth[i] = d
    outer = [i for i in range(n) if not frame[i] and depth[i] == 0]     # 只认最外层，里面的都算这个零件的

    # ---- 图元归属
    # 共边排版：一条线可能同时是两个零件的边，分给每个用到它的零件；只用到一段的，截出那一段
    fillg = {i: polys[i][0].buffer(EDGE_TOL) for i in outer}
    fill = {i: prep(fillg[i]) for i in outer}
    edge = {i: prep(polys[i][0].exterior.buffer(0.1)) for i in frames}
    owned = {i: [] for i in outer}; fr_recs = {i: [] for i in frames}; free = []
    oidx = list(outer); tree = STRtree([fillg[i] for i in oidx]) if oidx else None
    for r in recs:
        P = Point(r.rep); hits = []
        if r.pts is None:                                   # 文字：归最小的包含它的零件
            best = None
            for i in outer:
                if fill[i].contains(P) and (best is None or polys[i][0].area < polys[best][0].area): best = i
            if best is not None: hits = [(best, None)]
        else:
            ls = LineString(r.pts); Ln = ls.length
            for j in (tree.query(ls) if tree is not None else []):
                i = oidx[j]
                if fill[i].contains(ls): hits.append((i, None)); continue
                seg = ls.intersection(fillg[i])
                if seg.is_empty: continue
                if Ln <= 0 or seg.length / Ln > 0.995: hits.append((i, None))
                elif seg.length > 0.3: hits.append((i, seg))
        if hits:
            for i, seg in hits: owned[i].append((r, seg))
            continue
        f = next((i for i in frames if edge[i].contains(P)), None)
        if f is not None: fr_recs[f].append(r)
        else: free.append(r)

    # ---- 问题
    issues = []
    for g in thin:
        c = g.centroid
        iss = Issue('thin', ('thin', round(c.x), round(c.y)), [list(g.exterior.coords)], [], 2 * g.area / g.length, [])
        iss.state = 'ignore'
        issues.append(iss)
    for i in frames:
        c = polys[i][0].centroid
        iss = Issue('frame', ('frame', round(c.x), round(c.y)), [list(polys[i][0].exterior.coords)], [], 0, fr_recs[i])
        iss.state = 'part' if keep_frame else ('ignore' if iss.key in ignore else 'todo')
        issues.append(iss)
    fc = [r for r in free if r.pts is not None]
    extra = []                                                    # 放行成零件的 (poly, recs)
    if fc:
        ls = _snap_lines([r.pts for r in fc])
        m = linemerge(shapely.union_all(ls, grid_size=GRID)) if ls else None
        chains = [] if m is None or m.is_empty else (list(m.geoms) if hasattr(m, 'geoms') else [m])
        ends = [(c.coords[0], c.coords[-1]) for c in chains]
        CL = GAP_MAX
        uf = list(range(len(chains)))
        def root(a):
            while uf[a] != a: uf[a] = uf[uf[a]]; a = uf[a]
            return a
        for a in range(len(chains)):
            for b in range(a + 1, len(chains)):
                if min(math.dist(p, q) for p in ends[a] for q in ends[b]) <= CL: uf[root(a)] = root(b)
        groups = {}
        for a in range(len(chains)): groups.setdefault(root(a), []).append(a)
        left = list(fc)
        for grp in groups.values():
            pe = [p for j in grp for p in ends[j]]
            gaps = [min((math.dist(p, q) for v, q in enumerate(pe) if v != u), default=1e9) for u, p in enumerate(pe)]
            ml = MultiLineString([list(chains[j].coords) for j in grp])
            gap = max(gaps) if gaps else 0
            kind = 'open' if gaps and all(d <= CL for d in gaps) and ml.length > 3 * gap else 'loose'
            near = prep(ml.buffer(0.15)); mine = [r for r in left if near.contains(Point(r.rep))]
            left = [r for r in left if r not in mine]
            c = ml.centroid
            iss = Issue(kind, (kind, round(c.x), round(c.y)), [list(chains[j].coords) for j in grp],
                        [p for p, d in zip(pe, gaps) if d > 1e-6] if kind == 'open' else [], gap, mine)
            if iss.key in as_part:
                rr = gap / 2 + 0.2 if kind == 'open' else 0.5
                g = ml.buffer(rr)
                if g.geom_type == 'MultiPolygon': g = max(g.geoms, key=lambda q: q.area)
                g = Polygon(g.exterior)
                if kind == 'open':
                    g2 = g.buffer(-rr)
                    if not g2.is_empty and g2.geom_type == 'Polygon': g = g2
                iss.state = 'part'; extra.append((g, [(r, None) for r in mine]))
            elif iss.key in ignore: iss.state = 'ignore'
            issues.append(iss)
    ft = [r for r in free if r.pts is None]
    if ft:
        iss = Issue('text', ('text',), [], [r.rep for r in ft], 0, ft)
        if iss.key in ignore: iss.state = 'ignore'
        issues.append(iss)

    # ---- 零件
    stem = os.path.splitext(os.path.basename(fn))[0]
    parts = []
    # 放行的未闭合零件把落在它里面的闭合轮廓（减重孔等）收回来，当作它的孔和内部图元
    # 放行的未闭合轮廓如果整个落在另一个零件里面（如带水口的减重窗），它是那个零件的孔，不单独成零件
    extra.sort(key=lambda t: -t[0].area)
    hosts = []; xhole = {}; xisl = {}
    for g, own in extra:
        h = next((m for m in hosts if m[3].contains(g)), None)
        if h is not None:
            h[1] += [x for x in own if x not in h[1]]; h[2].append(g.exterior.coords); continue
        hi = next((i for i in outer if fillg.get(i) is not None and fillg[i].contains(g)), None)
        if hi is not None:
            owned[hi] += [x for x in own if x not in owned[hi]]
            mat = Polygon(polys[hi][0].exterior.coords, [polys[k][0].exterior.coords for k in kids[hi] if not frame[k]])
            if mat.buffer(0.2).contains(g): xhole.setdefault(hi, []).append(g.exterior.coords)
            else: xisl.setdefault(hi, []).append(g)            # 落在孔里：是镂空里的小件
            continue
        hosts.append([g, list(own), [], prep(Polygon(g.exterior).buffer(0.2))])
    extra = []
    for g, own, hl, _ in hosts:
        if hl:
            try:
                g2 = Polygon(g.exterior.coords, [r.coords for r in g.interiors] + hl)
                if g2.is_valid: g = g2
            except Exception: pass
        extra.append((g, own))
    absorbed = set(); ext2 = []
    for g, own in extra:
        gb = prep(g.buffer(0.2)); holes = []
        for i in outer:
            if i in absorbed or not gb.contains(polys[i][0]): continue
            absorbed.add(i); holes.append(polys[i][0].exterior.coords)
            own = own + [x for x in owned[i] if x not in own]
        if holes:
            try:
                g2 = Polygon(g.exterior.coords, holes)
                if g2.is_valid: g = g2
            except Exception: pass
        ext2.append((g, own))
    def top(j):
        while parent[j] >= 0 and not frame[parent[j]]: j = parent[j]
        return j
    isl = {}                                                    # 镂空里的闭合小件：深度为偶数的轮廓
    for j in range(n):
        if not frame[j] and depth[j] >= 2 and depth[j] % 2 == 0:
            isl.setdefault(top(j), []).append(
                Polygon(polys[j][0].exterior.coords, [polys[k][0].exterior.coords for k in kids[j] if not frame[k]]))
    for i, gl in xisl.items(): isl.setdefault(i, []).extend(gl)
    shapes = [(Polygon(polys[i][0].exterior.coords, [polys[k][0].exterior.coords for k in kids[i] if not frame[k]] + xhole.get(i, [])),
               owned[i], False, isl.get(i, [])) for i in outer if i not in absorbed] + [(g, own, True, []) for g, own in ext2]
    for g, own, gp, il in shapes:
        b = g.bounds
        g0 = affinity.translate(g, -b[0], -b[1])
        m = Matrix44.chain(S, Matrix44.translate(-b[0], -b[1], 0))
        ents = []
        for r, seg in own:
            clip = None
            if seg is not None:                              # 截取的那一段，存成零件局部坐标的折线
                gs = list(seg.geoms) if hasattr(seg, 'geoms') else [seg]
                clip = [[(x - b[0], y - b[1]) for x, y in q.coords] for q in gs if q.geom_type == 'LineString' and q.length > 0.05]
                if not clip: continue
            ents.append((r.e, r.lay, r.aci, r.tc, clip))
        parts.append(Part('', fn, g0, ents, m, (b[0], b[1]))); parts[-1].has_gaps = gp
        parts[-1].islands = [affinity.translate(q, -b[0], -b[1]) for q in il if q.is_valid]
        parts[-1].area += sum(q.area for q in parts[-1].islands)
    merged = []
    for p in parts:                                      # 同形零件合并成数量
        for q in merged:
            if abs(p.area - q.area) < 1e-3 * q.area + 0.05 and abs(p.w - q.w) < 0.05 and abs(p.h - q.h) < 0.05 \
                    and p.poly.symmetric_difference(q.poly).area < 2e-3 * q.area + 0.1 \
                    and len(p.islands) == len(q.islands) \
                    and (not p.islands or unary_union(p.islands).symmetric_difference(unary_union(q.islands)).area < 0.5):
                q.qty += 1; q.copies.append(p.org); break
        else: merged.append(p)
    merged.sort(key=lambda p: -p.area)
    for k, p in enumerate(merged): p.name = f'{stem}-{k + 1}'
    allg = [p[0] for p in polys] + [MultiLineString(i.lines) for i in issues if i.lines]
    bounds = unary_union(allg).bounds if allg else (0, 0, 1, 1)
    return FileData(fn, merged, issues, notes, bounds)


# ---------------------------------------------------------------- 排版求解
def item_shape(poly, gap):
    """排版用外形：外轮廓外扩 gap/2（再加一点余量给简化），逆时针点列。"""
    b = gap / 2 + 0.05
    g = Polygon(poly.exterior).buffer(b, quad_segs=4).simplify(0.04)
    if g.geom_type == 'MultiPolygon': g = max(g.geoms, key=lambda q: q.area)
    g = Polygon(g.exterior)
    if not g.exterior.is_ccw: g = Polygon(list(g.exterior.coords)[::-1])
    return [tuple(map(float, c)) for c in list(g.exterior.coords)[:-1]], b


def narrow_angle(g):
    """把零件转到最窄方向（最小外接矩形的长边水平）需要转的角度（度）。"""
    c = list(g.minimum_rotated_rectangle.exterior.coords)
    e1 = (c[1][0] - c[0][0], c[1][1] - c[0][1]); e2 = (c[2][0] - c[1][0], c[2][1] - c[1][1])
    e = e1 if math.hypot(*e1) >= math.hypot(*e2) else e2
    return -math.degrees(math.atan2(e[1], e[0]))


def _placed(shape_poly, rot, t):
    return affinity.translate(affinity.rotate(shape_poly, rot, origin=(0, 0)), t[0], t[1])


def _pl(sol):
    return [(p.id, float(p.rotation), float(p.translation[0]), float(p.translation[1])) for p in sol.placed_items]


def run_job(job, outq):
    import spyrrow as S
    shp = {k: Polygon(v) for k, v in job['shapes'].items()}
    L, H, rot, T = job['L'], job['H'], job['rot'], job['time']
    seed = job.get('seed', 1)

    def geo(pl): return [_placed(shp[i], r, (x, y)) for i, r, x, y in pl]

    def overlap(pl):
        gs = geo(pl)
        for a in range(len(gs)):
            for b in range(a + 1, len(gs)):
                if gs[a].bounds[2] < gs[b].bounds[0] or gs[b].bounds[2] < gs[a].bounds[0]: continue
                if gs[a].intersection(gs[b]).area > 0.5: return True
        return False

    def solve(rem, h, tsec, sheet, label):
        items = [S.Item(i, job['shapes'][i], c, rot) for i, c in rem.items() if c > 0]
        for s in range(3):
            inst = S.StripPackingInstance('nest', float(h), items)
            cfg = S.StripPackingConfig(early_termination=True, total_computation_time=max(2, int(tsec)),
                                       seed=seed + s * 101, num_workers=job.get('threads'))
            q = S.ProgressQueue(); box = {}
            def run():
                try: box['sol'] = inst.solve(cfg, q)
                except BaseException as ex: box['err'] = str(ex)          # 求解器崩溃（Rust panic）也接住
            th = threading.Thread(target=run, daemon=True)
            th.start(); last = 0
            while th.is_alive():
                time.sleep(0.05)
                reps = q.drain()
                if reps and time.time() - last > 0.08:
                    k, sol = reps[-1]; last = time.time()
                    outq.put(('progress', sheet, label, k.phase_name(), sol.width, h, _pl(sol)))
            th.join()
            if 'sol' not in box:
                msg = box.get('err', '')
                m = re.search(r'item (\d+) does not seem to fit', msg)
                if m and int(m.group(1)) < len(items):
                    raise NestError(f'零件【{items[int(m.group(1))].id}】放不进板宽，请加大板材宽度、减小边距或换一种旋转方式。')
                raise NestError('排版计算失败：' + msg[:300])
            sol = box['sol']; pl = _pl(sol)
            if not overlap(pl): return sol.width, pl
            outq.put(('note', f'第 {sheet + 1} 张结果存在重叠，重新计算'))
        return sol.width, pl

    if job['mode'] == 'corner':
        corner_free(job, outq, shp, solve, geo); return
    if job.get('strategy') == 'shelf':
        plan = shelf_plan(job, shp, seed)
        if plan:
            sheets = []
            for k, pl in enumerate(plan):
                sheets.append(pl); outq.put(('sheet', k, pl))
            for k, pl in enumerate(plan):                   # 每张板压紧，把空位让到右边，方便回填
                sub = {}
                for p in pl: sub[p[0]] = sub.get(p[0], 0) + 1
                try: w, p2 = solve(sub, H, max(3, T / 2), k, '压紧')
                except NestError: continue
                if w <= L + 1e-6: sheets[k] = p2; outq.put(('sheet', k, p2))
            if len(sheets) > 1: sheets = backfill(job, outq, sheets, shp, solve)
            outq.put(('done', sheets)); return
    rem = dict(job['qty']); sheets = []
    while any(c > 0 for c in rem.values()):
        k = len(sheets)
        w, pl = solve(rem, H, T, k, '排版')
        if w <= L + 1e-6:
            sheets.append(pl); outq.put(('sheet', k, pl)); break
        # 整条放不下：取条带前段（左端起点在板长内）的零件，单独再排一张；放不下就去掉最右边那件再排
        gs = geo(pl)
        cand = [p for p, g in sorted(zip(pl, gs), key=lambda t: t[1].bounds[0]) if g.bounds[0] < L * 0.97]
        inside = None
        while cand:
            sub = {}
            for p in cand: sub[p[0]] = sub.get(p[0], 0) + 1
            w2, p2 = solve(sub, H, max(3, T / 2), k, '分张')
            if w2 <= L + 1e-6: inside = p2; break
            g2 = geo(p2); far = max(range(len(p2)), key=lambda j: g2[j].bounds[2])
            drop = p2[far][0]
            for j in range(len(cand) - 1, -1, -1):
                if cand[j][0] == drop: del cand[j]; break
        if not inside:
            outq.put(('error', '存在超出板材尺寸的零件，请更换板材。')); return
        for p in inside: rem[p[0]] -= 1
        sheets.append(inside); outq.put(('sheet', k, inside))
    if len(sheets) > 1: sheets = backfill(job, outq, sheets, shp, solve)
    outq.put(('done', sheets))


def _profile(g, r):
    """零件按角度 r 转好后的上下轮廓（每 1 mm 一列，向相邻列取保守值）。"""
    import numpy as np
    from shapely import segmentize
    pr = affinity.rotate(g, r, origin=(0, 0)); bx, by, mx, my = pr.bounds
    pts = np.asarray(segmentize(pr.exterior, 0.25).coords) - (bx, by)
    n = int(math.floor(mx - bx)) + 1
    c = np.clip(np.floor(pts[:, 0]).astype(int), 0, n - 1)
    bot = np.full(n, np.inf); top = np.full(n, -np.inf)
    np.minimum.at(bot, c, pts[:, 1]); np.maximum.at(top, c, pts[:, 1])
    bot = np.where(np.isinf(bot), 0.0, bot); top = np.where(np.isinf(top), my - by, top)
    b2, t2 = bot.copy(), top.copy()
    b2[1:] = np.minimum(b2[1:], bot[:-1]); b2[:-1] = np.minimum(b2[:-1], bot[1:])
    t2[1:] = np.maximum(t2[1:], top[:-1]); t2[:-1] = np.maximum(t2[:-1], top[1:])
    return (r, n, b2, t2, bx, by, mx - bx, my - by)


def shelf_plan(job, shp, seed, budget=3.0):
    """分排：零件按真实上下轮廓从上往下“落”到板上（长条首尾相接、楔形条反向咬合），先少用板。
    返回各板放置，失败返回 None。"""
    import random
    import numpy as np
    from numpy.lib.stride_tricks import sliding_window_view as swv
    L, H, rot = job['L'], job['H'], job['rot']
    rng = random.Random(seed)
    rs = [0.0, 90.0, 180.0, 270.0] if rot is None else ([r for r in rot if abs(r % 90) < 1e-6] or [0.0])
    prof = {}
    for i, g in shp.items():
        o = [p for p in (_profile(g, r) for r in rs) if p[6] <= L - 1 and p[7] <= H + 1e-6]
        if not o: return None
        prof[i] = o
    area = {i: shp[i].area for i in shp}
    items = [i for i, c in job['qty'].items() for _ in range(c)]
    NL = int(math.floor(L))
    keys = [lambda i: -area[i], lambda i: -max(p[6] for p in prof[i]), lambda i: -min(p[7] for p in prof[i])]

    def put(sky, o):
        r, n, bot, top, bx, by, w, h = o
        npos = int(math.floor(L - w)) + 1
        if npos <= 0: return None
        y = np.maximum((swv(sky, n)[:npos] - bot).max(1), 0.0)
        tt = y + h
        k = int(np.argmin(tt))
        if tt[k] > H + 1e-6: return None
        return float(tt[k]), k, float(y[k])

    best = None; t_end = time.time() + budget; n_try = 0
    while n_try < 3 or time.time() < t_end:
        n_try += 1
        noise = 0 if n_try <= 3 else 0.2
        kf = keys[(n_try - 1) % 3]
        order = sorted(items, key=lambda i: kf(i) * (1 + noise * rng.random()))
        bins = []                                                # [天际线, 放置]
        for i in order:
            done = False
            for b in bins:
                cand = [(put(b[0], o), o) for o in prof[i]]
                cand = [(c, o) for c, o in cand if c]
                if not cand: continue
                (tt, x0, y), o = min(cand, key=lambda t: t[0][0])
                r, n, bot, top, bx, by, w, h = o
                b[0][x0:x0 + n] = np.maximum(b[0][x0:x0 + n], y + top)
                b[1].append((i, r, x0 - bx, y - by)); done = True; break
            if not done:
                sky = np.zeros(NL + 1); o = prof[i][0]
                r, n, bot, top, bx, by, w, h = o
                sky[0:n] = top; bins.append([sky, [(i, r, -bx, -by)]])
        sc = (len(bins), -sum((sum(area[p[0]] for p in b[1]) / (L * H)) ** 2 for b in bins))
        if best is None or sc < best[0]: best = (sc, [b[1] for b in bins])
    return best[1]


def backfill(job, outq, sheets, shp, solve):
    """回填：从利用率最低的板开始，把它的零件逐个塞进别的板；塞空了就删掉这张板。"""
    L, H, T = job['L'], job['H'], job['time']
    area = {i: shp[i].area for i in shp}
    use = lambda pl: sum(area[p[0]] for p in pl)
    t_try = max(4, T / 2); t_end = time.time() + min(300, max(90, T * len(sheets) * 1.5))
    tried = set(); sheets = [list(pl) for pl in sheets]
    outq.put(('note', '回填：尝试把零件挪到其他板上，减少用板数…'))
    while time.time() < t_end:
        order = sorted(range(len(sheets)), key=lambda k: use(sheets[k]))
        src = next((k for k in order if id(sheets[k]) not in tried), None)
        if src is None: break
        tried.add(id(sheets[src]))
        for item in sorted(sheets[src], key=lambda p: -area[p[0]]):
            if time.time() > t_end: break
            for j in sorted((j for j in range(len(sheets)) if j != src), key=lambda j: use(sheets[j])):
                if use(sheets[j]) + area[item[0]] > 0.97 * L * H: continue
                sub = {}
                for p in sheets[j] + [item]: sub[p[0]] = sub.get(p[0], 0) + 1
                try: w, p2 = solve(sub, H, t_try, j, '回填')
                except Exception: continue
                if w <= L + 1e-6:
                    sheets[j] = p2; tried.discard(id(sheets[j]))
                    sheets[src].remove(item)
                    outq.put(('sheet', j, p2)); outq.put(('sheet', src, sheets[src]))
                    break
        if not sheets[src]:
            del sheets[src]; outq.put(('drop', src)); tried = {t for t in tried}
    return sheets


def corner_free(job, outq, shp, solve, geo):
    """靠角：不限板子，零件往左下角挤，取占用矩形面积最小的方案。"""
    rem = dict(job['qty']); rot = job['rot']; T = job['time']
    A = sum(shp[i].area * c for i, c in rem.items())
    hmin = 0.0
    for i in rem:
        b = shp[i].bounds; w, h = b[2] - b[0], b[3] - b[1]
        if rot is not None and all(abs(r % 180) < 1e-6 for r in rot): hi = h
        elif rot is None: hi = min(w, h)
        else:
            hi = min((lambda bb: bb[3] - bb[1])(affinity.rotate(shp[i], r, origin=(0, 0)).bounds) for r in rot)
        hmin = max(hmin, hi)
    s0 = math.sqrt(A / 0.8)                         # 接近正方形的高度
    hs = sorted({round(max(hmin * 1.002, s0 * f), 1) for f in (0.55, 0.7, 0.85, 1.0, 1.2, 1.45)})
    def area(pl):
        gs = geo(pl); return max(g.bounds[2] for g in gs) * max(g.bounds[3] for g in gs)
    best = None; tt = max(3, T * 0.6 / len(hs))
    for j, h in enumerate(hs):
        w, pl = solve(rem, h, tt, 0, f'试排 {j + 1}/{len(hs)}')
        a = area(pl)
        if best is None or a < best[0]: best = (a, h, pl)
    a, h, pl = best
    w, p2 = solve(rem, h, max(3, T * 0.4), 0, '精排')
    if area(p2) < a: pl = p2
    outq.put(('sheet', 0, pl)); outq.put(('done', [pl]))


class NestError(Exception): pass


class _TagQ:
    """多个进程共用一个队列：消息前加进程编号；非 0 号进程只回报结果和错误。"""
    def __init__(self, q, wid): self.q, self.wid = q, wid
    def put(self, m):
        if self.wid == 0 or m[0] in ('done', 'error'): self.q.put((self.wid,) + tuple(m))


def worker(job, outq, wid=0):
    outq = _TagQ(outq, wid)
    try: run_job(job, outq)
    except NestError as ex: outq.put(('error', str(ex)))
    except Exception: outq.put(('error', traceback.format_exc()))
