# Basic grid search implementation plan

Status: proposed first delivery, 23 November 2026. This narrows the [broader study roadmap](parameter-studies.md) to grid generation and integration with existing training selection. The [research protocol](parameter-selection-research.md) governs the choice of parameters, bounds and interpretation of results.

## Architecture decision

Grid search will produce the same resolved candidate recipes that `validation.py` already evaluates. It will not own a backtest engine, strategy implementation, portfolio, data reader, performance calculator or database connection.

The actual call chain remains:

```text
execute_momentum_backtest.main: validate
  -> validation.run_validation
     -> expand declared grid into existing candidate records
     -> existing _candidate_recipes / load_recipe / MomentumRecipe validation
     -> existing run_related_cases
        -> existing run_momentum
           -> existing MomentumStrategy and Backtest.from_prepared
              -> existing BacktestPortfolio / Portfolio / Trade / Holding
              -> QualifiedSpotSource -> DataRepository.query
                 -> DataRequest.accept -> configured backend visitor
     -> existing case_metrics and training ranking
     -> existing selected test and continuous-account recipe schedule
     -> existing validation.json / validation_frames / write_workbook
```

The prepared momentum path already expresses allocation as target weights from the recipe. It continues through `BacktestPortfolio.process_targets`. Grid search must not switch that caller to the legacy `NaiveAllocationStrategy` path or calculate trades itself.

## Scope of the first version

Support a finite Cartesian grid of explicit parameter values, validated before execution. Use the existing rolling, expanding and holdout schedules and their training-only selection. Holdout here means the existing single train/test split, not the broader roadmap's new multi-window fixed-recipe selection mode.

Keep execution sequential through `run_related_cases`, with the existing case/failure receipts, full run artifacts, selected recipes and continuous account. Keep existing objectives (`net_return`, `annualised_daily_sharpe`), minimum complete training days, recipe-hash tie break, fail-fold policy and explicit fallback semantics. A top training score remains a historical selection result, not statistical proof of an optimal future strategy.

The first version searches only parameters already permitted by the candidate loader. Asset budgets/caps, universe, allocation/rebalance policy, annualisation units, costs, execution settings and source-gap policy retain their present fixed comparison contracts. Allocation search remains a subsequent extension of those same contracts. The first real signal experiment should also keep its volatility-sizing coefficient fixed, as specified by the research protocol.

Explicitly defer Bayesian/random search, parallel workers, resume/retry infrastructure, new PostgreSQL study tables, new allocation policies, compact report profiles and new statistical selection methods. Those belong to later enhancements of this runner. They are not prerequisites for establishing grid-search correctness.

## Change 1 Extend the existing plan and candidate resolver

Use a new `chronological-validation-v3` plan schema to distinguish the new input contract. Preserve v1/v2 parsing, legacy recipe hashes, defaults, limits and execution behavior. Source identities continue to reflect the actual code version.

A grid requires the existing `selection_rule: training_rank`. Within its existing `selection` object, accept exactly one of:

* `candidates`: the existing explicit candidate-list representation.
* `search`: a grid declaration containing `method: grid` and `parameters`, where each parameter maps to a finite, nonempty ordered value list.

The following is an illustrative selection fragment for a synthetic integration fixture, not a recommended trading experiment or a complete runnable study plan:

```json
{
  "rule": "training_rank",
  "objective": "annualised_daily_sharpe",
  "minimum_complete_training_days": 30,
  "tie_break": "resolved_recipe_sha256_ascending",
  "candidate_failure_policy": "fail_fold",
  "search": {
    "method": "grid",
    "parameters": {
      "smoothing_span": [3, 6],
      "signal_span": [6, 9]
    }
  }
}
```

These two axes yield four combinations. For the MVP, parameter keys are top-level recipe fields; whole structured recipe-field values may be supplied as categorical choices if the existing loader accepts them. Do not introduce dotted-field mutation, Python expressions or a separate parameter-validation rulebook. An explicit scalar value list is sufficient initially; min/max/step convenience syntax can follow later with exact-decimal semantics.

Add a small internal grid-expansion helper in `backtests/validation.py`. Keep `_candidate_recipes` as the shared candidate-resolution implementation and `load_recipe`/`MomentumRecipe` as the mathematical validation authority. If extracting a private helper improves readability, it remains inside the existing backtests module and has no execution or storage responsibility.

