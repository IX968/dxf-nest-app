"""航模 DXF 排版 v0.2.0 —— 界面。python nest_gui.py"""
import sys, os, re, math, time, colorsys
import multiprocessing as mp
from PySide6.QtCore import Qt, QTimer, QSettings, QSize, QRectF
from PySide6.QtCore import QItemSelectionModel
from PySide6.QtGui import QPainterPath, QPen, QBrush, QColor, QTransform, QPainter, QPixmap, QIcon, QFont, QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QSpinBox, QDoubleSpinBox, QComboBox, QLabel, QGroupBox,
    QFormLayout, QRadioButton, QButtonGroup, QCheckBox, QFileDialog, QMessageBox, QGraphicsView,
    QGraphicsScene, QGraphicsPathItem, QGraphicsRectItem, QGraphicsSimpleTextItem, QSplitter, QAbstractItemView,
    QTabWidget, QListWidget, QListWidgetItem, QGraphicsEllipseItem, QMenu, QGraphicsItem, QDialog, QTextBrowser, QScrollArea)
from shapely.geometry import Polygon
from shapely import affinity
import nest_core as N
import nest_post as P
import nest_hole as HL

APP = '航模DXF排版 v0.2.0'
PRESETS = ['1000 × 100', '900 × 900', '300 × 200', '400 × 300', '600 × 300', '600 × 400', '900 × 600', '1200 × 600', '自定义']
ROTS = [('90°', [0.0, 90.0, 180.0, 270.0]), ('45°', [45.0 * k for k in range(8)]),
        ('15°', [15.0 * k for k in range(24)]), ('任意角度', None), ('仅 180°', [0.0, 180.0]), ('不旋转', [0.0])]
HELP_HTML = """
<h3>使用流程</h3>
<ol>
<li>点击“导入 DXF…”选择文件（可多选），或将 DXF 文件、文件夹拖入窗口。</li>
<li>在“导入预览”中检查图形。“问题”页列出导入时发现的异常，逐项处理。</li>
<li>在“零件”页核对数量，设置板材、边距、间距和旋转方式。</li>
<li>需要把小零件排进大零件的镂空时，在“镂空”页把确认是废料的孔设为“可排入”（见下方“镂空排版”）。</li>
<li>选择排版方式，点击“开始排版”。计算过程实时显示，完成后停在最终结果。</li>
<li>点击“导出 DXF…”。多张板分别保存为“文件名_第N张.dxf”。</li>
</ol>

<h3>零件识别规则</h3>
<ul>
<li>每个最外层闭合轮廓识别为一个零件，轮廓内的孔、刻字、刻线随零件一起移动。</li>
<li>同一文件中形状相同的零件自动合并，数量可在“零件”页修改，数量为 0 的零件不参与排版。</li>
<li>端点间距小于 0.05 mm 的线段视为相连。</li>
<li>重复的线条自动删除，只保留一条。</li>
<li>导出时保留原图元（圆弧、样条不转换为折线），单位为 mm。切割线和刻线的图层、颜色见下方“后处理”。</li>
<li>零件轮廓内的文字（TEXT / MTEXT）默认不导出，需要时勾选“导出零件上的文字”。用线条画成的刻字不受此选项影响，始终导出。</li>
</ul>

<h3>问题类型</h3>
<table cellpadding="4">
<tr><td><b style="color:#d02020">未闭合轮廓</b></td><td>轮廓有缺口（3 mm 以内），常见于留了微连接的零件。图中红色显示，圆圈标出缺口位置。程序不修补缺口。选择“作为零件”后按闭合外形参与排版，导出时线条保持原样；选择“忽略”则不参与排版。</td></tr>
<tr><td><b style="color:#d02020">散线</b></td><td>不属于任何零件的线条，例如辅助线、标注线。</td></tr>
<tr><td><b style="color:#d02020">零件外的文字</b></td><td>不在任何零件轮廓内的文字，只能忽略。</td></tr>
<tr><td><b style="color:#d02020">极窄区域</b></td><td>两条线几乎重合（间距小于 0.3 mm）围成的窄条，常见于两个零件边线贴在一起。
该区域不作为零件，相关线条归入两侧零件，只能忽略。</td></tr>
<tr><td><b style="color:#e08020">板框</b></td><td>包住零件的矩形外框（橙色虚线），默认删除。一个文件里有多张板时，每张板的板框都会识别。如果它其实是零件，选择“作为零件”。</td></tr>
</table>
<p>未处理的问题在开始排版时按“忽略”处理。带微连接的文件可点“未闭合全部作为零件”一次放行。</p>

<h3>选择与操作</h3>
<ul>
<li>预览中单击选中零件或问题线条，Ctrl + 单击可多选。</li>
<li>按住左键拖出矩形框，完整框住的对象被选中。</li>
<li>图中的选择与左侧列表同步，列表中也可用 Shift / Ctrl 多选。</li>
<li>Delete 键或“删除所选”：删除选中的零件，或忽略选中的问题。</li>
<li>右键菜单：删除、恢复已删除零件、忽略、作为零件、恢复为未处理、镂空可排入 / 保留。已删除的零件在预览中显示为灰色。</li>
<li>滚轮缩放；按住中键拖动平移（排版结果中左键也可拖动）；双击显示全部。</li>
</ul>

<h3>排版方式</h3>
<ul>
<li><b>整板排版</b>：按设定的板材尺寸排列，一张放不下时自动增加板数，尽量少用板。</li>
<li><b>靠角排版</b>：不设板材尺寸，零件向左下角紧凑排列，使占用矩形面积最小，便于在整板或余料的一角切割。结果显示占用尺寸（长 × 宽）。</li>
</ul>

<h3>镂空排版</h3>
<ul>
<li>“镂空”页列出能排进别的零件的孔：会切穿、孔里是空的、大小放得下要排的零件。预览中青色虚线为“保留”，绿色为“可排入”。</li>
<li>默认全部<b>保留</b>，不往里排：孔里切下来的料可能是要用的零件，例如直接从机身板上切下来的舱盖，形状和减重孔一模一样，程序分不出来。
确认是废料的孔，选中后点“可排入”（也可在预览中单击、框选后右键）；全是减重孔的文件可点“全部可排入”。同一种零件的每一件都按同样设置处理。</li>
<li>以下孔自动排除，不会列出：孔里有东西（小孔、刻线、文字或小件）；轮廓是刻线、不会切穿；太小，放不下任何零件。</li>
<li>开始排版时先把小零件排进“可排入”的孔（大的优先，离孔边、零件之间都保持设定的零件间距），再按整体排版。
结果图中用绿色虚线标出排进了零件的孔，状态栏显示排进镂空的件数。</li>
<li><b>切割顺序</b>：镂空里的零件必须先切，最后再切孔的轮廓，否则那块料先掉下去或移位，里面的零件就切歪了。
导出文件中镂空里的零件写在前面；激光软件里请打开“先切内部形状”（LightBurn：Cut inner shapes first），或关闭路径优化按文件顺序切。</li>
</ul>

<h3>参数</h3>
<ul>
<li><b>边距 / 离角距离</b>：零件与板边（或角部两条边）的最小距离。</li>
<li><b>零件间距</b>：相邻零件之间的最小距离，可按激光切缝宽度设置。</li>
<li><b>零件旋转</b>：90° 适合大多数板件；45°、15°、任意角度适合异形件，需要更长计算时间（建议为 90° 的 2～3 倍）。</li>
<li><b>计算时间</b>：时间越长结果越好，30～60 秒通常足够。</li>
</ul>

<h3>后处理（排版完成后才能设置，结果图中可直接看到）</h3>
<ul>
<li><b>微连接</b>：在零件外轮廓的直边上留出小段不切，零件切完后仍连在板上。位置离拐角至少 2 mm；
数量按周长分配（“约每 N mm 一个”），小零件 1 个。本身带缺口的零件不再添加。
全水口：该段不切；半水口：该段放在红色“半水口”图层，切割时自行调低参数。结果图中黑点为全水口，红点为半水口。</li>
<li><b>板号</b>：每张板的空角刻一个字母，第 1 张 A，第 2 张 B，依次类推，蓝色“板号”图层。</li>
<li><b>零件编号</b>：每张板上的零件从上到下、从左到右编号，编号刻在零件旁的空隙（离零件至少 2 mm），每个编号都有小箭头指向对应零件，
绿色“零件编号”图层。零件间距太小时部分零件可能放不下编号，会在左侧提示。</li>
<li>零件外轮廓，以及和外轮廓图层、颜色都相同的线（如减重孔），统一放在黑色“切割”图层。
其他线保持原图层和颜色，例如用不同图层或不同颜色画的刻线——和外轮廓同图层、但颜色不同的线也按刻线处理，不会被切穿。字体为单线字。</li>
</ul>

<h3>当前版本限制</h3>
<ul>
<li>镂空只嵌套一层：有“可排入”孔的零件，本身不会被排进别的孔；排进孔里的零件，它自己的孔不再往里排。</li>
<li>不考虑木纹方向（轻木纹理方向计划在后续版本加入）。</li>
<li>微连接只加在直线边上；全部由圆弧或样条组成的零件不加，会在左侧提示。</li>
<li>点击“停止”会放弃本次计算结果。</li>
<li>“重排”：换一个随机起点重新计算，与上一次结果比较，自动保留更好的（整板排版按板数少、零件集中优先；靠角排版按占用面积小优先）。可以多次点击。</li>
<li>计算时会同时用几种不同的排法各算一遍，最后自动采用用板最少的结果。</li>
<li>整板排版排完后会自动“回填”：从利用率最低的板开始，把零件挪到其他板上，挪空的板直接去掉。</li>
</ul>
"""


SHEET_GAP = 60          # 画面里各张板之间的间距 mm


def poly_path(g, isl=()):
    p = QPainterPath(); p.setFillRule(Qt.OddEvenFill)
    for r in [g.exterior] + list(g.interiors) + [r for q in isl for r in [q.exterior] + list(q.interiors)]:
        c = list(r.coords); p.moveTo(*c[0])
        for x, y in c[1:]: p.lineTo(x, y)
        p.closeSubpath()
    return p


def color(k):
    r, g, b = colorsys.hsv_to_rgb((k * 0.61803) % 1.0, 0.55, 0.95)
    return QColor(int(r * 255), int(g * 255), int(b * 255))


def disp_path(p):
    """零件在预览和结果里的样子：刻线框不画成孔；切穿的孔、镂空里的小件照常。"""
    hs = HL.part_holes(p)
    g = Polygon(p.poly.exterior, [r for r, h in zip(p.poly.interiors, hs) if not h['mark']])
    return poly_path(g, p.islands)


