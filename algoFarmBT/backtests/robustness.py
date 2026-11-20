"""Predeclared cost, delay and ablation reruns using the existing Backtest.

One bounded plan, one frozen price snapshot and a retained receipt for every
case. There is deliberately no optimiser or retrospective case selection.
"""
from dataclasses import dataclass, asdict
from decimal import Decimal, localcontext
import hashlib
import json
from pathlib import Path
import re
import shutil
import tempfile
import zipfile

from algoLibs.backtest.types import number
from algoLibs.market_data.types import decode
from algoLibs.backtest.excel_report import write_workbook
from algoLibs.backtest.report_frames import comparison_frames
from ..spot_inputs import QualifiedSpotSource, local_file, digest
from .execute_momentum_backtest import run_momentum, source_snapshot, resolve_run_spec, _json
from .related_runs import run_related_cases, case_metrics


@dataclass(frozen=True)
class RobustnessCase:
    id: str
    fee_multiplier: object = '1'
    slippage_multiplier: object = '1'
    execution_delay_bars: int = 0
    excluded_symbols: tuple = ()
    excluded_horizons: tuple = ()

    def __post_init__(self):
        if not isinstance(self.id, str) or not re.fullmatch('[a-z][a-z0-9-]{0,47}', self.id):
            raise ValueError('Case ID must be a unique safe lowercase name')
        for name in ('fee_multiplier', 'slippage_multiplier'):
            value = number(getattr(self, name))
            if not 0 <= value <= 100:
                raise ValueError('Cost multipliers must be from 0 to 100')
            object.__setattr__(self, name, value)
        if type(self.execution_delay_bars) is not int or not 0 <= self.execution_delay_bars <= 168:
            raise ValueError('Execution delay must be an integer from 0 to 168 bars')
        if not isinstance(self.excluded_symbols, (list, tuple)) or any(not isinstance(s, str) for s in self.excluded_symbols):
            raise ValueError('Explicit excluded symbol list required')
        pairs = self.excluded_horizons
        if not isinstance(pairs, (list, tuple)) or any(not isinstance(p, (list, tuple)) or len(p)!=2 or
                any(type(n) is not int or n<1 for n in p) for p in pairs):
            raise ValueError('Excluded horizons are explicit [fast, slow] pairs')
        symbols, horizons = tuple(self.excluded_symbols), tuple(tuple(p) for p in pairs)
        if len(set(symbols))!=len(symbols) or len(set(horizons))!=len(horizons):
            raise ValueError('Duplicate exclusions are not permitted')
        object.__setattr__(self, 'excluded_symbols', symbols)
        object.__setattr__(self, 'excluded_horizons', horizons)


def _write(path, value):
    Path(path).write_text(_json(value))


def _read(path):
    return decode(json.loads(Path(path).read_text()))


def load_plan(path):
    plan = json.loads(path if isinstance(path,bytes) else Path(path).read_bytes())
    required = {'schema','name','hypothesis','asset_budget_policy','horizon_weight_policy','cases'}
    if set(plan)!=required or plan['schema']!='momentum-robustness-v1':
        raise ValueError('Explicit supported robustness plan fields required')
    if any(not isinstance(plan[k],str) or not plan[k].strip() for k in ('name','hypothesis')):
        raise ValueError('Plan name and predeclared hypothesis required')
    if plan['asset_budget_policy']!='retain_original_budgets_cash' or plan['horizon_weight_policy']!='retain_original_weights':
        raise ValueError('Supported ablations retain remaining weights and leave excluded budgets in cash')
    cases = plan['cases']
    if not isinstance(cases,list) or not 1 <= len(cases) <= 32:
        raise ValueError('Declare from 1 to 32 cases, with baseline first')
    allowed = set(RobustnessCase.__dataclass_fields__)
    ids = []
    for case in cases:
        if not isinstance(case,dict) or not set(case)<=allowed or 'id' not in case:
            raise ValueError('Unknown or missing case fields')
        ident = case['id']
        if not isinstance(ident,str) or not re.fullmatch('[a-z][a-z0-9-]{0,47}',ident):
            raise ValueError('Unsafe case ID')
        ids.append(ident)
    if len(set(ids))!=len(ids) or ids[0]!='baseline' or RobustnessCase(**cases[0])!=RobustnessCase('baseline'):
        raise ValueError('Unique case IDs and an unchanged baseline first are required')
    return plan


