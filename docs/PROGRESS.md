# TerrariaImageBuilder · 交接文档

> 新会话开始时先读这个文件，即可接上全部上下文。
> 最后更新：v1.2.2 已发布（GitHub Releases + 创意工坊审核中）。
> **下一个任务：第十三节「图片批量队列 + 定格动画排布」。**

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

---

## 十二、明天从这里接着做（2026-10-25 夜 收工快照）

### 已完成并上线

**创意工坊已发布**（2026-10-25 16:41，状态：等待 Steam 服务人员批准，属新模组首次发布正常流程）：
https://steamcommunity.com/sharedfiles/filedetails/?id=3826829451

**工坊描述更新未生效的排查（2026-10-25）**：编辑页已保存，但公开页面仍显示旧描述。
最可能是 **Steam 审核门**：横幅原文是「需要服务人员批准**其最新版本**」，即每次改动都会生成新版本并重新排队，
过审前对外仍是上一版。排查顺序：① 重开编辑页看描述框内容（是新文字=已保存）② 无痕窗口打开公开页（排除 CDN 缓存）
③ 若两者都正常则纯属待审，**不要再反复编辑**（每次改都可能重置队列）。
注意：本机无法访问 steamcommunity.com / api.steampowered.com（HTTP 000），只能靠用户截图判断。

发布时踩到的坑（这个 tML 版本 v2024.8.3.0 的发布流程**只认老式三件套**，不读 mod.hjson）：
- 缺 `build.txt` → 报「You need to specify an author in build.txt」
- 缺 `description.txt` → 报「文件 description.txt 不存在」
- `description.txt` **就是工坊页面显示的完整描述**（不是一句话简介），且 Steam 渲染 BBCode 不是 Markdown，粗体要用 `[b]...[/b]`
- 三个文件现已齐备：`build.txt` / `description.txt` / `icon.png`，`mod.hjson` 保留给构建流程

| 项 | 状态 |
|---|---|
| 仓库 | https://github.com/HikeTopZenith/terraria-image-builder （public，远端 HEAD 169f680） |
| Release | https://github.com/HikeTopZenith/terraria-image-builder/releases/tag/v1.2.2 |
| 附件 | TerrariaImageBuilder-v1.2.2.zip 47.5 MB · TerrariaImageBuilder.tmod 17.3 MB |
| 模组 | v1.2.2 已构建（0 警告 0 错误）并装进 Mods 目录 |
| 测试 | 6 个文件全绿（engine / matcher_v2 / colorimetry / place_command / pipeline_v2 / checksum_golden） |
| 工坊截图 | docs/screenshots/ 两张 1600×900，也在 D:\deepseek\output\workshop\ |

### 明天要做的三件事（按顺序）

**1. 工坊上传（只有你能做，需要 Steam 登录态）**

- 启动 tModLoader → Workshop → Develop Mods → TerrariaImageBuilder → Build & Reload → Publish
- 标题：泰拉图像建造台 Terraria Image Builder
- 描述：粘 WORKSHOP.md 第六节（含下载段）
- 标签：Utility / Tools / Client Side
- 截图：上传 docs/screenshots/ 那两张
- 上传完把链接发出来，回填到 README 与本文档

**2. CI 工作流还差一次授权**

- 文件已写好但推不上去：.github/workflows/tests.yml 目前只在本地
- 原因：改动 workflow 文件要求 token 带 workflow 权限，缺权限时 GitHub 故意返回 404（不是 payload 问题）
- 解法：执行 gh auth refresh -h github.com -s workflow，然后跑 python D:\deepseek\_pack\push_via_api.py
- 备选：在 GitHub 网页手动新建该文件（内容见本地副本）

**3. git 端点仍被网络拦，仓库推送走的是 API**

- github.com:443 超时、api.github.com 正常，所以改用 GitHub Git Data API 推送
- 后果：本地 HEAD（657e39f）与远端（169f680）内容相同但 SHA 不同
- 网络恢复后对齐一次：cd D:\deepseek\TerrariaImageBuilder-github && git fetch origin && git reset --hard origin/master
- 对齐后别忘了把第 2 条那个 workflow 文件补回去

