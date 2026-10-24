using System;
using Terraria;
using Terraria.ModLoader;
using Microsoft.Xna.Framework.Input;
using TerrariaImageBuilder.Communication;
using TerrariaImageBuilder.Execution;
using TerrariaImageBuilder.Safety;
using TerrariaImageBuilder.Undo;

namespace TerrariaImageBuilder
{
    /// <summary>
    /// 模组主类。
    ///
    /// 定位非常单纯：接收外部端算好的方块队列，分帧放进世界。
    /// 光照 / 飞行 / 刷怪抑制 / 区域清除等通用工具能力不在这里重复实现，
    /// 由 HEROsMod 之类的工具模组负责。
    /// </summary>
    public class TerrariaImageBuilder : Mod
    {
        internal static TerrariaImageBuilder Instance { get; private set; }
        internal static ModConfig Settings { get; private set; }

        // 热键（按键名可在 config.json 改，也可在游戏内 设置 → 控件 重新绑定）
        internal static ModKeybind OriginKeybind { get; private set; }
        internal static ModKeybind StartPauseKeybind { get; private set; }
        internal static ModKeybind UndoAllKeybind { get; private set; }

        // 实际生效的按键（非法配置会回退到这里记录的默认值），用于启动日志
        internal static Keys ResolvedOriginKey { get; private set; } = Keys.O;
        internal static Keys ResolvedStartPauseKey { get; private set; } = Keys.P;
        internal static Keys ResolvedUndoAllKey { get; private set; } = Keys.U;

        internal CommunicationManager CommManager { get; private set; }
        internal ExecutionEngine ExecEngine { get; private set; }
        internal SafetyGuard SafetyGuard { get; private set; }
        internal UndoManager UndoManager { get; private set; }
        internal ModLogger FileLogger { get; private set; }

        public override void Load()
        {
            Instance = this;

            // 日志必须先于一切：热键解析失败也要有地方落笔
            FileLogger = new ModLogger();
            FileLogger.Info(Bilingual.Pair("TerrariaImageBuilder 模组加载开始", "TerrariaImageBuilder loading"));

            Settings = new ModConfig();
            Settings.Load();
            Settings.EnsureConfigFileExists();

            // 热键：配置优先，非法键名回退默认并记日志
            ResolvedOriginKey = ParseKey(Settings.HotkeySetOrigin, Keys.O, nameof(Settings.HotkeySetOrigin));
            ResolvedStartPauseKey = ParseKey(Settings.HotkeyStartPause, Keys.P, nameof(Settings.HotkeyStartPause));
            ResolvedUndoAllKey = ParseKey(Settings.HotkeyUndoAll, Keys.U, nameof(Settings.HotkeyUndoAll));

            // 注册名是本地化键的稳定锚点，故意用英文标识，中文显示名放在 Localization/zh-Hans 里
            OriginKeybind = KeybindLoader.RegisterKeybind(this, "SetOrigin", ResolvedOriginKey);
            StartPauseKeybind = KeybindLoader.RegisterKeybind(this, "StartPause", ResolvedStartPauseKey);
            UndoAllKeybind = KeybindLoader.RegisterKeybind(this, "UndoAll", ResolvedUndoAllKey);

            SafetyGuard = new SafetyGuard
            {
                MaxTotalBlocks = Settings.MaxTotalBlocks,
                SkipExistingBlocks = Settings.SkipExistingBlocks
            };
            UndoManager = new UndoManager();
            ExecEngine = new ExecutionEngine(SafetyGuard, UndoManager, FileLogger)
            {
                MaxBlocksPerFrame = Settings.PlacementSpeedLevel switch
                {
                    1 => 4,
                    2 => 12,
                    3 => 24,
                    _ => 32
                }
            };
            CommManager = new CommunicationManager(ExecEngine, SafetyGuard, UndoManager, FileLogger);

            CommManager.Initialize();

            // 协议自检：与外部端的校验和口径是否仍然一致（漂移的话所有指令都会被拒）
            if (ChecksumValidator.SelfCheck(out string checksumDetail))
                FileLogger.Info(checksumDetail);
            else
                FileLogger.Error(checksumDetail);

            WriteStartupLog();
        }

