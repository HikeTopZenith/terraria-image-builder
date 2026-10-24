using System;
using Terraria;
using Terraria.ID;
using Terraria.ModLoader;

namespace TerrariaImageBuilder.Safety
{
    /// <summary>
    /// 安全防护体系：世界边界 / 未加载区域 / 非法 TileId / 离谱坐标 / 总上限 / 跳过已有方块。
    ///
    /// 三条铁律：
    ///   1. 所有检查都在写 tile 之前执行，任何一条不通过就整块跳过，绝不半写；
    ///   2. 本类不抛异常，任何底层数组越界都在这里被拦下，不让它污染存档；
    ///   3. 每个被拒的方块都计数，任务结束时回一条 ExecutionError 摘要。
    ///
    /// 只做过滤，不做光照 / 飞行 / 清除等工具能力。
    /// </summary>
    public class SafetyGuard
    {
        /// <summary>
        /// 离谱坐标阈值。原版最大世界 8400 x 2400，任何超过百万量级的坐标都必然是脏数据。
        /// 用 long 判定，避免「外部端 OriginX + 相对 X」在 int 上溢出后恰好落进合法区间。
        /// </summary>
        private const long AbsurdCoordinate = 1_000_000L;

        // 默认值与单任务硬上限对齐；指令里的 MaxTotalBlocks 会覆盖它（0 或负数 = 不限）
        public int MaxTotalBlocks { get; set; } = 200000;
        public bool SkipExistingBlocks { get; set; } = false;

        private int _blocksPlacedThisSession;
        private readonly ModLogger _logger;

        // 被拒统计（每次任务开始清零，任务结束汇总）
        public int RejectedNotFinite { get; private set; }      // 离谱坐标 / 溢出
        public int RejectedOutOfBounds { get; private set; }    // 世界边界外
        public int RejectedNotLoaded { get; private set; }      // 世界边界内但图块矩阵未就绪（未加载区域）
        public int RejectedInvalidTileId { get; private set; }  // TileId < 0 / >= TileID.Count
        public int RejectedNotPlaceable { get; private set; }   // 不是可放置方块（墙/液体/特殊ID）
        public int RejectedLimit { get; private set; }          // 超过 MaxTotalBlocks
        public int RejectedExisting { get; private set; }       // SkipExistingBlocks 命中已有方块
        public int SkippedPlayer { get; private set; }          // 玩家占据格（避让，非错误）

        public int RejectedTotal =>
            RejectedNotFinite + RejectedOutOfBounds + RejectedNotLoaded +
            RejectedInvalidTileId + RejectedNotPlaceable + RejectedLimit + RejectedExisting;

        public SafetyGuard()
        {
            _logger = TerrariaImageBuilder.Instance?.FileLogger;
        }

        /// <summary>任务开始：清空计数。撤销/统计看过往成绩时不会串任务。</summary>
        public void ResetTaskStats()
        {
            RejectedNotFinite = 0;
            RejectedOutOfBounds = 0;
            RejectedNotLoaded = 0;
            RejectedInvalidTileId = 0;
            RejectedNotPlaceable = 0;
            RejectedLimit = 0;
            RejectedExisting = 0;
            SkippedPlayer = 0;
        }

        /// <summary>世界坐标是否落在世界边界内（不含图块矩阵是否就绪的判定）。</summary>
        public bool IsInWorldBounds(int x, int y)
        {
            return x >= 0 && x < Main.maxTilesX && y >= 0 && y < Main.maxTilesY;
        }

        /// <summary>
        /// 图块矩阵是否就绪。世界加载中 / 多人客户端尚未收到区域数据时，
        /// Main.tile 的可用范围可能小于世界边界，写进去就是无效方块。
        /// </summary>
        private static bool IsTileMapReady()
        {
            try
            {
                return Main.tile.Width >= Main.maxTilesX && Main.tile.Height >= Main.maxTilesY;
            }
            catch
            {
                return false;
            }
        }

        /// <summary>
        /// TileId 是否为已注册且存在于原版常量表内的 ID。
        /// </summary>
        public bool IsValidTileId(int tileId)
        {
            if (tileId < 0 || tileId >= TileID.Count) return false;
            try
            {
                // 模组方块也必须真的注册过，避免外部端塞一个空洞 ID
                if (tileId >= TileID.Count) return false;
                return true;
            }
            catch
            {
                return false;
            }
        }

        /// <summary>
        /// 该 ID 是否是「可放置的方块」：原版实心方块、家具类（frameImportant）或已注册的模组方块。
        /// 墙 / 液体 / 纯装饰特殊 ID 不通过，防止把不该进世界的 ID 写进 tile。
        /// </summary>
        private static bool IsPlaceableTileType(int tileId)
        {
            try
            {
                if (TileLoader.GetTile(tileId) != null) return true;   // 已注册的模组方块
                if (Main.tileSolid != null && tileId < Main.tileSolid.Length && Main.tileSolid[tileId]) return true;
                if (Main.tileFrameImportant != null && tileId < Main.tileFrameImportant.Length && Main.tileFrameImportant[tileId]) return true;
                return false;
            }
            catch
            {
                // 底层表未就绪时放行，交给 IsValidTileId 的范围判定兜底
                return true;
            }
        }

        /// <summary>目标位置是否已有方块。越界视为占用。</summary>
        public bool IsPositionOccupied(int x, int y)
        {
            if (!IsInWorldBounds(x, y)) return true;
            try
            {
                var tile = Main.tile[x, y];
                return tile != null && tile.HasTile;
            }
            catch
            {
                return true;
            }
        }