### 明确暂缓（有理由，不是漏了）

- **TcpListener 兜底**：模组用 HttpListener（走 http.sys），理论缺陷是非管理员可能注册失败。
  但你机器日志显示「WebSocket 服务端已启动」正常，**没有真实故障案例就不动核心网络层**。
  正确姿势：等有用户报「日志里出现拒绝访问」，拿到日志再改 + 让他实测。
- **桌面端英文界面**：工坊面向全球，当前 UI 纯中文。工作量中等。
- **预览用真实贴图纹理**：现在平涂主色；assets/tiles 本地有，但仓库刻意不含（版权）。
- **选区建造**（框选局部重建，试配色用）。
- **启动时自动连接**（省一次点击）。

### 本轮踩过并已修的坑（别再踩）

1. **aiohttp 漏打包** —— 发行版 exe 里没有 WS 客户端，界面永远「连接中…」。打包环境必须装 aiohttp
2. **事件循环竞态** —— 刚启动就点连接，请求会被 _submit 丢弃。start() 已改为等循环就绪
3. **单文件打包不可用** —— --onefile 缺 Tcl/Tk 数据，会弹标题为 Error 的 Tk 对话框。必须用 --onedir
4. **PyInstaller 吃不了中文路径** —— 源码先复制到纯 ASCII 路径再打包
5. **续传的原点检查比错值** —— 比的是上一轮原点，等于没检查；已改为比输入框当前值
6. **workflow 文件 404 是权限问题**，不是 payload 问题（二分 4 次才定位）
7. **本机「文档」被重定向**，凡 %USERPROFILE%\Documents 都要按实际盘符替换

### 关键路径速查

    GitHub 仓库      D:\deepseek\TerrariaImageBuilder-github
    外端源码          D:\文档\My Games\Terraria\tModLoader\ModSources\terraria_builder
    模组源码          D:\deepseek\TerrariaImageBuilder
    打包环境          D:\deepseek\_pack\venv        （独立，未污染 ComfyUI）
    打包脚本          D:\deepseek\_pack\make_release.py
    API 推送脚本      D:\deepseek\_pack\push_via_api.py
    发行产物          D:\deepseek\output\release\TerrariaImageBuilder-v1.2.2.zip
    工坊截图          D:\deepseek\output\workshop\shot1_compare.png / shot2_dither.png

---

## 十三、待开发：图片批量队列 + 定格动画排布（v1.3.0 目标）

> 用户目标：做**定格动画**。把一帧帧像素画建到世界里，再用剪辑软件拼接成动画。
> 落点：**全部在外端 Python**，模组端零改动（只复用现有 `AllCompleted` / `ExecutionError` 回执）。

### 13.1 需求（用户原文要点）

1. 多选 / 拖拽导入多张图 → 生成任务队列；队列支持拖拽排序、单独删除、暂停、继续、跳过
2. **严格串行**：禁止并发下发，防止游戏卡死。一帧完整建成 + 收到完成回执 → 等间隔（2–15s，默认 5s）→ 自动下一帧。失败则暂停队列并弹窗（重试 / 跳过 / 清空）
3. 排布模式二选一（UI 切换）：
   - **模式 A｜平铺连环画（默认）**：以第一帧原点为基准，后续帧紧贴接续；默认向右、底边对齐、无重叠无空隙；可切向下，可设帧间空隙
   - **模式 B｜原地覆盖帧动画**：所有帧固定同一原点，靠新方块覆盖旧方块。**不调用任何清除功能、不依赖 HEROsMod 清除、不新增清除代码**。⚠️ 天然限制：帧内空气区域不会擦除旧方块，留白处会残留上一帧 —— 仅适合每帧铺满方块的素材
4. 复用全部现有管线：裁剪、旋转镜像、亮度对比度、自定义色卡、色彩匹配、WebSocket、撤销台账 —— **一律不动**
5. 原有单图导入不受影响，单图仍是"鼠标点哪放哪"；两套逻辑隔离
6. 兼容 HEROsMod / Better Zoom；**不要恢复已删除的光照、清除等模块**

### 13.2 关键技术结论（先看这条，能省一半工）

