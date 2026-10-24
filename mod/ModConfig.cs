using System;
using System.IO;
using System.Text.Json;

namespace TerrariaImageBuilder
{
    /// <summary>
    /// 模组配置。
    /// 定位：本模组只负责「把外部端算好的方块队列放进世界」，
    /// 光照 / 飞行 / 刷怪 / 清除等由 HEROsMod 等工具负责，这里不再重复实现。
    /// </summary>
    public class ModConfig
    {
        // 通信
        public CommunicationMode CommunicationMode { get; set; } = CommunicationMode.WebSocket;
        public int WebSocketPort { get; set; } = 8765;
        public int JsonPollingIntervalMs { get; set; } = 300;
        public string JsonCommandFileName { get; set; } = "TIB_command.json";
        public string JsonStatusFileName { get; set; } = "TIB_status.json";

        // 安全
        public bool SkipExistingBlocks { get; set; } = false;
        public int MaxTotalBlocks { get; set; } = 10000;

        // 执行
        // 放置速度档位：1=稳（4块/帧） 2=中（12块/帧） 3=快（24块/帧） 4=极限（32块/帧）
        public int PlacementSpeedLevel { get; set; } = 4;

        // 热键
        public string HotkeySetOrigin { get; set; } = "O";
        public string HotkeyStartPause { get; set; } = "P";
        public string HotkeyUndoAll { get; set; } = "U";

        private static readonly string ConfigDir = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments),
            "My Games", "Terraria", "ModLoader", "TerrariaImageBuilder");

        private static readonly string ConfigPath = Path.Combine(ConfigDir, "config.json");

        public void Load()
        {
            try
            {
                if (!Directory.Exists(ConfigDir))
                    Directory.CreateDirectory(ConfigDir);

                if (File.Exists(ConfigPath))
                {
                    var json = File.ReadAllText(ConfigPath);
                    var loaded = JsonSerializer.Deserialize<ModConfig>(json);
                    if (loaded != null)
                    {
                        CommunicationMode = loaded.CommunicationMode;
                        WebSocketPort = loaded.WebSocketPort;
                        JsonPollingIntervalMs = loaded.JsonPollingIntervalMs;
                        JsonCommandFileName = loaded.JsonCommandFileName;
                        JsonStatusFileName = loaded.JsonStatusFileName;
                        SkipExistingBlocks = loaded.SkipExistingBlocks;
                        MaxTotalBlocks = loaded.MaxTotalBlocks;
                        PlacementSpeedLevel = loaded.PlacementSpeedLevel;
                        HotkeySetOrigin = loaded.HotkeySetOrigin;
                        HotkeyStartPause = loaded.HotkeyStartPause;
                        HotkeyUndoAll = loaded.HotkeyUndoAll;
                    }
                }
            }
            catch (Exception ex)
            {
                // 配置加载失败使用默认值，绝不崩溃；但要留痕，别让用户以为配置生效了
                TerrariaImageBuilder.Instance?.FileLogger?.Warn(Bilingual.Pair(
                    $"config.json 读取失败，本次全部使用默认值：{ex.Message}",
                    $"failed to read config.json, using defaults this session: {ex.Message}"));
            }
        }

        /// <summary>
        /// 首次启动时把默认配置落盘，玩家才有文件可改（热键配置化必须让人找得到文件）。
        /// 已存在就绝不覆盖，避免把玩家手改的内容冲掉。
        /// </summary>
        public void EnsureConfigFileExists()
        {
            try
            {
                if (File.Exists(ConfigPath)) return;
                Save();
                TerrariaImageBuilder.Instance?.FileLogger?.Info(Bilingual.Pair(
                    $"已生成默认配置文件：{ConfigPath}",
                    $"default config file created: {ConfigPath}"));
            }
            catch
            {
                // 生成失败不影响运行
            }
        }

        public void Save()
        {
            try
            {
                var dir = Path.GetDirectoryName(ConfigPath);
                if (!string.IsNullOrEmpty(dir) && !Directory.Exists(dir))
                    Directory.CreateDirectory(dir);
                File.WriteAllText(ConfigPath, JsonSerializer.Serialize(this, new JsonSerializerOptions { WriteIndented = true }));
            }
            catch
            {
                // 保存失败不影响游戏
            }
        }

        public string GetCommandFilePath() => Path.Combine(ConfigDir, JsonCommandFileName);
        public string GetStatusFilePath() => Path.Combine(ConfigDir, JsonStatusFileName);
    }
}
