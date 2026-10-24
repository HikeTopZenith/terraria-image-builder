"""自动生成：Terraria 原版贴图采样出的无光照基准固有色 + 纹理方差。

由 tools/build_palette.py 生成，请勿手工编辑。
数据来源：原版贴图 Tiles_<tile_id>.xnb 解码后的 PNG（无光照干扰，纯像素统计）。

口径：
- solid_rgb：实心整块帧（4 角 + 4 边中点全不透明）逐帧取 16x16 精灵均值，
  剔除「边缘换材质」的混入帧后逐通道取中位数。这是匹配应使用的固有色。
- solid_variance / solid_pure_score：同一批帧内部的 Lab 距离标准差与其归一化纯色度。
- solid_rgb_band：同一批帧的「L* 最亮 30% 均值」，仅用于 A/B 对照，管线不读。
- avg_rgb / variance / pure_score：整张片口径，保留仅为向后兼容。
- frames 为识别到的实心帧总数，frames_clean 为剔除混入帧后实际参与统计的帧数。
- samples：实心干净帧内沿 L* 的 4 个代表点（最暗 / 中间偏暗 / 中间偏亮 / 最亮）。

字段：tile_id / solid_rgb / solid_variance / solid_pure_score / solid_rgb_band /
      frames / frames_clean / avg_rgb / samples / variance / pure_score。
"""

from __future__ import annotations

import numpy as np

