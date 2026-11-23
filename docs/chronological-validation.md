# Chronological validation

Use the existing CLI: `python -m algoFarmBT.backtests.execute_momentum_backtest validate --snapshot prices --spec run.json --recipe examples/momentum.baseline.json --plan examples/momentum.validation.json --output validation-run`. Each declared fold uses the existing related-run receipts and actual Backtest/BacktestPortfolio/Portfolio accounting. V1 plans run one frozen recipe; v2 plans also support the predeclared training selection described below.

## Chronology and evidence

The v1 plan pins one recipe SHA256 and a frozen-recipe selection rule. Each fold declares development, calibration, selection and test times. Test windows must be ordered/non-overlapping. Eligible test selection must precede its first decision; development/calibration cannot follow that selection. The strategy's existing causal feature/auxiliary timing rules remain authoritative. Costs, recipe and input snapshot are retained with each fold. Failures remain receipts; an error is not silently excluded.

The default capital policy is `independent_reset_accounts`: each fold starts a separate account with the configured initial capital and retains end positions at their final marks. Paths are segmented and rebased within each fold. No continuous capital series or compounded account is inferred across resets or gaps. Eligible paths are identified separately from development paths.

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

## Generated frozen-recipe schedules (issue #16)

The same `validate` action accepts `chronological-validation-v2` plans for
`holdout`, `rolling` and `expanding` schedules. Use
`examples/momentum.walk-forward.json` as a development example. Training ranges
are declarations in this slice: only test accounts execute, with the pinned
recipe. Candidate evaluation and selection belong to issue #17.

`bounds.start` is the first scored training opening and `bounds.end` is the
exclusive overall limit. All bounds must resolve to UTC hourly boundaries.
Window lengths, step and gap are integer hourly bar counts. Rolling advances
both training endpoints by the step; expanding keeps the first training start.
Holdout resolves one fold. Test intervals cannot overlap; a step larger than the
test length permits gaps. The final partial test policy is `drop`, `include` or
`reject`. A schedule must produce at least one fold and fit its explicit
`max_evaluations` budget (at most 32 test runs).

`restart_from_declared_warmup` restarts each test's features exactly
`recipe.warmup_bars` hours before its first decision. Training coverage is also
checked from its declared warmup start, although no fitting occurs. Warmup never
funds or scores an account. V1 explicit folds retain their original available
history initialization and independent-reset economics. Preview/frozen-definition
flags require v2; v1 explicit plans execute directly as before.

Preview first, then execute the frozen definition in a new directory:

```sh
python -m algoFarmBT.backtests.execute_momentum_backtest validate \
  --snapshot qualified-prices --duckdb prices.duckdb \
  --spec run.json --recipe examples/momentum.baseline.json \
  --plan examples/momentum.walk-forward.json --output preview --dry-run

python -m algoFarmBT.backtests.execute_momentum_backtest validate \
  --snapshot qualified-prices --duckdb prices.duckdb \
  --spec run.json --recipe examples/momentum.baseline.json \
  --plan examples/momentum.walk-forward.json --output validation-run \
  --resolved-plan preview/resolved-plan.json
```

Preview saves `resolved-plan.json` and `resolved-plan.sha256`: exact windows,
warmup, coverage, complete recipe, source qualification, code/input identities,
repository read evidence and execution options. Execution rechecks coverage and
compares the frozen definition before starting trials. Changed inputs or code
require an explicit new preview. The generated output retains the same resolved
definition alongside the usual JSON/XLSX, source archive and recursive manifest.
This is an integrity check, not a signature against deliberate rewriting of
both a definition and its checksum.

Both phases must receive the same optional `--auxiliary`, `--liquidity`,
`--liquidity-policy`, `--execution-delay-bars`, `--trace` and `--report-config`
inputs. Prices, auxiliary observations and qualified liquidity are frozen in
`common`; every child uses the ordinary momentum runner. The qualification ID
continues to identify the full verified price artifact; each bounded source
manifest separately records its actual query bounds and original qualified
bounds. Liquidity keeps its parent price identity and normal causal availability
checks. `data-access.json` records actual child repository requests. DuckDB errors
and qualification mismatches fail visibly without a file fallback. Database-free
portable replay is described below.

## Training recipe selection (issue #17)

The same v2 plan and `validate` command now support `selection_rule: training_rank`.
Use `examples/momentum.training-validation.json` for a three-fold historical
BTC/ETH/SOL development example. Preview with `--dry-run`, then execute with the
same inputs and `--resolved-plan preview/resolved-plan.json`, just as for the
frozen control. This does not add a model-fitting interface.

