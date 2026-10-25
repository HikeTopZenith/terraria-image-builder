# 开发框架说明 · Terraria Image Builder

> 给"要改这个项目的人"看的：模块边界、数据流、契约、扩展点、踩过的坑。
> 想了解怎么用，看 `MANUAL.md`；想了解怎么发布，看 `WORKSHOP.md`；想接上下文，看 `PROGRESS.md`。

---

## 一、总体架构

```
┌──────────────────────────────┐        ┌───────────────────────────┐
│  外端建造台（Python + tkinter）│        │  tModLoader 模组（C#）      │
│                              │        │                           │
│  ui/app.py     界面与交互      │        │  CommunicationManager     │
│  engine/       色彩与蓝图计算   │◄─────►│  ExecutionEngine          │
│  client.py     WebSocket 客户端│  指令   │  SafetyGuard / UndoManager │
│                              │  状态   │                           │
└──────────────────────────────┘        └───────────────────────────┘
        图片素材                         ws://127.0.0.1:8765            游戏世界
```

职责切得很硬：

| 层 | 只做 | 明确不做 |
|---|---|---|
| 外端 | 读图、匹配方块、生成蓝图、分块、发指令、离线预览 | 不碰游戏内存、不放置方块 |
| 模组 | 收指令、校验、分帧放置、限速、避让、撤销、回传状态 | 不做任何图像处理、不做匹配计算 |

这个边界是刻意的：**图像算法迭代快、游戏 API 迭代慢**，分开以后改算法不用动模组，改模组不用重装桌面端。

---

## 二、数据流（一次建造的完整链路）

```
1. 图片 (PNG/JPG)
      │  engine/input.py           载入、拒绝 webp/heic、提取 alpha
      ▼
2. 预处理数组 (H,W,4)
      │  engine/input.apply_preprocess  裁剪/旋转/镜像/亮度/缩放
      ▼
3. 匹配网格 (H,W) block_id
      │  engine/engine.BuildEngine._match_array   ← 全项目唯一的匹配入口
      │      ├─ matcher.match_rgb_batch        一维批量（默认）
      │      └─ matcher.match_image_dithered   2D 抖动路径（dither=True）
      ▼
4. 蓝图 Blueprint（分块，每块 ≤256×256，块内相对坐标）
      │  engine/contract.blueprint_to_chunked_command
      │      坐标换算成相对原点的全局坐标 + 递归排序 + SHA-256
      ▼
5. 指令 JSON {Type:"PlaceBlocks", Chunks:[...], Checksum}
      │  engine/client.py (aiohttp, 后台线程)  ──ws──►
      ▼
6. 模组 CommunicationManager：解析 → ChecksumValidator 校验 → 分发给 ExecutionEngine
      ▼
7. ExecutionEngine：分块队列 → 每帧放 N 块 → SafetyGuard 七道过滤 → 写 tile → 记录撤销台账
      ▼
8. 状态回传：InstructionReceived / ChunkProgress / AllCompleted / ExecutionError / PlayerPosition / ServerHello
      ▼
9. 外端把状态推进 UI（线程安全：后台线程只往 queue 里塞，主线程 after(60) 消费）
```

---

## 三、外端模块（Python，8241 行含测试与工具）

| 文件 | 行数 | 职责 | 关键约束 |
|---|---|---|---|
| `engine/colorimetry.py` | 124 | CIEDE2000 标量 + (m,n) 矩阵、Δh'、色相角 | 必须过 Sharma 34 组参考值 |
| `engine/matcher.py` | 611 | 匹配引擎：算法可选、三档模式、保色相、纯色优先、抖动、自定义色卡 | `match_pixel` 与 `match_rgb_batch` 必须同源同结果 |
| `engine/blocks.py` | 251 | 方块定义 + TileID 映射；`build_block_library(use_texture_palette)` | **全项目唯一取色入口**，禁止在别处另取 |
| `engine/palette_auto.py` | 172 | 【生成物】贴图采样的固有色/方差/纯色度 | 由 `tools/build_palette.py` 生成，不要手改 |
| `engine/contract.py` | 637 | 蓝图 schema、指令生成、校验和、.tibp 工程包、settings 归一化 | 校验和口径必须与 C# 逐字节一致 |
| `engine/engine.py` | 388 | 顶层 facade：`BuildEngine`、`EngineConfig`、`build_match_config` | **唯一匹配入口**；蓝图与预览共用 |
| `engine/blueprint.py` | 163 | 分块、蓝图导入导出 | 单块 ≤256×256 |
| `engine/input.py` | 201 | 载入与预处理 | 拒绝 webp/heic/gif/psd |
| `engine/preview.py` | 149 | 离线预览上色 | **只上色，不做匹配、不做颜色换算** |
| `engine/presets.py` | 155 | 四套预设 | 不改变既有默认手感 |
| `engine/client.py` | 198 | WebSocket 客户端（asyncio 后台线程） | `start()` 必须等事件循环就绪；连接带 8s 超时 |
| `ui/app.py` | 2199 | 三标签主界面、色卡面板、诊断包、断点续传 | 不写第二套匹配逻辑，一律走引擎 |
| `tools/xnb_lzx.py` | 439 | 纯 Python LZX 解码器 | 用 makecab 造柜验证过 |
| `tools/xnb_extract.py` | 245 | XNB → PNG | 幂等，带 --ids/--limit/--force |
| `tools/build_palette.py` | 419 | 贴图 → palette_auto.py | 实心帧口径 + 降采样一致律自检 |

