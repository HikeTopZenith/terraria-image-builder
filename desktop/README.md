# 泰拉瑞亚自动建筑系统 — 阶段3：外部桌面端图像处理与方块匹配核心引擎

本模块独立开发、可独立测试。不做窗口 UI，不做游戏内模组。
输出的方块蓝图 JSON 格式严格遵循统一通信契约（engine/contract.py），
后续与 tModLoader 1.4.4 模组端对接执行。

## 核心规则

- 1 像素对应 1 个泰拉瑞亚原版方块。
- 仅使用游戏原版自带方块，不新增任何自定义方块、贴图。
- 支持大图自动分块，单批次安全上限 256x256 方块。
- 外部程序负责图片解析、方块匹配、任务调度；模组端负责游戏内放置。
- 两端通过本地 WebSocket 或 JSON 文件通信，共用统一消息契约。

## 模块结构

```
terraria_builder/
├── engine/
│   ├── __init__.py      # 包导出（公共 API 入口）
│   ├── contract.py      # 统一通信契约：蓝图 JSON schema + 校验 + 示例
│   ├── blocks.py        # 泰拉瑞亚原版方块 RGB 色库（主色 + 纹理采样点）
│   ├── matcher.py       # CIELAB 方块匹配引擎（三模式/黑白名单/常用优先）
│   ├── input.py         # 图片输入与预处理（格式拒绝/ICC忽略/透明/缩放/裁剪）
│   ├── blueprint.py     # 大图分块与蓝图生成/导入导出
│   └── engine.py        # 顶层 facade：串联全流程 + 日志
└── tests/
    └── test_engine.py   # 自包含测试（合成图，无需外部图片）
```

## 各模块职责

### contract.py — 通信契约
- BlockEntry：单个方块（x, y, block_id, chunk_index）
- ChunkEntry：分块（chunk_index, offset_x/y, width, height, blocks）
- BlueprintMetadata：元数据（total_blocks, total_chunks, match_mode, processing_time_ms, source_image, config）
- Blueprint：完整蓝图，to_dict() / from_dict() 双向序列化
- validate_blueprint(bp)：校验契约一致性，返回错误列表
- example_blueprint()：返回最小示例 JSON
- MAX_CHUNK_DIM = 256：单批次安全上限
- SUPPORTED_IMAGE_EXTENSIONS / REJECTED_IMAGE_EXTENSIONS

### blocks.py — 方块色库
- BlockColor：block_id, 中文名, main_rgb, samples(纹理采样点), common(是否常用)
- BLOCKS：内置约 30 种原版方块
- BASIC_BLOCK_IDS：极简模式基础子集
- build_block_library()：返回 block_id -> BlockColor 映射
- get_common_block_ids()：常用方块列表

### matcher.py — 匹配引擎
- rgb_to_lab(rgb)：sRGB -> CIELAB（D65 白点，标准色彩科学）
- lab_distance(lab1, lab2)：加权欧氏距离（近似 Delta E）
- MatchConfig：匹配模式、容差、纹理权重、常用优先开关、黑白名单
- BlockMatcher：match_pixel(rgb) 匹配单像素；match_image(pixels) 匹配整图
- 三档模式：
  - precise（纯色精准）：仅比对主色，容差低，适合像素图/图标
  - natural（自然过渡）：主色 + 纹理采样点加权综合，适合照片/写实图
  - minimal（极简）：强制仅用基础子集，牺牲色彩换统一感

### input.py — 图片输入与预处理
- load_image(path)：读取图片，拒绝 webp/heic/gif/psd，忽略 ICC，提取透明通道
- apply_preprocess(arr, alpha_mask, cfg)：裁剪/旋转/镜像/亮度对比度/缩放
- PreprocessConfig：所有预处理参数
- preprocess_image(path, cfg)：便捷入口
- 文件 >5MB 输出提示；所有预处理不修改原文件

### blueprint.py — 分块与蓝图
- BlueprintEngine.grid_to_blueprint(grid, alpha_mask, ...)：方块网格 -> 分块蓝图
- split_image_into_chunks(arr, chunk_size)：图像切分（供逐块匹配）
- save_blueprint(bp, path) / load_blueprint(path)：蓝图导入导出

### engine.py — 顶层 facade
- EngineConfig：全部可配置参数
- BuildEngine：build_from_file(path) 图片 -> BuildResult；build_from_array(arr, mask) 数组 -> BuildResult
- BuildResult：blueprint, processing_time_ms, total_blocks, total_chunks, matched_grid, warnings
- 基础运行日志：匹配耗时、方块总数、分块数量

## 接口格式说明

### 蓝图 JSON 结构

