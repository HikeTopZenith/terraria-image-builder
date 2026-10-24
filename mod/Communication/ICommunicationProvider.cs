using System;

namespace TerrariaImageBuilder.Communication
{
    /// <summary>
    /// 通信提供者接口。WebSocket 与 JSON 轮询均实现此接口。
    /// 提供者只负责收发原始字符串，消息解析、校验与业务分发统一由
    /// CommunicationManager 完成，保证两种通信模式的契约完全一致。
    /// </summary>
    public interface ICommunicationProvider : IDisposable
    {
        void Initialize();
        void Update();

        /// <summary>收到一条原始 JSON 指令文本</summary>
        event Action<string> OnRawCommandReceived;
        event Action OnConnected;
        event Action OnDisconnected;
        event Action<string> OnError;

        /// <summary>发送一条原始 JSON 状态文本</summary>
        void SendMessage(string json);
    }
}
