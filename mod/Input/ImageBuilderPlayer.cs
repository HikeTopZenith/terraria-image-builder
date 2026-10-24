using System;
using Terraria;
using Terraria.ID;
using Terraria.GameInput;
using Terraria.ModLoader;
using Microsoft.Xna.Framework;

namespace TerrariaImageBuilder.Input
{
    /// <summary>
    /// 玩家热键处理。
    ///
    /// 只保留建造必需的三个热键（按键名与回退逻辑见 ModConfig / TerrariaImageBuilder.ParseKey）：
    ///   O = 标记原点（脚下放一个泥土）
    ///   P = 开始 / 暂停 / 继续
    ///   U = 一键撤销全部
    ///
    /// 所有玩家可见反馈都是中英双语，且绝不输出堆栈。
    /// 光照、飞行、刷怪等能力交给工具模组（如 HEROsMod），这里不重复实现。
    /// </summary>
    public class ImageBuilderPlayer : ModPlayer
    {
        public override void ProcessTriggers(TriggersSet triggersSet)
        {
            try
            {
                var mod = TerrariaImageBuilder.Instance;
                if (mod == null || mod.ExecEngine == null) return;

                if (TerrariaImageBuilder.OriginKeybind != null && TerrariaImageBuilder.OriginKeybind.JustPressed)
                    PlaceDirtMarkerAtFeet();

                if (TerrariaImageBuilder.StartPauseKeybind != null && TerrariaImageBuilder.StartPauseKeybind.JustPressed)
                    HandleStartPause(mod);

                if (TerrariaImageBuilder.UndoAllKeybind != null && TerrariaImageBuilder.UndoAllKeybind.JustPressed)
                    HandleUndoAll(mod);
            }
            catch (Exception ex)
            {
                // 只留业务描述，不输出堆栈
                TerrariaImageBuilder.Instance?.FileLogger?.Error(
                    Bilingual.Pair($"热键处理异常：{ex.Message}", $"hotkey handler error: {ex.Message}"));
            }
        }

        /// <summary>
        /// P 键：引擎内部保证从 Paused / Idle / Error 任一状态都能回到可推进状态，
        /// 这里只负责把结果用双语回显给玩家（否则玩家无从判断到底有没有生效）。
        /// </summary>
        private static void HandleStartPause(TerrariaImageBuilder mod)
        {
            mod.ExecEngine.ToggleRunPause();

            switch (mod.ExecEngine.Status)
            {
                case TaskStatus.Running:
                    Bilingual.ToPlayer(
                        $"建造已开始/继续：已放置 {mod.ExecEngine.CompletedBlocks} 个，待放置 {mod.ExecEngine.PendingBlocks} 个",
                        $"Build running: {mod.ExecEngine.CompletedBlocks} placed, {mod.ExecEngine.PendingBlocks} pending",
                        new Color(150, 240, 160));
                    break;

                case TaskStatus.Paused:
                    Bilingual.ToPlayer(
                        $"建造已暂停：已放置 {mod.ExecEngine.CompletedBlocks} 个，待放置 {mod.ExecEngine.PendingBlocks} 个（按 P 继续）",
                        $"Build paused: {mod.ExecEngine.CompletedBlocks} placed, {mod.ExecEngine.PendingBlocks} pending (press P to resume)",
                        new Color(255, 220, 140));
                    break;

                default:
                    Bilingual.ToPlayer(
                        "当前没有可执行的建造任务：请先在外部端下发任务（P 键不会把空队列变成暂停状态）",
                        "No build task in progress. Send a task from the desktop app first (P never leaves an empty queue paused).",
                        new Color(200, 200, 200));
                    break;
            }
        }

        private static void HandleUndoAll(TerrariaImageBuilder mod)
        {
            if (mod.UndoManager == null) return;
            int before = mod.UndoManager.GetTotalPlacedCount();
            mod.UndoManager.UndoAll();

            Bilingual.ToPlayer(
                $"已撤销本次任务放置的 {before} 个方块",
                $"Undone: {before} block(s) placed by this task were removed",
                new Color(170, 200, 255));
        }

        private void PlaceDirtMarkerAtFeet()
        {
            int tileX = (int)(Player.Center.X / 16f);
            int tileY = (int)((Player.position.Y + Player.height) / 16f) + 1;

            if (tileX < 0 || tileX >= Main.maxTilesX || tileY < 0 || tileY >= Main.maxTilesY)
            {
                Bilingual.ToPlayer("标记失败：角色脚下已越出世界边界", "Marker failed: your position is outside the world bounds",
                    new Color(255, 160, 120));
                return;
            }

            try
            {
                var tile = Main.tile[tileX, tileY];
                if (tile == null) return;

                tile.HasTile = true;
                tile.TileType = TileID.Dirt;
                WorldGen.SquareTileFrame(tileX, tileY);

                TerrariaImageBuilder.Instance?.FileLogger?.Info($"热键 O：在角色脚下生成泥土标记方块 ({tileX}, {tileY})");
                Bilingual.ToPlayer(
                    $"已标记原点：脚下的泥土方块 ({tileX}, {tileY})",
                    $"Origin marked with a dirt block at ({tileX}, {tileY})",
                    new Color(200, 235, 180));
            }
            catch (Exception ex)
            {
                TerrariaImageBuilder.Instance?.FileLogger?.Warn(
                    Bilingual.Pair($"热键 O 放置泥土失败：{ex.Message}", $"hotkey O failed to place dirt: {ex.Message}"));
                Bilingual.ToPlayer("原点标记失败：该位置当前不可写入", "Origin marker failed: this position cannot be written right now",
                    new Color(255, 160, 120));
            }
        }
    }
}
