using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Diagnostics;
using System.Linq;
using Terraria;
using Terraria.ID;
using TerrariaImageBuilder.Communication;
using TerrariaImageBuilder.Safety;
using TerrariaImageBuilder.Undo;

namespace TerrariaImageBuilder.Execution
{
    /// <summary>
    /// 方块执行引擎：把外部端下发的分块队列按帧步进放进世界。
    ///
    /// 只负责「放置」这一件事：
    ///   - 按 ChunkIndex 升序消费分块
    ///   - 每帧最多放 MaxBlocksPerFrame 个（自适应降速）
    ///   - 放置前做安全校验、避让玩家
    ///   - 分块完成 / 全部完成时回报状态
    ///
    /// 状态机的不变式（防「按 P 永久卡住」）：
    ///   _isRunning / _isPaused 是唯一真值来源，Status 只是给外部看的镜像，
    ///   ReconcileState() 每帧把镜像拉回一致；只有「队列里真还有活」才允许进入 Paused。
    ///
    /// 光照、飞行、刷怪等工具能力不在此实现。
    /// </summary>
    public class ExecutionEngine
    {
        /// <summary>运行中但超过这么多秒没有任何推进时记一条告警（不改变状态，只留证据）。</summary>
        private const double StallWarnSeconds = 10.0;

        private readonly SafetyGuard _safetyGuard;
        private readonly UndoManager _undoManager;
        private readonly ModLogger _logger;
        private Action<string, object> _statusCallback;

        // 并发队列：断线清队列发生在收发线程，放置发生在主线程，两者会同时碰这个队列
        private readonly ConcurrentQueue<ChunkExecutionContext> _chunkQueue = new ConcurrentQueue<ChunkExecutionContext>();
        private ChunkExecutionContext _currentChunk;
        private int _currentBlockIndexWithinChunk = 0;

        private volatile bool _isRunning = false;
        private volatile bool _isPaused = false;

        private int _framesSinceLastBlock = 0;
        private int _framesSinceLastChunk = 0;
        private int _delayBetweenChunksFrames = 0;
        private int _delayWithinChunkFrames = 0;

        private int _framesForFpsCalc = 0;
        private float _accumulatedDeltaTime = 0f;
        private float _currentFps = 60f;
        private int _blocksPerFrame = 1;

        private int? _originX;
        private int? _originY;

        private int _completedBlocks = 0;
        private int _totalBlocks = 0;
        private int _totalChunks = 0;
        private int _currentChunkIndex = 0;
        private long _taskStartTimestamp = 0;

        // 推进心跳：用 Stopwatch 而不是帧号，因为暂停时帧号不递增
        private long _lastProgressTicks = Stopwatch.GetTimestamp();
        private bool _stallWarned = false;

        public TaskStatus Status { get; private set; } = TaskStatus.Idle;
        public int CompletedBlocks => _completedBlocks;
        public int TotalBlocks => _totalBlocks;
        public int CurrentChunkIndex => _currentChunkIndex;
        public int TotalChunks => _totalChunks;

        /// <summary>还有没干完的活（含正在跑但队列已空的情况）。</summary>
        public bool HasPendingWork => _isRunning || _currentChunk != null || _chunkQueue.Count > 0;

        /// <summary>队列里还没开始的分块数（含正在执行的那一个）。</summary>
        public int PendingChunks => _chunkQueue.Count + (_currentChunk != null ? 1 : 0);

        /// <summary>还没放置的方块数（含正在执行分块内剩余的）。</summary>
        public int PendingBlocks
        {
            get
            {
                int n = 0;
                if (_currentChunk != null)
                    n += Math.Max(0, _currentChunk.Blocks.Count - _currentBlockIndexWithinChunk);
                foreach (var c in _chunkQueue) n += c.Blocks?.Count ?? 0;
                return n;
            }
        }

        /// <summary>每帧最多放置的方块数（由 PlacementSpeedLevel 映射）。</summary>
        public int MaxBlocksPerFrame { get; set; } = 32;

        public ExecutionEngine(SafetyGuard safetyGuard, UndoManager undoManager, ModLogger logger)
        {
            _safetyGuard = safetyGuard;
            _undoManager = undoManager;
            _logger = logger;
        }

        public void SetStatusCallback(Action<string, object> callback) => _statusCallback = callback;

        public void SetOrigin(int x, int y)
        {
            // 原点同样要挡脏数据：离谱原点会让后续每一块的加法都溢出
            if (x <= -1_000_000 || x >= 1_000_000 || y <= -1_000_000 || y >= 1_000_000)
            {
                _logger?.Warn($"原点被拒绝：({x},{y}) 超出合理范围 | origin rejected: ({x},{y}) out of sane range");
                return;
            }

            _originX = x;
            _originY = y;
            _logger?.Info($"执行引擎原点设置：({x}, {y})");
        }

