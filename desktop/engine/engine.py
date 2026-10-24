"""
顶层引擎 facade：串联图片输入、预处理、方块匹配、分块、蓝图生成，
并输出基础运行日志。

设计约束（需求 I.4 / I.5）：
- build_match_config(settings) 是**唯一**的 MatchConfig 构造器，UI / 预设 / 引擎共用；
- BuildEngine._match_array 是**唯一**的「调用匹配」入口，蓝图与离线预览都走它，
  dither=True 时改走二维误差扩散路径（alpha_mask 一并传下去）；
- 色库统一来自 blocks.build_block_library（贴图固有色默认接管），预览与蓝图同源。

供桌面 UI 直接调用，无需修改核心逻辑。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Optional, Tuple

import numpy as np

from .contract import Blueprint
from .blocks import build_block_library
from .matcher import BlockMatcher, MatchConfig
from .input import (
    PreprocessConfig,
    LoadResult,
    ImageInputError,
    load_image,
    preprocess_image,
    apply_preprocess,
    to_array,
)
from .blueprint import BlueprintEngine, split_image_into_chunks

logger = logging.getLogger("terraria_builder")
if not logger.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
    logger.addHandler(_h)
logger.setLevel(logging.INFO)


# settings 字典里被匹配使用的键（键名与 contract.build_settings_dict 一致）。
# 未知键一律忽略，缺键走 MatchConfig 默认值 —— 旧 settings / 新 settings 都能吃。
_MATCH_SETTING_KEYS = (
    "match_mode",
    "tolerance",
    "texture_weight",
    "common_priority",
    "common_bonus",
    "blacklist",
    "whitelist",
    "minimal_block_ids",
    "allow_falling",
    "allow_transparent",
    "delta_algorithm",
    "hue_priority",
    "prefer_pure",
    "dither",
    "use_custom_palette",
    "custom_palette",
    "hue_weight",
    "pure_bonus",
    "custom_threshold",
)

# 高级调参键：只有显式出现才覆盖 MatchConfig 默认值
_OPTIONAL_MATCH_SETTING_KEYS = ("hue_weight", "pure_bonus", "custom_threshold")


def build_match_config(settings: dict) -> MatchConfig:
    """唯一的 MatchConfig 公共构造器（UI / 预设 / 引擎共用）。

    settings 键名与 .tibp 契约一致（见 contract.build_settings_dict），其中 6 个
    新字段为 delta_algorithm / hue_priority / prefer_pure / dither /
    use_custom_palette / custom_palette；缺键或 None 一律走 MatchConfig 默认值，
    未知键忽略（向前兼容）。非法值由 MatchConfig.__post_init__ 抛 ValueError。
    """
    s = dict(settings or {})

    blacklist = set(s.get("blacklist") or [])
    whitelist = s.get("whitelist")
    whitelist = None if whitelist is None else set(whitelist)
    minimal = s.get("minimal_block_ids")
    minimal = None if minimal is None else set(minimal)
    custom_palette = [dict(e) for e in (s.get("custom_palette") or [])]

    kwargs = dict(
        mode=s.get("match_mode", "precise"),
        tolerance=float(s.get("tolerance", 40.0)),
        texture_weight=float(s.get("texture_weight", 0.4)),
        common_priority=bool(s.get("common_priority", True)),
        common_bonus=float(s.get("common_bonus", 2.0)),
        blacklist=blacklist,
        whitelist=whitelist,
        allow_falling=bool(s.get("allow_falling", False)),
        allow_transparent=bool(s.get("allow_transparent", False)),
        delta_algorithm=s.get("delta_algorithm") or "ciede2000",
        hue_priority=bool(s.get("hue_priority", False)),
        prefer_pure=bool(s.get("prefer_pure", False)),
        dither=bool(s.get("dither", False)),
        use_custom_palette=bool(s.get("use_custom_palette", False)),
        custom_palette=custom_palette,
    )
    if minimal is not None:
        kwargs["minimal_block_ids"] = minimal
    for key in _OPTIONAL_MATCH_SETTING_KEYS:
        if s.get(key) is not None:
            kwargs[key] = float(s[key])
    return MatchConfig(**kwargs)


@dataclass
class EngineConfig:
    """引擎全部可配置参数。"""
    # 匹配
    match_mode: str = "precise"          # "precise" | "natural" | "minimal"
    tolerance: float = 40.0
    texture_weight: float = 0.4
    common_priority: bool = True
    common_bonus: float = 2.0
    blacklist: list = field(default_factory=list)
    whitelist: Optional[list] = None
    allow_falling: bool = False
    allow_transparent: bool = False
    minimal_block_ids: Optional[list] = None
    # 新匹配开关（与 MatchConfig 同名，直接透传；键名即 .tibp 字段名）
    delta_algorithm: str = "ciede2000"   # "ciede2000" | "lab"
    hue_priority: bool = False
    prefer_pure: bool = False
    dither: bool = False
    use_custom_palette: bool = False
    custom_palette: list = field(default_factory=list)
    # 色库口径：True = 贴图实心固有色接管（默认）；False = 旧硬编码色库（A/B 对比用）
    use_texture_palette: bool = True
    # 分块
    chunk_size: int = 256
    # 预处理（沿用 PreprocessConfig）
    preprocess: PreprocessConfig = field(default_factory=PreprocessConfig)

    def match_settings(self) -> dict:
        """转成 build_match_config / .tibp 共用的 settings 字典。"""
        return {
            "match_mode": self.match_mode,
            "tolerance": self.tolerance,
            "texture_weight": self.texture_weight,
            "common_priority": self.common_priority,
            "common_bonus": self.common_bonus,
            "blacklist": list(self.blacklist or []),
            "whitelist": None if self.whitelist is None else list(self.whitelist),
            "minimal_block_ids": None if self.minimal_block_ids is None else list(self.minimal_block_ids),
            "allow_falling": self.allow_falling,
            "allow_transparent": self.allow_transparent,
            "delta_algorithm": self.delta_algorithm,
            "hue_priority": self.hue_priority,
            "prefer_pure": self.prefer_pure,
            "dither": self.dither,
            "use_custom_palette": self.use_custom_palette,
            "custom_palette": [dict(e) for e in (self.custom_palette or [])],
        }


@dataclass
class BuildResult:
    """构建结果。"""
    blueprint: Blueprint
    processing_time_ms: float
    total_blocks: int
    total_chunks: int
    matched_grid: np.ndarray  # (H, W) block_id 或 ""
    alpha_mask: np.ndarray    # (H, W) bool
    warnings: list = field(default_factory=list)


class BuildEngine:
    """阶段3 核心计算引擎。"""

    def __init__(self, config: Optional[EngineConfig] = None):
        self.config = config or EngineConfig()
        self.lib = build_block_library(use_texture_palette=bool(self.config.use_texture_palette))
        self.matcher = self._new_matcher()

    # ---------------- 唯一匹配入口的构造 ----------------

    def _new_matcher(self) -> BlockMatcher:
        """构造匹配器：色库与 MatchConfig 都只在这里落地一次。"""
        cfg = build_match_config(self.config.match_settings())
        return BlockMatcher(self.lib, cfg)

    def _to_match_config(self) -> MatchConfig:
        """构造当前配置对应的 MatchConfig（保持旧接口名，UI 可能直接调用）。"""
        return build_match_config(self.config.match_settings())

    @property
    def match_config(self) -> MatchConfig:
        """当前生效的匹配配置（与蓝图 metadata.config 同源）。"""
        return self.matcher.config

    def rebuild_matcher(self) -> None:
        """在配置变更后重建色库与匹配器（use_texture_palette 变化也会跟着换）。"""
        self.lib = build_block_library(use_texture_palette=bool(self.config.use_texture_palette))
        self.matcher = self._new_matcher()

    # ---------------- 主入口 ----------------

    def build_from_file(
        self,
        image_path: str,
        preprocess_cfg: Optional[PreprocessConfig] = None,
    ) -> BuildResult:
        """从图片文件生成蓝图。"""
        cfg = preprocess_cfg or self.config.preprocess
        t0 = time.perf_counter()

        # 1. 图片输入（含格式拒绝、ICC 忽略、透明提取、大文件提示）
        try:
            load_res = load_image(image_path)
        except ImageInputError as e:
            logger.error(str(e))
            raise
        for w in load_res.warnings:
            logger.warning(w)

        # 2. 预处理（不修改原文件）
        arr, mask = apply_preprocess(to_array(load_res.image), load_res.alpha_mask, cfg)

        # 3. 方块匹配（唯一入口，dither 在此分流）
        matched_grid, match_warnings = self._match_array(arr, mask)
        for w in match_warnings:
            logger.warning(w)

        # 4. 生成蓝图（分块）
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        bp = self._to_blueprint(
            matched_grid,
            mask,
            elapsed_ms,
            {
                "path": image_path,
                "width": arr.shape[1],
                "height": arr.shape[0],
                "size_bytes": load_res.size_bytes,
                "format": load_res.format,
            },
        )

        result = BuildResult(
            blueprint=bp,
            processing_time_ms=elapsed_ms,
            total_blocks=bp.metadata.total_blocks,
            total_chunks=bp.metadata.total_chunks,
            matched_grid=matched_grid,
            alpha_mask=mask,
            warnings=list(match_warnings) + list(load_res.warnings),
        )

        logger.info(
            "构建完成：%d 方块，%d 分块，耗时 %.1f ms，模式=%s，dither=%s",
            result.total_blocks,
            result.total_chunks,
            result.processing_time_ms,
            self.config.match_mode,
            self.match_config.dither,
        )
        return result

    def build_from_array(
        self,
        arr: np.ndarray,
        alpha_mask: Optional[np.ndarray] = None,
        source_info: Optional[dict] = None,
    ) -> BuildResult:
        """从已处理的像素数组生成蓝图（用于 UI 预览/重复生成）。"""
        t0 = time.perf_counter()
        if alpha_mask is None:
            alpha_mask = np.ones((arr.shape[0], arr.shape[1]), dtype=bool)
        alpha_mask = np.asarray(alpha_mask, dtype=bool)

        matched_grid, warnings = self._match_array(arr, alpha_mask)

        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        bp = self._to_blueprint(matched_grid, alpha_mask, elapsed_ms, source_info or {})

        return BuildResult(
            blueprint=bp,
            processing_time_ms=elapsed_ms,
            total_blocks=bp.metadata.total_blocks,
            total_chunks=bp.metadata.total_chunks,
            matched_grid=matched_grid,
            alpha_mask=alpha_mask,
            warnings=warnings,
        )

    def _to_blueprint(
        self,
        grid: np.ndarray,
        alpha_mask: np.ndarray,
        elapsed_ms: float,
        source_info: dict,
    ) -> Blueprint:
        """蓝图生成：使用与预览同一份 self.matcher.config。"""
        engine = BlueprintEngine(chunk_size=self.config.chunk_size)
        return engine.grid_to_blueprint(
            grid=grid,
            alpha_mask=alpha_mask,
            match_config=self.match_config,
            processing_time_ms=elapsed_ms,
            source_info=source_info,
            match_mode=self.config.match_mode,
        )

    # ---------------- 内部：唯一的匹配调用点 ----------------

    def _match_array(
        self, arr: np.ndarray, alpha_mask: np.ndarray
    ) -> Tuple[np.ndarray, list]:
        """对整张图逐像素匹配。返回 (grid, warnings)。

        dither=False：一维批量路径，与改动前逐像素等价（grid[alpha_mask] = batch）；
        dither=True ：二维 Floyd–Steinberg 路径，alpha_mask 一并传下去，透明像素
                      既不匹配也不扩散误差。两条路径的结果都会进入蓝图与预览。
        """
        h, w, _ = arr.shape
        grid = np.empty((h, w), dtype=object)
        grid[:] = ""
        warnings: list = []

        total = int(np.count_nonzero(alpha_mask))
        if total == 0:
            warnings.append("图片中不存在不透明像素，不会生成任何方块。")
            return grid, warnings

        # 只取前 3 个通道（arr 可能是 RGBA），匹配器只接受 RGB
        rgb_all = np.ascontiguousarray(arr[..., :3])

        if self.match_config.dither:
            logger.info("开始抖动匹配 %d 个不透明像素（模式=%s）", total, self.config.match_mode)
            grid = self.matcher.match_image_dithered(rgb_all, alpha_mask)
            # 透明像素强制为空，避免上游掩码口径差异漏进蓝图
            grid[~alpha_mask] = ""
            grid[grid == None] = ""  # noqa: E711 无可选方块时与批量入口保持一致
            logger.info("抖动匹配完成：%d/%d", total, total)
            return grid, warnings

        logger.info("开始匹配 %d 个不透明像素（模式=%s）", total, self.config.match_mode)

        # 向量化批量匹配：逐像素 Python 循环在 20 万级规模下会卡死主线程
        try:
            matched = self.matcher.match_rgb_batch(rgb_all[alpha_mask])
            grid[alpha_mask] = matched
        except Exception as exc:
            logger.warning("批量匹配失败，回退逐像素模式：%s", exc)
            for y, x in np.argwhere(alpha_mask):
                rgb = (int(arr[y, x, 0]), int(arr[y, x, 1]), int(arr[y, x, 2]))
                res = self.matcher.match_pixel(rgb)
                grid[y, x] = res.block_id if res else ""

        logger.info("匹配完成：%d/%d", total, total)
        return grid, warnings

    # ---------------- 预览：与蓝图同源 ----------------

    def render_preview(self, result: BuildResult, scale: int = 1):
        """离线预览：只上色，不做任何匹配；色库与蓝图同源（self.lib）。"""
        from .preview import render_grid, render_blueprint

        if getattr(result, "matched_grid", None) is not None:
            return render_grid(result.matched_grid, scale=scale, library=self.lib)
        return render_blueprint(result.blueprint, scale=scale, library=self.lib)

    # ---------------- 蓝图导出/导入 ----------------

    def save_blueprint(self, bp: Blueprint, path: str) -> None:
        from .blueprint import BlueprintEngine
        BlueprintEngine.save_blueprint(bp, path)
        logger.info("蓝图已保存：%s", path)

    def load_blueprint(self, path: str) -> Blueprint:
        from .blueprint import BlueprintEngine
        bp = BlueprintEngine.load_blueprint(path)
        logger.info("蓝图已加载：%s（%d 方块，%d 分块）", path, bp.metadata.total_blocks if bp.metadata else 0, len(bp.chunks))
        return bp


if __name__ == "__main__":
    print("engine module ok")
