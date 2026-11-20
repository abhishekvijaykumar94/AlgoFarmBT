# Chronological validation

Use the existing CLI: `python -m algoFarmBT.backtests.execute_momentum_backtest validate --snapshot prices --spec run.json --recipe examples/momentum.baseline.json --plan examples/momentum.validation.json --output validation-run`. Each declared fold uses the existing related-run receipts and actual Backtest/BacktestPortfolio/Portfolio accounting. No fitting, optimiser, alternative simulator or automatic recipe selection is provided.

## Chronology and evidence

The plan pins one recipe SHA256 and a frozen-recipe selection rule. Each fold declares development, calibration, selection and test times. Test windows must be ordered/non-overlapping. Eligible test selection must precede its first decision; development/calibration cannot follow that selection. The strategy's existing causal feature/auxiliary timing rules remain authoritative. Costs, recipe and input snapshot are retained with each fold. Failures remain receipts; an error is not silently excluded.

The capital policy is `independent_reset_accounts`: each fold starts a separate account with the configured initial capital and retains end positions at their final marks. Paths are segmented and rebased within each fold. No continuous capital series or compounded account is inferred across resets or gaps. Eligible paths are identified separately from development paths.

Eligibility also requires a declared inspection cutoff. The project exposure registry marks the BTC/ETH/SOL March–August 2026 data as already inspected, regardless of a renamed snapshot identity. The supplied real-data plan is therefore development research. Timestamp/inspection declarations are attestations, not cryptographic proof of no previous inspection. No actual unseen history is supplied by the example.

## Uncertainty conventions and references

Only complete UTC daily simple returns enter statistics; partial/missing days remain in fold accounting and break resampling segments. Fold boundaries always split segments. Annualisation uses365calendar days and risk-free return0. Statistical diagnostics use binary64; the shared ledger retains exact financial arithmetic.

The stationary bootstrap restarts with probability1/block_days and otherwise advances circularly within the same segment. The seed, expected block length, repetitions and confidence are declared. It assumes weak stationarity within segments, preserves within-block dependence and never crosses gaps/folds. Percentile intervals use linear interpolation. Eligibility requires30days,5expected blocks and2blocks per segment: a stated research policy, not a guarantee of coverage. An undefined bootstrap Sharpe invalidates its interval rather than selecting only valid draws. Reference: [Politis and Romano1994](https://users.ssc.wisc.edu/~bhansen/718/Politis%20Romano.pdf).

PSR and minimum track record length are conditional on an explicitly declared IID daily-return assumption and at least30observations with positive sample variance. They use sample standard deviation(n-1), standardized central skewness and Pearson kurtosis, with nonannualised daily Sharpe. MinTRL is rounded up to observations and unavailable when observed Sharpe does not exceed the reference. Dependence-aware bootstrap evidence remains separate; these quantities are not probabilities of future profit. Reference: [Bailey and Lopez de Prado2012, equations11/13](https://www.davidhbailey.com/dhbpapers/sharpe-frontier.pdf).

DSR additionally needs complete prior trial records, daily trial Sharpes, temporal completeness and a documented effective independent count. Raw correlated folds/signals/episodes are never used as independent trials. Supported count treatments are explicitly declared independent trials or an externally estimated effective count with evidence; no count is guessed from unknown history. The zero-mean null and sample cross-trial variance feed the extreme-value threshold approximation. Unknown/inadequate history remains unavailable. Reference: [Bailey and Lopez de Prado2014, equation2 and appendix3](https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf).

## Saved output and replay

`validation.json` and `validation.xlsx` share completed evidence. Summary leads with eligible observations; Performance separates eligible and exploratory statistics. Folds link to full original run reports; OOS segments contains eligible paths only. The source archive, common prices/spec/recipe/plan and every fold artifact are pinned by the suite manifest. Run the archived CLI with these common inputs into a new destination to reproduce results. No database or container is needed for this saved-input workflow.

DSR is unavailable below two effective independent trials, where the extreme-value approximation is unsuitable.

For recipes using supplied IV, regime or breaker observations, pass `--auxiliary path/to/observations.json`. Its bytes are frozen once as `common/auxiliary.json`, forwarded to each existing momentum run, included in the manifest and pinned by `auxiliary_sha256`. Replay must pass that frozen path. The existing strategy source/freshness/availability checks remain authoritative; future inputs are never backfilled.
