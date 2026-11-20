# AlgoFarmBT

Backtesting application and native historical-data preparation. Install sibling `algoLibs` plus this project's requirements. Configure `MARKET_DATA_READ_URL` privately for hosted `historical_data`; the saved SSM connection uses local port 5433.

## Inspect and prepare data

Run from this project:

```sh
python -m algoFarmBT.data catalog
python -m algoFarmBT.data read --request request.json
python -m algoFarmBT.data prepare --request request.json --output /new/snapshot
python -m algoFarmBT.data read --request request.json --snapshot /new/snapshot
```

Example `request.json`:

```json
{"dataset":"public.md_bar","start":"2026-09-04T00:00:00Z","end":"2026-09-04T01:00:00Z","filters":{"instrument_id":1,"venue_id":1,"interval":"1m","bar_type":"trade"},"batch_size":1000}
```

All hosted table/view families retain native fields. JSON output tags Decimal and UTC values to preserve precision. Reference tables omit time bounds. Prepared exports freeze full rows and instrument/source metadata; offline reads verify hashes. See [shared data documentation](../algoLibs/algoLibs/market_data/README.md) for semantics, coverage limits and explicit write migrations.

## Prepared cash-spot research

The prepared runner supports a single quote-currency account with long-only fractional spot holdings, sparse desired portfolio weights, explicit costs and a complete fill/accounting history. Use `Backtest.from_prepared(spec, source, strategy=MomentumStrategy(recipe)).run()` or `Backtest.run_prepared(spec, source, targets)`. These extend the existing `Backtest` runner and `BacktestPortfolio`: all fills are existing `Trade` objects posted through `Portfolio.update_holdings`, and positions are existing `Holding` objects. The prepared-bar profile adds chronology, target sizing and spot precision rules to those classes; there is no separate simulator or account ledger. Hosted/live clients are unnecessary for offline runs.

`research_bar_next_open_v1` publishes the preceding completed bar at boundary T, accepts a target known at T, then executes at the open at T under an explicit zero-latency, unconstrained-liquidity approximation. Future close/volume values cannot affect that opening fill. Quantities use integer lots; adverse prices respect ticks; buy notionals and fees round up to currency precision, sell proceeds round down. Targets share predecision equity, reductions precede increases, and fee-inclusive buy budgets are scaled together when cash is insufficient. Repeated weights do not add exposure; omitted symbols retain holdings. Set `rebalance: true` to request adjustment at an unchanged weight. End positions remain open and marked.

Qualify archived database extractions offline, then run supplied targets:

```sh
python -m algoFarmBT.backtests.execute_momentum_backtest qualify --input comparison-directory --output new-prices
python -m algoFarmBT.backtests.execute_momentum_backtest run --snapshot new-prices --spec run.json --targets targets.json --output new-result
```

Qualification requires a hashed native extraction, original Binance spot monthly hourly archives and published checksums. It compares timestamp, identity and selected native fields exactly. Quote-volume discrepancies are recorded and excluded; exported observations contain opening and closing prices only. No hosted repair occurs. Reader hashes and full hourly coverage are checked, with no forward filling. The CLI saves the resolved configuration, targets, fills, orders/rejections, cost basis, equity, source/code hashes and environment versions in a new result directory.

`run.json` supplies `start`, `end`, `interval_seconds`, `initial_cash`, `cash_quantum`, `quote_currency`, `assumptions`, `execution_profile`, and `rules`. Each rule supplies `symbol`, `lot_size`, `tick_size`, `min_notional`, `fee_rate`, `fixed_fee` and `slippage_bps` as decimal strings. Targets are a JSON list of objects such as `{"time":"2026-08-01T00:00:00Z","weights":{"BTCUSDT":"0.2","ETHUSDT":"0.2","SOLUSDT":"0.2"}}`. Every cost and precision assumption must be supplied. Data and code identities are resolved from actual files.

The momentum command instantiates `MomentumStrategy(CoreTradingStrategy)` in the existing strategies package. Its `apply_indicators` and `generate_signal` hooks use the shared EMA indicator and emit target weights into the existing backtest/portfolio lifecycle:

```sh
python -m algoFarmBT.backtests.execute_momentum_backtest momentum --snapshot prices --spec run.json --recipe recipe.json --output new-result --trace
```

The known six horizons, their weights, the 0.50 volatility target and ±2 clipping are recorded defaults. Every unspecified formula is a required recipe choice: histogram/signal span, recursive EMA initialisation, return and volatility estimator, blend, annualisation, normalisation units, smoothing, volatility floor, warmup, IV/risk inputs, budgets/caps and rebalancing. State persists across batches. Forecasts never use full-sample calibration. Weight intents round down to 24 decimal places before entering the simulator.

