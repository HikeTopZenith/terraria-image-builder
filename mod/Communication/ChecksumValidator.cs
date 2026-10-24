using System;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using System.Text.Encodings.Web;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace TerrariaImageBuilder.Communication
{
    /// <summary>
    /// 指令校验和校验器。
    ///
    /// 规则（与外部端严格一致）：
    ///   1. 取收到的原始指令 JSON；
    ///   2. 移除 Checksum 字段；
    ///   3. **递归**对所有对象做键名 Ordinal 升序排序（数组保持元素顺序）；
    ///   4. 输出紧凑 JSON（无空格、无多余转义，非 ASCII 不转义）；
    ///   5. UTF-8 编码后计算 SHA-256，转小写十六进制。
    ///
    /// 递归排序是必须的：外部端用 Python json.dumps(sort_keys=True) 会递归排序，
    /// 若这里只排顶层，嵌套的 blocks 字段顺序差异会让两端哈希对不上。
    /// </summary>
    public static class ChecksumValidator
    {
        private static readonly JsonSerializerOptions CompactOptions = new JsonSerializerOptions
        {
            WriteIndented = false,
            Encoder = JavaScriptEncoder.UnsafeRelaxedJsonEscaping
        };

        public static string ComputeChecksum(string json)
        {
            try
            {
                if (JsonNode.Parse(json) is not JsonObject root) return null;

                root.Remove("Checksum");

                var canonicalNode = SortRecursive(root);
                var canonical = canonicalNode.ToJsonString(CompactOptions);
                var bytes = Encoding.UTF8.GetBytes(canonical);

                using var sha = SHA256.Create();
                var hash = sha.ComputeHash(bytes);
                return BitConverter.ToString(hash).Replace("-", "").ToLowerInvariant();
            }
            catch
            {
                return null;
            }
        }

        /// <summary>
        /// 递归按键名排序；数组保持原顺序，仅对元素内部继续排序。
        /// </summary>
        private static JsonNode SortRecursive(JsonNode node)
        {
            switch (node)
            {
                case JsonObject obj:
                {
                    var sorted = new JsonObject();
                    foreach (var kv in obj.OrderBy(k => k.Key, StringComparer.Ordinal))
                        sorted[kv.Key] = SortRecursive(kv.Value);
                    return sorted;
                }
                case JsonArray arr:
                {
                    var copy = new JsonArray();
                    foreach (var item in arr)
                        copy.Add(SortRecursive(item));
                    return copy;
                }
                case null:
                    return null;
                default:
                    return node.DeepClone();
            }
        }

        public static bool VerifyChecksum(string json, string expectedChecksum)
        {
            if (string.IsNullOrWhiteSpace(expectedChecksum)) return false;
            var actual = ComputeChecksum(json);
            return actual != null && string.Equals(actual, expectedChecksum, StringComparison.OrdinalIgnoreCase);
        }
    }
}
