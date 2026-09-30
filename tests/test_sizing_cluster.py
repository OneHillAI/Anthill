"""#661 Tier 5: distributed local pooling capacity math (anthill.hosting.sizing.cluster_max_params_b /
model_fits_cluster). The network-efficiency discount is an unvalidated placeholder (no rpc-server
benchmark exists in this codebase), only anchor-checked against the roadmap's own "~150B for 2-4
machines" framing - these tests ground the exact arithmetic, not a claim of measured accuracy."""

from anthill.hosting import sizing


def test_four_64gb_apple_nodes_lands_near_roadmap_anchor():
    # usable_gb(64, apple, context_k=8, concurrency=1) = 64*0.66 - 0.8(kv) - 2.5(runner) = 38.94GB.
    # Four nodes sum to 155.76GB raw; at the 0.5 efficiency discount that is 77.88 effective GB,
    # / _GB_PER_B_Q4 (0.55) = 141.6... - within shouting distance of the roadmap's "~150B".
    s = sizing.cluster_max_params_b([64.0, 64.0, 64.0, 64.0], kind="apple")
    assert s.node_count == 4
    assert abs(s.raw_sum_gb - 155.76) < 0.01
    assert abs(s.max_params_b - 141.6) < 0.1
    assert "Estimate only" in s.note
    assert "especially unreliable" not in s.note  # 4 nodes is within the anchor-checked range


def test_two_nodes_smaller_estimate_than_four():
    two = sizing.cluster_max_params_b([64.0, 64.0], kind="apple")
    four = sizing.cluster_max_params_b([64.0, 64.0, 64.0, 64.0], kind="apple")
    assert two.max_params_b < four.max_params_b


def test_below_min_nodes_flagged_especially_unreliable():
    s = sizing.cluster_max_params_b([64.0], kind="apple")
    assert s.node_count == 1
    assert "especially unreliable" in s.note


def test_above_max_nodes_flagged_especially_unreliable():
    s = sizing.cluster_max_params_b([64.0] * 5, kind="apple")
    assert s.node_count == 5
    assert "especially unreliable" in s.note


def test_empty_node_list_is_zero_not_an_error():
    s = sizing.cluster_max_params_b([], kind="apple")
    assert s.node_count == 0
    assert s.max_params_b == 0.0
    assert s.raw_sum_gb == 0.0


def test_model_fits_cluster_true_for_small_model():
    assert sizing.model_fits_cluster(1.0, [64.0, 64.0, 64.0, 64.0], kind="apple") is True


def test_model_fits_cluster_false_for_huge_model():
    assert sizing.model_fits_cluster(9999.0, [64.0, 64.0], kind="apple") is False


def test_model_fits_cluster_boundary_matches_cluster_max_params_b():
    s = sizing.cluster_max_params_b([64.0, 64.0, 64.0], kind="apple")
    assert sizing.model_fits_cluster(s.max_params_b, [64.0, 64.0, 64.0], kind="apple") is True
    assert (
        sizing.model_fits_cluster(s.max_params_b + 1.0, [64.0, 64.0, 64.0], kind="apple") is False
    )
