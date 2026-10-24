# TerrariaImageBuilder · 交接文档

> 新会话开始时先读这个文件，即可接上全部上下文。
> 最后更新：迭代2 完成（色差升级 + 贴图色库 + 三开关 + 自定义色卡 + 上架加固）v1.2.0

---

## 一、项目定位

把一张图片自动变成泰拉瑞亚里的方块像素画。

- **外部端**（Python + tkinter）：导入 → 预处理 → 色彩匹配 → 分块 → 发指令
- **模组端**（tModLoader 1.4.4 v1.2.0）：把方块队列分帧放进世界

**边界**：模组端只做「通信 + 分块建造 + 分帧限速 + 撤销 + 安全校验」，
光照 / 全亮 / 飞行 / 清场等交给 HEROsMod、Better Zoom。

---

## 二、路径速查

| 内容 | 路径 |
|---|---|
| 模组源码 | D:\deepseek\TerrariaImageBuilder\ |
| 模组产物 | D:\文档\My Games\Terraria\tModLoader\Mods\TerrariaImageBuilder.tmod（15.4MB） |
| 外端源码 | D:\文档\My Games\Terraria\tModLoader\ModSources\terraria_builder\ |
| 外端 UI | ...\terraria_builder\ui\app.py（1865 行） |
| 引擎 | ...\terraria_builder\engine\ |
| 开发期工具 | ...\terraria_builder\tools\（XNB 解码 + 色库生成） |
| 原始贴图缓存 | ...\terraria_builder\assets\tiles\（754 个 PNG，6.9MB） |
| 游戏原始资源 | D:\Steam\steamapps\common\Terraria\Content\Images\Tiles_*.xnb（862 个） |
| Python | D:\deepseek\ComfyUI\.venv\Scripts\python.exe（3.12 / numpy 2.5 / Pillow 12.3） |
| 配置 | D:\文档\My Games\Terraria\ModLoader\TerrariaImageBuilder\config.json |
| 日志 | D:\文档\My Games\Terraria\tModLoader\Logs\TerrariaImageBuilder\ |

---

## 三、构建与运行

### 构建模组（**必须先把 SDK 放进 PATH**）

    export PATH="/c/Users/h/.dotnet:$PATH"
    cd /d/deepseek/TerrariaImageBuilder
    dotnet build TerrariaImageBuilder.csproj -c Release -v m

> 不加 PATH 会调 `C:\Program Files\dotnet`（只有 6.0/9.0，缺 .NET 8），
> 在**编译成功之后**的打包阶段报 `MSB3073 … -2147450730`。
> 该报错不代表代码有错，别被误导。游戏开着时 .tmod 会被锁。

### 跑测试（5 个文件，共 63 项）

    cd ".../terraria_builder"
    export PYTHONPATH="$PWD" PYTHONIOENCODING=utf-8 TEMP=D:/deepseek/_tmp TMP=D:/deepseek/_tmp
    for t in test_engine test_matcher_v2 test_colorimetry test_place_command test_pipeline_v2; do
      python tests/$t.py; done

> 测试是脚本式入口（本 venv 没装 pytest）；不设 TEMP 会报 No usable temporary directory。
> 加一项：tools/ 下的色库生成脚本可重跑，见第九节。

---

## 四、模组端结构（v1.2.0，15 个 .cs）

    TerrariaImageBuilder.cs          Mod 主类（热键注册/启动日志/模块装配）
    TerrariaImageBuilderSystem.cs    ModSystem（每帧驱动 + 通信 + 离世界清队列）
    Bilingual.cs                     中英双语助手（跨线程走 EnqueueToPlayer）
    ModConfig.cs                     配置（通信/安全/速度/热键），首次启动落默认文件
    ModLogger.cs                     按天日志
    Communication/
      MessageContract.cs             消息契约（**协议零改动**）
      ChecksumValidator.cs           SHA-256 递归键排序校验（未动）
      WebSocketProvider.cs           本地 WebSocket 服务端（断开/Dispose 都上报）
      JsonPollingProvider.cs         文件轮询（连写失败 5 次 / 目录消失 → 断开）
      CommunicationManager.cs        解析→校验→分发→状态回传；断线清队列入口
    Execution/ExecutionEngine.cs     分块队列（ConcurrentQueue）+ 分帧限速 + 玩家避让
    Safety/SafetyGuard.cs            七道过滤（溢出/边界/未加载/非法ID/不可放置/上限/跳过已有）
    Undo/UndoManager.cs              全部撤销 + 分段撤销
    Input/ImageBuilderPlayer.cs      O/P/U 热键（键名可配置，非法回退默认）

