# TerrariaImageBuilder · tModLoader 1.4.4 构建指南（v1.2.0）

## 一、环境（已就绪）

- .NET 8 SDK 8.0.426 —— 安装于 C:\Users\h\.dotnet，已写入用户 PATH
- tModLoader 1.4.4.9+2026.08.3.0（Steam）
- 注意：本机「文档」目录已重定向到 D:\文档，tModLoader 存档实际位于
  D:\文档\My Games\Terraria\tModLoader

## 二、官方标准构建（已验证跑通）

在项目目录执行：

    export PATH="/c/Users/h/.dotnet:$PATH"
    dotnet build TerrariaImageBuilder.csproj -c Release -v m

> **必须先把 `C:\Users\h\.dotnet` 放进 PATH。** MSBuild 默认调的是
> `C:\Program Files\dotnet\dotnet.exe`，那里只有 6.0/9.0 运行时、没有 .NET 8，
> 打包阶段会报 `MSB3073 … -2147450730` + `Framework: 'Microsoft.NETCore.App', version '8.0.0'` 缺失。
> 该报错发生在「编译成功之后」的打包阶段，容易被误判成代码编译失败。

csproj 中的 BuildMod=true 会触发 tMLMod.targets 的 BuildMod 目标，
由 tModLoader 内置编译器完成编译与打包，输出真实 TMOD 容器格式（非 zip）。

## 三、产物位置

官方产物（tModLoader 会自动装载这里）：

    D:\文档\My Games\Terraria\tModLoader\Mods\TerrariaImageBuilder.tmod

项目目录同步副本：

    D:\deepseek\TerrariaImageBuilder\TerrariaImageBuilder.tmod   （v1.2.0 实测 15,406,836 字节；智能构建产物体积随内容变化，勿以此数字做校验）

文件头魔数为 TMOD，可由 tModLoader 直接识别。

## 四、源码结构

- TerrariaImageBuilder.cs          Mod 主类（热键注册、模块装配、生命周期）
- TerrariaImageBuilderSystem.cs    ModSystem（执行引擎分帧 + 通信轮询）
- Input/ImageBuilderPlayer.cs      ModPlayer（O/P/U 热键）
- ModConfig.cs                     配置持久化（System.Text.Json）
- ModLogger.cs                     按天本地日志
- Communication/
  - MessageContract.cs             消息契约（分块指令 + 4 类状态）
  - ChecksumValidator.cs           SHA-256 规范化校验
  - ICommunicationProvider.cs      通信接口（只收发原始 JSON）
  - WebSocketProvider.cs           本地 WebSocket 服务端
  - JsonPollingProvider.cs         本地 JSON 文件轮询
  - CommunicationManager.cs        解析 -> 校验 -> 分发 -> 状态回传
- Execution/ExecutionEngine.cs     分块队列 + 分帧限速 + 自适应 FPS
- Safety/SafetyGuard.cs            边界 / 非法ID / 总上限 / 跳过已有方块
- Undo/UndoManager.cs              全部撤销 + 分段撤销

## 五、基础测试

1. 启动 tModLoader，启用 TerrariaImageBuilder，进入世界。
2. 按 O：角色脚下出现 1 个泥土方块。
3. WebSocket：外部程序连 ws://127.0.0.1:8765，发送带正确 Checksum 的 PlaceBlocks（Chunks 数组），
   应依次收到 InstructionReceived -> ChunkProgress... -> AllCompleted。
4. 篡改 Checksum：只收到 ExecutionError，不放置方块。
5. 旧版无 Chunks/Checksum 的 Blocks 指令：正常放置。
6. 越界坐标 / 非法 TileId：被拦截且不崩溃。
7. 日志：D:\文档\My Games\Terraria\tModLoader\Logs\TerrariaImageBuilder\（按天）