PALETTE: dict[str, dict] = {
    "Dirt": {"tile_id": 0, "solid_rgb": [147, 104, 76], "solid_variance": 5.82, "solid_pure_score": 0.508, "solid_rgb_band": [155, 110, 80],
     "frames": 80, "frames_clean": 43, "avg_rgb": [144, 104, 77], "samples": [[114, 81, 56], [151, 107, 75], [151, 107, 75], [169, 125, 93]], "variance": 10.22, "pure_score": 0.37},
    "Stone": {"tile_id": 1, "solid_rgb": [118, 118, 118], "solid_variance": 9.93, "solid_pure_score": 0.377, "solid_rgb_band": [156, 156, 156],
     "frames": 86, "frames_clean": 30, "avg_rgb": [109, 104, 101], "samples": [[64, 64, 64], [64, 64, 64], [128, 128, 128], [203, 203, 203]], "variance": 9.76, "pure_score": 0.381},
    "Wood": {"tile_id": 30, "solid_rgb": [139, 101, 73], "solid_variance": 6.63, "solid_pure_score": 0.475, "solid_rgb_band": [179, 133, 101],
     "frames": 119, "frames_clean": 103, "avg_rgb": [134, 97, 71], "samples": [[41, 30, 21], [120, 85, 60], [151, 107, 75], [191, 143, 111]], "variance": 8.39, "pure_score": 0.417},
    "WoodPlank": {"tile_id": 30, "solid_rgb": [139, 101, 73], "solid_variance": 6.63, "solid_pure_score": 0.475, "solid_rgb_band": [179, 133, 101],
     "frames": 119, "frames_clean": 103, "avg_rgb": [134, 97, 71], "samples": [[41, 30, 21], [120, 85, 60], [151, 107, 75], [191, 143, 111]], "variance": 8.39, "pure_score": 0.417},
    "StoneBrick": {"tile_id": 38, "solid_rgb": [108, 107, 106], "solid_variance": 8.65, "solid_pure_score": 0.409, "solid_rgb_band": [155, 155, 155],
     "frames": 144, "frames_clean": 59, "avg_rgb": [107, 103, 99], "samples": [[23, 23, 23], [114, 81, 56], [120, 120, 120], [175, 175, 175]], "variance": 8.64, "pure_score": 0.41},
    "Sand": {"tile_id": 53, "solid_rgb": [178, 162, 80], "solid_variance": 6.45, "solid_pure_score": 0.482, "solid_rgb_band": [191, 173, 88],
     "frames": 78, "frames_clean": 27, "avg_rgb": [172, 155, 84], "samples": [[139, 131, 59], [186, 168, 84], [186, 168, 84], [211, 198, 111]], "variance": 11.11, "pure_score": 0.351},
    "Grass": {"tile_id": 2, "solid_rgb": [43, 144, 67], "solid_variance": 14.79, "solid_pure_score": 0.289, "solid_rgb_band": [29, 179, 82],
     "frames": 106, "frames_clean": 12, "avg_rgb": [55, 135, 66], "samples": [[3, 32, 11], [151, 107, 75], [30, 150, 72], [28, 216, 94]], "variance": 14.89, "pure_score": 0.287},
    "Brick": {"tile_id": 39, "solid_rgb": [136, 82, 74], "solid_variance": 6.74, "solid_pure_score": 0.471, "solid_rgb_band": [190, 128, 113],
     "frames": 144, "frames_clean": 67, "avg_rgb": [134, 82, 73], "samples": [[43, 28, 26], [134, 60, 48], [181, 62, 59], [188, 152, 152]], "variance": 7.16, "pure_score": 0.456},
    "Ash": {"tile_id": 57, "solid_rgb": [72, 69, 80], "solid_variance": 7.37, "solid_pure_score": 0.449, "solid_rgb_band": [84, 79, 87],
     "frames": 80, "frames_clean": 80, "avg_rgb": [81, 74, 84], "samples": [[50, 50, 70], [59, 59, 69], [68, 68, 76], [128, 128, 128]], "variance": 6.32, "pure_score": 0.487},
    "Copper": {"tile_id": 7, "solid_rgb": [155, 93, 52], "solid_variance": 8.36, "solid_pure_score": 0.418, "solid_rgb_band": [223, 149, 91],
     "frames": 86, "frames_clean": 25, "avg_rgb": [144, 83, 46], "samples": [[78, 51, 38], [113, 57, 31], [183, 88, 25], [255, 229, 183]], "variance": 8.08, "pure_score": 0.426},
    "Iron": {"tile_id": 6, "solid_rgb": [125, 111, 102], "solid_variance": 11.82, "solid_pure_score": 0.337, "solid_rgb_band": [191, 168, 153],
     "frames": 86, "frames_clean": 40, "avg_rgb": [124, 105, 95], "samples": [[52, 31, 31], [87, 60, 60], [140, 101, 80], [234, 230, 226]], "variance": 11.48, "pure_score": 0.343},
    "Silver": {"tile_id": 9, "solid_rgb": [119, 128, 131], "solid_variance": 13.19, "solid_pure_score": 0.313, "solid_rgb_band": [205, 212, 213],
     "frames": 86, "frames_clean": 30, "avg_rgb": [119, 123, 121], "samples": [[26, 36, 40], [84, 93, 96], [171, 182, 183], [246, 249, 250]], "variance": 12.8, "pure_score": 0.319},
    "Gold": {"tile_id": 8, "solid_rgb": [161, 142, 55], "solid_variance": 11.98, "solid_pure_score": 0.334, "solid_rgb_band": [219, 203, 95],
     "frames": 86, "frames_clean": 18, "avg_rgb": [155, 135, 58], "samples": [[78, 77, 54], [122, 92, 10], [185, 164, 23], [255, 249, 183]], "variance": 10.89, "pure_score": 0.355},
    "Diamond": {"tile_id": 68, "solid_rgb": [96, 114, 116], "solid_variance": 12.59, "solid_pure_score": 0.323, "solid_rgb_band": [139, 170, 172],
     "frames": 86, "frames_clean": 10, "avg_rgb": [94, 104, 104], "samples": [[23, 23, 23], [46, 70, 84], [128, 128, 128], [182, 244, 243]], "variance": 12.28, "pure_score": 0.328},
    "Glass": {"tile_id": 54, "solid_rgb": [63, 91, 103], "solid_variance": 14.47, "solid_pure_score": 0.293, "solid_rgb_band": [132, 187, 204],
     "frames": 3, "frames_clean": 3, "avg_rgb": [70, 103, 120], "samples": [[0, 0, 0], [13, 26, 43], [62, 121, 148], [200, 246, 254]], "variance": 14.85, "pure_score": 0.288},
    "Clay": {"tile_id": 40, "solid_rgb": [141, 83, 70], "solid_variance": 4.31, "solid_pure_score": 0.582, "solid_rgb_band": [148, 84, 71],
     "frames": 78, "frames_clean": 43, "avg_rgb": [140, 83, 71], "samples": [[111, 73, 69], [146, 81, 68], [146, 81, 68], [163, 105, 97]], "variance": 7.52, "pure_score": 0.444},
    "WoodEbon": {"tile_id": 157, "solid_rgb": [104, 98, 120], "solid_variance": 6.98, "solid_pure_score": 0.462, "solid_rgb_band": [138, 126, 153],
     "frames": 109, "frames_clean": 36, "avg_rgb": [103, 93, 110], "samples": [[29, 28, 36], [83, 80, 100], [125, 117, 143], [153, 137, 165]], "variance": 8.57, "pure_score": 0.412},
    "WoodPearl": {"tile_id": 159, "solid_rgb": [75, 66, 51], "solid_variance": 10.74, "solid_pure_score": 0.358, "solid_rgb_band": [124, 110, 84],
     "frames": 144, "frames_clean": 76, "avg_rgb": [85, 75, 57], "samples": [[30, 27, 21], [51, 47, 37], [87, 80, 63], [192, 176, 138]], "variance": 10.25, "pure_score": 0.369},
    "WoodShade": {"tile_id": 208, "solid_rgb": [76, 89, 100], "solid_variance": 6.59, "solid_pure_score": 0.477, "solid_rgb_band": [94, 111, 123],
     "frames": 132, "frames_clean": 29, "avg_rgb": [82, 89, 93], "samples": [[24, 28, 31], [73, 86, 97], [88, 105, 118], [104, 121, 134]], "variance": 8.25, "pure_score": 0.421},
    "Snow": {"tile_id": 147, "solid_rgb": [180, 214, 232], "solid_variance": 8.74, "solid_pure_score": 0.407, "solid_rgb_band": [198, 226, 235],
     "frames": 78, "frames_clean": 26, "avg_rgb": [174, 193, 204], "samples": [[128, 180, 226], [190, 223, 232], [190, 223, 232], [227, 242, 247]], "variance": 15.47, "pure_score": 0.279},
    "Ice": {"tile_id": 161, "solid_rgb": [127, 187, 244], "solid_variance": 13.25, "solid_pure_score": 0.312, "solid_rgb_band": [197, 226, 251],
     "frames": 86, "frames_clean": 24, "avg_rgb": [123, 180, 240], "samples": [[40, 105, 240], [96, 160, 232], [176, 210, 255], [244, 254, 255]], "variance": 17.5, "pure_score": 0.255},
    "Hellstone": {"tile_id": 58, "solid_rgb": [113, 51, 46], "solid_variance": 14.18, "solid_pure_score": 0.297, "solid_rgb_band": [175, 85, 74],
     "frames": 86, "frames_clean": 5, "avg_rgb": [105, 47, 46], "samples": [[58, 30, 20], [78, 36, 36], [102, 34, 34], [243, 185, 147]], "variance": 14.63, "pure_score": 0.291},
    "Meteorite": {"tile_id": 37, "solid_rgb": [115, 81, 82], "solid_variance": 9.19, "solid_pure_score": 0.395, "solid_rgb_band": [173, 127, 113],
     "frames": 86, "frames_clean": 44, "avg_rgb": [107, 75, 74], "samples": [[39, 15, 29], [90, 46, 64], [147, 104, 87], [228, 162, 174]], "variance": 7.87, "pure_score": 0.433},
    "Lihzahrd": {"tile_id": 226, "solid_rgb": [95, 43, 26], "solid_variance": 12.09, "solid_pure_score": 0.332, "solid_rgb_band": [147, 70, 12],
     "frames": 142, "frames_clean": 142, "avg_rgb": [91, 42, 26], "samples": [[19, 7, 24], [85, 28, 31], [134, 56, 13], [177, 88, 8]], "variance": 12.18, "pure_score": 0.33},
    "Mushroom": {"tile_id": 190, "solid_rgb": [173, 162, 124], "solid_variance": 10.24, "solid_pure_score": 0.369, "solid_rgb_band": [190, 182, 141],
     "frames": 144, "frames_clean": 71, "avg_rgb": [148, 138, 114], "samples": [[44, 40, 28], [150, 143, 110], [182, 175, 130], [210, 199, 170]], "variance": 19.28, "pure_score": 0.237},
    "LivingWood": {"tile_id": 191, "solid_rgb": [145, 105, 76], "solid_variance": 7.78, "solid_pure_score": 0.435, "solid_rgb_band": [178, 130, 98],
     "frames": 138, "frames_clean": 34, "avg_rgb": [117, 92, 64], "samples": [[41, 30, 21], [137, 98, 68], [158, 115, 84], [190, 142, 110]], "variance": 10.3, "pure_score": 0.368},
    "PalmWood": {"tile_id": 322, "solid_rgb": [142, 110, 66], "solid_variance": 7.68, "solid_pure_score": 0.439, "solid_rgb_band": [185, 149, 91],
     "frames": 156, "frames_clean": 51, "avg_rgb": [133, 102, 62], "samples": [[98, 74, 43], [117, 89, 51], [143, 109, 63], [198, 170, 104]], "variance": 10.06, "pure_score": 0.374},
    "Obsidian": {"tile_id": 56, "solid_rgb": [60, 57, 99], "solid_variance": 10.81, "solid_pure_score": 0.357, "solid_rgb_band": [90, 86, 154],
     "frames": 144, "frames_clean": 26, "avg_rgb": [64, 58, 88], "samples": [[15, 14, 34], [43, 40, 84], [74, 71, 127], [126, 128, 196]], "variance": 10.48, "pure_score": 0.364},
    "Mud": {"tile_id": 59, "solid_rgb": [90, 70, 74], "solid_variance": 6.11, "solid_pure_score": 0.495, "solid_rgb_band": [98, 74, 78],
     "frames": 78, "frames_clean": 53, "avg_rgb": [90, 69, 74], "samples": [[73, 57, 63], [92, 68, 73], [92, 68, 73], [135, 101, 108]], "variance": 6.34, "pure_score": 0.486},
    "Sandstone": {"tile_id": 396, "solid_rgb": [189, 117, 72], "solid_variance": 5.45, "solid_pure_score": 0.524, "solid_rgb_band": [204, 135, 82],
     "frames": 78, "frames_clean": 38, "avg_rgb": [181, 114, 69], "samples": [[128, 80, 44], [179, 103, 65], [198, 124, 78], [212, 148, 88]], "variance": 6.62, "pure_score": 0.475},
    "HardenedSand": {"tile_id": 397, "solid_rgb": [193, 150, 83], "solid_variance": 6.75, "solid_pure_score": 0.471, "solid_rgb_band": [213, 195, 105],
     "frames": 78, "frames_clean": 37, "avg_rgb": [185, 152, 83], "samples": [[111, 97, 39], [172, 125, 66], [212, 148, 88], [223, 219, 147]], "variance": 5.86, "pure_score": 0.506},
    "Ebonstone": {"tile_id": 25, "solid_rgb": [95, 79, 111], "solid_variance": 6.75, "solid_pure_score": 0.471, "solid_rgb_band": [104, 97, 133],
     "frames": 84, "frames_clean": 27, "avg_rgb": [90, 72, 94], "samples": [[62, 45, 75], [98, 75, 107], [94, 87, 122], [151, 107, 75]], "variance": 10.31, "pure_score": 0.368},
    "Pearlstone": {"tile_id": 117, "solid_rgb": [132, 117, 136], "solid_variance": 9.67, "solid_pure_score": 0.383, "solid_rgb_band": [168, 152, 165],
     "frames": 88, "frames_clean": 37, "avg_rgb": [128, 110, 128], "samples": [[64, 45, 86], [88, 100, 140], [158, 139, 152], [221, 209, 203]], "variance": 9.0, "pure_score": 0.4},
    "Crimstone": {"tile_id": 203, "solid_rgb": [103, 34, 34], "solid_variance": 9.88, "solid_pure_score": 0.378, "solid_rgb_band": [162, 58, 55],
     "frames": 86, "frames_clean": 16, "avg_rgb": [106, 38, 34], "samples": [[51, 8, 8], [67, 20, 20], [98, 32, 32], [195, 67, 67]], "variance": 9.65, "pure_score": 0.383},
    "JungleGrass": {"tile_id": 60, "solid_rgb": [91, 139, 32], "solid_variance": 20.82, "solid_pure_score": 0.224, "solid_rgb_band": [118, 178, 30],
     "frames": 106, "frames_clean": 16, "avg_rgb": [89, 125, 37], "samples": [[12, 34, 2], [40, 101, 13], [99, 151, 31], [143, 215, 29]], "variance": 17.04, "pure_score": 0.26},
    "ShimmerBrick": {"tile_id": 667, "solid_rgb": [191, 212, 214], "solid_variance": 9.46, "solid_pure_score": 0.388, "solid_rgb_band": [255, 253, 235],
     "frames": 144, "frames_clean": 14, "avg_rgb": [174, 189, 195], "samples": [[114, 81, 56], [121, 200, 243], [253, 201, 232], [255, 254, 245]], "variance": 9.31, "pure_score": 0.392},
    "FleshBlock": {"tile_id": 195, "solid_rgb": [147, 31, 43], "solid_variance": 4.93, "solid_pure_score": 0.549, "solid_rgb_band": [172, 44, 57],
     "frames": 76, "frames_clean": 15, "avg_rgb": [138, 45, 59], "samples": [[115, 18, 28], [134, 22, 34], [158, 31, 45], [183, 51, 65]], "variance": 13.75, "pure_score": 0.304},
    "RedStucco": {"tile_id": 153, "solid_rgb": [221, 130, 116], "solid_variance": 7.21, "solid_pure_score": 0.454, "solid_rgb_band": [245, 151, 138],
     "frames": 144, "frames_clean": 29, "avg_rgb": [200, 118, 103], "samples": [[114, 81, 56], [215, 118, 99], [239, 141, 126], [255, 169, 160]], "variance": 12.54, "pure_score": 0.324},
    "RedDynastyShingles": {"tile_id": 312, "solid_rgb": [152, 62, 55], "solid_variance": 6.38, "solid_pure_score": 0.485, "solid_rgb_band": [188, 76, 65],
     "frames": 32, "frames_clean": 12, "avg_rgb": [130, 53, 48], "samples": [[46, 28, 15], [81, 49, 27], [180, 68, 62], [210, 106, 88]], "variance": 6.44, "pure_score": 0.482},
    "HellstoneBrick": {"tile_id": 76, "solid_rgb": [110, 44, 40], "solid_variance": 12.31, "solid_pure_score": 0.328, "solid_rgb_band": [215, 76, 59],
     "frames": 118, "frames_clean": 11, "avg_rgb": [104, 44, 43], "samples": [[68, 16, 16], [68, 16, 16], [117, 48, 48], [250, 150, 95]], "variance": 14.0, "pure_score": 0.3},
    "LunarBlockNebula": {"tile_id": 417, "solid_rgb": [129, 89, 189], "solid_variance": 12.42, "solid_pure_score": 0.326, "solid_rgb_band": [214, 120, 234],
     "frames": 86, "frames_clean": 19, "avg_rgb": [122, 84, 168], "samples": [[28, 43, 132], [80, 87, 182], [160, 87, 234], [255, 225, 250]], "variance": 14.07, "pure_score": 0.299},
    "AncientBlueBrick": {"tile_id": 677, "solid_rgb": [77, 79, 148], "solid_variance": 8.67, "solid_pure_score": 0.409, "solid_rgb_band": [120, 121, 195],
     "frames": 144, "frames_clean": 22, "avg_rgb": [79, 76, 131], "samples": [[52, 53, 78], [56, 58, 144], [66, 69, 194], [149, 149, 189]], "variance": 10.96, "pure_score": 0.354},
}

