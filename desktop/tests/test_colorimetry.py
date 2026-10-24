from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

from engine.colorimetry import (
    delta_e_2000,
    delta_e_2000_array,
    delta_h_prime,
    delta_h_prime_array,
    hue_angle_delta,
    hue_angle_delta_array,
)

# Sharma, Wu & Dalal (2005) 的 34 组 CIEDE2000 参考数据：(Lab1, Lab2, 期望色差)
SHARMA = [
    ((50.0000, 2.6772, -79.7751), (50.0000, 0.0000, -82.7485), 2.0425),
    ((50.0000, 3.1571, -77.2803), (50.0000, 0.0000, -82.7485), 2.8615),
    ((50.0000, 2.8361, -74.0200), (50.0000, 0.0000, -82.7485), 3.4412),
    ((50.0000, -1.3802, -84.2814), (50.0000, 0.0000, -82.7485), 1.0000),
    ((50.0000, -1.1848, -84.8006), (50.0000, 0.0000, -82.7485), 1.0000),
    ((50.0000, -0.9009, -85.5211), (50.0000, 0.0000, -82.7485), 1.0000),
    ((50.0000, 0.0000, 0.0000), (50.0000, -1.0000, 2.0000), 2.3669),
    ((50.0000, -1.0000, 2.0000), (50.0000, 0.0000, 0.0000), 2.3669),
    ((50.0000, 2.4900, -0.0010), (50.0000, -2.4900, 0.0009), 7.1792),
    ((50.0000, 2.4900, -0.0010), (50.0000, -2.4900, 0.0010), 7.1792),
    ((50.0000, 2.4900, -0.0010), (50.0000, -2.4900, 0.0011), 7.2195),
    ((50.0000, 2.4900, -0.0010), (50.0000, -2.4900, 0.0012), 7.2195),
    ((50.0000, -0.0010, 2.4900), (50.0000, 0.0009, -2.4900), 4.8045),
    ((50.0000, -0.0010, 2.4900), (50.0000, 0.0010, -2.4900), 4.8045),
    ((50.0000, -0.0010, 2.4900), (50.0000, 0.0011, -2.4900), 4.7461),
    ((50.0000, 2.5000, 0.0000), (50.0000, 0.0000, -2.5000), 4.3065),
    ((50.0000, 2.5000, 0.0000), (73.0000, 25.0000, -18.0000), 27.1492),
    ((50.0000, 2.5000, 0.0000), (61.0000, -5.0000, 29.0000), 22.8977),
    ((50.0000, 2.5000, 0.0000), (56.0000, -27.0000, -3.0000), 31.9030),
    ((50.0000, 2.5000, 0.0000), (58.0000, 24.0000, 15.0000), 19.4535),
    ((50.0000, 2.5000, 0.0000), (50.0000, 3.1736, 0.5854), 1.0000),
    ((50.0000, 2.5000, 0.0000), (50.0000, 3.2972, 0.0000), 1.0000),
    ((50.0000, 2.5000, 0.0000), (50.0000, 1.8634, 0.5757), 1.0000),
    ((50.0000, 2.5000, 0.0000), (50.0000, 3.2592, 0.3350), 1.0000),
    ((60.2574, -34.0099, 36.2677), (60.4626, -34.1751, 39.4387), 1.2644),
    ((63.0109, -31.0961, -5.8663), (62.8187, -29.7946, -4.0864), 1.2630),
    ((61.2901, 3.7196, -5.3901), (61.4292, 2.2480, -4.9620), 1.8731),
    ((35.0831, -44.1164, 3.7933), (35.0232, -40.0716, 1.5901), 1.8645),
    ((22.7233, 20.0904, -46.6940), (23.0331, 14.9730, -42.5619), 2.0373),
    ((36.4612, 47.8580, 18.3852), (36.2715, 50.5065, 21.2231), 1.4146),
    ((90.8027, -2.0831, 1.4410), (91.1528, -1.6435, 0.0447), 1.4441),
    ((90.9257, -0.5406, -0.9208), (88.6381, -0.8985, -0.7239), 1.5381),
    ((6.7747, -0.2908, -2.4247), (5.8714, -0.0985, -2.2286), 0.6377),
    ((2.0776, 0.0795, -1.1350), (0.9033, -0.0636, -0.5514), 0.9082),
]