Declare `selection` with `rule: training_rank`, `objective: net_return` or
`annualised_daily_sharpe`, `minimum_complete_training_days` (a positive integer),
`tie_break: resolved_recipe_sha256_ascending`, `candidate_failure_policy: fail_fold`,
and 1–32 candidates. Each candidate has a unique safe `id` and `recipe_overrides`
object applied to the pinned base recipe. All fields, including defaults, are
resolved and frozen before evaluation. Duplicate resolved recipes are rejected.
Candidate overrides preserve symbol budgets, caps, allocation policy, rebalance
policy and annualisation units; capital, costs, precision and execution settings
come from the same run specification. Mathematical recipe parameters and warmup
may vary. Auxiliary observations must satisfy every candidate's ordinary source,
freshness and field-consumer contracts; failed trials remain visible.

There are at most 32 folds. The explicit total evaluation budget counts one run
per training candidate plus one test per fold, even when failure later prevents
a test. Training-selection budgets range from 1 to 1056; frozen controls retain
their 32-run bound. Preview freezes each candidate's exact training/test history
bounds and fully resolved recipe hash. Coverage uses the maximum required history,
while each child receives exactly its own recipe's declared warmup.

Every training candidate runs through the existing momentum runner, account and
canonical reporting, on identical scored training dates. Training queries stop at
the training cutoff, and only complete UTC daily observations count toward the
minimum. The score is the retained net-of-cost canonical metric, maximised without
recalculating it. Undefined metrics and insufficient samples cannot win. Ties use
the ascending complete-recipe SHA256. A runtime failure fails the fold under the
explicit `fail_fold` policy; all candidate trials still execute and are retained.
If no candidate is eligible, the fold retains its failure and runs no test.

The selected full recipe and `selection.json` are written before test execution.
Test features update causally, with frozen parameters and an independent account.
`training_data_cutoff` and `simulated_selection_cutoff` are historical data times;
`performed_at` is the actual UTC time selection was performed. The evidence is
labelled `historical_walk_forward`, not prospective research. Existing inspection
exclusions remain active, so the known March–August history stays development.

`common/candidates` retains every full recipe; each fold has `training/cases`,
its selected recipe, selection receipt, failures and the ordinary test `run`.
`validation.json` retains all training metrics and trial identities separately
from test-fold performance. The existing workbook exporter adds Training and
Selection sheets with the same scores and test-independent chronology. New
candidate trials invalidate a previously complete prior DSR history: original
history is preserved, the combined status is
`incomplete_after_training_selection`, and no effective independent trial count
is invented from correlated candidates or folds. PSR/bootstrap conventions remain
unchanged. Actual selection timestamps make whole-suite manifests differ across
reruns; selected recipes, targets and financial results remain reproducible.
Portable native-data replay is available with `--portable` as described below.

## Portable studies and verified replay (issue #18)

Add `--portable` to both the v2 preview and its execution. A native repository
(such as `--duckdb`) is required. The existing `DataRepository.prepare` route
freezes complete native market rows covering the union of required warmup,
training and test intervals for each exact instrument/venue identity. Overlapping
intervals merge; disjoint windows retain their gaps without exporting those gaps.
Only selected available instrument, venue, venue-listing and referenced asset
metadata are included. Ingestion logs and unrelated instruments are excluded.
Qualified price files keep their original full-artifact identity, and qualified
liquidity, auxiliary observations, parameters, source code and every trial remain
pinned by the study's recursive manifest.

The bundle uses the existing repository preparation option
`include_reference_metadata=False` with explicit reference requests. Other
preparation callers keep their default behavior of freezing all available
reference tables. This policy travels through the same preparation query object
and backend visitors; no SQL is added to the validation module.

Replay needs the saved study and a new output directory. Input overrides are
rejected whenever explicitly supplied, including a zero execution delay:

```sh
python -m algoFarmBT.backtests.execute_momentum_backtest validate \
  --replay saved-portable-study --output replayed-study
```

Replay verifies the manifest, complete file inventory and all file hashes before
starting. It reuses the frozen spec, recipes, plan, auxiliary/liquidity inputs and
execution settings, then constructs `SnapshotReader(...).repository` for the same
validation runner. All bars still enter `DataRepository.query`, `DataRequest.accept`
and the snapshot visitor. The original DuckDB service/file is unnecessary. The
replay also checks the source study again before publication, rejecting changes
made while replay was running. Both modes fail on an existing destination.