### v1.2.0 的关键加固

- **断线清队列**：WebSocket/轮询断开 → `AbortPendingTask` → `_chunkQueue.Clear()`；重连**不自动续跑**
- **P 键可靠性**：无活不进 Paused；Resume 强制立 `_isRunning`；每帧 `ReconcileState` 自愈；10s 零推进告警
- **去重双保险**：帧号前进整帧去重，帧号冻结（暂停/失焦）退化为 4ms 墙钟窗
- **安全摘要**：拒绝原因分类计数 + 日志节流 + 回一条双语 ExecutionError

---

## 五、通信协议（**本次迭代未改动**）

外端 → 模组：`PlaceBlocks` + Chunks[]，坐标是相对原点的全局坐标；
`MaxTotalBlocks=0` 表示不限；Checksum 缺失跳过校验；单任务硬上限 **200,000**。

Checksum：移除 Checksum 字段 → 递归按键名 Ordinal 排序 → 紧凑 JSON → UTF-8 → SHA-256 → 小写十六进制。

模组 → 外端：`InstructionReceived` / `ChunkProgress` / `AllCompleted` / `ExecutionError` / `PlayerPosition`。

---

## 六、外端结构

    engine/
      colorimetry.py    CIEDE2000（标量 + 向量化）+ Δh' / 色相角工具
      matcher.py        匹配引擎：算法可选(ciede2000/lab) + 保色相 + 纯色优先 + 抖动 + 自定义色卡
      palette_auto.py   【生成物】原版贴图采样的无光照固有色 + 纹理方差（35 方块）
      blocks.py         方块定义 + TileID 映射；build_block_library(use_texture_palette=True) 取贴图色
      contract.py       蓝图 + 指令生成 + .tibp 工程包（含 6 个新匹配字段的向前兼容）
      presets.py        四套预设（参数未变，仅显式带上新字段默认值）
      input.py          图片加载与预处理
      blueprint.py      分块与蓝图
      engine.py         顶层 facade；**唯一匹配入口**（抖动走 2D 路径）
      preview.py        离线预览（只上色，不做任何匹配/颜色换算）
      client.py         WebSocket 客户端
    tools/
      xnb_lzx.py        纯 Python LZX 解码器（已用 makecab 造柜逐字节验证）
      xnb_extract.py    XNB → PNG 批量导出
      build_palette.py  贴图 → palette_auto.py（实心帧口径 + 降采样一致律自检）
    ui/app.py           主窗口（三标签 + 单行匹配设置 + 自定义色卡面板）

### 新增可选功能（全部默认关闭，关掉逐像素还原旧行为）

| 开关 / 字段 | 说明 |
|---|---|
| `delta_algorithm` | `ciede2000`（默认）/ `lab`（旧算法） |
| `hue_priority` | 【优先保色相】源侧彩度门 + Δh' 加权，压蓝→紫、红→棕 |
| `prefer_pure` | 【纯色方块优先】按贴图方差给低纹理方块折扣 |
| `dither` | 【Floyd-Steinberg 抖动】行向量化 + 行间严格传播 |
| `use_custom_palette` + `custom_palette` | 自定义色卡，≤50 条，随 .tibp 保存，黑名单方块自动让位 |

---

## 七、踩过的坑（改代码前必读）

| 坑 | 结论 |
|---|---|
| **Terraria XNB 容器** | flags=0x80 但 payload 是 LZX 压缩：offset10=解压后长度，offset14=2 字节**大端**块长，LZX 流从 **16** 起；位流按小端 16 位字 + 字内 MSB 优先读。用 makecab 造已知答案的柜逐字节验证解码器 |
| **色库不能取"亮带均值"** | 砍掉暗部会把整套色库系统性调亮，表现为"预览对、游戏暗"——自己和自己一致、和游戏不一致，最难发现 |
| **色库自检不能用"最接近的那帧"** | 挑最接近的帧去比等于没验。要固定规则选帧并如实报极差 |
| **帧集合要先剔异材质** | 石头片里有大量"石块被泥土包围"的过渡帧（R-B 到 35）；靠色度差剔，不要靠亮度结构 |
| **色相项必须有源侧彩度门** | 近中性像素（C*<8）的色相角无意义；不加门会把亮白 L*89.7 推到 L*67 的暗方块上，就是"白骨骼发灰" |
| **方块名与 TileID 未逐条核验** | 颜色取自 `Tiles_<id>`，实际放置的方块与该贴图一致；但少数不常见条目（珍珠木/暗影木/宝石）的英文名与 ID 对不上，只影响显示名 |
| **失焦暂停** | 窗口失焦会暂停 PostUpdateEverything，必须挂 On_Main.Update 兜底 |
| **GameUpdateCount 去重** | 暂停时它不递增，会挡掉后续所有帧 → 帧号冻结时退化为墙钟窗 |
| **光照传播** | 实心方块挡光，内部永远黑 → 交给 HEROsMod 全亮 |
| **Sand 是重力方块** | 塌方，已从色库排除；Glass/Ice 半透明也默认排除 |
| **RGBA→RGB** | 只吃 3 通道的函数不能喂 4 通道 |
| **中文 bat 编码** | GBK 中文行破坏 if 块解析 → 启动脚本纯 ASCII |
| **文档存储重定向** | 本机「文档」= D:\文档 |

