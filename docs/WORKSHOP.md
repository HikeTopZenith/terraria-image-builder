# 发布与分发：创意工坊 / 给朋友玩 / 录制功能介绍

> 配套文件：`icon.png`（80×80，已生成，用项目自身方块色渲染的骷髅图标，可随时替换）> 版本：v1.2.0

---

## 一、给朋友玩的两种方式

### 方式 A：直接传 .tmod（最快，适合一两个朋友）

1. 把 `TerrariaImageBuilder.tmod`（约 15MB）发给朋友
2. 朋友放到：`%USERPROFILE%\Documents\My Games\Terraria\tModLoader\Mods\`
   （本机「文档」被重定向到 D:\文档，所以是 `D:\文档\My Games\Terraria\tModLoader\Mods\`）
3. 启动 tModLoader → 模组管理 → 启用它 → 重载

**注意**：tModLoader 版本必须兼容（本次构建环境是 1.4.4.9+2026.8.3.0，`MinimumApiVersion: 2026.4`）。
朋友版本太老会报 API 版本不匹配。

### 方式 B：创意工坊订阅（适合公开，见第二节）

订阅后 tModLoader 自动下载，朋友不用碰文件。

### 关键：只给模组他们玩不了

模组是"执行器"，真正把图片算成方块的是**外端 Python 程序**。所以一起发的东西要包括：

| 必须给 | 说明 |
|---|---|
| `ui/` `engine/` `vendor/` | 外端程序本体 |
| `启动外部建造台.bat` | 双击启动 |
| Python 环境 | numpy + Pillow，或打包成 exe（推荐，见下） |

**可以不给**（减少体积，也避免暴露调试工具）：

- `tools/`（XNB 解码、色库生成，属于开发期工具）
- `assets/`（6.9MB 贴图缓存，只有重跑色库才需要）
- `tests/`（测试）

打包成 exe 的做法（还没做，需要时告诉我）：

    pip install pyinstaller
    pyinstaller --noconsole --onefile --name "泰拉图像建造台" \
        --add-data "engine;engine" --add-data "ui;ui" --add-data "vendor;vendor" \
        ui/app.py

---

## 二、上传创意工坊

### 前置检查（缺一不可）

- [x] `icon.png` 80×80 —— **已补**（之前缺失，会卡住发布）
- [x] `mod.hjson` 有 Name / Author / Version / Description / MinimumApiVersion
- [ ] Steam **已登录且在运行**（发布走 Steam UGC，必须在线）
- [ ] 首次发布要同意创意工坊法律协议（会弹一次窗）
- [ ] 游戏内至少成功构建过一次（Publish 按钮才会亮）

### 步骤

1. 启动 tModLoader（保持 Steam 在线）
2. 主菜单 → **Workshop** → **Develop Mods**
3. 列表里选中 `TerrariaImageBuilder` → 先点 **Build & Reload**（必须成功）
4. 再点 **Publish**（部分版本叫 *Publish Mod*）
5. 填工坊信息：
   - **Title**：`泰拉图像建造台 Terraria Image Builder`
   - **Description**：粘贴下面第三节的文案
   - **Tags**：`Utility`、`Tools`、`Client Side`（选与建造/工具相关的）
6. 提交后进入审核队列，通常几分钟到几小时

### 更新版本

改 `mod.hjson` 的 `Version`（或 `build.txt` 里的 version）→ 重新 Build & Reload → 再点 Publish，
同一个工坊条目会更新，不会新建。

### 常见被拒原因

- Description 里有外链/联系方式（工坊禁止导流）
- 缺少图标或图标非 80×80
- 声称兼容其他模组但实际不兼容
- 上传了什么奇怪的东西 —— 你这里只上传 .tmod，**外端 Python 程序不在工坊里**，符合规则

---

## 三、功能介绍文案（可直接粘到工坊 Description）

### 中文

**把你的图片，变成泰拉瑞亚里的方块像素画。**

导入一张图 → 程序自动匹配原版方块 → 分块发给游戏 → 站在原地看它自己长出来。

**核心能力**

- **CIEDE2000 色差匹配**：不是简单的 RGB 比色，而是按人眼感知算色差。另保留传统 LAB 算法可切换
- **贴图级色库**：35+ 种原版方块的固有色直接从前作贴图采样（剔除阴影与烘焙光照），不是手填的估算值
- **三项可选微调**（默认全关，不影响原版手感）
  - 优先保色相：压掉"红变棕、蓝变紫"的跑偏，人像/角色向
  - 纯色方块优先：大面积背景优先用低纹理方块，减少纹理噪点
  - Floyd–Steinberg 抖动：渐变区做误差扩散，消除色块断层
- **自定义色卡**：顽固偏色区域可以手动指定"这个颜色 → 这个方块"，最多 50 条，随工程保存
- **离线预览**：不连游戏也能看到方块化结果，与蓝图用同一套匹配逻辑，所见即所得
- **预处理**：裁剪（拖拽框选）、旋转、镜像、亮度对比度、自动缩放到目标方块数
- **工程包 .tibp**：蓝图 + 全部参数一起存，导回即可重建；旧工程完全兼容
- **安全**：单次 20 万方块硬上限、坐标/方块 ID 全量校验、断线自动清队列、一键撤销全部

**建造控制（游戏内）**

- `O` 标记原点 · `P` 开始/暂停 · `U` 撤销全部（三个键都可以在 config.json 里改）
- 4 档放置速度 + 自适应降速 + 自动避让玩家位置

**推荐搭配**

本模组**只负责**把图片变成方块。灯光、飞行、清场这些请交给专业工具：

- **HEROsMod**（全亮模式 / 建造工具）
- **Better Zoom**（镜头缩放，远观像素画必备）

**使用流程**

1. 想让模组生效：tModLoader 里启用本模组 → 进世界
2. 桌面上运行**外端建造台**（Windows + Python，见下方说明）
3. 导入图片 → 调匹配参数 → 生成预览 → 连接游戏 → 开始建造
4. 需要远观就开 Better Zoom，需要看清就开 HEROsMod 全亮

> 外端程序（把图片算成蓝图的桌面工具）不在工坊内，是独立的 Windows 程序，
> 因为创意工坊只接受 .tmod 文件，而图像处理需要 Python 运行环境。

### English (short, for the description field)

**Turns your images into Terraria block pixel art.**

Import an image, the companion desktop app matches vanilla blocks with CIEDE2000 color science,
splits it into chunks and streams them to this mod — you stand back and watch the build appear.

- CIEDE2000 perceptual matching (legacy LAB still selectable)
- Palette sampled from real vanilla tile textures (shadow/highlight removed)
- Optional: hue-preserving, pure-block priority, Floyd–Steinberg dithering (all off by default)
- Custom color card (≤50 rules) for stubborn color casts
- Offline preview using the same matching path as the blueprint
- Safety: 200k block hard cap, coordinate/ID validation, queue cleared on disconnect, full undo

**This mod only places blocks.** For lighting, flying and area clearing use
**HEROsMod** (fullbright / build tools) and **Better Zoom** (camera zoom).

Hotkeys: `O` set origin · `P` start/pause · `U` undo all (rebindable).

> The desktop app that converts images is distributed separately: the Workshop only accepts .tmod files.

---

## 四、录制功能介绍视频的思路

### 一句话方案

**OBS 录画面 + 单独录音 + Whisper 出字幕 + Remotion 做字幕动效和片头**。
四步可以完全分离，录完再剪，不需要一次到位。

### 第一步：录屏

用 **OBS Studio**（免费）：

- 来源：**窗口采集**（tModLoader 窗口 / 建造台窗口各一个场景，方便切换）
- 输出：1080p 60fps，CRF 20~23；游戏内 60fps 很重要，方块一片片长出来那种"生长感"靠帧率
- 建议**先录游戏素材、再录软件操作**，最后剪在一起 —— 分次录比一次录完轻松得多

拍摄顺序建议（约 3~5 分钟成片）：

| 段落 | 画面 | 时长 |
|---|---|---|
| 开场钩子 | 成品的像素画全景（Better Zoom 拉远 + HEROsMod 全亮） | 5s |
| 问题 | 原图 vs 成品并排 | 3s |
| 核心流程 | 导入 → 预览 → 连接 → 开始建造 | 20s |
| 加速段 | 建造过程 4~8 倍速（保留开头和结尾正常速） | 15s |
| 特色功能 | 三个开关前后对比（尤其抖动的断层消除） | 30s |
| 自定义色卡 | 点选取色 → 绑定方块 → 修正偏色区 | 20s |
| 收尾 | 远观全景 + "推荐搭配 HEROsMod / Better Zoom" | 5s |

### 第二步：录音

不要边操作边录 —— 手指点鼠标的声音会全进去。做法：

1. 先看录像，写一份逐段解说词（每段 2~3 句，控制语速 ~4 字/秒）
2. 用手机或任意录音软件**单独录**，或用 **Win11 自带的语音听写**/剪映的字幕生成做草稿
3. 想要好听：OBS 的「噪声音频抑制」+ 「限幅器」两个滤镜就够，不必买声卡

### 第三步：字幕

三条路，从省事到专业：

| 方案 | 做法 | 适合 |
|---|---|---|
| **剪映 / CapCut（最省事）** | 导入音频 → 自动识别字幕 → 手动改错 → 导出 | 想当天出片 |
| **Whisper 本地转写（可控、免费）** | `faster-whisper` 转出 SRT，再拖进剪辑软件 | 想要时间轴精确 |
| **Remotion（我要给你的方案）** | 用 React 写视频，字幕是代码里的一行数组，改起来像改文本 | 想反复迭代、要统一视觉风格 |

**Remotion 方案的具体做法**（这台机器上已经装好了 Remotion 相关的 skill）：

1. 用 `faster-whisper` 把解说音频转成**词级时间戳**的 JSON
2. Remotion 工程里：`<OffthreadVideo src={录像}>` 垫底，上面一层是字幕组件
3. 字幕组件读 JSON，按时间显示，可做「逐词高亮」「淡入上浮」等动效 —— 全是 CSS/JS
4. 片头、参数对比图、箭头标注等**图形层**也用 React 画，和字幕同一套时间轴
5. `npx remotion render` 直接出 MP4

这条路的好处是**字幕后期的返工成本极低**：说错一句只要改数组里一行，不用重新对轴。

### 第四步：成品前自检清单

- [ ] 声音比画面**早**半秒以内就不难受，差太多要手动对齐
- [ ] 字幕一行 ≤ 18 个汉字，两行以内
- [ ] 专有名词统一（CIEDE2000 / HEROsMod / Better Zoom 别写成别的）
- [ ] 每个开关都给出"开 vs 关"的对照画面，否则观众不知道有什么用
- [ ] 结尾给一个明确的下一步（订阅 / 下载外端程序 / 看文档）

### 我可以帮你做的部分

- 写**逐段解说词**（按上面的分镜表，中文口语化，可直接照着念）
- 搭 **Remotion 工程**：字幕动效 + 片头 + 参数对比图，输入 SRT/JSON 即可渲染
- 用本地模型出**片头封面图 / 缩略图**（ComfyUI 的 Qwen-Image）
- 把建造过程的录像做成**延时加速 + 进度条叠加**的小动画

---

## 五、发布前自检脚本（已就位）

    cd D:\deepseek\TerrariaImageBuilder
    python tools\check_workshop_ready.py

会检查：icon.png 是否 80×80 合法 PNG、mod.hjson 必需字段与版本号格式、
.tmod 是否比编译产物新、mod.hjson 里有没有工坊忌讳的外链。
当前状态：**程序侧检查全过，阻塞 0 项**。

脚本会自己解决"文档目录被重定向"这个坑（本机「文档」= D:\文档）。

### 现在的图标

`icon.png`（80×80）方案 **B 竖直对半**：左半是照片质感的「山 + 太阳」，
右半是同一幅图吸附到原版方块固有色、并叠了方格暗线的方块化结果 —— 一眼就是"图片变方块"。
要换图案随时说，保持 80×80 PNG 即可。

## 六、工坊描述（直接复制粘贴，短版）

    **把你的图片，变成泰拉瑞亚里的方块像素画。**

    导入一张图 → 自动匹配原版方块 → 分块发给游戏 → 站在原地看它自己长出来。


    【下载桌面端（必须）】
    创意工坊只能分发模组本身，把图片算成方块的桌面程序在这里：
    https://github.com/HikeTopZenith/terraria-image-builder/releases/latest
    解压即用，不需要安装 Python。

    【核心能力】
    · CIEDE2000 色差匹配：按人眼感知算色差，不是简单 RGB 比色；传统 LAB 算法可切换
    · 贴图级色库：方块固有色直接从原版贴图采样（剔除阴影与烘焙光照），不是手填估算值
    · 三项可选微调（默认全关）：优先保色相 / 纯色方块优先 / Floyd-Steinberg 抖动
    · 自定义色卡：顽固偏色区可手动指定"这个颜色 → 这个方块"，最多 50 条，随工程保存
    · 离线预览：不连游戏也能看到方块化结果，与蓝图同一套匹配逻辑
    · 预处理：拖拽裁剪、旋转、镜像、亮度对比度、自动缩放到目标方块数
    · 工程包 .tibp：蓝图 + 全部参数一起保存，旧工程完全兼容
    · 安全：单次 20 万方块硬上限、坐标与方块 ID 全量校验、断线自动清队列、一键撤销全部

    【游戏内操作】
    O 标记原点 · P 开始/暂停 · U 撤销全部（三个键都可在 config.json 自定义）
    4 档放置速度 + 自适应降速 + 自动避让玩家位置

    【推荐搭配】
    本模组只负责把图片变成方块。灯光与镜头请交给专业工具：
    HEROsMod（全亮模式 / 建造工具）、Better Zoom（镜头缩放）

    【使用流程】
    1. tModLoader 启用本模组并进入世界
    2. 运行外端建造台（独立 Windows 程序，用于把图片算成蓝图）
    3. 导入图片 → 调参数 → 生成预览 → 连接游戏 → 开始建造

    【English】
    Turns your images into Terraria block pixel art. CIEDE2000 perceptual matching,
    palette sampled from real vanilla tile textures, optional hue-preserving /
    pure-block priority / Floyd-Steinberg dithering, custom color-card rules,
    offline preview sharing the same matching path. Safety: 200k block cap,
    full coordinate/ID validation, queue cleared on disconnect, one-key undo.
    This mod only places blocks — for lighting and camera use HEROsMod and Better Zoom.
    Hotkeys: O set origin, P start/pause, U undo all (rebindable).

## 七、上传这一步为什么必须你来做

发布不是传文件，而是调 **Steamworks 的 UGC 接口**，需要：
Steam 客户端登录态 + 创意工坊法律协议同意 + 游戏内来操作。

技术上没法代替：一端没有你的 Steam 会话，另一端发布按钮只存在于游戏窗口里。
所以流程是：**我把程序侧全部备好 → 你在游戏里点 4 下 → 有报错把原文发我。**


---

## 八、发行压缩包（已打好并实测）

> 给谁都用这一个包：自己留着、发给朋友、传网盘、挂 Release 附件，什么都不用改。

产物：`D:\deepseek\output\release\TerrariaImageBuilder-v1.2.0.zip`（45 MB）

内含三样：

    TerrariaImageBuilder.tmod   模组（放进 tModLoader\Mods\）
    外端建造台\                 双击 TerrariaImageBuilder.exe 运行
    使用说明.txt                 给朋友的图文步骤（UTF-8 BOM，记事本不乱码）

**已实测**：把 zip 解到另一个目录、直接双击 exe，2 秒出窗口，能独立运行（不依赖构建目录）。

### 打包方式与一个坑

用 PyInstaller 打**目录版**（`--onedir --noconsole`），不是单文件版。
单文件版（`--onefile`）实测起不来：弹出标题为 `Error` 的 Tk 对话框，
原因是 Tcl/Tk 运行时数据没有进单文件包（目录版里有 `_internal\tcl8`、`tk86t.dll` 才正常）。
PyInstaller 的 warn 文件里只有一条无关的 `tkinterdnd2 (optional)` 警告，看不出线索，
是靠"目录版正常 / 单文件版报错"的对照定位出来的。

打包环境是独立的 `D:\deepseek\_pack\venv`（numpy 2.5.4 / Pillow / PyInstaller 6.22.3），
**没有污染 ComfyUI 的 venv**。源码先复制到纯 ASCII 路径 `D:\deepseek\_pack\src` 再打包 ——
PyInstaller 处理不了含中文的 MSYS 风格路径（`/d/文档/...` 会被解析成 `\d\文档\...`）。

### 复现打包

    D:\deepseek\_pack\build_exe.bat        # 打 exe（目录版）
    python D:\deepseek\_pack\make_release.py # 连同 .tmod 一起压成 zip

### 朋友那边可能遇到的

- **杀毒误报**：PyInstaller 产物常见，加白名单
- **必须先装模组**：外端连的是 ws://127.0.0.1:8765，模组没启用就一直是"未连接"
- **72MB 的 _internal 不能单独删**：那是 Python 运行时