`replay-verification.json` records exact SHA256 agreement for retained account,
parameter, target, signal, trace and canonical financial-report files, plus
selection, score and trial-outcome agreement. The full deterministic suite-report
projection is also compared, including fold metrics, daily/path evidence,
aggregate statistics and eligibility. Actual selection times, physical run IDs
and backend-specific failure descriptions remain separate execution facts. Failed trials remain visible.
Actual selection times and physical backend provenance retain their true replay
values. Consequently, complete study/workbook/manifests need not have identical
hashes across executions; deterministic financial and signal artifacts do.
Original DuckDB provenance is retained separately under replay's `storage_input`.
Replay output is itself portable and carries a fresh recursive manifest.

Use the study's archived `source.zip` when the installed code differs from its
frozen code identity. Extract it safely into an isolated directory and run the
same CLI with that directory on `PYTHONPATH`, using compatible recorded runtime
dependencies. No executable runtime or credentials are bundled. Hash checks are
integrity checks, not signatures against deliberate replacement of all pins.

## Planned walk forward extension

Status: original design evaluated on 22 November 2026. The generated frozen-recipe
schedule, repository input and training-selection slices are implemented above.
Portable study/replay is also implemented above. The continuous-account section
remains future work.
The table below records the starting point for that design. Extend the
existing `validate` action and `run_validation` module. The execution/accounting
implementation remains `run_momentum` / `run_snapshot` -> `Backtest` ->
`BacktestPortfolio` / `Portfolio` -> `Trade` / `Holding`.

### Existing implementation and gaps

| Area | Existing behavior | Required extension |
| --- | --- | --- |
| Fold execution | `validation.py` calls `run_momentum` through `run_related_cases`; failures retain receipts | Add generated windows and training/selection stages to this same workflow |
| Configuration | Strict `chronological-validation-v1`, explicit folds, one pinned recipe, `frozen_recipe` only | Add a versioned plan supporting holdout, rolling and expanding schedules, plus optional parameter selection |
| Training | Development/calibration timestamps are declarations; only test windows execute | Run and score explicit candidate recipes on training windows, then freeze the selected configuration before testing |
| Data | Validation freezes qualified files; single-run momentum already accepts `data_repository` and `--duckdb` | Forward the same repository through validation and every child; add bounded views to `QualifiedSpotSource` |
| Warmup | Momentum recursively consumes available earlier bars; v1 does not implement bounded rolling feature history | Define warmup separately from scored training/test data and enforce the requested history policy |
| Capital | Separate reset accounts with segmented returns and no compounded global account | Retain this default; add continuous deployment later through one shared Backtest account |
| Evidence | JSON/XLSX, source/input hashes, inspection history, statistics and archived replay exist | Extend those outputs with window previews, candidate trials, selections and test-only summaries |

The current momentum strategy is rule-based. For this first implementation,
"training" means selecting among predeclared valid `MomentumRecipe` parameter
sets, using earlier data. A frozen-recipe evaluation performs no fitting.
`CoreTradingStrategy` currently has no general model-fitting interface; adding
arbitrary trainable models is a later strategy-interface task, not a requirement
for this walk-forward delivery.

### Architecture and caller contracts

Keep the existing call chain:

```text
execute_momentum_backtest validate
  -> validation.run_validation
     -> related_runs.run_related_cases
        -> existing run_momentum / run_snapshot
           -> Backtest.from_prepared / run
              -> MomentumStrategy hooks and shared EMA
              -> BacktestPortfolio / Portfolio / Trade / Holding
```

Both training and testing obtain bars through the existing input interface:
`QualifiedSpotSource(repository=...)` -> `DataRepository.query(DataRequest)` ->
`DataRequest.accept` -> selected query visitor. DuckDB SQL stays in its existing
backend implementation. Preserve qualified-file replay and Timescale callers.
Validation must never query DuckDB/PostgreSQL directly or calculate a second
ledger, fee model, allocator, Sharpe implementation or report payload.

Freeze only the required native market ranges and available metadata through
existing `DataRepository.prepare` for portable replay, alongside the qualified
price oracle. Original DuckDB executions must actually use the DuckDB visitor;
frozen-data replay can use the existing snapshot query visitor through the same
repository interface. Preserve the actual access-backend provenance. Cross-backend
replay must match decisions and ledger results exactly; backend-specific access
metadata need not have identical bytes. Same-backend replay from the same frozen
inputs retains the existing deterministic artifact checks. Do not require copying
an entire research database just to retain one experiment.

