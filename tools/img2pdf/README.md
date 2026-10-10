# 图片转 PDF Img2Pdf

把一堆 JPG / PNG 拼成一个 PDF。**绿色单文件 exe，双击就用，不需要安装，不联网。**

做的场景很具体：报销要贴发票照片、扫描件要合成一份交上去、内网电脑装不了任何
"PDF 大师"。这种时候在线转换网站打不开，而手头的 Python 脚本又要装 Pillow ——
于是干脆不装，PDF 直接自己写。

## 为什么还要做一个

- **内网电脑装不了软件**，也没有管理员权限，任何"要安装"的方案第一步就死了
- **在线转换要上传文件**。发票、合同、证件照这种东西不该往别人的服务器上送
- 常见的"图片转 PDF"会**把 JPEG 重新压缩一遍**，转一次画质掉一次，文件还更大
- 现成的 Python 轮子（img2pdf 库、Pillow）都是**第三方依赖**，
  打包进 exe 就等于把别人的版本节奏和许可协议一起带进来

## 一句话原理

JPEG 的码流本身就是 PDF 认的一种图像格式（`/DCTDecode`），
所以**一个字节都不用改，直接原样塞进 PDF 就行**——这就是"不重编码、不掉画质"的全部秘密。
PNG 走的是另一条路：自己解扫描线（含 5 种行滤波）→ 拆出 alpha 当软掩膜 → 重新 zlib 压缩。

```bash
python -c "print(open('x.jpg','rb').read()[:4])"   # 这就是为什么 JPEG 能原样封装
```

## 支持什么

| 输入 | 说明 |
|---|---|
| `.jpg` `.jpeg` | 灰度 / RGB / CMYK / 渐进式（progressive）都收，**码流原样封装** |
| `.png` | 灰度 / 真彩色 / 索引调色板 / 带 alpha，位深 1 / 2 / 4 / 8 / 16 |
| 其他格式 | 明确报错并跳过这一张，不会静默丢文件（webp、heic、bmp、gif、tiff 都不支持） |

几个真会碰到的细节：

- **手机竖拍的照片会自动转正**：读 EXIF 的 Orientation，转成 PDF 里的放置矩阵，
  不会出现"拍的时候是竖的，转完躺倒了"
- **透明 PNG 转进 PDF 还是透明的**：alpha 拆成 `/SMask` 软掩膜，
  阅读器里叠在白纸上，打印出来就是白底
- **16 位 PNG 降到 8 位**（取高字节）。PDF 的 `BitsPerComponent` 要写 16 就得走
  另一套解码约定，收益小，不值得
- **隔行（Adam7）PNG 不支持**，会直接报错说明原因，而不是给你一张花图
- **页面大小跟着图里的密度走**：PNG 的 `pHYS`、JPEG 的 JFIF / EXIF 密度都会读，
  读不到就按 200 DPI 排，界面上也能手动填

## 排版选项

| 选项 | 取值 | 默认 |
|---|---|---|
| 纸张 | 按图片大小 / A4 / A5 / Letter | 按图片大小 |
| 方向 | 自动（横图配横纸）/ 竖版 / 横版 | 自动（选"按图片大小"时这项灰掉） |
| 页边距 | 磅，0 就是顶边 | 28 磅（约 1 厘米，打印不会被裁） |
| DPI | 留空 = 用图里声明的密度 | 空 |
| 文档标题 | 写进 PDF 元数据，阅读器标签页上能看到 | 空 |

选 A4 / Letter 时图片会**等比缩放居中**，不会被拉扁；一页一张图，
列表里可以上下移动调页序。

## 跑起来

下载 `dist/Img2Pdf.exe` 双击。不用安装，也不需要管理员权限。

从源码跑（要 tkinter，Python 3.8+ 自带的那个就行）：

```bash
python src/main.py
```

### 命令行模式（批处理 / 脚本里调）

带参数就是不弹窗直接干活，适合"每个星期都要转一次"的重复劳动：

```bash
# 一个目录里的图全转成 A4、页边距 20 磅
Img2Pdf.exe 发票\2026年1月 -o 报销.pdf --paper A4 --margin 20

# 显式给文件，顺序就是页序
Img2Pdf.exe 封面.jpg 正文1.jpg 正文2.png -o 材料.pdf --title "3月报销材料"

Img2Pdf.exe --help        # 用法
Img2Pdf.exe --version     # 版本号 + 仓库地址
```

不给 `-o` 就落在第一张图旁边，叫 `<首图名>等N张.pdf`。
退出码：成功 0，读不了 1，参数错 2 —— 在批处理里能判断。

## 体积和"无损"到底验没验

验过，而且是用**第三方阅读器**验的，不是自己写自己查：

- 一张 3840×2400 的系统壁纸真照片（1,602,752 字节）转完 1,603,709 字节，
  **多出来的 957 字节就是 PDF 外壳**，图像数据一点没动