```json
{
  "protocol": "terraria_builder",
  "version": "1.0",
  "width": 300,
  "height": 200,
  "chunk_size": 256,
  "chunks": [
    {
      "chunk_index": 0,
      "offset_x": 0,
      "offset_y": 0,
      "width": 256,
      "height": 256,
      "blocks": [
        { "x": 0, "y": 0, "block_id": "Dirt", "chunk_index": 0 }
      ]
    }
  ],
  "metadata": {
    "total_blocks": 60000,
    "total_chunks": 4,
    "match_mode": "precise",
    "processing_time_ms": 1.5,
    "created_at": "2025-...",
    "source_image": { "path": "...", "width": 300, "height": 200, "size_bytes": 12345, "format": "PNG" },
    "config": { "mode": "precise", "tolerance": 40.0 }
  }
}
```

### 关键约定
- block_id：英文标识符（如 Dirt, Stone, Wood），与模组端方块映射对齐
- 坐标：x/y 为分块内相对坐标；chunk_index 标识所属分块
- chunk_size 默认 256，单分块不超过 256x256
- 透明像素不生成方块（不出现在 blocks 列表中）

## 基础测试方法

测试全部自包含，自动合成测试图，无需外部图片。

```bash
cd terraria_builder
python tests/test_engine.py
```

覆盖用例：
1. test_contract_roundtrip — 蓝图契约序列化往返
2. test_supported_and_rejected_extensions — 格式支持/拒绝
3. test_reject_webp — webp 拒绝提示
4. test_transparency_skipped — 透明通道跳过
5. test_preprocess_crop_and_scale — 裁剪 + 缩放
6. test_preprocess_rotate_flip — 旋转 + 镜像
7. test_full_pipeline_small — 全流程（4x4 合成图）
8. test_three_modes — 三种匹配模式切换
9. test_blacklist_whitelist — 黑白名单
10. test_common_priority — 常用方块优先
11. test_chunking_large_image — 大图分块
12. test_chunk_256_limit — 256 上限校验
13. test_blueprint_save_load — 蓝图导入导出
14. test_large_file_warning — 大文件提示

## 运行示例

```python
from engine.engine import BuildEngine, EngineConfig

eng = BuildEngine(EngineConfig(match_mode='precise', chunk_size=256))
result = eng.build_from_file('path/to/image.png')
print(result.total_blocks, result.total_chunks)
eng.save_blueprint(result.blueprint, 'blueprint.json')
```

## 依赖

- Python 3.8+
- numpy
- Pillow (PIL)

## 后续对接

模组端（tModLoader 1.4.4）按同一契约解析 JSON 蓝图：
- 读取 chunks[].blocks，按 chunk_index 分批放置
- 将 block_id 映射到游戏内方块类型
- 1 像素 -> 1 方块，按相对坐标放置

---

# 阶段4：外部桌面程序（UI 与对接层）

## 启动方式

双击项目根目录的 **启动外部建造台.bat**，或：

```
python ui/app.py
```

需要带 numpy / Pillow 的 Python（脚本默认指向 ComfyUI 的 venv）。

## 新增模块

| 文件 | 职责 |
|---|---|
| `engine/preview.py` | 离线方块预览渲染（不连游戏即可出图）+ 蓝图统计 |
| `engine/client.py` | 模组端 WebSocket 客户端，asyncio 跑后台线程，UI 不阻塞 |
| `ui/app.py` | tkinter 主窗口，三栏布局 + 状态回显 |

## 第一梯队（已实现）

- 文件选择 / 剪贴板导入（PNG·JPG·JPEG；webp/heic/gif/psd 拒绝并提示）
- 原图预览 + 离线方块预览（并排，均分宽度）
- 三档匹配模式切换（纯色精准 / 自然过渡 / 极简）
- 连接游戏 / 断开
- 原点 X/Y 设置
- 开始建造 / 终止
- 实时进度条 + `0 / N` 计数 + 分块进度 + 方块统计（总数/种类/分块/明细）

## 已修复的 UI 问题

1. **控制面板被挤成 1×1**：`pack(expand=True)` 的兄弟会把后 pack 的固定宽度面板压没；
   改为 `grid` 三列（1:1:固定 320）。
2. **高 DPI 下界面发虚**：启动时调用 `SetProcessDpiAwareness(1)`，150%/200% 缩放下清晰且坐标正确。
3. `current_path` 未初始化：未导入图片直接点「生成预览」会 AttributeError。

## 与模组端的契约（实机验证过的口径）

- 指令：`blueprint_to_chunked_command()` 生成 `Chunks` 数组指令，坐标已换算为全局坐标；
- 校验和：**递归**键排序 + 紧凑 JSON + SHA-256 小写十六进制；
- 0 语义：`max_total_blocks=0` 表示不限制；
- 指令自带 `OriginX/OriginY`，模组端会静默采用（不必单独发 SetOrigin）。

