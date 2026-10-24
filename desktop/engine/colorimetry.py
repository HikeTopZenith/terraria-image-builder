"""
CIEDE2000 色差计算（向量化）。

匹配器默认使用本模块的 CIEDE2000 代替旧的 CIELAB 欧氏距离；旧算法保留在
matcher.lab_distance 中，作为 delta_algorithm="lab" 的备选匹配模式。

参考数据取自 Sharma, Wu & Dalal (2005) 的 34 组 CIEDE2000 测试向量，
见 tests/test_colorimetry.py（atol 1e-4）。

依赖：numpy（已内置）。
"""

from __future__ import annotations

import numpy as np

# 25^7：CIEDE2000 中 C'^7 归一化的常数项
_POW25_7 = 25.0 ** 7


def _as_pts(labs) -> np.ndarray:
    return np.asarray(labs, dtype=np.float64).reshape(-1, 3)


def _hue_deg(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """色相角（度），归一化到 [0, 360)。全零输入（无彩度）返回 0，由调用方掩码覆盖。"""
    return np.mod(np.degrees(np.arctan2(b, a)), 360.0)


def _hue_terms(labs_m, labs_n):
    """色相相关中间量 -> (C1p, C2p, Δh', h1p, h2p, 无彩度掩码)，前三个形状为 (m, n)。"""
    m = _as_pts(labs_m)
    n = _as_pts(labs_n)
    a1, b1 = m[:, 1:2], m[:, 2:3]
    a2, b2 = n[None, :, 1], n[None, :, 2]

    cbar = 0.5 * (np.hypot(a1, b1) + np.hypot(a2, b2))
    cbar7 = cbar ** 7
    # G：低彩度区域放大 a*，修正灰轴附近的色相敏感性
    g = 0.5 * (1.0 - np.sqrt(cbar7 / (cbar7 + _POW25_7)))

    a1p = (1.0 + g) * a1
    a2p = (1.0 + g) * a2
    c1p, c2p = np.broadcast_arrays(np.hypot(a1p, b1), np.hypot(a2p, b2))
    h1p = _hue_deg(a1p, b1)
    h2p = _hue_deg(a2p, b2)

    dh = h2p - h1p
    dh = np.where(np.abs(dh) <= 180.0, dh, np.where(dh > 180.0, dh - 360.0, dh + 360.0))
    # 任一端无彩度时色相无定义，Δh' 记 0（CIEDE2000 原文约定）
    achromatic = (c1p * c2p) == 0.0
    dh = np.where(achromatic, 0.0, dh)
    return c1p, c2p, dh, h1p, h2p, achromatic


def delta_e_2000_array(labs_m, labs_n) -> np.ndarray:
    """向量化 CIEDE2000：(m,3) × (n,3) -> (m,n) 色差矩阵。"""
    m = _as_pts(labs_m)
    n = _as_pts(labs_n)
    c1p, c2p, dh, h1p, h2p, achromatic = _hue_terms(m, n)

    L1, L2 = m[:, 0:1], n[None, :, 0]
    d_lp = L2 - L1
    d_cp = c2p - c1p
    d_hp = 2.0 * np.sqrt(c1p * c2p) * np.sin(np.radians(0.5 * dh))

    lbarp = 0.5 * (L1 + L2)
    cbarp = 0.5 * (c1p + c2p)

    hsum = h1p + h2p
    hbarp = np.where(
        np.abs(h1p - h2p) <= 180.0,
        0.5 * hsum,
        np.where(hsum < 360.0, 0.5 * (hsum + 360.0), 0.5 * (hsum - 360.0)),
    )
    hbarp = np.where(achromatic, hsum, hbarp)

    hbar_r = np.radians(hbarp)
    T = (
        1.0
        - 0.17 * np.cos(hbar_r - np.radians(30.0))
        + 0.24 * np.cos(2.0 * hbar_r)
        + 0.32 * np.cos(3.0 * hbar_r + np.radians(6.0))
        - 0.20 * np.cos(4.0 * hbar_r - np.radians(63.0))
    )
    d_theta = 30.0 * np.exp(-(((hbarp - 275.0) / 25.0) ** 2))
    cbarp7 = cbarp ** 7
    r_c = 2.0 * np.sqrt(cbarp7 / (cbarp7 + _POW25_7))
    s_l = 1.0 + (0.015 * (lbarp - 50.0) ** 2) / np.sqrt(20.0 + (lbarp - 50.0) ** 2)
    s_c = 1.0 + 0.045 * cbarp
    s_h = 1.0 + 0.015 * cbarp * T
    r_t = -np.sin(np.radians(2.0 * d_theta)) * r_c

    t_l = d_lp / s_l
    t_c = d_cp / s_c
    t_h = d_hp / s_h
    return np.sqrt(np.maximum(0.0, t_l * t_l + t_c * t_c + t_h * t_h + r_t * t_c * t_h))


def delta_e_2000(lab1, lab2) -> float:
    """标量 CIEDE2000：与 delta_e_2000_array 同一套运算，保证两者一致。"""
    return float(delta_e_2000_array([lab1], [lab2])[0, 0])


def delta_h_prime_array(labs_m, labs_n) -> np.ndarray:
    """(m,n) 的 CIEDE2000 ΔH' 分量（色相残差，随彩度放大）。"""
    c1p, c2p, dh, _, _, _ = _hue_terms(labs_m, labs_n)
    return 2.0 * np.sqrt(c1p * c2p) * np.sin(np.radians(0.5 * dh))


def hue_angle_delta_array(labs_m, labs_n) -> np.ndarray:
    """(m,n) 的色相角差 |Δh'|（度，0-180），与彩度无关，供 hue_priority 加权。"""
    _, _, dh, _, _, _ = _hue_terms(labs_m, labs_n)
    return np.abs(dh)


def delta_h_prime(lab1, lab2) -> float:
    """标量 ΔH' 分量。"""
    return float(delta_h_prime_array([lab1], [lab2])[0, 0])


def hue_angle_delta(lab1, lab2) -> float:
    """标量色相角差（度）。"""
    return float(hue_angle_delta_array([lab1], [lab2])[0, 0])
