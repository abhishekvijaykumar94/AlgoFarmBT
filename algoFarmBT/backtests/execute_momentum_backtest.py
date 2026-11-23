"""Qualify spot prices and run supplied targets or a configured momentum recipe.

  python -m algoFarmBT.backtests.execute_momentum_backtest qualify --input comparison-directory --output prices
  python -m algoFarmBT.backtests.execute_momentum_backtest run --snapshot prices --spec run.json --targets targets.json --output result
  python -m algoFarmBT.backtests.execute_momentum_backtest momentum --snapshot prices --spec run.json --recipe recipe.json --output result

Momentum formulas and risk inputs must be explicit in the recipe. Manual target
runs are simulator diagnostics, not momentum strategy results.
"""
import argparse
from dataclasses import asdict
from datetime import timedelta
from decimal import Decimal, ROUND_DOWN, localcontext
import hashlib
from importlib import metadata
import json
from pathlib import Path
import shutil
import sys
import tempfile
import zipfile

import algoLibs
import algoFarmBT
from algoLibs.backtest import Backtest, SpotRules, SpotRunSpec, SpotTarget
from algoLibs.backtest.spot_reporting import spot_benchmarks
from algoLibs.market_data.types import encode, utc
from ..spot_inputs import QualifiedSpotSource, digest, qualify_price_bars


def source_snapshot():
    """Hash the actual imported Python/SQL sources, including local edits."""
    result = hashlib.sha256()
    files = {}
    for name, package in (('algoLibs', algoLibs), ('algoFarmBT', algoFarmBT)):
        root = Path(package.__file__).parent
        for path in sorted(root.rglob('*')):
            if path.is_file() and path.suffix in ('.py', '.sql'):
                filename = name + '/' + path.relative_to(root).as_posix()
                files[filename] = path.read_bytes()
                result.update((filename + '\0').encode())
                result.update(hashlib.sha256(files[filename]).digest())
    return 'sha256:' + result.hexdigest(), files


def _json(value):
    return json.dumps(encode(value), sort_keys=True, separators=(',', ':'), allow_nan=False)


def resolve_run_spec(raw, data_id, code_id):
    """Validate common run inputs before any related case is attempted."""
    raw = dict(raw)
    seconds = raw.pop('interval_seconds')
    if type(seconds) is not int or seconds <= 0:
        raise ValueError('interval_seconds must be a positive integer')
    raw['interval'] = timedelta(seconds=seconds)
    raw['rules'] = tuple(SpotRules(**r) for r in raw['rules'])
    if 'data_id' in raw or 'code_id' in raw:
        raise ValueError('Data and code identities are resolved from actual inputs')
    return SpotRunSpec(**raw, data_id=data_id, code_id=code_id), seconds


