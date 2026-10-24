using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.Json.Nodes;
using Terraria;
using TerrariaImageBuilder.Execution;
using TerrariaImageBuilder.Safety;
using TerrariaImageBuilder.Undo;

namespace TerrariaImageBuilder.Communication
{
    /// <summary>
    /// 通信管理器：调度 WebSocket / JSON 轮询两种提供者。
    ///
    /// 职责边界：
    ///   入站 → 解析 → Checksum 校验 → 分发
    ///   出站 → InstructionReceived / ChunkProgress / AllCompleted / ExecutionError
    ///
    /// 只做「方块放置」相关的协议处理，不含任何光照 / 通行 / 清除逻辑。
    /// </summary>
    public class CommunicationManager : IDisposable
    {
        private readonly ExecutionEngine _execEngine;
        private readonly SafetyGuard _safetyGuard;
        private readonly UndoManager _undoManager;
        private readonly ModLogger _logger;
        private readonly ModConfig _config;

        private ICommunicationProvider _provider;
        private bool _initialized = false;

        // 断线清队列只处理一次（JSON 轮询可能重复触发 OnDisconnected）
        private bool _disconnectedHandled = false;
        // 一旦因断线清过队列，重连时必须明确「不自动续跑」
        private bool _taskAbortedByDisconnect = false;

        // 单次任务方块总数硬上限，防止误传百万级任务拖死游戏（启动日志会打印此值）
        internal const int MaxBlocksPerTask = 200000;

        public CommunicationManager(ExecutionEngine execEngine, SafetyGuard safetyGuard, UndoManager undoManager, ModLogger logger)
        {
            _execEngine = execEngine;
            _safetyGuard = safetyGuard;
            _undoManager = undoManager;
            _logger = logger;
            _config = TerrariaImageBuilder.Settings;
        }

        public void Initialize()
        {
            if (_initialized) return;

            switch (_config.CommunicationMode)
            {
                case CommunicationMode.JsonPolling:
                    _provider = new JsonPollingProvider(_logger, _config);
                    break;
                case CommunicationMode.WebSocket:
                default:
                    _provider = new WebSocketProvider(_logger, _config.WebSocketPort);
                    break;
            }

            _provider.OnRawCommandReceived += OnRawCommandReceived;
            _provider.OnConnected += OnProviderConnected;
            _provider.OnDisconnected += OnProviderDisconnected;
            _provider.OnError += msg => _logger.Error(Bilingual.Pair($"通信错误：{msg}", $"communication error: {msg}"));

            _execEngine.SetStatusCallback(OnEngineStatus);

            _provider.Initialize();
            _initialized = true;
        }

        public void Update() => _provider?.Update();

        // ───────────────── 连接生命周期 ─────────────────

        private void OnProviderConnected()
        {
            _disconnectedHandled = false;
            _logger.Info(Bilingual.Pair("通信通道已连接", "communication channel connected"));

            // 先报身份：外部端据此显示模组版本，并在协议版本不一致时提示更新
            SendObject(new ServerHelloStatus
            {
                MsgType = "ServerHello",
                ModVersion = TerrariaImageBuilder.Instance?.Version?.ToString() ?? "unknown",
                ProtocolVersion = ProtocolVersion,
                DesktopDownloadUrl = DesktopDownloadUrl,
                Timestamp = NowMs()
            });

            if (_taskAbortedByDisconnect)
            {
                _taskAbortedByDisconnect = false;
                _logger.Info(Bilingual.Pair(
                    "重连不自动续跑：上次未完成的队列已在断线时清空，需要外部端重新下发 PlaceBlocks",
                    "No auto-resume after reconnect: the previous queue was cleared on disconnect; send PlaceBlocks again"));
                Bilingual.EnqueueToPlayer(
                    "通信已重连。上次未完成的建造队列已在断线时清空，本次不会自动续跑，请重新下发任务。",
                    "Reconnected. The unfinished build queue was cleared when the connection dropped and will NOT auto-resume. Please send the task again.",
                    new Microsoft.Xna.Framework.Color(255, 210, 130));
            }
        }

