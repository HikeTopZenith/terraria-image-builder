# -*- coding: utf-8 -*-
"""
泰拉瑞亚自动建筑系统 - 外部桌面程序（阶段4 UI）

第一梯队：导入（文件/剪贴板）、原图预览、方块预览、三档模式、开始/终止、进度与统计
第二梯队：预处理工具条（旋转/镜像/亮度/对比度/裁剪）、黑白名单面板
第三梯队：匹配设置（色差算法/保色相/纯色优先/抖动）、自定义色卡面板
注：亮度/对比度是图像预处理（不是已移除的光照功能），保留。

后端：engine 包（预处理/匹配/蓝图/契约）+ client.BuildClient（WebSocket）。
协议：递归键排序 SHA-256、Chunks 数组、指令自带 OriginX/OriginY、max_total_blocks=0 表示不限。
"""

from __future__ import annotations

import dataclasses
import math
import os
import queue
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Optional

from PIL import Image, ImageEnhance, ImageOps, ImageTk

# 高 DPI 感知：不做的话 150%/200% 缩放下界面发虚、截图坐标也会错位
try:
    from ctypes import windll
    windll.shcore.SetProcessDpiAwareness(1)  # PROCESS_SYSTEM_DPI_AWARE
except Exception:
    pass

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# 拖拽支持：tkinterdnd2 放在项目内 vendor/ 下（自包含，不污染 ComfyUI 环境）
_VENDOR = os.path.join(_ROOT, "vendor")
if os.path.isdir(_VENDOR) and _VENDOR not in sys.path:
    sys.path.insert(0, _VENDOR)

try:
    from tkinterdnd2 import TkinterDnD, DND_FILES
    _HAS_DND = True
    _TkBase = TkinterDnD.Tk
except Exception:
    _HAS_DND = False
    _TkBase = tk.Tk

import re

from engine.engine import BuildEngine, EngineConfig, BuildResult
from engine.contract import (
    blueprint_to_chunked_command, PlaceCommandConfig,
    save_project, load_project, build_settings_dict,
)

try:  # 引擎侧新增：补齐 6 个新字段的默认值（旧 .tibp 缺字段时全走默认）
    from engine.contract import normalize_settings as normalize_project_settings
    _HAS_NORMALIZE_SETTINGS = True
except Exception:
    _HAS_NORMALIZE_SETTINGS = False

    def normalize_project_settings(settings):
        return dict(settings or {})
from engine.presets import preset_labels, get_preset, DEFAULT_PRESET
from engine.preview import render_grid, blueprint_statistics
from engine.client import BuildClient
from engine.input import SUPPORTED_IMAGE_EXTENSIONS, REJECTED_IMAGE_EXTENSIONS
from engine.blocks import BLOCKS

MODE_LABELS = [
    ("precise", "纯色精准"),
    ("natural", "自然过渡"),
    ("minimal", "极简"),
]
MODE_BY_LABEL = {label: mode for mode, label in MODE_LABELS}

# 色差算法下拉：键名与 MatchConfig.delta_algorithm、.tibp 的 settings 一致
DELTA_LABELS = [
    ("ciede2000", "CIEDE2000"),
    ("lab", "LAB（旧）"),
]
DELTA_DEFAULT = "ciede2000"
DELTA_LABEL_TO_KEY = {label: key for key, label in DELTA_LABELS}
DELTA_KEY_TO_LABEL = {key: label for key, label in DELTA_LABELS}

# 自定义色卡上限以引擎为准（不在 UI 里另写一份数字）
try:
    from engine.matcher import MAX_CUSTOM_PALETTE as PALETTE_LIMIT
    _PALETTE_LIMIT_FROM_ENGINE = True
except Exception:  # 引擎侧未合并时兜底（会在界面上提示，不静默）
    PALETTE_LIMIT = 50
    _PALETTE_LIMIT_FROM_ENGINE = False
PALETTE_ROW_H = 26


class _Tooltip:
    """极简悬浮提示：<Enter> 延时弹无边框小窗，<Leave>/按下即销毁。

    tkinter 没有内置 tooltip，这里自己实现（不引任何第三方依赖）。
    text 可在运行时改（面板状态变化时要跟着变）。
    """

    DELAY_MS = 350

    def __init__(self, widget, text: str):
        self.widget = widget
        self.text = text
        self._win = None
        self._after = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _event=None):
        self._cancel()
        try:
            self._after = self.widget.after(self.DELAY_MS, self._show)
        except tk.TclError:
            self._after = None

    def _cancel(self):
        if self._after is not None:
            try:
                self.widget.after_cancel(self._after)
            except tk.TclError:
                pass
            self._after = None

    def _show(self):
        self._after = None
        if self._win is not None or not self.text:
            return
        try:
            win = tk.Toplevel(self.widget)
            win.wm_overrideredirect(True)
            win.attributes("-topmost", True)
            tk.Label(win, text=self.text, justify=tk.LEFT, background="#ffffe1",
                     foreground="#111111", relief=tk.SOLID, borderwidth=1,
                     wraplength=300, font=("Microsoft YaHei UI", 9),
                     padx=5, pady=3).pack()
            x = self.widget.winfo_rootx() + 6
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
            win.geometry("+%d+%d" % (x, y))
        except tk.TclError:
            return
        self._win = win

    def _hide(self, _event=None):
        self._cancel()
        win, self._win = self._win, None
        if win is not None:
            try:
                win.destroy()
            except tk.TclError:
                pass

# 规模提示阈值：超过 WARN 提示耗时，超过 HARD 直接拒绝（与模组端 MaxBlocksPerTask 对齐）
WARN_BLOCK_COUNT = 50000
HARD_BLOCK_COUNT = 200000
# 模组端实测放置速率（块/秒），用于估算耗时（保守取值，留余量）
ESTIMATED_BLOCKS_PER_SEC = 1200
# 单批发往模组的最大块数，必须小于模组端硬上限 200000
BATCH_MAX_BLOCKS = 150000
# 建筑最大高度：地表玩家向上约 350 格可用，再高会顶到天顶只看到底部
MAX_BUILD_HEIGHT = 350


