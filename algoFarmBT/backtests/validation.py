"""Frozen chronological folds on the existing related-run/backtest workflow."""
from decimal import Decimal, localcontext
from datetime import timedelta
from datetime import datetime, timezone
from dataclasses import asdict
import json
import hashlib
import copy
import math
from pathlib import Path
import re
import shutil
import tempfile
import zipfile

from algoLibs.market_data.types import utc, DataRequest
from algoLibs.backtest.excel_report import write_workbook
from algoLibs.backtest.report_frames import validation_frames
from algoLibs.backtest.validation_statistics import validation_statistics, validate_statistics_options
from ..spot_inputs import QualifiedSpotSource, local_file, digest
from .execute_momentum_backtest import run_momentum, source_snapshot, resolve_run_spec, _json
from .related_runs import run_related_cases, case_metrics
from .robustness import _read, _write
from .research_history import eligibility_reasons, KNOWN_EXPOSURES


def _native_requests(coverage):
    """Merge overlapping actual repository selections without widening identities."""
    groups={}
    for window in coverage:
        for audit in window['reads']:
            request=DataRequest(**audit['request'])
            key=_json(dict(dataset=request.dataset,filters=request.filters,time_basis=request.time_basis))
            groups.setdefault(key,[]).append(request)
    requests=[]
    for key in sorted(groups):
        merged=[]
        for request in sorted(groups[key],key=lambda r:r.start):
            if merged and request.start<=merged[-1]['end']:
                merged[-1]['end']=max(merged[-1]['end'],request.end)
            else:
                merged.append(dict(asdict(request),columns=()))
        requests.extend(DataRequest(**r) for r in merged)
    return requests


def _reference_requests(repository, market_requests):
    """Capture only available cash-spot identity metadata for selected bars."""
    catalog=repository.catalog()
    instruments=sorted({r.filters['instrument_id'] for r in market_requests})
    venues=sorted({r.filters['venue_id'] for r in market_requests})
    result=[];assets=set();pairs={}
    for request in market_requests:
        pairs.setdefault(request.filters['venue_id'],set()).add(request.filters['instrument_id'])
    selections=[('public.instrument',{'instrument_id':instruments}),('public.venue',{'venue_id':venues})]
    selections += [('public.venue_instrument',{'instrument_id':sorted(values),'venue_id':venue})
                   for venue,values in sorted(pairs.items())]
    for name,filters in selections:
        if name not in catalog or not catalog[name].get('available',True):continue
        request=DataRequest(name,filters=filters)
        for batch in repository.query(request):
            for row in batch:
                assets.update(row[k] for k in ('asset_base_id','asset_quote_id','settle_ccy_id','price_ccy_id') if row.get(k) is not None)
        result.append(request)
    if assets and 'public.asset' in catalog and catalog['public.asset'].get('available',True):
        result.append(DataRequest('public.asset',filters={'asset_id':sorted(assets)}))
    return result


def _seal_validation(directory, data_id, code_id):
    files={p.relative_to(directory).as_posix():digest(p) for p in sorted(directory.rglob('*'))
           if p.is_file() and not (p.parent==directory and p.name in ('manifest.json','manifest.sha256'))}
    _write(directory/'manifest.json',dict(format='chronological-validation-v1',data_id=data_id,code_id=code_id,files=files))
    (directory/'manifest.sha256').write_text(digest(directory/'manifest.json')+'\n')


def _verified_validation(directory):
    directory=Path(directory)
    if digest(directory/'manifest.json')!=(directory/'manifest.sha256').read_text().strip():
        raise ValueError('Validation manifest hash mismatch')
    manifest=_read(directory/'manifest.json')
    if manifest['format']!='chronological-validation-v1':raise ValueError('Unsupported validation artifact')
    for name,sha in manifest['files'].items():
        path=directory/name
        if not path.resolve().is_relative_to(directory.resolve()) or path.is_symlink() or not path.is_file() or digest(path)!=sha:
            raise ValueError('Validation artifact missing or hash mismatch: '+name)
    actual={p.relative_to(directory).as_posix() for p in directory.rglob('*') if p.is_file()}
    if actual!=set(manifest['files'])|{'manifest.json','manifest.sha256'}:
        raise ValueError('Unexpected validation artifact inventory')
    return manifest


