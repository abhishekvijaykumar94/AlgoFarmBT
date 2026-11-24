## Problem Statement

Researchers currently have to enumerate every candidate momentum recipe manually to compare parameter combinations. This is repetitive, makes omissions and duplicate configurations easy, and obscures how many ordinary backtest executions a study requires.

The existing framework already resolves recipes, evaluates training candidates, selects a recipe for each subsequent test interval, produces a continuous account when requested, and retains financial reports and replay evidence. The missing first increment is a basic grid declaration that feeds that same workflow. A separate optimization backtester, portfolio calculator, market-data reader or reporting implementation would duplicate established behavior and violate the required architecture.

Researchers need a deterministic, bounded and reproducible way to declare combinations while preserving the existing chronology, accounting, data qualification and research-history safeguards.

## Solution

Extend the existing validation workflow with a versioned grid input representation. Researchers declare finite value lists for selected recipe parameters, preview every resolved combination and its workload, then execute the frozen definition through the existing candidate-training, selection, forward-test and reporting workflow.

The first version uses sequential execution and the current persisted run artifacts. It changes candidate generation and the metadata needed to inspect and replay the grid. Existing recipe validation, strategy calculations, portfolio accounting, repository routing, canonical metrics and winner selection remain authoritative.

The result is a complete comparison of declared training candidates, their eligibility and failures, selected recipes, subsequent test results and the existing continuous account. It does not claim that the highest historical score identifies a uniquely optimal future strategy.

## User Stories