_BLOCK_IDS: tuple = ("Dirt", "Stone", "Wood", "WoodPlank", "StoneBrick", "Sand", "Grass", "Brick", "Ash", "Copper", "Iron", "Silver", "Gold", "Diamond", "Glass", "Clay", "WoodEbon", "WoodPearl", "WoodShade", "Snow", "Ice", "Hellstone", "Meteorite", "Lihzahrd", "Mushroom", "LivingWood", "PalmWood", "Obsidian", "Mud", "Sandstone", "HardenedSand", "Ebonstone", "Pearlstone", "Crimstone", "JungleGrass", "ShimmerBrick", "FleshBlock", "RedStucco", "RedDynastyShingles", "HellstoneBrick", "LunarBlockNebula", "AncientBlueBrick")


def get_palette() -> dict:
    """返回 block_id -> 色卡条目的映射。"""
    return PALETTE


def get_entry(block_id: str) -> dict | None:
    """按 block_id 取单个色卡条目，缺失返回 None。"""
    return PALETTE.get(block_id)


def coverage() -> float:
    """成功覆盖的方块比例（0~1）。"""
    if not _BLOCK_IDS:
        return 0.0
    return sum(1 for bid in _BLOCK_IDS if bid in PALETTE) / len(_BLOCK_IDS)


