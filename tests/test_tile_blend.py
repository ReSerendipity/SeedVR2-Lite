"""tile_blend 分块融合 CPU 数值测试（MLOps P1-2：覆盖率 omit 偏科修复）。

验收标准：
1. 线性/余弦权重图：形状、边缘渐变值、对称性、中心为 1、overlap<=0 全 1；
2. blend_tiled_output：无重叠区精确还原 tile 常量、重叠区严格介于两值之间、空输入报错；
3. compute_temporal_segments：docstring 示例序列、边界（total<=segment）、退化重叠回退；
4. blend_temporal_segments：非重叠帧精确、重叠帧介于两值、形状保持；
5. overlap_weights（P0-1）：hann 平台/对称/单调、linear 与历史公式逐位一致、
   n<=2 与未知取值的回退语义。
"""

import math

import pytest
import torch

from app.integrated_app.optimization.inference.tile_blend import (
    blend_temporal_segments,
    blend_tiled_output,
    compute_temporal_segments,
    create_cosine_weight_map,
    create_linear_weight_map,
    hann_overlap_weights,
    linear_overlap_weights,
    overlap_weights,
)


class TestWeightMaps:
    def test_linear_weight_map_shape_and_center(self):
        w = create_linear_weight_map(64, 8)
        assert w.shape == (64, 64)
        assert float(w[32, 32]) == pytest.approx(1.0)

    def test_linear_edge_ramp_values(self):
        ts, ov = 64, 8
        w = create_linear_weight_map(ts, ov)
        # 1D 边缘权重 = 1/(ov+1)，2D 角点是两个轴外积
        edge = 1.0 / (ov + 1)
        assert float(w[0, 0]) == pytest.approx(edge * edge, abs=1e-6)
        assert float(w[3, 32]) == pytest.approx((4 / (ov + 1)) * 1.0, abs=1e-6)

    def test_linear_weight_map_symmetric(self):
        w = create_linear_weight_map(64, 8)
        assert torch.allclose(w, w.flip(0), atol=1e-7)
        assert torch.allclose(w, w.flip(1), atol=1e-7)

    def test_zero_overlap_all_ones(self):
        assert torch.all(create_linear_weight_map(32, 0) == 1)
        assert torch.all(create_cosine_weight_map(32, 0) == 1)

    def test_cosine_edge_below_linear(self):
        ts, ov = 64, 8
        lin = create_linear_weight_map(ts, ov)
        cos = create_cosine_weight_map(ts, ov)
        # sin^2(pi*x/(2(ov+1))) 在 (0,1) 区间恒低于线性 ramp
        edge_val = math.sin(math.pi / (2 * (ov + 1))) ** 2
        assert float(cos[0, 0]) == pytest.approx(edge_val * edge_val, abs=1e-6)
        assert float(cos[0, 0]) < float(lin[0, 0])

    def test_maps_in_unit_interval(self):
        for fn in (create_linear_weight_map, create_cosine_weight_map):
            w = fn(48, 6)
            assert bool((w >= 0).all() and (w <= 1).all())