Strategy algorithms stay in Python; parameters stay in recipes; brokerage/cost,
execution and instrument rules stay in the existing run specification. The
validation plan adds research scheduling and selection policy. Freeze fully
resolved candidate recipes, including code defaults, before any trial starts.
The same fees, slippage, precision rules, capital and universe apply to candidate
comparisons and their test run. Existing supported auxiliary and liquidity
inputs must be forwarded rather than silently dropped.

### Proposed plan contract

Retain v1 parsing, behavior, feature initialization and output conventions. Add
`chronological-validation-v2` in the same module; normalize supported inputs to
one internal fold representation. An explicit holdout is one fold; rolling and
expanding plans compile into the same representation.

A v2 definition specifies overall UTC bounds, window mode, training length or
initial training length, test length, step, training-to-test gap, final partial
window policy, selection rule, warmup policy, capital policy and evaluation
budget. Retain hypothesis, inspection/trial history, recipe/source identities
and statistical settings. Dates are half-open and align to the run's bar
interval. Generate chronological timestamps, not row-number splits that could
hide missing market observations. Initially require nonoverlapping test windows;
training windows may overlap and earlier test data can enter later training
once it is historically available.

The following is an illustrative fragment, not an accepted current CLI schema:

```json
{
  "schema": "chronological-validation-v2",
  "mode": "rolling",
  "window": {
    "train_bars": 4320,
    "test_bars": 720,
    "step_bars": 720,
    "gap_bars": 1,
    "partial_final_fold": "drop"
  },
  "selection": {
    "rule": "training_rank",
    "objective": "annualised_daily_sharpe",
    "minimum_complete_training_days": 30,
    "tie_break": "resolved_recipe_sha256_ascending",
    "candidate_failure_policy": "fail_fold",
    "candidates": [
      {"id": "vol025", "recipe_overrides": {"vol_target": "0.25"}},
      {"id": "vol050", "recipe_overrides": {"vol_target": "0.50"}},
      {"id": "vol075", "recipe_overrides": {"vol_target": "0.75"}}
    ]
  },
  "feature_history_policy": "restart_from_declared_warmup",
  "capital_policy": "independent_reset_accounts",
  "max_evaluations": 64
}
```

This example means 180 training days and 30 test days for hourly bars. It is not
an estimate of sufficient statistical evidence or a claim that the current
six-month qualified dataset can satisfy those windows plus warmup. Expanding
mode retains the initial training start and advances its end. `frozen_recipe`
remains a supported control with no candidate search.

Add `--duckdb` and `--dry-run` to the existing `validate` action, not another
walk-forward command. Preview must show the fully expanded folds, scored and
warmup bounds, recipe identities, expected bar coverage, number of candidate/test
runs and reasons a plan cannot execute. Save that resolved definition before
execution. Preserve the current fail-on-existing-output behavior.

Preview invocation (implemented for frozen recipes):

```sh
python -m algoFarmBT.backtests.execute_momentum_backtest validate \
  --snapshot qualified-prices --duckdb prices.duckdb \
  --spec run.json --recipe recipe.json --plan walk-forward.json \
  --output new-validation-run --dry-run
```

Remove `--dry-run`, pass `--resolved-plan new-validation-run/resolved-plan.json`
and choose a new output directory to execute the reviewed resolved definition. Freeze and verify that same definition; do not regenerate a
different schedule from mutable settings at execution time.

