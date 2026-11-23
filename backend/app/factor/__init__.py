"""Factor mining: does this observable separate future returns?

The package deliberately has no eager submodule imports: ``evaluate`` depends on
the backtest layer, which in turn depends on the screener, so importing anything
here at package level would close a cycle.
"""