Expansion rules:

1. Validate search shape, parameter names and value-list shapes, and compute product cardinality before materializing the grid or reading market data.
2. Enumerate parameter names deterministically, preserve declared value order, and create overrides for every combination using the standard Cartesian product.
3. Resolve every combination through the existing recipe loader and comparison guards. Validate compatibility with the chosen source/strategy policy during preview by reusing or factoring the existing strategy configuration checks, rather than discovering an unsupported combination halfway through execution.
4. Reject invalid combinations and duplicate resolved recipes before any account runs. Semantic duplicates such as equivalent Decimal spellings must also be detected for v3. Preserve legacy v2 identities; do not globally rewrite historical recipe hashes.
5. Assign safe deterministic IDs from the complete resolved-recipe hash, respecting the existing ID-length contract and detecting any shortened-ID collision. Retain the full hash and original grid coordinates in the resolved definition.

Invalid configurations fail the preview with the offending coordinates. The MVP does not silently skip combinations, prune candidates or replace invalid settings.

## Change 2 Feed the resolved grid into existing validation execution

Recognize v3 in `run_validation` and `_generated_folds`, and route the resolved candidates to the existing training loop. Reuse `run_related_cases`, its child callback and `run_momentum` unchanged wherever possible. Keep canonical scoring in `case_metrics` and the current selection logic; do not implement a grid-specific score calculator or winner selector.

Preview and execution continue through the existing `validate --dry-run` and `--resolved-plan` workflow. Retain the original user grid and the complete resolved candidate list, exact recipes, full hashes, coordinates and per-candidate history windows in the frozen definition. Preserve the original input bytes/hash as well as the resolved content. Actual execution must match the preview; no rediscovery of ranges, dates or changed defaults is permitted.

Reuse `QualifiedSpotSource.history_start` for observed-bar warmup. Each candidate receives its own required history while scoring identical training dates. Training queries stop at the training cutoff. Selected tests consume the selected recipe and its warmup. For a continuous study, the existing `_RecipeSchedule` and account carry cash, holdings and basis across switches.

Keep the initial 32-candidate and 256-generated-fold structural limits explicit. For v3, replace the inherited 1,056-evaluation ceiling with a budget consistent with those structural limits: up to `256 * (32 + 1) + 1 = 8,449` logical evaluations. The user still declares `max_evaluations`, and preview rejects a workload that exceeds it. Retain v2's current ceiling unchanged. This is a bounded first version; larger candidate spaces require a later, explicit resource-budget enhancement.

Count `folds * (candidates + 1 selected test) + optional continuous account`. A four-candidate grid across 217 folds requires 1,086 evaluations; 27 candidates require 6,077. Both can fit v3 when their explicit budgets allow them. Never reduce history or omit folds to fit a budget. Keep all declared hourly history, asset inception dates and residual gaps.

Preserve failure semantics. A failed required training candidate retains its receipt and fails the fold under `fail_fold`; successful siblings remain recorded. Undefined scores and insufficient observations cannot win. No eligible score means no selected test unless the study declares an existing named fallback. Keep the existing named fallback field and type. Preview shows generated IDs; if a fallback is wanted, choose its ID from that metadata, update the plan and freeze a fresh preview before any account execution. Its resolved recipe must match a grid member. An operational fallback remains labelled and does not establish that a research hypothesis passed.

## Change 3 Extend existing output and replay

Retain the established artifact structure:

* `resolved-plan.json`: original grid, resolved recipes and coordinates, exact windows, count/budget and provenance.
* `common/candidates`: the ordinary fully resolved recipe files.
* `cases/<fold>/training/cases/<candidate>/run`: existing training runs and failure receipts.
* Each fold's `selection.json`, selected recipe and selected-test run.
* The existing separate continuous-account run and recipe-switch schedule when requested.

Extend `validation.json` and `validation_frames` with candidate parameter columns and a compact grid summary. The existing Training and Selection sheets remain the primary per-fold comparison. Show scores, eligibility reasons, fees, turnover, drawdown and sample coverage from `case_metrics`, plus links to ordinary run artifacts. A matrix/pivot view may present two chosen axes; dimensions held fixed must be declared. No separate reporting engine is needed for the MVP.

