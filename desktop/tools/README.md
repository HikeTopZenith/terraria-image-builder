# tools/ — 原版贴图解码与配色采样

只依赖标准库 + numpy + Pillow，不引入任何 pip 依赖。

## 1. Terraria .xnb 容器布局（实测结论）

对 `D:/Steam/steamapps/common/Terraria/Content/Images/Tiles_*.xnb`（763 个）全量核对，
每个文件的头 14 字节都是同一结构：

| 偏移 | 内容 | 实测值（Tiles_1） |
|---|---|---|
| 0..2 | 魔数 | `58 4e 42` = `XNB` |
| 3 | 平台标识 | `77` = `w`（Windows） |
| 4 | 容器版本 | `05` |
| 5 | 标志位 | `80` = LZX 压缩 |
| 6..9 | u32 文件总字节数（含 10 字节头） | `5862`，恰等于文件真实长度 |
| 10..13 | u32 解压后字节数 | `311217` |
| 14.. | 帧序列 | 见下 |

帧序列（与 FNA / MonoGame 的 XNB 读取口径一致）：

- 普通帧：2 字节**大端**压缩块长度 + 该长度的 LZX 数据，解压出 `0x8000` 字节；
- 转义帧：首字节 `0xFF` + 2 字节大端**解压**长度 + 2 字节大端**压缩**长度，共 5 字节头。

关键判据（为什么能确定是这套布局，而不是别的变体）：

1. 起点 `14` 直接按 LZX 读会得到非法块类型；按「2 字节头 + LZX 起点 16」读，
   位流首 4 位解出 `intel=0, blocktype=2(ALIGNED)`，随后 24 位块长 = `311217`，
   **与 offset 10 的解压长度完全相等**（该巧合概率约 2^-24）；
2. 按此布局从 offset 14 逐帧串联，每帧「压缩长度」都恰好落到下一帧的帧头，
   10 帧解压总量 = `311217` = offset 10 的字段值；
3. 解压结果首字节 `01` = 读取器数 1，随后 7bit 长度 `94 01` = 148，正好是
   `Microsoft.Xna.Framework.Content.Texture2DReader, Microsoft.Xna.Framework.Graphics, Version=4.0.0.0, Culture=neutral, PublicKeyToken=842cf8be1de50553`；
4. 再往下 `int32 格式 / 宽 / 高 / mip 数 / 每级数据长度` 与像素字节数、payload
   总长三方同时自洽（288x270x4 = 311040，177 + 311040 = 311217）。

payload 布局：`7bit 读取器数` → 每项 `7bit 长度 + UTF-8 类型名` → `7bit 共享资源数`
→ **5 字节固定前缀 `00 00 00 00 01`**（实测 5 个不同 tile 完全一致）→ Texture2D 头。
该前缀用途未查明，但语义上不影响：解析后会用「mip 数据长度 == 宽x高x字节每像素」与
「payload 总长恰好耗尽」两条硬约束校验，不符即报错退出。

## 2. LZX 位流约定（容易踩的坑）

位流按**小端 16 位字**取数、**字内 MSB 优先**：`word = (byte1 << 8) | byte0`，
首个位是 `byte1` 的最高位。这与 libmspack `readbits.h` 的 `BITS_ORDER_MSB` +
`READ_BYTES` 一致。把它当成「按字节大端」直接读，会在第一个块头上就失败。

解码器正确性用 Windows `makecab` 生成的 LZX 样本做了逐字节比对：
把 36911 字节输入压成 cabinet，按 CFDATA 取流后解码，输出与原文件**完全一致**
（window 16 与 21 两种都一致）。这一步把「解码器有 bug」和「容器布局猜错」彻底分开。

## 3. 用法

```bash
python tools/xnb_extract.py                       # 全量导出到 assets/tiles/
python tools/xnb_extract.py --ids 1,2,53          # 只导指定 tile
python tools/xnb_extract.py --limit 10 --force    # 前 10 个并覆盖已有
python tools/xnb_extract.py --out D:/tmp/tiles    # 换输出目录（幂等，默认跳过已存在）

python tools/build_palette.py                     # 生成 engine/palette_auto.py
python tools/build_palette.py --report            # 附全量对照表
```

解码失败一律抛错退出，消息带文件名、偏移、期望值与实际值，例如：
`解码失败 Tiles_13.xnb：偏移 17 处期望 可解码的 LZX 数据，实际 LZX 流启用了 E8 变换…`

## 4. 贴图布局与配色口径

- 贴图尺寸是 18px 单元格：16px 精灵在格左上，右侧与下侧各 2px 透明 padding
  （288x270 = 16 列 x 15 行）。实测只有 (0,0) 偏移能让单元格 8 个边界点全不透明。
- **实心整块帧**：16x16 精灵的 4 角 + 4 条边中点全部 alpha=255 且非纯黑。斜坡、
  半砖、接缝帧会被筛掉。识别到的总数记为 `frames`。
- **混入帧剔除**：方块片里混有「石块被泥土包围」这类过渡帧——一帧内同时存在中性灰
  石块区与暖棕异材质区（实测 Stone 的 86 个实心帧里有 56 个）。判据为「边环（外 2px）
  与核心（8x8）的 Lab 色度差 ΔC >= 5」；砖块的砂浆、木纹属**同色相**明暗差，实测 Brick
  被剔除的帧 dh（色相差）全部 <= 10，因此不会被误判成异材质。剩余不足 3 帧则整片回退。
  保留下来的帧数记为 `frames_clean`。
- `solid_rgb`：clean 帧逐帧取 16x16 精灵**内部** 14x14 的像素均值（剔除自带 1px
  暗描边），再对帧集合逐通道取中位数。Stone = `[118,118,118]`（L* 49.6）、
  Sand = `[178,162,80]`、Brick = `[136,82,74]`、Grass = `[43,144,67]`。
- `solid_rgb_band`：旧的「L* 最亮 30% 均值」口径，仅保留供 A/B 对照，管线不读。
  它系统性偏亮（Stone 156 / L* 62），与游戏观感不符。
- `avg_rgb / variance / pure_score`：整张片口径，仅保留向后兼容。

**降采样一致律自检**（不能用「挑最接近的那帧」否则是循环论证）：代表帧规则写死为
「clean 帧按帧内 L* 中位数排名取正中间那一帧」，平铺 10x10 后 BOX 降采样成 1 像素，
与 `solid_rgb` 比较；同时报出该方块逐帧降采样值的极差。
实测 17/35 通过 <=3，极差中位数约 11、最大 67（Grass）——大面积观感与单帧的差异来自
方块片自带的多视觉变体，不是估计量能消除的。

## 5. 本机环境注意

本会话里 Python 子进程的写盘被限制在 `D:/deepseek`，写不进 `D:\文档\...` 项目目录
（bash 可以）。产物是先用 `--out` 落到 `D:/deepseek/_tmp` 再复制进项目的；
在本机普通终端里直接跑则不需要这一步。

