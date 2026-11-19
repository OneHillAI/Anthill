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


def test_rows_without_a_verdict_keep_the_ceiling_rule():
    no_fit = (sizing.Model("plain", 6, "x/y", "plain:t", intelligence=0.0),)
    assert (
        sizing.recommend(16, kind="apple", concurrency=1, catalog=no_fit).recommended.name
        == "plain"
    )
    gated = (_m("g", 6, {"8": "fits"}), sizing.Model("plain", 5, "x/y", "plain:t"))
    # tier 16 has no verdict for "g", so it falls back to the ceiling rule like "plain"
    assert sizing.recommend(16, kind="apple", concurrency=1, catalog=gated).recommended.name == "g"


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
