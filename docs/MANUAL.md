# Terraria Image Builder · 说明书与排障手册

> 给使用者，也给帮你排查问题的 AI。凡是要"复现/定位"，本文的行为都是可验证的。

---

## 一、这套东西由两部分组成

```
图片  →  [外端建造台 .exe]  →  方块蓝图 JSON  →  WebSocket →  [tModLoader 模组]  →  游戏世界里出现方块
         Windows 桌面程序                                                     游戏内 .tmod
```

| 部分 | 形态 | 职责 |
|---|---|---|
| 外端建造台 | Windows 免安装程序（folder + .exe） | 读图、匹配方块、分块、发指令 |
| 模组 | `.tmod`，装进 tModLoader | 收指令、分帧放方块、限速、撤销、安全校验 |

**两者通过本机回环地址 `ws://127.0.0.1:8765` 通信**，数据不出本机。
创意工坊只能分发 `.tmod`，所以外端程序必须另外拿（本项目的 Releases 或群文件）。

---

## 二、安装

### 1. 模组装进游戏

把 `TerrariaImageBuilder.tmod` 复制到：

    <文档>\My Games\Terraria\tModLoader\Mods\

然后启动 tModLoader → 模组管理 → 勾选 **TerrariaImageBuilder** → 重载模组。
需要 tModLoader **1.4.4+**（本模组 `MinimumApiVersion: 2026.4`）。

> 若游戏提示"模组制作需要 .NET 8.0 SDK"，是指**编译模组**需要 SDK。
> 只玩的话可以忽略；想自己编译见文末第 8 节。

### 2. 跑外端建造台

进 `外端建造台\`，双击 `TerrariaImageBuilder.exe`（首次启动慢几秒）。
不需要装 Python，`_internal\` 里自带运行时，**整个文件夹一起拷，别只拷 exe**。

---

## 三、正常使用流程

1. 进游戏、进世界（**不能停在主菜单**），把角色走到目标位置附近
2. 外端点 **连接游戏** → 右侧状态变成「已连接」
3. **选择图片**（PNG/JPG）
4. 「预处理」页可裁剪（左图拖拽框选）、旋转、镜像、亮度对比度；「方块」页可限制可用方块、
   开【优先保色相】/【纯色方块优先】/【抖动】、配自定义色卡
5. **生成预览** → 看效果满意 → **开始建造**
6. 游戏里：`O` 标记原点 · `P` 开始/暂停 · `U` 撤销全部（热键可在 config.json 改）

---

## 四、排障：一直显示「连接中…」连不上

### 4.1 先做三件事（30 秒）

1. **看外端状态栏**：现在（v1.2.0 修复版）连接失败会在 9 秒内明确报错，并自动判断端口状态：
   - 「没有任何程序在监听」→ 问题在**游戏端**没起服务
   - 「有程序在监听，但没完成 WebSocket 握手」→ 端口被别的软件占了，或游戏端异常
2. **看模组日志**：

       <文档>\My Games\Terraria\ModLoader\Logs\TerrariaImageBuilder\TerrariaImageBuilder_YYYY-MM-DD.log

   正常应有这两行：

       WebSocket 服务端已启动，监听 127.0.0.1:8765
       启动：通信模式 = WebSocket 服务端，监听 127.0.0.1:8765（仅本机）

   - **没有这两行** → 模组没加载 / 没启用 → 回第二节第 1 步
   - 有「WebSocket 初始化失败：拒绝访问」→ 见 4.2 表格第 5 条
3. **测端口**（PowerShell 或 CMD）：

       netstat -ano | findstr :8765

   有 `LISTENING` 才说明游戏端在监听；记下 PID 可反查是谁：

       tasklist /FI "PID eq <上一步的PID>"

### 4.2 常见原因对照表

| # | 现象 | 判别方法 | 处置 |
|---|---|---|---|
| 1 | 状态栏报「缺少 aiohttp 库」 | 外端版本过旧 | **换用 v1.2.0 修复版**（旧包漏打包 aiohttp，客户端功能是死的 —— 这就是最初"一直连接中"的真凶） |
| 2 | 点了没反应，几秒后才报错 | 程序刚启动就点连接 | v1.2.0 已修（事件循环未就绪时连接请求会被丢弃）；换新版即可 |
| 3 | 端口无监听，模组日志也没有启动行 | 模组未启用 | 模组管理里勾选 TerrariaImageBuilder → 重载 |
| 4 | 端口无监听，但模组日志有启动行 | 两个可能：改了端口 / 日志是上一次运行的 | 核对 `config.json` 的 `WebSocketPort`（默认 8765）；确认游戏真的进了世界 |
| 5 | 模组日志「WebSocket 初始化失败：拒绝访问」 | `HttpListener` 走 Windows `http.sys`，注册 URL 前缀需要权限 | **用管理员身份启动一次 tModLoader**；或 `netsh http add urlacl url=http://127.0.0.1:8765/ user=Everyone`（管理员执行一次即可） |
| 6 | 端口 LISTENING 但外端说握手失败 | 8765 被别的程序占用 | `netstat -ano \| findstr :8765` 找 PID → 关掉那个程序，或把模组 `WebSocketPort` 与外端端口一起改 |
| 7 | 防火墙弹窗被点了"阻止" | Windows Defender 防火墙 → 允许应用通过防火墙 | 找到 `TerrariaImageBuilder.exe` 勾上专用/公用；回环通常不需要，但被拦过要放行 |
| 8 | 游戏刚重载模组，外端仍显示旧的"已连接" | 长连接已断但 UI 未刷新 | 外端点一次「断开连接」再「连接游戏」 |
| 9 | 外端弹「版本不匹配」 | 模组与桌面端是分开发布的（模组走工坊、桌面端走 Releases），两边协议版本号不一致 | 按弹窗里的链接把**桌面端**更新到与模组同版本；连接成功后标题栏会显示 `已连接 · 模组 vX.Y.Z`，可据此核对 |