### 三个"单一入口"是硬规矩

1. **取色**：只从 `blocks.build_block_library()`。预览、蓝图、UI 都拿它给的 `BlockColor.main_rgb`，不许各处自己算 —— 否则预览和游戏会对不上。
2. **匹配**：只从 `BuildEngine._match_array()`。离线预览与正式蓝图走同一条路，"所见即所得"才成立。
3. **配置构造**：只从 `engine.build_match_config(settings)`。UI、预设、工程包都调它，避免缺省值分叉。

---

## 四、模组端模块（C#，2856 行）

| 文件 | 行数 | 职责 |
|---|---|---|
| `TerrariaImageBuilder.cs` | 212 | Mod 主类：读配置、解析热键、装配各模块、写启动日志、校验和自检 |
| `TerrariaImageBuilderSystem.cs` | 128 | 每帧驱动（`PostUpdateEverything` + `On_Main.Update` 双保险）、离世界清队列、首次进世界给下载提示 |
| `Bilingual.cs` | 76 | 双语助手；跨线程提示走 `EnqueueToPlayer`，主线程 Flush |
| `ModConfig.cs` | 116 | config.json 读写；首次启动落默认文件 |
| `ModLogger.cs` | 46 | 按天日志 |
| `Communication/MessageContract.cs` | 153 | 指令与 6 类状态消息的数据类 |
| `Communication/ChecksumValidator.cs` | 130 | 递归按键名排序 + 紧凑 JSON + SHA-256；含黄金向量自检 |
| `Communication/ICommunicationProvider.cs` | 24 | 通道接口（只收发原始 JSON） |
| `Communication/WebSocketProvider.cs` | 196 | 本地 WebSocket 服务端（HttpListener） |
| `Communication/JsonPollingProvider.cs` | 180 | JSON 文件轮询通道（外端未实现，仅保留） |
| `Communication/CommunicationManager.cs` | 444 | 解析 → 校验 → 分发 → 状态回传 → 断线清队列；`ProtocolVersion` / 下载地址常量 |
| `Execution/ExecutionEngine.cs` | 619 | 分块队列（ConcurrentQueue）+ 分帧限速 + 自适应降速 + 玩家避让 + 暂停状态机 |
| `Safety/SafetyGuard.cs` | 275 | 七道过滤：离谱坐标 → 世界边界 → 矩阵就绪 → TileId 越界 → 不可放置 → 总上限 → 跳过已有 |
| `Undo/UndoManager.cs` | 129 | 全部撤销 / 分段撤销，还原方块数据 |
| `Input/ImageBuilderPlayer.cs` | 128 | O / P / U 热键（键名可在配置改） |

### 几个必须知道的设计点

- **坐标用 long 传入** `CanPlace(long,long,...)`：外端给的是 int，但"相对坐标 + 原点"的加法在 int 上会溢出并绕过所有边界判断。
- **分帧节奏**：`MaxBlocksPerFrame` 由 `PlacementSpeedLevel` 决定（4/12/24/32），并带自适应降速；每帧最多推进一次（帧号没变时退化成 4ms 墙钟窗，防止暂停后永久卡住）。
- **暂停状态机**：无活不进 Paused；Resume 强制立 `_isRunning`；每帧 `ReconcileState` 自愈；运行中 10s 零推进记告警。
- **断线清队列**：`OnProviderDisconnected` → `AbortPendingTask` → `_chunkQueue.Clear()`，重连**不自动续跑**（续跑是外端的事，见断点续传）。

---

## 五、通信契约（改之前务必看这一节）

### 外端 → 模组