        /// <summary>
        /// 上架合规的启动日志：通信方式、端口、三枚热键、安全上限、SkipExistingBlocks，
        /// 以及推荐搭配模组。中英双语各一行，方便工坊审核和玩家排查。
        /// </summary>
        private void WriteStartupLog()
        {
            try
            {
                string channelZh, channelEn;
                switch (Settings.CommunicationMode)
                {
                    case CommunicationMode.JsonPolling:
                        channelZh = $"JSON 文件轮询，间隔 {Settings.JsonPollingIntervalMs}ms，目录 {Settings.GetCommandFilePath()}";
                        channelEn = $"JSON file polling, {Settings.JsonPollingIntervalMs}ms, {Settings.GetCommandFilePath()}";
                        break;
                    case CommunicationMode.WebSocket:
                    default:
                        channelZh = $"WebSocket 服务端，监听 127.0.0.1:{Settings.WebSocketPort}（仅本机）";
                        channelEn = $"WebSocket server on 127.0.0.1:{Settings.WebSocketPort} (local only)";
                        break;
                }

                FileLogger.Info(Bilingual.Pair($"启动：通信模式 = {channelZh}", $"startup: transport = {channelEn}"));

                FileLogger.Info(Bilingual.Pair(
                    $"启动：热键 原点={ResolvedOriginKey} / 开始暂停={ResolvedStartPauseKey} / 撤销全部={ResolvedUndoAllKey}",
                    $"startup: hotkeys origin={ResolvedOriginKey} / start-pause={ResolvedStartPauseKey} / undo-all={ResolvedUndoAllKey}"));

                FileLogger.Info(Bilingual.Pair(
                    $"启动：安全上限 单次任务 {CommunicationManager.MaxBlocksPerTask} 方块 / 总上限 {SafetyGuard.MaxTotalBlocks}（0=不限） / 每帧最多 {ExecEngine.MaxBlocksPerFrame} 方块；" +
                    $"跳过已有方块 SkipExistingBlocks={SafetyGuard.SkipExistingBlocks}",
                    $"startup: safety limits per-task {CommunicationManager.MaxBlocksPerTask} blocks / total cap {SafetyGuard.MaxTotalBlocks} (0 = unlimited) / max {ExecEngine.MaxBlocksPerFrame} blocks per frame; " +
                    $"SkipExistingBlocks={SafetyGuard.SkipExistingBlocks}"));

                // 上架文案的推荐搭配，写死在启动日志里，玩家开日志即可看到
                FileLogger.Info(Bilingual.Pair(
                    "推荐搭配 HEROsMod（全亮 / 建造工具）与 Better Zoom（镜头缩放）：本模组只负责图片转方块建造，光照与镜头交给它们",
                    "Recommended companions: HEROsMod (fullbright / build tools) and Better Zoom (camera zoom). This mod only turns images into blocks; lighting and camera are theirs."));
            }
            catch (Exception ex)
            {
                FileLogger.Error(Bilingual.Pair($"启动日志写入失败：{ex.Message}", $"startup log failed: {ex.Message}"));
            }
        }

        public override void Unload()
        {
            FileLogger?.Info(Bilingual.Pair("TerrariaImageBuilder 模组卸载", "TerrariaImageBuilder unloading"));
            CommManager?.Dispose();

            OriginKeybind = null;
            StartPauseKeybind = null;
            UndoAllKeybind = null;

            Instance = null;
            Settings = null;
            CommManager = null;
            ExecEngine = null;
            SafetyGuard = null;
            UndoManager = null;
            FileLogger = null;
        }

        /// <summary>
        /// 把 config.json 里的字符串解析成按键。
        /// 支持 "O" / "o" / "Keys.O"（Windows.Forms 风格）与原版枚举名 "OemQuestion" 等。
        /// 任何非法值都回退默认，并写一行日志说明——绝不静默降级。
        /// </summary>
        private Keys ParseKey(string name, Keys fallback, string configKeyName)
        {
            if (string.IsNullOrWhiteSpace(name))
            {
                FileLogger?.Warn(Bilingual.Pair(
                    $"热键配置 {configKeyName} 为空，回退默认 {fallback}",
                    $"hotkey config {configKeyName} is empty, falling back to {fallback}"));
                return fallback;
            }

            string cleaned = name.Trim();
            if (cleaned.StartsWith("Keys.", StringComparison.OrdinalIgnoreCase))
                cleaned = cleaned.Substring("Keys.".Length).Trim();

            bool parsed = Enum.TryParse(cleaned, true, out Keys key);
            if (!parsed || key == Keys.None || !Enum.IsDefined(typeof(Keys), key))
            {
                FileLogger?.Warn(Bilingual.Pair(
                    $"热键配置 {configKeyName} 的键名 \"{name}\" 非法，回退默认 {fallback}（可用键名参考 System.Windows.Forms.Keys，例如 O / P / U / F5 / OemQuestion）",
                    $"hotkey config {configKeyName} has an invalid key name \"{name}\", falling back to {fallback} (see System.Windows.Forms.Keys, e.g. O / P / U / F5 / OemQuestion)"));
                return fallback;
            }

            if (!string.Equals(cleaned, name.Trim(), StringComparison.Ordinal))
                FileLogger?.Info(Bilingual.Pair(
                    $"热键配置 {configKeyName}：\"{name}\" 已归一化为 {key}",
                    $"hotkey config {configKeyName}: \"{name}\" normalized to {key}"));

            return key;
        }
    }

    public enum LogLevel
    {
        Debug,
        Info,
        Warn,
        Error
    }

    public enum TaskStatus
    {
        Idle,
        Running,
        Paused,
        Error
    }

    public enum CommunicationMode
    {
        WebSocket,
        JsonPolling
    }
}