**平铺模式不需要修改蓝图的方块坐标，只改指令里的 `OriginX/OriginY`。**

原因：协议里 `PlaceBlocks` 的 `X/Y` 是「相对原点的全局坐标」，外端发指令时才把 origin 一起带上。所以同一份蓝图换个 origin 发出去，整幅画就整体平移到别处 —— 因此每帧各建一次蓝图后，只需计算各自的 origin。

推导（设基准原点 `(bx, by)`，第 i 帧尺寸 `w_i × h_i`，总体最大高度 `H = max(h_i)`，帧间距 `gap`）：

```
模式 A · 向右：origin_x = bx + Σ(w_0..w_{i-1}) + i*gap
               origin_y = by + (H - h_i)      # 底边对齐
模式 A · 向下：origin_y = by + Σ(h_0..h_{i-1}) + i*gap
               origin_x = bx + (W - w_i)      # 右边对齐（W = max(w_i)）
模式 B ·      ：所有帧 origin 恒等于 (bx, by)，不做任何偏移
```

### 13.3 落点与改动清单（按 ARCHITECTURE 第七节扩展指南）

| 文件 | 改动 | 说明 |
|---|---|---|
| `ui/queue.py`（新建） | 队列控制器 `FrameQueue` + 状态机 | **纯 Python，不 import tkinter**，才能 headless 测试 |
| `ui/app.py` | 队列面板（列表 + 排序 + 增删 + 暂停/继续/跳过）、模式开关、间隔滑块、批量导入入口 | 见 13.4 的隔离要求 |
| `tests/test_frame_queue.py`（新建） | 偏移计算 / 状态机 / 串行性 / 跳过后续位置 | 全 headless |
| `engine/contract.py` | **不改**（origin 由 `blueprint_to_chunked_command(origin_x=..., origin_y=...)` 传入，已支持） |
| 模组端 | **不改** | 只依赖现有回执 |

### 13.4 必须遵守的隔离与约束

- **单图流程保持原样**：`on_start_build()` / `_send_next_batch()` 的语义不动。队列走独立入口 `_queue_start_frame()`，不要改造成"队列长度=1 的特殊情况"
- **拖拽行为要分流**（现状：`ui/app.py:874 _on_drop` 解析多路径后**只取第一个就 return**）：
  - 拖到**原图画布** → 维持现状：只导入第一张（单图流程）
  - 拖到**队列面板** → 全部入队（新行为）
  - 多选文件同理：`on_open_file` 用单选保持，另加「批量添加」按钮用 `askopenfilenames`
- **串行判定不能只看一批**：单帧可能被 `BATCH_MAX_BLOCKS = 150000`（`app.py:175`）切成多批，必须等该帧**所有批次**都收到 `AllCompleted` 才算这一帧完成
- **世界边界校验**：模式 A 拼接后总宽度可能超出世界或未加载区域。发送前按 `Main.maxTilesX/Y` 预估并**明确警告**（UI 侧算，模组端已有七道过滤会拦，但要让用户提前知道而不是建一半报错）
- **与断点续传冲突（重要）**：现有 `resume.json` 按「蓝图指纹 + origin」记账（`app.py` 的 `_resume_path` / `_save_resume`）。多帧场景下每帧 origin 不同、蓝图不同，会互相串味 → **队列模式下要么按任务 id 分别记账，要么直接禁用单图续传**，二选一写进实现说明
- **模式 B 必须在 UI 上明示限制**：空气不擦除、留白会残留上一帧。不写这句，用户建完发现"糊了"会当成 bug

### 13.5 状态机（建议）

```
idle ──开始──► building(帧i) ──AllCompleted──► waiting(间隔倒计时) ──► building(帧i+1)
                     │                                              │
                     ├─ ExecutionError ──► paused_failed ──重试──┘
                     │                         └──跳过──► building(帧i+1)
                     │                         └──清空──► idle
                     └─ 用户暂停 ──► paused ──继续──► building(帧i)
全部帧完成 ──► done（清队列记录，若用了续传则清 resume.json）
}
```

暂停/跳过的语义要写死：