### 4.3 让朋友发给你的四条信息（定位最快）

1. 外端状态栏的**完整文字**（或弹窗截图）
2. 模组日志**最后 15 行**
3. `netstat -ano | findstr :8765` 的输出
4. `<文档>\My Games\Terraria\ModLoader\TerrariaImageBuilder\config.json` 的内容

---

## 五、外端自带自检（判断"这个包是不是好的"）

    在 exe 同目录打开 CMD：
    set TB_SELFTEST=1
    TerrariaImageBuilder.exe
    type selftest.log

正常输出（PASS）：

    aiohttp 3.14.5 OK
    engine OK, blocks=42
    websocket CONNECT OK (127.0.0.1:8765)
    RESULT=PASS

- 第 1 行 FAIL → 包坏了，aiohttp 没打进去
- 第 3 行 FAIL 且此时游戏没开 → 正常（没服务端可连），只说明客户端会尝试并超时
- 第 3 行 FAIL 但游戏开着且日志正常 → 见 4.2 第 5/6/7 条

---

## 六、协议速查（给排查用的 AI）

外端 → 模组：

    {
      "Type": "PlaceBlocks",
      "OriginX": 4200, "OriginY": 430,
      "ChunkCount": 6, "TotalBlocks": 199809,
      "Chunks": [{"Index":0,"DelayMs":200,"Blocks":[{"X":0,"Y":0,"TileId":0}]}],
      "DelayBetweenChunksMs": 200, "DelayWithinChunkMs": 0,
      "MaxTotalBlocks": 0, "SkipExistingBlocks": false,
      "Timestamp": 1792718267209, "Checksum": "..."
    }

- 坐标是**相对原点的全局坐标**（外端已把分块偏移加进去）
- `MaxTotalBlocks = 0` 表示不限；单任务硬上限 **200,000**
- `Checksum`：去掉该字段 → 递归按键名排序 → 紧凑 JSON → UTF-8 → SHA-256 → 小写十六进制
- 缺失 `Checksum` 时跳过校验（兼容旧版）

模组 → 外端：`InstructionReceived` / `ChunkProgress` / `AllCompleted` / `ExecutionError` / `PlayerPosition`
查询玩家位置：外端发 `{"Type":"GetPlayerPos","Timestamp":...}`

---

## 七、文件位置

