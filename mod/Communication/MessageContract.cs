using System;
using System.Collections.Generic;
using System.Text.Encodings.Web;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace TerrariaImageBuilder.Communication
{
    /// <summary>
    /// 外部程序与模组之间的消息契约。两端必须严格遵守此结构。
    /// 使用 BCL 内置 System.Text.Json，不引入任何第三方依赖。
    /// </summary>

    public enum CommandType
    {
        SetOrigin,
        StartTask,
        PauseTask,
        ResumeTask,
        StopTask,
        PlaceBlocks,
        UndoAll,
        UndoLastChunk,
        GetPlayerPos   // 查询玩家当前所在方块坐标（UI 用来自动定位原点）
    }

    /// <summary>单个方块放置指令</summary>
    public class BlockCommand
    {
        public int X { get; set; }
        public int Y { get; set; }
        public int TileId { get; set; }
    }

    /// <summary>
    /// 分块结构：新版 PlaceBlocks 指令中的单个分块。
    /// 同时兼容 Index / ChunkIndex 两种字段命名。
    /// </summary>
    public class ChunkCommand
    {
        public int Index { get; set; }
        public int? ChunkIndex { get; set; }
        public int DelayMs { get; set; }
        public List<BlockCommand> Blocks { get; set; }

        [JsonIgnore]
        public int EffectiveIndex => ChunkIndex ?? Index;
    }

    /// <summary>
    /// 入站指令：支持新版分块结构，同时保持对旧版单批次字段的向下兼容。
    /// Type 同时接受字符串（"PlaceBlocks"）与数字枚举值。
    /// </summary>
    public class IncomingCommand
    {
        [JsonConverter(typeof(JsonStringEnumConverter))]
        public CommandType Type { get; set; }

        // 旧版单批次字段
        public int? ChunkIndex { get; set; }
        public int? DelayBetweenChunksMs { get; set; }
        public int? DelayWithinChunkMs { get; set; }
        public List<BlockCommand> Blocks { get; set; }

        // 新版分块字段
        public int? ChunkCount { get; set; }
        public int? TotalBlocks { get; set; }
        public List<ChunkCommand> Chunks { get; set; }

        public int? OriginX { get; set; }
        public int? OriginY { get; set; }
        public int? MaxTotalBlocks { get; set; }
        public bool? SkipExistingBlocks { get; set; }
        public long Timestamp { get; set; }
        public string Checksum { get; set; }
    }

    /// <summary>状态消息基类：所有状态消息均包含 MsgType 与 Timestamp</summary>
    public abstract class OutgoingStatusBase
    {
        public string MsgType { get; set; }
        public long Timestamp { get; set; }
    }

    /// <summary>① InstructionReceived：指令接收校验通过</summary>
    public class InstructionReceivedStatus : OutgoingStatusBase
    {
        public int TotalBlocks { get; set; }
        public int ChunkCount { get; set; }
    }

    /// <summary>② ChunkProgress：单个分块执行完成</summary>
    public class ChunkProgressStatus : OutgoingStatusBase
    {
        public int ChunkIndex { get; set; }
        public int CompletedBlocks { get; set; }
    }

    /// <summary>③ AllCompleted：全部分块执行完毕</summary>
    public class AllCompletedStatus : OutgoingStatusBase
    {
        public int TotalCompletedBlocks { get; set; }
        public long TotalCostMs { get; set; }
    }

    /// <summary>⑤ PlayerPosition：回传玩家当前所在方块坐标</summary>
    public class PlayerPosStatus : OutgoingStatusBase
    {
        public int X { get; set; }
        public int Y { get; set; }
        /// <summary>该列地表高度（第一个实心方块所在 y），UI 用它对地表对齐。</summary>
        public int SurfaceY { get; set; }
    }

    /// <summary>
    /// ServerHello：外部端连上时，模组主动报一次身份。
    ///
    /// 目的：让「模组版本」和「协议版本」对使用者可见。两边是分开发布的
    /// （模组走创意工坊、桌面端走 Releases），版本对不上时必须能一眼看出来，
    /// 否则使用者只会看到「连上了但建造没反应」这种哑谜。
    /// 纯新增字段，不改动既有消息，老外部端不认识这个 MsgType 时会被忽略。
    /// </summary>
    public class ServerHelloStatus : OutgoingStatusBase
    {
        /// <summary>模组版本（与 mod.hjson 的 Version 一致）。</summary>
        public string ModVersion { get; set; }
        /// <summary>通信协议版本：外部端与模组都认这个数字，不一致就提示更新。</summary>
        public int ProtocolVersion { get; set; }
        /// <summary>桌面端下载地址（模组元数据里那一份）。</summary>
        public string DesktopDownloadUrl { get; set; }
    }

    /// <summary>④ ExecutionError：执行异常</summary>
    public class ExecutionErrorStatus : OutgoingStatusBase
    {
        public string ErrorMessage { get; set; }
        public int? ErrorChunkIndex { get; set; }
    }

    public static class MessageSerializer
    {
        private static readonly JsonSerializerOptions Options = new JsonSerializerOptions
        {
            PropertyNameCaseInsensitive = true,
            DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull,
            WriteIndented = false,
            Encoder = JavaScriptEncoder.UnsafeRelaxedJsonEscaping
        };

        public static string Serialize<T>(T obj) => JsonSerializer.Serialize(obj, Options);
        public static T Deserialize<T>(string json) => JsonSerializer.Deserialize<T>(json, Options);
    }
}