        public void SetTiming(int? delayBetweenChunksMs, int? delayWithinChunkMs)
        {
            _delayBetweenChunksFrames = delayBetweenChunksMs.HasValue
                ? Math.Max(0, (int)Math.Round(delayBetweenChunksMs.Value * 60f / 1000f)) : 0;
            _delayWithinChunkFrames = delayWithinChunkMs.HasValue
                ? Math.Max(0, (int)Math.Round(delayWithinChunkMs.Value * 60f / 1000f)) : 0;
        }

        public void EnqueueChunks(List<ChunkCommand> chunks, int? chunkCount = null, int? totalBlocks = null)
        {
            if (chunks == null || chunks.Count == 0) return;

            foreach (var chunk in chunks.OrderBy(c => c.EffectiveIndex))
            {
                _chunkQueue.Enqueue(new ChunkExecutionContext
                {
                    Index = chunk.EffectiveIndex,
                    DelayMs = chunk.DelayMs,
                    Blocks = chunk.Blocks ?? new List<BlockCommand>()
                });
            }

            _totalChunks = chunkCount ?? (_totalChunks + chunks.Count);
            // 用 long 累加再夹紧，避免元数据溢出成负数
            long sum = (long)_totalBlocks + chunks.Sum(c => (long)(c.Blocks?.Count ?? 0));
            _totalBlocks = (int)Math.Min(int.MaxValue, Math.Max(0, sum));

            _logger?.Debug($"入队 {chunks.Count} 个分块，总分块 {_totalChunks}，总方块 {_totalBlocks}");
            TryStartFirstChunk();
        }

        public void EnqueueBlocks(List<BlockCommand> blocks, int chunkIndex = 0)
        {
            if (blocks == null || blocks.Count == 0) return;

            _chunkQueue.Enqueue(new ChunkExecutionContext
            {
                Index = chunkIndex,
                DelayMs = 0,
                Blocks = blocks
            });
            _totalChunks = Math.Max(_totalChunks, 1);
            long sum = (long)_totalBlocks + blocks.Count;
            _totalBlocks = (int)Math.Min(int.MaxValue, Math.Max(0, sum));

            _logger?.Debug($"旧版入队单分块，方块数 {blocks.Count}，总方块 {_totalBlocks}");
            TryStartFirstChunk();
        }

        private void TryStartFirstChunk()
        {
            if (_currentChunk == null && !_chunkQueue.IsEmpty && !_isPaused && _chunkQueue.TryDequeue(out var next))
            {
                _currentChunk = next;
                _currentBlockIndexWithinChunk = 0;
                _currentChunkIndex = _currentChunk.Index;
                _framesSinceLastChunk = 0;
                MarkProgress();
            }
        }

        public void Start()
        {
            if (_isRunning && !_isPaused) return;
            _isRunning = true;
            _isPaused = false;
            Status = TaskStatus.Running;
            _safetyGuard?.ResetSessionCounter();
            _taskStartTimestamp = Stopwatch.GetTimestamp();
            MarkProgress();
            _logger?.Info("执行引擎开始 | engine started");
        }

        /// <summary>
        /// 暂停。没有活的时候不回写 Paused——那会把状态机推进「Paused 但没队列」的死角，
        /// 之后按 P 只能反复走 Resume 的空转分支，看起来就是「永久卡住」。
        /// </summary>
        public void Pause()
        {
            if (!HasPendingWork)
            {
                ReconcileState();
                _logger?.Info("暂停请求忽略：当前没有进行中的任务 | pause ignored: no task in progress");
                return;
            }

            _isPaused = true;
            _isRunning = true;
            Status = TaskStatus.Paused;
            MarkProgress();
            _logger?.Info("执行引擎暂停 | engine paused");
        }