## 待办（第二 / 第三梯队）

- 第二梯队：裁剪/旋转/亮度对比度工具条、黑白名单面板、4 套参数预设、蓝图导入导出
- 第三梯队：暂停/继续/断点续传、最近导入历史、方块模拟预览的高级渲染

## 第二梯队（已实现）

### 预处理工具条（右侧「预处理」标签页）
- 顺时针 / 逆时针 90° 旋转（累积角度）
- 水平 / 垂直镜像
- 亮度 / 对比度滑块（0.3 ~ 1.8，实时预览）
- **拖拽裁剪**：在左侧原图上按住左键框选，松开即裁剪
- 撤销裁剪 / 重置全部预处理

### 方块黑白名单（右侧「方块」标签页）
- 列表展示全部方块，标注 [常用] / [透明] / [重力]
- 多选后「禁用选中」加入黑名单（默认模式）
- 勾选「白名单模式」后，「启用选中」表示只允许这些方块
- 清空限制
- 黑白名单与匹配模式一起在「生成预览」时生效

### 修复
- `engine._match_array` 曾把 RGBA 的 4 个通道整体传给只接受 RGB 的 `rgb_to_lab`，
  导致 UI 走 `build_from_array` 时崩溃（`ValueError: too many values to unpack`）。
  已改为只取前 3 通道。

## 待办（第三梯队）
- 暂停/继续/断点续传
- 最近导入历史
- 方块模拟预览的高级渲染
- 4 套参数预设、蓝图导入导出（用户要求稍后）

## 拖拽导入（2026-10-23）

- 依赖 `tkinterdnd2`，已 vendor 到项目内 `terraria_builder/vendor/`（自包含，不污染 ComfyUI 环境）
- 整个窗口 + 两个画布都接受拖入；支持带空格路径、多文件、非法格式提示
- 找不到 tkinterdnd2 时自动降级（仍可用按钮/剪贴板导入，状态栏会提示）

## 参数预设（四套）

| 预设 | 匹配模式 | 容差 | 特点 |
|---|---|---|---|
| 像素画精准 | precise | 20 | 严格比对主色，忠于原图，不偏向常用方块 |
| 建筑简化 | minimal | 45 | 仅基础方块，块数少、建造快 |
| 生存可用 | natural | 40 | 白名单限定 14 种易获取方块 |
| 透明优先 | natural | 35 | 允许 Glass/Ice 参与匹配（窗户/水族效果） |

定义在 `engine/presets.py`，每套含匹配参数 + 黑白名单 + 推荐预处理参数。

## 工程包（.tibp）

导出内容 = **蓝图 + 全部可复现参数**：

```json
{
  "format": "terraria_builder_project",
  "version": "1.0",
  "settings": {
    "match_mode": "natural", "tolerance": 35.0, "texture_weight": 0.45,
    "common_priority": false, "blacklist": [], "whitelist": null,
    "allow_falling": false, "allow_transparent": true,
    "preset": "透明优先",
    "preprocess": { "rotate_quarters": 0, "flip_h": false, "flip_v": false,
                    "brightness": 1.05, "contrast": 1.0 },
    "origin": { "x": 0, "y": 0 }
  },
  "source": { "path": "...", "width": 96, "height": 64 },
  "blueprint": { ... 完整分块蓝图 ... }
}
```

导入后：参数自动还原到控件，蓝图直接进入可建造状态（无需重新匹配）。
也兼容导入裸蓝图 JSON（只有 chunks 字段）。


---

# 迭代2：色差升级 / 贴图色库 / 三项处理开关 / 自定义色卡

> 所有新增功能都是**可选开关，默认全关**；关掉时逐像素还原旧行为。
> WebSocket 协议与蓝图 JSON 格式**零改动**，旧工程文件可直接用。

## 新增模块

| 文件 | 职责 |
|---|---|
| `engine/colorimetry.py` | CIEDE2000（标量 + (m,n) 矩阵）+ Δh'/色相角工具；旧的加权欧氏距离保留为 `lab` 模式 |
| `engine/palette_auto.py` | 【生成物】从原版贴图采样的无光照固有色（solid_rgb）+ 纹理方差 + 纯色度 |
| `tools/xnb_lzx.py` | 纯 Python LZX 解码器（已用 makecab 造柜逐字节验证） |
| `tools/xnb_extract.py` | `Tiles_<id>.xnb` → PNG（754 个，含 `--ids`/`--limit`/`--force`） |
| `tools/build_palette.py` | 贴图 → `palette_auto.py`（实心帧口径 + 降采样一致律自检） |

