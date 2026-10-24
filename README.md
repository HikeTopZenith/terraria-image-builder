# Terraria Image Builder · 泰拉图像建造台

把一张图片，自动变成泰拉瑞亚里的方块像素画。

```
图片 → 外端建造台（桌面程序）→ 方块蓝图 → WebSocket → tModLoader 模组 → 游戏世界里长出来
```

两部分缺一不可：**模组**负责把方块真正放进世界，**桌面程序**负责把图片算成方块。
创意工坊只能分发 `.tmod`，所以桌面程序在这里另行提供。

---

## 下载

| 内容 | 位置 |
|---|---|
| 模组（.tmod） | 创意工坊订阅，或本项目 **Releases** 里的 `.tmod` |
| 桌面程序（免安装 exe） | 本项目 **Releases** 里的 `TerrariaImageBuilder-v*.zip` |
| 源码 | 本仓库 |

需要 **tModLoader 1.4.4+**（本模组 `MinimumApiVersion: 2026.4`）。

---

## 特性

- **CIEDE2000 色差匹配**：按人眼感知算色差，另保留传统 LAB 算法可切换
- **贴图级色库**：42 种原版方块的固有色直接从游戏贴图采样（剔除阴影与烘焙光照），
  不是手填的估算值；重建口径见 `docs/PROGRESS.md`
- **三项可选微调**（默认全关，关掉即逐像素还原旧行为）
  - 优先保色相：压掉「红变棕、蓝变紫」，人像/角色向
  - 纯色方块优先：大面积背景优先低纹理方块
  - Floyd–Steinberg 抖动：渐变区误差扩散，消除色块断层
- **自定义色卡**：顽固偏色区可手工指定「这个颜色 → 这个方块」，上限 50 条，随工程保存
- **离线预览**：不连游戏也能出结果，与蓝图共用同一套匹配入口
- **预处理**：拖拽裁剪、旋转、镜像、亮度对比度、自动缩放到目标方块数
- **工程包 `.tibp`**：蓝图与全部参数一起存，旧工程完全兼容
- **安全**：单次 20 万方块硬上限、坐标与方块 ID 全量校验、断线自动清队列、一键撤销

游戏内：`O` 标记原点 · `P` 开始/暂停 · `U` 撤销全部（均可在 `config.json` 改）

推荐搭配 **HEROsMod**（全亮/建造工具）与 **Better Zoom**（镜头缩放）——
本模组只放方块，光照和镜头交给它们。

---

## 仓库结构

```
mod/       tModLoader 模组源码（C#）—— 放 ModSources 里编译
desktop/   外端建造台（Python + tkinter）
  engine/   色彩学、匹配、蓝图、契约、预览、通信
  ui/       主窗口（三标签 + 色卡面板）
  tools/    开发期工具：XNB 解码、色库生成、发布自检
  tests/    测试（5 个文件，脚本式入口）
  vendor/   tkinterdnd2（拖拽，自包含）
docs/      交接文档、发布说明、配置说明、构建指南
```

> `desktop/assets/tiles/`（从游戏解出的贴图）**刻意不入库**：既涉及版权，
> 运行时也不需要（色值已固化进 `engine/palette_auto.py`）。

---

## 从源码运行

### 桌面程序

```bash
cd desktop
pip install -r requirements.txt
python ui/app.py
```

### 模组

把 `mod/` 整个目录放进 `Documents/My Games/Terraria/tModLoader/ModSources/`，然后：

```bash
export PATH="/path/to/.dotnet:$PATH"   # 让 MSBuild 用 .NET 8，别用 6.0
dotnet build TerrariaImageBuilder.csproj -c Release
```

详见 `docs/BUILD_GUIDE.md`（含一个容易踩的坑：不把 SDK 放进 PATH 会在打包阶段报
`MSB3073`，看起来像代码错误，其实是 MSBuild 调到了缺 .NET 8 的宿主）。

### 测试

```bash
cd desktop
PYTHONPATH=$PWD python tests/test_engine.py     # 以及 test_matcher_v2 / test_colorimetry / test_place_command / test_pipeline_v2
```

---

## 已知限制

1. **白/灰会比原图略暗（约 7 个 L\*）**。原版没有"亮中性且实心"的方块：雪与微光砖偏冷，
   珍珠沙是重力方块会塌方。旧版预览"白得正好"是靠游戏里不存在的颜色凑出来的，
   现在预览与游戏一致。
2. **"红变棕"** 是旧指标口径的假象：把"暖色相且 L<65"都判成棕，会让血肉块这种明显血红的方块被判成棕。
   按"被洗成哑棕"（暖色相且 C\*<25）算是 6.8%。
3. 少数不常见方块的英文名与 TileID 已用游戏自报的 `TileID.Search.GetName` 核对过（693 条），
   但中文显示名是人工翻译的。

---

## 许可

MIT（见 `LICENSE`）。第三方组件与贴图版权说明也写在 `LICENSE` 末尾。