1. As a quantitative researcher, I want to declare a finite list of values for each searched parameter, so that I can compare all combinations without manually authoring every recipe.
2. As a quantitative researcher, I want to use the existing validation command, so that grid studies follow the same workflow as explicit-candidate studies.
3. As a maintainer, I want grid expansion to produce existing candidate records, so that simulation and accounting behavior are shared rather than duplicated.
4. As a quantitative researcher, I want a new plan version for grid inputs, so that previously saved plans retain their existing interpretation.
5. As a quantitative researcher, I want explicit candidate lists to remain supported, so that existing studies and hand-authored controls continue to work.
6. As a quantitative researcher, I want the plan to require either a grid or an explicit candidate list, so that the source of candidate definitions is unambiguous.
7. As a quantitative researcher, I want grid combinations to resolve through the existing recipe loader, so that all mathematical and comparison rules remain consistent.
8. As a quantitative researcher, I want each candidate to include all resolved defaults, so that a study does not depend on unrecorded recipe assumptions.
9. As a quantitative researcher, I want unknown parameters and malformed or empty value lists rejected during preview, so that invalid studies never start accounts.
10. As a quantitative researcher, I want each invalid combination identified by its parameter coordinates, so that I can correct the declared grid precisely.
11. As a quantitative researcher, I want source-policy incompatibilities rejected before execution, so that an unsupported recipe cannot fail halfway through a study.
12. As a quantitative researcher, I want duplicate resolved recipes rejected, including equivalent numeric spellings, so that repetitions do not inflate trial counts or masquerade as distinct candidates.
13. As a reviewer, I want deterministic candidate IDs and full recipe hashes, so that I can identify the same configuration across previews, execution and replay.
14. As a reviewer, I want parameter-key ordering to have no effect on candidate identity or selection, so that formatting changes cannot alter an experiment.
15. As an operator, I want candidate cardinality checked before the Cartesian product is materialized, so that an oversized declaration cannot cause unbounded memory use.
16. As a quantitative researcher, I want preview to show every recipe, coordinate, warmup window and scoring interval, so that I can review exactly what will run.
17. As an operator, I want the preview to count all training candidates, selected tests and an optional continuous account, so that I can set an informed execution budget.
18. As an operator, I want excessive grids or insufficient execution budgets rejected explicitly, so that the system never shortens history or omits cases to fit a limit.
19. As a quantitative researcher, I want the original grid and the complete resolved candidate list frozen together, so that execution can verify the precise experiment I reviewed.
20. As a quantitative researcher, I want execution to reject changed recipes, source identities or plan inputs after preview, so that results match the frozen study definition.
21. As a quantitative researcher, I want all candidates in a fold scored on the same training dates, so that parameter comparisons are meaningful.
22. As a quantitative researcher, I want each candidate to receive its own required earlier observed-bar history, so that warmup differences and historic gaps are handled correctly.
23. As a reviewer, I want training reads to stop at the declared cutoff, so that test or future prices cannot influence parameter selection.
24. As a quantitative researcher, I want capital, costs, universe, allocation assumptions, execution settings and source qualification held fixed, so that signal comparisons do not quietly change the experiment.
25. As a quantitative researcher, I want candidates to use the ordinary momentum runner and existing Backtest, so that signal generation, positions, fills and costs match standalone runs.
26. As a reviewer, I want actual market reads to retain repository query and visitor evidence, so that the application demonstrably uses the intended data-access route.
27. As an operator, I want sequential execution through the current related-run workflow, so that the first delivery has predictable resource behavior and existing failure receipts.
28. As a quantitative researcher, I want training scores taken from the current canonical metrics, so that grid results do not introduce another performance calculation.
29. As a quantitative researcher, I want the declared objective, minimum sample requirement and deterministic tie break preserved, so that candidate selection remains explicit and reproducible.
30. As a quantitative researcher, I want failed candidates and their successful siblings retained in the study evidence, so that failures cannot disappear from the comparison.
31. As a reviewer, I want a required candidate failure to gate testing under the existing fail-fold rule, so that an incomplete search cannot produce an apparently valid winner.
32. As a quantitative researcher, I want undefined or insufficient training scores to remain ineligible, so that the system cannot manufacture a winning score.
33. As a quantitative researcher, I want an optional named fallback resolved before execution and clearly labelled, so that operational fallback behavior is distinguishable from a passed research result.
34. As a quantitative researcher, I want each selected recipe frozen before its following test interval, so that forward testing evaluates the declared selection procedure.
35. As a quantitative researcher, I want the existing continuous account to carry cash, holdings and cost basis across selected recipes, so that real boundary trades and costs are reflected.
36. As a reviewer, I want the complete declared hourly coverage, asset inception dates and unresolved gaps preserved, so that grid execution cannot fabricate data or fills.
37. As a quantitative researcher, I want parameter values and coordinates included in the current training and selection reports, so that I can understand why each result belongs to its configuration.
38. As a quantitative researcher, I want scores, eligibility, fees, turnover, drawdown and sample coverage available together, so that I can assess more than a headline return.
39. As a reviewer, I want training results distinguished from selected-test results, so that an untested candidate is never assigned a test score it did not earn.
40. As a quantitative researcher, I want ordinary artifact links for each trial and selected test, so that I can inspect the underlying signals, positions and accounting.
41. As a quantitative researcher, I want portable replay to verify the grid and resolved recipes before running, so that tampering or changed defaults cannot silently alter the study.
42. As a reviewer, I want grid studies to match equivalent explicit-candidate studies financially, so that reuse of the existing framework is demonstrated by observable behavior.
43. As a maintainer, I want old validation, training-selection, continuous-account and replay fixtures preserved, so that this enhancement does not regress existing users.
44. As a researcher, I want inspected-history status and uncertainty about prior trials retained, so that a new grid does not relabel development work as unseen evidence.
45. As a maintainer, I want candidate proposal kept separate from existing execution and metric extraction, so that a future Bayesian policy can reuse the same evaluation path.

## Implementation Decisions