def _experiment(definition):
    # Replay changes physical access provenance, never research inputs or windows.
    definition=json.loads(_json(definition))
    from algoLibs.market_data.types import decode
    definition=decode(definition)
    context=dict(definition['options']['report_context'] or {})
    context.pop('storage_input',None)
    definition['options']['report_context']=context
    return _json(definition)


def _financial_evidence(report):
    """Compare all suite evidence except actual execution/physical-access facts."""
    report=copy.deepcopy(report)
    for fold in report['folds']+([report['continuous']] if 'continuous' in report else []):
        fold.pop('run_id',None)
        fold.pop('error',None)  # Backend-specific error text remains in receipts.
    for selection in report.get('selections',{}).values():
        selection.pop('performed_at',None)
        for trial in selection['trials']:
            trial.pop('run_id',None)
            trial.pop('error',None)
    return _json(report)


def replay_validation(study, destination):
    """Verify a portable study, then use the same validation runner offline."""
    from algoLibs.market_data.snapshots import SnapshotReader
    study,destination=Path(study),Path(destination)
    if destination.exists():raise FileExistsError(destination)
    source_sha=digest(study/'manifest.json')
    manifest=_verified_validation(study)
    original=_read(study/'resolved-plan.json')
    if not original['options'].get('portable'):
        raise ValueError('Replay requires a portable native validation study')
    common=study/'common';native=common/'native-market-data'
    snapshot=SnapshotReader(native)
    options=original['options']
    context=dict(options['report_context'] or {})
    context['storage_input']=dict(backend='native_snapshot',path=str(native.resolve()),
        sha256=digest(native/'manifest.json'),source_study_manifest_sha256=source_sha,
        original_storage_input=context.get('storage_input'))
    kwargs=dict(data_repository=snapshot.repository,portable=True,native_snapshot_path=native,
        auxiliary_path=common/'auxiliary.json' if original['inputs']['auxiliary'] is not None else None,
        liquidity_path=common/'liquidity' if options['liquidity_data_id'] is not None else None,
        liquidity_policy=options['liquidity_policy'],execution_delay_bars=options['execution_delay_bars'],
        retain_trace=options['retain_trace'],fixed_reference_weights=options['fixed_reference_weights'],report_context=context)
    destination.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.validation-replay-',dir=destination.parent) as temporary:
        stage=Path(temporary)
        preview=run_validation(common/'prices',common/'spec.json',common/'recipe.json',common/'plan.json',stage/'preview',
            dry_run=True,**kwargs)
        if _experiment(preview)!=_experiment(original):
            raise ValueError('Replay experiment or source code differs from the frozen study; use its archived source')
        output=stage/'completed'
        report=run_validation(common/'prices',common/'spec.json',common/'recipe.json',common/'plan.json',output,
            resolved_plan_path=stage/'preview/resolved-plan.json',**kwargs)
        stable={'result.json','spec.json','targets.json','target-timing.jsonl','report.json','signals.json',
                'benchmarks.json','execution-trace.jsonl','calculation-trace.jsonl','recipe.json','selected-recipe.json','recipe-schedule.json'}
        hashes={}
        for name,sha in manifest['files'].items():
            if name.startswith('cases/') and Path(name).name in stable:
                if not (output/name).is_file() or digest(output/name)!=sha:
                    raise ValueError('Replay deterministic artifact differs: '+name)
                hashes[name]=dict(original=sha,replay=digest(output/name))
        old_report=_read(study/'validation.json')
        if _financial_evidence(report)!=_financial_evidence(old_report):
            raise ValueError('Replay suite financial evidence differs')
        if [r['status'] for r in report['folds']]!=[r['status'] for r in old_report['folds']]:
            raise ValueError('Replay fold outcomes differ')
        for fold,selection in old_report.get('selections',{}).items():
            actual=report['selections'][fold]
            fields=('status','selected_candidate','selected_recipe_sha256','objective','training_data_cutoff','simulated_selection_cutoff')
            if any(actual[k]!=selection[k] for k in fields):raise ValueError('Replay selection differs: '+fold)
            fields=('case_id','status','recipe_sha256','score','eligible','metrics','ineligible_reason')
            if [{k:t[k] for k in fields} for t in actual['trials']]!=[{k:t[k] for k in fields} for t in selection['trials']]:
                raise ValueError('Replay training evidence differs: '+fold)
        if digest(study/'manifest.json')!=source_sha:
            raise ValueError('Source study changed during replay')
        _verified_validation(study)
        _write(output/'replay-verification.json',dict(status='verified',source_manifest_sha256=source_sha,
            code_id=manifest['code_id'],data_id=manifest['data_id'],deterministic_files=hashes,
            convention='Financial, signal and parameter hashes match; actual timestamps and backend provenance are retained independently'))
        _seal_validation(output,manifest['data_id'],manifest['code_id'])
        output.rename(destination)
    return report


