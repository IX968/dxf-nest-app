# 航模DXF排版

给激光切割用的 DXF 自动排版小工具，带图形界面。导入零件图，自动排到板材上，导出每张板的 DXF。

## 下载

不想装 Python 的话，直接到右边 **Releases** 下载 `航模DXF排版_v0.1.5.exe`，双击运行（第一次启动要等几秒）。

## 能做什么

- 导入一个或多个 DXF，自动识别零件（闭合轮廓、孔、镂空里的小件）；没闭合的轮廓会列出来让你处理。
- 板材尺寸有常用预设（1000×100、900×900 等），也可以自定义；可设零件间距和板边距。
- 排版时实时显示过程，同时用几种排法并行计算，自动挑用板最少、最紧的结果。
- 排完自动核对数量、重叠、间距和边距。
- 导出每张板一个 DXF，可直接拿去切。

基本流程：导入 DXF → 在预览中处理问题项 → 设置参数 → 开始排版 → 导出 DXF。详细说明见软件里的“帮助”（F1）。

暂不考虑木纹方向，计划在后续版本加入。

## 从源码运行

需要 Python 3.13。

```
pip install -r requirements.txt
python nest_gui.py
```

## 打包成 exe

```
pip install pyinstaller
pyinstaller --onefile --windowed --collect-binaries spyrrow --exclude-module matplotlib --exclude-module PyQt5 --exclude-module tkinter --name "航模DXF排版_v0.1.5" nest_gui.py
```

## 文件

- `nest_gui.py`：界面
- `nest_core.py`：读 DXF、识别零件、排版
- `nest_post.py`：导出 DXF

## 算法来源与致谢

排版核心算法来自 **Jeroen Gardeyn** 的 [sparrow](https://github.com/JeroenGar/sparrow)（底层碰撞检测引擎是他的 [jagua-rs](https://github.com/JeroenGar/jagua-rs)），
通过 **Paul Durand-Lupinski** 的 Python 接口 [spyrrow](https://github.com/PaulDL-RS/spyrrow) 调用。本项目在它之上做了零件识别、界面、多方案并行、回填和结果核对。
感谢原作者的开源工作。各组件的许可证见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

## 许可证

[MIT](LICENSE)