- **Architecture:** Extend the existing validation orchestrator and candidate resolver. Add only a small internal grid-expansion helper. Do not introduce a GridBacktester, independent optimizer runner, second ledger, optimizer-owned data reader or another result database.
- **Actual caller chain:** Continue through the existing validate command, run_validation, candidate resolution, run_related_cases, run_momentum and Backtest.from_prepared. Preserve MomentumStrategy and the existing BacktestPortfolio, Portfolio, Trade and Holding behavior. Prepared momentum targets continue through BacktestPortfolio.process_targets rather than the separate legacy naive-allocation caller.
- **Plan versioning:** Introduce chronological-validation-v3 for the new input contract. Preserve v1/v2 parsing, default behavior, legacy recipe hashes and limits. Code identities continue to reflect actual source versions.
- **Selection representation:** A grid requires the existing training_rank selection rule. Accept exactly one of the existing explicit candidates representation or a search declaration with grid method and parameter-to-value-list mappings.
- **Parameter surface:** Restrict grid keys to top-level recipe fields already accepted by the existing candidate comparison contract. Values are explicit, finite, nonempty lists. A complete structured field value can be a categorical choice only when the ordinary loader accepts it. Exclude expression evaluation, dotted-field mutation and range-step syntax from this increment.
- **Resolution authority:** Pass every combination through load_recipe and MomentumRecipe. Preserve existing fixed universe, budget/cap, allocation/rebalance and annualisation comparison guards. Reuse or factor existing source/strategy compatibility checks for preflight; do not duplicate parameter validation.
- **Determinism:** Compute cardinality before expansion, enumerate parameter names deterministically and preserve declared value-list order. Reject invalid combinations and semantic duplicates before account execution. Preserve legacy recipe-hash behavior while adding v3 duplicate detection, including equivalent Decimal values. Generate safe candidate IDs from complete resolved-recipe hashes, retain full hashes and detect shortened-ID collisions. Preserve parameter coordinates separately from identity.
- **Frozen preview:** Extend the existing dry-run and resolved-plan workflow to retain original input bytes and hashes, original grid, complete candidate recipes/defaults, IDs, hashes, coordinates, candidate history windows and expected workload. Execution must match the frozen definition.
- **Bounded workload:** Retain the first-version limits of 32 candidates and 256 generated folds. Preserve v2's 1,056-evaluation ceiling. For v3, permit an explicit user budget up to the structural maximum of 8,449 logical evaluations. Count every fold's training candidates and selected test plus an optional continuous account. A four-candidate grid over 217 folds requires 1,086 evaluations; a 27-candidate grid requires 6,077. These are capacity examples, not recommended search definitions. Budget overflow rejects the study without truncation.
- **Execution:** Keep sequential run_related_cases execution and existing trial/failure receipts and full artifact exports. Reuse its ordinary child callback and run_momentum rather than creating a grid-specific execution loop with duplicated behavior.
- **Chronology and warmup:** Preserve existing rolling, expanding and single train/test holdout schedules. Resolve candidate history through QualifiedSpotSource.history_start. Score identical training intervals and stop training repository requests at the cutoff. Preserve initial/new-asset warmup and the chosen observed-gap policy.
- **Data access:** All actual bars continue through QualifiedSpotSource, DataRepository.query, DataRequest.accept and the configured backend visitor. Native DuckDB/registered-Parquet execution and explicit portable SnapshotReader replay retain their established routes and provenance. Denied or mismatched repository reads fail without a fallback reader or direct SQL.
- **Scoring and gating:** Extract results through existing case_metrics and retain existing net-return/daily-Sharpe objectives, minimum complete samples, recipe-hash tie break and fail-fold policy. Undefined scores cannot win. A failed required candidate gates its fold's test and preserves all trial evidence. Do not add a new winner-selection or statistical-performance implementation.
- **Fallback:** Retain the existing named fallback field and type. If needed for a grid, select a generated ID from preview metadata, update the plan and freeze a fresh preview before account execution. Verify membership in the resolved grid and preserve visible fallback labelling.
- **Forward execution:** Save the selected recipe before the following test and continue through the ordinary selected-test run. When requested, use the existing recipe schedule and continuous account, carrying cash, holdings, basis, costs and existing pending-intent timing semantics. Training accounts never fund that account.
- **Outputs:** Extend existing validation output and validation_frames with grid metadata, candidate parameter columns and a compact grid summary in the current JSON/workbook. Use canonical metrics and ordinary artifact links. A two-axis matrix may present results only with other dimensions explicitly fixed. Distinguish training evidence from selected-test evidence.
- **Replay:** Extend replay_validation's existing experiment comparison to include grid and resolved-candidate metadata. Deterministically re-expand and compare against the retained complete candidate list before account execution, then use the same validation runner and existing financial/hash checks. Reject changed inputs, inventories or grid metadata.
- **Documentation:** Add an illustrative synthetic grid example and update the existing validation guide. Examples demonstrate mechanics and do not prescribe a profitable parameter range. Reconcile outdated descriptions of limits against the implemented version-specific behavior.
- **Future extension:** Keep candidate proposal separate from the existing per-case execution callback and canonical result extraction. Later Bayesian search will need training-only feedback, adaptive proposal receipts and reproducible observation order; this increment does not implement that machinery or add an optimizer class hierarchy.

