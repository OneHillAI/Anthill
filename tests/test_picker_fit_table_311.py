"""The picker's default is the largest model the catalogue marks "fits" for the machine's memory tier (#311)."""

from anthill.hosting import sizing
from anthill.hosting.sizing import Model


def _m(name, params, fit, **kw):
    return Model(
        name, params, "x/y", f"{name}:t", fit=tuple(fit.items()), family=kw.pop("family", "F"), **kw
    )


CAT = (
    _m("big-tight", 12, {"16": "tight", "8": "too_large"}),
    _m("mid-fits", 9, {"16": "fits", "8": "too_large"}),
    _m("small-fits", 4, {"16": "fits", "8": "fits"}),
    _m("tie-later", 9, {"16": "fits", "8": "too_large"}),
)


def test_default_is_the_largest_fits_never_tight():
    r = sizing.recommend(16, kind="apple", concurrency=1, catalog=CAT)
    assert (
        r.recommended.name == "mid-fits"
    )  # 12B is "tight": not the default; the 9B tie goes to catalogue order
    assert {f.model.name: f.fits for f in r.fits}[
        "big-tight"
    ] is True  # still offered, not "too large"


def test_a_smaller_tier_and_a_machine_below_the_smallest_tier():
    assert (
        sizing.recommend(8, kind="apple", concurrency=1, catalog=CAT).recommended.name
        == "small-fits"
    )
    assert (
        sizing.fit_tier_gb(7) is None
        and sizing.fit_tier_gb(18) == 16
        and sizing.fit_tier_gb(200) == 64
    )


def test_with_no_verdicts_at_all_the_ceiling_rule_is_used():
    no_fit = (sizing.Model("plain", 6, "x/y", "plain:t"),)
    assert (
        sizing.recommend(16, kind="apple", concurrency=1, catalog=no_fit).recommended.name
        == "plain"
    )


def test_a_row_without_a_verdict_is_never_the_default_when_others_have_one():
    mixed = (_m("checked", 4, {"16": "fits"}), sizing.Model("gated-70b", 70, "x/y", "gated:t"))
    r = sizing.recommend(16, kind="apple", concurrency=1, catalog=mixed)
    assert (
        r.recommended.name == "checked"
    )  # tier 16 has a verdict: the unchecked 70B is listed, not chosen


def test_a_box_far_above_the_largest_tier_keeps_the_ceiling_rule(monkeypatch):
    monkeypatch.setattr(sizing, "is_usable_speed", lambda m, *, kind: True)  # not about speed
    big = (_m("huge", 120, {"64": "too_large"}), _m("mid", 30, {"64": "fits"}))
    assert sizing.recommend(64, kind="apple", concurrency=1, catalog=big).recommended.name == "mid"
    assert (
        sizing.recommend(128, kind="apple", concurrency=1, catalog=big).recommended.name == "huge"
    )


def test_other_shapes_of_box_keep_the_ceiling_rule():
    # a GPU box and a multi-stream box do not read the table (it was computed for one user on unified memory)
    assert sizing.recommend(16, kind="gpu", concurrency=1, catalog=CAT).recommended is not None
    multi = sizing.recommend(16, kind="apple", concurrency=4, catalog=CAT)
    assert multi.recommended is not None


def test_a_bad_fit_in_a_row_is_dropped():
    assert sizing._fit_from_row(
        {"8": "fits", "16": "not_computed", "7": "fits", "24": "maybe"}
    ) == (("8", "fits"),)
    assert sizing._fit_from_row("junk") == ()


def test_family_pick_uses_the_table():
    picks = {p.family: p for p in sizing.recommend_by_family(16, catalog=CAT, families=("F",))}
    assert picks["F"].recommended.name == "mid-fits"