        /// <summary>
        /// 继续。三件事缺一不可：
        ///   1. 真正把 _isRunning 立起来（历史 bug：暂停期间 _isRunning 被清掉后 Resume 只清 _isPaused，
        ///      Update 的 if (!_isRunning) return 会让引擎永远不再走一帧）；
        ///   2. 清 _isPaused；
        ///   3. 若当前分块为空但队列有货，立刻取下一个。
        /// 没有活时把 Status 归位 Idle，绝不停在 Paused。
        /// </summary>
        public void Resume()
        {
            if (!HasPendingWork)
            {
                _isPaused = false;
                _isRunning = false;
                Status = TaskStatus.Idle;
                _logger?.Info("恢复请求忽略：队列已空，状态归位 Idle | resume ignored: queue empty, status reset to Idle");
                return;
            }

            _isRunning = true;
            _isPaused = false;
            Status = TaskStatus.Running;
            _framesSinceLastBlock = 0;
            _framesSinceLastChunk = 0;
            if (_taskStartTimestamp == 0) _taskStartTimestamp = Stopwatch.GetTimestamp();
            MarkProgress();
            TryStartFirstChunk();
            _logger?.Info("执行引擎继续 | engine resumed");
        }

        public void Stop()
        {
            _isRunning = false;
            _isPaused = false;
            _currentChunk = null;
            _currentBlockIndexWithinChunk = 0;
            _chunkQueue.Clear();
            _framesSinceLastBlock = 0;
            _framesSinceLastChunk = 0;
            Status = TaskStatus.Idle;
            _logger?.Info("执行引擎终止（人工停止）| engine stopped by command");
        }

        /// <summary>
        /// 通信断开时调用：丢掉全部未完成的工作，已放下的方块保留（不自动撤销，交给玩家按 U）。
        /// 重连后不会自动续跑——队列已经不存在，只有新的 PlaceBlocks 指令才会重新开始。
        /// </summary>
        public void AbortPendingTask(string zhReason, string enReason)
        {
            bool hadWork = HasPendingWork;
            int pendingChunks = PendingChunks;
            int pendingBlocks = PendingBlocks;

            // 先落 volatile 停标记，让主线程的放置循环在当前方块后立刻收手，再清队列/当前分块
            _isRunning = false;
            _isPaused = false;

            _chunkQueue.Clear();
            _currentChunk = null;
            _currentBlockIndexWithinChunk = 0;
            _framesSinceLastBlock = 0;
            _framesSinceLastChunk = 0;
            Status = TaskStatus.Idle;

            if (!hadWork)
            {
                _logger?.Info($"{zhReason}：当前没有未完成任务，无需清理 | {enReason}: no pending task to clear");
                return;
            }

            // 只留一行说明（中英双语），作为断线清队列的证据
            _logger?.Info($"{zhReason}：已清空未完成建造队列（丢弃 {pendingChunks} 个分块 / {pendingBlocks} 个未放置方块），" +
                          $"已放置 {_completedBlocks} 个保留，重连后不会自动续跑 | {enReason}: cleared unfinished build queue " +
                          $"({pendingChunks} chunk(s) / {pendingBlocks} pending block(s) dropped), {_completedBlocks} placed block(s) kept, no auto-resume after reconnect");
        }

        /// <summary>
        /// P 键：暂停 / 继续 / 起跑，三种情况都保证能从任何一个状态回到可推进状态。
        /// </summary>
        public void ToggleRunPause()
        {
            ReconcileState();

            if (_isPaused || Status == TaskStatus.Paused)
            {
                Resume();
                return;
            }

            if (Status == TaskStatus.Error)
            {
                // 错误态按 P 等于「重试当前队列」，没有队列就归位 Idle，绝不卡在 Error
                if (HasPendingWork) { Resume(); return; }
                Status = TaskStatus.Idle;
                _logger?.Info("热键 P：清除错误状态（队列为空）| hotkey P: error state cleared, queue empty");
                return;
            }

            if (_isRunning)
            {
                Pause();
                return;
            }

            if (HasPendingWork)
            {
                // Idle / Error 但队列还有货：直接恢复推进，而不是停在死状态
                Resume();
                return;
            }

            // 什么都没有：保持 Idle，绝不进入 Paused
            Status = TaskStatus.Idle;
            _logger?.Info("热键 P：当前没有任务 | hotkey P: no task to run");
        }

        public void SetError(string message)
        {
            Status = TaskStatus.Error;
            _isPaused = false;
            _logger?.Error(message);
        }