- **暂停**：不再发新指令；**已下发的当前帧不中断**（模组端 P 键才是真中断，队列暂停只管"下一帧"）
- **跳过**：跳过当前帧剩余的批次（不再发），直接进入间隔 → 下一帧。注意已建的部分**不回滚**
- **跳过后的位置必须正确**：模式 A 的偏移是按"帧序号"还是"实际执行顺序"计算？**必须按帧的固定排布位置算**（即第 3 帧被跳过后，第 4 帧仍落在第 4 个位置），否则接续位置会错位 —— 这是验收第 1 条的核心

### 13.6 需要新增的测试（headless，进 `run_tests.bat`）

| 测试 | 断言 |
|---|---|
| `test_offset_tile_right` | 向右平铺：第 i 帧 origin_x 递增量 = 前一帧宽 + gap；origin_y 底边对齐（不同高度的帧底边一致） |
| `test_offset_tile_down` | 向下平铺对称正确 |
| `test_offset_overlay_same_origin` | 模式 B：所有帧 origin 完全相同 |
| `test_skip_keeps_position` | 跳过第 2 帧后，第 3 帧仍落在第 3 个槽位（不塌缩） |
| `test_no_concurrent_send` | 一帧未收到 AllCompleted 前，队列**绝不**发出下一帧指令 |
| `test_multi_batch_frame_completes_once` | 单帧被切成 3 批时，只有第 3 批的 AllCompleted 才推进队列 |
| `test_failure_pauses_queue` | 收到 ExecutionError → 状态变 paused_failed，且不再发指令 |
| `test_single_image_flow_untouched` | 单图流程的 `_send_next_batch` 行为与改动前一致（回归） |

### 13.7 验收（对应用户 7 条，写成可核对的）

- [ ] 平铺模式：帧连成一片、紧贴无重叠；**跳过/暂停后接续位置正确**（不塌缩、不错位）
- [ ] 原地覆盖模式：固定原点逐帧覆盖，**全程无任何清除调用**（代码里搜不到清除/`ClearTile` 相关新增），间隔可控
- [ ] 严格串行：抓包或日志可见「上一帧全部批次完成」之后才出现下一帧指令，**无并发发包**
- [ ] 失败路径：制造一次 ExecutionError（例如传非法 TileId）→ 队列暂停 + 弹窗三选项可用
- [ ] 单图、色彩、预处理、色卡、工程包、断点续传全部回归正常（`run_tests.bat` 6 个文件 + 手动冒烟）
- [ ] 模组端 `.tmod` 未重新构建也能正常跑（本轮零改动）

### 13.8 风险与已知取舍

| 风险 | 说明 | 处置 |
|---|---|---|
| 平铺 N 帧绘制时间线性增长 | 每帧各自 ≤20 万块上限，但 20 帧 × 2 万块 = 40 万块，按 1900 块/秒约 3.5 分钟 | UI 显示"总帧数 + 预计总耗时" |
| 模式 B 的残留问题 | 空气区域不擦除，是原生机制决定的 | UI 明示 + 建议用户素材每帧铺满 |
| 世界越界 | 横向拼接最容易撞边界 | 发送前预估并警告 |
| 间隔过短导致模组队列未清空 | 理论风险（模组收到新指令时上一条已 AllCompleted） | 间隔下限 2s，且严格等回执后才计时 |
| 队列与续传台账互相干扰 | 每帧 origin 不同 | 见 13.4 最后一条 |

### 13.9 版本与交付

- 外端版本号升 **1.3.0**（`ui/app.py` 的 `APP_VERSION`），协议版本**不变**（`CLIENT_PROTOCOL_VERSION = 1`）
- 模组端**不重建**，`.tmod` 保持 v1.2.2
- 完成后需要：更新 `MANUAL.md`（新增"批量队列"章节）、`ARCHITECTURE.md`（新增 `ui/queue.py` 模块）、`description.txt`（工坊描述加功能点）、`VIDEO_SCRIPT.md`（定格动画是个很好的视频题材）
- 打包发布：`make_release.py` → 上传 GitHub Releases + 网盘镜像 → 工坊重新发布