```json
{
  "Type": "PlaceBlocks",
  "OriginX": 4200, "OriginY": 430,
  "ChunkCount": 1, "TotalBlocks": 3,
  "Chunks": [{"Index":0,"DelayMs":200,
              "Blocks":[{"X":0,"Y":0,"TileId":0}]}],
  "DelayBetweenChunksMs": 200, "DelayWithinChunkMs": 0,
  "MaxTotalBlocks": 0, "SkipExistingBlocks": false,
  "Timestamp": 1792718267209, "Checksum": "..."
}
```

- **坐标**：`X/Y` 是相对 `OriginX/OriginY` 的全局坐标（外端已把分块偏移加进去）
- `MaxTotalBlocks = 0` 表示不限；单任务硬上限 **200,000**
- **校验和**：移除 `Checksum` → 递归按键名 Ordinal 排序 → 紧凑 JSON（`:` 与 `,` 无空格、非 ASCII 不转义）→ UTF-8 → SHA-256 → 小写十六进制
- `Checksum` 缺失时跳过校验（兼容旧客户端）

### 模组 → 外端

| MsgType | 说明 |
|---|---|
| `ServerHello` | 连上即发：模组版本 / 协议版本 / 桌面端下载地址（v1.2.1 新增，老外端会忽略） |
| `InstructionReceived` | 指令已被接受 |
| `ChunkProgress` | 某分块完成（带 `ChunkIndex`、累计块数）—— **断点续传靠它记账** |
| `AllCompleted` | 整批完成（带总耗时） |
| `ExecutionError` | 执行异常 / 安全摘要 |
| `PlayerPosition` | 玩家坐标与地表高度（外端发 `{"Type":"GetPlayerPos"}` 索取） |

### 两个版本号，别搞混

- **模组版本**（mod.hjson / build.txt 的 `Version`）：发版用
- **协议版本**（`CommunicationManager.ProtocolVersion` ↔ `ui/app.py` 的 `CLIENT_PROTOCOL_VERSION`）：只有消息语义变了才递增；不一致时外端弹窗要求更新

---

## 六、工程结构

```
terraria-image-builder/
├─ mod/                        tModLoader 模组源码（放 ModSources 里编译）
│  ├─ build.txt                ★ 发布流程读它（老式），必须与 mod.hjson 一致
│  ├─ description.txt          ★ 就是工坊页面显示的完整描述（BBCode）
│  ├─ icon.png                 80×80
│  ├─ mod.hjson                构建流程读它（新式）
│  ├─ TerrariaImageBuilder.csproj
│  ├─ Communication/ Execution/ Safety/ Undo/ Input/ Localization/
│  └─ *.cs
├─ desktop/                    外端建造台
│  ├─ engine/  ui/  tools/  tests/  vendor/
│  ├─ requirements.txt         numpy / Pillow / aiohttp（三个都必须）
│  ├─ run_tests.bat            双击跑全部测试
│  └─ 启动外部建造台.bat
├─ docs/                       PROGRESS / MANUAL / WORKSHOP / VIDEO_SCRIPT / ARCHITECTURE
└─ .github/workflows/tests.yml CI（需 token 带 workflow 权限）
```

---

## 七、扩展指南

### 加一个方块

1. `engine/blocks.py`：加 `BlockColor(...)` 条目 + `BLOCK_NAME_TO_TILE_ID` 映射（**TileID 必须是原版真实值**）
2. 跑 `python tools/build_palette.py` 重新生成 `palette_auto.py`（会自动从贴图采样）
3. 跑 `run_tests.bat`，重点看贴图覆盖率是否仍为 1.0

> TileID 怎么确认：用游戏自报的 `TileID.Search.GetName` 导出对照表（本项目有一份在 `desktop/assets/tile_names.json`，693 条）。

### 加一个匹配开关（最容易踩坑的改动）

必须同时改 6 处，漏一处就会出现"UI 里能勾但不起作用"或"工程包存不住"：

1. `matcher.MatchConfig` 加字段（**默认值必须是关闭态**）
2. `matcher._score` / `_quantize_row` 里实现，且保证「关掉 = 与改动前逐像素一致」
3. `contract.build_settings_dict` / `normalize_settings` 加入键名（缺省走默认，未知键原样保留）
4. `presets.py` 四套预设显式写上默认值（保持既有手感）
5. `engine.EngineConfig` 同名透传字段 + `build_match_config` 映射
6. `ui/app.py` 控件 + `_match_settings_dict` / `_apply_match_settings`

配一条测试：**开关全关时结果与旧行为 `array_equal`**（`test_matcher_v2.py::test_switch_off_matches_legacy_pixelwise` 就是这么写的）。

### 加一条协议消息