def _candidate_recipes(selection, raw_recipe):
    """Resolve a finite predeclared recipe search with the existing loader."""
    from ..strategies.momentum_strategy import load_recipe
    expected={'rule','objective','minimum_complete_training_days','tie_break','candidate_failure_policy','candidates'}
    if not isinstance(selection,dict) or set(selection)!=expected or selection['rule']!='training_rank':
        raise ValueError('Explicit supported training selection fields required')
    if selection['objective'] not in ('net_return','annualised_daily_sharpe'):
        raise ValueError('Unsupported canonical training objective')
    if selection['tie_break']!='resolved_recipe_sha256_ascending' or selection['candidate_failure_policy']!='fail_fold':
        raise ValueError('Declare recipe-hash tie break and fail_fold candidate policy')
    if type(selection['minimum_complete_training_days']) is not int or selection['minimum_complete_training_days']<1:
        raise ValueError('Positive minimum complete training days required')
    cases=selection['candidates']
    if not isinstance(cases,list) or not 1<=len(cases)<=32:
        raise ValueError('Declare 1 to 32 training candidates')
    result=[];ids=set();hashes=set()
    with tempfile.TemporaryDirectory(prefix='validation-recipes-') as temporary:
        path=Path(temporary)/'recipe.json'
        path.write_bytes(raw_recipe);baseline=load_recipe(path)
        for case in cases:
            if not isinstance(case,dict) or set(case)!={'id','recipe_overrides'}:
                raise ValueError('Explicit candidate id and recipe_overrides required')
            identity=case['id']
            if not isinstance(identity,str) or not re.fullmatch('[a-z][a-z0-9-]{0,47}',identity) or identity in ids:
                raise ValueError('Unique safe candidate IDs required')
            if not isinstance(case['recipe_overrides'],dict):raise ValueError('Recipe overrides must be an object')
            raw=dict(json.loads(raw_recipe),**case['recipe_overrides'])
            path.write_text(json.dumps(raw))
            resolved=load_recipe(path)
            if any(getattr(resolved,key)!=getattr(baseline,key) for key in
                   ('symbol_budgets','weight_caps','allocation_policy','rebalance','annualisation_hours')):
                raise ValueError('Candidate comparison assumptions must keep allocation, universe and annualisation fixed')
            content=json.dumps(asdict(resolved),sort_keys=True,separators=(',',':'),default=str,allow_nan=False)
            sha=hashlib.sha256(content.encode()).hexdigest()
            if sha in hashes:raise ValueError('Duplicate resolved candidate recipes')
            ids.add(identity);hashes.add(sha)
            result.append(dict(id=identity,recipe=json.loads(content),recipe_sha256=sha,warmup_bars=resolved.warmup_bars))
    return result