## Testing Decisions

- **Primary public seam:** Exercise the existing validate workflow through preview, frozen execution and portable replay. This is the one proposed external test seam. Use it to observe candidate definitions, actual trial outputs, financial results, query evidence and report/replay artifacts.
- **Test behavior, not internals:** Do not assert private-helper structure, mock the grid expander or write tests that recalculate production formulas. Use small literal grids and independently authored equivalent candidate lists. Expected combinations and counts should be known examples.
- **Prior art:** Extend the existing public training-selection, continuous-account and portable-validation tests. Reuse their ordinary-run comparisons, future-price mutation checks, accounting fixtures, repository-denial/mismatch cases and saved-report verification.
- **Grid resolution:** A two-by-two grid previews exactly four unique recipes; a one-point grid matches its explicit-candidate counterpart. Repeated previews and parameter-key reordering preserve candidate identities. Ties obey the existing recipe-hash rule.
- **Preflight rejection:** Cover empty/malformed axes, unknown fields, contradictory recipes, source-policy incompatibility, duplicate numeric spellings, excessive cardinality and insufficient budgets. No trial starts after rejection. Verify the version-specific 1,056/8,449 bounds and complete execution-count arithmetic without running thousands of accounts.
- **Core equivalence:** The same v3 grid and explicit candidate list, with matching candidate IDs and exact resolved values, produce identical training financial results, scores, selected recipes, selected-test results and continuous-account results. Exclude representation metadata and actual timestamps from financial-equivalence assertions.
- **Causality:** Actual training requests never pass their cutoff. Changing gap/test/future prices cannot change earlier scores or selected recipes. Per-candidate observed history and later asset inception remain correct.
- **Accounting and routing:** Existing ledger/audit checks reconcile positions, fees, cash, basis and equity across selected switches. Data-access receipts demonstrate the intended repository visitor. A denied or mismatched repository read must fail without fallback. Residual gaps never generate artificial fills.
- **Failures and eligibility:** Retain successful siblings and runtime failure receipts, enforce fail-fold gating, preserve undefined/insufficient-score behavior and verify optional named fallback labelling.
- **Reports and replay:** Check that JSON and current workbook show consistent parameters, scores and eligibility. Portable replay reproduces outcomes and rejects altered grid/candidate inputs or artifacts before accounts run.
- **Compatibility and integration:** Run existing v1/v2 chronological, candidate-selection, continuous and portable-replay regressions. After those pass, verify a separately frozen, explicitly budgeted study on the complete repaired hourly history and all declared folds, using the existing runner and independent ledger audit. Implementing the feature does not itself authorize a particular large production research sweep.

## Out of Scope

- Bayesian, random, genetic or other adaptive search; early pruning; a generic optimizer class hierarchy.
- Parallel/distributed execution, resume/retry infrastructure or new PostgreSQL study tables.
- Asset-budget/cap search, new allocation/rebalance policies, new instrument universes or changes to brokerage, costs or source qualification.
- New strategy engines, account/fee calculators, direct backend readers, result databases or report engines.
- Compact export profiles, new statistical selection tests, model-confidence-set implementation or a new multi-window fixed-recipe selection mode.
- Choosing the actual research grid, changing the investment hypothesis, executing thousands of accounts by default, or claiming a uniquely optimal future strategy.

## Further Notes

This extends the walk-forward foundation tracked in #16, #17, #18 and #19. The tracking repository is algoLibs, while delivery spans the existing AlgoFarmBT validation module and shared reporting where required. Preserve inherited branch work and inspect the actual implementation before changing it.

The dominant delivery requirement is reuse: grid declarations must become the same candidate records the existing framework already understands. Scientific interpretation remains governed by the recorded research hypothesis, fixed assumptions, chronology, costs and prior inspection/trial history. Already inspected historical data remains development evidence.
