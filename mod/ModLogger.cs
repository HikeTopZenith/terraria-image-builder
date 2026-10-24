using System;
using System.IO;
using System.Text;

namespace TerrariaImageBuilder
{
    /// <summary>
    /// 本地日志系统，按天写入日志文件。所有日志操作捕获异常，绝不导致游戏崩溃。
    /// </summary>
    public class ModLogger
    {
        private static readonly string LogDir = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments),
            "My Games", "Terraria", "ModLoader", "Logs", "TerrariaImageBuilder");

        private readonly object _lock = new object();

        public void Log(LogLevel level, string message)
        {
            try
            {
                if (!Directory.Exists(LogDir))
                    Directory.CreateDirectory(LogDir);

                var date = DateTime.Now.ToString("yyyy-MM-dd");
                var logPath = Path.Combine(LogDir, $"TerrariaImageBuilder_{date}.log");
                var timestamp = DateTime.Now.ToString("HH:mm:ss.fff");
                var line = $"[{timestamp}] [{level}] {message}";

                lock (_lock)
                {
                    File.AppendAllText(logPath, line + Environment.NewLine, Encoding.UTF8);
                }
            }
            catch
            {
                // 日志写入失败绝不影响游戏
            }
        }

        public void Debug(string msg) => Log(LogLevel.Debug, msg);
        public void Info(string msg) => Log(LogLevel.Info, msg);
        public void Warn(string msg) => Log(LogLevel.Warn, msg);
        public void Error(string msg) => Log(LogLevel.Error, msg);
    }
}