Enabled IV, regime or circuit-breaker inputs require `--auxiliary observations.json`, containing `symbol`, `field`, `observed_at`, `available_at`, `value`, `units` and `source_id`. Values join backward by availability; later arrivals of older observations do not replace newer state. IV units must be `annual_decimal_volatility`; multipliers use `dimensionless`. Missing/stale inputs fail. `disabled_ablation` must be selected explicitly to omit an adjustment. Regime/breaker definitions remain strategy inputs; no hidden rule is invented.

Every run also saves `report.xlsx` through the existing `Backtest.generate_report` exporter. Its `Run Summary` shares the canonical metrics used by `report.json`; existing `Summary Stats`, `Current Holdings` and `Trade Log` sheets remain available. Period summaries use opening equity plus completed closes in `(start, end]`, carry boundary returns, show actual UTC bounds, and flag partial periods or missing samples. Full-run drawdown appears separately from period drawdown. Flat/insufficient-sample Sharpe values are N/A. Annualisation is explicit (8,760 hours/year in the approved recipe); manual target runs without a convention leave annualised metrics undefined. Excel uses typed numeric/date cells with UTC documented; JSON retains exact Decimal values. Workbook bytes and hashes are deterministic for saved-input replay.

Normal runs now also save `research.json` (`backtest-research-v1`). The same completed payload drives the workbook's `Summary`, `Performance` and `Run details` views, with visible `Chart data` ranges and native equity, drawdown and rolling-risk charts. Daily/monthly bounds, partial periods, worst periods and drawdown excursions have separate tables. Daily risk uses only fully observed midnight-to-midnight UTC days, a 365-day calendar, zero risk-free return and zero downside target; the downside denominator includes every eligible day. Full trading P&L and separately labelled hourly/bar statistics retain partial days and initial costs. The report shows risk sample bounds and excluded-period P&L because these can change the interpretation of the daily statistics. Historical 95% expected shortfall requires at least 100 complete days and uses exactly 5% tail mass with a fractional boundary observation; tail counts and unavailable estimates remain visible. Rolling risk requires 30 contiguous complete days.

Cash and initial buy-and-hold references use the same data, bounds, execution costs, precision and end policy. Manual target runs default to an equal-weight risky basket; momentum retains its declared recipe weight caps. Add `--report-config report-config.json` to either `run` or `momentum` to declare a fixed risky-plus-cash reference before evaluation, for example `{"fixed_reference_weights":{"BTCUSDT":"0.1","ETHUSDT":"0.1","SOLUSDT":"0.1"},"research_record":{"designation":"development","hypotheses":["Predeclared research question"],"parameter_changes":[],"related_runs":[]}}`. Every configured instrument needs a weight, with total risky weight strictly between zero and one; the residual remains cash, and no observed-risk rescaling occurs. No investor fee schedule is assumed.

Run details retain resolved rules, recipe, disabled adjustments, coverage/warmup, actual imported source identity and package versions. Fill-based cash, quantity, basis, realised P&L and fee reconstruction is executed by the report; independent venue-rule checks are unavailable unless supplied as evidence. Output checksums are published after export in the existing manifest, avoiding a circular report self-hash. The minimal `research-record-v1` record distinguishes development/test designation and leaves unknown prior trial history explicit. `Backtest.performance_report()` retains its canonical base keys. Optional analytics use `Backtest.research_report(context=..., benchmarks=..., sections=...)` and `generate_report(..., research=payload, extra_frames=...)`; absent optional evidence is identified without hiding calculation failures.

The output includes per-asset attribution, fees, turnover, completed-close drawdown and returns, explicit annualisation conventions, and matching cash/buy-and-hold benchmarks with the same execution/cost rules. Undefined Sharpe ratios remain undefined. `--trace` retains calculation diagnostics; otherwise they are not saved. Recipes, auxiliary inputs and generated targets are frozen and hashed alongside source code.

The user-approved research baseline is [`examples/momentum.baseline.json`](examples/momentum.baseline.json), with its run settings in [`examples/momentum.baseline.run.json`](examples/momentum.baseline.run.json). It uses explicit no-IV/regime/breaker ablations and long-only capped budgets. The earlier `*.proposed.json` files are retained as design history. This baseline does not claim exact reproduction of the incomplete screenshot.