### 13.10 增强建议（在原始需求之上，按性价比排序）

> 这一节是在用户的七条需求之外**主动补的**，目的：把"能跑通"提升到"真的好用"。
> 标 ⭐ 的是我强烈建议纳入本轮的。

#### P0 · 低成本、直接消灭事故

**⭐ 14.1 自然排序 + 去重 + 尺寸一致性校验**

批量导入最容易出错的三件事，全在导入这一步解决：

- **自然排序**：`frame_1.png, frame_2.png, …, frame_10.png` 不能按字典序排成 1,10,2
  → 用 `re.split(r"(\d+)", name)` 取数字段做键，纯数字文件名按数值比较
- **去重**：按文件 SHA-1 前缀去重（同一帧导入两次 → 动画会卡顿一拍）。发现重复时提示"已跳过 N 个重复文件"而不是静默丢弃
- **尺寸一致性**：定格动画要求每帧同尺寸。若不一致：
  - 模式 A 平铺会错位（宽度不同 → 接缝对不上）
  - 模式 B 覆盖会偏（高度不同 → 底边对不齐）
  → 导入时检测，弹窗三选一：【统一缩放到第一帧尺寸】/【统一裁剪到最小公共尺寸】/【保持原样（我不在乎）】

**⭐ 14.2 队列项显示足够信息**

每行：`#序号  文件名  128×128  16384 块  ~9 秒`，右侧一个小缩略图（32×32 最近邻放大）。
拖拽排序时**只看文件名是排不出动画顺序的**，缩略图是刚需。

**⭐ 14.3 拼接总览预览（建之前先看排布）**

点一个「预览整体排布」按钮：把 N 帧蓝图按模式 A 的偏移**拼成一张大 PNG**（用现有的 `render_blueprint` 逐帧渲染后贴到大画布）。

价值：一眼确认有没有重叠、接缝对不对、总宽度是否超出世界。**比建完 20 帧才发现错位省几十分钟。**

#### P1 · 解决原方案硬伤 / 大幅省事

**⭐⭐ 14.4 模式 B 的「背景填充两步法」—— 不写一行清除代码就解决残留**

这是我最想推荐的一条。用户原方案里模式 B 有个硬伤："帧内空气区域不会擦除，留白会残留上一帧方块"。

**解法（完全符合"不调用任何清除功能"的约束）**：每帧发送**两条**指令，都只是普通放置：

```
第 1 条：背景层 —— 用用户指定的「填充方块」把整幅画布区域铺满
第 2 条：内容层 —— 正常的帧内容（同一 origin）
```

因为方块放置本身就是覆盖语义，第 1 条已经把上一帧全部盖掉，第 2 条再画这一帧的内容 —— **等于擦除，但没有任何清除 API 调用**。

细节：

- 填充方块默认取"第一帧里出现次数最多的方块"（用户可改），这样背景自然融入画面
- 若用户素材本来就每帧铺满，可以关掉这个"两步法"（UI 开关，默认开）
- 多出的一层方块数 = 画布面积，2 万块的图就是 2 万块，**耗时增加约 10~15 秒/帧**，UI 要显示这个代价

**⭐⭐ 14.5 自动逐帧截图（把"手动截图"这一步也省掉）**

定格动画最烦的环节不是建造，是**每帧建完要手动截图**，而且手抖就导致成片画面抖动。

**做法**：每帧收到 `AllCompleted` → 倒计时 N 秒（默认 3 秒，UI 可调）→ 自动截取游戏窗口 → 存 `frames/frame_0001.png`。

技术路径（都已验证可行）：

- 找窗口：`ctypes.windll.user32.FindWindowW(None, "Terraria")`（tModLoader 窗口标题通常是 `Terraria`；找不到再遍历枚举模糊匹配）
- 取窗口矩形：`GetWindowRect` → 用 `PIL.ImageGrab.grab(bbox=...)` 截指定区域（本项目已经用过 ImageGrab）
- 只截客户区更好：`GetClientRect` + `ClientToScreen`
- 截图前倒计时期间，UI 大字提示「**请不要移动视角**」；可选蜂鸣提示

