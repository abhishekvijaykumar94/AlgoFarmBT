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


def run_snapshot(snapshot, config, targets_path, destination, *, strategy=None,
                 attachments=None, annualisation_hours=None, benchmark_weights=None, expected_data_id=None, strategy_controller=None,
                 fixed_reference_weights=None, report_context=None, signal_trace=None, retain_diagnostics=False):
    destination = Path(destination)
    if destination.exists():
        raise FileExistsError(destination)
    source = QualifiedSpotSource(snapshot)
    if expected_data_id is not None and source.data_id != expected_data_id:
        raise ValueError('Snapshot changed after target preparation')
    raw = json.loads(Path(config).read_text())
    seconds = raw.pop('interval_seconds')
    if type(seconds) is not int or seconds <= 0:
        raise ValueError('interval_seconds must be a positive integer')
    raw['interval'] = timedelta(seconds=seconds)
    raw['rules'] = tuple(SpotRules(**r) for r in raw['rules'])
    if 'data_id' in raw or 'code_id' in raw:
        raise ValueError('Data and code identities are resolved from actual inputs')
    code_id, code_files = source_snapshot()
    spec = SpotRunSpec(**raw, data_id=source.data_id, code_id=code_id)
    # References are declared before evaluating any strategy returns.
    with localcontext() as decimal_context:
        decimal_context.prec=28
        benchmark_weights=(dict(benchmark_weights) if benchmark_weights is not None else
                           {r.symbol:(Decimal(1)/len(spec.rules)).quantize(Decimal('1e-24'),rounding=ROUND_DOWN) for r in spec.rules})
    fixed_reference_weights=dict(fixed_reference_weights) if fixed_reference_weights is not None else None
    context=dict(report_context or {})
    if strategy_controller is not None:
        backtest = Backtest.from_prepared(spec, source, strategy=strategy_controller, annualisation_hours=annualisation_hours, retain_diagnostics=retain_diagnostics)
        result = backtest.run()
        rows = [dict(time=t.time.isoformat(),weights={s:str(w) for s,w in t.weights},rebalance=t.rebalance) for t in backtest.target_log]
        targets_raw = json.dumps(rows,sort_keys=True,separators=(',',':')).encode()
    else:
        targets_raw = Path(targets_path).read_bytes()
        targets = [SpotTarget(**t) for t in json.loads(targets_raw)]
        backtest = Backtest.from_prepared(spec, source, targets=targets, annualisation_hours=annualisation_hours, retain_diagnostics=retain_diagnostics)
        result = backtest.run()
    report = backtest.performance_report()
    benchmarks = spot_benchmarks(spec, source, benchmark_weights, annualisation_hours,
                                 fixed_reference_weights=fixed_reference_weights)
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
                                               policy=benchmarks['policy']))
    sections=backtest.report_sections(signal_trace=signal_trace)
    research=backtest.research_report(context=context,benchmarks=benchmarks,sections=sections)
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix='.spot-run-', dir=destination.parent))
    try:
        (stage / 'result.json').write_text(_json(body))
        (stage / 'spec.json').write_text(_json(body['spec']))
        (stage / 'targets.json').write_bytes(targets_raw)
        (stage / 'report.json').write_text(_json(report))
        (stage / 'research.json').write_text(_json(research))
        (stage / 'signals.json').write_text(_json(sections['signals']))
        if retain_diagnostics:
            (stage / 'execution-trace.jsonl').write_text(''.join(_json(row)+'\n' for row in backtest.execution_audit))
        backtest.generate_report(stage / 'report.xlsx',research=research)
        if benchmarks is not None:
            (stage / 'benchmarks.json').write_text(_json(benchmarks))
        for name, content in (attachments or {}).items():
            if Path(name).name != name or name in ('result.json','spec.json','targets.json','report.json',
                                                   'benchmarks.json','research.json','signals.json','execution-trace.jsonl','report.xlsx','source.zip','manifest.json','manifest.sha256'):
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


