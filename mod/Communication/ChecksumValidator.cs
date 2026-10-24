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

        /// <summary>
        /// 黄金向量自检：模组加载时跑一次，确参与外部端（Python）的字节级口径没漂移。
        ///
        /// 输入故意用「键乱序 + 带空格缩进」的原始 JSON，这样一次就能覆盖
        /// 解析 → 递归排序 → 紧凑序列化 → UTF-8 → SHA-256 整条链路。
        /// 预期哈希必须与 tests/test_checksum_golden.py 里的 GOLDEN_CHECKSUM 一致。
        /// </summary>
        public static bool SelfCheck(out string detail)
        {
            const string messyJson = @"{
  ""Timestamp"": 1792718267209,
  ""OriginY"": 430,
  ""Chunks"": [
    { ""Blocks"": [
        { ""Y"": 0, ""TileId"": 0, ""X"": 0 },
        { ""X"": 1, ""TileId"": 1, ""Y"": 0 },
        { ""TileId"": 53, ""Y"": 0, ""X"": 2 }
      ],
      ""DelayMs"": 200,
      ""Index"": 0
    }
  ],
  ""Type"": ""PlaceBlocks"",
  ""MaxTotalBlocks"": 0,
  ""TotalBlocks"": 3,
  ""DelayWithinChunkMs"": 0,
  ""ChunkCount"": 1,
  ""SkipExistingBlocks"": false,
  ""OriginX"": 4200,
  ""DelayBetweenChunksMs"": 200
}";
            const string expected = "99facb66a94fdec4a304aba7023da66ff455e9607432dd8450716f5705e464ac";

            string actual = ComputeChecksum(messyJson);
            bool ok = string.Equals(actual, expected, StringComparison.OrdinalIgnoreCase);
            detail = ok
                ? "校验和黄金向量自检通过"
                : $"校验和黄金向量自检失败：期望 {expected}，实际 {actual ?? "(null)"}";
            return ok;
        }

        public static bool VerifyChecksum(string json, string expectedChecksum)
        {
            if (string.IsNullOrWhiteSpace(expectedChecksum)) return false;
            var actual = ComputeChecksum(json);
            return actual != null && string.Equals(actual, expectedChecksum, StringComparison.OrdinalIgnoreCase);
        }
    }
}