class TestBlendTiledOutput:
    def test_empty_tiles_raises(self):
        with pytest.raises(ValueError):
            blend_tiled_output([], [], (64, 64), 64, 8)

    def test_single_tile_reconstructs_constant(self):
        tile = torch.full((3, 64, 64), 0.7)
        out = blend_tiled_output([tile], [(0, 0)], (64, 64), 64, 16)
        assert out.shape == (3, 64, 64)
        # 单一 tile 归一化后应精确还原常量（权重在分子分母同乘）
        assert torch.allclose(out, tile, atol=1e-5)

    def test_overlap_blend_between_two_constants(self):
        ts, ov = 64, 16
        c1, c2 = 0.2, 0.8
        t1 = torch.full((1, ts, ts), c1)
        t2 = torch.full((1, ts, ts), c2)
        stride = ts - ov
        out = blend_tiled_output(
            [t1, t2],
            [(0, 0), (0, stride)],
            (ts, ts + stride),
            ts,
            ov,
        )
        assert out.shape == (1, ts, ts + stride)
        # 非重叠区（tile1 独有列）精确等于 c1
        assert torch.allclose(out[0, :, :10], torch.full((ts, 10), c1), atol=1e-3)
        # 重叠区严格介于两常量之间
        mid = out[0, ts // 2, stride + 2]
        assert c1 < float(mid) < c2


class TestTemporalSegments:
    def test_docstring_example(self):
        assert compute_temporal_segments(100, 32, 8) == [(0, 32), (24, 56), (48, 80), (72, 100)]

    def test_single_segment_when_short(self):
        assert compute_temporal_segments(10, 32) == [(0, 10)]
        assert compute_temporal_segments(32, 32) == [(0, 32)]

    def test_segments_cover_all_frames_monotonic(self):
        segs = compute_temporal_segments(300, 64, 16)
        assert segs[0][0] == 0 and segs[-1][1] == 300
        starts = [s for s, _ in segs]
        assert starts == sorted(starts)
        # 帧覆盖无空洞：每段起点不晚于上一段终点
        for (_s1, e1), (s2, _e2) in zip(segs, segs[1:], strict=False):
            assert s2 <= e1

    def test_degenerate_overlap_falls_back(self):
        # overlap >= segment_size 时回退半段 stride（不崩溃、仍覆盖全片）
        segs = compute_temporal_segments(100, 32, 40)
        assert segs[0][0] == 0 and segs[-1][1] == 100


class TestBlendTemporalSegments:
    def test_constant_segments_blend(self):
        # 布局注意：两个前维都不是 3 时回退旧启发式 shape[0] <= shape[1] 判 (T,C,H,W)
        seg0 = torch.full((8, 16, 4, 4), 1.0)  # (T=8, C=16, H, W)，全局帧 [0,8)
        seg1 = torch.full((8, 16, 4, 4), 2.0)  # 全局帧 [6,14)，与 seg0 重叠 2 帧
        segs = [(0, 8), (6, 14)]
        out = blend_temporal_segments([seg0, seg1], segs, total_frames=14, overlap=2)
        assert out.shape == (14, 16, 4, 4)
        # 仅 seg0 覆盖的帧：权重归一化后精确 1.0
        assert torch.allclose(out[:6], seg0[:6], atol=1e-5)
        # 仅 seg1 覆盖的帧（局部索引 2..7）：精确 2.0
        assert torch.allclose(out[8:], seg1[2:], atol=1e-5)
        # 重叠帧：cosine 权重精确——frame6 = 0.75*1 + 0.25*2（seg0 尾权 0.75 / seg1 头权 0.25）
        assert float(out[6, 0, 0, 0]) == pytest.approx(1.25, abs=1e-5)
        assert float(out[7, 0, 0, 0]) == pytest.approx(1.75, abs=1e-5)

    def test_empty_segments_raise(self):
        with pytest.raises(ValueError):
            blend_temporal_segments([], [], 10, 2)

    def test_bad_ndim_raises(self):
        with pytest.raises(ValueError):
            blend_temporal_segments([torch.zeros(3, 4, 4)], [(0, 3)], 3, 1)

    def test_video_tchw_layout_with_t_gt_c(self):
        # 真实视频布局 (T=65, C=3)：T>C，旧启发式会误判为 (C,T,H,W)
        seg = torch.full((65, 3, 4, 4), 1.0)
        out = blend_temporal_segments([seg], [(0, 65)], total_frames=65, overlap=1)
        assert out.shape == (65, 3, 4, 4)
        assert torch.allclose(out, seg, atol=1e-5)

    def test_cthw_layout_with_channel_first(self):
        seg = torch.full((3, 65, 4, 4), 2.0)
        out = blend_temporal_segments([seg], [(0, 65)], total_frames=65, overlap=1)
        assert out.shape == (65, 3, 4, 4)
        assert torch.allclose(out, torch.full((65, 3, 4, 4), 2.0), atol=1e-5)

    def test_explicit_layout_override(self):
        seg = torch.ones((4, 8, 2, 2))  # 无 3 通道线索，自动判定不可靠
        out_t = blend_temporal_segments([seg], [(0, 4)], total_frames=4, overlap=1, layout="tchw")
        assert out_t.shape == (4, 8, 2, 2)
        out_c = blend_temporal_segments([seg], [(0, 4)], total_frames=4, overlap=1, layout="cthw")
        assert out_c.shape == (4, 4, 2, 2)

    def test_invalid_layout_raises(self):
        with pytest.raises(ValueError):
            blend_temporal_segments([torch.zeros(8, 3, 2, 2)], [(0, 8)], 8, 1, layout="hwct")


class TestOverlapWeights:
    """P0-1：段间重叠权重纯函数（hann / linear 回退）。"""

    def test_linear_matches_legacy_formula(self):
        # 旧版 _video_pipeline 内联公式 (i+1)/(n+1) 必须逐位保留，供回退使用
        n = 8
        assert linear_overlap_weights(n) == [pytest.approx((i + 1) / (n + 1)) for i in range(n)]
        assert linear_overlap_weights(1) == [0.5]

    def test_empty_for_non_positive_n(self):
        assert linear_overlap_weights(0) == []
        assert hann_overlap_weights(0) == []
        assert overlap_weights(-1) == []

    def test_hann_endpoints_and_unit_interval(self):
        w = hann_overlap_weights(8)
        assert len(w) == 8
        assert w[0] == pytest.approx(0.0)
        assert w[-1] == pytest.approx(1.0)
        assert all(0.0 <= v <= 1.0 for v in w)

    def test_hann_monotonic_non_decreasing(self):
        w = hann_overlap_weights(16)
        assert all(b >= a for a, b in zip(w, w[1:], strict=False))

    def test_hann_symmetric_complement(self):
        # 任意一对对称帧的权重互补（前段占比 + 当前段占比 = 1）
        for n in (3, 8, 15):
            w = hann_overlap_weights(n)
            for i in range(n):
                assert w[i] + w[n - 1 - i] == pytest.approx(1.0)

    def test_hann_plateau_edges_are_exclusive(self):
        # n=8、plateau=1/3：两端各 3 帧平台（0 / 1），中间 2 帧过渡
        w = hann_overlap_weights(8, plateau=1.0 / 3.0)
        assert w[:3] == pytest.approx([0.0, 0.0, 0.0])
        assert w[-3:] == pytest.approx([1.0, 1.0, 1.0])
        assert 0.0 < w[3] < 0.5 < w[4] < 1.0

    def test_hann_zero_plateau_is_pure_cosine(self):
        w = hann_overlap_weights(5, plateau=0.0)
        expected = [0.5 - 0.5 * math.cos(math.pi * i / 4) for i in range(5)]
        assert w == pytest.approx(expected)

    def test_short_overlap_falls_back_to_linear(self):
        assert hann_overlap_weights(1) == linear_overlap_weights(1)
        assert hann_overlap_weights(2) == linear_overlap_weights(2)

    def test_dispatch_hann_and_linear(self):
        n = 8
        assert overlap_weights(n, "hann") == hann_overlap_weights(n)
        assert overlap_weights(n, "linear") == linear_overlap_weights(n)
        # 大小写/空白容错
        assert overlap_weights(n, " HANN ") == hann_overlap_weights(n)

    def test_dispatch_unknown_falls_back_to_linear(self):
        n = 8
        assert overlap_weights(n, "") == linear_overlap_weights(n)
        assert overlap_weights(n, "triangle") == linear_overlap_weights(n)