## 匹配设置（UI「方块」页顶部，单行紧凑 + 悬浮提示）

| 控件 | 作用 |
|---|---|
| 颜色算法下拉 | CIEDE2000（默认，人眼更贴合）/ 传统 LAB（旧算法） |
| ☐ 优先保色相 | 压蓝→紫、红→棕；源侧彩度门保证近中性像素不受影响 |
| ☐ 纯色方块优先 | 按贴图方差给低纹理纯色方块折扣（硬化沙 > 灰砖 > 石 > 草） |
| ☐ Floyd-Steinberg 抖动 | 渐变区误差扩散，消除色块断层；与匹配模式无关（量化只比方块本色） |
| 自定义色卡… | 打开色卡面板（见下） |

## 自定义色卡

- 【启用自定义色卡优先】默认关闭；开启后优先读映射表，未命中才走自动匹配
- 从**当前预览图**（预处理之后）点选颜色，强制只取 RGB、alpha 记 255
- 绑定任意原版方块；支持新增 / 删除选中 / 一键清空；满 50 条拒绝新增并提示
- 绑定方块若在黑名单（或不在白名单）里，列表打 ⚠ 标记；引擎侧自动跳过该条回退自动匹配
- 随 `.tibp` 保存/加载；旧工程缺这些字段时全部走默认值，不报错

## .tibp 新增字段（缺省即默认，向前兼容）

    delta_algorithm     "ciede2000" | "lab"
    hue_priority        false
    prefer_pure         false
    dither              false
    use_custom_palette  false
    custom_palette      []   # [{"rgb":[r,g,b],"block_id":"Stone"}]，上限 50

## 贴图色库的口径（重要，别改回去）

1. **帧集合**：只取边界 8 点全不透明的「实心整块」帧，并剔除「异材质混入」的过渡帧（石块被泥土包围那类，靠边环与核心的色度差判定）
2. **基准色**：`solid_rgb` = 实心帧内部像素逐帧均值取**中位数**（中位数自动丢掉离群暖帧/暗帧，对烘焙光照不敏感）
3. **不要用亮带均值**：那会把整个色库系统性调亮，出现"预览对、游戏暗"
4. **自检**：降采样一致律（代表帧按固定规则选、不挑最接近的）并如实报帧间极差
5. 管线只从 `build_block_library(use_texture_palette=True)` 这一处取色；`False` 可回旧硬编码库做 A/B

## 已知限制

- 少数不常见方块（珍珠木 / 暗影木 / 宝石类）的英文名与原版 TileID 未逐条核验。
  颜色取自 `Tiles_<id>`，**实际放置的方块与该贴图一致**，只是显示名可能不符。
- 原版色库缺「亮中性」与「高饱和红」，因此白/灰会略暗（约 9 个 L*）、饱和红会偏砖红。
  这是色域问题，不是匹配算法问题。


## 色库扩充（42 块）

用游戏自报的 `TileID.Search.GetName` 对照表核对过全部命名（表存于 `assets/tile_names.json`，693 条），
现有 35 块的名字与 TileID **全部正确**。随后从 763 张贴图里数据驱动补了 7 个原版实心方块：

| TileID | 方块 | 测量色 | 用途 |
|---|---|---|---|
| 667 | 微光砖 ShimmerBrick | (191,212,214) L*83 | 补亮中性（白） |
| 195 | 血肉块 FleshBlock | (147,31,43) C*53 | 补高饱和红 |
| 153 | 红灰泥 RedStucco | (221,130,116) L*64 C*41 | 中亮红 |
| 312 | 红王朝瓦 RedDynastyShingles | (152,62,55) C*44 | 中暗红 |
| 76 | 狱石砖 HellstoneBrick | (110,44,40) | 暗暖红 |
| 417 | 星云块 LunarBlockNebula | (129,89,189) C*60 | 高饱和紫 |
| 677 | 远古蓝砖 AncientBlueBrick | (77,79,148) | 深蓝紫 |

### 两条别踩的坑

1. **"红→棕"指标不能只看色相与亮度**：老口径把"暖色相且 L<65"都判成棕，于是
   FleshBlock(147,31,43) 这种一眼血红的方块也被算成棕。正确判据是"被洗成哑棕"= 暖色相且 **C*<25**。
2. **白永远无法做到与源图同等亮度**：原版没有"亮中性且实心"的方块 —— 雪与微光砖都偏冷，
   珍珠沙(206,192,196) 中性但是**重力方块**会塌方。旧版那个"白得正好"的观感，是靠
   Snow(240,245,250)、珍珠木(220,235,245) 这类**游戏里不存在的颜色**在预览里凑出来的。
   现在预览与游戏一致，代价是灰白整体暗约 7 个 L*。