1. `MessageContract.cs` 加数据类（新字段可加，**不要改既有字段语义**）
2. `CommunicationManager` 发送/分发
3. `client.py` 与 `ui/app.py::_handle_status` 处理（未知 MsgType 必须忽略而不是报错）
4. 如果影响校验和口径，**同步更新两端的黄金向量**（`tests/test_checksum_golden.py` 与 `ChecksumValidator.SelfCheck`）

### 改色库采样口径

只改 `tools/build_palette.py`。两条铁律：

- **不要用"亮带均值"**：会系统性调亮整个色库，表现为"预览对、游戏暗"
- 自检要用**降采样一致律**（平铺 10×10 → 降采样 → 与固有色比对），且**不能挑最接近的那帧**去比（那是循环论证）

---

## 八、构建 / 测试 / 打包 / 发布

### 编译模组

```bash
export PATH="/c/Users/h/.dotnet:$PATH"     # 必须！否则 MSBuild 用了只有 6.0 的宿主
cd D:/deepseek/TerrariaImageBuilder
dotnet build TerrariaImageBuilder.csproj -c Release -v m
```

> 不设 PATH 会在**打包阶段**报 `MSB3073 … 缺 .NET 8`，看起来像编译错误，其实是宿主不对。
> 想只验证编译不打包（游戏开着也能跑）：加 `-p:BuildMod=false`。

### 跑测试

双击 `desktop/run_tests.bat`，或：

```bash
cd desktop
PYTHONPATH=$PWD TEMP=D:/deepseek/_tmp python tests/test_engine.py   # 以及其余 5 个
```

六个文件：`test_engine` / `test_matcher_v2` / `test_colorimetry` / `test_place_command` / `test_pipeline_v2` / `test_checksum_golden`。

### 打包桌面端（PyInstaller）

```
pip install pyinstaller numpy pillow aiohttp     # aiohttp 漏了就连不上游戏
pyinstaller --onedir --noconsole --name TerrariaImageBuilder \
  --add-data "engine;engine" --add-data "ui;ui" --add-data "vendor;vendor" run_app.py
```

三个必须记住的点：

1. **必须装 aiohttp** —— 曾经漏打包，发行版界面永远"连接中…"
2. **必须 `--onedir`** —— `--onefile` 缺 Tcl/Tk 数据，启动弹一个标题为 `Error` 的 Tk 对话框
3. **源码路径不能含中文** —— PyInstaller 会把 `/d/文档/...` 解析成 `\d\文档\...` 然后找不到文件

自检（发行前必跑）：`TB_SELFTEST=1 TerrariaImageBuilder.exe` → 看同目录 `selftest.log` 是否 `RESULT=PASS`。

### 发布创意工坊

游戏内 Workshop → Develop Mods → `构建并重新加载` → `发布`。

⚠️ 这个 tML 版本（v2024.8.3.0）的**发布流程只读老式三件套**：`build.txt`（author 必需）、`description.txt`（工坊描述，Steam 用 BBCode 不是 Markdown）、`icon.png`（80×80）。`mod.hjson` 只给构建流程用。三者的 `version` / `author` 必须一致。

---

## 九、已知限制（不是 bug，别当 bug 修）

| 限制 | 原因 |
|---|---|
| 白/灰比原图暗约 7 个 L\* | 原版没有"亮中性且实心"的方块：雪与微光砖偏冷，珍珠沙是重力方块会塌方 |
| 饱和红偏砖红 | 原版最红的可用方块只有红砖/血肉块量级 |
| 预览是平涂主色，游戏里有纹理 | 预览是示意图；渲染真实贴图需要贴图资源（版权原因未入库） |
| 少数方块中文名是人工翻译 | 英文常量名已用游戏自报的 `TileID.Search.GetName` 核对过 693 条 |
| `JsonPolling` 通道外端未实现 | 仅保留给脚本对接；桌面端只用 WebSocket |

---

## 十、踩过的坑速查（改代码前扫一眼）

| 坑 | 结论 |
|---|---|
| 窗口失焦 → 引擎不推进 | 必须挂 `On_Main.Update` 兜底 |
| `GameUpdateCount` 暂停时不递增 | 不能把它当唯一去重依据，要退化成墙钟窗 |
| int 坐标溢出绕过边界判断 | `CanPlace` 用 `long` 接 |
| Python `sort_keys=True` 是递归的 | C# 端也必须递归排序，否则校验和永远失败 |
| `--noconsole` 吞掉所有报错 | 关键失败要落盘（`startup_error.log` / `selftest.log`） |
| 事件循环未就绪就 connect | `start()` 必须阻塞到循环 running |
| 续传比对"上一轮"的原点 | 等于没检查；要比输入框当前值 |