def _resolved_config(base, case):
    config = json.loads(json.dumps(base))
    with localcontext() as context:
        context.prec = 80
        for rule in config['rules']:
            for field, multiplier in (('fee_rate',case.fee_multiplier),('fixed_fee',case.fee_multiplier),
                                      ('slippage_bps',case.slippage_multiplier)):
                rule[field] = str((number(rule[field])*multiplier).normalize())
    return config




def _cost_brackets(rows):
    """Observed sign-change intervals from actual matched joint-cost reruns."""
    eligible = []
    for row in rows:
        p = row.get('parameters',{})
        if row['status']=='succeeded' and p['fee_multiplier']==p['slippage_multiplier'] and not (
                p['execution_delay_bars'] or p['excluded_symbols'] or p['excluded_horizons']):
            eligible.append(row)
    eligible.sort(key=lambda r:r['parameters']['fee_multiplier'])
    brackets = []
    for a,b in zip(eligible,eligible[1:]):
        left,right = a['metrics']['net_pnl'],b['metrics']['net_pnl']
        if a['parameters']['fee_multiplier']<b['parameters']['fee_multiplier'] and (left==0 or right==0 or (left<0)!=(right<0)):
            brackets.append(dict(lower_case=a['case_id'],upper_case=b['case_id'],
                lower_multiplier=a['parameters']['fee_multiplier'],upper_multiplier=b['parameters']['fee_multiplier'],
                lower_net_pnl=left,upper_net_pnl=right))
    return dict(status='observed_brackets' if brackets else 'unavailable_no_observed_sign_change',brackets=brackets,
                convention='Joint fee/slippage scalar; observed endpoint reruns only, not an interpolated root or monotonicity guarantee')