        /// <summary>
        /// 断线清理的唯一入口：WebSocket 客户端断开、JSON 轮询通道失效都会走到这里。
        ///
        /// 调用链：
        ///   WebSocketProvider.ReceiveLoop 的 finally（或 Dispose）→ OnDisconnected
        ///   JsonPollingProvider.Dispose / 状态文件连续写失败 → OnDisconnected
        ///   → CommunicationManager.OnProviderDisconnected → ExecutionEngine.AbortPendingTask
        ///   → _chunkQueue.Clear() + _currentChunk = null（队列在这里被真正清空）
        ///
        /// 已放下的方块保留不撤销（撤销是玩家按 U 的事），重连后不自动续跑。
        /// </summary>
        private void OnProviderDisconnected()
        {
            _logger.Info(Bilingual.Pair("通信通道已断开", "communication channel disconnected"));

            if (_disconnectedHandled) return;
            _disconnectedHandled = true;

            if (_execEngine != null && _execEngine.HasPendingWork)
            {
                int pendingChunks = _execEngine.PendingChunks;
                int pendingBlocks = _execEngine.PendingBlocks;

                _execEngine.AbortPendingTask("通信断开", "connection lost");
                _taskAbortedByDisconnect = true;
                _safetyGuard?.ResetTaskStats();

                Bilingual.EnqueueToPlayer(
                    $"通信中断，已丢弃未完成建造队列（{pendingChunks} 个分块 / {pendingBlocks} 个方块）；重连后不会自动续跑。",
                    $"Connection lost: unfinished build queue discarded ({pendingChunks} chunk(s) / {pendingBlocks} block(s)). It will NOT auto-resume after reconnect.",
                    new Microsoft.Xna.Framework.Color(255, 160, 120));
            }
        }

        /// <summary>
        /// 退出世界 / 卸载时同样不允许残留任务（不依赖连接事件）。
        /// </summary>
        public void AbortForWorldExit(string zhReason, string enReason)
        {
            if (_execEngine == null || !_execEngine.HasPendingWork) return;
            _execEngine.AbortPendingTask(zhReason, enReason);
            _taskAbortedByDisconnect = true;
        }

        // ───────────────── 入站 ─────────────────

        private void OnRawCommandReceived(string json)
        {
            IncomingCommand cmd;
            try
            {
                cmd = MessageSerializer.Deserialize<IncomingCommand>(json);
            }
            catch (Exception ex)
            {
                _logger.Warn(Bilingual.Pair($"指令反序列化失败：{ex.Message}", $"command deserialize failed: {ex.Message}"));
                SendExecutionError(Bilingual.Pair("指令格式非法，无法解析", "Malformed command: JSON could not be parsed"), null);
                return;
            }

            if (cmd == null)
            {
                SendExecutionError(Bilingual.Pair("指令内容为空", "Empty command payload"), null);
                return;
            }

            bool hasChecksum = false;
            try
            {
                hasChecksum = JsonNode.Parse(json) is JsonObject o && o.ContainsKey("Checksum");
            }
            catch { }

            if (hasChecksum && !ChecksumValidator.VerifyChecksum(json, cmd.Checksum))
            {
                _logger.Error(Bilingual.Pair("Checksum 校验失败，拒绝执行", "Checksum mismatch, execution refused"));
                SendExecutionError(Bilingual.Pair(
                    "Checksum 校验失败：指令可能被篡改或写入不完整，请重新下发",
                    "Checksum mismatch: the command was modified or only partially written. Please resend it."), null);
                return;
            }

            Dispatch(cmd);
        }

        private void Dispatch(IncomingCommand cmd)
        {
            _logger.Debug($"收到指令：Type={cmd.Type}, Chunks={cmd.Chunks?.Count ?? 0}, Blocks={cmd.Blocks?.Count ?? 0}");

            switch (cmd.Type)
            {
                case CommandType.SetOrigin:
                    HandleSetOrigin(cmd);
                    break;
                case CommandType.PlaceBlocks:
                case CommandType.StartTask:
                    HandlePlaceBlocks(cmd);
                    break;
                case CommandType.PauseTask:
                    _execEngine.Pause();
                    break;
                case CommandType.ResumeTask:
                    _execEngine.Resume();
                    break;
                case CommandType.StopTask:
                    _execEngine.Stop();
                    break;
                case CommandType.UndoAll:
                    _undoManager.UndoAll();
                    break;
                case CommandType.UndoLastChunk:
                    _undoManager.UndoLastChunk();
                    break;
                case CommandType.GetPlayerPos:
                    HandleGetPlayerPos();
                    break;
                default:
                    _logger.Warn($"未知指令类型：{cmd.Type}");
                    SendExecutionError(Bilingual.Pair($"未知指令类型：{cmd.Type}", $"Unknown command type: {cmd.Type}"), null);
                    break;
            }
        }

