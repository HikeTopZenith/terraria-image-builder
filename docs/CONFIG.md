# TerrariaImageBuilder 模组配置说明（v1.2.0）

> 配置只走 json，模组不提供游戏内设置面板。
> 配置文件路径（Windows）：
> `%USERPROFILE%\Documents\My Games\Terraria\ModLoader\TerrariaImageBuilder\config.json`
> （本机「文档」重定向到 `D:\文档`，实际路径为
> `D:\文档\My Games\Terraria\ModLoader\TerrariaImageBuilder\config.json`）

## 通信配置
- CommunicationMode: "WebSocket"（默认）或 "JsonPolling"
- WebSocketPort: 本地 WebSocket 监听端口，默认 8765（仅 127.0.0.1，不对外网开放）
- JsonPollingIntervalMs: JSON 文件轮询间隔，默认 300 毫秒
- JsonCommandFileName / JsonStatusFileName: 轮询模式下的指令 / 状态文件名

启动时日志会打印实际生效的通信模式、端口（或轮询间隔与目录），便于排查。

## 断线处理（v1.2.0 起）
- WebSocket 客户端断开（或模组卸载、JSON 轮询通道失效）时，**未完成的建造队列会被整队清空**：
  丢弃尚未放置的分块与方块，已放下的方块保留（要撤按 U）。
- 日志固定留一行说明，例如：
  `通信断开：已清空未完成建造队列（丢弃 3 个分块 / 1200 个未放置方块），已放置 480 个保留，重连后不会自动续跑`
- **重连后不会自动续跑**：只有外部端重新下发 `PlaceBlocks` 才会开始新任务。
- 退出世界同样会清空队列，任务不会被带进下一个世界。

## 安全配置
- SkipExistingBlocks: true/false，启用后不覆盖玩家原有建筑（被跳过的位置计入「已有方块」统计）
- MaxTotalBlocks: 单次任务已放置方块数上限（0 或负数 = 不限）；外部端指令里的 MaxTotalBlocks 会覆盖此项

### 固定安全上限（不可配置，写死在代码里）
- 单次任务硬上限 **200,000 方块**，超出直接回 `ExecutionError`
- TileId 必须满足 `0 <= TileId < TileID.Count`，且必须是可放置方块（实心方块 / 家具类 / 已注册模组方块）
- 坐标必须落在世界边界内（`Main.maxTilesX / maxTilesY`），且图块矩阵已就绪（未加载区域一律拒绝）
- 坐标用 long 做「相对坐标 + 原点」的加法并限制在 ±1,000,000 之内，杜绝 int 溢出绕过边界检查
- 每帧最多放置方块数由 PlacementSpeedLevel 映射：1→4，2→12，3→24，4/其它→32

### 被拒统计
任务结束时若有方块被拒，会回一条中英双语的 `ExecutionError` 摘要（协议字段不变），
内容形如「安全过滤：共拒绝 N 个方块（越界 a，未加载 b，非法ID c，不可放置 d，离谱坐标 e，超上限 f，已有方块 g），已放置 M 个」。
日志里同类拒绝最多记 5 行，之后只计数，避免刷爆日志文件。

## 执行配置
- PlacementSpeedLevel: 1=慢，2=中，3=快，4=极限（影响每帧放置上限，实际值按 FPS 自适应下调）

## 热键配置
按键名参考 `System.Windows.Forms.Keys` 的枚举名，也接受 `Keys.O` 这种带前缀写法，大小写不敏感。

- HotkeySetOrigin: 标记原点，默认 `"O"`
- HotkeyStartPause: 开始/暂停/继续，默认 `"P"`
- HotkeyUndoAll: 一键撤销全部，默认 `"U"`

**非法键名不会崩溃也不会静默**：回退到默认键 O / P / U，并在日志里写一行
`热键配置 HotkeySetOrigin 的键名 "xxx" 非法，回退默认 O`。
空值同样回退默认并记日志。游戏内「设置 → 控件」的重新绑定优先级更高。

## P 键状态机约定（v1.2.0 起）
- 只有在「队列里真还有活」时才允许进入暂停态；空队列按 P 不会把状态写成 Paused。
- 暂停 → 恢复强制把运行标记重新立起来，即使窗口失焦（`PostUpdateEverything` 不执行）也能靠
  `On_Main.Update` 兜底继续推进。
- 状态镜像每帧自愈：Status / 运行标记 / 暂停标记三者一旦不一致，下一帧自动纠正。
- 按键结果会用中英双语在聊天栏回显（已开始 / 已暂停 / 没有任务），不再"按了没反应"。

## 文案
- 玩家可见的提示与报错均为中英双语，且不输出底层堆栈。
- 推荐搭配（写进启动日志与工坊简介）：**HEROsMod**（全亮 / 建造工具）与 **Better Zoom**（镜头缩放）。
