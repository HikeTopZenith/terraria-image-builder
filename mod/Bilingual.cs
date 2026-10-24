using System;
using Microsoft.Xna.Framework;
using Terraria;

namespace TerrariaImageBuilder
{
    /// <summary>
    /// 玩家可见文案的中英双语助手。
    ///
    /// 约定：
    ///   - 回传外端的协议字段（ErrorMessage）用 Pair：一条字符串，中文 / English；
    ///   - 游戏内聊天提示用 ToPlayer：两行分开，先中文后英文，阅读更清楚。
    ///
    /// 绝不输出底层堆栈：异常一律只取 Message 的业务化描述。
    /// </summary>
    public static class Bilingual
    {
        /// <summary>中文 / English 合并成一条字符串，用于协议字段或日志。</summary>
        public static string Pair(string zh, string en)
        {
            if (string.IsNullOrEmpty(en)) return zh ?? string.Empty;
            if (string.IsNullOrEmpty(zh)) return en;
            return zh + " / " + en;
        }

        // 断线等事件发生在收发线程上，直接写聊天栏有线程安全风险：
        // 这些线程只入队，由主线程的驱动循环统一 FlushPending 输出。
        private static readonly System.Collections.Concurrent.ConcurrentQueue<(string zh, string en, Color color)> _pending
            = new System.Collections.Concurrent.ConcurrentQueue<(string zh, string en, Color color)>();

        /// <summary>线程安全的玩家提示入队（非主线程调用这个）。</summary>
        public static void EnqueueToPlayer(string zh, string en, Color? color = null)
        {
            try { _pending.Enqueue((zh, en, color ?? new Color(180, 220, 255))); }
            catch { }
        }

        /// <summary>在主线程把排队的玩家提示真正发出去。</summary>
        public static void FlushPending()
        {
            try
            {
                while (_pending.TryDequeue(out var msg))
                    ToPlayer(msg.zh, msg.en, msg.color);
            }
            catch { }
        }

        /// <summary>游戏内聊天提示：中文一行、英文一行。独立服务器/无玩家时静默跳过。必须在主线程调用。</summary>
        public static void ToPlayer(string zh, string en, Color? color = null)
        {
            try
            {
                if (Main.dedServ) return;
                var c = color ?? new Color(180, 220, 255);
                if (!string.IsNullOrEmpty(zh)) Main.NewText(zh, c);
                if (!string.IsNullOrEmpty(en)) Main.NewText(en, c);
            }
            catch
            {
                // 聊天输出失败绝不影响建造
            }
        }

        /// <summary>
        /// 日志里记录异常时统一用这个：只留业务描述，不带堆栈。
        /// </summary>
        public static string Safe(Exception ex, string zhFallback, string enFallback)
        {
            if (ex == null) return Pair(zhFallback, enFallback);
            string msg = ex.Message;
            if (string.IsNullOrWhiteSpace(msg)) msg = ex.GetType().Name;
            return Pair(zhFallback + "：" + msg, enFallback + ": " + msg);
        }
    }
}