def thumb(p, col, s=44):
    pm = QPixmap(s, s); pm.fill(Qt.transparent)
    b = p.poly.bounds; k = (s - 6) / max(b[2] - b[0], b[3] - b[1], 1e-6)
    qp = QPainter(pm); qp.setRenderHint(QPainter.Antialiasing)
    qp.translate(s / 2, s / 2); qp.scale(k, -k); qp.translate(-(b[0] + b[2]) / 2, -(b[1] + b[3]) / 2)
    pen = QPen(col.darker(160)); pen.setCosmetic(True); qp.setPen(pen); qp.setBrush(col)
    qp.drawPath(disp_path(p)); qp.end()
    return QIcon(pm)


class InfoLabel(QLabel):
    """自动换行的提示文字：内容变了就按宽度重新算高度，避免和下面的控件重叠。"""
    def _fit(self):
        w = max(self.width(), 300)
        h = self.fontMetrics().boundingRect(0, 0, w, 10000, Qt.TextWordWrap, self.text()).height() + 6 if self.text() else 0
        if self.minimumHeight() != h:
            self.setMinimumHeight(h); self.updateGeometry()
            if self.parentWidget() and self.parentWidget().layout(): self.parentWidget().layout().activate()

    def setText(self, t):
        super().setText(t); self._fit()

    def resizeEvent(self, e):
        super().resizeEvent(e); self._fit()


class View(QGraphicsView):
    def __init__(self):
        super().__init__()
        self.setScene(QGraphicsScene(self)); self.setRenderHint(QPainter.Antialiasing)
        self.setDragMode(QGraphicsView.ScrollHandDrag); self.setBackgroundBrush(QColor('#2b2f36'))
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff); self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.scale(1, -1); self.auto = True; self._pan = None
        self.setRubberBandSelectionMode(Qt.ContainsItemShape)

    def mousePressEvent(self, e):
        if e.button() == Qt.MiddleButton:
            self._pan = e.position(); self.setCursor(Qt.ClosedHandCursor); return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._pan is not None:
            d = e.position() - self._pan; self._pan = e.position(); self.auto = False
            for sb, v in ((self.horizontalScrollBar(), d.x()), (self.verticalScrollBar(), d.y())): sb.setValue(sb.value() - int(v))
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MiddleButton and self._pan is not None:
            self._pan = None; self.unsetCursor(); return
        super().mouseReleaseEvent(e)

    def wheelEvent(self, e):
        f = 1.2 if e.angleDelta().y() > 0 else 1 / 1.2
        self.scale(f, f); self.auto = False

    def fit(self):
        r = self.scene().itemsBoundingRect()
        if r.isValid():
            r = r.adjusted(-20, -20, 20, 20); self.fit_all(); self.fitInView(r, Qt.KeepAspectRatio)

    def fit_all(self):
        r = self.scene().itemsBoundingRect()
        if r.isValid():
            m = max(r.width(), r.height()) * 3 + 500           # 留足余量，中键随时能拖动
            self.scene().setSceneRect(r.adjusted(-m, -m, m, m))

    def mouseDoubleClickEvent(self, e): self.auto = True; self.fit()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.auto: self.fit()