def run_snapshot(snapshot, config, targets_path, destination, *, strategy=None,
                 attachments=None, annualisation_hours=None, benchmark_weights=None, expected_data_id=None, strategy_controller=None,
                 fixed_reference_weights=None, report_context=None, signal_trace=None, retain_diagnostics=False,
                 execution_delay_bars=0, liquidity_path=None, liquidity_policy=None, data_repository=None, source_bounds=None):
    destination = Path(destination)
    if destination.exists():
        raise FileExistsError(destination)
    source = QualifiedSpotSource(snapshot, repository=data_repository, bounds=source_bounds)
    if expected_data_id is not None and source.data_id != expected_data_id:
        raise ValueError('Snapshot changed after target preparation')
    code_id, code_files = source_snapshot()
    spec, seconds = resolve_run_spec(json.loads(Path(config).read_text()),source.data_id,code_id)
    liquidity=None
    if (liquidity_path is None)!=(liquidity_policy is None):
        raise ValueError('Liquidity snapshot and policy are required together')
    if liquidity_path is not None:
        from ..liquidity_inputs import load_liquidity
        liquidity=load_liquidity(liquidity_path,QualifiedSpotSource(snapshot),liquidity_policy)
    # References are declared before evaluating any strategy returns.
    with localcontext() as decimal_context:
        decimal_context.prec=28
        benchmark_weights=(dict(benchmark_weights) if benchmark_weights is not None else
                           {r.symbol:(Decimal(1)/len(spec.rules)).quantize(Decimal('1e-24'),rounding=ROUND_DOWN) for r in spec.rules})
    fixed_reference_weights=dict(fixed_reference_weights) if fixed_reference_weights is not None else None
    context=dict(report_context or {})
    if strategy_controller is not None:
        backtest = Backtest.from_prepared(spec, source, strategy=strategy_controller, annualisation_hours=annualisation_hours, retain_diagnostics=retain_diagnostics, execution_delay_bars=execution_delay_bars, liquidity=liquidity, availability_policy=source.availability_policy)
        result = backtest.run()
        rows = [dict(time=t.time.isoformat(),weights={s:str(w) for s,w in t.weights},rebalance=t.rebalance) for t in backtest.target_log]
        targets_raw = json.dumps(rows,sort_keys=True,separators=(',',':')).encode()
    else:
        targets_raw = Path(targets_path).read_bytes()
        targets = [SpotTarget(**t) for t in json.loads(targets_raw)]
        backtest = Backtest.from_prepared(spec, source, targets=targets, annualisation_hours=annualisation_hours, retain_diagnostics=retain_diagnostics, execution_delay_bars=execution_delay_bars, liquidity=liquidity, availability_policy=source.availability_policy)
        result = backtest.run()
    report = backtest.performance_report()
    benchmarks = (spot_benchmarks(spec, source, benchmark_weights, annualisation_hours,
                                 fixed_reference_weights=fixed_reference_weights,liquidity=liquidity)
                  if source.availability_policy=='strict' else None)
    if source_snapshot()[0] != code_id:
        raise ValueError('Source code changed during the run')
    body = asdict(result)
    body['spec'].pop('interval')
    body['spec']['interval_seconds'] = seconds
    # The same installed metadata can be discovered twice through a source
    # checkout and site-packages. Canonicalise identities, retaining distinct
    # versions, so archived replay does not depend on duplicate sys.path entries.
    environment = dict(python=sys.version, dependencies=sorted({
        (d.metadata.get('Name', 'unknown'), d.version) for d in metadata.distributions()}))
    manifest = dict(format='cash-spot-run-v1', data_id=source.data_id, code_id=spec.code_id,
                    targets_sha256=hashlib.sha256(targets_raw).hexdigest(), environment=environment,
                    strategy=strategy or 'externally-supplied-targets', files={})
    context.update(resolved_parameters=body['spec'],strategy=strategy or 'externally-supplied-targets',
                   execution_schedule=dict(delay_bars=execution_delay_bars,
                       policy='Decision known at target time; execute at that opening plus delay complete bars; censor execution >= end; retain final positions',
                       scheduled=sum(r['status']=='scheduled' for r in backtest.target_timing),
                       censored=sum(r['status']=='censored_at_run_end' for r in backtest.target_timing)),
                   environment=environment,source_manifest=source.manifest,
                   coverage=dict(source_start=utc(source.manifest['start']),source_end=utc(source.manifest['end']),
                                 execution_start=spec.start,execution_end=spec.end,
                                 available_pre_run_bars=int((spec.start-utc(source.manifest['start']))/spec.interval)),
                   input_integrity=dict(status='verified',data_id=source.data_id,code_id=code_id,
                                        targets_sha256=manifest['targets_sha256'],
                                        evidence='Qualified file SHA256 checked on iteration; imported source unchanged after execution; targets bytes pinned'),
                   artifact_integrity=dict(status='published_after_export',
                       evidence='manifest.json contains SHA256 of each exported artifact; manifest.sha256 pins that manifest. No report self-hash is embedded.'),
                   benchmark_declaration=dict(buy_and_hold=benchmark_weights,fixed_reference=fixed_reference_weights,
                                               policy=benchmarks['policy'] if benchmarks is not None else 'Unavailable: no listing/gap benchmark entry policy declared'))
    if data_repository is not None:
        context['input_integrity']['evidence'] = (
            'Actual bars queried via DataRepository; every returned price/identity/time equals the pinned qualified snapshot; '
            'qualified file SHA256 checked on iteration; imported source unchanged after execution')
        context['input_integrity']['access_route'] = 'repository_query_visitor'
    if source.availability_policy!='strict':
        context['market_data_availability']=dict(policy=source.availability_policy,events=backtest.availability_audit,optional_analytics='Regular-grid asset/episode analytics unavailable; actual ledger tables retained')
    if liquidity is not None:
        context['liquidity']=dict(data_id=liquidity.data_id,policy=asdict(liquidity.policy),evidence=liquidity.evidence)
    sections=backtest.report_sections(signal_trace=signal_trace)
    research=backtest.research_report(context=context,benchmarks=benchmarks,sections=sections)
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix='.spot-run-', dir=destination.parent))
    try:
        if source.availability_policy!='strict':(stage/'availability.json').write_text(_json(dict(policy=source.availability_policy,events=backtest.availability_audit)))
        (stage / 'result.json').write_text(_json(body))
        (stage / 'spec.json').write_text(_json(body['spec']))
        (stage / 'targets.json').write_bytes(targets_raw)
        (stage / 'target-timing.jsonl').write_text(''.join(_json(row)+'\n' for row in backtest.target_timing))
        (stage / 'report.json').write_text(_json(report))
        (stage / 'research.json').write_text(_json(research))
        (stage / 'signals.json').write_text(_json(sections['signals']))
        if retain_diagnostics:
            (stage / 'execution-trace.jsonl').write_text(''.join(_json(row)+'\n' for row in backtest.execution_audit))
        backtest.generate_report(stage / 'report.xlsx',research=research)
        if benchmarks is not None:
            (stage / 'benchmarks.json').write_text(_json(benchmarks))
        if data_repository is not None:
            (stage / 'data-access.json').write_text(_json(dict(
                route='DataRepository.query -> DataRequest.accept -> backend visitor',
                qualification_data_id=source.data_id, reads=source.read_audit,
                qualification='Queried bars must exactly equal the qualified price artifact; no fallback')))
        for name, content in (attachments or {}).items():
            if Path(name).name != name or name in ('result.json','spec.json','targets.json','report.json',
                                                   'target-timing.jsonl','benchmarks.json','research.json','signals.json','execution-trace.jsonl','data-access.json','availability.json','report.xlsx','source.zip','manifest.json','manifest.sha256'):
                raise ValueError('Invalid or reserved attachment name')
            (stage / name).write_bytes(content() if callable(content) else content)
        with zipfile.ZipFile(stage / 'source.zip', 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            for name, content in sorted(code_files.items()):
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, content)
        if source_snapshot()[0] != code_id:
            raise ValueError('Source code changed during report export')
        for path in sorted(stage.iterdir()):
            manifest['files'][path.name] = digest(path)
        (stage / 'manifest.json').write_text(_json(manifest))
        (stage / 'manifest.sha256').write_text(digest(stage / 'manifest.json') + '\n')
        stage.rename(destination)
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    return result


