using System;
using System.Diagnostics;
using Terraria;
using Terraria.ModLoader;
using Microsoft.Xna.Framework;
using TerrariaImageBuilder.Communication;

namespace TerrariaImageBuilder
{
    /// <summary>
    /// 游戏主循环驱动。
    ///
    /// 只做两件事：
    ///   1. 每帧推进执行引擎（分帧放置方块）
    ///   2. 每帧推进通信层轮询（JSON 文件模式）
    ///
    /// 关键点（历史坑，改之前先读）：
    ///   - Terraria 窗口失焦时 PostUpdateEverything 不再执行，所以额外挂 On_Main.Update 兜底；
    ///   - 暂停时 Main.GameUpdateCount 不递增，不能拿它当唯一去重依据，否则会挡掉之后所有帧。
    ///     因此这里用「帧号变了就整帧去重 + 帧号没变就退化成 4ms 时间窗」的双保险。
    /// </summary>
    public class TerrariaImageBuilderSystem : ModSystem
    {
        private long _lastTimestamp = Stopwatch.GetTimestamp();
        private long _lastDriveTicks;
        private uint _lastFrameId = uint.MaxValue;
        private bool _wasInWorld;
        // 每个世界只提示一次：告诉玩家桌面端去哪下（创意工坊只能分发模组本身）
        private bool _downloadHintShown;

        public override void Load()
        {
            On_Main.Update += OnUpdateHook;
        }

        public override void Unload()
        {
            On_Main.Update -= OnUpdateHook;
            _wasInWorld = false;
        }

        private void OnUpdateHook(On_Main.orig_Update orig, Main self, GameTime gameTime)
        {
            orig(self, gameTime);
            Drive();
        }

        public override void PostUpdateEverything()
        {
            Drive();
        }

        /// <summary>
        /// 每帧最多真正驱动一次。
        /// 帧号前进了 → 本帧已驱动过就直接返回（On_Main.Update 与 PostUpdateEverything 同帧只算一次）；
        /// 帧号没前进（暂停/失焦）→ 退化成 4ms 时间窗，保证引擎仍然被推进，不会被去重永久挡死。
        /// </summary>
        private void Drive()
        {
            var mod = TerrariaImageBuilder.Instance;
            if (mod == null) return;

            if (Main.dedServ || Main.gameMenu)
            {
                // 退出世界：绝不把未完成任务带进下一个世界
                if (_wasInWorld)
                {
                    _wasInWorld = false;
                    try { mod.CommManager?.AbortForWorldExit("离开世界", "world exited"); }
                    catch { }
                }
                return;
            }

            _wasInWorld = true;

            if (!_downloadHintShown)
            {
                // 只提示一次，且不刷屏：玩家第一次进世界时给一句可点击阅读的指引
                _downloadHintShown = true;
                try
                {
                    Bilingual.EnqueueToPlayer(
                        "TerrariaImageBuilder 已就绪。本模组只负责放方块，把图片算成蓝图的桌面程序在：" +
                        CommunicationManager.DesktopDownloadUrl +
                        "（创意工坊只能分发模组本身，桌面端无法放进工坊）。",
                        "TerrariaImageBuilder ready. This mod only places blocks; the desktop app that turns images into " +
                        "blueprints is at " + CommunicationManager.DesktopDownloadUrl +
                        " (the Workshop can only ship the mod itself, not the desktop app).",
                        new Microsoft.Xna.Framework.Color(150, 210, 255));
                }
                catch
                {
                    // 提示失败不影响任何建造逻辑
                }
            }

            long now = Stopwatch.GetTimestamp();
            uint frameId = Main.GameUpdateCount;
            bool frameAdvanced = frameId != _lastFrameId;
            long sinceLastDrive = now - _lastDriveTicks;

            if (!frameAdvanced && _lastDriveTicks != 0 && sinceLastDrive < Stopwatch.Frequency / 250)
                return;

            _lastFrameId = frameId;
            _lastDriveTicks = now;

            try
            {
                float deltaTime = (float)(now - _lastTimestamp) / Stopwatch.Frequency;
                _lastTimestamp = now;
                if (deltaTime > 1f) deltaTime = 1f;
                if (deltaTime <= 0f) deltaTime = 1f / 60f;

                // 收发线程排队过来的玩家提示统一在这里（主线程）输出
                Bilingual.FlushPending();

                mod.ExecEngine?.Update(deltaTime);
                mod.CommManager?.Update();
            }
            catch (Exception ex)
            {
                mod.FileLogger?.Error(Bilingual.Pair($"驱动循环异常：{ex.Message}", $"driver loop error: {ex.Message}"));
            }
        }
    }
}
