# 迭代2 · 计划与决策记录

> 由调度会话维护。目标：修复偏色 + 补全图像处理 + 自定义色卡 + 上架前加固。
> 不变量：WebSocket 协议与蓝图 JSON 格式**零改动**；旧工程/旧蓝图可直接用。

## 一、已确认决策（用户确认）

| # | 决策 | 结论 |
|---|---|---|
| 1 | 贴图来源 | 自写纯 Python LZX/XNB 解码器，离线一次性抽原版贴图，生成无光照基准色库（同时提供纯色优先所需的纹理方差） |
| 2 | 交付节奏 | 外端 + 模组端本轮一次做完；.tmod 构建由调度方执行，**构建前需用户关闭游戏** |
| 3 | 验收素材 | 骷髅测试图 = `D:/桌面/7ae18658f121ec3058c65a7564e750af.jpg` |

## 二、运行中假设（不阻塞，若被推翻请叫我）

1. **色差算法作为独立维度**：新增 `delta_algorithm`（"ciede2000" | "lab"），与既有三档模式（precise/natural/minimal）正交，默认 "ciede2000"，旧 "lab" 随时可选。不新造匹配模式。
2. **新开关默认全关**，关掉时逐像素还原改动前行为；4 套预设的参数与默认行为**不动**（不悄悄改掉用户已习惯的手感）。
3. **自定义色卡只影响匹配**，输出始终是原版 TileID；协议、蓝图、模组端无需任何改动。
4. `.tibp` 只新增字段，缺失时走默认值（向前兼容旧工程）。

## 三、新增配置项（外端 .tibp / MatchConfig）

    delta_algorithm     "ciede2000" | "lab"        默认 ciede2000
    hue_priority        bool                       【优先保色相】默认 false
    prefer_pure         bool                       【纯色方块优先】默认 false
    dither              bool                       【Floyd-Steinberg 抖动】默认 false
    use_custom_palette  bool                       【启用自定义色卡优先】默认 false
    custom_palette      [{"rgb":[r,g,b],"block_id":"Stone"}]   上限 50 条，默认 []

## 四、palette_auto 数据契约（D1 产出 / D2 消费）

    engine/palette_auto.py
      PALETTE: dict[str, dict]   # key = blocks.py 的 block_id
      # {"tile_id": int, "avg_rgb": [r,g,b], "samples": [[r,g,b],...],
      #  "variance": float, "pure_score": float}
      get_palette() -> dict
      get_entry(block_id) -> dict | None
      coverage() -> float

## 五、任务分解与归属

| 波次 | 任务 | 归属 | 交付边界 |
|---|---|---|---|
| 1 | D1 XNB 解码 + 贴图色库 | 子代理 | tools/、assets/tiles/、engine/palette_auto.py |
| 1 | D2 CIEDE2000 + 三开关 + 色卡 | 子代理 | colorimetry.py、matcher.py、tests/ |
| 1 | D3 模组端加固 | 子代理 | 模组端 *.cs / mod.hjson / Localization / CONFIG.md |
| 2 | D4 契约兼容 + 预览/蓝图同源 | 子代理 | contract.py、presets.py、preview.py、engine.py、__init__.py |
| 2 | D5 UI 三开关 + 算法下拉 + 色卡面板 | 子代理 | ui/app.py |
| 3 | 构建 .tmod、全量回归、验收、文档 | 调度方 | 产物与文档 |

## 六、验收口径

1. 偏色：骷髅图蓝底不偏紫、红血肉不偏棕、白骨骷髅不发灰（用 Lab 量化 + 预览图肉眼双证）
2. 色卡：开启可手动修正顽固色区；关闭完全恢复；不与黑白名单冲突；旧工程加载正常
3. 模组端：O/P/U 正常，建造/暂停/撤销/限速/仅空气模式正常
4. 兼容：旧工程、旧蓝图可加载建造；与 HEROsMod / Better Zoom 无冲突
5. 稳定性：上万方块不卡死；断线不闪退；异常输入不破坏存档