The verified March–August 2026 snapshot leaves 2,560 hours for warmup and 1,856 for execution. The approved baseline was run from 15 June 2026 16:00 UTC to 1 September 2026 00:00 UTC (16 June 00:00 to 1 September 08:00 Singapore time). With the declared research costs and precision rules, 10,000 USDT ended at 9,863.78: -1.36% net return, 8.97% maximum completed-close drawdown, and 394.29 USDT fees across 2,788 fills. Equal-weight buy-and-hold returned +28.95% with substantially greater average exposure. The data, recipe, targets, journals, source bundle, benchmarks and independent verification are retained under `/Users/clawdbot/Documents/dev/research-data/btc-eth-sol-momentum-baseline-v1`; `report.html` contains the charts and CSV links.

Replay from the archived source and saved run inputs produced an identical manifest. An independent integer-cash/lot verifier reconciled all 1,857 equity observations, fees, cost basis and positions. Earlier stored history has demonstrated spot/futures identity collisions and remains excluded; longer spot-history work requires separate reconstruction and qualification.

The corrected execution through the existing project classes has been rerun and independently reconciled under `/Users/clawdbot/Documents/dev/research-data/btc-eth-sol-momentum-project-integration-v1`. Every fill and equity observation matches the earlier baseline, and replay from its archived corrected source produces an identical manifest. See [integration correction](../../algoDocs/backtesting/project-integration-correction.md) for the class changes and validation.

## Quote runner

`Backtest(..., data_source=QuoteDataSource(...))` injects actual quotes without constructing unrelated clients. The moving-average execution module accepts explicit dates, symbols and a quote dataset. Warmup rows are used only for features; prices are no longer repeatedly rescaled and missing bid/ask observations are not fabricated.

Native bar/derivative access is available for research. The legacy equity execution/accounting model does not become a validated crypto derivatives simulator merely by changing storage. A bar execution profile and each product's economics remain separate increments. The retained US ETF option replay helper requires supplied point-in-time listing sources and actual stored option quotes; hosted crypto data does not imply US ETF coverage.

## Fund-style reporting views

Normal prepared runs now include Summary, Performance, Assets, Trades, Signals and Run details, with supporting source tables. `research.json` is the canonical combined payload; base `report.json` remains available. Assets/Trades use retained shared-ledger evidence and preserve marking residuals. Completed position episodes are distinct from fills and from open episodes. Signals contains explicit unavailable sections when no retained trace was requested; use `--trace` for exact intermediate forecasts and execution audit evidence. Diagnostics do not rerun or change the strategy.

Daily risk uses complete UTC days with 365-day annualisation and declared zero risk-free/downside-target conventions. Full-run P&L and retained hourly statistics include partial periods; their different sample bounds are displayed. Historical 95% expected shortfall requires at least 100 complete days under the documented research policy, so the current 77-complete-day baseline reports it unavailable. This is a sample eligibility convention, not an allocation threshold. Fixed risky-plus-cash references are supported via explicit report configuration and are never fitted to the evaluated full sample.

## Controlled robustness comparisons

Run a bounded, predeclared set of actual Backtest reruns:

```bash
python -m algoFarmBT.backtests.execute_momentum_backtest compare --snapshot qualified-prices --spec run.json --recipe recipe.json --cases examples/momentum.robustness.json --output robustness-run
```

The plan starts with an unchanged baseline and contains at most 32 named cases. Supported interventions are fee multipliers (both percentage and fixed fees), adverse-slippage multipliers, execution delays of 0–168 complete bars, and explicit symbol/horizon exclusions. No optimiser or automatic selection is included. All cases retain the same frozen prices, run bounds, warmup, capital and original benchmark weights. Excluded assets remain in the marked universe with zero target budget; their capital stays in cash. Excluded horizon contributions become zero before composite/allocation calculations; the remaining original weights are not rescaled.

Delayed decisions retain known-at times and execute at the later bar open. Executions at or beyond the end boundary are censored and saved in each run's `target-timing.jsonl`; end holdings remain marked, without a forced liquidation. `--trace` additionally retains full intermediate calculation/execution diagnostics.

The output freezes common prices, plan, recipe and run configuration once, saves every case and its manifest/run ID (including a failure receipt when a case fails), and writes matching `comparison.json` and `comparison.xlsx`. Summary/Performance compare actual net results, risk, exposure, costs, turnover and execution effects, with relative links to each case report. Undefined values remain unavailable. A joint fee/slippage sign-change bracket uses observed rerun endpoints only; the separate same-fill fee diagnostic is not a zero-cost rerun. Marginal ablation differences are not additive P&L contributions.

Replay using the archived `source.zip`, `common/prices`, `common/spec.json`, `common/recipe.json` and `common/plan.json` (plus `common/auxiliary.json` when present). Existing outputs are never overwritten. Price inputs are copied and verified before any case runs, and the suite manifest hashes every retained artifact. Results on previously inspected history remain development research.