        private void HandleSetOrigin(IncomingCommand cmd)
        {
            if (!cmd.OriginX.HasValue || !cmd.OriginY.HasValue)
            {
                SendExecutionError(Bilingual.Pair("SetOrigin 缺少 OriginX / OriginY", "SetOrigin is missing OriginX / OriginY"), null);
                return;
            }
            _execEngine.SetOrigin(cmd.OriginX.Value, cmd.OriginY.Value);
            _logger.Info($"原点已设置：({cmd.OriginX}, {cmd.OriginY})");
        }

        private void HandlePlaceBlocks(IncomingCommand cmd)
        {
            int ox = cmd.OriginX ?? 0;
            int oy = cmd.OriginY ?? 0;

            if (cmd.OriginX.HasValue && cmd.OriginY.HasValue)
                _execEngine.SetOrigin(ox, oy);

            if (cmd.MaxTotalBlocks.HasValue)
                _safetyGuard.MaxTotalBlocks = cmd.MaxTotalBlocks.Value;   // 0 或负数 = 不限

            if (cmd.SkipExistingBlocks.HasValue)
                _safetyGuard.SkipExistingBlocks = cmd.SkipExistingBlocks.Value;

            _execEngine.SetTiming(cmd.DelayBetweenChunksMs ?? cmd.DelayWithinChunkMs, cmd.DelayWithinChunkMs);
            _execEngine.Reset();

            // 新版：分块结构
            if (cmd.Chunks != null && cmd.Chunks.Count > 0)
            {
                var ordered = cmd.Chunks.OrderBy(c => c.EffectiveIndex).ToList();
                int totalBlocks = cmd.TotalBlocks ?? ordered.Sum(c => c.Blocks?.Count ?? 0);
                int chunkCount = cmd.ChunkCount ?? ordered.Count;

                if (totalBlocks > MaxBlocksPerTask)
                {
                    _logger.Error(Bilingual.Pair($"任务被拒绝：{totalBlocks} 方块超过单次上限 {MaxBlocksPerTask}",
                        $"task refused: {totalBlocks} blocks exceed the per-task limit {MaxBlocksPerTask}"));
                    SendExecutionError(Bilingual.Pair(
                        $"方块总数 {totalBlocks} 超过单次上限 {MaxBlocksPerTask}，请缩小图片或分多次建造",
                        $"Total blocks {totalBlocks} exceed the per-task limit {MaxBlocksPerTask}; shrink the image or build in several passes."), null);
                    return;
                }

                var copies = ordered.Select(c => new ChunkCommand
                {
                    Index = c.EffectiveIndex,
                    DelayMs = c.DelayMs,
                    Blocks = c.Blocks?.Select(b => new BlockCommand { X = b.X, Y = b.Y, TileId = b.TileId }).ToList()
                             ?? new List<BlockCommand>()
                }).ToList();

                _execEngine.EnqueueChunks(copies, chunkCount, totalBlocks);
                _execEngine.Start();

                SendInstructionReceived(totalBlocks, chunkCount);
                _logger.Info($"新版分块任务开始：{chunkCount} 个分块，共 {totalBlocks} 方块");
                return;
            }

            // 旧版：单批次方块数组
            if (cmd.Blocks != null && cmd.Blocks.Count > 0)
            {
                if (cmd.Blocks.Count > MaxBlocksPerTask)
                {
                    _logger.Error(Bilingual.Pair($"任务被拒绝：{cmd.Blocks.Count} 方块超过单次上限 {MaxBlocksPerTask}",
                        $"task refused: {cmd.Blocks.Count} blocks exceed the per-task limit {MaxBlocksPerTask}"));
                    SendExecutionError(Bilingual.Pair(
                        $"方块总数 {cmd.Blocks.Count} 超过单次上限 {MaxBlocksPerTask}，请缩小图片或分多次建造",
                        $"Total blocks {cmd.Blocks.Count} exceed the per-task limit {MaxBlocksPerTask}; shrink the image or build in several passes."), null);
                    return;
                }

                var copies = cmd.Blocks.Select(b => new BlockCommand { X = b.X, Y = b.Y, TileId = b.TileId }).ToList();
                _execEngine.EnqueueBlocks(copies, cmd.ChunkIndex ?? 0);
                _execEngine.Start();

                SendInstructionReceived(copies.Count, 1);
                _logger.Info($"旧版单批次任务开始：{copies.Count} 方块");
                return;
            }

            SendExecutionError(Bilingual.Pair("PlaceBlocks 指令既无 Chunks 也无 Blocks",
                "PlaceBlocks contains neither Chunks nor Blocks"), null);
        }