class App(_TkBase):
    def __init__(self):
        super().__init__()
        self.title("TerrariaImageBuilder · 外部建造台")
        self.geometry("1280x760")
        self.minsize(1080, 660)

        # 匹配开关：与 engine.MatchConfig 同名字段，也原样写进 .tibp 的 settings
        self.delta_var = tk.StringVar(value=DELTA_KEY_TO_LABEL[DELTA_DEFAULT])
        self.hue_var = tk.BooleanVar(value=False)
        self.pure_var = tk.BooleanVar(value=False)
        self.dither_var = tk.BooleanVar(value=False)
        self.custom_palette_var = tk.BooleanVar(value=False)
        self.custom_palette: list = []      # [{"rgb": [r, g, b], "block_id": "Stone"}]
        self._palette_win = None
        self._palette_canvas = None
        self._palette_count_lbl = None
        self._palb_block_var = None
        self._pick_swatch = None
        self._pick_label = None
        self._palette_sel = -1
        self._pick_mode = False
        self._pick_rgb = None
        self._fallback_notes: list = []   # 引擎侧降级/忽略参数的记录（不许静默）
        self._tooltips: list = []         # 持有 Tooltip 引用，避免被 GC

        if not _PALETTE_LIMIT_FROM_ENGINE:
            self._warn_fallback("引擎未提供 MAX_CUSTOM_PALETTE，色卡上限按 " + str(PALETTE_LIMIT) + " 处理")
        if not _HAS_NORMALIZE_SETTINGS:
            self._warn_fallback("引擎未提供 normalize_settings，旧工程按缺省值兼容读取")

        self.engine = BuildEngine(self._make_engine_config())
        self._sync_engine_match_config()
        self.result: Optional[BuildResult] = None

        self.source_image: Optional[Image.Image] = None   # 原始导入图
        self.processed_image: Optional[Image.Image] = None  # 预处理后的图（用于匹配）
        self.current_path: Optional[str] = None

        self._photo_src = None
        self._photo_blocks = None
        self._disp = None            # (x, y, w, h, scale) 预览图在画布中的位置
        self._crop_start = None
        self._crop_rect_id = None

        self.rotate_quarters = 0
        self.flip_h = False
        self.flip_v = False
        self.brightness = 1.0
        self.contrast = 1.0

        self.blacklist: set = set()
        self.whitelist_mode = False
        self.whitelist: set = set()

        self.events: "queue.Queue[tuple]" = queue.Queue()
        self.client = BuildClient(
            on_status=lambda d: self.events.put(("status", d)),
            on_connected=lambda: self.events.put(("connected", None)),
            on_disconnected=lambda: self.events.put(("disconnected", None)),
            on_log=lambda t: self.events.put(("log", t)),
        )
        self.client.start()

        self.total_blocks = 0
        self.completed_blocks = 0
        self._batches = []
        self._batch_index = 0
        self._batch_blocks_total = 0
        self._auto_scaled = False   # 用户主动缩放到目标后，不再重复弹规模警告
        self._build_ui()
        if self._fallback_notes:   # 构造期就降级的，启动后在状态栏回显一次
            self.status_var.set("提示：" + self._fallback_notes[0])
        self.after(60, self._drain_events)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ─────────────────────────── UI 骨架 ───────────────────────────

    def _build_ui(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        top = ttk.Frame(self, padding=(10, 8))
        top.pack(side=tk.TOP, fill=tk.X)

        ttk.Button(top, text="选择图片", command=self.on_open_file).pack(side=tk.LEFT)
        ttk.Button(top, text="从剪贴板", command=self.on_paste).pack(side=tk.LEFT, padx=(6, 0))

        ttk.Label(top, text="匹配模式").pack(side=tk.LEFT, padx=(18, 4))
        self.mode_var = tk.StringVar(value="纯色精准")
        mode_box = ttk.Combobox(top, textvariable=self.mode_var, width=10, state="readonly",
                                values=[label for _, label in MODE_LABELS])
        mode_box.pack(side=tk.LEFT)
        mode_box.bind("<<ComboboxSelected>>", lambda e: self.on_mode_changed())

        self.btn_conn = ttk.Button(top, text="连接游戏", command=self.on_connect)
        self.btn_conn.pack(side=tk.RIGHT)
        self.conn_label = ttk.Label(top, text="未连接", foreground="#b00")
        self.conn_label.pack(side=tk.RIGHT, padx=(0, 8))

        body = ttk.Frame(self, padding=(10, 0))
        body.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        body.columnconfigure(0, weight=1)
        body.columnconfigure(1, weight=1)
        body.columnconfigure(2, weight=0)
        body.rowconfigure(0, weight=1)

        # 左：原图（支持拖拽裁剪）
        left = ttk.LabelFrame(body, text="原图（按住左键拖拽可裁剪）", padding=6)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 4))
        self.canvas_src = tk.Canvas(left, bg="#1e1e1e", highlightthickness=0)
        self.canvas_src.pack(fill=tk.BOTH, expand=True)

        # 中：方块预览
        mid = ttk.LabelFrame(body, text="方块预览（离线）", padding=6)
        mid.grid(row=0, column=1, sticky="nsew", padx=4)
        self.canvas_blocks = tk.Canvas(mid, bg="#1e1e1e", highlightthickness=0)
        self.canvas_blocks.pack(fill=tk.BOTH, expand=True)

        # 右：标签页
        right = ttk.Frame(body, width=340)
        right.grid(row=0, column=2, sticky="ns", padx=(4, 0))
        right.grid_propagate(False)
        self.notebook = ttk.Notebook(right)
        self.notebook.pack(fill=tk.BOTH, expand=True)

        self._build_tab_build()
        self._build_tab_preprocess()
        self._build_tab_blocks()

        bottom = ttk.Frame(self, padding=(10, 6))
        bottom.pack(side=tk.BOTTOM, fill=tk.X)
        self.status_var = tk.StringVar(value="就绪：请选择一张 PNG / JPG 图片")
        ttk.Label(bottom, textvariable=self.status_var, anchor="w").pack(side=tk.LEFT, fill=tk.X, expand=True)

        for cv in (self.canvas_src, self.canvas_blocks):
            cv.bind("<Configure>", lambda e: self._redraw_all())

        self._setup_dnd();

        self.canvas_src.bind("<ButtonPress-1>", self._crop_press)
        self.canvas_src.bind("<Button-3>", lambda e: self._cancel_pick())
        self.canvas_src.bind("<B1-Motion>", self._crop_drag)
        self.canvas_src.bind("<ButtonRelease-1>", self._crop_release)

    def _build_tab_build(self):
        tab = ttk.Frame(self.notebook, padding=8)
        self.notebook.add(tab, text="建造")

        preset_row = ttk.LabelFrame(tab, text="参数预设", padding=6)
        preset_row.pack(fill=tk.X)
        self.preset_labels = preset_labels()
        default_label = self.preset_labels.get(DEFAULT_PRESET, "像素画精准")
        self.preset_var = tk.StringVar(value=default_label)
        ttk.Combobox(preset_row, textvariable=self.preset_var, width=12, state="readonly",
                     values=list(self.preset_labels.values())).pack(side=tk.LEFT)
        ttk.Button(preset_row, text="套用", command=self._apply_preset).pack(side=tk.LEFT, padx=(6, 0))

        io_row = ttk.Frame(tab)
        io_row.pack(fill=tk.X, pady=(6, 0))
        ttk.Button(io_row, text="导出工程", command=self._export_project).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(io_row, text="导入工程", command=self._import_project).pack(side=tk.LEFT, padx=(6, 0), fill=tk.X, expand=True)

        ttk.Separator(tab).pack(fill=tk.X, pady=8)

        ttk.Button(tab, text="生成预览", command=self.on_build_preview).pack(fill=tk.X)
        self._hint(tab, "色差算法 / 保色相 / 纯色优先 / 抖动 / 自定义色卡在「方块」页顶部")

        ttk.Separator(tab).pack(fill=tk.X, pady=8)

        origin = ttk.Frame(tab)
        origin.pack(fill=tk.X)
        ttk.Label(origin, text="原点 X").pack(side=tk.LEFT)
        self.origin_x = tk.StringVar(value="0")
        ttk.Entry(origin, textvariable=self.origin_x, width=7).pack(side=tk.LEFT, padx=(4, 8))
        ttk.Label(origin, text="Y").pack(side=tk.LEFT)
        self.origin_y = tk.StringVar(value="0")
        ttk.Entry(origin, textvariable=self.origin_y, width=7).pack(side=tk.LEFT, padx=(4, 4))
        ttk.Button(origin, text="同步玩家位置", command=self._request_player_pos).pack(side=tk.LEFT)

        btns = ttk.Frame(tab)
        btns.pack(fill=tk.X, pady=(8, 0))
        self.btn_start = ttk.Button(btns, text="开始建造", command=self.on_start_build)
        self.btn_start.pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(btns, text="终止", command=self.on_stop_build).pack(side=tk.LEFT, padx=(6, 0))



        ttk.Separator(tab).pack(fill=tk.X, pady=8)

        self.lbl_stats = ttk.Label(tab, text="总方块：0\n种类：0\n分块：0", justify=tk.LEFT)
        self.lbl_stats.pack(fill=tk.X)

        ttk.Separator(tab).pack(fill=tk.X, pady=8)

        self.progress = ttk.Progressbar(tab, mode="determinate", maximum=100)
        self.progress.pack(fill=tk.X)
        self.lbl_progress = ttk.Label(tab, text="0 / 0")
        self.lbl_progress.pack(fill=tk.X, pady=(4, 0))

        ttk.Separator(tab).pack(fill=tk.X, pady=8)
        self.lbl_block_detail = ttk.Label(tab, text="", justify=tk.LEFT, wraplength=300)
        self.lbl_block_detail.pack(fill=tk.BOTH, expand=True)

    def _build_tab_preprocess(self):
        tab = ttk.Frame(self.notebook, padding=8)
        self.notebook.add(tab, text="预处理")

        rot = ttk.LabelFrame(tab, text="方向", padding=6)
        rot.pack(fill=tk.X)
        ttk.Button(rot, text="顺时针 90°", command=lambda: self._rotate(1)).pack(side=tk.LEFT)
        ttk.Button(rot, text="逆时针 90°", command=lambda: self._rotate(-1)).pack(side=tk.LEFT, padx=(6, 0))
        line2 = ttk.Frame(tab)
        line2.pack(fill=tk.X, pady=(6, 0))
        ttk.Button(line2, text="水平镜像", command=self._flip_h).pack(side=tk.LEFT)
        ttk.Button(line2, text="垂直镜像", command=self._flip_v).pack(side=tk.LEFT, padx=(6, 0))

        enh = ttk.LabelFrame(tab, text="亮度 / 对比度", padding=6)
        enh.pack(fill=tk.X, pady=(8, 0))

        ttk.Label(enh, text="亮度").pack(anchor="w")
        self.bright_var = tk.DoubleVar(value=1.0)
        ttk.Scale(enh, from_=0.3, to=1.8, variable=self.bright_var,
                  command=lambda v: self._on_enhance()).pack(fill=tk.X)

        ttk.Label(enh, text="对比度").pack(anchor="w", pady=(6, 0))
        self.contrast_var = tk.DoubleVar(value=1.0)
        ttk.Scale(enh, from_=0.3, to=1.8, variable=self.contrast_var,
                  command=lambda v: self._on_enhance()).pack(fill=tk.X)

        crop = ttk.LabelFrame(tab, text="裁剪", padding=6)
        crop.pack(fill=tk.X, pady=(8, 0))
        ttk.Label(crop, text="在左侧原图上按住左键拖拽选择区域").pack(anchor="w")
        row = ttk.Frame(crop)
        row.pack(fill=tk.X, pady=(6, 0))
        ttk.Button(row, text="撤销裁剪", command=self._undo_crop).pack(side=tk.LEFT)

        size_box = ttk.LabelFrame(tab, text="目标方块数（自动缩放）", padding=6)
        size_box.pack(fill=tk.X, pady=(8, 0))

        row_sz = ttk.Frame(size_box)
        row_sz.pack(fill=tk.X)
        ttk.Label(row_sz, text="目标").pack(side=tk.LEFT)
        self.scale_target_var = tk.StringVar(value="200000")
        ttk.Entry(row_sz, textvariable=self.scale_target_var, width=9).pack(side=tk.LEFT, padx=(4, 6))
        ttk.Label(row_sz, text="块").pack(side=tk.LEFT)
        ttk.Button(row_sz, text="自动缩放", command=self._auto_scale).pack(side=tk.RIGHT)

        self.lbl_size = ttk.Label(size_box, text="", justify=tk.LEFT, wraplength=300)
        self.lbl_size.pack(fill=tk.X, pady=(4, 0))

        ttk.Separator(tab).pack(fill=tk.X, pady=8)
        ttk.Button(tab, text="重置全部预处理", command=self._reset_preprocess).pack(fill=tk.X)

        self.lbl_pre = ttk.Label(tab, text="", justify=tk.LEFT, wraplength=300)
        self.lbl_pre.pack(fill=tk.X, pady=(8, 0))

    def _build_tab_blocks(self):
        tab = ttk.Frame(self.notebook, padding=8)
        self.notebook.add(tab, text="方块")

        # ── 匹配设置（单行紧凑）：算法下拉 + 三个开关 + 色卡入口 ──
        #    解释文案全部走悬浮提示（_Tooltip），不占行；这样方块列表仍留有充足高度。
        match_row = ttk.Frame(tab)
        match_row.pack(fill=tk.X, pady=(0, 6))

        self._frame_bg = ttk.Style().lookup("TFrame", "background") or "#dcdad5"
        self.delta_box = ttk.Combobox(
            match_row, textvariable=self.delta_var, width=12, state="readonly",
            values=[label for _, label in DELTA_LABELS])
        self.delta_box.pack(side=tk.LEFT)
        self.delta_box.bind("<<ComboboxSelected>>", lambda e: self._on_match_option_changed())

        chk_hue = ttk.Checkbutton(match_row, text="保色相", variable=self.hue_var,
                                  command=self._on_match_option_changed)
        chk_hue.pack(side=tk.LEFT, padx=(4, 0))
        chk_pure = ttk.Checkbutton(match_row, text="纯色优先", variable=self.pure_var,
                                   command=self._on_match_option_changed)
        chk_pure.pack(side=tk.LEFT, padx=(4, 0))
        chk_dither = ttk.Checkbutton(match_row, text="抖动", variable=self.dither_var,
                                     command=self._on_match_option_changed)
        chk_dither.pack(side=tk.LEFT, padx=(4, 0))

        self.btn_palette = tk.Button(
            match_row, text="色卡 0/50", width=9, font=("Microsoft YaHei UI", 8),
            bd=1, relief=tk.RAISED, padx=2, pady=0, highlightthickness=0,
            bg=self._frame_bg, activebackground=self._frame_bg,
            command=self._open_palette_window)
        self.btn_palette.pack(side=tk.LEFT, padx=(6, 0))

        self._tooltip(self.delta_box,
                      "颜色算法\nCIEDE2000（默认/推荐）更贴近人眼；传统 LAB 是旧算法，"
                      "同样参数下结果会有差别")
        self._tooltip(chk_hue,
                      "优先保色相\n允许牺牲亮度/饱和度换色相准确，解决红变棕、蓝变紫，"
                      "适合人像 / 角色")
        self._tooltip(chk_pure,
                      "纯色方块优先\n优先低纹理纯色方块（如硬化沙、彩砖），适合大面积背景")
        self._tooltip(chk_dither,
                      "Floyd-Steinberg 抖动\n渐变区域用误差扩散消除色块断层，"
                      "方块种类可能变多，大图会慢几秒")
        self._palette_tip = self._tooltip(self.btn_palette, "")
        self._update_palette_state_label()

        self.whitelist_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(tab, text="白名单模式（只允许选中项）",
                        variable=self.whitelist_var,
                        command=self._on_blocklist_mode).pack(anchor="w")

        ttk.Label(tab, text="多选后可禁用/启用：").pack(anchor="w", pady=(6, 2))

        wrap = ttk.Frame(tab)
        wrap.pack(fill=tk.BOTH, expand=True)
        scroll = ttk.Scrollbar(wrap)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.lst_blocks = tk.Listbox(wrap, selectmode=tk.EXTENDED, yscrollcommand=scroll.set,
                                     exportselection=False, height=16)
        self.lst_blocks.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.config(command=self.lst_blocks.yview)

        self._block_ids = []
        for b in BLOCKS:
            self._block_ids.append(b.block_id)
            tag = " [常用]" if b.common else ""
            extra = " [透明]" if getattr(b, "transparent", False) else ""
            extra += " [重力]" if getattr(b, "falling", False) else ""
            self.lst_blocks.insert(tk.END, f"{b.block_id} · {b.name}{tag}{extra}")

        btns = ttk.Frame(tab)
        btns.pack(fill=tk.X, pady=(6, 0))
        ttk.Button(btns, text="禁用选中", command=self._disable_selected).pack(side=tk.LEFT)
        ttk.Button(btns, text="启用选中", command=self._enable_selected).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Button(btns, text="清空限制", command=self._clear_restrictions).pack(side=tk.LEFT, padx=(6, 0))

        self.lbl_blocks = ttk.Label(tab, text="禁用：无", justify=tk.LEFT, wraplength=300)
        self.lbl_blocks.pack(fill=tk.X, pady=(6, 0))


    # ─────────────────────────── 预设 / 工程包 ───────────────────────────

    def _collect_settings(self) -> dict:
        """把 UI 当前所有可复现参数（含 6 个匹配开关）收拢成 settings 字典。"""
        base = dict(
            match_mode=MODE_BY_LABEL.get(self.mode_var.get(), "precise"),
            tolerance=self.engine.config.tolerance,
            texture_weight=self.engine.config.texture_weight,
            common_priority=self.engine.config.common_priority,
            blacklist=sorted(self.blacklist),
            whitelist=sorted(self.whitelist) if (self.whitelist_mode and self.whitelist) else None,
            allow_falling=bool(getattr(self.engine.config, "allow_falling", False)),
            allow_transparent=bool(getattr(self.engine.config, "allow_transparent", False)),
            preset=self.preset_var.get(),
            preprocess={
                "rotate_quarters": self.rotate_quarters % 4,
                "flip_h": self.flip_h,
                "flip_v": self.flip_v,
                "brightness": round(float(self.brightness), 3),
                "contrast": round(float(self.contrast), 3),
            },
            origin={"x": self._safe_int(self.origin_x.get()), "y": self._safe_int(self.origin_y.get())},
        )
        extra = self._match_settings_dict()
        try:
            return build_settings_dict(**base, **extra)
        except TypeError:  # 引擎侧还没收编这 6 个参数：退回按键名合并（值完全一致）
            settings = build_settings_dict(**base)
            settings.update(extra)
            return settings

    @staticmethod
    def _safe_int(text: str, fallback: int = 0) -> int:
        try:
            return int(text)
        except Exception:
            return fallback

    def _apply_preset(self):
        label = self.preset_var.get()
        key = next((k for k, v in self.preset_labels.items() if v == label), DEFAULT_PRESET)
        preset = get_preset(key)

        # 匹配参数
        cfg = self.engine.config
        cfg.match_mode = preset["match_mode"]
        cfg.tolerance = preset["tolerance"]
        cfg.texture_weight = preset["texture_weight"]
        cfg.common_priority = preset["common_priority"]
        cfg.allow_falling = preset.get("allow_falling", False)
        cfg.allow_transparent = preset.get("allow_transparent", False)

        # 黑白名单
        self.blacklist = set(preset.get("blacklist") or [])
        whitelist = preset.get("whitelist")
        self.whitelist = set(whitelist) if whitelist else set()
        self.whitelist_mode = bool(whitelist)
        if hasattr(self, "whitelist_var"):
            self.whitelist_var.set(self.whitelist_mode)
        self._refresh_block_label()

        # UI 匹配模式下拉
        label_of_mode = next((lab for mode, lab in MODE_LABELS if mode == preset["match_mode"]), "纯色精准")
        self.mode_var.set(label_of_mode)

        # 预处理参数
        pre = preset.get("preprocess") or {}
        self.rotate_quarters = int(pre.get("rotate_quarters", 0)) % 4
        self.flip_h = bool(pre.get("flip_h", False))
        self.flip_v = bool(pre.get("flip_v", False))
        if hasattr(self, "bright_var"):
            self.bright_var.set(float(pre.get("brightness", 1.0)))
            self.contrast_var.set(float(pre.get("contrast", 1.0)))
        self.brightness = float(pre.get("brightness", 1.0))
        self.contrast = float(pre.get("contrast", 1.0))

        self._sync_engine_match_config()
        self.result = None
        if self.source_image is not None:
            self._rebuild_processed()
        self.status_var.set(f"已套用预设「{label}」：{preset.get('desc','')}")

    def _export_project(self):
        if self.result is None:
            messagebox.showinfo("提示", "请先生成预览（导出包含蓝图与全部参数）")
            return
        path = filedialog.asksaveasfilename(
            title="导出工程", defaultextension=".tibp",
            filetypes=[("建造工程", "*.tibp"), ("JSON", "*.json")])
        if not path:
            return
        source = {"path": self.current_path or "", "width": 0, "height": 0}
        if self.source_image is not None:
            source["width"], source["height"] = self.source_image.size
        try:
            save_project(self.result.blueprint, self._collect_settings(), path, source)
        except Exception as exc:
            messagebox.showerror("导出失败", str(exc))
            return
        self.status_var.set(f"工程已导出：{os.path.basename(path)}")

    def _import_project(self):
        path = filedialog.askopenfilename(
            title="导入工程", filetypes=[("建造工程", "*.tibp"), ("JSON", "*.json"), ("所有文件", "*.*")])
        if not path:
            return
        try:
            loaded = load_project(path)
        except Exception as exc:
            messagebox.showerror("导入失败", str(exc))
            return

        settings = loaded.get("settings") or {}
        if settings:
            self._apply_settings(settings)

        # 工程里带蓝图时直接进入可建造状态
        bp = loaded.get("blueprint")
        if bp is not None and getattr(bp, "chunks", None):
            try:
                from engine.engine import BuildResult
                stats = blueprint_statistics(bp)
                self.result = BuildResult(
                    blueprint=bp, processing_time_ms=0.0,
                    total_blocks=stats["total_blocks"], total_chunks=stats["chunks"],
                    matched_grid=None, alpha_mask=None, warnings=[])
                self._redraw_blocks()
                self.lbl_stats.config(
                    text=f"总方块：{stats['total_blocks']}\n种类：{stats['unique_blocks']}\n分块：{stats['chunks']}")
                top = list(stats["by_block"].items())[:10]
                self.lbl_block_detail.config(text="\n".join(f"{b}: {n}" for b, n in top))
            except Exception as exc:
                self.status_var.set(f"工程已读取，但蓝图应用失败：{exc}")
                return

        self.status_var.set(f"工程已导入：{os.path.basename(path)}（可直接开始建造）")

    def _apply_settings(self, settings: dict):
        """把工程里的参数还原到 UI 控件与引擎配置。"""
        cfg = self.engine.config
        cfg.match_mode = settings.get("match_mode", cfg.match_mode)
        cfg.tolerance = float(settings.get("tolerance", cfg.tolerance))
        cfg.texture_weight = float(settings.get("texture_weight", cfg.texture_weight))
        cfg.common_priority = bool(settings.get("common_priority", cfg.common_priority))
        cfg.allow_falling = bool(settings.get("allow_falling", False))
        cfg.allow_transparent = bool(settings.get("allow_transparent", False))

        self.blacklist = set(settings.get("blacklist") or [])
        wl = settings.get("whitelist")
        self.whitelist = set(wl) if wl else set()
        self.whitelist_mode = bool(wl)
        if hasattr(self, "whitelist_var"):
            self.whitelist_var.set(self.whitelist_mode)
        self._refresh_block_label()

        label_of_mode = next((lab for mode, lab in MODE_LABELS if mode == cfg.match_mode), "纯色精准")
        self.mode_var.set(label_of_mode)
        if settings.get("preset"):
            self.preset_var.set(settings["preset"])

        pre = settings.get("preprocess") or {}
        self.rotate_quarters = int(pre.get("rotate_quarters", 0)) % 4
        self.flip_h = bool(pre.get("flip_h", False))
        self.flip_v = bool(pre.get("flip_v", False))
        if hasattr(self, "bright_var"):
            self.bright_var.set(float(pre.get("brightness", 1.0)))
            self.contrast_var.set(float(pre.get("contrast", 1.0)))
        self.brightness = float(pre.get("brightness", 1.0))
        self.contrast = float(pre.get("contrast", 1.0))

        origin = settings.get("origin") or {}
        self.origin_x.set(str(origin.get("x", 0)))
        self.origin_y.set(str(origin.get("y", 0)))

        # 6 个匹配开关（旧工程没有这些字段时全部取默认值，不报错）
        self._apply_match_settings(settings)

        self._apply_restrictions_to_engine()
        if self.source_image is not None:
            self._rebuild_processed()

    # ─────────────────────────── 拖拽导入 ───────────────────────────

    def _setup_dnd(self):
        """注册文件拖拽：整个窗口 + 两个画布都接受拖入。"""
        if not _HAS_DND:
            self.status_var.set("提示：未找到 tkinterdnd2，拖拽不可用（仍可用按钮或剪贴板导入）")
            return

        targets = [(self, "窗口")]
        for cv in (self.canvas_src, self.canvas_blocks):
            try:
                cv.drop_target_register(DND_FILES)
                cv.dnd_bind("<<Drop>>", self._on_drop)
                targets.append((cv, "画布"))
            except Exception:
                pass

        try:
            self.drop_target_register(DND_FILES)
            self.dnd_bind("<<Drop>>", self._on_drop)
        except Exception:
            pass

    @staticmethod
    def _parse_drop_paths(data: str):
        """解析 tkdnd 的 data 串：可能是 {带空格的路径} 或空格分隔的多个路径。"""
        if not data:
            return []
        paths = re.findall(r"\{([^}]*)\}", data)
        rest = re.sub(r"\{[^}]*\}", " ", data)
        paths += [p for p in rest.split() if p]
        return [p.strip().strip('"') for p in paths if p.strip()]

    def _on_drop(self, event):
        paths = self._parse_drop_paths(getattr(event, "data", ""))
        if not paths:
            return

        rejected = []
        for p in paths:
            if not os.path.isfile(p):
                continue
            ext = os.path.splitext(p)[1].lower()
            if ext in SUPPORTED_IMAGE_EXTENSIONS:
                self.load_path(p)
                self.status_var.set(f"已拖入：{os.path.basename(p)}")
                return
            if ext in REJECTED_IMAGE_EXTENSIONS:
                rejected.append(ext)

        if rejected:
            messagebox.showerror("格式不支持",
                                 f"拖入的格式不支持：{', '.join(sorted(set(rejected)))}\n仅支持 PNG / JPG / JPEG")
        else:
            messagebox.showinfo("拖拽", "请拖入 PNG / JPG 图片文件")

    # ─────────────────────────── 导入 ───────────────────────────

    def on_open_file(self):
        exts = " ".join("*" + e for e in sorted(SUPPORTED_IMAGE_EXTENSIONS))
        path = filedialog.askopenfilename(title="选择图片",
                                          filetypes=[("图片", exts), ("所有文件", "*.*")])
        if path:
            self.load_path(path)

    def on_paste(self):
        try:
            from PIL import ImageGrab
            img = ImageGrab.grabclipboard()
        except Exception as exc:
            messagebox.showerror("剪贴板", f"读取剪贴板失败：{exc}")
            return
        if isinstance(img, list):
            messagebox.showinfo("剪贴板", "剪贴板里是文件列表，请用「选择图片」")
            return
        if img is None:
            messagebox.showinfo("剪贴板", "剪贴板里没有图片")
            return
        self.source_image = img.convert("RGBA")
        self.current_path = None
        self.result = None
        self._reset_preprocess(silent=True)
        self._rebuild_processed()
        self.status_var.set(f"已从剪贴板导入：{img.width}×{img.height}")

    def load_path(self, path: str):
        ext = os.path.splitext(path)[1].lower()
        if ext in REJECTED_IMAGE_EXTENSIONS:
            messagebox.showerror("格式不支持", f"{ext} 不在支持范围内（仅 PNG / JPG / JPEG）")
            return
        if ext not in SUPPORTED_IMAGE_EXTENSIONS:
            messagebox.showerror("格式不支持", f"不支持的格式：{ext}")
            return
        try:
            self.source_image = Image.open(path).convert("RGBA")
        except Exception as exc:
            messagebox.showerror("打开失败", str(exc))
            return
        self.current_path = path
        self.result = None
        self._reset_preprocess(silent=True)
        self._rebuild_processed()
        size_mb = os.path.getsize(path) / (1024 * 1024)
        warn = "（大于 5MB，建议先压缩）" if size_mb > 5 else ""
        self.status_var.set(
            f"已导入 {os.path.basename(path)}：{self.source_image.width}×{self.source_image.height} {warn}")

    # ─────────────────────────── 预处理 ───────────────────────────

    def _rebuild_processed(self):
        if self.source_image is None:
            self.processed_image = None
            self._redraw_src()
            return

        img = self.source_image
        if self.flip_h:
            img = ImageOps.mirror(img)
        if self.flip_v:
            img = ImageOps.flip(img)
        if self.rotate_quarters % 4:
            img = img.rotate(-90 * (self.rotate_quarters % 4), expand=True)

        self.brightness = float(self.bright_var.get()) if hasattr(self, "bright_var") else 1.0
        self.contrast = float(self.contrast_var.get()) if hasattr(self, "contrast_var") else 1.0
        if abs(self.brightness - 1.0) > 1e-3:
            img = ImageEnhance.Brightness(img).enhance(self.brightness)
        if abs(self.contrast - 1.0) > 1e-3:
            img = ImageEnhance.Contrast(img).enhance(self.contrast)

        self.processed_image = img
        self._redraw_src()
        self._update_size_label()
        if hasattr(self, "lbl_pre"):
            self.lbl_pre.config(
                text=f"旋转 {self.rotate_quarters % 4 * 90}°  镜像 H={self.flip_h} V={self.flip_v}\n"
                     f"亮度 {self.brightness:.2f}  对比度 {self.contrast:.2f}\n"
                     f"尺寸 {img.width}×{img.height}")

    def _rotate(self, quarters: int):
        if self.source_image is None:
            return
        self.rotate_quarters += quarters
        self.result = None
        self._rebuild_processed()

    def _flip_h(self):
        if self.source_image is None:
            return
        self.flip_h = not self.flip_h
        self.result = None
        self._rebuild_processed()

    def _flip_v(self):
        if self.source_image is None:
            return
        self.flip_v = not self.flip_v
        self.result = None
        self._rebuild_processed()

    def _on_enhance(self):
        if self.source_image is None:
            return
        self.result = None
        self._rebuild_processed()

    def _reset_preprocess(self, silent: bool = False):
        self.rotate_quarters = 0
        self.flip_h = self.flip_v = False
        if hasattr(self, "bright_var"):
            self.bright_var.set(1.0)
            self.contrast_var.set(1.0)
        self.brightness = self.contrast = 1.0
        if not silent:
            self.result = None
            self._rebuild_processed()
            self.status_var.set("已重置预处理")

    def _undo_crop(self):
        if self.current_path:
            self.load_path(self.current_path)
            self.status_var.set("已还原为磁盘上的原图")
        else:
            messagebox.showinfo("裁剪", "剪贴板导入的图片无法还原，请重新导入")

    # ─────────────────────────── 裁剪交互 ───────────────────────────

    def _crop_press(self, event):
        if self._pick_mode:
            self._pick_at(event.x, event.y)
            return
        if self.processed_image is None or self._disp is None:
            return
        x, y, w, h, scale = self._disp
        if not (x <= event.x <= x + w and y <= event.y <= y + h):
            return
        self._crop_start = (event.x, event.y)
        if self._crop_rect_id:
            self.canvas_src.delete(self._crop_rect_id)
        self._crop_rect_id = self.canvas_src.create_rectangle(
            event.x, event.y, event.x, event.y, outline="#4da3ff", width=2)

    def _crop_drag(self, event):
        if self._crop_start is None or self._crop_rect_id is None:
            return
        sx, sy = self._crop_start
        self.canvas_src.coords(self._crop_rect_id, sx, sy, event.x, event.y)

    def _crop_release(self, event):
        if self._crop_start is None or self._disp is None:
            return
        sx, sy = self._crop_start
        ex, ey = event.x, event.y
        self._crop_start = None
        if self._crop_rect_id:
            self.canvas_src.delete(self._crop_rect_id)
            self._crop_rect_id = None

        if abs(ex - sx) < 4 or abs(ey - sy) < 4:
            return

        x, y, w, h, scale = self._disp
        # 画布坐标 -> 预处理图坐标
        px0 = max(0, min(w, min(sx, ex) - x)) / scale
        py0 = max(0, min(h, min(sy, ey) - y)) / scale
        px1 = max(0, min(w, max(sx, ex) - x)) / scale
        py1 = max(0, min(h, max(sy, ey) - y)) / scale

        left, top = int(px0), int(py0)
        right, bottom = int(px1), int(py1)
        if right - left < 2 or bottom - top < 2:
            return

        try:
            cropped = self.processed_image.crop((left, top, right, bottom))
        except Exception as exc:
            messagebox.showerror("裁剪失败", str(exc))
            return

        # 裁剪结果作为新的源图，并清空方向/增强参数以免二次叠加
        self.source_image = cropped
        self.current_path = None
        self.rotate_quarters = 0
        self.flip_h = self.flip_v = False
        if hasattr(self, "bright_var"):
            self.bright_var.set(1.0)
            self.contrast_var.set(1.0)
        self.brightness = self.contrast = 1.0
        self.result = None
        self._rebuild_processed()
        self.status_var.set(f"已裁剪为 {cropped.width}×{cropped.height}")

    # ─────────────────────────── 尺寸 / 自动缩放 ───────────────────────────

    def _update_size_label(self):
        if not hasattr(self, "lbl_size"):
            return
        if self.processed_image is None:
            self.lbl_size.config(text="")
            return
        w, h = self.processed_image.size
        blocks = w * h
        target = self._safe_int(self.scale_target_var.get(), HARD_BLOCK_COUNT)
        pct = (blocks / target * 100.0) if target > 0 else 0
        self.lbl_size.config(
            text=f"当前 {w}×{h} = {blocks} 块（目标的 {pct:.0f}%）\n"
                 f"上限 {HARD_BLOCK_COUNT} 块；超出会被模组端拒绝")

    def _auto_scale(self):
        """按目标方块数等比缩小到 w*h <= 目标。"""
        if self.processed_image is None:
            messagebox.showinfo("提示", "请先导入图片")
            return

        target = self._safe_int(self.scale_target_var.get(), HARD_BLOCK_COUNT)
        if target <= 0:
            messagebox.showerror("参数错误", "目标方块数必须是正整数")
            return
        # 不允许超过硬上限，否则模组端会拒绝
        if target > HARD_BLOCK_COUNT:
            target = HARD_BLOCK_COUNT
            self.scale_target_var.set(str(target))

        img = self.processed_image
        w, h = img.size
        if w * h <= target:
            self.status_var.set(f"当前 {w}×{h}={w*h} 块，已在目标 {target} 以内，无需缩放")
            self._update_size_label()
            return

        ratio = math.sqrt(target / float(w * h))
        nw = max(1, int(w * ratio))
        nh = max(1, int(h * ratio))

        # 高度上限：地表附近向上通常只有 350 格空间，再高就会顶到世界天顶
        if nh > MAX_BUILD_HEIGHT:
            shrink = MAX_BUILD_HEIGHT / float(nh)
            nw = max(1, int(nw * shrink))
            nh = MAX_BUILD_HEIGHT

        # 微调，确保 nw*nh 不超过目标
        while nw * nh > target and (nw > 1 or nh > 1):
            if nw >= nh and nw > 1:
                nw -= 1
            elif nh > 1:
                nh -= 1
            else:
                break

        # 像素风用 NEAREST 保锐利，其余用 LANCZOS 平滑
        mode = MODE_BY_LABEL.get(self.mode_var.get(), "precise")
        resample = Image.NEAREST if mode == "precise" else Image.LANCZOS

        try:
            self.source_image = img.resize((nw, nh), resample)
        except Exception as exc:
            messagebox.showerror("缩放失败", str(exc))
            return

        # 缩放后清空方向类预处理，避免二次叠加导致尺寸对不上
        self.rotate_quarters = 0
        self.flip_h = self.flip_v = False
        if hasattr(self, "bright_var"):
            self.bright_var.set(1.0)
            self.contrast_var.set(1.0)
        self.brightness = self.contrast = 1.0
        self.result = None
        self._auto_scaled = True
        self._rebuild_processed()
        self._update_size_label()
        self.status_var.set(
            f"已自动缩放：{w}×{h} → {nw}×{nh}（{nw*nh} 块，目标 {target}）")

    # ─────────────────────────── 方块黑白名单 ───────────────────────────

    def _on_blocklist_mode(self):
        self.whitelist_mode = bool(self.whitelist_var.get())
        self._refresh_block_label()
        self.result = None

    def _disable_selected(self):
        sel = [self._block_ids[i] for i in self.lst_blocks.curselection()]
        if not sel:
            return
        if self.whitelist_mode:
            self.whitelist -= set(sel)
        else:
            self.blacklist |= set(sel)
        self._refresh_block_label()
        self.result = None

    def _enable_selected(self):
        sel = [self._block_ids[i] for i in self.lst_blocks.curselection()]
        if not sel:
            return
        if self.whitelist_mode:
            self.whitelist |= set(sel)
        else:
            self.blacklist -= set(sel)
        self._refresh_block_label()
        self.result = None

    def _clear_restrictions(self):
        self.blacklist.clear()
        self.whitelist.clear()
        self._refresh_block_label()
        self.result = None

    def _refresh_block_label(self):
        if self.whitelist_mode:
            txt = "白名单：" + (", ".join(sorted(self.whitelist)) if self.whitelist else "空（= 不限制）")
        else:
            txt = "禁用：" + (", ".join(sorted(self.blacklist)) if self.blacklist else "无")
        if hasattr(self, "lbl_blocks"):
            self.lbl_blocks.config(text=txt)
        self._refresh_palette_list()

    # ────────────────── 匹配开关（算法 / 色相 / 纯色 / 抖动 / 色卡） ──────────────────

    @staticmethod
    def _hint(parent, text: str):
        """开关旁的一行人话提示（小字），免得用户对着复选框猜效果。"""
        lbl = ttk.Label(parent, text=text, foreground="#666", wraplength=298,
                        justify=tk.LEFT, font=("Microsoft YaHei UI", 8))
        lbl.pack(fill=tk.X, pady=(1, 0))
        return lbl

    def _tooltip(self, widget, text: str) -> "_Tooltip":
        """挂一个悬浮提示，并把引用留下（否则会被 GC 掉）。"""
        tip = _Tooltip(widget, text)
        self._tooltips.append(tip)
        return tip

    def _warn_fallback(self, msg: str):
        """引擎侧降级/忽略参数时留痕：状态栏 + 启动后回显 + 日志 —— 不许静默。"""
        if msg in self._fallback_notes:
            return
        self._fallback_notes.append(msg)
        try:
            print("[UI][降级] " + msg)
        except Exception:
            pass
        if hasattr(self, "status_var"):
            try:
                self.status_var.set("提示：" + msg)
            except tk.TclError:
                pass

    def _match_settings_dict(self) -> dict:
        """6 个匹配开关，键名与 MatchConfig 字段、.tibp 的 settings 一一对应。"""
        return {
            "delta_algorithm": DELTA_LABEL_TO_KEY.get(self.delta_var.get(), DELTA_DEFAULT),
            "hue_priority": bool(self.hue_var.get()),
            "prefer_pure": bool(self.pure_var.get()),
            "dither": bool(self.dither_var.get()),
            "use_custom_palette": bool(self.custom_palette_var.get()),
            "custom_palette": [
                {"rgb": [int(e["rgb"][0]), int(e["rgb"][1]), int(e["rgb"][2])],
                 "block_id": str(e["block_id"])}
                for e in self.custom_palette
            ],
        }

    def _make_engine_config(self, match_mode: str = "precise") -> EngineConfig:
        """构造 EngineConfig：新开关能收就收，收不了就降级（窗口绝不能被 import 拖垮）。"""
        extra = self._match_settings_dict()
        try:
            return EngineConfig(match_mode=match_mode, **extra)
        except TypeError:
            self._warn_fallback("引擎 EngineConfig 不接受 6 个匹配参数，已改为逐项 setattr 兜底")
            cfg = EngineConfig(match_mode=match_mode)
            ignored = []
            for key, value in extra.items():
                try:
                    setattr(cfg, key, value)
                except Exception:
                    ignored.append(key)
            if ignored:
                self._warn_fallback("引擎不支持这些匹配参数，已忽略：" + ", ".join(ignored))
            return cfg

    def _build_match_config(self):
        """在引擎自己的 MatchConfig 上补齐 6 个开关，不复制任何匹配逻辑。"""
        try:
            base = self.engine._to_match_config()
        except Exception as exc:
            base = self.engine.matcher.config
            self._warn_fallback("引擎构造 MatchConfig 失败（" + str(exc) + "），改用当前匹配器配置")

        wanted = self._match_settings_dict()
        usable = {k: v for k, v in wanted.items() if hasattr(base, k)}
        missing = [k for k in wanted if k not in usable]
        if missing:
            self._warn_fallback("引擎 MatchConfig 缺少字段，这些开关不会生效：" + ", ".join(sorted(missing)))
        if not usable:
            return base
        try:
            return dataclasses.replace(base, **usable)
        except Exception as exc:
            self._warn_fallback("引擎拒绝该 MatchConfig（" + str(exc) + "），本次沿用旧匹配配置")
            return base

    def _sync_engine_match_config(self):
        """把 6 个开关搬进引擎配置并重建匹配器；出错弹窗，不往控制台抛。"""
        ignored = []
        for key, value in self._match_settings_dict().items():
            try:
                setattr(self.engine.config, key, value)
            except Exception:
                ignored.append(key)
        if ignored:
            self._warn_fallback("引擎配置不接受这些字段，已忽略：" + ", ".join(sorted(ignored)))
        try:
            self.engine.matcher = self.engine.matcher.__class__(
                self.engine.lib, self._build_match_config())
        except Exception as exc:
            messagebox.showerror("匹配设置应用失败", str(exc))
        self._update_palette_state_label()

    def _on_match_option_changed(self):
        """开关/算法变更：立刻进引擎，并把旧预览判废，避免拿旧蓝图去建造。"""
        self._sync_engine_match_config()
        self.result = None
        self._redraw_blocks()
        self.status_var.set("匹配设置已更新：重新点「生成预览」后生效")

    def _apply_match_settings(self, settings: dict):
        """从 .tibp 的 settings 还原 6 个开关；缺字段/非法值一律回落默认值。"""
        settings = dict(settings or {})
        try:  # 引擎口径：补齐 6 个新字段默认值 + 校验色卡
            settings = normalize_project_settings(settings)
        except Exception as exc:
            # 色卡超限/格式异常：下面按宽容口径处理，但要明确告诉用户
            self._warn_fallback("引擎 normalize_settings 拒绝该工程设置（" + str(exc) + "），已按宽容口径读取")

        algo = str(settings.get("delta_algorithm") or DELTA_DEFAULT)
        if algo not in DELTA_KEY_TO_LABEL:
            algo = DELTA_DEFAULT
        self.delta_var.set(DELTA_KEY_TO_LABEL[algo])
        self.hue_var.set(bool(settings.get("hue_priority", False)))
        self.pure_var.set(bool(settings.get("prefer_pure", False)))
        self.dither_var.set(bool(settings.get("dither", False)))
        self.custom_palette_var.set(bool(settings.get("use_custom_palette", False)))

        raw = settings.get("custom_palette") or []
        self.custom_palette = self._sanitize_palette(raw)
        self._palette_sel = -1
        if isinstance(raw, list) and len(raw) > PALETTE_LIMIT:
            messagebox.showwarning(
                "色卡超限",
                "工程里有 " + str(len(raw)) + " 条色卡，超过上限 " + str(PALETTE_LIMIT)
                + " 条，只载入前 " + str(PALETTE_LIMIT) + " 条（不是静默丢弃，特此提示）。")
        self._refresh_palette_list()
        self._update_palette_state_label()

    @staticmethod
    def _sanitize_palette(raw) -> list:
        """只接受 {rgb: [0-255 x3], block_id: "非空"}；非法条目直接丢弃。"""
        entries: list = []
        for item in raw if isinstance(raw, list) else []:
            try:
                rgb = [max(0, min(255, int(c))) for c in list(item["rgb"])[:3]]
                block_id = str(item.get("block_id") or "").strip()
            except Exception:
                continue
            if len(rgb) != 3 or not block_id:
                continue
            entries.append({"rgb": rgb, "block_id": block_id})
            if len(entries) >= PALETTE_LIMIT:
                break
        return entries

    # ─────────────────────────── 自定义色卡面板 ───────────────────────────

    def _block_choices(self):
        ids = list(getattr(self, "_block_ids", None) or [])
        if ids:
            return ids
        try:
            return [b.block_id for b in BLOCKS]
        except Exception:
            return []

    def _palette_entry_warning(self, block_id: str) -> str:
        """色卡绑定的方块被黑白名单排除时给个标记（UI 不阻止添加，引擎会回退）。"""
        if self.whitelist_mode:
            return "" if block_id in self.whitelist else "不在白名单"
        return "已在黑名单" if block_id in self.blacklist else ""

    def _update_palette_state_label(self):
        """色卡入口按钮兼作状态显示：固定宽度，数字变化不抖动。"""
        on = bool(self.custom_palette_var.get())
        n = len(self.custom_palette)
        btn = getattr(self, "btn_palette", None)
        if btn is not None:
            try:
                btn.config(text="色卡 %d/%d" % (n, PALETTE_LIMIT),
                           fg="#070" if on else "#111111")
            except tk.TclError:
                pass
        tip = getattr(self, "_palette_tip", None)
        if tip is not None:
            tip.text = ("自定义色卡：点开面板 取色 → 绑定方块。当前 %d/%d 条，%s"
                        % (n, PALETTE_LIMIT,
                           "已启用（优先使用色卡）" if on else "未启用"))
        if self._palette_count_lbl is not None:
            try:
                self._palette_count_lbl.config(text="%d/%d 条" % (n, PALETTE_LIMIT))
            except tk.TclError:
                pass

    def _open_palette_window(self):
        win = self._palette_win
        if win is not None:
            try:
                if win.winfo_exists():
                    win.deiconify()
                    win.lift()
                    self._refresh_palette_list()
                    return
            except tk.TclError:
                pass

        win = tk.Toplevel(self)
        self._palette_win = win
        win.title("自定义色卡 · 取色 → 绑定方块")
        win.geometry("620x580")
        win.transient(self)
        win.protocol("WM_DELETE_WINDOW", self._close_palette_window)

        ttk.Checkbutton(win, text="启用自定义色卡优先", variable=self.custom_palette_var,
                        command=self._on_custom_palette_toggle).pack(anchor="w", padx=10, pady=(10, 0))
        ttk.Label(win, text="开启后，和色卡条目足够接近的像素优先用你绑定的方块；差太远仍然自动匹配。",
                  foreground="#666", wraplength=590).pack(anchor="w", padx=10, pady=(2, 0))

        pick = ttk.LabelFrame(win, text="① 从预览图取色", padding=8)
        pick.pack(fill=tk.X, padx=10, pady=(10, 0))
        row1 = ttk.Frame(pick)
        row1.pack(fill=tk.X)
        ttk.Button(row1, text="在当前预览图上点一下取色", command=self._arm_pick).pack(side=tk.LEFT)
        self._pick_swatch = tk.Canvas(row1, width=28, height=28, bg="#202020",
                                      highlightthickness=1, highlightbackground="#888")
        self._pick_swatch.pack(side=tk.LEFT, padx=(10, 6))
        self._pick_label = ttk.Label(row1, text="尚未取色")
        self._pick_label.pack(side=tk.LEFT)
        ttk.Label(pick, text="取色来自预处理后的图像（旋转 / 镜像 / 亮度 / 对比度之后，与匹配对象是同一张图）。",
                  foreground="#666", wraplength=570).pack(anchor="w", pady=(4, 0))
        ttk.Label(pick, text="只读取 RGB 三个通道，alpha 一律按 255（不透明）记录，取不到透明色；右键可取消取色。",
                  foreground="#666", wraplength=570).pack(anchor="w", pady=(1, 0))

        bind = ttk.LabelFrame(win, text="② 选目标方块 → 添加", padding=8)
        bind.pack(fill=tk.X, padx=10, pady=(8, 0))
        row2 = ttk.Frame(bind)
        row2.pack(fill=tk.X)
        ttk.Label(row2, text="目标方块").pack(side=tk.LEFT)
        self._palb_block_var = tk.StringVar(value="")
        self._palb_block_box = ttk.Combobox(row2, textvariable=self._palb_block_var, width=24,
                                            state="readonly", values=self._block_choices())
        self._palb_block_box.pack(side=tk.LEFT, padx=(6, 6))
        ttk.Button(row2, text="添加", command=self._add_palette_entry).pack(side=tk.LEFT)

        list_box = ttk.LabelFrame(win, text="③ 现有条目（点一行选中）", padding=8)
        list_box.pack(fill=tk.BOTH, expand=True, padx=10, pady=(8, 0))
        wrap = ttk.Frame(list_box)
        wrap.pack(fill=tk.BOTH, expand=True)
        scroll = ttk.Scrollbar(wrap)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        cv = tk.Canvas(wrap, bg="#ffffff", highlightthickness=0, yscrollcommand=scroll.set, height=170)
        cv.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.config(command=cv.yview)
        cv.bind("<Button-1>", self._on_palette_click)
        cv.bind("<Configure>", lambda e: self._refresh_palette_list())
        self._palette_canvas = cv

        btns = ttk.Frame(win)
        btns.pack(fill=tk.X, padx=10, pady=(8, 0))
        ttk.Button(btns, text="删除选中", command=self._delete_palette_entry).pack(side=tk.LEFT)
        ttk.Button(btns, text="一键清空", command=self._clear_palette).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Button(btns, text="关闭", command=self._close_palette_window).pack(side=tk.RIGHT)
        self._palette_count_lbl = ttk.Label(btns, text="")
        self._palette_count_lbl.pack(side=tk.LEFT, padx=(10, 0))

        ttk.Label(win, text="⚠ 标记 = 该方块被黑白名单排除：不拦你添加，引擎会自动回退做正常匹配。上限 "
                            + str(PALETTE_LIMIT) + " 条，满额后禁止新增。",
                  foreground="#666", wraplength=590).pack(anchor="w", padx=10, pady=(6, 10))

        try:
            self.update_idletasks()
            x = self.winfo_rootx() + max(self.winfo_width() - 640, 20)
            y = self.winfo_rooty() + 60
            win.geometry("+" + str(x) + "+" + str(y))
        except tk.TclError:
            pass

        self._refresh_palette_list()
        self._update_palette_state_label()

    def _close_palette_window(self):
        win = self._palette_win
        self._palette_win = None
        self._palette_canvas = None
        self._palette_count_lbl = None
        self._pick_swatch = None
        self._pick_label = None
        self._palb_block_var = None
        self._cancel_pick()
        try:
            if win is not None and win.winfo_exists():
                win.destroy()
        except tk.TclError:
            pass

    def _refresh_palette_list(self):
        cv = self._palette_canvas
        if cv is None:
            return
        try:
            if not cv.winfo_exists():
                return
            cv.delete("all")
            width = max(int(cv.winfo_width()), 540)
        except tk.TclError:
            return

        entries = self.custom_palette
        if not entries:
            cv.create_text(10, 14, anchor="w", fill="#888",
                           text="还没有条目：先「在原图上点一下取色」，选目标方块后点「添加」。")
            cv.config(scrollregion=(0, 0, width, 40))
            return

        for i, ent in enumerate(entries):
            y = i * PALETTE_ROW_H
            if i == self._palette_sel:
                cv.create_rectangle(0, y, width, y + PALETTE_ROW_H, fill="#dbeafe", outline="")
            r, g, b = [int(c) for c in ent["rgb"][:3]]
            cv.create_rectangle(6, y + 4, 26, y + 22, fill="#%02x%02x%02x" % (r, g, b), outline="#888")
            warn = self._palette_entry_warning(str(ent["block_id"]))
            text = str(ent["block_id"]) + "    RGB(" + str(r) + ", " + str(g) + ", " + str(b) + ")"
            fill = "#111111"
            if warn:
                text += "    ⚠ " + warn
                fill = "#b45309"
            cv.create_text(34, y + PALETTE_ROW_H // 2, anchor="w", text=text, fill=fill)
        cv.config(scrollregion=(0, 0, width, len(entries) * PALETTE_ROW_H + 4))

    def _on_palette_click(self, event):
        cv = self._palette_canvas
        if cv is None or not self.custom_palette:
            return
        try:
            idx = int(cv.canvasy(event.y) // PALETTE_ROW_H)
        except tk.TclError:
            return
        if 0 <= idx < len(self.custom_palette):
            self._palette_sel = idx
            self._refresh_palette_list()

    def _arm_pick(self):
        if self.processed_image is None:
            messagebox.showinfo("拾色", "请先导入一张图片")
            return
        self._pick_mode = True
        try:
            self.canvas_src.config(cursor="crosshair")
        except tk.TclError:
            pass
        self.status_var.set("拾色中：请在左侧预览图上单击取色（取色来自预处理后的图像；右键取消）")
        self.lift()   # 把原图窗口提到前面，别被色卡面板挡住

    def _cancel_pick(self):
        if not self._pick_mode:
            return
        self._pick_mode = False
        try:
            self.canvas_src.config(cursor="")
        except tk.TclError:
            pass
        self.status_var.set("已取消拾色")

    def _pick_at(self, cx: int, cy: int):
        """把画布坐标映射回 processed_image 的像素并取 RGB（alpha 强制 255）。

        注意：取的是「预处理之后」的图 —— 旋转/镜像/亮度/对比度都算过了，
        与真正参与匹配的像素一致；用户看到的预览图也是这张。
        """
        if not self._pick_mode:
            return
        if self.processed_image is None or self._disp is None:
            self._cancel_pick()
            messagebox.showinfo("拾色", "请先导入一张图片")
            return

        x, y, w, h, scale = self._disp
        if not (x <= cx <= x + w and y <= cy <= y + h):
            messagebox.showinfo("拾色", "请点在预览图范围内（黑色空白区域取不到颜色）")
            return

        px = max(0, min(self.processed_image.width - 1, int((cx - x) / scale)))
        py = max(0, min(self.processed_image.height - 1, int((cy - y) / scale)))
        try:
            pixel = self.processed_image.convert("RGBA").getpixel((px, py))
        except Exception as exc:
            messagebox.showerror("拾色失败", str(exc))
            return

        r, g, b = int(pixel[0]), int(pixel[1]), int(pixel[2])
        alpha = int(pixel[3]) if len(pixel) > 3 else 255
        self._pick_rgb = [r, g, b]
        self._pick_mode = False
        try:
            self.canvas_src.config(cursor="")
        except tk.TclError:
            pass

        if self._pick_swatch is not None:
            try:
                self._pick_swatch.config(bg="#%02x%02x%02x" % (r, g, b))
                note = "" if alpha >= 255 else "（原像素 alpha=" + str(alpha) + "，按 255 记录）"
                self._pick_label.config(text="RGB(" + str(r) + ", " + str(g) + ", " + str(b) + ")  alpha=255 " + note)
            except tk.TclError:
                self._pick_swatch = None
                self._pick_label = None

        self.status_var.set("已取色 RGB(" + str(r) + ", " + str(g) + ", " + str(b)
                            + ")（来自预处理后的图像）：选目标方块后点「添加」")
        win = self._palette_win
        try:
            if win is not None and win.winfo_exists():
                win.lift()
        except tk.TclError:
            pass

    def _add_palette_entry(self):
        if self._pick_rgb is None:
            messagebox.showinfo("添加色卡", "请先点「在原图上点一下取色」取一个颜色")
            return
        if len(self.custom_palette) >= PALETTE_LIMIT:
            messagebox.showwarning(
                "色卡已满",
                "自定义色卡上限 " + str(PALETTE_LIMIT)
                + " 条，已经满了，请先删除一些条目再添加。")
            return
        block_id = ""
        if self._palb_block_var is not None:
            block_id = str(self._palb_block_var.get() or "").strip()
        if not block_id:
            messagebox.showinfo("添加色卡", "请从下拉里选一个目标方块")
            return

        r, g, b = [int(c) for c in self._pick_rgb[:3]]
        self.custom_palette.append({"rgb": [r, g, b], "block_id": block_id})
        self._palette_sel = len(self.custom_palette) - 1
        self._sync_engine_match_config()
        self.result = None
        self._refresh_palette_list()
        self._update_palette_state_label()
        warn = self._palette_entry_warning(block_id)
        extra = ("（注意：该方块" + warn + "，引擎会回退自动匹配）") if warn else ""
        self.status_var.set("已添加色卡：RGB(" + str(r) + ", " + str(g) + ", " + str(b) + ") → " + block_id + extra)

    def _delete_palette_entry(self):
        if not (0 <= self._palette_sel < len(self.custom_palette)):
            messagebox.showinfo("删除色卡", "请先在列表里点选一条")
            return
        removed = self.custom_palette.pop(self._palette_sel)
        self._palette_sel = -1
        self._sync_engine_match_config()
        self.result = None
        self._refresh_palette_list()
        self._update_palette_state_label()
        self.status_var.set("已删除色卡条目：" + str(removed.get("block_id")))

    def _clear_palette(self):
        if not self.custom_palette:
            messagebox.showinfo("清空色卡", "现在没有色卡条目")
            return
        if not messagebox.askyesno("清空色卡",
                                   "确定清空全部 " + str(len(self.custom_palette)) + " 条色卡吗？"):
            return
        self.custom_palette = []
        self._palette_sel = -1
        self._sync_engine_match_config()
        self.result = None
        self._refresh_palette_list()
        self._update_palette_state_label()
        self.status_var.set("已清空自定义色卡")

    def _on_custom_palette_toggle(self):
        self._sync_engine_match_config()
        self.result = None
        self._redraw_blocks()
        on = bool(self.custom_palette_var.get())
        self.status_var.set("自定义色卡优先：" + ("已启用" if on else "已关闭") + "（重新生成预览生效）")

    # ─────────────────────────── 预览 ───────────────────────────

    def _redraw_all(self):
        self._redraw_src()
        self._redraw_blocks()

    def _redraw_src(self):
        self._draw_image(self.canvas_src, self.processed_image, smooth=True)

    def _redraw_blocks(self):
        if self.result is None:
            self._draw_image(self.canvas_blocks, None)
            return
        img = render_grid(self.result.matched_grid, scale=1)
        self._draw_image(self.canvas_blocks, img, smooth=False)

    def _draw_image(self, canvas: tk.Canvas, img: Optional[Image.Image], smooth: bool = False):
        canvas.delete("all")
        if img is None:
            if canvas is self.canvas_src:
                self._disp = None
            return
        cw = max(canvas.winfo_width(), 1)
        ch = max(canvas.winfo_height(), 1)
        if cw <= 1 or ch <= 1:
            return
        iw, ih = img.size
        ratio = min(cw / iw, ch / ih, 4.0)
        nw, nh = max(1, int(iw * ratio)), max(1, int(ih * ratio))
        resample = Image.BILINEAR if smooth else Image.NEAREST
        disp = img.resize((nw, nh), resample)
        photo = ImageTk.PhotoImage(disp)
        ox, oy = (cw - nw) // 2, (ch - nh) // 2
        canvas.create_image(ox, oy, image=photo, anchor="nw")

        if canvas is self.canvas_src:
            self._photo_src = photo
            self._disp = (ox, oy, nw, nh, ratio)
        else:
            self._photo_blocks = photo

    def on_mode_changed(self):
        mode = MODE_BY_LABEL.get(self.mode_var.get(), "precise")
        self.engine.config.match_mode = mode
        self._sync_engine_match_config()
        self.status_var.set(f"匹配模式：{self.mode_var.get()}（重新生成预览生效）")

    def _apply_restrictions_to_engine(self):
        """黑白名单 + 匹配开关一起进引擎，再由引擎重建匹配器（UI 不含匹配逻辑）。"""
        cfg = self.engine.config
        cfg.blacklist = sorted(self.blacklist)
        cfg.whitelist = sorted(self.whitelist) if (self.whitelist_mode and self.whitelist) else None
        self._sync_engine_match_config()

    def on_build_preview(self):
        if self.processed_image is None:
            messagebox.showinfo("提示", "请先导入图片")
            return
        self._apply_restrictions_to_engine()
        try:
            import numpy as np
            arr = np.array(self.processed_image.convert("RGBA"))
            result = self.engine.build_from_array(arr, arr[:, :, 3] > 0)
        except Exception as exc:
            messagebox.showerror("生成失败", str(exc))
            return

        self.result = result
        self._redraw_blocks()
        stats = blueprint_statistics(result.blueprint)
        self.lbl_stats.config(
            text=f"总方块：{stats['total_blocks']}\n种类：{stats['unique_blocks']}\n分块：{stats['chunks']}")
        top = list(stats["by_block"].items())[:10]
        self.lbl_block_detail.config(text="\n".join(f"{b}: {n}" for b, n in top))

        total = stats["total_blocks"]
        eta = total / ESTIMATED_BLOCKS_PER_SEC
        eta_txt = f"，预计建造约 {eta/60:.1f} 分钟" if eta >= 60 else f"，预计建造约 {eta:.0f} 秒"
        self.status_var.set(
            f"预览完成：{total} 方块 / {stats['chunks']} 分块，耗时 {result.processing_time_ms:.1f} ms{eta_txt}")

        if total > HARD_BLOCK_COUNT:
            messagebox.showerror(
                "规模过大",
                f"本次共 {total} 方块，超过单次上限 {HARD_BLOCK_COUNT}。\n\n"
                f"模组端会直接拒绝执行。请先用「自动缩放」压到目标以内。")
        elif total > WARN_BLOCK_COUNT and not self._auto_scaled:
            messagebox.showwarning(
                "规模较大",
                f"本次共 {total} 方块，预计建造约 {eta/60:.1f} 分钟。\n\n"
                f"建议：用「自动缩放」把方块数压到 {HARD_BLOCK_COUNT} 以内。")

    # ─────────────────────────── 建造 ───────────────────────────

    def _request_player_pos(self):
        """向模组查询玩家当前方块坐标（用于自动定位原点）。"""
        if not self.client.is_connected():
            return
        import time
        self.client.send_command({"Type": "GetPlayerPos", "Timestamp": int(time.time() * 1000)})

    def on_connect(self):
        if self.client.is_connected():
            self.client.stop()
            self.btn_conn.config(text="连接游戏")
            return
        self.btn_conn.config(text="连接中…")
        self.client.connect()

    def _split_batches(self, blueprint):
        """按分块把蓝图切成多批，每批不超过 BATCH_MAX_BLOCKS 块。"""
        chunks = list(getattr(blueprint, "chunks", []) or [])
        batches, cur, cur_blocks = [], [], 0
        for ch in chunks:
            n = len(getattr(ch, "blocks", []) or [])
            if cur and cur_blocks + n > BATCH_MAX_BLOCKS:
                batches.append(cur)
                cur, cur_blocks = [], 0
            cur.append(ch)
            cur_blocks += n
        if cur:
            batches.append(cur)
        return batches

    def on_start_build(self):
        if self.result is None:
            messagebox.showinfo("提示", "请先生成预览")
            return
        # 与「生成预览」走同一个 config 构造，保证下发的蓝图用的是当前设置
        self._apply_restrictions_to_engine()
        if not self.client.is_connected():
            messagebox.showwarning("未连接", "请先连接游戏（确保 tModLoader 已进世界）")
            return
        try:
            ox = int(self.origin_x.get())
            oy = int(self.origin_y.get())
        except ValueError:
            messagebox.showerror("参数错误", "原点必须是整数")
            return

        # 规模二次确认：超过提示阈值时让用户确认，避免误点后长时间占用
        try:
            est_blocks = sum(len(getattr(ch, "blocks", []) or []) for ch in getattr(self.result.blueprint, "chunks", []))
        except Exception:
            est_blocks = 0
        if est_blocks > WARN_BLOCK_COUNT:
            eta_min = est_blocks / ESTIMATED_BLOCKS_PER_SEC / 60
            if not messagebox.askyesno(
                    "确认开始建造",
                    f"本次共 {est_blocks} 方块，预计约 {eta_min:.1f} 分钟。\n\n确定开始吗？"):
                self.status_var.set("已取消（规模确认未通过）")
                return

        batches = self._split_batches(self.result.blueprint)
        if not batches:
            messagebox.showinfo("提示", "蓝图里没有可建造的方块")
            return

        total = sum(len(getattr(ch, "blocks", []) or []) for b in batches for ch in b)
        if total > WARN_BLOCK_COUNT and not self._auto_scaled:
            eta_min = total / ESTIMATED_BLOCKS_PER_SEC / 60
            if not messagebox.askyesno(
                    "确认开始建造",
                    f"本次共 {total} 方块，分 {len(batches)} 批发送。\n"
                    f"预计约 {eta_min:.1f} 分钟。\n\n确定开始吗？"):
                self.status_var.set("已取消")
                return

        self._batches = batches
        self._batch_index = 0
        self._batch_blocks_total = total
        self._origin = (ox, oy)

        self.total_blocks = total
        self.completed_blocks = 0
        self.progress.config(maximum=max(total, 1), value=0)
        self.lbl_progress.config(text=f"0 / {total}")
        self._send_next_batch()

    def _send_next_batch(self):
        """发送下一批；全部发完则结束。"""
        if not self._batches or self._batch_index >= len(self._batches):
            self.status_var.set(f"全部 {len(self._batches)} 批已发送完毕")
            return

        chunks = self._batches[self._batch_index]
        batch_no = self._batch_index + 1
        batch_blocks = sum(len(getattr(ch, "blocks", []) or []) for ch in chunks)

        # 构造只含本批分块的临时蓝图（浅拷贝，避免改到 self.result）
        import copy
        sub = copy.copy(self.result.blueprint)
        sub.chunks = chunks

        ox, oy = getattr(self, "_origin", (0, 0))
        try:
            cmd = blueprint_to_chunked_command(
                sub,
                PlaceCommandConfig(delay_between_chunks_ms=200, delay_within_chunk_ms=0,
                                   skip_existing_blocks=False, max_total_blocks=0),
                origin_x=ox, origin_y=oy,
            )
        except Exception as exc:
            self.status_var.set(f"第 {batch_no} 批指令构造失败：{exc}")
            return

        if self.client.send_command(cmd):
            self.status_var.set(
                f"第 {batch_no}/{len(self._batches)} 批已下发：{len(chunks)} 分块 / {batch_blocks} 方块")
        else:
            self.status_var.set("指令发送失败：未连接")

    def on_stop_build(self):
        if not self.client.is_connected():
            return
        import time
        self.client.send_command({"Type": "StopTask", "Timestamp": int(time.time() * 1000)})
        self.status_var.set("已发送终止指令")

    # ─────────────────────────── 事件泵 ───────────────────────────

    def _drain_events(self):
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "status":
                    self._handle_status(payload)
                elif kind == "connected":
                    self.conn_label.config(text="已连接", foreground="#070")
                    self.btn_conn.config(text="断开")
                    # 连上就自动同步一次玩家位置，避免原点停在 0,0 建到世界角落
                    self._request_player_pos()
                elif kind == "disconnected":
                    self.conn_label.config(text="未连接", foreground="#b00")
                    self.btn_conn.config(text="连接游戏")
                elif kind == "log":
                    self.status_var.set(str(payload))
        except queue.Empty:
            pass
        self.after(60, self._drain_events)

    def _handle_status(self, data: dict):
        mtype = data.get("MsgType")
        if mtype == "InstructionReceived":
            self.total_blocks = data.get("TotalBlocks", self.total_blocks)
            self.completed_blocks = 0
            self.progress.config(maximum=max(self.total_blocks, 1), value=0)
            self.lbl_progress.config(text=f"0 / {self.total_blocks}")
            self.status_var.set(f"模组已接收：{data.get('ChunkCount')} 分块 / {self.total_blocks} 方块")
        elif mtype == "ChunkProgress":
            self.completed_blocks = data.get("CompletedBlocks", self.completed_blocks)
            self.progress.config(value=self.completed_blocks)
            self.lbl_progress.config(text=f"{self.completed_blocks} / {self.total_blocks}")
            self.status_var.set(f"分块 {data.get('ChunkIndex')} 完成，累计 {self.completed_blocks} 方块")
        elif mtype == "AllCompleted":
            self.completed_blocks = data.get("TotalCompletedBlocks", self.completed_blocks)
            self.progress.config(value=self.completed_blocks)
            self.lbl_progress.config(text=f"{self.completed_blocks} / {self.total_blocks}")

            if self._batches and self._batch_index + 1 < len(self._batches):
                self._batch_index += 1
                self.status_var.set(
                    f"第 {self._batch_index}/{len(self._batches)} 批完成，"
                    f"继续下一批…（累计 {self.completed_blocks} 方块）")
                # 稍作停顿，避免指令间隔过密
                self.after(400, self._send_next_batch)
            else:
                self._batch_index = len(self._batches) if self._batches else 0
                self.status_var.set(
                    f"全部完成：{self.completed_blocks} 方块"
                    + (f"（共 {len(self._batches)} 批）" if self._batches else "")
                    + f"，最后一批耗时 {data.get('TotalCostMs')} ms")
        elif mtype == "PlayerPosition":
            px, py = data.get("X", 0), data.get("Y", 0)
            surface_y = data.get("SurfaceY", py)

            bw = bh = 0
            if self.result is not None:
                bw = int(getattr(self.result.blueprint, "width", 0) or 0)
                bh = int(getattr(self.result.blueprint, "height", 0) or 0)

            # 以「角色脚下」为地面：
            #   水平居中在角色两侧，垂直从脚下向上展开（底部贴角色所在高度）
            ox = px - bw // 2 if bw else px
            if ox < 0:
                ox = 0

            if bh > 0:
                oy = py - bh + 1
                if oy < 0:
                    # 世界顶部放不下这么高的建筑：贴顶放置，并提示需要缩小
                    oy = 0
                    self.status_var.set(
                        f"注意：建筑高 {bh} 格，但你所在高度只有 {py} 格，"
                        f"顶部会贴到世界天顶（y=0）。建议先缩小或裁剪图片。")
                else:
                    self.status_var.set(
                        f"已同步：原点 ({ox}, {oy})，建筑底部在你脚下（y={py}），向上展开 {bh} 格")
            else:
                oy = py
                self.status_var.set(f"已同步玩家位置：({px}, {py})，原点已填入")

            self.origin_x.set(str(ox))
            self.origin_y.set(str(oy))
        elif mtype == "ExecutionError":
            self.status_var.set(f"执行错误：{data.get('ErrorMessage')}")

    def _on_close(self):
        try:
            self.client.stop()
        except Exception:
            pass
        self.destroy()


if __name__ == "__main__":
    App().mainloop()