class _RecipeSchedule:
    """Compose ordinary momentum decisions; account ownership stays in Backtest."""
    def __init__(self, segments, observations, trace):
        self.segments,self.observations,self.trace=segments,observations,trace

    def prepare_targets(self, source, start, end):
        from ..strategies.momentum_strategy import MomentumStrategy
        for segment in self.segments:
            view=QualifiedSpotSource(source.directory,repository=source.repository,
                bounds=(segment['history_start'],segment['end']))
            def trace(point):
                if segment['start']<=point.time<segment['end']:self.trace(point)
            controller=MomentumStrategy(segment['recipe'],self.observations,trace if self.trace else None)
            try:
                yield from controller.prepare_targets(view,segment['start'],segment['end'])
            finally:
                source.read_audit.extend(view.read_audit)


def run_momentum(snapshot, config, recipe_path, destination, auxiliary_path=None, retain_trace=False, *,
                 fixed_reference_weights=None, report_context=None, execution_delay_bars=0,
                 excluded_symbols=(), excluded_horizons=(), liquidity_path=None, liquidity_policy=None, data_repository=None, source_bounds=None,
                 recipe_schedule=None):
    """Prepare a fully configured recipe, then replay its known-at targets."""
    from ..strategies.momentum_strategy import AuxiliaryObservation, MomentumStrategy, load_recipe
    if Path(destination).exists():
        raise FileExistsError(destination)
    preparation_code_id = source_snapshot()[0]
    recipe_raw, config_raw = Path(recipe_path).read_bytes(), Path(config).read_bytes()
    auxiliary_raw = Path(auxiliary_path).read_bytes() if auxiliary_path else None
    observations = [AuxiliaryObservation(**row) for row in json.loads(auxiliary_raw)] if auxiliary_raw else ()
    attachments = {'recipe.json':recipe_raw}
    if auxiliary_raw is not None:
        attachments['auxiliary.json'] = auxiliary_raw
    with tempfile.TemporaryDirectory(prefix='momentum-targets-') as directory:
        frozen_recipe, frozen_config = Path(directory)/'recipe.json', Path(directory)/'config.json'
        frozen_recipe.write_bytes(recipe_raw)
        frozen_config.write_bytes(config_raw)
        recipe = load_recipe(frozen_recipe)
        source = QualifiedSpotSource(snapshot, repository=data_repository, bounds=source_bounds)
        raw_config = json.loads(config_raw)
        if raw_config['interval_seconds'] != 3600:
            raise ValueError('The supplied momentum recipe requires hourly bars')
        if set(dict(recipe.symbol_budgets)) != {r['symbol'] for r in raw_config['rules']}:
            raise ValueError('Recipe symbols must match the run instruments')
        trace_points = [] if retain_trace else None
        trace = trace_points.append if retain_trace else None
        controller = MomentumStrategy(recipe, observations, trace, excluded_symbols=excluded_symbols, excluded_horizons=excluded_horizons)
        if recipe_schedule is not None:
            if not recipe_schedule or excluded_symbols or excluded_horizons:
                raise ValueError('A nonempty recipe schedule without ablation overrides is required')
            segments=[];previous=utc(raw_config['start'])
            for item in recipe_schedule:
                if set(item)!={'fold','start','end','history_start','recipe_path'}:
                    raise ValueError('Explicit supported recipe schedule fields required')
                raw=Path(item['recipe_path']).read_bytes()
                path=Path(directory)/('recipe-'+str(len(segments))+'.json');path.write_bytes(raw)
                selected=load_recipe(path)
                start,end,history=(utc(item[k]) for k in ('start','end','history_start'))
                if start!=previous or end<=start or history!=start-selected.warmup_bars*timedelta(hours=1):
                    raise ValueError('Recipe schedules require contiguous windows and declared feature restarts')
                if any(getattr(selected,k)!=getattr(recipe,k) for k in
                       ('symbol_budgets','weight_caps','allocation_policy','rebalance','annualisation_hours')):
                    raise ValueError('Scheduled recipes require fixed universe, allocation and annualisation')
                # Constructing the existing view validates coverage before account execution.
                QualifiedSpotSource(snapshot,repository=data_repository,bounds=(history,end))
                segments.append(dict(fold=item['fold'],start=start,end=end,history_start=history,
                    recipe=selected,recipe_sha256=hashlib.sha256(raw).hexdigest()))
                previous=end
            if previous!=utc(raw_config['end']):raise ValueError('Recipe schedule must cover the complete account')
            controller=_RecipeSchedule(segments,observations,trace)
            schedule=[dict(s,recipe=asdict(s['recipe']),execution_start=s['start']+execution_delay_bars*timedelta(hours=1)) for s in segments]
            attachments['recipe-schedule.json']=_json(schedule).encode()
            report_context=dict(report_context or {},recipe_switches=schedule,
                pinned_recipe_role='Baseline fixes universe, allocation and annualisation; actual recipes are retained per switch',
                feature_state_policy='restart_from_declared_warmup',
                account_state_policy='carry_cash_holdings_and_cost_basis',
                switch_timing_policy='Recipe decisions activate at each test start; uniform execution delay applies across switches; pending prior decisions retain their scheduled times; only account-end executions are censored')
        if retain_trace:
            attachments['calculation-trace.jsonl'] = lambda: ''.join(_json(asdict(point))+'\n' for point in trace_points).encode()
        definition = dict(kind='configured-momentum-recipe', name=recipe.name,
                          recipe_sha256=hashlib.sha256(attachments['recipe.json']).hexdigest(),
                          auxiliary_sha256=hashlib.sha256(auxiliary_raw).hexdigest() if auxiliary_raw else None,
                          iv_mode=recipe.iv_mode, regime_mode=recipe.regime_mode, breaker_mode=recipe.breaker_mode,
                          assumptions=recipe.assumptions)
        if recipe_schedule is not None:
            definition.update(kind='scheduled-momentum-recipes',
                recipe_schedule_sha256=hashlib.sha256(attachments['recipe-schedule.json']).hexdigest())
        if source_snapshot()[0] != preparation_code_id:
            raise ValueError('Source code changed during target preparation')
        return run_snapshot(snapshot,frozen_config,None,destination,strategy=definition,attachments=attachments,
                            annualisation_hours=recipe.annualisation_hours,benchmark_weights=recipe.weight_caps,
                            expected_data_id=source.data_id,strategy_controller=controller,data_repository=data_repository,source_bounds=source_bounds,
                            fixed_reference_weights=fixed_reference_weights,
                            liquidity_path=liquidity_path,liquidity_policy=liquidity_policy,
                            signal_trace=trace_points,retain_diagnostics=retain_trace,execution_delay_bars=execution_delay_bars,
                            report_context=dict(report_context or {},pinned_recipe=asdict(recipe),
                                                interventions=dict(excluded_symbols=sorted(excluded_symbols),excluded_horizons=sorted(excluded_horizons),
                                                    asset_budget_policy='retain_original_budgets_cash',horizon_weight_policy='retain_original_weights'),
                                                disabled_adjustments=({name:getattr(recipe,name) for name in ('iv_mode','regime_mode','breaker_mode')}
                                                    if recipe_schedule is None else {'policy':'Adjustment modes are declared separately in each recipe switch'}),
                                                warmup=(dict(required_bars=recipe.warmup_bars,
                                                            policy='Recipe startup policy; not a statistical sufficiency claim') if recipe_schedule is None else
                                                    dict(policy='restart_from_declared_warmup',windows=[dict(fold=s['fold'],start=s['history_start'],
                                                        decision_start=s['start'],required_bars=s['recipe'].warmup_bars) for s in segments]))))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest='action', required=True)
    qualify = actions.add_parser('qualify')
    qualify.add_argument('--input', required=True)
    qualify.add_argument('--output', required=True)
    liquidity = actions.add_parser('qualify-liquidity',help='Qualify saved bar volumes separately from prices')
    for name in ('input','snapshot','output'):
        liquidity.add_argument('--'+name,required=True)
    liquidity.add_argument('--availability-lag-seconds',type=int,required=True)
    run = actions.add_parser('run')
    for name in ('snapshot', 'spec', 'targets', 'output'):
        run.add_argument('--' + name, required=True)
    momentum = actions.add_parser('momentum')
    for name in ('snapshot','spec','recipe','output'):
        momentum.add_argument('--' + name,required=True)
    momentum.add_argument('--auxiliary')
    momentum.add_argument('--trace',action='store_true')
    compare = actions.add_parser('compare', help='Run a bounded declared robustness plan')
    for name in ('snapshot','spec','recipe','cases','output'):
        compare.add_argument('--'+name, required=True)
    compare.add_argument('--auxiliary')
    compare.add_argument('--trace', action='store_true')
    for command in (run,momentum):
        command.add_argument('--duckdb',help='Existing local DuckDB file; bars must match the qualified --snapshot')
        command.add_argument('--liquidity')
        command.add_argument('--liquidity-policy',help='JSON policy: max_participation, impact_coefficient_bps, max_age_seconds')
        command.add_argument('--report-config',help='JSON: fixed_reference_weights, optional research_record and report_context; declared before evaluation')
    validate = actions.add_parser('validate',help='Run frozen chronological validation folds')
    for name in ('snapshot','spec','recipe','plan'):
        validate.add_argument('--'+name)
    validate.add_argument('--output',required=True)
    validate.add_argument('--portable',action='store_true')
    validate.add_argument('--replay',help='Verified portable study; replay through its frozen native repository')
    validate.add_argument('--auxiliary')
    validation_source=validate.add_mutually_exclusive_group()
    validation_source.add_argument('--duckdb')
    validation_source.add_argument('--parquet',help='JSON mapping of registered datasets to local Parquet file lists')
    validate.add_argument('--dry-run',action='store_true')
    validate.add_argument('--resolved-plan')
    validate.add_argument('--liquidity')
    validate.add_argument('--liquidity-policy')
    validate.add_argument('--report-config')
    validate.add_argument('--execution-delay-bars',type=int)
    validate.add_argument('--trace',action='store_true')
    capacity = actions.add_parser('capacity',help='Actual capital-size reruns with qualified bar liquidity')
    for name in ('snapshot','liquidity','spec','recipe','plan','output'):
        capacity.add_argument('--'+name,required=True)
    capacity.add_argument('--auxiliary')
    args = parser.parse_args(argv)
    if args.action == 'capacity':
        from .capacity import run_capacity
        result=run_capacity(args.snapshot,args.liquidity,args.spec,args.recipe,args.plan,args.output,auxiliary_path=args.auxiliary)
        print(json.dumps(dict(directory=args.output,cases=len(result['cases']),status=result['capacity_status'])))
    elif args.action == 'qualify-liquidity':
        from ..liquidity_inputs import qualify_liquidity
        path=qualify_liquidity(args.input,args.snapshot,args.output,availability_lag_seconds=args.availability_lag_seconds)
        print(json.dumps(dict(directory=str(path))))
    elif args.action == 'validate':
        from .validation import run_validation, replay_validation
        if args.replay:
            supplied=('snapshot','spec','recipe','plan','duckdb','parquet','auxiliary','liquidity',
                      'liquidity_policy','report_config','resolved_plan','execution_delay_bars')
            if any(getattr(args,name) is not None for name in supplied) or args.dry_run or args.portable or args.trace:
                parser.error('--replay uses only its frozen study inputs and --output')
            result=replay_validation(args.replay,args.output)
            print(json.dumps(dict(directory=args.output,folds=len(result['folds']),status='replay_verified')))
            return
        if not all((args.snapshot,args.spec,args.recipe,args.plan)):
            parser.error('validate requires --snapshot, --spec, --recipe and --plan, or --replay')
        client=None
        repository=None
        options=json.loads(Path(args.report_config).read_text()) if args.report_config else {}
        if set(options)-{'fixed_reference_weights','report_context','research_record'}:
            raise ValueError('Unknown report-config option')
        context=dict(options.get('report_context',{}))
        if 'research_record' in options:context['research_record']=options['research_record']
        if args.duckdb or args.parquet:
            from algoLibs.dao import DataRepository
            from algoLibs.market_data import DuckDBClientManager
            mapping=json.loads(Path(args.parquet).read_text()) if args.parquet else None
            client=DuckDBClientManager(path=args.duckdb or ':memory:',parquet_sources=mapping)
            repository=DataRepository(market_data_client_manager=client)
            context['storage_input']=(dict(backend='duckdb_parquet',mapping=mapping,
                files={str(Path(p).resolve()):digest(p) for paths in mapping.values() for p in paths},read_only=True)
                if mapping is not None else dict(backend='duckdb',path=str(Path(args.duckdb).resolve()),sha256=digest(args.duckdb),read_only=True))
        try:
            result=run_validation(args.snapshot,args.spec,args.recipe,args.plan,args.output,auxiliary_path=args.auxiliary,
                data_repository=repository,dry_run=args.dry_run,resolved_plan_path=args.resolved_plan,portable=args.portable,
                liquidity_path=args.liquidity,liquidity_policy=json.loads(Path(args.liquidity_policy).read_text()) if args.liquidity_policy else None,
                execution_delay_bars=args.execution_delay_bars or 0,retain_trace=args.trace,
                fixed_reference_weights=options.get('fixed_reference_weights'),report_context=context)
        finally:
            if client is not None:client.close()
        print(json.dumps(dict(directory=args.output,folds=len(result['folds']))))
    elif args.action == 'compare':
        from .robustness import run_robustness
        comparison = run_robustness(args.snapshot,args.spec,args.recipe,args.cases,args.output,
                                    auxiliary_path=args.auxiliary,retain_trace=args.trace)
        print(json.dumps(dict(directory=args.output,cases=len(comparison['cases']),
                              succeeded=sum(r['status']=='succeeded' for r in comparison['cases']))))
    elif args.action == 'qualify':
        source = qualify_price_bars(args.input, args.output)
        print(json.dumps(dict(directory=str(source.directory), data_id=source.data_id)))
    else:
        options=json.loads(Path(args.report_config).read_text()) if args.report_config else {}
        if set(options)-{'fixed_reference_weights','report_context','research_record'}:
            raise ValueError('Unknown report-config option')
        context=dict(options.get('report_context',{}))
        if 'research_record' in options:
            context['research_record']=options['research_record']
        report_options=dict(fixed_reference_weights=options.get('fixed_reference_weights'),report_context=context,
                            liquidity_path=args.liquidity,liquidity_policy=json.loads(Path(args.liquidity_policy).read_text()) if args.liquidity_policy else None)
        client = None
        if args.duckdb:
            from algoLibs.dao import DataRepository
            from algoLibs.market_data import DuckDBClientManager
            client = DuckDBClientManager(path=args.duckdb)
            report_options['data_repository'] = DataRepository(market_data_client_manager=client)
            context['storage_input'] = dict(backend='duckdb',path=str(Path(args.duckdb).resolve()),
                                            sha256=digest(args.duckdb),read_only=True)
        try:
            if args.action == 'momentum':
                result = run_momentum(args.snapshot,args.spec,args.recipe,args.output,args.auxiliary,args.trace,**report_options)
                strategy = 'configured-momentum-recipe'
            else:
                result = run_snapshot(args.snapshot, args.spec, args.targets, args.output,**report_options)
                strategy = 'externally-supplied-targets'
        finally:
            if client is not None:
                client.close()
        print(json.dumps(dict(directory=args.output, fills=len(result.fills),
                              final_cash=str(result.final_cash), equity=str(result.equity[-1].equity),
                              total_fees=str(result.total_fees), strategy=strategy)))


if __name__ == '__main__':
    main()