def run_robustness(snapshot, config, recipe, plan_path, destination, *, auxiliary_path=None, retain_trace=False):
    """Freeze common inputs, execute every declared case and save one comparison."""
    destination = Path(destination)
    if destination.exists():
        raise FileExistsError(destination)
    config_raw,recipe_raw,plan_raw = (Path(p).read_bytes() for p in (config,recipe,plan_path))
    plan = load_plan(plan_raw)
    auxiliary_raw = Path(auxiliary_path).read_bytes() if auxiliary_path else None
    source = QualifiedSpotSource(snapshot)
    code_id,code_files = source_snapshot()
    base = json.loads(config_raw)
    common_spec, seconds = resolve_run_spec(base,source.data_id,code_id)
    if seconds != 3600:
        raise ValueError('Momentum robustness requires hourly bars')
    sample = dict(start=common_spec.start,end=common_spec.end,interval_seconds=seconds,
                  initial_cash=common_spec.initial_cash,quote_currency=common_spec.quote_currency)
    destination.parent.mkdir(parents=True,exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix='.related-runs-',dir=destination.parent))
    try:
        common = stage/'common'; common.mkdir()
        for name,raw in (('spec.json',config_raw),('recipe.json',recipe_raw),('plan.json',plan_raw)):
            (common/name).write_bytes(raw)
        if auxiliary_raw is not None:
            (common/'auxiliary.json').write_bytes(auxiliary_raw)
        prices = common/'prices'; prices.mkdir()
        for name in ['manifest.json','manifest.sha256']+[e['file'] for e in source.manifest['entries']]:
            shutil.copyfile(local_file(source.directory,name),prices/name)
        frozen = QualifiedSpotSource(prices)
        if frozen.data_id!=source.data_id:
            raise ValueError('Price snapshot changed while freezing common inputs')
        for _ in frozen:
            pass  # verifies every pinned file and row before any case runs
        def execute(raw, folder):
            case = RobustnessCase(**raw)
            resolved = _resolved_config(base,case)
            (folder/'spec-input.json').write_text(json.dumps(resolved,sort_keys=True,separators=(',',':')))
            _write(folder/'resolved-case.json',asdict(case))
            run_momentum(prices,folder/'spec-input.json',common/'recipe.json',folder/'run',
                         common/'auxiliary.json' if auxiliary_raw is not None else None,retain_trace,
                         excluded_symbols=case.excluded_symbols,excluded_horizons=case.excluded_horizons,
                         execution_delay_bars=case.execution_delay_bars,
                         report_context=dict(related_run=dict(plan=plan['name'],case_id=case.id,hypothesis=plan['hypothesis'],
                             designation='development',asset_budget_policy=plan['asset_budget_policy'],
                             horizon_weight_policy=plan['horizon_weight_policy']),
                             research_record=dict(hypothesis=plan['hypothesis'],designation='development',
                                                  parameter_changes=asdict(case),related_runs=[r['id'] for r in plan['cases']])))
            return 'sha256:' + (folder/'run/manifest.sha256').read_text().strip()
        records = run_related_cases(plan['cases'],stage,execute,_write)
        for row in records:
            folder = stage/row['artifacts']
            row['parameters'] = _read(folder/'resolved-case.json') if (folder/'resolved-case.json').exists() else next(c for c in plan['cases'] if c['id']==row['case_id'])
            row['metrics'] = case_metrics(folder,row)
            row['report'] = row['artifacts']+'/run/report.xlsx' if row['status']=='succeeded' else None
        baseline_pnl = records[0]['metrics'].get('net_pnl')
        baseline_return = records[0]['metrics'].get('net_return')
        with localcontext() as context:
            context.prec = 80
            for row in records:
                row['metrics']['delta_net_pnl_vs_baseline'] = row['metrics']['net_pnl']-baseline_pnl if baseline_pnl is not None and row['status']=='succeeded' else None
                row['metrics']['delta_net_return_vs_baseline'] = row['metrics']['net_return']-baseline_return if baseline_return is not None and row['status']=='succeeded' else None
        comparison = dict(schema='related-run-comparison-v1',name=plan['name'],hypothesis=plan['hypothesis'],
            designation='development',data_id=source.data_id,code_id=code_id,cases=records,
            sample=sample,
            common_inputs=dict(spec='common/spec.json',recipe='common/recipe.json',plan='common/plan.json',prices='common/prices',
                               auxiliary='common/auxiliary.json' if auxiliary_raw is not None else None),
            conventions=dict(asset_budget_policy=plan['asset_budget_policy'],horizon_weight_policy=plan['horizon_weight_policy'],
                sample='Identical run dates, warmup, prices, initial capital and benchmark weights; only declared case interventions differ',
                effects='Marginal rerun differences, including nonlinear clip/cap/sizing effects; ablations are not additive P&L attribution',
                costs='Multipliers apply to fee_rate AND fixed_fee, and adverse slippage_bps; every case reruns fee-aware account sizing',
                break_even='same_fill_break_even_fee_bps retains actual fills and slippage; rerun_cost_brackets use actual joint-cost reruns',
                missing='Failed cases retained with failure receipts; undefined risk remains unavailable; no automatic selection'),
            rerun_cost_brackets=_cost_brackets(records))
        _write(stage/'comparison.json',comparison)
        write_workbook(stage/'comparison.xlsx',comparison_frames(comparison))
        with zipfile.ZipFile(stage/'source.zip','w',compression=zipfile.ZIP_DEFLATED) as archive:
            for name,content in sorted(code_files.items()):
                info=zipfile.ZipInfo(name,date_time=(1980,1,1,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED
                archive.writestr(info,content)
        if source_snapshot()[0]!=code_id:
            raise ValueError('Source code changed during related runs')
        files = {p.relative_to(stage).as_posix():digest(p) for p in sorted(stage.rglob('*')) if p.is_file()}
        _write(stage/'manifest.json',dict(format='related-runs-v1',data_id=source.data_id,code_id=code_id,
                                        case_ids=[r['case_id'] for r in records],files=files))
        (stage/'manifest.sha256').write_text(digest(stage/'manifest.json')+'\n')
        stage.rename(destination)
    except BaseException:
        shutil.rmtree(stage,ignore_errors=True)
        raise
    return comparison