---

## 八、性能（实测，20 万像素）

| 场景 | 耗时 |
|---|---|
| precise / LAB | 0.27s |
| precise / CIEDE2000（默认） | 1.3s |
| natural / CIEDE2000 | 5.0s |
| dither / CIEDE2000（与 mode 无关） | 4.3s |
| 放置速度（档位 4） | 约 1900 块/秒 |

---

## 九、迭代2 验收数据（骷髅测试图，1296×1280，1,658,880 不透明像素）

同一套指标，自洽可比：

| 指标 | 上一版（35 块 / 手写色库 / lab） | 本次（42 块 / 贴图色库 / ciede2000） |
|---|---|---|
| mean ΔE2000 | 16.18 | **12.62** |
| Δh（彩度≥10） | 28.03 | **18.88** |
| 蓝→紫 | 0.291% | **0.000%** |
| 红→被洗成哑棕 | 0.00%（旧色库靠"编出来的高饱和色"达标） | 6.81% |
| 灰白匹配色 L（源 86.49） | 86.45（假高饱和亮色撑出来的） | 79.36 |
| 方块数 | 35 | 42 |

**结论**：ΔE 与色相误差都明显改善，蓝背景偏紫彻底消除。

**迭代2 补充做的扩库**（42 块）：用游戏自报的 \`TileID.Search.GetName\` 对照表（693 条，存于
\`assets/tile_names.json\`）核对全部命名后，从 763 张贴图里数据驱动补了 7 个原版实心方块：
微光砖(667)/血肉块(195)/红灰泥(153)/红王朝瓦(312)/狱石砖(76)/星云块(417)/远古蓝砖(677)。
效果：ΔE 13.90→12.62，Δh 25.01→18.88，灰白 L 77.15→79.36。

**两个诚实的说明**：
1. **"红→棕 72%" 是旧指标口径的假象**。原口径把"暖色相且 L<65"都算棕，于是 FleshBlock(147,31,43)
   这种明确的血红也被判成棕。改成"被洗成哑棕"= 暖色相且 **C*<25** 之后是 **6.81%**。
2. **灰白仍比源图暗约 7 个 L***。原版确实没有"亮中性且实心"的方块：雪(180,214,232)、微光砖(191,212,214)
   都偏冷，珍珠沙(206,192,196) 中性但**是重力方块**（会塌方）。这是色域硬限制，不是匹配算法问题。
   旧基线那个 86.45 是靠 Snow(240,245,250)、珍珠木(220,235,245) 这些**游戏里并不存在的颜色**凑出来的，
   所以旧预览"看着正好"，游戏里却偏暗。

> 权威名称表已由一次性开发工具导出并落库到 `assets/tile_names.json`（693 条），工具本身已删除，
> 模组端恢复原状（重新构建过，0 警告 0 错误）。以后要核对方块名直接读这个文件即可。

---

## 十、待优化方向

1. **补齐色库色域**（见第九节）：亮中性 + 高饱和红，能把「白骨骼发灰 / 红血肉偏棕」收掉
2. 按分块断点续传（蓝图导入导出已有）
3. 暂停/续传（需持久化建造状态）
4. 放置速度：把 SquareTileFrame 延迟到分块末尾批量做，预计再快 30~50%
5. 少数方块名与 TileID 的对应关系逐条核验
6. 清理工作交给 HEROsMod

---

## 十一、新会话开场白模板

    继续 TerrariaImageBuilder 项目。先读这两个文件了解现状：
    1. D:\deepseek\TerrariaImageBuilder\PROGRESS.md（交接文档）
    2. D:\文档\My Games\Terraria\tModLoader\ModSources\terraria_builder\README.md（外端说明）

    当前状态：外端已具备 CIEDE2000 匹配 / 贴图色库 / 保色相 / 纯色优先 / 抖动 / 自定义色卡，
    模组端 v1.2.0 已加固，协议与蓝图格式未变。
    现在我想优化：<在这里写你的需求>