        public void Update(float deltaTime)
        {
            UpdateFps(deltaTime);
            ReconcileState();

            if (!_isRunning || _isPaused) return;

            WatchForStall();

            if (_currentChunk == null)
            {
                if (_chunkQueue.IsEmpty)
                {
                    _isRunning = false;
                    Status = TaskStatus.Idle;
                    var elapsedMs = (long)((Stopwatch.GetTimestamp() - _taskStartTimestamp) * 1000L / Stopwatch.Frequency);

                    // 先回安全过滤摘要（若有），再回 AllCompleted，玩家一眼能看到哪些方块被挡了
                    var rejectionSummary = _safetyGuard?.BuildRejectionSummary();
                    if (!string.IsNullOrEmpty(rejectionSummary))
                        _statusCallback?.Invoke("RejectSummary", rejectionSummary);

                    _statusCallback?.Invoke("AllCompleted", new AllCompletedPayload(_completedBlocks, elapsedMs));
                    _logger?.Info($"全部完成：{_completedBlocks} 方块，耗时 {elapsedMs}ms | all completed");
                    return;
                }

                _framesSinceLastChunk++;
                if (_framesSinceLastChunk < _delayBetweenChunksFrames) return;

                if (!_chunkQueue.TryDequeue(out var nextChunk))
                {
                    // 队列被并发清空（断线），本帧什么都不做
                    return;
                }

                _currentChunk = nextChunk;
                _currentBlockIndexWithinChunk = 0;
                _currentChunkIndex = _currentChunk.Index;
                _framesSinceLastChunk = 0;
                MarkProgress();
            }

            _framesSinceLastBlock++;
            if (_framesSinceLastBlock < _delayWithinChunkFrames) return;

            // 本帧锁定当前分块：断线清队列可能在收发线程把 _currentChunk 置空，
            // 用局部引用 + 每块复查，保证绝不往已作废的任务里写方块。
            var chunk = _currentChunk;
            if (chunk == null) return;

            int blocksThisFrame = _blocksPerFrame;
            for (int i = 0; i < blocksThisFrame && _currentBlockIndexWithinChunk < chunk.Blocks.Count; i++)
            {
                if (!_isRunning || _isPaused || !ReferenceEquals(_currentChunk, chunk))
                    return;

                var cmd = chunk.Blocks[_currentBlockIndexWithinChunk];
                if (cmd == null)
                {
                    _currentBlockIndexWithinChunk++;
                    continue;
                }

                // 相对坐标 + 原点在 long 上算：int 溢出会绕过所有边界检查
                long x = cmd.X;
                long y = cmd.Y;
                int tileId = cmd.TileId;

                if (_originX.HasValue && _originY.HasValue)
                {
                    x += _originX.Value;
                    y += _originY.Value;
                }

                // 避让玩家：绝不把方块放进角色占据的格子
                if (IsPlayerOccupied(x, y))
                {
                    _safetyGuard?.RecordSkippedPlayer();
                    _currentBlockIndexWithinChunk++;
                    continue;
                }

                if (_safetyGuard == null || !_safetyGuard.CanPlace(x, y, tileId, out _))
                {
                    _currentBlockIndexWithinChunk++;
                    continue;
                }

                int ix = (int)x;
                int iy = (int)y;

                try
                {
                    var tile = Main.tile[ix, iy];
                    if (tile == null)
                    {
                        _currentBlockIndexWithinChunk++;
                        continue;
                    }

                    tile.LiquidAmount = 0;
                    tile.Slope = SlopeType.Solid;
                    tile.IsHalfBlock = false;
                    tile.IsActuated = false;
                    tile.HasActuator = false;

                    tile.HasTile = true;
                    tile.TileType = (ushort)tileId;
                    WorldGen.SquareTileFrame(ix, iy, true);

                    _safetyGuard.RecordPlaced();
                    _undoManager?.RecordPlaced(chunk.Index, ix, iy);
                    _completedBlocks++;
                    _currentBlockIndexWithinChunk++;
                    _framesSinceLastBlock = 0;
                    MarkProgress();
                }
                catch (Exception ex)
                {
                    // 只记业务描述，不输出堆栈
                    _logger?.Error($"放置失败：({ix},{iy}) ID={tileId}，{ex.Message} | place failed");
                    _currentBlockIndexWithinChunk++;
                }
            }

            // 只有这一帧锁定的分块确实还在手上时才收尾（否则队列已被并发清空，什么都不动）
            if (_currentBlockIndexWithinChunk >= chunk.Blocks.Count && ReferenceEquals(_currentChunk, chunk))
            {
                _statusCallback?.Invoke("ChunkProgress", new ChunkProgressPayload(chunk.Index, _completedBlocks));
                _logger?.Info($"分块 {chunk.Index} 完成，累计 {_completedBlocks} 方块");
                _currentChunk = null;
                _currentBlockIndexWithinChunk = 0;
                _framesSinceLastChunk = 0;
                MarkProgress();
            }
        }