| 内容 | 路径 |
|---|---|
| 模组 | `<文档>\My Games\Terraria\tModLoader\Mods\TerrariaImageBuilder.tmod` |
| 模组配置 | `<文档>\My Games\Terraria\ModLoader\TerrariaImageBuilder\config.json` |
| 模组日志 | `<文档>\My Games\Terraria\ModLoader\Logs\TerrariaImageBuilder\TerrariaImageBuilder_日期.log` |
| 外端程序 | `外端建造台\TerrariaImageBuilder.exe`（整个文件夹） |
| 外端启动错误 | 同目录 `startup_error.log`（仅启动失败时生成） |
| 外端自检结果 | 同目录 `selftest.log`（仅自检模式生成） |

> 本机「文档」可能被重定向（例如到 `D:\文档`），凡是 `<文档>` 都要按你的实际盘符替换。

---

## 八、给想自己编译的人

编译**模组**：把 `mod/` 放进 `ModSources/`，然后

    export PATH="<你的 .NET 8 SDK 目录>:$PATH"
    dotnet build TerrariaImageBuilder.csproj -c Release

不把 SDK 放进 PATH 会在**打包阶段**报 `MSB3073 … 缺 .NET 8`，看起来像代码错误，其实是 MSBuild
调到了只有 6.0 的宿主。

编译**外端**成 exe（PowerShell/CMD，需要 Python 3.10+）：

    pip install pyinstaller numpy pillow aiohttp
    pyinstaller --onedir --noconsole --name TerrariaImageBuilder \
        --add-data "<源码>/engine;engine" --add-data "<源码>/ui;ui" --add-data "<源码>/vendor;vendor" \
        run_app.py

⚠️ 三个必须记得的点（每条都真实踩过）：
1. **`aiohttp` 必须装进打包环境**，否则 exe 里没有 WebSocket 客户端，界面永远"连接中…"
2. 用 **`--onedir`**，别用 `--onefile`：单文件包里 Tcl/Tk 数据会缺，启动就弹一个标题为 `Error` 的 Tk 对话框
3. 源码路径**不要含中文**：PyInstaller 会把 `/d/文档/...` 解析成 `\d\文档\...` 然后报找不到文件

---

## 九、已知限制

- **白/灰比原图略暗**（约 7 个 L\*）：原版没有"亮中性且实心"的方块（雪与微光砖偏冷，珍珠沙是重力方块）
- **饱和红会偏砖红**：原版最红的可用方块是红砖/血肉块量级，达不到屏幕上的高饱和
- 少数不常见方块的中文名是人工翻译，英文常量名已用游戏自报的 `TileID.Search.GetName` 核对（693 条）

---

## 十、断点续传与诊断包（v1.2.2）

### 断点续传

大图建到一半断线/关游戏，不用从头再来：

1. 重新连接游戏，外端点「开始建造」
2. 若检测到**同一张图 + 同一个原点**有未完成的分块，会弹窗问
   「上次建到 3/4 分块，要继续剩下的 1 个吗？」
3. 选「是」只补没完成的分块；选「否」从头重建

规则与边界：

- 续传记录存在 `%LOCALAPPDATA%\TerrariaImageBuilder\resume.json`（可用环境变量 `TIB_STATE_DIR` 改位置）
- **换图或改参数**（匹配模式/黑白名单/抖动等）后指纹变化，旧记录自动失效，不会误续
- **原点变了不续传**（否则会建到错误位置）
- 全部批次完成后记录自动清空
- 模组端零改动，协议不变；已建好的方块不会被重复放置

### 诊断包

外端「建造」页有 **「导出诊断包（出问题时点这个）」** 按钮，一键生成 zip，内含：

| 文件 | 内容 |
|---|---|
| `diagnostics.txt` | 版本、系统、依赖版本（含 aiohttp 是否缺失）、端口是否在监听、连接状态、当前匹配设置、蓝图规模、状态栏最后一条 |
| `mod_config.json` | 模组的 config.json |
| `mod_log_tail.log` | 模组日志最后 200 行 |
| `selftest.log` / `startup_error.log` | 若有则一并带上 |
| `app_source_snapshot.py` | 当前 app.py 快照（便于对版本） |

出问题直接把这个 zip 发出来即可，不用再远程指导翻日志。

