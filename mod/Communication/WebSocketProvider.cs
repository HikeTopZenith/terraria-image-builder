using System;
using System.Collections.Generic;
using System.Net;
using System.Net.WebSockets;
using System.Text;
using System.Threading;
using System.Threading.Tasks;

namespace TerrariaImageBuilder.Communication
{
    /// <summary>
    /// 本地 WebSocket 服务端提供者。
    /// 监听 127.0.0.1 固定端口，与外部桌面程序建立双向长连接。
    /// 仅负责收发原始 JSON 文本，不解析业务内容；不访问外网。
    /// </summary>
    public class WebSocketProvider : ICommunicationProvider
    {
        private readonly ModLogger _logger;
        private readonly int _port;
        private HttpListener _listener;
        private readonly List<System.Net.WebSockets.WebSocket> _clients = new List<System.Net.WebSockets.WebSocket>();
        private readonly object _clientsLock = new object();
        private readonly SemaphoreSlim _sendLock = new SemaphoreSlim(1, 1);
        private volatile bool _running = false;
        private CancellationTokenSource _cts;

        public event Action<string> OnRawCommandReceived;
        public event Action OnConnected;
        public event Action OnDisconnected;
        public event Action<string> OnError;

        public WebSocketProvider(ModLogger logger, int port)
        {
            _logger = logger;
            _port = port;
        }

        public void Initialize()
        {
            try
            {
                if (!HttpListener.IsSupported)
                {
                    _logger.Error("系统不支持 HttpListener，WebSocket 通信不可用");
                    OnError?.Invoke("系统不支持 HttpListener");
                    return;
                }

                _cts = new CancellationTokenSource();
                _listener = new HttpListener();
                _listener.Prefixes.Add($"http://127.0.0.1:{_port}/");
                _listener.Start();
                _running = true;

                _ = Task.Run(() => AcceptLoop(_cts.Token));
                _logger.Info($"WebSocket 服务端已启动，监听 127.0.0.1:{_port}");
            }
            catch (Exception ex)
            {
                _logger.Error($"WebSocket 初始化失败：{ex.Message}");
                OnError?.Invoke(ex.Message);
            }
        }

        private async Task AcceptLoop(CancellationToken token)
        {
            while (_running && !token.IsCancellationRequested)
            {
                try
                {
                    var context = await _listener.GetContextAsync();
                    if (context.Request.IsWebSocketRequest)
                    {
                        var wsContext = await context.AcceptWebSocketAsync(null);
                        lock (_clientsLock) _clients.Add(wsContext.WebSocket);
                        _logger.Info("WebSocket 客户端已连接");
                        OnConnected?.Invoke();
                        _ = Task.Run(() => ReceiveLoop(wsContext.WebSocket, token));
                    }
                    else
                    {
                        context.Response.StatusCode = 400;
                        context.Response.Close();
                    }
                }
                catch (Exception ex)
                {
                    if (_running && !token.IsCancellationRequested)
                        _logger.Warn($"WebSocket 接受连接异常：{ex.Message}");
                }
            }
        }

        private async Task ReceiveLoop(System.Net.WebSockets.WebSocket ws, CancellationToken token)
        {
            var buffer = new byte[8192];
            var messageBuffer = new List<byte>();

            try
            {
                while (_running && ws.State == WebSocketState.Open && !token.IsCancellationRequested)
                {
                    var result = await ws.ReceiveAsync(new ArraySegment<byte>(buffer), token);
                    if (result.MessageType == WebSocketMessageType.Close)
                        break;

                    messageBuffer.AddRange(new ArraySegment<byte>(buffer, 0, result.Count));
                    if (!result.EndOfMessage) continue;

                    var json = Encoding.UTF8.GetString(messageBuffer.ToArray());
                    messageBuffer.Clear();

                    if (!string.IsNullOrWhiteSpace(json))
                        OnRawCommandReceived?.Invoke(json);
                }
            }
            catch (Exception ex)
            {
                if (_running)
                    _logger.Warn($"WebSocket 接收异常：{ex.Message}");
            }
            finally
            {
                lock (_clientsLock) _clients.Remove(ws);
                ws.Dispose();
                _logger.Info("WebSocket 客户端已断开");
                OnDisconnected?.Invoke();
            }
        }

        /// <summary>
        /// 向所有已连接客户端推送状态消息。
        /// </summary>
        public void SendMessage(string json)
        {
            if (string.IsNullOrEmpty(json)) return;

            System.Net.WebSockets.WebSocket[] targets;
            lock (_clientsLock) targets = _clients.ToArray();
            if (targets.Length == 0) return;

            var bytes = Encoding.UTF8.GetBytes(json);

            _sendLock.Wait();
            try
            {
                foreach (var ws in targets)
                {
                    if (ws.State != WebSocketState.Open) continue;
                    try
                    {
                        ws.SendAsync(new ArraySegment<byte>(bytes), WebSocketMessageType.Text, true, CancellationToken.None)
                          .GetAwaiter().GetResult();
                    }
                    catch (Exception ex)
                    {
                        _logger.Warn($"WebSocket 发送状态失败：{ex.Message}");
                    }
                }
            }
            finally
            {
                _sendLock.Release();
            }
        }

        public void Update()
        {
            // WebSocket 为事件驱动，无需轮询
        }

        public void Dispose()
        {
            _running = false;
            try { _cts?.Cancel(); } catch { }
            try { _listener?.Stop(); } catch { }
            try { _listener?.Close(); } catch { }

            bool hadClients;
            lock (_clientsLock)
            {
                hadClients = _clients.Count > 0;
                foreach (var ws in _clients)
                {
                    try { ws.Dispose(); } catch { }
                }
                _clients.Clear();
            }

            // 卸载即通道消失：让上层清掉未完成队列（上层幂等，只处理一次）
            if (hadClients) OnDisconnected?.Invoke();

            _sendLock.Dispose();
        }
    }
}