产物：一个 `manifest.json` 记录帧序 / origin / 尺寸 / 方块数 / 截图时间，方便剪辑时对齐。

⚠️ 已知限制：截图含游戏内 UI（血条、小地图）→ 建议用户在录制时按一下"隐藏 UI"（游戏设置里有）或后期裁切；UI 可以在说明里提示。

**⭐ 14.6 材料采购清单**

N 帧动画可能要几十万方块。建到一半发现材料不够是真实灾难。

队列建好后点「统计材料」：汇总所有帧的 `block_id` 计数，输出成表格（可导出 txt / 复制到剪贴板）：

```
Stone        182,340
Dirt          41,200
Grass         12,880
…
```

实现零成本：`render_blueprint` 里已有按 block_id 计数的逻辑（`blueprint_statistics`），逐帧调用后累加即可。

#### P2 · 进阶（看时间决定，可放 v1.3.1）

**14.7 模式 C：网格拼版（sprite sheet）**

世界横向空间不够时（平铺 30 帧 × 150 宽 = 4500 格，很多世界装不下），按"每行 K 帧"自动换行：

```
origin_x = bx + (i % K) * (w + gap)
origin_y = by + (i // K) * (h + gap)
```

剪辑时按网格裁切即可。议价：对做定格动画的人来说，这比"纵向平铺"更实用（因为世界宽度通常比高度富余，但深度有限）。

**14.8 队列级断点续传（与单图 resume.json 分开）**

`queue_state.json` 记录：任务列表、已完成帧号、基准原点、排布模式、填充方块设置、截图目录。
游戏崩溃 / 关服 / 主动退出后，重开直接"继续队列"从断点帧接着建 —— 而不是从头再来。

**14.9 基准原点锁定 + 试跑一帧**

- 队列一旦开始，**锁定 origin**：用户改原点输入框时提示"队列运行中，原点已锁定"，防止误改导致后续帧全错位
- 「先试建第一帧」按钮：只建第 1 帧，用户看位置对不对，再点"继续全部"。**避免建完 20 帧才发现位置偏了**

**14.10 失败原因分类处置**

`ExecutionError` 的 `ErrorMessage` 里已经带了原因（本机是这样），可以分类给建议而不是干巴巴报错：

| 报错含 | 处置建议 |
|---|---|
| 越界 / 边界 | 建议调整基准原点，或改用模式 C 网格拼版 |
| 非法方块ID | 提示检查自定义色卡与黑白名单（正常不该出现） |
| 超过总方块数上限 | 提示调 `config.json` 的 `MaxTotalBlocks` 或分批 |
| 图块矩阵未就绪 | 提示等待世界加载完成再继续 |

**14.11 自动合成动画（可选，注意依赖）**

有了截图序列就可以直接合成：

- **GIF / 动态 WebP**：Pillow 直接支持（**已验证本机 Pillow 12.3 支持 webp 写入**），零额外依赖，立刻可用
- **MP4**：本机目前**没有 ffmpeg / imageio / cv2**（已核实），需要 `pip install imageio-ffmpeg`（约 30MB）。装了才做，**不要把它做成硬依赖**，检测不到就只出 GIF

**14.12 间隔期间的倒计时与"立即继续"**

间隔 5 秒的等待期，UI 显示「下一帧 3.2s 后」并给一个「立即继续」按钮 —— 用户可能想加速。

#### 明确不做（防止新会话过度设计）

- ❌ 不做并发（用户明确要求严格串行）
- ❌ 不做游戏内 UI（配置统一走 json，外端控制）
- ❌ 不新增任何清除 / 光照相关代码，不恢复已删模块
- ❌ 不改模组端 `.tmod`（本轮零改动，只复用回执）
- ❌ 不引入 ffmpeg 作为硬依赖

#### 建议的落地顺序

| 批次 | 内容 | 理由 |
|---|---|---|
| 第一批 | 13.1~13.9 原始需求 + 14.1 + 14.2 + 14.3 | 骨架 + 三个防事故项，缺了会返工 |
| 第二批 | 14.4（背景填充）+ 14.5（自动截图） | 这两条把"能用"变成"好用"，是这个功能的灵魂 |
| 第三批 | 14.6 + 14.8 + 14.9 + 14.12 | 稳定性与体验 |
| 第四批 | 14.7 + 14.10 + 14.11 | 空间受限时的解与锦上添花 |