        /// <summary>
        /// 状态镜像自愈：Status / _isRunning / _isPaused 三者任何一刻不一致，这里下一帧就拉回来。
        /// 这是「暂停 → 恢复永不卡死」的第一道保险。
        /// </summary>
        private void ReconcileState()
        {
            if (_isPaused)
            {
                if (Status != TaskStatus.Paused) Status = TaskStatus.Paused;
                return;
            }

            if (Status == TaskStatus.Paused)
            {
                // 暂停标记已清但镜像还写着 Paused：说明恢复走了一半，直接补完
                Status = HasPendingWork ? TaskStatus.Running : TaskStatus.Idle;
            }

            if (Status == TaskStatus.Running && !_isRunning && HasPendingWork)
            {
                // 镜像说在跑、驱动标记却是停的：把驱动标记立起来，否则 Update 永远进不来
                _isRunning = true;
                MarkProgress();
            }

            // Error 状态没有驱动标记也跟着跑：它是给人看的一次性标记，不能让引擎停在原地
            if (Status == TaskStatus.Error && _isRunning == false && HasPendingWork)
            {
                _isRunning = true;
                Status = TaskStatus.Running;
                MarkProgress();
            }
        }

        private void MarkProgress()
        {
            _lastProgressTicks = Stopwatch.GetTimestamp();
            _stallWarned = false;
        }

        /// <summary>
        /// 卡死告警：运行中却长时间零推进（例如被外部暂停、更新钩子没被调用）。
        /// 只留日志证据，不擅自改状态。
        /// </summary>
        private void WatchForStall()
        {
            double idleSec = (Stopwatch.GetTimestamp() - _lastProgressTicks) / (double)Stopwatch.Frequency;
            if (idleSec < StallWarnSeconds || _stallWarned) return;

            _stallWarned = true;
            _logger?.Warn($"执行引擎 {idleSec:F0}s 无推进（状态=运行中/未暂停），请检查窗口是否失焦或分块延迟过大 | " +
                          $"engine idle {idleSec:F0}s while running");
        }

        private static bool IsPlayerOccupied(long x, long y)
        {
            if (Main.dedServ) return false;
            try
            {
                var p = Main.LocalPlayer;
                if (p == null || !p.active) return false;

                int px0 = (int)(p.position.X / 16f) - 1;
                int px1 = (int)((p.position.X + p.width) / 16f) + 1;
                int py0 = (int)(p.position.Y / 16f) - 1;
                int py1 = (int)((p.position.Y + p.height) / 16f) + 1;

                return x >= px0 && x <= px1 && y >= py0 && y <= py1;
            }
            catch
            {
                return false;
            }
        }

        private void UpdateFps(float deltaTime)
        {
            if (deltaTime <= 0f) return;

            _accumulatedDeltaTime += deltaTime;
            _framesForFpsCalc++;
            if (_accumulatedDeltaTime >= 1.0f)
            {
                _currentFps = _framesForFpsCalc / _accumulatedDeltaTime;
                _accumulatedDeltaTime = 0f;
                _framesForFpsCalc = 0;

                int cap = Math.Max(1, MaxBlocksPerFrame);
                if (_currentFps < 30f)
                    _blocksPerFrame = 1;
                else if (_currentFps > 55f)
                    _blocksPerFrame = Math.Min(cap, _blocksPerFrame + 2);
                else
                    _blocksPerFrame = Math.Clamp(_blocksPerFrame, 1, cap);
            }
        }

        public void Reset()
        {
            _chunkQueue.Clear();
            _currentChunk = null;
            _currentBlockIndexWithinChunk = 0;
            _completedBlocks = 0;
            _totalBlocks = 0;
            _totalChunks = 0;
            _currentChunkIndex = 0;
            _isRunning = false;
            _isPaused = false;
            _framesSinceLastBlock = 0;
            _framesSinceLastChunk = 0;
            Status = TaskStatus.Idle;
            _safetyGuard?.ResetTaskStats();
            MarkProgress();
        }
    }

    public class ChunkExecutionContext
    {
        public int Index { get; set; }
        public int DelayMs { get; set; }
        public List<BlockCommand> Blocks { get; set; } = new List<BlockCommand>();
    }

    public class ChunkProgressPayload
    {
        public int ChunkIndex { get; }
        public int CompletedBlocks { get; }
        public ChunkProgressPayload(int chunkIndex, int completedBlocks)
        {
            ChunkIndex = chunkIndex;
            CompletedBlocks = completedBlocks;
        }
    }

    public class AllCompletedPayload
    {
        public int TotalCompletedBlocks { get; }
        public long TotalCostMs { get; }
        public AllCompletedPayload(int totalCompletedBlocks, long totalCostMs)
        {
            TotalCompletedBlocks = totalCompletedBlocks;
            TotalCostMs = totalCostMs;
        }
    }
}
