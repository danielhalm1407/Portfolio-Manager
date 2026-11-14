"""Offline tests for portutils.strategies.attribution (2026-11-13)."""

import numpy as np
import pandas as pd
import pytest

from portutils.strategies.attribution import (FACTORS, attribute_leg, put_value,
                                              shapley_put_attribution)


def test_shapley_contributions_sum_exactly_to_the_value_change():
    # A deep put through a fall with a vol spike and most of its life gone: every factor moves.
    start = {"spot": 100.0, "base": 0.12, "tau": 63 / 252}
    end = {"spot": 91.0, "base": 0.25, "tau": 5 / 252}
    parts = shapley_put_attribution(start, end, strike=80.0)
    v0 = put_value(100.0, 80.0, start["tau"], 0.12, 100.0)[0]
    v1 = put_value(91.0, 80.0, end["tau"], 0.25, 91.0)[0]
    assert sum(parts.values()) == pytest.approx(v1 - v0, abs=1e-12)


def test_signs_match_the_economics():
    # Spot down and vol up both help a put; less time hurts it; a falling spot slides a fixed
    # strike up the smirk to a lower vol, which also hurts.
    parts = shapley_put_attribution({"spot": 100.0, "base": 0.12, "tau": 0.25},
                                    {"spot": 91.0, "base": 0.25, "tau": 0.02}, strike=80.0)
    assert parts["spot"] > 0
    assert parts["vol_level"] > 0
    assert parts["time"] < 0
    assert parts["skew_slide"] < 0


def test_attribute_leg_rows_reconcile_and_start_at_one():
    idx = pd.bdate_range("2026-01-02", periods=70)
    spot = pd.Series(np.linspace(100, 92, 70), index=idx)
    iv = pd.Series(np.linspace(0.12, 0.22, 70), index=idx)
    out = attribute_leg(spot, iv, strike=90.0, open_ts=idx[1], expiry_bar=64)
    # The opening bar has no change to attribute.
    assert out["multiple"].iloc[0] == pytest.approx(1.0)
    recon = 1.0 + out[list(FACTORS)].sum(axis=1)
    assert np.allclose(recon, out["multiple"], atol=1e-10)
    # The last row is the bar before expiry.
    assert out["bars_left"].iloc[-1] == 1


def test_fill_values_substitutes_and_raises_on_leftovers():
    from pipelines.build_report import fill_values
    assert fill_values("SPY fell {{fall}}.", {"fall": "9.1%"}) == "SPY fell 9.1%."
    # Ordinary braces are prose, not tokens.
    assert fill_values("a {b} c", {}) == "a {b} c"
    with pytest.raises(ValueError, match="missing"):
        fill_values("{{missing}}", {"other": 1})


def test_moneyness_styles_blind_faded_and_triggers_dashed():
    from pipelines.option_grid_figures import BLIND_OPACITY, moneyness_styles
    s = moneyness_styles(["0.85x/blind", "0.85x/dd10%", "0.85x/dd8%", "0.95x/dd10%"])
    assert s["0.85x/blind"]["colour"] == s["0.85x/dd10%"]["colour"]
    assert s["0.85x/blind"]["opacity"] == BLIND_OPACITY
    assert s["0.85x/dd10%"]["dash"] == "solid" and s["0.85x/dd8%"]["dash"] == "dash"
    # A strike drawn once keeps full opacity and a solid line.
    assert s["0.95x/dd10%"] == {**s["0.95x/dd10%"], "opacity": 1.0, "dash": "solid"}
    # Two labels identical in strike AND trigger cannot be styled apart: fall back.
    assert moneyness_styles(["0.90x/dd10% (a)", "0.90x/dd10% (b)"]) is None


def test_split_sections_places_figure_at_end_of_its_own_subsection():
    from pipelines.build_report import _split_sections
    html = "<h1>Top</h1><p>a</p><h2>Child one</h2><p>b</p><h3>Leaf</h3><p>c</p><h2>Child two</h2>"
    out = _split_sections(html, {"Leaf": "fig"}, levels=(1, 2, 3))
    # The figure follows the leaf's own text, before the next heading of any level.
    assert out.index("<p>c</p>") < out.index("<!--FIGURE:fig-->") < out.index("<h2>Child two")
    # The default (h2 only) keeps the old behaviour: an h3 key is not a section, so it raises.
    with pytest.raises(KeyError):
        _split_sections(html, {"Leaf": "fig"})


def test_add_contents_nests_every_level_and_balances_tags():
    from pipelines.build_report import add_contents
    body = ("<h1>A</h1><h2>A1</h2><h3>A1a</h3><h2>A2</h2><h1>B</h1><h2>B1</h2>")
    out = add_contents(body, top=1, depth=6)
    toc = out[:out.index("</nav>")]
    assert toc.count("<ul") == toc.count("</ul>") and toc.count("<li>") == toc.count("</li>") == 6
    # Back links sit under each top-level heading only.
    assert out.count('<p class="back">') == 4  # two under h1s, two closing their sections