def run_momentum(snapshot, config, recipe_path, destination, auxiliary_path=None, retain_trace=False, *,
                 fixed_reference_weights=None, report_context=None):
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
        source = QualifiedSpotSource(snapshot)
        raw_config = json.loads(config_raw)
        if raw_config['interval_seconds'] != 3600:
            raise ValueError('The supplied momentum recipe requires hourly bars')
        if set(dict(recipe.symbol_budgets)) != {r['symbol'] for r in raw_config['rules']}:
            raise ValueError('Recipe symbols must match the run instruments')
        trace_points = [] if retain_trace else None
        trace = trace_points.append if retain_trace else None
        controller = MomentumStrategy(recipe, observations, trace)
        if retain_trace:
            attachments['calculation-trace.jsonl'] = lambda: ''.join(_json(asdict(point))+'\n' for point in trace_points).encode()
        definition = dict(kind='configured-momentum-recipe', name=recipe.name,
                          recipe_sha256=hashlib.sha256(attachments['recipe.json']).hexdigest(),
                          auxiliary_sha256=hashlib.sha256(auxiliary_raw).hexdigest() if auxiliary_raw else None,
                          iv_mode=recipe.iv_mode, regime_mode=recipe.regime_mode, breaker_mode=recipe.breaker_mode,
                          assumptions=recipe.assumptions)
        if source_snapshot()[0] != preparation_code_id:
            raise ValueError('Source code changed during target preparation')
        return run_snapshot(snapshot,frozen_config,None,destination,strategy=definition,attachments=attachments,
                            annualisation_hours=recipe.annualisation_hours,benchmark_weights=recipe.weight_caps,
                            expected_data_id=source.data_id,strategy_controller=controller,
                            fixed_reference_weights=fixed_reference_weights,
                            signal_trace=trace_points,retain_diagnostics=retain_trace,
                            report_context=dict(report_context or {},pinned_recipe=asdict(recipe),
                                                disabled_adjustments={name:getattr(recipe,name) for name in ('iv_mode','regime_mode','breaker_mode')},
                                                warmup=dict(required_bars=recipe.warmup_bars,
                                                            policy='Recipe startup policy; not a statistical sufficiency claim')))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest='action', required=True)
    qualify = actions.add_parser('qualify')
    qualify.add_argument('--input', required=True)
    qualify.add_argument('--output', required=True)
    run = actions.add_parser('run')
    for name in ('snapshot', 'spec', 'targets', 'output'):
        run.add_argument('--' + name, required=True)
    momentum = actions.add_parser('momentum')
    for name in ('snapshot','spec','recipe','output'):
        momentum.add_argument('--' + name,required=True)
    momentum.add_argument('--auxiliary')
    momentum.add_argument('--trace',action='store_true')
    for command in (run,momentum):
        command.add_argument('--report-config',help='JSON: fixed_reference_weights, optional research_record and report_context; declared before evaluation')
    args = parser.parse_args(argv)
    if args.action == 'qualify':
        source = qualify_price_bars(args.input, args.output)
        print(json.dumps(dict(directory=str(source.directory), data_id=source.data_id)))
    else:
        options=json.loads(Path(args.report_config).read_text()) if args.report_config else {}
        if set(options)-{'fixed_reference_weights','report_context','research_record'}:
            raise ValueError('Unknown report-config option')
        context=dict(options.get('report_context',{}))
        if 'research_record' in options:
            context['research_record']=options['research_record']
        report_options=dict(fixed_reference_weights=options.get('fixed_reference_weights'),report_context=context)
        if args.action == 'momentum':
            result = run_momentum(args.snapshot,args.spec,args.recipe,args.output,args.auxiliary,args.trace,**report_options)
            strategy = 'configured-momentum-recipe'
        else:
            result = run_snapshot(args.snapshot, args.spec, args.targets, args.output,**report_options)
            strategy = 'externally-supplied-targets'
        print(json.dumps(dict(directory=args.output, fills=len(result.fills),
                              final_cash=str(result.final_cash), equity=str(result.equity[-1].equity),
                              total_fees=str(result.total_fees), strategy=strategy)))


if __name__ == '__main__':
    main()