### 13.11 界面草图（文字版布局，供实现时对齐）

> 设计原则：**不新增独立窗口、不破坏现有三栏布局**。队列作为右侧面板的第 4 个标签页，
> 左侧两块画布在队列页下复用为"当前选中帧的原图 / 方块化预览"。

#### 13.11.1 整体位置

```
┌──────────────────────────────────────────────────────────────────────────┐
│ [选择图片] [批量添加…] [从剪贴板]   匹配模式[纯色精准▾]      ●未连接 [连接游戏] │  ← 顶栏（+1 个「批量添加」）
├──────────────────────────┬──────────────────────────┬────────────────────┤
│ 原图（拖拽裁剪/取色）      │ 方块预览（离线）           │ 建造 │预处理│方块│★队列│
│                          │                          │                    │
│  队列页下 = 当前选中帧     │  队列页下 = 该帧方块化结果   │   ← 队列面板（320 宽）│
│                          │                          │                    │
└──────────────────────────┴──────────────────────────┴────────────────────┘
```

★ = 新增第 4 个标签页。之前的「匹配设置」仍留在「方块」页顶部单行（那次已经定型，不要挪）。

#### 13.11.2 「队列」标签页内部（自上而下）

```
┌─ 排布模式 ─────────────────────────────┐
│ (•) 平铺连环画   ( ) 原地覆盖帧动画     │
│   方向 [向右▾]  帧间空隙 [0] 格          │  ← 模式A 才可用
│   [ ] 每 8 帧换行（网格拼版）            │  ← 模式C（进阶，可先隐藏）
└────────────────────────────────────────┘
┌─ 执行 ─────────────────────────────────┐
│ 帧间隔 [5] 秒 (2-15)     [x] 每帧自动截图 │
│ [x] 模式B 先铺背景层   填充方块 [自动▾]   │
└────────────────────────────────────────┘
┌─ 队列 (12 帧 / 共 19.6 万块 / ~1.9 分钟) ┐
│ ☰ [#1 ▣] frame_001.png   128²  16384  ~9s│  ← 拖拽排序；☰ 手柄；▣ 缩略图
│ ☰ [#2 ▣] frame_002.png   128²  16384  ~9s│
│ ☰ [#3 ▣] frame_003.png   128²  16384  ~9s│ ●建造中
│ ☰ [#4 ▣] frame_004.png   128²  16384  ~9s│ ✓已完成
│ ☰ [#5 ▣] frame_005.png   128²  16384  ~9s│ ⊘已跳过
│ ☰ [#6 ▣] frame_006.png   128²  16384  ~9s│ ✗失败(越界)
│ …（可滚动）                              │
└────────────────────────────────────────┘
┌─ 操作 ─────────────────────────────────┐
│ [试建第一帧] [开始队列] [暂停] [跳过]     │
│ [删除选中] [清空队列] [材料清单…]         │
│ [预览整体排布…]                          │
└────────────────────────────────────────┘
┌─ 进度 ─────────────────────────────────┐
│ 第 3/12 帧   16384/196608 块             │
│ ▓▓▓▓▓▓░░░░░░░░░░░░░░░  下一帧 3.2s 后    │
└────────────────────────────────────────┘
```

#### 13.11.3 尺寸预算（必须实测，不能用"差不多"）

右侧面板固定 **320 px**。@760 高时：

- 排布模式区 ≈ 90 px（3 行）
- 执行区 ≈ 60 px（2 行）
- 队列列表 = **剩余空间 ≈ 380~400 px** → 以每行 26 px 算，**约 14~15 行可见**，其余滚动
- 操作区 ≈ 90 px（3 行按钮）
- 进度区 ≈ 60 px（2 行）

⚠️ 验收时要实测 760 / 700 / 660 三个高度下：**队列列表 ≥ 8 行可见**、控件不被裁切。
（这是上次 UI 改动定的标准，沿用。）

#### 13.11.4 队列项的视觉状态