        private void HandleGetPlayerPos()
        {
            try
            {
                var player = Main.LocalPlayer;
                if (player == null)
                {
                    SendExecutionError(Bilingual.Pair("无法获取本地玩家（可能还没进世界）",
                        "Local player unavailable (not in a world yet)"), null);
                    return;
                }

                int tx = (int)(player.Center.X / 16f);
                int ty = (int)(player.Center.Y / 16f);

                int surfaceY = (int)Main.worldSurface;
                for (int y = 10; y < Main.maxTilesY - 10; y++)
                {
                    var t = Main.tile[tx, y];
                    if (t != null && t.HasTile && Main.tileSolid[t.TileType]) { surfaceY = y; break; }
                }

                SendObject(new PlayerPosStatus
                {
                    MsgType = "PlayerPosition",
                    X = tx,
                    Y = ty,
                    SurfaceY = surfaceY,
                    Timestamp = NowMs()
                });

                _logger.Info($"回传玩家位置：({tx}, {ty})，该列地表 y={surfaceY}");
            }
            catch (Exception ex)
            {
                _logger.Warn($"获取玩家位置失败：{ex.Message}");
            }
        }

        // ───────────────── 出站 ─────────────────

        private void OnEngineStatus(string msgType, object payload)
        {
            try
            {
                switch (msgType)
                {
                    case "ChunkProgress" when payload is ChunkProgressPayload chunk:
                        SendObject(new ChunkProgressStatus
                        {
                            MsgType = "ChunkProgress",
                            ChunkIndex = chunk.ChunkIndex,
                            CompletedBlocks = chunk.CompletedBlocks,
                            Timestamp = NowMs()
                        });
                        break;

                    // 安全过滤摘要：任务结束时若有方块被拒，回一条 ExecutionError（协议字段不变）
                    case "RejectSummary" when payload is string summary:
                        SendExecutionError(summary, null);
                        break;

                    case "AllCompleted" when payload is AllCompletedPayload all:
                        SendObject(new AllCompletedStatus
                        {
                            MsgType = "AllCompleted",
                            TotalCompletedBlocks = all.TotalCompletedBlocks,
                            TotalCostMs = all.TotalCostMs,
                            Timestamp = NowMs()
                        });
                        break;
                }
            }
            catch (Exception ex)
            {
                _logger.Warn($"状态消息构造失败：{ex.Message}");
            }
        }

        private void SendInstructionReceived(int totalBlocks, int chunkCount)
        {
            SendObject(new InstructionReceivedStatus
            {
                MsgType = "InstructionReceived",
                TotalBlocks = totalBlocks,
                ChunkCount = chunkCount,
                Timestamp = NowMs()
            });
        }

        public void SendExecutionError(string errorMessage, int? errorChunkIndex)
        {
            SendObject(new ExecutionErrorStatus
            {
                MsgType = "ExecutionError",
                ErrorMessage = errorMessage,
                ErrorChunkIndex = errorChunkIndex,
                Timestamp = NowMs()
            });
        }

        /// <summary>
        /// 通信协议版本。外部端与模组各认一个数字，不一致时外部端会提示更新桌面端。
        /// 只有当「消息字段语义」真的变了才递增；单纯加可选字段不算。
        /// </summary>
        public const int ProtocolVersion = 1;

        /// <summary>桌面端下载地址。改这里要同步 mod.hjson 的 Website。</summary>
        public const string DesktopDownloadUrl =
            "https://github.com/HikeTopZenith/terraria-image-builder/releases/latest";

        private void SendObject(object message)
        {
            try
            {
                _provider?.SendMessage(MessageSerializer.Serialize(message));
            }
            catch (Exception ex)
            {
                _logger.Warn($"状态发送失败：{ex.Message}");
            }
        }

        private static long NowMs() => DateTimeOffset.Now.ToUnixTimeMilliseconds();

        public void Dispose() => _provider?.Dispose();
    }
}
