# Liquidity and capital-size research

The existing `Backtest` / `BacktestPortfolio` / `Portfolio.update_holdings` path remains the execution and accounting owner. `capacity` calls the same `run_momentum` and `run_related_cases` workflow at each declared capital size; it never scales a 10,000 USDT result. Each account, including its benchmarks, starts fresh and reruns fee-aware sizing, rounding, minimums and the shared ledger.

## Evidence before execution

```
python -m algoFarmBT.backtests.execute_momentum_backtest qualify-liquidity \
  --input comparison-directory --snapshot qualified-prices \
  --availability-lag-seconds 2 --output qualified-liquidity

python -m algoFarmBT.backtests.execute_momentum_backtest capacity \
  --snapshot qualified-prices --liquidity qualified-liquidity \
  --spec run.json --recipe examples/momentum.baseline.json \
  --plan examples/momentum.capacity.json --output capacity-results
```

`--auxiliary` is optional and is frozen and forwarded to all cases when supplied. Single `run` and `momentum` commands also accept `--liquidity` and `--liquidity-policy` (a JSON file with the three explicit policy fields). A constrained spec must select `research_lagged_volume_v1`. The original `research_bar_next_open_v1` requires no liquidity and retains its behavior.

Qualification is separate from the price-only projection. It checks the raw native extraction against the price snapshot's native SHA256, selected instrument and venue IDs, and the same checksum-verified spot monthly archives. Base volume and trade count are independently compared. Quote volume is admitted row by row only when it matches; a mismatch becomes unavailable, never a fabricated notional. Per-symbol files and the manifest are hashed. Readers reject inconsistent units, identities, coverage, source IDs, hashes and availability timestamps before simulation. Qualified coverage is bounded to the saved inputs; no database repair occurs.

The [Binance archive specification](https://github.com/binance/binance-public-data) defines base volume, quote volume, count and the post-2025 microsecond timestamp convention. The [spot stream specification](https://github.com/binance/binance-spot-api-docs/blob/master/web-socket-streams.md) distinguishes event time, bar clocks and closed-bar fields. Public archives are published later and do **not** prove that this extraction captured a closed bar at its event-time close. Thus availability is explicitly **assumed bar end plus the configured lag**, not verified historical capture latency. A two-second lag at an hourly opening excludes the just-closed bar and uses an older eligible bar.

## The narrow research profile

At opening time `t`, select the latest observation whose `available_at <= t` and `end <= t`. Reject a reference older than `max_age_seconds` measured from bar end. Missing, stale and zero volume produce separate unfilled reasons. The cap in base units is `floor(max_participation * reference_base_volume / lot_size)` lots, minus quantity already filled for that symbol and opening. A cap is shared across buys and sells at that opening. Cash, lots, minimum notional, ticks and fees are still enforced by the existing ledger. The unfilled remainder is cancelled at bar end; it is not queued. A subsequent explicit rebalance can request it again.

The configured impact quote is deliberately conservative and simple:

```
impact_bps = impact_coefficient_bps * sqrt(max_participation)
buy  = adverse_tick_round(open * (1 + (rule_slippage_bps + impact_bps) / 10000))
sell = adverse_tick_round(open * (1 - (rule_slippage_bps + impact_bps) / 10000))
```

This is a constant quote at the **participation ceiling**, including for smaller fills. It is an assumed scenario coefficient, not a fitted square-root impact model at actual participation. The example's 1% cap and 100 bps coefficient imply an additional 10 bps adverse quote. The cap is an opening proxy based on older hourly volume, not evidence that a market order could access that liquidity. There is no order-book, intrabar scheduling, queue position or guaranteed fill realism.

Original requested quantity remains in order records. Liquidity audit records retain reference clocks, known volume, cap, requested and filled quantities, cancellation reasons and shortfall. Shortfall aggregates in USDT at opening prices, never by adding BTC, ETH and SOL units. The impact component is part of the existing modeled slippage and fill prices; it is not an extra cash charge. Realised current-bar volume is accessed only by completed-run reporting, labeled `ex_post_participation`, and never sizes an opening fill.

## Reading the output

`comparison.json` and the workbook share one completed payload. Summary compares net P&L/return, drawdown, fees, modeled adverse price costs and exposure. Performance compares daily risk, turnover and executions. Liquidity compares known and ex-post participation, missing references, shortfalls and impact. Coverage records field qualification, identity, period and assumed latency. Each case links to its full report and retained ledger; failed cases retain receipts. Run details declares the assumptions and sourcing limitations.

The group freezes price/liquidity snapshots, configuration, recipe, case plan and optional auxiliary observations. It archives the actual imported sources and hashes every published artifact. Repeating the command with those frozen inputs and sources reproduces the result and workbook bytes in the recorded environment.

**All constrained outputs are `scenario_only_not_certified`.** Historical spread/depth, actual capture latency, historical account fee tiers, exchange precision and empirical impact calibration are not established by these data. The example's rules are research assumptions. `certified_capacity` is unavailable: capital scenarios describe model behavior and do not establish maximum deployable capital. Losing runs, missing data and failed cases remain visible; there is no automatic winner selection.