def _generated_folds(plan, warmup):
    """Resolve scored training declarations and bounded test feature history."""
    if plan['mode'] not in ('holdout','rolling','expanding'):
        raise ValueError('Unsupported validation schedule mode')
    if plan['feature_history_policy'] != 'restart_from_declared_warmup':
        raise ValueError('Explicit restart_from_declared_warmup policy required')
    if plan['designation'] not in ('test','development'):
        raise ValueError('Explicit test/development designation required')
    if set(plan['bounds']) != {'start','end'}:
        raise ValueError('Explicit UTC schedule bounds required')
    start,end=(utc(plan['bounds'][k]) for k in ('start','end'))
    if start>=end or any(t.minute or t.second or t.microsecond for t in (start,end)):
        raise ValueError('Chronological hourly aligned bounds required')
    window=plan['window']
    if set(window)!={'train_bars','test_bars','step_bars','gap_bars','partial_final_fold'}:
        raise ValueError('Explicit supported window fields required')
    for key in ('train_bars','test_bars','step_bars','gap_bars'):
        if type(window[key]) is not int or window[key] < (0 if key=='gap_bars' else 1):
            raise ValueError('Window lengths must be integer bar counts')
    if window['step_bars']<window['test_bars']:
        raise ValueError('Test windows must not overlap')
    if window['partial_final_fold'] not in ('drop','include','reject'):
        raise ValueError('Explicit partial_final_fold policy required')
    maximum=1056 if plan['selection_rule']=='training_rank' else 32
    if type(plan['max_evaluations']) is not int or not 1<=plan['max_evaluations']<=maximum:
        raise ValueError('Evaluation budget must be 1 to '+str(maximum))
    hour=timedelta(hours=1)
    folds=[];views=[];offset=0
    while True:
        train_start=start if plan['mode']=='expanding' else start+offset*hour
        train_end=start+(offset+window['train_bars'])*hour
        test_start=train_end+window['gap_bars']*hour
        if test_start>=end:break
        test_end=test_start+window['test_bars']*hour
        if test_end>end:
            if window['partial_final_fold']=='drop':break
            if window['partial_final_fold']=='reject':raise ValueError('Partial final test window rejected')
            test_end=end
        if len(folds)>=min(32,plan['max_evaluations']):
            raise ValueError('Evaluation budget exceeded before trials')
        fold=dict(id='fold-%03d'%(len(folds)+1),designation=plan['designation'],development_start=train_start.isoformat(),
            development_end=train_end.isoformat(),calibration_end=train_end.isoformat(),selected_at=train_end.isoformat(),
            test_start=test_start.isoformat(),test_end=test_end.isoformat())
        folds.append(fold)
        views.append(dict(id=fold['id'],train_start=train_start,train_end=train_end,test_start=test_start,test_end=test_end,
            training_history_start=train_start-warmup*hour,history_start=test_start-warmup*hour,warmup_bars=warmup))
        if plan['mode']=='holdout':break
        offset+=window['step_bars']
    if not folds:raise ValueError('No complete test window in schedule bounds')
    return folds,views