class Main(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP); self.resize(1400, 860); self.setAcceptDrops(True)
        # 每个文件：{'fd': FileData, 'opts': 读取选项, 'qty': 改过的数量, 'drop': 删掉的零件, 'holes': 设为可排入的孔}
        self.files = []
        self.vmode = 'preview'; self.sel_parts = set(); self.sel_issues = set(); self.sel_holes = set()
        self._sync = False; self._pitems = []; self._hrows = []; self.phase = None; self.hole_cache = None
        self.comps = {}; self.solid = {}; self.n_in_holes = 0        # 组合件、实际占用形状、排进镂空的件数
        self.proc = None; self.q = None; self.sheets = []; self.job = None
        self.cfg = QSettings('CUADC', 'DXFNest')

        # ---- 左侧
        left = QWidget(); lv = QVBoxLayout(left)
        hb = QHBoxLayout()
        b = QPushButton('帮助'); b.clicked.connect(self.show_help); b.setMaximumWidth(60); hb.addWidget(b)
        b = QPushButton('导入 DXF…'); b.clicked.connect(self.pick); hb.addWidget(b)
        b = QPushButton('删除所选'); b.clicked.connect(self.remove); hb.addWidget(b)
        b = QPushButton('清空'); b.clicked.connect(self.clear); hb.addWidget(b)
        lv.addLayout(hb)
        lv.addWidget(QLabel('支持多选文件，也可将 DXF 文件或文件夹拖入窗口。'))
        self.tab = QTableWidget(0, 4); self.tab.setHorizontalHeaderLabels(['零件', '尺寸 mm', '数量', '来源文件'])
        self.tab.setIconSize(QSize(40, 40))
        self.tab.verticalHeader().setDefaultSectionSize(46); self.tab.verticalHeader().setVisible(False)
        h = self.tab.horizontalHeader(); h.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(1, QHeaderView.ResizeToContents); h.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(3, QHeaderView.Stretch)
        self.tab.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tab.itemSelectionChanged.connect(self.part_selected)
        self.tabs = QTabWidget(); self.tabs.addTab(self.tab, '零件')
        iw = QWidget(); iv = QVBoxLayout(iw); iv.setContentsMargins(4, 4, 4, 4)
        iv.addWidget(QLabel('红色为未处理项。单击定位到图中位置，Shift/Ctrl 可多选。\n未闭合线条不做修补，作为零件放行后按原样导出。'))
        self.ilist = QListWidget(); self.ilist.itemSelectionChanged.connect(self.issue_selected); iv.addWidget(self.ilist, 1)
        self.ilist.setSelectionMode(QAbstractItemView.ExtendedSelection)
        hb2 = QHBoxLayout()
        self.b_ign = QPushButton('忽略'); self.b_ign.clicked.connect(lambda: self.issue_act('ignore'))
        self.b_part = QPushButton('作为零件'); self.b_part.clicked.connect(lambda: self.issue_act('part'))
        self.b_todo = QPushButton('恢复'); self.b_todo.clicked.connect(lambda: self.issue_act('todo'))
        self.b_all = QPushButton('其余全部忽略'); self.b_all.clicked.connect(self.issue_all)
        for x in (self.b_ign, self.b_part, self.b_todo): hb2.addWidget(x)
        iv.addLayout(hb2)
        hb3 = QHBoxLayout()
        self.b_open = QPushButton('未闭合全部作为零件'); self.b_open.clicked.connect(self.open_all)
        hb3.addWidget(self.b_open); hb3.addWidget(self.b_all); iv.addLayout(hb3)
        self.tabs.addTab(iw, '问题')
        hw = QWidget(); hv2 = QVBoxLayout(hw); hv2.setContentsMargins(4, 4, 4, 4)
        lb = QLabel('这里列出能排进别的零件的孔（会切穿、孔里是空的、放得下零件）。默认“保留”：孔里切下来的料'
                    '可能是要用的零件（如舱盖）。确认是废料的孔设为“可排入”，开始排版时会把小零件排进去。')
        lb.setWordWrap(True); hv2.addWidget(lb)
        self.hlist = QListWidget(); self.hlist.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.hlist.itemSelectionChanged.connect(self.hole_selected); hv2.addWidget(self.hlist, 1)
        self.hnote = QLabel(''); self.hnote.setWordWrap(True); self.hnote.setStyleSheet('color:#777'); hv2.addWidget(self.hnote)
        hb4 = QHBoxLayout()
        self.b_hfill = QPushButton('可排入'); self.b_hfill.clicked.connect(lambda: self.hole_act('fill'))
        self.b_hkeep = QPushButton('保留'); self.b_hkeep.clicked.connect(lambda: self.hole_act('keep'))
        self.b_hall = QPushButton('全部可排入'); self.b_hall.clicked.connect(lambda: self.hole_all('fill'))
        self.b_hnone = QPushButton('全部保留'); self.b_hnone.clicked.connect(lambda: self.hole_all('keep'))
        for x in (self.b_hfill, self.b_hkeep, self.b_hall, self.b_hnone): hb4.addWidget(x)
        hv2.addLayout(hb4)
        self.tabs.addTab(hw, '镂空')
        lv.addWidget(self.tabs, 1)
        self.info = InfoLabel(''); self.info.setWordWrap(True); self.info.setStyleSheet('color:#b06000')
        lv.addWidget(self.info)

        gb = QGroupBox('板材'); f = QFormLayout(gb)
        self.preset = QComboBox(); self.preset.addItems(PRESETS)
        self.sw = QDoubleSpinBox(); self.sh = QDoubleSpinBox()
        for s in (self.sw, self.sh): s.setRange(20, 5000); s.setDecimals(0); s.setSuffix(' mm')
        hs = QHBoxLayout(); hs.addWidget(QLabel('长')); hs.addWidget(self.sw); hs.addWidget(QLabel('宽')); hs.addWidget(self.sh)
        f.addRow('板材尺寸', self.preset); f.addRow('', hs)
        self.margin = QDoubleSpinBox(); self.margin.setRange(0, 50); self.margin.setSuffix(' mm'); self.margin.setDecimals(1)
        self.gap = QDoubleSpinBox(); self.gap.setRange(0, 20); self.gap.setSuffix(' mm'); self.gap.setDecimals(1)
        self.margin_lbl = QLabel('边距'); f.addRow(self.margin_lbl, self.margin); f.addRow('零件间距', self.gap)
        self.rot = QComboBox(); self.rot.addItems([r[0] for r in ROTS]); f.addRow('零件旋转', self.rot)
        lv.addWidget(gb)

        gb = QGroupBox('排版方式'); v = QVBoxLayout(gb)
        self.m_fill = QRadioButton('整板排版：按板材尺寸排列，超出时自动分张')
        self.m_corner = QRadioButton('靠角排版：不限板材，零件向左下角紧凑排列')
        self.mode = QButtonGroup(self); self.mode.addButton(self.m_fill); self.mode.addButton(self.m_corner)
        v.addWidget(self.m_fill); v.addWidget(self.m_corner)
        ht = QHBoxLayout(); self.tlbl = QLabel('单张计算时间'); ht.addWidget(self.tlbl)
        self.tsec = QSpinBox(); self.tsec.setRange(5, 600); self.tsec.setSuffix(' 秒'); ht.addWidget(self.tsec); ht.addStretch()
        v.addLayout(ht)
        self.frame = QCheckBox('导出板框（“板框”图层，切割前请关闭该图层）'); v.addWidget(self.frame)
        self.texts = QCheckBox('导出零件上的文字（TEXT / MTEXT）'); v.addWidget(self.texts)
        lv.addWidget(gb)

        gb = QGroupBox('后处理（排版完成后可用）'); f2 = QFormLayout(gb); self.post_box = gb; gb.setEnabled(False)
        self.tab_on = QCheckBox('微连接'); self.tab_len = QDoubleSpinBox(); self.tab_len.setRange(0.2, 10); self.tab_len.setDecimals(1)
        self.tab_len.setSuffix(' mm'); self.tab_sp = QSpinBox(); self.tab_sp.setRange(30, 2000); self.tab_sp.setSuffix(' mm')
        self.tab_mode = QComboBox(); self.tab_mode.addItems(['全水口（不切）', '半水口（单独图层）'])
        h1 = QHBoxLayout(); h1.addWidget(QLabel('长度')); h1.addWidget(self.tab_len); h1.addWidget(QLabel('约每')); h1.addWidget(self.tab_sp)
        h1.addWidget(QLabel('一个'))
        f2.addRow(self.tab_on, self.tab_mode); f2.addRow('', h1)
        self.mark_on = QCheckBox('板号'); self.mark_h = QDoubleSpinBox(); self.mark_h.setRange(2, 50); self.mark_h.setSuffix(' mm 字高')
        f2.addRow(self.mark_on, self.mark_h)
        self.num_on = QCheckBox('零件编号'); self.num_h = QDoubleSpinBox(); self.num_h.setRange(1, 20); self.num_h.setDecimals(1)
        self.num_h.setSuffix(' mm 字高'); f2.addRow(self.num_on, self.num_h)
        f2.addRow(QLabel('切割线：“切割”层（黑色）；半水口：红色；板号：蓝色；零件编号：绿色。'))
        lv.addWidget(gb)
        for w_ in (self.tab_on, self.mark_on, self.num_on): w_.toggled.connect(self.post_changed)
        for w_ in (self.tab_len, self.tab_sp, self.mark_h, self.num_h): w_.valueChanged.connect(self.post_changed)
        self.tab_mode.currentIndexChanged.connect(self.post_changed)
        self.post = None

        hb = QHBoxLayout()
        self.bstart = QPushButton('开始排版'); self.bstart.clicked.connect(self.start)
        self.bstop = QPushButton('停止'); self.bstop.clicked.connect(self.stop); self.bstop.setEnabled(False)
        self.bexp = QPushButton('导出 DXF…'); self.bexp.clicked.connect(self.export); self.bexp.setEnabled(False)
        self.bredo = QPushButton('重排'); self.bredo.clicked.connect(self.redo); self.bredo.setEnabled(False)
        self.bredo.setToolTip('换一个随机起点重新计算，和上一次比较，保留更好的结果')
        for x in (self.bstart, self.bredo, self.bstop, self.bexp): x.setMinimumHeight(36); hb.addWidget(x)
        self.bstart.setStyleSheet('font-weight:bold')
        lv.addLayout(hb)

        # ---- 右侧
        self.view = View()
        right = QWidget(); rv = QVBoxLayout(right); rv.setContentsMargins(0, 0, 0, 0)
        hv = QHBoxLayout(); hv.setContentsMargins(4, 4, 4, 0)
        self.bv_pre = QPushButton('导入预览'); self.bv_res = QPushButton('排版结果')
        self.vgrp = QButtonGroup(self)
        for k, x in enumerate((self.bv_pre, self.bv_res)):
            x.setCheckable(True); x.setMinimumWidth(110); self.vgrp.addButton(x, k); hv.addWidget(x)
        self.bv_pre.setChecked(True); self.vgrp.idClicked.connect(lambda k: self.set_show('preview' if k == 0 else 'result'))
        hv.addStretch(); rv.addLayout(hv)
        rv.addWidget(self.view, 1)
        self.view.scene().selectionChanged.connect(self.scene_sel)
        self.view.setContextMenuPolicy(Qt.CustomContextMenu); self.view.customContextMenuRequested.connect(self.ctx_menu)
        QShortcut(QKeySequence.Delete, self, activated=self.delete_sel)
        QShortcut(QKeySequence.HelpContents, self, activated=self.show_help)
        self.stat = QLabel('导入 DXF 后检查预览，再点击“开始排版”。')
        self.stat.setStyleSheet('padding:4px; font-size:13px'); rv.addWidget(self.stat)
        lsc = QScrollArea(); lsc.setWidget(left); lsc.setWidgetResizable(True); lsc.setMinimumWidth(470)
        sp = QSplitter(); sp.addWidget(lsc); sp.addWidget(right); sp.setSizes([500, 900])
        self.setCentralWidget(sp)

        self.load_cfg()
        self.m_fill.toggled.connect(self.mode_changed); self.mode_changed()
        self.preset.currentIndexChanged.connect(self.preset_changed)
        for s in (self.sw, self.sh): s.valueChanged.connect(self.size_edited)
        self.gap.valueChanged.connect(self.gap_changed)
        self.timer = QTimer(self); self.timer.timeout.connect(self.poll)
        self.draw_empty()

    def show_help(self):
        d = QDialog(self); d.setWindowTitle('帮助'); d.resize(720, 760)
        v = QVBoxLayout(d); tb = QTextBrowser(); tb.setHtml(HELP_HTML); v.addWidget(tb)
        b = QPushButton('关闭'); b.clicked.connect(d.accept); v.addWidget(b, 0, Qt.AlignRight)
        d.exec()

    # ---------------- 设置
    def load_cfg(self):
        c = self.cfg
        self.sw.setValue(float(c.value('sw', 600))); self.sh.setValue(float(c.value('sh', 300)))
        self.margin.setValue(float(c.value('margin', 3))); self.gap.setValue(float(c.value('gap', 1)))
        names = [r[0] for r in ROTS]; rn = c.value('rotname', names[0])
        self.rot.setCurrentIndex(names.index(rn) if rn in names else 0); self.tsec.setValue(int(c.value('time', 30)))
        (self.m_corner if c.value('mode', 'fill') == 'corner' else self.m_fill).setChecked(True)
        self.frame.setChecked(c.value('frame', 'false') == 'true')
        self.texts.setChecked(c.value('texts', 'false') == 'true')
        b_ = lambda k: c.value(k, 'false') == 'true'
        for w_, k in ((self.tab_on, 'tab_on'), (self.mark_on, 'mark_on'), (self.num_on, 'num_on')): w_.blockSignals(True); w_.setChecked(b_(k)); w_.blockSignals(False)
        for w_, k, d in ((self.tab_len, 'tab_len', 1.0), (self.tab_sp, 'tab_sp', 150), (self.mark_h, 'mark_h', 8.0), (self.num_h, 'num_h', 3.0)):
            w_.blockSignals(True); w_.setValue(type(w_.value())(float(c.value(k, d)))); w_.blockSignals(False)
        self.tab_mode.blockSignals(True); self.tab_mode.setCurrentIndex(int(c.value('tab_mode', 0))); self.tab_mode.blockSignals(False)
        self.sync_preset()

    def save_cfg(self):
        c = self.cfg
        for k, v in (('sw', self.sw.value()), ('sh', self.sh.value()), ('margin', self.margin.value()),
                     ('gap', self.gap.value()), ('rotname', self.rot.currentText()), ('time', self.tsec.value()),
                     ('mode', 'corner' if self.m_corner.isChecked() else 'fill'),
                     ('frame', 'true' if self.frame.isChecked() else 'false'),
                     ('texts', 'true' if self.texts.isChecked() else 'false'),
                     ('tab_on', 'true' if self.tab_on.isChecked() else 'false'), ('mark_on', 'true' if self.mark_on.isChecked() else 'false'),
                     ('num_on', 'true' if self.num_on.isChecked() else 'false'), ('tab_len', self.tab_len.value()),
                     ('tab_sp', self.tab_sp.value()), ('mark_h', self.mark_h.value()), ('num_h', self.num_h.value()),
                     ('tab_mode', self.tab_mode.currentIndex())): c.setValue(k, v)

    def corner(self): return self.m_corner.isChecked()

    def mode_changed(self, *_):
        self.mode_ui()
        if not self.running(): self.reset_result()

    def mode_ui(self):
        on = not self.corner()
        for w in (self.preset, self.sw, self.sh): w.setEnabled(on)
        self.margin_lbl.setText('边距' if on else '离角距离')
        self.tlbl.setText('单张计算时间' if on else '计算时间')

    def sync_preset(self):
        t = f'{self.sw.value():g} × {self.sh.value():g}'
        self.preset.blockSignals(True)
        self.preset.setCurrentIndex(PRESETS.index(t) if t in PRESETS else len(PRESETS) - 1)
        self.preset.blockSignals(False)

    def preset_changed(self, i):
        t = PRESETS[i]
        if '×' in t:
            a, b = (float(x) for x in t.split('×'))
            for s, v in ((self.sw, a), (self.sh, b)): s.blockSignals(True); s.setValue(v); s.blockSignals(False)
        self.draw_empty()

    def size_edited(self): self.sync_preset(); self.draw_empty()

    # ---------------- 导入
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls(): e.acceptProposedAction()

    def dropEvent(self, e):
        fs = [u.toLocalFile() for u in e.mimeData().urls()]
        out = []
        for f in fs:
            if os.path.isdir(f):
                out += [os.path.join(f, x) for x in sorted(os.listdir(f)) if x.lower().endswith('.dxf')]
            elif f.lower().endswith('.dxf'): out.append(f)
        self.add_files(out)

    def pick(self):
        fs, _ = QFileDialog.getOpenFileNames(self, '选择 DXF 文件（可多选）', self.cfg.value('dir', ''), 'DXF 文件 (*.dxf)')
        if fs: self.cfg.setValue('dir', os.path.dirname(fs[0])); self.add_files(fs)

    @property
    def parts(self):
        return [p for F in self.files for p in F['fd'].parts if self.pkey(p) not in F['drop']]

    @staticmethod
    def pkey(p): return (round(p.org[0], 1), round(p.org[1], 1), round(p.area, 1))

    def fobj(self, fn): return next(F for F in self.files if F['fd'].fn == fn)

    def add_files(self, fs):
        if self.running(): return
        msgs = []; have = {os.path.normcase(F['fd'].fn) for F in self.files}
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            for f in fs:
                if os.path.normcase(f) in have: continue
                opts = {'ignore': set(), 'as_part': set(), 'keep_frame': False}
                try: fd = N.load_dxf(f, opts)
                except Exception as ex: msgs.append(f'{os.path.basename(f)}：无法读取（{ex}）'); continue
                if not fd.parts and not fd.issues: msgs.append(f'{fd.name}：未找到可用图形')
                if fd.notes: msgs.append(f'{fd.name}：' + '；'.join(fd.notes))
                self.files.append({'fd': fd, 'opts': opts, 'qty': {}, 'drop': set(), 'holes': {}}); have.add(os.path.normcase(f))
        finally: QApplication.restoreOverrideCursor()
        self.info.setText('\n'.join(msgs)); self.refresh_all()
        if any(i.state == 'todo' for F in self.files for i in F['fd'].issues): self.tabs.setCurrentIndex(1)

    def refresh_all(self):
        self.sel_parts = set(); self.sel_issues = set(); self.sel_holes = set()
        self.fill_table(); self.fill_issues(); self.set_show('preview'); self.reset_result()

    def reload(self, F):
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try: F['fd'] = N.load_dxf(F['fd'].fn, F['opts'])
        finally: QApplication.restoreOverrideCursor()
        for p in F['fd'].parts:
            if self.pkey(p) in F['qty']: p.qty = F['qty'][self.pkey(p)]

    # ---------------- 问题列表
    def all_issues(self): return [(F, i) for F in self.files for i in F['fd'].issues]

    def fill_issues(self, keep=None):
        self._sync = True; self.ilist.blockSignals(True); self.ilist.clear()
        st = {'todo': '未处理', 'ignore': '已忽略', 'part': '已作为零件'}
        for r, (F, i) in enumerate(self.all_issues()):
            s = st[i.state] if not (i.kind == 'frame' and i.state != 'part') else ('已删除' if i.state == 'ignore' else '未处理（默认删除）')
            if i.kind == 'thin': s = '已排除'
            it = QListWidgetItem(f'{F["fd"].name}  |  {i.text()}  |  {s}')
            it.setForeground(QColor({'todo': '#d02020', 'ignore': '#888888', 'part': '#208a20'}[i.state]))
            self.ilist.addItem(it)
            if (F['fd'].fn, i.key) in self.sel_issues: it.setSelected(True)
        self.ilist.blockSignals(False); self._sync = False
        n = sum(1 for _, i in self.all_issues() if i.state == 'todo')
        self.tabs.setTabText(1, f'问题（{n} 项未处理）' if n else f'问题（{len(self.all_issues())}）')
        self.tabs.tabBar().setTabTextColor(1, QColor('#d02020') if n else QColor('#000000'))
        self.tabs.setTabText(0, f'零件（{len(self.parts)} 种）')

    def issue_selected(self):
        if self._sync: return
        L = self.all_issues(); rows = sorted({x.row() for x in self.ilist.selectedIndexes()})
        self.sel_issues = {(L[r][0]['fd'].fn, L[r][1].key) for r in rows if r < len(L)}
        ks = [L[r][1].kind for r in rows if r < len(L)]
        self.b_part.setEnabled(any(k not in ('text', 'thin') for k in ks) or not ks)
        self.b_ign.setText('删除' if ks and all(k == 'frame' for k in ks) else '忽略')
        self.set_show('preview', draw=False); self.draw_preview(zoom=len(rows) == 1)

    def issue_act(self, act):
        L = self.all_issues()
        rows = [r for r, (F, i) in enumerate(L) if (F['fd'].fn, i.key) in self.sel_issues]
        if self.running() or not rows: return
        keep = None; todo_reload = []
        for r in rows:
            F, i = L[r]; o = F['opts']; need = False
            if act == 'part' and i.kind in ('text', 'thin'): continue
            if i.kind == 'frame':
                need = o['keep_frame'] != (act == 'part'); o['keep_frame'] = act == 'part'
            else:
                if (i.key in o['as_part']) != (act == 'part'): need = True
                o['as_part'].discard(i.key)
                if act == 'part': o['as_part'].add(i.key)
            o['ignore'].discard(i.key)
            if act == 'ignore': o['ignore'].add(i.key)
            if need:
                if F not in todo_reload: todo_reload.append(F)
            else: i.state = act
        for F in todo_reload: self.reload(F)
        self.fill_table(); self.fill_issues(keep=keep); self.reset_result(); self.draw_preview(zoom=len(rows) == 1)

    def open_all(self):
        if self.running(): return
        self.sel_issues = {(F['fd'].fn, i.key) for F, i in self.all_issues() if i.kind == 'open' and i.state == 'todo'}
        if self.sel_issues: self.issue_act('part')
        self.sel_issues = set(); self.fill_issues(); self.draw_preview()

    def issue_all(self):
        for F, i in self.all_issues():
            if i.state == 'todo': F['opts']['ignore'].add(i.key); i.state = 'ignore'
        self.fill_issues(); self.reset_result()

    def part_selected(self):
        if self._sync: return
        rows = sorted({x.row() for x in self.tab.selectedIndexes()}); ps = self.parts
        self.sel_parts = {(ps[r].src, self.pkey(ps[r])) for r in rows if r < len(ps)}
        if self.vmode == 'preview' and not self.running(): self.draw_preview(zoom=len(rows) == 1)

    # ---------------- 镂空
    def hkey(self, F, p, h): return (F['fd'].fn, self.pkey(p), h['key'])

    def hole_state(self, F, p, h): return F['holes'].get((self.pkey(p), h['key']), 'keep')

    def fill_holes_list(self):
        """镂空页：列出候选孔 = 会切穿、孔里是空的、粗判放得下当前要排的某个零件。"""
        gap = self.gap.value()
        sd = [(s, HL.dims(s)) for s in (Polygon(N.item_shape(p.poly, gap)[0]) for p in self.parts if p.qty > 0)]
        rows, busy, mark = [], 0, 0
        for F in self.files:
            for p in F['fd'].parts:
                if self.pkey(p) in F['drop']: continue
                for h in HL.part_holes(p):
                    if h['mark']: mark += 1
                    elif h['cut'] and h['busy']: busy += 1
                    elif h['cut'] and sd and HL.fits_some(h['ring'], sd, gap): rows.append((F, p, h))
        self._hrows = rows
        self._sync = True; self.hlist.blockSignals(True); self.hlist.clear(); nf = 0
        for F, p, h in rows:
            st = self.hole_state(F, p, h); nf += st == 'fill'; b = h['ring'].bounds
            n = f'（共 {p.qty} 件，每件都按此处理）' if p.qty > 1 else ''
            it = QListWidgetItem(f'{F["fd"].name}  |  {p.name}  |  孔约 {b[2] - b[0]:.0f} × {b[3] - b[1]:.0f} mm  |  '
                                 f'{"可排入" if st == "fill" else "保留"}{n}')
            it.setForeground(QColor('#208a20' if st == 'fill' else '#2a7fa8'))
            self.hlist.addItem(it)
            if self.hkey(F, p, h) in self.sel_holes: it.setSelected(True)
        self.hlist.blockSignals(False); self._sync = False
        notes = ([f'{busy} 个孔里有东西（小孔、刻线、文字等），可能是要用的零件'] if busy else []) + \
                ([f'{mark} 个是刻线框，不会切穿'] if mark else [])
        self.hnote.setText('另有 ' + '；'.join(notes) + '，不参与。' if notes else '')
        self.tabs.setTabText(2, f'镂空（{nf}/{len(rows)} 可排入）' if rows else '镂空')

    def hole_selected(self):
        if self._sync: return
        rows = sorted({x.row() for x in self.hlist.selectedIndexes()})
        self.sel_holes = {self.hkey(*self._hrows[r]) for r in rows if r < len(self._hrows)}
        self.set_show('preview', draw=False); self.draw_preview(zoom=len(rows) == 1)

    def set_hole(self, F, p, h, st):
        if st == 'fill': F['holes'][(self.pkey(p), h['key'])] = 'fill'
        else: F['holes'].pop((self.pkey(p), h['key']), None)

    def hole_act(self, st):
        if self.running() or not self.sel_holes: return
        for F, p, h in self._hrows:
            if self.hkey(F, p, h) in self.sel_holes: self.set_hole(F, p, h, st)
        self.fill_holes_list(); self.set_show('preview', draw=False); self.reset_result()

    def hole_all(self, st):
        if self.running() or not self._hrows: return
        for F, p, h in self._hrows: self.set_hole(F, p, h, st)
        self.fill_holes_list(); self.set_show('preview', draw=False); self.reset_result()

    def gap_changed(self, *_):
        if self.running(): return
        self.fill_holes_list()
        if self.vmode == 'preview': self.draw_preview()

    # ---------------- 画面里点选/框选
    def scene_sel(self):
        if self._sync or self.vmode != 'preview': return
        sel = {'p': set(), 'i': set(), 'h': set()}
        for it, kind, key, pen in self._pitems:
            try:
                if it.isSelected(): sel[kind].add(key)
            except RuntimeError: pass
        self.sel_parts, self.sel_issues, self.sel_holes = sel['p'], sel['i'], sel['h']
        self.restyle(); self.sync_lists()

    def restyle(self):
        hp = QPen(QColor('#ffe14d'), 3); hp.setCosmetic(True)
        for it, kind, key, pen in self._pitems:
            on = key in {'p': self.sel_parts, 'i': self.sel_issues, 'h': self.sel_holes}[kind]
            try: it.setPen(hp if on else pen)
            except RuntimeError: pass

    def sync_lists(self):
        self._sync = True
        sm = self.tab.selectionModel(); sm.clearSelection()
        for r, p in enumerate(self.parts):
            if (p.src, self.pkey(p)) in self.sel_parts:
                sm.select(self.tab.model().index(r, 0), QItemSelectionModel.Select | QItemSelectionModel.Rows)
        for r, (F, i) in enumerate(self.all_issues()):
            self.ilist.item(r).setSelected((F['fd'].fn, i.key) in self.sel_issues)
        for r, row in enumerate(self._hrows):
            self.hlist.item(r).setSelected(self.hkey(*row) in self.sel_holes)
        self._sync = False

    def delete_sel(self):
        if self.running(): return
        if self.sel_parts:
            for F in self.files:
                for p in F['fd'].parts:
                    if (p.src, self.pkey(p)) in self.sel_parts: F['drop'].add(self.pkey(p))
            self.sel_parts = set(); self.fill_table()
        if self.sel_issues: self.issue_act('ignore'); return
        self.fill_issues(); self.reset_result()

    def restore_sel(self):
        for F in self.files:
            for p in F['fd'].parts:
                if (p.src, self.pkey(p)) in self.sel_parts: F['drop'].discard(self.pkey(p))
        self.fill_table(); self.fill_issues(); self.reset_result()

    def ctx_menu(self, pos):
        if self.vmode != 'preview' or self.running(): return
        it = self.view.itemAt(pos)
        if it is not None and it.flags() & QGraphicsItem.ItemIsSelectable and not it.isSelected():
            self.view.scene().clearSelection(); it.setSelected(True)
        m = QMenu(self)
        if self.sel_parts:
            dropped = any(self.pkey(p) in F['drop'] for F in self.files for p in F['fd'].parts if (p.src, self.pkey(p)) in self.sel_parts)
            m.addAction(f'删除零件（{len(self.sel_parts)} 种）', self.delete_sel)
            if dropped: m.addAction('恢复已删除零件', self.restore_sel)
        if self.sel_issues:
            if m.actions(): m.addSeparator()
            m.addAction(f'忽略（{len(self.sel_issues)} 项）', lambda: self.issue_act('ignore'))
            m.addAction('作为零件', lambda: self.issue_act('part'))
            m.addAction('恢复为未处理', lambda: self.issue_act('todo'))
        if self.sel_holes:
            if m.actions(): m.addSeparator()
            m.addAction(f'镂空可排入（{len(self.sel_holes)} 个）', lambda: self.hole_act('fill'))
            m.addAction('镂空保留（不往里排）', lambda: self.hole_act('keep'))
        if not m.actions(): m.addAction('未选中对象').setEnabled(False)
        m.exec(self.view.mapToGlobal(pos))

    # ---------------- 预览
    def set_show(self, s, draw=True):
        if self.running() and s == 'preview': s = 'result'
        self.vmode = s; (self.bv_pre if s == 'preview' else self.bv_res).setChecked(True)
        self.view.setDragMode(QGraphicsView.RubberBandDrag if s == 'preview' else QGraphicsView.ScrollHandDrag)
        self.view.auto = True
        if draw: self.draw_empty()

    def draw_preview(self, zoom=False):
        self._sync = True
        sc = self.view.scene(); sc.clear(); self._pitems = []; x0 = 0.0; focus = None
        W = sum(F['fd'].bounds[2] - F['fd'].bounds[0] for F in self.files) or 1
        gapx = max(30.0, 0.04 * W)
        def pen(c, w, dash=False):
            p = QPen(QColor(c), w, Qt.DashLine if dash else Qt.SolidLine); p.setCosmetic(True); return p
        cands = {}
        for F, p, h in self._hrows: cands.setdefault(id(p), []).append(h)
        for F in self.files:
            fd = F['fd']; b = fd.bounds; dx, dy = x0 - b[0], -b[1]
            t = QGraphicsSimpleTextItem(fd.name); t.setBrush(QColor('#e0e0e0')); t.setFont(QFont('Microsoft YaHei', 10))
            t.setFlag(QGraphicsSimpleTextItem.ItemIgnoresTransformations); t.setPos(x0, b[3] + dy)
            t.setTransform(QTransform.fromTranslate(0, -24)); sc.addItem(t)
            for p in fd.parts:
                dropped = self.pkey(p) in F['drop']; qp = disp_path(p)
                mk = QPainterPath()                                   # 刻线框：画成细线，不当孔
                for h in HL.part_holes(p):
                    if h['mark']: mk.addPath(poly_path(h['ring']))
                hs = [] if dropped else cands.get(id(p), [])
                for o in p.copies:
                    it = QGraphicsPathItem(qp)
                    col = QColor('#555a63') if dropped else QColor(getattr(p, 'col', QColor('#9ab')))
                    col.setAlpha(110 if dropped else 200); it.setBrush(col)
                    hl = (p.src, self.pkey(p)) in self.sel_parts; npen = pen(col.darker(170).name(), 1)
                    it.setPen(pen('#ffe14d', 3) if hl else npen)
                    tr = QTransform.fromTranslate(o[0] + dx, o[1] + dy); it.setTransform(tr); sc.addItem(it)
                    it.setFlag(QGraphicsItem.ItemIsSelectable); it.setSelected(hl)
                    self._pitems.append((it, 'p', (p.src, self.pkey(p)), npen))
                    if hl: focus = it.sceneBoundingRect() if focus is None else focus.united(it.sceneBoundingRect())
                    if not mk.isEmpty():
                        mi = QGraphicsPathItem(mk); mi.setPen(pen(col.darker(220).name(), 1)); mi.setTransform(tr)
                        mi.setZValue(1); sc.addItem(mi)
                    for h in hs:                                      # 候选孔：保留 = 青色虚线，可排入 = 绿色
                        key = self.hkey(F, p, h); fill = self.hole_state(F, p, h) == 'fill'; on = key in self.sel_holes
                        hi = QGraphicsPathItem(poly_path(h['ring'])); hi.setTransform(tr); hi.setZValue(4)
                        npe = pen('#3ad06a', 2) if fill else pen('#36c5e0', 1.5, True)
                        hi.setPen(pen('#ffe14d', 3) if on else npe)
                        hi.setBrush(QColor(58, 208, 106, 110) if fill else QColor(54, 197, 224, 25))
                        hi.setFlag(QGraphicsItem.ItemIsSelectable); sc.addItem(hi); hi.setSelected(on)
                        self._pitems.append((hi, 'h', key, npe))
                        if on: focus = hi.sceneBoundingRect() if focus is None else focus.united(hi.sceneBoundingRect())
            for i in fd.issues:
                hl = (fd.fn, i.key) in self.sel_issues
                if i.kind == 'frame':
                    if i.state == 'part': continue
                    npe = pen('#ff9a2e', 2, True) if i.state == 'todo' else pen('#777', 1, True)
                else:
                    npe = pen('#ff3b3b', 2.5) if i.state == 'todo' else pen('#3cc43c', 1.5) if i.state == 'part' else pen('#777', 1, True)
                pe = pen('#ffe14d', 4) if hl else npe
                r = QRectF()
                for ln in i.lines:
                    path = QPainterPath(); path.moveTo(ln[0][0] + dx, ln[0][1] + dy)
                    for q in ln[1:]: path.lineTo(q[0] + dx, q[1] + dy)
                    it = QGraphicsPathItem(path); it.setPen(pe); it.setZValue(5); sc.addItem(it)
                    it.setFlag(QGraphicsItem.ItemIsSelectable); it.setSelected(hl)
                    self._pitems.append((it, 'i', (fd.fn, i.key), npe))
                    r = r.united(it.boundingRect())
                if i.kind in ('open', 'text') and i.state != 'ignore':
                    for q in i.ends:
                        e = QGraphicsEllipseItem(-7, -7, 14, 14); e.setFlag(QGraphicsEllipseItem.ItemIgnoresTransformations)
                        e.setPen(pen('#ffe14d' if hl else '#ff3b3b', 2)); e.setPos(q[0] + dx, q[1] + dy); e.setZValue(6); sc.addItem(e)
                        r = r.united(QRectF(q[0] + dx - 2, q[1] + dy - 2, 4, 4))
                if hl: focus = r if focus is None else focus.united(r)
            x0 += (b[2] - b[0]) + gapx
        if not self.files:
            t = QGraphicsSimpleTextItem('将 DXF 文件拖入此处，或点击“导入 DXF…”'); t.setBrush(QColor('#aaaaaa'))
            t.setFont(QFont('Microsoft YaHei', 14)); t.setFlag(QGraphicsSimpleTextItem.ItemIgnoresTransformations); sc.addItem(t)
        self._sync = False
        self.view.fit_all()
        if zoom and focus is not None and focus.isValid():
            m = max(focus.width(), focus.height(), 20) * 1.5
            self.view.auto = False; self.view.fitInView(focus.adjusted(-m, -m, m, m), Qt.KeepAspectRatio)
        elif self.view.auto: self.view.fit()

    def fill_table(self):
        self.tab.blockSignals(True)
        ps = self.parts
        self.tab.setRowCount(len(ps))
        for r, p in enumerate(ps):
            p.col = color(r)
            it = QTableWidgetItem(thumb(p, p.col), p.name); it.setFlags(it.flags() & ~Qt.ItemIsEditable)
            self.tab.setItem(r, 0, it)
            it = QTableWidgetItem(f'{p.w:.1f} × {p.h:.1f}'); it.setFlags(it.flags() & ~Qt.ItemIsEditable)
            self.tab.setItem(r, 1, it)
            sb = QSpinBox(); sb.setRange(0, 999); sb.setValue(p.qty)
            sb.valueChanged.connect(lambda v, p=p: self.qty_changed(p, v))
            self.tab.setCellWidget(r, 2, sb)
            it = QTableWidgetItem(os.path.basename(p.src)); it.setToolTip(p.src); it.setFlags(it.flags() & ~Qt.ItemIsEditable)
            self.tab.setItem(r, 3, it)
        self.tab.blockSignals(False)
        self.update_count(); self.fill_holes_list()

    def qty_changed(self, p, v):
        p.qty = v; self.fobj(p.src)['qty'][self.pkey(p)] = v; self.fill_holes_list(); self.reset_result()

    def update_count(self):
        n = sum(p.qty for p in self.parts)
        if not self.running():
            self.stat.setText(f'零件 {len(self.parts)} 种，共 {n} 件。' if self.parts else '导入 DXF 后检查预览，再点击“开始排版”。')

    def remove(self): self.delete_sel()

    def clear(self):
        if self.running(): return
        self.files = []; self.info.setText(''); self.refresh_all()

    def post_ui(self):
        if hasattr(self, 'post_box'): self.post_box.setEnabled(bool(self.sheets) and not self.running())

    def reset_result(self):
        self.sheets = []; self.bexp.setEnabled(False); self.update_count(); self.draw_empty()
        self.post_ui()

    # ---------------- 画面
    def sheet_origin(self, k):
        return 0.0, -k * (self.sh.value() + SHEET_GAP)

    def draw_sheet(self, k, overflow=0.0, label=''):
        sc = self.view.scene(); L, H = self.sw.value(), self.sh.value(); ox, oy = self.sheet_origin(k)
        r = QGraphicsRectItem(ox, oy, L, H); r.setBrush(QColor('#d8c3a0')); pen = QPen(QColor('#8a7a5c')); pen.setCosmetic(True)
        r.setPen(pen); r.setZValue(-2); sc.addItem(r)
        if overflow > L:
            o = QGraphicsRectItem(ox + L, oy, overflow - L, H); o.setBrush(QColor(220, 60, 60, 60))
            pen = QPen(QColor(220, 60, 60), 1, Qt.DashLine); pen.setCosmetic(True); o.setPen(pen); o.setZValue(-1); sc.addItem(o)
        t = QGraphicsSimpleTextItem(label or f'第 {k + 1} 张  {L:g}×{H:g}'); t.setBrush(QColor('#e0e0e0'))
        t.setFont(QFont('Microsoft YaHei', 10)); t.setFlag(QGraphicsSimpleTextItem.ItemIgnoresTransformations)
        t.setPos(ox, oy); sc.addItem(t)

    def draw_part(self, pid, rot, tx, ty, k):
        p = self.byid[pid]; ox, oy = self.sheet_origin(k); off = self.job_off
        it = QGraphicsPathItem(p.qpath); it.setBrush(p.col)
        pen = QPen(p.col.darker(170)); pen.setCosmetic(True); it.setPen(pen)
        it.setTransform(QTransform().translate(ox + tx + off, oy + ty + off).rotate(rot))
        self.view.scene().addItem(it)

    def draw_empty(self):
        if self.running(): return
        if self.vmode == 'preview': self.draw_preview(); return
        sc = self.view.scene(); sc.clear()
        if self.sheets: self.draw_result(); return
        if self.corner():
            for L_ in ((0, 0, 300, 0), (0, 0, 0, 200)):
                ln = sc.addLine(*L_); p2 = QPen(QColor('#ffcc33'), 2.5); p2.setCosmetic(True); ln.setPen(p2)
        else: self.draw_sheet(0)
        self.view.auto = True; self.view.fit()

    def bbox(self, pl):
        r = QRectF()
        for pid, rot, tx, ty in pl:
            t = QTransform().translate(tx + self.job_off, ty + self.job_off).rotate(rot)
            r = r.united(t.map(self.byid[pid].qpath).boundingRect())
        return r

    def draw_corner(self, pl, label):
        sc = self.view.scene(); sc.clear()
        r = self.bbox(pl); m = self.margin.value(); W, H = r.right() + m, r.bottom() + m
        bg = QGraphicsRectItem(0, 0, W, H); bg.setBrush(QColor('#d8c3a0')); bg.setZValue(-2)
        pen = QPen(QColor('#f0f0f0'), 1.5, Qt.DashLine); pen.setCosmetic(True); bg.setPen(pen); sc.addItem(bg)
        for L_ in ((0, 0, W * 1.15 + 20, 0), (0, 0, 0, H * 1.15 + 20)):        # 左下角两条边
            ln = sc.addLine(*L_); p2 = QPen(QColor('#ffcc33'), 2.5); p2.setCosmetic(True); ln.setPen(p2)
        for x in pl: self.draw_part(*x, 0)
        if label == '结果': self.draw_post(0, 0, 0)
        t = QGraphicsSimpleTextItem(f'{label}  占用 {W:.0f} × {H:.0f} mm'); t.setBrush(QColor('#e0e0e0'))
        t.setFont(QFont('Microsoft YaHei', 10)); t.setFlag(QGraphicsSimpleTextItem.ItemIgnoresTransformations)
        t.setPos(0, 0); sc.addItem(t)
        if self.view.auto: self.view.fit()

    def draw_state(self, cur_k=None, cur_pl=None, width=0.0, label='', replace=False):
        if getattr(self, 'job_corner', False):
            pl = cur_pl if cur_pl is not None else (self.sheets[0] if self.sheets else [])
            if pl: self.draw_corner(pl, label or '结果')
            return
        sc = self.view.scene(); sc.clear()
        for k, pl in enumerate(self.sheets):
            if replace and k == cur_k: continue
            self.draw_sheet(k, label=f'第 {k + 1} 张（{P.sheet_name(k)}）  利用率 {self.util(pl):.0%}')
            for x in pl: self.draw_part(*x, k)
            if cur_pl is None: self.draw_post(k, *self.sheet_origin(k))
        if cur_pl is not None:
            self.draw_sheet(cur_k, overflow=width + self.job_off * 2 if width else 0, label=label)
            for x in cur_pl: self.draw_part(*x, cur_k)
        if self.view.auto: self.view.fit()

    def draw_result(self): self.draw_state()

    # ---------------- 后处理
    def post_changed(self, *_):
        if self.sheets and not self.running():
            self.compute_post()
            if self.vmode == 'result': self.draw_state()

    def sheet_rect(self, k, pl):
        m = self.margin.value()
        if self.job_corner:
            r = self.bbox(pl); return (m, m, r.right() + m + 40, r.bottom() + m + 40)
        return (m, m, self.sw.value() - m, self.sh.value() - m)

    def compute_post(self):
        self.post = None
        if not self.sheets: return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            tabs, notab = {}, 0
            if self.tab_on.isChecked():
                for p in {id(self.byid[i]): self.byid[i] for pl in self.sheets for i, *_ in pl}.values():
                    plan = P.plan_tabs(p, self.tab_len.value(), self.tab_sp.value())
                    if plan is None: notab += 1
                    tabs[id(p)] = plan or {}
            out, miss = [], 0
            for k, pl in enumerate(self.sheets):
                items = [(self.byid[i], r, x + self.job_off, y + self.job_off) for i, r, x, y in pl]
                solids = [self.placed_solid(*x) for x in pl]
                polys = P.reading_order(list(zip(solids, items)))
                mk, nm, ms = P.plan_marks([g for g, _ in polys], self.sheet_rect(k, pl), P.sheet_name(k),
                                          self.mark_h.value(), self.num_h.value(), self.mark_on.isChecked(),
                                          self.num_on.isChecked(), outside_ok=self.job_corner)
                miss += ms
                tp = []
                for part, r, x, y in items:
                    ca, sa = math.cos(math.radians(r)), math.sin(math.radians(r))
                    tp += [(u * ca - v * sa + x, u * sa + v * ca + y) for u, v in P.tab_points(part, tabs.get(id(part)))]
                used_h = []                                       # 排进了零件的镂空，结果图里描出来
                for g in solids:
                    for r_ in g.interiors:
                        hp = Polygon(r_)
                        if any(hp.contains(q.representative_point()) for q in solids if q is not g): used_h.append(list(r_.coords))
                out.append(dict(items=items, marks=mk, nums=nm, tabpts=tp, holes=used_h))
            used = list({id(self.byid[i]): self.byid[i] for pl in self.sheets for i, *_ in pl}.values())
            for p in used: P.part_prims(p)
            kept = sum(1 for p in used if p._kept); clash = [p.name for p in used if p._clash]
            self.post = dict(sheets=out, tabs=tabs, clash=clash)
            msg = []
            if self.tab_on.isChecked():
                msg.append(f'微连接 {sum(len(s["tabpts"]) for s in out)} 处')
                if notab: msg.append(f'{notab} 种零件没有合适的直边，未加微连接')
            if self.num_on.isChecked() and miss: msg.append(f'{miss} 个零件旁边空间不足，未编号（可加大零件间距）')
            lines = ['后处理：' + '；'.join(msg) + '。'] if msg else []
            if kept: lines.append(f'{kept} 种零件内部有和外轮廓同图层、但颜色不同的线，已按刻线处理：保留原图层和颜色，不放进“切割”层。')
            if clash: lines.append(self.clash_text(clash))
            if lines: self.info.setText('\n'.join(lines))
        finally: QApplication.restoreOverrideCursor()

    @staticmethod
    def clash_text(names):
        s = '、'.join(names[:5]) + ('等' if len(names) > 5 else '')
        return (f'注意：{s} 的切割线原来不是黑/白色，导出后统一成黑/白色（“切割”层），和零件上黑/白色的刻线颜色相同。'
                'LightBurn、RDWorks 等按颜色区分加工的软件会把这些刻线一起切穿，请先把原图里的刻线改成别的颜色。')

    def draw_post(self, k, ox, oy):
        if not self.post or k >= len(self.post['sheets']): return
        S = self.post['sheets'][k]; sc = self.view.scene()
        for q, col, w, dash in [(q, '#3a6df0', 1.6, False) for q in S['marks']] + [(q, '#1f9d3a', 1.6, False) for q in S['nums']] + \
                [(q, '#2fbf5a', 2.0, True) for q in S.get('holes', [])]:
            path = QPainterPath(); path.moveTo(q[0][0] + ox, q[0][1] + oy)
            for x, y in q[1:]: path.lineTo(x + ox, y + oy)
            it = QGraphicsPathItem(path); pe = QPen(QColor(col), w, Qt.DashLine if dash else Qt.SolidLine); pe.setCosmetic(True)
            it.setPen(pe); it.setZValue(8); sc.addItem(it)
        half = self.tab_mode.currentIndex() == 1
        for x, y in S['tabpts']:
            e = QGraphicsEllipseItem(-3, -3, 6, 6); e.setFlag(QGraphicsEllipseItem.ItemIgnoresTransformations)
            e.setBrush(QColor('#e02020' if half else '#202020')); e.setPen(QPen(QColor('#ffffff'), 1))
            e.setPos(x + ox, y + oy); e.setZValue(9); sc.addItem(e)

    def util(self, pl):
        return sum(self.byid[i].area for i, *_ in pl) / (self.sw.value() * self.sh.value())

    # ---------------- 运行
    def running(self): return any(p.is_alive() for p in getattr(self, 'procs', []))

    def kill_all(self):
        for p in getattr(self, 'procs', []):
            if p.is_alive(): p.terminate()
        for p in getattr(self, 'procs', []): p.join(1)
        self.procs = []

    def redo(self):
        if self.running() or not self.sheets: return
        self.best = dict(sheets=self.sheets, score=self.score(), base_rot=dict(getattr(self, 'base_rot', {})),
                         byid=self.byid, off=self.job_off, corner=self.job_corner, n=getattr(self, 'redo_n', 1),
                         chk=getattr(self, 'job_chk', None), solid=self.solid, nh=self.n_in_holes)
        self.start(again=True)

    def restore_best(self):
        b = self.best
        self.sheets, self.base_rot, self.byid, self.job_off, self.job_corner = b['sheets'], b['base_rot'], b['byid'], b['off'], b['corner']
        self.job_chk, self.solid, self.n_in_holes = b['chk'], b['solid'], b['nh']

    def placed_solid(self, pid, rot, tx, ty):
        """零件在板上的实际占用形状：外轮廓减去可排入的镂空，其他孔当实心。"""
        g = self.solid.get(pid) or Polygon(self.byid[pid].poly.exterior)
        return affinity.translate(affinity.rotate(g, rot, origin=(0, 0)), tx + self.job_off, ty + self.job_off)

    def score(self, sheets=None):
        """越小越好：整板 = (张数, -各张利用率平方和)；靠角 = 占用面积。"""
        sheets = self.sheets if sheets is None else sheets
        if self.job_corner:
            r = self.bbox(sheets[0]); return (1, r.width() * r.height())
        return (len(sheets), -sum(self.util(pl) ** 2 for pl in sheets))

    def fill_rings(self, p):
        """零件上设为可排入、而且确实能用的孔（零件局部坐标）。"""
        F = self.fobj(p.src)
        return [h['ring'] for h in HL.part_holes(p) if h['cut'] and not h['busy'] and self.hole_state(F, p, h) == 'fill']

    def start(self, again=False):
        if self.running(): return
        if not again: self.best = None; self.redo_n = 1
        if not any(p.qty for p in self.parts):
            QMessageBox.information(self, APP, '没有可排版的零件。请导入 DXF，并确认数量不为 0。'); return
        todo = [(F, i) for F, i in self.all_issues() if i.state == 'todo']
        if todo and not again and not os.environ.get('NEST_SELFTEST'):
            t = '\n'.join(f'{F["fd"].name}  {i.text()}' for F, i in todo[:12]) + ('\n…' if len(todo) > 12 else '')
            if QMessageBox.question(self, APP, f'有 {len(todo)} 项问题未处理：\n{t}\n\n未处理项按“忽略”处理（板框删除），'
                                    '不参与排版。是否继续？') != QMessageBox.Yes:
                self.tabs.setCurrentIndex(1); return
        self.save_cfg()
        L, H = self.sw.value(), self.sh.value(); m, gap = self.margin.value(), self.gap.value()
        rot = ROTS[self.rot.currentIndex()][1]
        shapes, qty, self.byid = {}, {}, {}
        b = 0; bad = []; orig = {}; hosts = []
        self.base_rot = {}; self.comps = {}; self.solid = {}; self.n_in_holes = 0
        for k, p in enumerate(self.parts):
            if p.qty <= 0: continue
            pid = f'p{k}'; s, b = N.item_shape(p.poly, gap); orig[pid] = s
            if rot is None:                                  # 任意角度：先转到最窄方向，求解器从 0° 起就能放下
                a = N.narrow_angle(Polygon(s)); self.base_rot[pid] = a
                s = [tuple(c) for c in affinity.rotate(Polygon(s), a, origin=(0, 0)).exterior.coords[:-1]]
            shapes[pid] = s; qty[pid] = p.qty; self.byid[pid] = p; p.qpath = disp_path(p)
            fr = self.fill_rings(p)
            if fr:                                           # 有可排入的镂空：占用形状挖掉这些孔
                self.solid[pid] = Polygon(p.poly.exterior, [r.exterior.coords for r in fr])
                hosts.append(dict(pid=pid, qty=p.qty, holes=[list(r.exterior.coords) for r in fr]))
            if not self.corner() and not self.fits(Polygon(s), rot if rot is not None else [0.0, 90.0, 180.0, 270.0],
                                                   L - 2 * m + 2 * b, H - 2 * m + 2 * b): bad.append(p.name)
        if bad:
            QMessageBox.warning(self, APP, '以下零件超出板材尺寸：\n' + '\n'.join(bad[:20]) +
                                ('\n…' if len(bad) > 20 else '') + '\n\n请更换板材尺寸或调整旋转方式。'); return
        self.job_off = m - b; self.job_corner = self.corner()
        self.job_chk = dict(L=L, H=H, m=m, gap=gap, qty=dict(qty))
        self.job = dict(shapes=shapes, qty=qty, L=L - 2 * m + 2 * b, H=H - 2 * m + 2 * b, rot=rot,
                        time=self.tsec.value(), mode='corner' if self.m_corner.isChecked() else 'fill',
                        seed=int(time.time()) % 100000)
        if again: self.redo_n = getattr(self, 'redo_n', 1) + 1
        self.sheets = []; self.post = None; self.final = False; self.t0 = time.time(); self.view.auto = True; self.waiting = False
        self.vmode = 'result'; self.bv_res.setChecked(True); self.procs = []
        if not hosts: self.launch(); return
        # 先往镂空里排（子进程），排完再启动排版。输入没变（重排）就直接用上次的结果
        hjob = dict(hosts=hosts, shapes=orig, qty=dict(qty), gap=gap, rot=rot); key = repr(hjob)
        if self.hole_cache and self.hole_cache[0] == key:
            self.apply_holes(self.hole_cache[1]); self.launch(); return
        self.hole_job_key = key
        ctx = mp.get_context('spawn'); self.q = ctx.Queue()
        p = ctx.Process(target=HL.hole_worker, args=(hjob, self.q), daemon=True); p.start(); self.procs = [p]
        self.phase = 'holes'; self.set_busy(True); self.timer.start(40)
        self.view.scene().clear()
        if not self.job_corner: self.draw_sheet(0)
        self.stat.setText('正在往镂空里排零件…')

    def launch(self):
        """启动排版进程：同时算几个方案，最后取最好的。"""
        job = self.job; self.phase = 'nest'
        ctx = mp.get_context('spawn'); self.q = ctx.Queue()
        cpu = os.cpu_count() or 4; nw = max(2, min(6, cpu // 4)); self.nw = nw; self.results = {}
        self.procs = []
        for wid in range(nw):
            jb = dict(job, seed=job['seed'] + wid * 7919, threads=max(1, cpu // nw),
                      strategy='strip' if wid % 2 == 0 else 'shelf')
            p = ctx.Process(target=N.worker, args=(jb, self.q, wid), daemon=True); p.start(); self.procs.append(p)
        self.proc = self.procs[0]
        self.set_busy(True); self.timer.start(40)
        self.stat.setText('正在启动…' + (f'（已把 {self.n_in_holes} 件排进镂空）' if self.n_in_holes else ''))

    def apply_holes(self, res):
        """往孔里排的结果 → 组合件：装了同样零件的大零件副本合成一种（id 如 p3h0），外形和大零件一样；
        被装进孔里的零件、变成组合件的大零件，数量相应扣掉。"""
        job = self.job; grp = {}
        for hp, cont in res: grp[(hp, cont)] = grp.get((hp, cont), 0) + 1
        for n, ((hp, cont), c) in enumerate(grp.items()):
            cid = f'{hp}h{n}'
            job['shapes'][cid] = job['shapes'][hp]; job['qty'][cid] = c; job['qty'][hp] -= c
            for i, *_ in cont: job['qty'][i] -= c
            self.comps[cid] = (hp, cont); self.n_in_holes += c * len(cont)
            if hp in self.base_rot: self.base_rot[cid] = self.base_rot[hp]
        for i in [i for i, c in job['qty'].items() if c <= 0]: del job['qty'][i]; del job['shapes'][i]

    def poll_holes(self):
        res = err = None
        def drain(block):
            nonlocal res, err
            try:
                while True:
                    m = self.q.get(timeout=0.5) if block else self.q.get_nowait()
                    if m[0] == 'hprog':
                        self.stat.setText(f'正在往镂空里排零件：第 {m[1]}/{m[2]} 个孔，已排进 {m[3]} 件，已用 {time.time() - self.t0:.0f} 秒')
                    elif m[0] == 'hdone': res = m[1]; return
                    elif m[0] == 'error': err = m[1]; return
            except Exception: pass
        drain(False)
        if res is None and err is None:
            if self.running(): return
            drain(True)
            if res is None and err is None: err = '往镂空里排零件的进程异常退出。'
        self.timer.stop(); self.kill_all()
        if err is not None:
            self.phase = None; self.set_busy(False)
            if getattr(self, 'best', None): self.restore_best(); self.compute_post(); self.draw_state()
            else: self.draw_empty()
            QMessageBox.critical(self, APP, '往镂空里排零件时出错：\n' + err[-1500:]); return
        self.hole_cache = (self.hole_job_key, res)
        self.apply_holes(res); self.launch()

    def compare_best(self):
        """重排后和之前的结果比，保留更好的。返回附加说明。"""
        b = getattr(self, 'best', None)
        if not b: return ''
        new = self.score(); n = self.redo_n
        desc = lambda sc, corner: f'{sc[1] / 100:.0f} cm²' if corner else f'{sc[0]} 张'
        if new <= b['score']:
            return f'（第 {n} 次计算，比之前更好或相同，已采用：{desc(new, self.job_corner)}，之前 {desc(b["score"], b["corner"])}）'
        self.restore_best()
        return f'（第 {n} 次计算得到 {desc(new, self.job_corner)}，不如之前的 {desc(b["score"], b["corner"])}，已保留之前的结果）'

    def verify(self):
        """排完后静默核对：数量、零件重叠、间距、超出板材/边距。返回问题列表（空 = 通过）。"""
        from shapely.strtree import STRtree
        c = getattr(self, 'job_chk', None)
        if not c or not self.sheets: return []
        bad = []; tol = 0.1
        cnt = {}
        for pl in self.sheets:
            for i, *_ in pl: cnt[i] = cnt.get(i, 0) + 1
        for i, q in c['qty'].items():
            if cnt.get(i, 0) != q:
                bad.append(f'{self.byid[i].name}：应有 {q} 件，排了 {cnt.get(i, 0)} 件')
        for k, pl in enumerate(self.sheets):
            sn = f'第 {k + 1} 张'
            gs = [self.placed_solid(*x) for x in pl]           # 孔里的零件和大零件不算重叠；排进“保留”的孔会报出来
            names = [self.byid[i].name for i, *_ in pl]
            for g, nm in zip(gs, names):
                b = g.bounds
                if b[0] < c['m'] - tol or b[1] < c['m'] - tol or (not self.job_corner and
                        (b[2] > c['L'] - c['m'] + tol or b[3] > c['H'] - c['m'] + tol)):
                    bad.append(f'{sn}：{nm} 超出板材或边距')
            tree = STRtree(gs)
            for a, g in enumerate(gs):
                for j in tree.query(g.buffer(c['gap'])):
                    j = int(j)
                    if j <= a: continue
                    d = g.distance(gs[j])
                    if g.intersects(gs[j]) and g.intersection(gs[j]).area > 0.01:
                        bad.append(f'{sn}：{names[a]} 和 {names[j]} 重叠')
                    elif d < c['gap'] - tol:
                        bad.append(f'{sn}：{names[a]} 和 {names[j]} 间距 {d:.2f} mm，小于设定的 {c["gap"]:.1f} mm')
        return bad

    def fixrot(self, pl):
        """求解结果 → 普通放置：加回基准角；组合件展开成孔里的零件（写在前面，导出时先切）和大零件。"""
        b = getattr(self, 'base_rot', {}); C = getattr(self, 'comps', {}); out = []
        for i, r, x, y in pl:
            r = r + b.get(i, 0.0)
            if i in C:
                i, cont = C[i]; ca, sa = math.cos(math.radians(r)), math.sin(math.radians(r))
                out += [(j, r + rj, x + xj * ca - yj * sa, y + xj * sa + yj * ca) for j, rj, xj, yj in cont]
            out.append((i, r, x, y))
        return out

    @staticmethod
    def fits(g, rot, L, H):
        if rot is None:
            mr = g.minimum_rotated_rectangle; c = list(mr.exterior.coords)
            a, b = math.dist(c[0], c[1]), math.dist(c[1], c[2])
            return min(a, b) <= min(L, H) + 1e-6 and max(a, b) <= max(L, H) + 1e-6
        from shapely import affinity
        for r in rot:
            b = affinity.rotate(g, r, origin=(0, 0)).bounds
            if b[2] - b[0] <= L + 1e-6 and b[3] - b[1] <= H + 1e-6: return True
        return False

    def set_busy(self, busy):
        self.bstart.setEnabled(not busy); self.bstop.setEnabled(busy); self.bexp.setEnabled(not busy and bool(self.sheets))
        self.bredo.setEnabled(not busy and bool(self.sheets))
        for w in (self.tab, self.preset, self.sw, self.sh, self.margin, self.gap, self.rot, self.m_fill, self.m_corner, self.tsec,
                  self.b_ign, self.b_part, self.b_todo, self.b_all, self.b_open, self.bv_pre,
                  self.b_hfill, self.b_hkeep, self.b_hall, self.b_hnone):
            w.setEnabled(not busy)
        if not busy: self.mode_ui()
        self.post_ui()

    def stop(self):
        self.kill_all()
        self.proc = None; self.timer.stop(); self.sheets = []; self.phase = None
        if getattr(self, 'best', None):                    # 重排中途停止：恢复之前的结果
            self.restore_best()
            self.set_busy(False); self.compute_post(); self.stat.setText('已停止，保留之前的结果。'); self.draw_state(); return
        self.set_busy(False)
        self.stat.setText('已停止。'); self.draw_empty()

    def poll(self):
        if self.phase == 'holes': self.poll_holes(); return
        last = None; msgs = []
        try:
            while True:
                m = self.q.get_nowait(); wid, m = m[0], m[1:]
                if m[0] in ('done', 'error'): self.results[wid] = m
                if wid != 0: continue
                if m[0] == 'progress': last = m
                elif m[0] == 'done':                       # 0 号方案完成：先显示，等其他方案
                    self.sheets = [self.fixrot(s) for s in m[1]]
                elif m[0] != 'error': msgs.append(m)
        except Exception: pass
        el = time.time() - self.t0
        for m in msgs:
            if m[0] == 'sheet':
                k, pl = m[1], self.fixrot(m[2])
                if k < len(self.sheets): self.sheets[k] = pl
                else: self.sheets.append(pl)
            elif m[0] == 'note': self.info.setText(m[1])
            elif m[0] == 'drop':
                if m[1] < len(self.sheets): del self.sheets[m[1]]
        if self.procs and len(self.results) < self.nw and not any(p.is_alive() for p in self.procs):
            for w in range(self.nw): self.results.setdefault(w, ('error', '计算进程异常退出。'))
        if self.procs and len(self.results) >= self.nw:
            ok = [[self.fixrot(s) for s in r[1]] for r in self.results.values() if r[0] == 'done' and r[1]]
            if not ok:
                self.timer.stop(); self.kill_all(); self.proc = None; self.phase = None; self.set_busy(False); self.sheets = []
                err = self.results.get(0, next(iter(self.results.values())))[1]
                nm = lambda mm: self.byid[f'p{mm.group(1)}'].name if f'p{mm.group(1)}' in self.byid else mm.group(0)
                txt = re.sub(r'p(\d+)(h\d+)?', nm, err)                 # 组合件 p3h0 也显示大零件的名字
                QMessageBox.critical(self, APP, '排版出错：\n' + txt[-1500:]); self.draw_empty(); return
            msgs = [('done', min(ok, key=self.score))]
        for m in msgs:
            if m[0] == 'done':
                self.sheets = m[1]; self.timer.stop(); self.kill_all(); self.proc = None; self.phase = None
                cmp = self.compare_best(); self.set_busy(False)
                t_v = time.time(); bad = self.verify()
                if os.environ.get('NEST_SELFTEST'): print('verify', round((time.time() - t_v) * 1000), 'ms', len(bad), bad[:5], flush=True)
                cmp += '  已核对：数量、重叠、间距、边距都没问题。' if not bad else '  核对发现问题，见弹窗。'
                if bad:
                    QMessageBox.warning(self, APP, '排版结果核对发现问题（切割前请检查）：\n' + '\n'.join(bad[:15]) +
                                        ('\n…' if len(bad) > 15 else ''))
                n = sum(len(s) for s in self.sheets); self.compute_post(); self.post_ui()
                nh = f'（其中 {self.n_in_holes} 件排在镂空里）' if self.n_in_holes else ''
                if self.job_corner:
                    r = self.bbox(self.sheets[0]); m = self.margin.value(); W, H = r.right() + m, r.bottom() + m
                    a = sum(self.byid[i].area for i, *_ in self.sheets[0])
                    self.stat.setText(f'排版完成：共 {n} 件{nh}，占用 {W:.0f} × {H:.0f} mm，利用率 {a / (W * H):.0%}，用时 {el:.0f} 秒。' + cmp)
                    self.draw_state(); return
                us = '，'.join(f'{self.util(s):.0%}' for s in self.sheets)
                self.stat.setText(f'排版完成：共 {n} 件{nh}，用板 {len(self.sheets)} 张，利用率 {us}，用时 {el:.0f} 秒。' + cmp)
                self.draw_state(); return
        if 0 in self.results and last is None:
            if not getattr(self, 'waiting', False): self.draw_state(); self.waiting = True
            self.stat.setText(f'正在比较几种排法，还剩 {self.nw - len(self.results)} 种在算，已用 {el:.0f} 秒'); return
        if last is not None:
            _, k, label, phase, width, h, pl = last; pl = self.fixrot(pl)
            if k < len(self.sheets):                       # 回填/压紧：在原位置显示这张板的试排
                self.draw_state(k, pl, 0.0, f'第 {k + 1} 张  {label}中', replace=True)
                self.stat.setText(f'正在{label}：第 {k + 1} 张板，当前 {len(self.sheets)} 张，已用 {el:.0f} 秒'); return
            ph = {'exploring': '搜索', 'compressing': '压缩', 'final': '完成'}.get(phase, phase)
            if self.job_corner:
                self.draw_state(k, pl, width, f'{label}·{ph}')
                self.stat.setText(f'正在计算：靠角排版，{label}，{ph}，已用 {el:.0f} 秒'); return
            Lin = self.sw.value() - 2 * self.job_off
            ok = '未超出' if width <= Lin + 1e-6 else '超出板长'
            self.draw_state(k, pl, width, f'第 {k + 1} 张  当前长度 {width + 2 * self.job_off:.0f} mm（{ok}）')
            self.stat.setText(f'正在计算：第 {k + 1} 张，{label}，{ph}，已用 {el:.0f} 秒')


    # ---------------- 导出
    def export(self):
        if not self.sheets: return
        fn, _ = QFileDialog.getSaveFileName(self, '导出 DXF（多张板自动编号）',
                                            os.path.join(self.cfg.value('dir', ''), '排版结果.dxf'), 'DXF 文件 (*.dxf)')
        if not fn: return
        self.export_to(fn)

    def export_to(self, fn, quiet=False):
        base = fn[:-4] if fn.lower().endswith('.dxf') else fn
        out = []
        try:
            for k, pl in enumerate(self.sheets):
                f = base + '.dxf' if len(self.sheets) == 1 else f'{base}_第{k + 1}张.dxf'
                if self.job_corner:
                    r = self.bbox(pl); wh = (r.right() + self.margin.value(), r.bottom() + self.margin.value())
                else: wh = (self.sw.value(), self.sh.value())
                if self.post is None: self.compute_post()
                S = self.post['sheets'][k]
                P.export_sheet(f, S['items'], wh, self.frame.isChecked(), self.texts.isChecked(),
                               tabs=self.post['tabs'] if self.tab_on.isChecked() else None,
                               half=self.tab_mode.currentIndex() == 1, tab_len=self.tab_len.value(),
                               marks=S['marks'], nums=S['nums'])
                out.append(os.path.basename(f))
        except Exception as ex:
            if quiet: raise
            QMessageBox.critical(self, APP, f'导出失败：{ex}'); return
        if quiet: return
        clash = (self.post or {}).get('clash')
        if clash: QMessageBox.warning(self, APP, '已导出：\n' + '\n'.join(out) + '\n\n' + self.clash_text(clash))
        else: QMessageBox.information(self, APP, '已导出：\n' + '\n'.join(out))

    def closeEvent(self, e):
        self.save_cfg()
        self.kill_all()
        super().closeEvent(e)


if __name__ == '__main__':
    mp.freeze_support()
    app = QApplication(sys.argv); app.setFont(QFont('Microsoft YaHei', 9))
    w = Main(); w.show()
    if len(sys.argv) > 1: w.add_files([a for a in sys.argv[1:] if a.lower().endswith('.dxf')])
    pv = os.environ.get('NEST_PREVIEW')            # 自测：导入预览截图（选中第 N 个问题）
    if pv:
        def pshot():
            k = int(os.environ.get('NEST_ISSUE', -1))
            if k >= 0: w.ilist.setCurrentRow(k)
            if os.environ.get('NEST_BOX'):
                pp = QPainterPath(); pp.addPolygon(w.view.mapToScene(w.view.viewport().rect()))
                w.view.scene().setSelectionArea(pp, Qt.ReplaceSelection, Qt.ContainsItemShape)
            act = os.environ.get('NEST_ACT')
            if act == 'openall': w.open_all()
            elif act: w.issue_act(act)
            if os.environ.get('NEST_HELP'):
                QTimer.singleShot(300, w.show_help)
                QTimer.singleShot(900, lambda: (QApplication.activeModalWidget().grab().save(pv), app.quit())); return
            QTimer.singleShot(400, lambda: (w.grab().save(pv), app.quit()))
        QTimer.singleShot(800, pshot)
    shot = os.environ.get('NEST_SELFTEST')          # 自测：自动排版，完成后截图退出
    if shot:
        if os.environ.get('NEST_MODE') == 'corner': w.m_corner.setChecked(True)
        elif os.environ.get('NEST_MODE') == 'fill': w.m_fill.setChecked(True)
        if os.environ.get('NEST_ACT') == 'openall': w.open_all()
        if os.environ.get('NEST_POST'):
            w.tab_on.setChecked(True); w.mark_on.setChecked(True); w.num_on.setChecked(True)
            w.tab_mode.setCurrentIndex(int(os.environ.get('NEST_POST')) - 1)
        w.tsec.setValue(int(os.environ.get('NEST_T', 5))); QTimer.singleShot(300, w.start)
        def chk():
            if not w.running():
                if int(os.environ.get('NEST_REDO', 0)) >= getattr(w, 'redo_n', 1) and w.sheets:
                    w.grab().save(shot.replace('.png', f'_{w.redo_n}.png')); w.redo(); return
                if os.environ.get('NEST_EXPORT'): w.export_to(os.environ['NEST_EXPORT'], quiet=True)
                w.grab().save(shot); app.quit()
        tt = QTimer(); tt.timeout.connect(chk); QTimer.singleShot(2000, lambda: tt.start(500))
    sys.exit(app.exec())