def base_rgb(block_id: str) -> list | None:
    """匹配用固有色：实心整块口径，缺失返回 None。"""
    entry = PALETTE.get(block_id)
    return entry.get("solid_rgb") if entry else None


def pure_score(block) -> float:
    """纯色度 0~1：接受 block_id / BlockColor 对象 / samples 列表 / 主色。

    优先实心整块口径；matcher.py 会依次用这三种实参试探，任一可用即返回。
    """
    if isinstance(block, str):
        entry = PALETTE.get(block)
        return _entry_purity(entry) if entry else 0.0

    bid = getattr(block, "block_id", None)
    if isinstance(bid, str):
        entry = PALETTE.get(bid)
        if entry:
            return _entry_purity(entry)
        return _score_from_samples(getattr(block, "samples", None))

    if isinstance(block, (list, tuple)):
        if len(block) == 3 and all(isinstance(c, (int, float)) for c in block):
            return 1.0
        return _score_from_samples(block)
    raise TypeError("pure_score 无法识别的实参类型：%r" % (type(block),))


def _entry_purity(entry: dict) -> float:
    if entry.get("solid_rgb") is not None:
        return float(entry["solid_pure_score"])
    return float(entry["pure_score"])


def _score_from_samples(samples) -> float:
    """按采样点在 RGB 立方内的离散度估算纯色度。"""
    pts = [tuple(int(c) for c in s) for s in (samples or []) if len(s) == 3]
    if len(pts) <= 1:
        return 1.0
    arr = np.asarray(pts, dtype=np.float64)
    spread = float(np.sqrt(((arr - arr.mean(axis=0)) ** 2).sum(axis=1)).mean())
    return 1.0 / (1.0 + spread / 24.0)