| 状态 | 后缀标记 | 颜色 |
|---|---|---|
| 待建 | （无） | 默认 |
| 建造中 | `●建造中` | 蓝色 |
| 已完成 | `✓已完成` | 绿色 |
| 已跳过 | `⊘已跳过` | 灰色 |
| 失败 | `✗失败(原因)` | 红色 |

实现建议：用 `tk.Listbox` 无法按行着色 → **改用 `tk.Canvas` 自绘行**（项目里「自定义色卡面板」已经是自绘行，可复用那套写法），或 `ttk.Treeview` 搭配 tag 着色。**推荐 Treeview**：原生支持多列（序号/文件名/尺寸/块数/耗时/状态）+ tag 着色 + 选中，最省事。

#### 13.11.5 交互流程（按这个顺序验收）

```
1. 点「批量添加…」或把多张图拖到队列面板
   → 自然排序、去重、尺寸校验（不一致时弹三选一）
   → 列表显示 序号/文件名/尺寸/块数/预估耗时
2. （可选）拖拽调整顺序；选中某行 → 左侧两块画布显示该帧
3. 选排布模式；模式A 设方向与空隙；设帧间隔；勾是否自动截图
4. 点「预览整体排布…」→ 弹窗看拼接总览（含总宽/总高/是否越界警告）
5. 点「试建第一帧」→ 只建第 1 帧 → 看位置对不对
6. 点「开始队列」→ 锁定原点 → 串行建造
   → 每帧：发全部批次 → 等 AllCompleted →（可选）截图 → 倒计时 → 下一帧
7. 中途可「暂停」（不中断当前帧）/「跳过」（当前帧剩余批次不发）
8. 失败 → 暂停 + 弹窗【重试 / 跳过 / 清空队列】
9. 全部完成 → 状态栏汇总 + 若开了截图则生成 manifest.json
```

#### 13.11.6 新增控件与变量名建议

| 控件 | 类型 | 变量名 | 备注 |
|---|---|---|---|
| 批量添加… | Button | — | `filedialog.askopenfilenames` |
| 排布模式 | Radiobutton ×2 | `self.queue_mode` (`"tile"` / `"overlay"`) | 默认 tile |
| 平铺方向 | Combobox | `self.queue_direction` (`"right"` / `"down"`) | 仅 tile 可用 |
| 帧间空隙 | Spinbox | `self.queue_gap` (int, 0~50) | 仅 tile 可用 |
| 每 N 帧换行 | Checkbutton + Spinbox | `self.queue_wrap`, `self.queue_cols` | 进阶，可先禁用 |
| 帧间隔 | Spinbox | `self.queue_delay` (2~15, 默认 5) | |
| 每帧自动截图 | Checkbutton | `self.queue_screenshot` | 默认关（首次用建议手动确认一次） |
| 先铺背景层 | Checkbutton | `self.queue_backfill` | 模式B 默认开 |
| 填充方块 | Combobox | `self.queue_fill_block` | 默认「自动 = 第一帧最多方块」 |
| 队列列表 | Treeview | `self.queue_tree` | 列：序号/文件名/尺寸/块数/耗时/状态 |
| 开始队列 / 暂停 / 跳过 | Button ×3 | — | 按状态启用禁用 |
| 材料清单… | Button | — | 弹窗列出各 block_id 与数量 |
| 预览整体排布… | Button | — | Toplevel 显示拼图 + 总宽高 |

#### 13.11.7 状态栏文案（照这个写，别自由发挥）

```
待建：     队列 12 帧，共 196,608 块，预计约 1.9 分钟
建造中：   第 3/12 帧：已发 2/2 批，等待完成回执…
等待中：   第 3 帧完成，5.0 秒后开始第 4 帧（可点「立即继续」）
截图：     正在截图 frame_0003.png（请不要移动视角）
暂停：     队列已暂停（当前帧不中断）；点「继续」从第 4 帧开始
结束：     队列完成：12 帧全部建成，用时 1 分 52 秒（截图 12 张）
失败：     第 3 帧失败：坐标越界 —— 建议调整基准原点或改用网格拼版
```
