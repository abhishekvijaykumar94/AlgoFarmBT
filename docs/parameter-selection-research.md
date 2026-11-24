# Institutional evidence for parameter selection

Research date: 23 November 2026. This note distinguishes first-person institutional disclosures, published research illustrations, academic methods and the proposed application to our hourly BTC/ETH/SOL strategy.

## Recommendation

Use constrained, hypothesis-driven model calibration and validation. For this strategy, preserve a diversified horizon blend, investigate costs and effective holding periods, compare a small number of shared signal specifications at a common risk convention, and allow rejection of the entire family. Choose risk sizing and asset allocation in separate experiments. A broad search followed by a maximum-backtest-Sharpe winner is not the recommended research protocol.

Grid, random and Bayesian search are numerical ways to propose evaluations. They do not determine which hypotheses deserve testing, whether differences are statistically meaningful, or whether an economic edge survives costs. The public evidence below establishes research principles and examples rather than one universal hedge-fund optimizer.

## Direct institutional evidence

### Man AHL chooses complementary trend speeds

In [The Need for Speed in Trend-Following Strategies, 4 January 2023](https://www.man.com/insights/need-for-speed-trend-following), AHL describes using double-EWMA crossover models and selecting speeds to cover trend timescales and reduce intermodel correlation. It argues for combining speeds, with weights reflecting investment objectives such as defensive behavior as well as returns. Its comparison explicitly incorporates execution costs, which hurt faster models more. Locations: sections “What Is Speed”, “The Need for Speed” and “The Need for Execution”, especially paragraphs beginning “The choice of trading speeds” and “Transaction costs impact”. These are disclosed design considerations; the comparison portfolios are illustrations.

### CFM describes limiting parameter discretion

[CFM, In-sample overfitting: avoiding the pitfalls in data mining, May 2016](https://www.cfm.com/wp-content/uploads/2022/12/170-2016-In-sample-overfitting-avoiding-the-pitfalls-in-data-mining.pdf), printed page 7, describes its preferred approach for low-Sharpe strategies: formulate ideas before inspecting data, use a standard statistical toolkit with little parameter discretion, and drop unsuccessful ideas instead of refining their backtests. The short supporting phrase is “with no freedom for parameter choice”. This is a dated, scoped description of CFM's preferred process, not a claim about every current model or all quant funds.

### AQR combines common signal measures and separates risk scaling

[A Century of Evidence on Trend-Following Investing, Hurst, Ooi and Pedersen, 2017](https://www.aqr.com/-/media/AQR/Documents/Insights/Journal-Article/AQR-JPM-Fall-2017.pdf), “Constructing the Time-Series Momentum Strategy”, uses common 1-, 3- and 12-month signals across 67 markets and combines them equally. Individual positions are volatility scaled; portfolio risk is scaled separately. This is a published research methodology, not a disclosure of proprietary fund holdings. It supports considering shared simple specifications rather than independent best-lookback searches for each market; it does not justify transplanting those horizons or its risk target to hourly crypto.

In [Lies, Damned Lies, and Data Mining, 12 April 2017](https://www.aqr.com/insights/perspectives/lies-damned-lies-and-data-mining), Asness describes AQR averaging related measures of a few concepts to reduce measurement error, and requiring economic reasoning plus replication across times, geographies and asset classes. Locations: opening argument and footnote 8. That is evidence for robust measurement and external replication, not a particular optimizer.

### AHL records the research process

In [Overfitting and Its Impact on the Investor, discussion held May 2015](https://www.man.com/insights/overfitting-and-its-impact-on-the-investor/), Matthew Sargaison explicitly describes AHL recording attempted ideas and requiring written sample partitions, methodology and expectations. Locations: his responses under assessing overfitting and lessons from other industries. The same discussion contains academic recommendations about confidence sets and heatmaps; those recommendations are not proof that AHL implemented those exact statistical algorithms.

### Crypto implementation needs its own evidence

[Man AHL, In Crypto We Trend, 19 December 2024](https://www.man.com/insights/in-crypto-we-trend), uses a simplified 50-/200-day crossover illustration and examines equal-risk coin diversification with transaction/shorting costs and liquidity constraints. It expressly warns against directly transferring models into crypto. Locations: “Does trading more coins enhance diversification” and “Conclusion”. Its illustrative universe optimum is not an allocation recommendation here. Our hourly, long-only cash-spot histogram strategy differs materially from those examples.

## Statistical interpretation

[Hansen, Lunde and Nason, The Model Confidence Set, Econometrica 2011](https://onlinelibrary.wiley.com/doi/abs/10.3982/ecta5771) formalizes the possibility that available data support several competitive models rather than a uniquely identifiable winner. The [author/institution working paper](https://pure.au.dk/ws/files/34269366/rp10_76.pdf) provides the construction and bootstrap assumptions. An implementation for us would require a declared period-level loss function, aligned candidate observations and dependence-aware inference. Relative membership does not establish positive expected profit; every candidate could be unattractive versus an external benchmark. This is an academic method, not evidence of universal institutional adoption.

[Harvey and Liu, Backtesting, Journal of Portfolio Management 2015](https://people.duke.edu/~charvey/Research/Published_Papers/P120_Backtesting.PDF), discusses multiple testing, correlated strategies and the loss of out-of-sample status when a researcher repeatedly changes a strategy after inspecting its test results. See printed pages 16–18. These issues remain under Bayesian search or walk-forward testing. Trial history and dependence assumptions are needed; a raw run count is not automatically an independent-test count.

## Application to our strategy

The following is our proposed protocol informed by those sources, rather than a firm's disclosed trading recipe.

1. **Specify the mechanism and model first.** Distinguish a plain EWMA trend difference from the current MACD-style histogram with its signal EMA. Treat a change of signal definition as another research hypothesis. The institutional moving-average studies do not validate our particular histogram formula. Keep the existing recipe as the recorded baseline.
2. **Inspect the existing six-horizon blend before adding degrees of freedom.** Measure horizon-level contribution, correlations, effective holding periods, turnover and net performance using the same portfolio/accounting interfaces. Attribution/ablation is diagnostic evidence, not six independent discoveries. Retain the full hourly source history and the existing availability/warmup policy.
3. **State one falsifiable hypothesis per experiment.** For example: slower collective horizon sensitivity might retain predictive content while reducing turnover costs. A shared speed adjustment across the horizon family offers fewer degrees of freedom than freely optimizing every fast/slow span, each horizon weight, smoothing, filters and asset budgets at once. Values and bounds must be justified in clock time and by available observations before candidate ranking.
4. **Separate prediction from risk appetite.** Keep the volatility-sizing coefficient and asset budgets fixed during signal comparisons, report actual exposure and realized risk, and choose risk/cap/allocation rules in a separate constrained experiment. Changing `vol_target` can alter caps, cash and trading costs; it is not a pure measure of improved forecasts. Any risk-normalized diagnostic computed after the fact must be labelled and cannot enter causal position generation.
5. **Make costs a primary acceptance condition.** The retained control reports USDT 5,933.91 P&L before fees on the same executed fills, with modeled slippage already included, USDT 10,005.89 fees and USDT -4,071.98 net P&L. These are local study results, not evidence that reducing turnover will necessarily make the strategy profitable. Fees/slippage are declared assumptions rather than verified historical venue terms. Diagnose net edge and stress plausible execution assumptions before expanding parameter search.
6. **Use shared candidates with chronological validation.** Share signal specifications across BTC/ETH/SOL initially. Use asset breakdowns or an asset withheld from calibration as diagnostics, recognizing that three correlated coins are weak independent replication. If tuning, compare candidates on inner chronological development splits; freeze the choice or blend before evaluating the next outer window. Preserve all declared history. Select training length from the strategy's horizon and evidential requirements, not a universal calendar rule; the previous 32-day training windows should not be presumed sufficient for reliable Sharpe ranking.
7. **Report uncertainty and permit no selection.** Compare candidate improvements jointly on training/validation data with dependence-aware uncertainty and multiple-testing controls, retaining undefined/insufficient cases. Use the existing segmented resampling conventions; do not count hourly bars or correlated coins as independent observations. A simple baseline or predeclared blend remains preferable when an apparent incremental improvement is unsupported. Relative superiority alone is insufficient if all candidates fail net-edge/risk criteria.
8. **Freeze the research procedure and confirm forward.** Record the search space, all attempts, manual refinements, chronology, metric and stopping rule. Our full history is already inspected; repeated historical studies remain development research. Subsequent forward shadow/paper execution can check implementation and realized friction; a short pilot alone cannot establish statistical trading efficacy.

## Consequences for the framework plan

The proposed trial persistence, repository visitor routing, resumable execution and reports remain useful. The research decision interface needs more than `argmax(training Sharpe)`: it needs a declared baseline, experiment hypothesis, training-only uncertainty/acceptance rule, optional preservation of a competitive set, and explicit no-selection outcomes. Existing horizon combination should remain the forecast-combination foundation. Do not introduce another signal engine or a parallel ledger.

The prior 27-point volatility/smoothing/signal-span grid was an arbitrary illustration, not an institutionally justified first experiment. It is withdrawn as the recommended first study. Its 6,077-run arithmetic remains only an example of capacity planning. Bayesian optimization remains optional computational machinery after specifying a defensible protocol; the sources do not establish it as the institutional default.
