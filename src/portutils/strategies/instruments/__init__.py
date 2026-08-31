"""
INSTRUMENTS — what a strategy can hold, and what each holding is worth.

The stage functions in ``strategies/`` decide SIZE; the modules in here decide VALUE. Keeping
them apart is what lets an option rule be tested against a known price without a market data
connection, and what lets the pricing be swapped (synthetic surface now, a real chain later)
without touching a single rule.

Current contents:

* ``vol.py``     — ``synthetic_iv_surface``: implied vol as a function of strike and tenor.
* ``pricing.py`` — ``black_scholes_put``: the European put that consumes that vol.

Planned by 16-02: ``options.py`` — ``SyntheticContract`` and ``OptionLeg`` (delta, theta, and a
``price()`` that calls ``pricing.black_scholes_put``).
"""