def run_validation(snapshot, config, recipe, plan_path, destination, *, auxiliary_path=None,
                   data_repository=None, dry_run=False, resolved_plan_path=None,
                   liquidity_path=None, liquidity_policy=None, execution_delay_bars=0,
                   retain_trace=False, fixed_reference_weights=None, report_context=None,
                   portable=False, native_snapshot_path=None):
    """Run fold diagnostics and an explicitly requested shared portfolio account."""
    destination=Path(destination)
    if destination.exists():
        raise FileExistsError(destination)
    raw_config,raw_recipe,raw_plan=(Path(p).read_bytes() for p in (config,recipe,plan_path))
    raw_auxiliary=Path(auxiliary_path).read_bytes() if auxiliary_path is not None else None
    plan=json.loads(raw_plan);base=json.loads(raw_config)
    required={'schema','name','hypothesis','capital_policy','selection_rule','recipe_sha256',
              'inspected_through','prior_trials','statistics','folds'}
    generated=isinstance(plan,dict) and plan.get('schema')=='chronological-validation-v2'
    ranking=generated and plan.get('selection_rule')=='training_rank'
    continuous=isinstance(plan,dict) and plan.get('capital_policy')=='continuous_account'
    if generated:
        required=(required-{'folds'})|{'mode','bounds','window','feature_history_policy','max_evaluations','designation'}
    if ranking:required=required|{'selection'}
    if not isinstance(plan,dict) or set(plan)!=required:
        raise ValueError('Explicit supported validation plan fields required')
    if plan['selection_rule']!='frozen_recipe' and not ranking:
        raise ValueError('Only predeclared frozen_recipe selection is supported; no full-sample selection')
    if any(not isinstance(plan[k],str) or not plan[k].strip() for k in ('name','hypothesis')):
        raise ValueError('Name and hypothesis required')
    if not isinstance(plan['prior_trials'],dict) or plan['prior_trials'].get('status') not in ('unknown','complete'):
        raise ValueError('Explicit known/unknown prior trial history required')
    if plan['inspected_through'] is not None:utc(plan['inspected_through'])
    validate_statistics_options(plan['statistics'])
    if plan['schema'] not in ('chronological-validation-v1','chronological-validation-v2') or plan['capital_policy'] not in ('independent_reset_accounts','continuous_account') or (continuous and not generated):
        raise ValueError('Explicit supported validation and capital/reset policy required')
    if not generated and (dry_run or resolved_plan_path is not None):
        raise ValueError('Preview and frozen resolved plans require chronological-validation-v2; run v1 explicit folds directly')
    if hashlib.sha256(raw_recipe).hexdigest()!=plan['recipe_sha256']:
        raise ValueError('Selected recipe differs from the declared recipe hash')
    windows=[]
    candidates=_candidate_recipes(plan['selection'],raw_recipe) if ranking else []
    if generated:
        from ..strategies.momentum_strategy import load_recipe
        resolved_recipe=load_recipe(recipe)
        if set(dict(resolved_recipe.symbol_budgets))!={r['symbol'] for r in base['rules']}:
            raise ValueError('Recipe symbols must match the run instruments')
        for candidate in candidates:
            if {s for s,_ in candidate['recipe']['symbol_budgets']}!={r['symbol'] for r in base['rules']}:
                raise ValueError('Candidate recipe symbols must match the run instruments')
        warmup=max([c['warmup_bars'] for c in candidates] or [resolved_recipe.warmup_bars])
        folds,windows=_generated_folds(plan,warmup)
    else:folds=plan['folds']
    evaluation_count=(len(folds)*(len(candidates)+1) if ranking else len(folds))+int(continuous)
    if generated and evaluation_count>plan['max_evaluations']:
        raise ValueError('Evaluation budget exceeded before trials')
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
        if fold['designation']=='test' and (utc(fold['selected_at'])>start if generated else utc(fold['selected_at'])>=start):
            raise ValueError('Recipe selection must precede the first test decision')
        if utc(fold['development_start'])>=utc(fold['development_end']) or utc(fold['development_end'])>utc(fold['calibration_end']):
            raise ValueError('Development must end by the calibration cutoff')
        if utc(fold['calibration_end'])>utc(fold['selected_at']):
            raise ValueError('Future calibration cannot follow recipe selection')
        previous_end=end
    if continuous and any(utc(a['test_end'])!=utc(b['test_start']) for a,b in zip(folds,folds[1:])):
        raise ValueError('Continuous account requires contiguous test coverage')
    source=QualifiedSpotSource(snapshot);code_id,code_files=source_snapshot()
    base_spec,_=resolve_run_spec(base,source.data_id,code_id)
    if generated and {e['symbol'] for e in source.manifest['entries']}!={r['symbol'] for r in base['rules']}:
        raise ValueError('Price symbols must match the run instruments')
    if generated and base['interval_seconds']!=3600:
        raise ValueError('Generated momentum validation requires hourly bars')
    if portable and (not generated or data_repository is None):
        raise ValueError('Portable validation requires a v2 plan and a native data repository')
    if type(execution_delay_bars) is not int or not 0<=execution_delay_bars<=168:
        raise ValueError('Execution delay must be an integer from 0 to 168 bars')
    if (liquidity_path is None)!=(liquidity_policy is None):
        raise ValueError('Liquidity snapshot and policy are required together')
    liquidity=None
    if liquidity_path is not None:
        from ..liquidity_inputs import load_liquidity
        liquidity=load_liquidity(liquidity_path,source,liquidity_policy)
        liquidity_policy=asdict(liquidity.policy)
    if generated:
        from algoLibs.backtest import Backtest
        # Reuse the engine's constructor contract before any trial executes.
        Backtest.from_prepared(base_spec,source,targets=(),execution_delay_bars=execution_delay_bars,liquidity=liquidity)
    options=dict(execution_delay_bars=execution_delay_bars,retain_trace=retain_trace,
        fixed_reference_weights=fixed_reference_weights,report_context=report_context,
        liquidity_policy=liquidity_policy,liquidity_data_id=liquidity.data_id if liquidity else None)
    if portable:options['portable']=True
    coverage=[]
    for window in windows:
        view=QualifiedSpotSource(snapshot,repository=data_repository,
            bounds=(min(window['training_history_start'],window['history_start']),window['test_end']))
        count=sum(len(batch) for batch in view)
        coverage.append(dict(id=window['id'],start=view.bounds[0],end=view.bounds[1],bars=count,
                             verified=True,reads=view.read_audit))
    definition=dict(schema='resolved-chronological-validation-v1',plan=plan,folds=folds,windows=windows,
        evaluation_count=evaluation_count,coverage=coverage,data_id=source.data_id,code_id=code_id,options=options,
        source_manifest=source.manifest,resolved_recipe=asdict(resolved_recipe) if generated else None,
        inputs=dict(spec=hashlib.sha256(raw_config).hexdigest(),recipe=hashlib.sha256(raw_recipe).hexdigest(),
                    plan=hashlib.sha256(raw_plan).hexdigest(),auxiliary=hashlib.sha256(raw_auxiliary).hexdigest() if raw_auxiliary is not None else None))
    if continuous:
        definition['continuous_account']=dict(start=utc(folds[0]['test_start']),end=utc(folds[-1]['test_end']),
            feature_state_policy=plan['feature_history_policy'],account_state_policy='carry_cash_holdings_and_cost_basis',
            switch_timing_policy='Decisions switch at test start; uniform delay carries pending decisions across switches; executions at account end are censored',
            fold_accounts='Independent-reset diagnostics only; training never funds the continuous account')
    if ranking:
        definition['candidates']=candidates
        definition['candidate_windows']=[dict(fold=w['id'],candidate=c['id'],
            train_start=w['train_start'],train_end=w['train_end'],test_start=w['test_start'],test_end=w['test_end'],
            training_history_start=w['train_start']-c['warmup_bars']*timedelta(hours=1),
            test_history_start=w['test_start']-c['warmup_bars']*timedelta(hours=1)) for w in windows for c in candidates]
    native_requests=_native_requests(coverage) if portable else []
    if portable:native_requests+=_reference_requests(data_repository,native_requests)
    if portable:definition['native_requests']=[asdict(r) for r in native_requests]
    if resolved_plan_path is not None:
        pinned=Path(resolved_plan_path)
        if digest(pinned)!=pinned.with_suffix('.sha256').read_text().strip() or _json(_read(pinned))!=_json(definition):
            raise ValueError('Frozen resolved plan or inputs changed; preview again explicitly')
    elif generated and not dry_run:
        raise ValueError('Execute generated schedules with a frozen --resolved-plan from --dry-run')
    destination.parent.mkdir(parents=True,exist_ok=True)
    stage=Path(tempfile.mkdtemp(prefix='.validation-',dir=destination.parent))
    try:
        if generated or dry_run or resolved_plan_path is not None:
            _write(stage/'resolved-plan.json',definition)
            (stage/'resolved-plan.sha256').write_text(digest(stage/'resolved-plan.json')+'\n')
        if dry_run:
            stage.rename(destination)
            return definition
        common=stage/'common';common.mkdir()
        for name,content in (('spec.json',raw_config),('recipe.json',raw_recipe),('plan.json',raw_plan)):
            (common/name).write_bytes(content)
        if raw_auxiliary is not None:
            (common/'auxiliary.json').write_bytes(raw_auxiliary)
        if ranking:
            (common/'candidates').mkdir()
            for candidate in candidates:
                (common/'candidates'/(candidate['id']+'.json')).write_text(
                    json.dumps(candidate['recipe'],sort_keys=True,separators=(',',':')))
        if liquidity is not None:
            from ..liquidity_inputs import load_liquidity
            frozen_liquidity=common/'liquidity';frozen_liquidity.mkdir()
            for name in ['manifest.json','manifest.sha256']+[e['file'] for e in liquidity.evidence['entries']]:
                shutil.copyfile(local_file(Path(liquidity_path),name),frozen_liquidity/name)
            if load_liquidity(frozen_liquidity,source,liquidity_policy).data_id!=liquidity.data_id:
                raise ValueError('Liquidity changed during freezing')
        prices=common/'prices';prices.mkdir()
        for name in ['manifest.json','manifest.sha256']+[e['file'] for e in source.manifest['entries']]:
            shutil.copyfile(local_file(source.directory,name),prices/name)
        frozen=QualifiedSpotSource(prices)
        if frozen.data_id!=source.data_id:
            raise ValueError('Price snapshot changed during freezing')
        for _ in frozen:pass
        if portable:
            from algoLibs.market_data.snapshots import SnapshotReader
            native=common/'native-market-data'
            if native_snapshot_path is None:
                prepared=data_repository.prepare(native_requests,native,include_reference_metadata=False)
            else:
                shutil.copytree(native_snapshot_path,native)
                prepared=SnapshotReader(native)
            for window in coverage:
                for _ in QualifiedSpotSource(prices,repository=prepared.repository,bounds=(window['start'],window['end'])):pass
            # A metadata change between preflight and freezing must not leave
            # newly referenced assets absent from the portable snapshot.
            for request in _reference_requests(prepared.repository,[r for r in native_requests if r.dataset=='public.md_bar']):
                for _ in prepared.repository.query(request):pass
        def child(folder, start, end, recipe_path, bounds, context, recipe_schedule=None):
            resolved=dict(base,start=start,end=end)
            (folder/'spec-input.json').write_text(json.dumps(resolved,sort_keys=True,separators=(',',':')))
            run_momentum(prices,folder/'spec-input.json',recipe_path,folder/'run',
                data_repository=data_repository,source_bounds=bounds,
                execution_delay_bars=execution_delay_bars,retain_trace=retain_trace,
                fixed_reference_weights=fixed_reference_weights,
                liquidity_path=common/'liquidity' if liquidity is not None else None,liquidity_policy=liquidity_policy,
                auxiliary_path=common/'auxiliary.json' if raw_auxiliary is not None else None,
                report_context=context,recipe_schedule=recipe_schedule)
            return 'sha256:'+(folder/'run/manifest.sha256').read_text().strip()
        selections={}
        test_schedules=[]
        def execute(fold, folder):
            reasons=eligibility_reasons(fold,plan,[r['symbol'] for r in base['rules']])
            designation='development' if reasons else 'test'
            evidence=dict(fold,requested_designation=fold['designation'],designation=designation,
                          eligible=not reasons,eligibility_reasons=reasons)
            context=dict(report_context or {},validation_fold=evidence,research_record=dict(hypothesis=plan['hypothesis'],
                designation=designation,parameter_changes=[],related_runs=ids))
            window=next((w for w in windows if w['id']==fold['id']),None)
            bounds=(window['history_start'],window['test_end']) if window else None
            recipe_path=common/'recipe.json'
            if ranking:
                training=folder/'training';training.mkdir()
                def train(candidate, trial_folder):
                    return child(trial_folder,window['train_start'].isoformat(),window['train_end'].isoformat(),
                        common/'candidates'/(candidate['id']+'.json'),
                        (window['train_start']-candidate['warmup_bars']*timedelta(hours=1),window['train_end']),
                        dict(context,validation_phase='training',research_record=dict(hypothesis=plan['hypothesis'],
                            designation='development',parameter_changes=[],related_runs=ids)))
                trials=run_related_cases(candidates,training,train,_write)
                policy=plan['selection']
                for trial,candidate in zip(trials,candidates):
                    metrics=case_metrics(training/trial['artifacts'],trial)
                    score=metrics.get(policy['objective'])
                    reason=None
                    if trial['status']!='succeeded':reason='failed_trial'
                    elif score is None or not math.isfinite(float(score)):reason='undefined_metric'
                    elif metrics['complete_day_count']<policy['minimum_complete_training_days']:
                        reason='insufficient_complete_training_days'
                    trial.update(recipe_sha256=candidate['recipe_sha256'],metrics=metrics,score=score,eligible=reason is None,
                        ineligible_reason=reason)
                valid=sorted((t for t in trials if t['eligible']),key=lambda t:t['recipe_sha256'])
                # Stable descending comparison preserves the hash order for exact ties.
                # Arithmetic on Decimal scores would round under the caller's context.
                valid.sort(key=lambda t:t['score'],reverse=True)
                winner=valid[0] if valid and all(t['status']=='succeeded' for t in trials) else None
                selection=dict(schema='walk-forward-selection-v1',status='selected' if winner else 'failed',
                    evidence_kind='historical_walk_forward',training_data_cutoff=window['train_end'],
                    simulated_selection_cutoff=utc(fold['selected_at']),performed_at=datetime.now(timezone.utc),
                    train_start=window['train_start'],test_start=window['test_start'],test_end=window['test_end'],
                    minimum_complete_training_days=policy['minimum_complete_training_days'],
                    objective=policy['objective'],tie_break=policy['tie_break'],candidate_failure_policy=policy['candidate_failure_policy'],
                    selected_candidate=winner['case_id'] if winner else None,
                    selected_recipe_sha256=winner['recipe_sha256'] if winner else None,trials=trials)
                selections[fold['id']]=selection
                if winner:
                    candidate=next(c for c in candidates if c['id']==winner['case_id'])
                    recipe_path=folder/'selected-recipe.json'
                    shutil.copyfile(common/'candidates'/(candidate['id']+'.json'),recipe_path)
                    bounds=(window['test_start']-candidate['warmup_bars']*timedelta(hours=1),window['test_end'])
                    selection.update(training_history_start=window['train_start']-candidate['warmup_bars']*timedelta(hours=1),
                        test_history_start=bounds[0])
                _write(folder/'selection.json',selection)
                if not winner:raise ValueError('Training selection failed; no test run permitted')
                context.update(validation_phase='test',selection=selection)
            if continuous:
                test_schedules.append(dict(fold=fold['id'],start=utc(fold['test_start']),end=utc(fold['test_end']),
                    history_start=bounds[0],recipe_path=recipe_path))
            return child(folder,fold['test_start'],fold['test_end'],recipe_path,bounds,context)
        records=run_related_cases(folds,stage,execute,_write)
        if ranking:
            history=dict(history,status='incomplete_after_training_selection')
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
            plan=dict(plan,folds=folds),known_exposures=KNOWN_EXPOSURES,
            oos_segments=[dict(fold=r['case_id'],path=r['path']) for r in records if r['eligible']])
        if continuous:
            continuous_reasons=[row['case_id']+': '+reason for row in records for reason in row['eligibility_reasons']]
            continuous_evidence=dict(requested_designation=plan['designation'],
                designation='development' if continuous_reasons else 'test',eligible=not continuous_reasons,
                eligibility_reasons=continuous_reasons)
            def deploy(case,folder):
                if any(r['status']!='succeeded' for r in records):
                    raise ValueError('Continuous account requires every selected test fold to succeed')
                return child(folder,folds[0]['test_start'],folds[-1]['test_end'],common/'recipe.json',
                    (min(s['history_start'] for s in test_schedules),utc(folds[-1]['test_end'])),
                    dict(report_context or {},validation_phase='continuous_test',capital_policy='continuous_account',
                        validation_continuous=continuous_evidence,research_record=dict(hypothesis=plan['hypothesis'],
                            designation=continuous_evidence['designation'],parameter_changes=[],related_runs=ids)),
                    recipe_schedule=test_schedules)
            account=run_related_cases([dict(id='continuous-account')],stage,deploy,_write)[0]
            account.update(continuous_evidence)
            account.update(eligible=account['status']=='succeeded' and all(r['eligible'] for r in records),
                metrics=case_metrics(stage/account['artifacts'],account),
                report=account['artifacts']+'/run/report.xlsx' if account['status']=='succeeded' else None,
                switches=[dict(fold=s['fold'],start=s['start'],end=s['end'],history_start=s['history_start'],
                    recipe_sha256=digest(s['recipe_path']),execution_start=s['start']+execution_delay_bars*timedelta(hours=1)) for s in test_schedules],
                feature_state_policy='restart_from_declared_warmup',
                account_state_policy='carry_cash_holdings_and_cost_basis')
            report['continuous']=account
            report['primary'].update(continuous_account_return=account['metrics'].get('net_return') if account['eligible'] else None,
                convention='Reset-fold diagnostics remain segmented; one separately reported continuous account carries real portfolio state')
        if ranking:
            report.update(evidence_kind='historical_walk_forward',selections=selections,
                trial_history=dict(prior=plan['prior_trials'],status=history['status'],training_trial_count=sum(len(s['trials']) for s in selections.values()),
                    effective_independent_trials=None,convention='Candidates and folds are dependent; no automatic independent trial count'))
        _write(stage/'validation.json',report)
        write_workbook(stage/'validation.xlsx',validation_frames(report))
        with zipfile.ZipFile(stage/'source.zip','w',compression=zipfile.ZIP_DEFLATED) as archive:
            for name,content in sorted(code_files.items()):
                info=zipfile.ZipInfo(name,date_time=(1980,1,1,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED
                archive.writestr(info,content)
        if source_snapshot()[0]!=code_id:raise ValueError('Source changed during validation')
        _seal_validation(stage,source.data_id,code_id)
        stage.rename(destination)
    except BaseException:
        shutil.rmtree(stage,ignore_errors=True)
        raise
    return report