Training rankings stay separate from selected-test performance. Do not label a candidate with a test score when it was not the selected candidate for that fold, and do not imply that a per-fold recipe schedule is one globally optimal vector. Keep development/inspection status and prior-trial uncertainty unchanged.

Extend `replay_validation`'s existing experiment comparison to include the grid and resolved-candidate metadata. Replay verifies the frozen definition, deterministically re-expands and compares it against the retained complete candidate list, then executes through the same validation runner. A mismatch fails before account execution. Portable replay retains the existing SnapshotReader repository visitor; normal DuckDB/Parquet execution retains its configured market-data visitor. Preserve existing financial/hash comparison rules and reject artifact or parameter tampering.

## Change 4 Verify through the existing public interfaces

Extend `tests/test_training_selection.py`, `test_portable_validation.py` and `test_continuous_validation.py` at the existing `validate` preview/execute/replay interfaces. Add a focused grid fixture only where necessary. Test expected behavior using literal small grids and existing canonical runs, rather than private helper assertions.

Acceptance checks:

1. Two axes with two values preview exactly four distinct valid recipes with stable IDs/hashes and the correct evaluation count. A one-point grid matches its equivalent explicit-candidate study.
2. The same resolved grid, expressed as an ordinary v3 explicit candidate list with matching IDs, produces identical candidate financial results, scores, selected recipe and continuous-account results. Comparison excludes representation metadata and actual timestamps.
3. Empty axes, unknown fields, contradictory recipe settings, source-policy incompatibility, semantic duplicates, excessive candidate counts and insufficient budgets fail before a trial starts.
4. Repeated previews and JSON parameter-key reordering preserve the resolved recipe set and IDs. Finite-list enumeration is deterministic; neither its order nor training-score ties changes the existing hash tie-break result.
5. Training requests stop at their cutoff, and changing future/gap/test prices cannot alter earlier training scores or selections.
6. Warmup follows each candidate's requirements, including missing historical bars and later asset inception. The selected continuous account reconciles through the ordinary ledger and existing audit interfaces.
7. Runtime failures, undefined metrics and named fallback behavior preserve existing receipts and gating. Replay reproduces successes/failures and rejects modified grid or resolved inputs.
8. Actual data-access receipts show `DataRepository.query -> DataRequest.accept -> configured visitor`. Repository denial/mismatch fails without a file or direct-SQL fallback.
9. Existing v1/v2 training-selection, chronological, continuous-account and portable-replay regression tests retain their financial outcomes. New grid metadata appears consistently in JSON and the existing workbook.

After these checks, use the complete repaired hourly dataset for a declared, bounded integration study, including all specified folds. Parameter values, objective and scoring-window choices must be frozen first; this implementation plan does not choose a profitable-looking grid or launch thousands of accounts.

## File and ownership summary

| Location | First-version change |
|---|---|
| `AlgoFarmBT/algoFarmBT/backtests/validation.py` | Grid input resolution, v3 recognition/budget, frozen metadata, reuse of selection and replay |
| `AlgoFarmBT/algoFarmBT/backtests/execute_momentum_backtest.py` | Existing validate help/description and any necessary schema forwarding; keep the same command |
| `algoLibs/algoLibs/backtest/report_frames.py` | Grid parameter columns/summary using existing validation report data |
| Existing AlgoFarmBT validation tests/examples/docs | Public-interface acceptance, one illustrative grid example and usage documentation |
| `related_runs.py`, MomentumStrategy, Backtest, BacktestPortfolio, Portfolio, Trade, Holding, QualifiedSpotSource, DataRepository and backend visitors | Reuse their existing behavior; modify only a directly required shared validation check, not execution/accounting/read implementations |

No new runtime module/class is required merely to name grid search. In particular, there is no `GridBacktester`, alternative portfolio calculator, optimizer-owned SQL reader or second result database.

## Future Bayesian extension

Keep candidate proposal separate from recipe validation, the existing per-case execution callback and canonical result extraction. Those existing seams allow a future feedback-aware proposal policy to reuse the evaluation path. Bayesian search will additionally need training-only observation feedback, budgets, recorded proposal order and replay of adaptive decisions; that is a later change, not a claim that a sampler can be swapped into a precomputed list with no coordination work. Do not add an optimizer hierarchy or external Bayesian dependency in the grid-only delivery.