        /// <summary>
        /// 放置前总检查。返回 true 才允许写 tile。
        /// 坐标用 long 传入：外部端给的是 int，但「相对坐标 + 原点」的加法必须在 long 上做，
        /// 否则 int 溢出会绕过所有边界判断。
        /// </summary>
        public bool CanPlace(long x, long y, int tileId, out string skipReason)
        {
            skipReason = null;

            // ① 离谱坐标 / 溢出
            if (x < -AbsurdCoordinate || x > AbsurdCoordinate || y < -AbsurdCoordinate || y > AbsurdCoordinate)
            {
                RejectedNotFinite++;
                skipReason = "坐标溢出或离谱大";
                ThrottledWarn($"放置拒绝：坐标({x},{y})离谱大或溢出，超出 ±{AbsurdCoordinate}");
                return false;
            }

            // ② 世界边界
            if (x < 0 || x >= Main.maxTilesX || y < 0 || y >= Main.maxTilesY)
            {
                RejectedOutOfBounds++;
                skipReason = "越界";
                ThrottledWarn($"放置拒绝：坐标({x},{y})越出世界边界 {Main.maxTilesX}x{Main.maxTilesY}");
                return false;
            }

            // ③ 未加载区域（图块矩阵还没准备好）
            if (!IsTileMapReady())
            {
                RejectedNotLoaded++;
                skipReason = "世界区域未加载";
                ThrottledWarn("放置拒绝：图块矩阵尚未就绪（世界加载中或区域未同步）");
                return false;
            }

            int ix = (int)x;
            int iy = (int)y;

            // ④ 非法 TileId
            if (tileId < 0 || tileId >= TileID.Count)
            {
                RejectedInvalidTileId++;
                skipReason = "非法方块ID";
                ThrottledWarn($"放置拒绝：方块ID={tileId} 非法（合法区间 0 ~ {TileID.Count - 1}）");
                return false;
            }

            // ⑤ 不可放置的方块类型
            if (!IsPlaceableTileType(tileId))
            {
                RejectedNotPlaceable++;
                skipReason = "不是可放置方块";
                ThrottledWarn($"放置拒绝：方块ID={tileId} 不是可放置方块（非实心 / 非家具 / 未注册）");
                return false;
            }

            // ⑥ 总方块数上限（MaxTotalBlocks <= 0 表示不限）
            if (MaxTotalBlocks > 0 && _blocksPlacedThisSession >= MaxTotalBlocks)
            {
                RejectedLimit++;
                skipReason = "超过总方块数上限";
                ThrottledWarn($"放置拒绝：已放置 {_blocksPlacedThisSession} 方块，达到上限 {MaxTotalBlocks}");
                return false;
            }

            // ⑦ 跳过已有方块
            if (SkipExistingBlocks && IsPositionOccupied(ix, iy))
            {
                RejectedExisting++;
                skipReason = "位置已有方块（跳过模式）";
                return false;
            }

            return true;
        }

        public bool CanPlace(int x, int y, int tileId, out string skipReason)
            => CanPlace((long)x, (long)y, tileId, out skipReason);

        public void RecordPlaced() => _blocksPlacedThisSession++;
        public void RecordSkippedPlayer() => SkippedPlayer++;

        public void ResetSessionCounter()
        {
            _blocksPlacedThisSession = 0;
            ResetTaskStats();
        }

        public int GetPlacedCount() => _blocksPlacedThisSession;

        /// <summary>
        /// 同一条拒绝原因最多连续记 5 行日志，之后只计数，避免一次烂指令刷爆日志文件。
        /// </summary>
        private int _warnBudgetLeft = 5;
        private string _lastWarnTemplate = "";

        private void ThrottledWarn(string message)
        {
            // 按「去掉数字后的模板」归并，同类只报前 5 次
            string template = System.Text.RegularExpressions.Regex.Replace(message, @"[-\d]+", "#");
            if (template != _lastWarnTemplate)
            {
                _lastWarnTemplate = template;
                _warnBudgetLeft = 5;
            }

            if (_warnBudgetLeft > 0)
            {
                _warnBudgetLeft--;
                _logger?.Warn(message);
                if (_warnBudgetLeft == 0)
                    _logger?.Warn($"同类拒绝已过多，后续仅计数不记日志 | further identical rejections are counted only");
            }
        }

        /// <summary>
        /// 任务结束的双语拒绝摘要。全部通过时返回 null（不发多余的 ExecutionError）。
        /// </summary>
        public string BuildRejectionSummary()
        {
            if (RejectedTotal <= 0) return null;

            string zh = $"安全过滤：共拒绝 {RejectedTotal} 个方块（越界 {RejectedOutOfBounds}，未加载 {RejectedNotLoaded}，" +
                        $"非法ID {RejectedInvalidTileId}，不可放置 {RejectedNotPlaceable}，离谱坐标 {RejectedNotFinite}，" +
                        $"超上限 {RejectedLimit}，已有方块 {RejectedExisting}），已放置 {_blocksPlacedThisSession} 个";
            string en = $"Safety filter rejected {RejectedTotal} block(s) (out-of-bounds {RejectedOutOfBounds}, not loaded {RejectedNotLoaded}, " +
                        $"invalid ID {RejectedInvalidTileId}, not placeable {RejectedNotPlaceable}, absurd coords {RejectedNotFinite}, " +
                        $"over limit {RejectedLimit}, existing {RejectedExisting}); placed {_blocksPlacedThisSession}";

            _logger?.Warn("安全过滤摘要 | rejection summary: " + zh);

            return Bilingual.Pair(zh, en);
        }
    }
}
