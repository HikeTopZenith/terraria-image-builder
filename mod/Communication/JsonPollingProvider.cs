using System;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace TerrariaImageBuilder.Communication
{
    /// <summary>
    /// 本地 JSON 文件轮询提供者。
    /// 外部程序写指令文件，模组按间隔读取并执行，状态写回状态文件。
    /// 不占端口，杀毒软件拦截风险极低。只负责收发原始 JSON 文本。
    /// </summary>
    public class JsonPollingProvider : ICommunicationProvider
    {
        private readonly ModLogger _logger;
        private readonly ModConfig _config;
        private readonly object _fileLock = new object();
        private volatile bool _running = false;
        private int _pollIntervalMs;
        private string _commandFilePath;
        private string _statusFilePath;
        private long _lastPollTimestamp = 0;
        private long _lastCommandTimestamp = -1;
        private string _lastCommandChecksum = "";

        // 断线检测：JSON 模式没有连接概念，用「状态文件连续写失败」和「工作目录消失」作为对端失联证据。
        // 阈值故意放宽，避免杀毒软件短暂锁文件被误判成断线而清掉正常任务。
        private const int WriteFailureLimit = 5;
        private int _consecutiveWriteFailures = 0;
        private volatile bool _disconnectedRaised = false;

        public event Action<string> OnRawCommandReceived;
        public event Action OnConnected;
        public event Action OnDisconnected;
        public event Action<string> OnError;

        public JsonPollingProvider(ModLogger logger, ModConfig config)
        {
            _logger = logger;
            _config = config;
        }

        public void Initialize()
        {
            try
            {
                _pollIntervalMs = Math.Max(50, _config.JsonPollingIntervalMs);
                _commandFilePath = _config.GetCommandFilePath();
                _statusFilePath = _config.GetStatusFilePath();
                _running = true;

                var dir = Path.GetDirectoryName(_commandFilePath);
                if (!string.IsNullOrEmpty(dir) && !Directory.Exists(dir))
                    Directory.CreateDirectory(dir);

                if (File.Exists(_commandFilePath)) File.Delete(_commandFilePath);
                if (File.Exists(_statusFilePath)) File.Delete(_statusFilePath);

                _logger.Info($"JSON 轮询已启动，间隔 {_pollIntervalMs}ms");
                OnConnected?.Invoke();
            }
            catch (Exception ex)
            {
                _logger.Error($"JSON 轮询初始化失败：{ex.Message}");
                OnError?.Invoke(ex.Message);
            }
        }

        /// <summary>
        /// 由主循环每帧调用，内部按时间戳节流，不做阻塞式 Sleep。
        /// </summary>
        public void Update()
        {
            if (!_running) return;

            long now = Stopwatch.GetTimestamp();
            long elapsedMs = (now - _lastPollTimestamp) * 1000L / Stopwatch.Frequency;
            if (elapsedMs < _pollIntervalMs) return;
            _lastPollTimestamp = now;

            try
            {
                PollCommandFile();
                CheckPeerAlive();
            }
            catch (Exception ex) { _logger.Warn($"JSON 轮询异常：{ex.Message} | polling error"); }
        }

        /// <summary>
        /// JSON 模式的对端存活探测：指令目录被删 / 无法访问时，判定为对端已失联。
        /// 触发后由 CommunicationManager 清空未完成队列（只报一次）。
        /// </summary>
        private void CheckPeerAlive()
        {
            if (_disconnectedRaised) return;

            string dir = Path.GetDirectoryName(_commandFilePath);
            bool dirOk = string.IsNullOrEmpty(dir) || Directory.Exists(dir);
            if (dirOk) return;

            _disconnectedRaised = true;
            _logger.Warn($"JSON 轮询目录已不可用：{dir}，判定通信断开 | polling directory missing, treating as disconnected");
            OnDisconnected?.Invoke();
        }

        private void PollCommandFile()
        {
            if (!File.Exists(_commandFilePath)) return;

            string json;
            lock (_fileLock)
            {
                try { json = File.ReadAllText(_commandFilePath, Encoding.UTF8); }
                catch { return; }
            }

            if (string.IsNullOrWhiteSpace(json)) return;

            try
            {
                if (JsonNode.Parse(json) is not JsonObject obj) return;
                if (!obj.ContainsKey("Type") || !obj.ContainsKey("Timestamp"))
                {
                    _logger.Warn("JSON 指令缺少必需字段，跳过本轮");
                    return;
                }

                string checksum = obj["Checksum"]?.GetValue<string>() ?? "";
                long timestamp = obj["Timestamp"]?.GetValue<long>() ?? 0;

                if (timestamp == _lastCommandTimestamp && checksum == _lastCommandChecksum)
                    return;

                _lastCommandTimestamp = timestamp;
                _lastCommandChecksum = checksum;
                // 收到新指令即视为对端恢复
                _disconnectedRaised = false;
                _consecutiveWriteFailures = 0;
                OnRawCommandReceived?.Invoke(json);
            }
            catch (Exception ex)
            {
                _logger.Warn($"JSON 指令解析失败（可能半写），跳过本轮：{ex.Message}");
            }
        }

        public void SendMessage(string json)
        {
            if (string.IsNullOrEmpty(json)) return;
            try
            {
                lock (_fileLock)
                    File.WriteAllText(_statusFilePath, json, new UTF8Encoding(false));

                _consecutiveWriteFailures = 0;
            }
            catch (Exception ex)
            {
                _consecutiveWriteFailures++;
                _logger.Warn($"写入 JSON 状态文件失败（连续第 {_consecutiveWriteFailures} 次）：{ex.Message} | status write failed");

                if (_consecutiveWriteFailures >= WriteFailureLimit && !_disconnectedRaised)
                {
                    _disconnectedRaised = true;
                    _logger.Warn("状态文件连续写入失败，判定通信断开 | repeated status write failures, treating as disconnected");
                    OnDisconnected?.Invoke();
                }
            }
        }

        public void Dispose()
        {
            _running = false;
            // 卸载即通道消失，同样要让上层清队列（幂等，上层只处理一次）
            OnDisconnected?.Invoke();
        }
    }
}