def test_sharma_34_pairs():
    """34 组参考数据全部落在 atol 1e-4 内。"""
    max_err = 0.0
    worst = 0
    for i, (lab1, lab2, expect) in enumerate(SHARMA, 1):
        got = delta_e_2000(lab1, lab2)
        err = abs(got - expect)
        if err > max_err:
            max_err, worst = err, i
        assert err <= 1e-4, f"第 {i} 组偏差过大：got={got!r} expect={expect} err={err:.3e}"
    print(f"PASS test_sharma_34_pairs  max_abs_err={max_err:.6e}（第 {worst} 组）")


def test_array_matches_scalar_and_expected():
    """向量化版本必须与标量版逐元素一致，且对角命中参考值。"""
    a = np.array([p[0] for p in SHARMA], dtype=np.float64)
    b = np.array([p[1] for p in SHARMA], dtype=np.float64)
    mat = delta_e_2000_array(a, b)
    assert mat.shape == (34, 34), mat.shape

    for i, (lab1, lab2, expect) in enumerate(SHARMA):
        assert abs(mat[i, i] - delta_e_2000(lab1, lab2)) < 1e-12
        assert abs(mat[i, i] - expect) <= 1e-4

    # 单个点也走同一套公式（1×n / m×1 广播）
    assert abs(float(delta_e_2000_array([SHARMA[16][0]], b)[0, 16]) - 27.1492) <= 1e-4
    print("PASS test_array_matches_scalar_and_expected  shape=(34,34)")


def test_self_distance_zero_and_monotonic():
    """同一颜色色差为 0，不同颜色为正；黑白差值应最大。"""
    probe = (50.0, 20.0, -20.0)
    mat = delta_e_2000_array([probe, (100.0, 0.0, 0.0)], [probe, (0.0, 0.0, 0.0)])
    assert abs(mat[0, 0]) < 1e-12
    assert mat[0, 1] > 0 and mat[1, 0] > 0
    assert mat[1, 1] > 90.0, mat[1, 1]  # 白 vs 黑 ≈ 100
    # 一组同色点必须全部为 0
    same = np.array([probe, (100.0, 0.0, 0.0), (0.0, 0.0, 0.0)])
    assert np.allclose(np.diag(delta_e_2000_array(same, same)), 0.0)
    print(f"PASS test_self_distance_zero_and_monotonic  white-black={mat[1, 1]:.2f}")


def test_achromatic_hue_is_zero():
    """任一端无彩度时色相无定义，Δh' 与 ΔH' 都记 0。"""
    gray = (50.0, 0.0, 0.0)
    red = (50.0, 60.0, 40.0)
    assert hue_angle_delta(gray, red) == 0.0
    assert hue_angle_delta(red, gray) == 0.0
    assert abs(delta_h_prime(gray, red)) < 1e-12
    # 两种彩色之间才有色相角差
    assert hue_angle_delta(red, (50.0, -40.0, 50.0)) > 10.0
    print("PASS test_achromatic_hue_is_zero")


def test_hue_angle_and_dh_prime_consistency():
    """色相角差落在 [0,180]，ΔH' 与角度差同向（同彩度量级下）。"""
    red = (50.0, 60.0, 40.0)
    blue = (50.0, 20.0, -60.0)
    small_gap = hue_angle_delta(red, (50.0, 62.0, 41.0))
    big_gap = hue_angle_delta(red, blue)
    assert 0.0 <= small_gap <= 180.0
    assert 0.0 <= big_gap <= 180.0
    assert big_gap > small_gap

    labs = np.array([red, blue, (50.0, 0.0, 0.0)])
    gap = hue_angle_delta_array(labs, labs)
    assert gap.shape == (3, 3)
    assert np.allclose(np.diag(gap), 0.0)
    assert np.all(gap <= 180.0 + 1e-9) and np.all(gap >= 0.0)
    dh = delta_h_prime_array(labs, labs)
    assert dh.shape == (3, 3)
    assert np.allclose(np.diag(dh), 0.0)
    print("PASS test_hue_angle_and_dh_prime_consistency")


if __name__ == "__main__":
    test_sharma_34_pairs()
    test_array_matches_scalar_and_expected()
    test_self_distance_zero_and_monotonic()
    test_achromatic_hue_is_zero()
    test_hue_angle_and_dh_prime_consistency()
    print("\n=== 色差模块测试通过 ===")