- 用 MuPDF（PyMuPDF）把 PDF 里的图像对象取回来，**和源文件逐字节全等** → 确实没重压
- 各种形状的样本（灰度/RGB/RGBA/调色板/1-2-4-16 位/CMYK/4:2:0/渐进式）
  逐个让 MuPDF 打开、按 1 磅 = 1 像素渲染回来，和原图像素比，平均差 ≤ 容差

⚠️ 这里的 PyMuPDF / Pillow **只在开发期验收脚本里**（`tests/verify_fitz.py`），
不进 `src/`、不进 exe、不是运行依赖。PyMuPDF 是 AGPL 协议，
把它打进产品就和"MIT 可自由商用"这句话冲突了。

## 安全设计

- **不联网**：代码里没有任何 socket / urlopen，文件不会离开这台电脑
- **不改原图**：只读输入，输出写到新文件；目标被占用（正开着）时会自动换个名字写，
  不会把用户已有的 PDF 覆盖坏
- **坏图跳过不中断**：一批 50 张里有 1 张损坏，结果是 49 页 + 一行"跳过了谁、为什么"
- **写一半断电不留半个 PDF**：先写临时文件再 `os.replace`

## 自己打包

```bash
# 用装了 PyInstaller 的那个 venv（Python 3.8 + tkinter 那套），别用系统 Python
python build.py
```

产物 `dist/Img2Pdf.exe`（约 7.6 MB，不含 UPX 压缩 —— 压过的 exe 杀软误报明显多）。
`build.py` 会按 `src/core.py` 里的 `VERSION` 生成版本元数据再传给 PyInstaller，
所以右键 → 属性 → 详细信息里的 0.1.0 和界面署名那个 v0.1.0 永远是一致的。

## 技术说明

- **零第三方依赖**：只用 Python 标准库（`zlib` / `struct` / `tkinter` / `ctypes`）
- PDF 是手写的：对象号、xref 表、`/Catalog /Pages /Page /XObject`、
  `cm` 放置矩阵、`BI...ID...EI` 内联图像、UTF-16BE 元数据字符串
- PNG 解码也是手写的：块遍历 + 5 种行滤波（None/Sub/Up/Average/Paeth）+
  位深 1/2/4/8/16 展开 + 调色板与 `tRNS`
- 核心逻辑全在 `src/core.py`，纯函数不碰界面 → 单测可以直接跑
- 界面 `src/ui.py`：DPI 感知（200% 缩放的屏上不糊）、转换在后台线程跑
  （几百张图也不会把窗口卡成"未响应"）、页序可上下移动
- 图标 `src/icon.py` 纯标准库现画（手写 PNG 编码再包 ICO），不依赖美术素材
- 版本号（`core.VERSION`）、中文名（`core.TOOL_CN`）、简介（`core.SUMMARY`）、
  页边距默认值（`core.DEFAULT_MARGIN`）、品牌信息（`src/brand.py`）各只有一个来源

## 界面署名

底部有一行「制作 by 允澈工坊 · 图片转 PDF v0.1.0」，点它弹关于窗：
工具名和版本、一句话简介、仓库地址、许可协议、运行环境、联网情况。
仓库那行显示成 `github.com/...`（不带协议头，窄窗口里不会撑成两行），
点「复制仓库地址」复制的是完整地址，包括 `https://`。

## 测试

```bash
python -m unittest discover -s tests -p "test_*.py"   # 131 项：PNG 每种位深/滤波、
                                                       # JPEG 头、EXIF、排版数学、
                                                       # PDF 结构、CLI 参数
python tests/smoke_ui.py                              # 66 项界面冒烟（要 tkinter）
python tests/verify_fitz.py                           # 87 项：拿 MuPDF 当独立读者验收
python tests/mutate_probe.py                          # 把 probe 改坏，要求测试必须变红
python tests/mutate_fitz.py                           # 同上，针对真阅读器验收脚本
python tests/e2e_shot.py                              # 真开界面走一遍，顺手出 6 张配图
```

后两个是"测试的测试"：把内核里某一行故意改错（比如 alpha 掩膜 0/255 对调、
DPI 换算整个删掉），如果所有断言还是全绿，说明那条路我们其实没验到。
目前 6 个变异全部被抓、probe 的 9 个变异全部被抓。

跑测试统一用带 tkinter 的那个解释器，并带上 `PYTHONUTF8=1`：

```bash
PYTHONUTF8=1 PYTHONPATH=src python -m unittest discover -s tests -p "test_*.py"
```

## 已知边界

- 只处理图片 → PDF。**不做** PDF 合并、拆分、加水印、压缩（那是另一个坑，
  调研过，工作量比这个工具全部加起来还大）
- 隔行 PNG 直接拒；`iTXt`/`zTXt` 之类的辅助块会忽略（不影响像素）
- 大图（4000 万像素级）能转，但纯 Python 解 PNG 扫描线会慢，
  JPEG 不受影响（不解析像素）
- 不做 OCR、不做图片压缩、不认 PDF 里的文字

## 许可

MIT · 允澈工坊（[yunche-workshop](https://github.com/yunche-workshop)）
