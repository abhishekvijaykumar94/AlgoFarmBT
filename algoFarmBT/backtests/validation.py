"""Frozen chronological folds on the existing related-run/backtest workflow."""
from decimal import Decimal, localcontext
import json
import hashlib
from pathlib import Path
import re
import shutil
import tempfile
import zipfile

from algoLibs.market_data.types import utc
from algoLibs.backtest.excel_report import write_workbook
from algoLibs.backtest.report_frames import validation_frames
from algoLibs.backtest.validation_statistics import validation_statistics, validate_statistics_options
from ..spot_inputs import QualifiedSpotSource, local_file, digest
from .execute_momentum_backtest import run_momentum, source_snapshot, resolve_run_spec
from .related_runs import run_related_cases
from .robustness import _read, _write
from .research_history import eligibility_reasons, KNOWN_EXPOSURES


def run_validation(snapshot, config, recipe, plan_path, destination, *, auxiliary_path=None):
    """Run separate reset accounts; never link their NAV levels as one account."""
    destination=Path(destination)
    if destination.exists():
        raise FileExistsError(destination)
    raw_config,raw_recipe,raw_plan=(Path(p).read_bytes() for p in (config,recipe,plan_path))
    raw_auxiliary=Path(auxiliary_path).read_bytes() if auxiliary_path is not None else None
    plan=json.loads(raw_plan);base=json.loads(raw_config)
    required={'schema','name','hypothesis','capital_policy','selection_rule','recipe_sha256',
              'inspected_through','prior_trials','statistics','folds'}
    if not isinstance(plan,dict) or set(plan)!=required:
        raise ValueError('Explicit supported validation plan fields required')
    if plan['selection_rule']!='frozen_recipe':
        raise ValueError('Only predeclared frozen_recipe selection is supported; no full-sample selection')
    if any(not isinstance(plan[k],str) or not plan[k].strip() for k in ('name','hypothesis')):
        raise ValueError('Name and hypothesis required')
    if not isinstance(plan['prior_trials'],dict) or plan['prior_trials'].get('status') not in ('unknown','complete'):
        raise ValueError('Explicit known/unknown prior trial history required')
    if plan['inspected_through'] is not None:utc(plan['inspected_through'])
    validate_statistics_options(plan['statistics'])
    if plan['schema']!='chronological-validation-v1' or plan['capital_policy']!='independent_reset_accounts':
        raise ValueError('Explicit supported validation and capital/reset policy required')
    if hashlib.sha256(raw_recipe).hexdigest()!=plan['recipe_sha256']:
        raise ValueError('Selected recipe differs from the declared recipe hash')
    folds=plan['folds']
    if not isinstance(folds,list) or not 1<=len(folds)<=32:
        raise ValueError('Declare 1 to 32 chronological folds')
    ids=[r['id'] for r in folds]
    if any(not isinstance(i,str) or not re.fullmatch('[a-z][a-z0-9-]{0,47}',i) for i in ids) or len(ids)!=len(set(ids)):
        raise ValueError('Unique safe fold IDs required')
    history=dict(plan['prior_trials'])
    if history.get('status')=='complete':
        try:
            selection=min(utc(f['selected_at']) for f in folds)
            if utc(history['complete_through'])<selection or any(utc(r['evaluated_at'])>selection for r in history['records']):
                history.update(status='inadequate_temporal_history')
        except (ValueError,TypeError,KeyError):
            history.update(status='inadequate_temporal_history')
    previous_end=None
    for fold in folds:
        expected={'id','designation','development_start','development_end','calibration_end','selected_at','test_start','test_end'}
        if set(fold)!=expected or fold['designation'] not in ('test','development'):
            raise ValueError('Explicit supported fold fields and designation required')
        start,end=utc(fold['test_start']),utc(fold['test_end'])
        if start>=end or (previous_end is not None and start<previous_end):
            raise ValueError('Test windows must be chronological without overlap')
        if fold['designation']=='test' and utc(fold['selected_at'])>=start:
            raise ValueError('Recipe selection must precede the first test decision')
        if utc(fold['development_start'])>=utc(fold['development_end']) or utc(fold['development_end'])>utc(fold['calibration_end']):
            raise ValueError('Development must end by the calibration cutoff')
        if utc(fold['calibration_end'])>utc(fold['selected_at']):
            raise ValueError('Future calibration cannot follow recipe selection')
        previous_end=end
    source=QualifiedSpotSource(snapshot);code_id,code_files=source_snapshot()
    resolve_run_spec(base,source.data_id,code_id)
    destination.parent.mkdir(parents=True,exist_ok=True)
    stage=Path(tempfile.mkdtemp(prefix='.validation-',dir=destination.parent))
    try:
        common=stage/'common';common.mkdir()
        for name,content in (('spec.json',raw_config),('recipe.json',raw_recipe),('plan.json',raw_plan)):
            (common/name).write_bytes(content)
        if raw_auxiliary is not None:
            (common/'auxiliary.json').write_bytes(raw_auxiliary)
        prices=common/'prices';prices.mkdir()
        for name in ['manifest.json','manifest.sha256']+[e['file'] for e in source.manifest['entries']]:
            shutil.copyfile(local_file(source.directory,name),prices/name)
        frozen=QualifiedSpotSource(prices)
        if frozen.data_id!=source.data_id:
            raise ValueError('Price snapshot changed during freezing')
        for _ in frozen:pass
        def execute(fold, folder):
            resolved=dict(base,start=fold['test_start'],end=fold['test_end'])
            (folder/'spec-input.json').write_text(json.dumps(resolved,sort_keys=True,separators=(',',':')))
            reasons=eligibility_reasons(fold,plan,[r['symbol'] for r in base['rules']])
            designation='development' if reasons else 'test'
            evidence=dict(fold,requested_designation=fold['designation'],designation=designation,
                          eligible=not reasons,eligibility_reasons=reasons)
            run_momentum(prices,folder/'spec-input.json',common/'recipe.json',folder/'run',
                auxiliary_path=common/'auxiliary.json' if raw_auxiliary is not None else None,
                report_context=dict(validation_fold=evidence,research_record=dict(hypothesis=plan['hypothesis'],
                    designation=designation,parameter_changes=[],related_runs=ids)))
            return 'sha256:'+(folder/'run/manifest.sha256').read_text().strip()
        records=run_related_cases(folds,stage,execute,_write)
        segments=[];primary_segments=[]
        for row,fold in zip(records,folds):
            reasons=eligibility_reasons(fold,plan,[r['symbol'] for r in base['rules']])
            if row['status']!='succeeded':reasons.append('failed_fold')
            row.update(designation='development' if reasons else 'test',requested_designation=fold['designation'],
                       eligible=not reasons,eligibility_reasons=reasons,path=[],
                       metrics={},
                       report=row['artifacts']+'/run/report.xlsx' if row['status']=='succeeded' else None)
            if row['status']=='succeeded':
                result=_read(stage/row['artifacts']/'run/result.json')
                research=_read(stage/row['artifacts']/'run/research.json')
                canonical=_read(stage/row['artifacts']/'run/report.json')
                strategy=research['summary']['comparison'][0]
                row['metrics']=dict(net_pnl=canonical['net_pnl'],net_return=canonical['net_return'],
                    max_drawdown=canonical['max_drawdown'],total_fees=canonical['total_fees'],
                    modeled_slippage=canonical['modeled_slippage'],mean_close_exposure=strategy['mean_close_exposure'],
                    turnover=strategy['turnover'],complete_day_count=strategy['complete_day_count'],
                    annualised_daily_sharpe=strategy['annualised_daily_sharpe'])
                row['daily']=research['performance']['daily']
                segment=[]
                for day in row['daily']:
                    if not day['partial_period'] and day['complete_sampling'] and day['net_return'] is not None:
                        segment.append(day['net_return'])
                    elif segment:
                        segments.append(segment)
                        if row['eligible']:primary_segments.append(segment)
                        segment=[]
                if segment:
                    segments.append(segment)
                    if row['eligible']:primary_segments.append(segment)
                with localcontext() as context:
                    context.prec=80
                    row['path']=[dict(fold=row['case_id'],time=e['time'],equity=e['equity'],
                                     rebased_equity=e['equity']/result['spec']['initial_cash']) for e in result['equity']]
        report=dict(schema='chronological-validation-report-v1',name=plan['name'],capital_policy=plan['capital_policy'],
            data_id=source.data_id,code_id=code_id,folds=records,
            auxiliary_sha256=hashlib.sha256(raw_auxiliary).hexdigest() if raw_auxiliary is not None else None,
            primary=dict(continuous_account_return=None,eligible_folds=sum(r['eligible'] for r in records),
                         convention='Independent reset accounts; segmented paths only, no global compounded account',
                         statistics=validation_statistics(primary_segments,plan['statistics'],history)),
            exploratory=dict(statistics=validation_statistics(segments,plan['statistics'],history)),
            plan=plan,known_exposures=KNOWN_EXPOSURES,
            oos_segments=[dict(fold=r['case_id'],path=r['path']) for r in records if r['eligible']])
        _write(stage/'validation.json',report)
        write_workbook(stage/'validation.xlsx',validation_frames(report))
        with zipfile.ZipFile(stage/'source.zip','w',compression=zipfile.ZIP_DEFLATED) as archive:
            for name,content in sorted(code_files.items()):
                info=zipfile.ZipInfo(name,date_time=(1980,1,1,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED
                archive.writestr(info,content)
        if source_snapshot()[0]!=code_id:raise ValueError('Source changed during validation')
        files={p.relative_to(stage).as_posix():digest(p) for p in sorted(stage.rglob('*')) if p.is_file()}
        _write(stage/'manifest.json',dict(format='chronological-validation-v1',data_id=source.data_id,code_id=code_id,files=files))
        (stage/'manifest.sha256').write_text(digest(stage/'manifest.json')+'\n')
        stage.rename(destination)
    except BaseException:
        shutil.rmtree(stage,ignore_errors=True)
        raise
    return report
