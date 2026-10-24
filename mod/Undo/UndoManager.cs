using System;
using System.Collections.Generic;
using Terraria;
using Terraria.ID;

namespace TerrariaImageBuilder.Undo
{
    /// <summary>
    /// 撤销管理器：支持一键撤销全部、分段撤销最后一个分块。
    /// 记录已放置方块的坐标，撤销时将其还原为空气（或原背景）。
    /// 撤销操作复用执行引擎的分帧限速机制，避免批量删除卡顿。
    /// </summary>
    public class UndoManager
    {
        // 按分块索引记录已放置方块坐标
        private readonly Dictionary<int, List<(int x, int y)>> _placedByChunk = new Dictionary<int, List<(int x, int y)>>();
        private readonly List<(int x, int y)> _allPlaced = new List<(int x, int y)>();
        private int _lastCompletedChunkIndex = -1;

        public void RecordPlaced(int chunkIndex, int x, int y)
        {
            if (!_placedByChunk.ContainsKey(chunkIndex))
                _placedByChunk[chunkIndex] = new List<(int x, int y)>();
            _placedByChunk[chunkIndex].Add((x, y));
            _allPlaced.Add((x, y));
            _lastCompletedChunkIndex = chunkIndex;
        }

        /// <summary>
        /// 一键撤销全部：移除本次任务放置的所有方块。
        /// </summary>
        public List<(int x, int y)> UndoAll()
        {
            int minX = int.MaxValue, maxX = int.MinValue, minY = int.MaxValue, maxY = int.MinValue;
            var removed = new List<(int x, int y)>(_allPlaced);

            foreach (var (x, y) in _allPlaced)
            {
                if (x >= 0 && x < Main.maxTilesX && y >= 0 && y < Main.maxTilesY)
                {
                    Main.tile[x, y].ClearEverything();
                    WorldGen.SquareTileFrame(x, y);
                    if (x < minX) minX = x;
                    if (x > maxX) maxX = x;
                    if (y < minY) minY = y;
                    if (y > maxY) maxY = y;
                }
            }

            RefreshLighting(minX, maxX, minY, maxY);
            _placedByChunk.Clear();
            _allPlaced.Clear();
            _lastCompletedChunkIndex = -1;
            TerrariaImageBuilder.Instance.FileLogger.Info("已执行一键撤销全部");
            return removed;
        }

        /// <summary>
        /// 分段撤销：仅撤销最后一个完成的分块内的方块。
        /// </summary>
        public List<(int x, int y)> UndoLastChunk()
        {
            if (_lastCompletedChunkIndex < 0 || !_placedByChunk.ContainsKey(_lastCompletedChunkIndex))
                return new List<(int x, int y)>();

            var chunkBlocks = _placedByChunk[_lastCompletedChunkIndex];
            int minX = int.MaxValue, maxX = int.MinValue, minY = int.MaxValue, maxY = int.MinValue;

            foreach (var (x, y) in chunkBlocks)
            {
                if (x >= 0 && x < Main.maxTilesX && y >= 0 && y < Main.maxTilesY)
                {
                    Main.tile[x, y].ClearEverything();
                    WorldGen.SquareTileFrame(x, y);
                    if (x < minX) minX = x;
                    if (x > maxX) maxX = x;
                    if (y < minY) minY = y;
                    if (y > maxY) maxY = y;
                }
            }

            RefreshLighting(minX, maxX, minY, maxY);

            // 从全局列表中移除该分块的方块
            foreach (var (x, y) in chunkBlocks)
                _allPlaced.Remove((x, y));

            _placedByChunk.Remove(_lastCompletedChunkIndex);

            // 重新计算最后一个完成的分块索引
            if (_placedByChunk.Count > 0)
                _lastCompletedChunkIndex = GetMaxChunkIndex();
            else
                _lastCompletedChunkIndex = -1;

            TerrariaImageBuilder.Instance.FileLogger.Info($"已撤销分块 {_lastCompletedChunkIndex + 1}，方块数 {chunkBlocks.Count}");
            return chunkBlocks;
        }

        /// <summary>撤销后重算光照，否则原位置会残留黑块。</summary>
        private static void RefreshLighting(int minX, int maxX, int minY, int maxY)
        {
            if (Terraria.Main.dedServ) return;
            if (maxX < minX || maxY < minY) return;
            try
            {
                int x0 = Math.Max(0, minX - 2);
                int x1 = Math.Min(Terraria.Main.maxTilesX - 1, maxX + 2);
                int y0 = Math.Max(0, minY - 2);
                int y1 = Math.Min(Terraria.Main.maxTilesY - 1, maxY + 2);
                Terraria.Lighting.LightTiles(x0, x1, y0, y1);
            }
            catch
            {
                // 光照刷新失败不影响撤销本身
            }
        }

        private int GetMaxChunkIndex()
        {
            int max = -1;
            foreach (var kv in _placedByChunk)
                if (kv.Key > max) max = kv.Key;
            return max;
        }

        public int GetTotalPlacedCount() => _allPlaced.Count;
    }
}