Chronological split and gap terminology follows the [TimeSeriesSplit
reference](https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.TimeSeriesSplit.html)
and [rolling-origin evaluation](https://otexts.com/fpp3/toolbox.html#time-series-cross-validation).
These are conceptual references; introducing scikit-learn as a runtime dependency
is not needed for the existing bar-aware framework.

### Fold execution and leakage controls

1. Resolve and freeze all candidates, input snapshots, execution settings and
   scoring rules. Preflight every candidate's warmup and every fold's scored
   coverage. Keep the existing 32-fold bound and enforce an explicit total-run
   budget; include later continuous evaluation in that budget when enabled.
2. Extend `QualifiedSpotSource` with a bounded view. Training receives only its
   declared scoring window plus permitted earlier warmup. Its repository requests
   end at the training cutoff, and its file oracle uses the identical selection.
   Test execution uses a distinct bounded view. Warmup never contributes trades
   or returns to the scored window. Legacy v1 history semantics stay unchanged.
3. Run training candidates through the same `run_momentum`, shared portfolio and
   canonical reports. Rank using the declared net-of-cost metric from existing
   `case_metrics` / report payloads. Reject undefined metrics or insufficient
   samples. Preserve every candidate's parameters, receipts, failures and scores;
   never default to a candidate after all trials fail.
4. Freeze the selected full recipe and a selection receipt, including training
   boundaries, objective, deterministic tie result and artifact hashes. The next
   test interval cannot change this selection. A failed candidate follows the
   plan's explicit policy; the initial default fails that fold rather than
   silently ignoring trials. Later folds retain their own receipts.
5. Execute the test once with that recipe, using the existing strategy lifecycle
   and ledger. Update causal feature state from already available test bars;
   do not refit parameters, normalization or thresholds using test outcomes.
   Retain source/freshness rules for IV, regime and breaker observations.
6. Continue forward. Aggregate only the declared test windows for evaluation;
   report training scores separately. Do not choose parameters, candidate ranges,
   window lengths or report conventions retrospectively from the test results.

Separate warmup, parameter-fitting and scoring cutoffs. When forward labels or
outcomes span a boundary, exclude training examples whose outcomes are not known
by selection; a fixed gap alone is not proof against that leakage. The current
return-scoring selector uses only outcomes available by its training cutoff.
A gap may update passive causal indicators before testing, but cannot supply
extra fitting/scoring observations. Record selection-latency assumptions.

Use distinct fields for simulated selection/data cutoffs and actual wall-clock
execution times. Never backdate a real recipe selection to make old data appear
prospective. Keep `research_history.py` and existing eligibility checks; any v2
classification of historical holdout versus prospective evidence must be explicit
and preserve the known-inspected exclusion. The March-August dataset remains
marked development. Additional candidate runs extend recorded trial history;
correlated folds/candidates do not become invented independent trials for DSR.

### Capital and state policies

The first delivery retains `independent_reset_accounts`. Every training candidate
and test fold gets its own starting capital; training cash, trades and holdings
do not become test P&L. Feature initialization is a separate declared policy.
V2 initially restarts features from each recipe's required warmup, rather than
implicitly carrying an indicator state across a parameter change. Test-only
returns remain segmented at resets/gaps under the current statistics rules.

A later `continuous_account` mode uses one existing `Backtest.from_prepared`
instance and one portfolio over contiguous test intervals. Validation composes
the selected strategies' `SpotTarget` sequences and passes them into the existing
execution interface. At a switch, real holdings remain in the same ledger and
rebalancing incurs normal fees/slippage. Initially require a fixed instrument
universe and contiguous test coverage. Gaps, changing instruments and feature-state
migration need explicit policies before support. Never obtain a continuous NAV
by concatenating independent-reset equity levels.

### Implementation order and acceptance gates

| Step | Extend these existing files | Acceptance gate |
| --- | --- | --- |
| 1. Definitions and preview | `backtests/validation.py`, existing CLI, `examples/momentum.validation.json`, this document | Holdout/rolling/expanding resolve deterministic UTC folds; overlaps, bad bounds, insufficient warmup, missing data and excessive budgets reject before trials; v1 remains supported |
| 2. Bounded data and forwarding | `spot_inputs.py`, `validation.py`, existing CLI and `run_momentum` | Real training/test requests traverse repository/accept/visitor routes with the declared bounds; no unbounded market-data query or file fallback; auxiliary/liquidity settings reach children |
| 3. Training and selection | `validation.py`, `related_runs.py`, existing `case_metrics` and recipe loader | All candidate training uses the existing engine, identical costs and equal scored coverage; selections and failures are retained; no test-dependent ranking |
| 4. Testing and reporting | `validation.py`, `research_history.py`, shared `validation_frames` / validation statistics | Frozen selected recipes drive normal child backtests; JSON/XLSX show training, selected recipe, test performance and eligibility separately; reset/gap conventions remain accurate |
| 5. Verification and delivery | Extend `tests/test_validation.py`, existing DuckDB integration tests and documentation/examples | Existing suite passes; actual CLI end-to-end test, ledger reconciliation, window/query audit and frozen-source replay pass |
| 6. Continuous deployment | Same validation orchestrator and existing Backtest target-input interface | One shared account retains actual holdings across switches, boundary costs reconcile, and changing future data cannot affect earlier selections or fills |

Build each step in vertical test/implementation slices at the existing public
seams: validation CLI / `run_validation`, `QualifiedSpotSource` through
`DataRepository`, and `Backtest.from_prepared`. No internal class replacement or
mocked simulator is sufficient for the end-to-end acceptance.

Required regressions include rolling/expanding boundary examples, warmup/scoring
separation, incomplete last windows, deterministic ties, invalid metrics and
candidate failures, availability-time auxiliary inputs, and no duplicate OOS
observations. Perturb future/test prices and verify earlier training scores and
selected recipe hashes stay unchanged. Verify each test result equals a direct
`run_momentum` invocation using the same selected recipe and bounded source.
Preserve v1 economic/reporting conventions; code/source hashes will legitimately
change after implementation, while replay within a frozen version must match.

Retain the existing JSON/XLSX/manifests, adding resolved window definitions,
parent/fold/candidate identities, training metrics and selected-recipe hashes.
Keep report calculations in the current shared reporting modules. Default to
bounded sequential execution and capture detailed traces for selected tests;
avoid producing full Excel/trace exports for an unbounded parameter search.
No new PostgreSQL schema is a prerequisite for scheduling or execution. If a
strategy/run registry is subsequently requested, add its data/query objects and
SQLAlchemy visitors through `DataRepository` and read back the persisted records;
do not introduce direct SQL from validation or a competing results store.

### Controlled first demonstration

Use the existing qualified BTC/ETH/SOL development history to test mechanics,
with 2,560 pre-training warmup hours, 768 scored training hours, a one-hour
selection gap, 360 test hours and a 360-hour step. Three complete rolling folds
fit the frozen March-August input:

| Fold | Scored training UTC | Test UTC |
| --- | --- | --- |
| 1 | 15 June 16:00 to 17 July 16:00 | 17 July 17:00 to 1 August 17:00 |
| 2 | 30 June 16:00 to 1 August 16:00 | 1 August 17:00 to 16 August 17:00 |
| 3 | 15 July 16:00 to 16 August 16:00 | 16 August 17:00 to 31 August 17:00 |

For expanding mode, keep 15 June 16:00 as every training start and use the same
cutoffs/tests. These dates are 2026, illustrative integration-test windows, not
recommended strategy calibration lengths. Compare a frozen-recipe control with
three predeclared volatility-target candidates. Replay from frozen inputs and
source; verify fold selection, actual DuckDB visitor calls, generated signals,
Trade/Holding accounting and saved artifacts. Genuine unseen evaluation requires
separately qualified suitable history; the raw one-second S3 archives are not
promoted to qualified inputs by this feature.


## Continuous portfolio account (issue #19)

Set `capital_policy: continuous_account` in a v2 plan, using
`examples/momentum.continuous-validation.json` as a development example. Preview
and execute through the same `validate` action and frozen resolved plan. This
policy retains the ordinary reset-fold diagnostic runs and adds one real
continuous account. Include that extra account in `max_evaluations`. The default
policy and v1 behavior remain independent resets.

Test intervals must be contiguous and use the fixed run universe, allocation,
annualisation and execution assumptions. Gaps and unsupported feature-state
policies fail during preflight, before trials. Features restart independently
from each selected recipe's declared warmup; indicators update causally within
its window. Cash, holdings and cost basis carry through one existing Backtest
and Portfolio ledger. Training and reset-fold capital never fund that account.

The momentum runner composes the selected recipes' ordinary SpotTarget streams.
Recipe decisions switch at the next test start. The existing uniform execution
delay applies to the complete stream: pending prior-recipe decisions retain their
scheduled execution times until the new recipe's first decision executes. Only
executions at or beyond the final account end are censored. Boundary trades use
normal sizing, fees, slippage and any supplied qualified liquidity constraints.
No NAV concatenation, feature-state migration or terminal liquidation is inferred.

`validation.json` retains `continuous` separately from reset-fold metrics. Its
canonical metrics and full ordinary run report are under
`cases/continuous-account/run`; `recipe-schedule.json` retains every complete
recipe, hash, decision/activation time and feature-history bound. JSON/Excel
include Continuous account and Recipe switches views, alongside Folds, Training
and Selection. Reset-fold statistics remain segmented diagnostics. The primary
continuous return is available only if the whole account is eligible; the
previously inspected development history retains that designation in the
continuous run and report. Any failed required fold prevents a partial continuous
account from being published as successful; the ordinary failure receipt remains.

`--portable` and `--replay` use the same native preparation and snapshot visitor
routes. Replay compares continuous financial evidence, execution decisions,
signals and recipe-schedule hashes while retaining actual backend provenance and
execution times. The target-input interface of the same ordinary Backtest is the
reference for continuous-account comparisons.
