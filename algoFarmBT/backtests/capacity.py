"""Declared capital-size reruns through the existing momentum and case workflow."""
from dataclasses import asdict
import json
from pathlib import Path
import re
import shutil
import tempfile
import zipfile

from algoLibs.backtest.types import number, PROFILE
from algoLibs.backtest.liquidity import LiquidityPolicy, LIQUIDITY_PROFILE
from algoLibs.backtest.excel_report import write_workbook
from algoLibs.backtest.report_frames import capacity_frames
from algoLibs.market_data.types import decode
from ..spot_inputs import QualifiedSpotSource, local_file, digest
from ..liquidity_inputs import load_liquidity
from .execute_momentum_backtest import run_momentum, resolve_run_spec, source_snapshot, _json
from .related_runs import run_related_cases, case_metrics


def load_plan(path):
    plan=json.loads(path if isinstance(path,bytes) else Path(path).read_bytes())
    if set(plan)!={'schema','name','hypothesis','policy','cases'} or plan['schema']!='momentum-capacity-v1':
        raise ValueError('Explicit supported capacity plan required')
    if any(not isinstance(plan[k],str) or not plan[k].strip() for k in ('name','hypothesis')):
        raise ValueError('Declare name and hypothesis before evaluation')
    LiquidityPolicy(**plan['policy'])
    cases=plan['cases']
    if not isinstance(cases,list) or not 2<=len(cases)<=32:
        raise ValueError('Declare 2 to 32 capital cases, baseline first')
    ids=[]
    for case in cases:
        if set(case)!={'id','initial_cash','constrained'} or not isinstance(case['id'],str) or not re.fullmatch('[a-z][a-z0-9-]{0,47}',case['id']):
            raise ValueError('Explicit capital, boolean constrained and safe case ID required')
        if number(case['initial_cash'])<=0 or type(case['constrained']) is not bool:
            raise ValueError('Positive capital and boolean constrained required')
        ids.append(case['id'])
    if len(set(ids))!=len(ids) or ids[0]!='baseline' or cases[0]['constrained'] or not any(c['constrained'] for c in cases):
        raise ValueError('Unique IDs, unconstrained baseline first and a constrained case required')
    return plan


def _write(path,value):Path(path).write_text(_json(value))
def _read(path):return decode(json.loads(Path(path).read_text()))


def _freeze(source,destination,entries):
    destination.mkdir()
    for name in ['manifest.json','manifest.sha256']+[e['file'] for e in entries]:
        shutil.copyfile(local_file(Path(source),name),destination/name)


def run_capacity(snapshot,liquidity_snapshot,config,recipe,plan_path,destination,*,auxiliary_path=None):
    destination=Path(destination)
    if destination.exists():raise FileExistsError(destination)
    config_raw,recipe_raw,plan_raw=(Path(p).read_bytes() for p in (config,recipe,plan_path))
    plan=load_plan(plan_raw);base=json.loads(config_raw)
    code_id,code_files=source_snapshot();source=QualifiedSpotSource(snapshot)
    spec,seconds=resolve_run_spec(base,source.data_id,code_id)
    if spec.execution_profile!=PROFILE or seconds!=3600 or number(plan['cases'][0]['initial_cash'])!=spec.initial_cash:
        raise ValueError('Capacity requires the unchanged hourly unconstrained baseline first')
    liquidity=load_liquidity(liquidity_snapshot,source,plan['policy'])
    auxiliary_raw=Path(auxiliary_path).read_bytes() if auxiliary_path else None
    destination.parent.mkdir(parents=True,exist_ok=True)
    stage=Path(tempfile.mkdtemp(prefix='.capacity-',dir=destination.parent))
    try:
        common=stage/'common';common.mkdir()
        for name,raw in [('spec.json',config_raw),('recipe.json',recipe_raw),('plan.json',plan_raw)]:
            (common/name).write_bytes(raw)
        if auxiliary_raw is not None:(common/'auxiliary.json').write_bytes(auxiliary_raw)
        prices,volumes=common/'prices',common/'liquidity'
        _freeze(source.directory,prices,source.manifest['entries'])
        _freeze(liquidity_snapshot,volumes,liquidity.evidence['entries'])
        frozen=QualifiedSpotSource(prices)
        if frozen.data_id!=source.data_id:raise ValueError('Prices changed while freezing capacity inputs')
        for _ in frozen:pass
        if load_liquidity(volumes,frozen,plan['policy']).data_id!=liquidity.data_id:
            raise ValueError('Liquidity changed while freezing capacity inputs')
        def execute(case,folder):
            resolved=dict(base,initial_cash=case['initial_cash'],
                execution_profile=LIQUIDITY_PROFILE if case['constrained'] else PROFILE)
            (folder/'spec-input.json').write_text(json.dumps(resolved,sort_keys=True,separators=(',',':')))
            run_momentum(prices,folder/'spec-input.json',common/'recipe.json',folder/'run',
                common/'auxiliary.json' if auxiliary_raw is not None else None,
                liquidity_path=volumes if case['constrained'] else None,
                liquidity_policy=plan['policy'] if case['constrained'] else None,
                report_context=dict(capacity_case=case,capacity_status='scenario_only_not_certified',
                    research_record=dict(hypothesis=plan['hypothesis'],designation='development',parameter_changes=case,
                        related_runs=[c['id'] for c in plan['cases']])))
            return 'sha256:'+(folder/'run/manifest.sha256').read_text().strip()
        records=run_related_cases(plan['cases'],stage,execute,_write)
        for row,case in zip(records,plan['cases']):
            folder=stage/row['artifacts'];row['parameters']=case;row['metrics']=case_metrics(folder,row)
            row['report']=row['artifacts']+'/run/report.xlsx' if row['status']=='succeeded' else None
            if row['status']=='succeeded':
                research=_read(folder/'run/research.json');section=research['sections'].get('liquidity')
                row['liquidity_status']=section['status'] if section else 'unconstrained_reference'
                row['metrics'].update(section['summary'] if section else {})
        report=dict(schema='capital-capacity-comparison-v1',capacity_status='scenario_only_not_certified',
            name=plan['name'],hypothesis=plan['hypothesis'],designation='development',data_id=source.data_id,
            liquidity_data_id=liquidity.data_id,code_id=code_id,policy=asdict(liquidity.policy),cases=records,
            evidence=liquidity.evidence,certified_capacity=None,
            common_inputs=dict(prices='common/prices',liquidity='common/liquidity',spec='common/spec.json',
                recipe='common/recipe.json',plan='common/plan.json',auxiliary='common/auxiliary.json' if auxiliary_raw is not None else None),
            sample=dict(start=spec.start,end=spec.end,interval_seconds=seconds,quote_currency=spec.quote_currency),
            conventions=dict(reruns='Actual ledger reruns at declared capital; no scaled baseline P&L or optimiser',
                execution='Lagged available bar base-volume cap; cancelled remainder; same profile for benchmark accounts',
                impact='Assumed coefficient times sqrt(participation ceiling), fixed adverse quote for every order',
                evidence='Volume and count observed/checked; availability, impact, fee tier and precision assumed; spread/depth unavailable',
                capacity='Scenario results only. Missing calibration and capture evidence prevent certification; no maximum capital inferred',
                participation='Execution-known lagged reference distinct from reporting-only realised current bar volume',
                units='USDT shortfall notional, never aggregate incomparable base quantities; impact already included in fill prices'))
        _write(stage/'comparison.json',report);write_workbook(stage/'comparison.xlsx',capacity_frames(report))
        with zipfile.ZipFile(stage/'source.zip','w',compression=zipfile.ZIP_DEFLATED) as archive:
            for name,content in sorted(code_files.items()):
                info=zipfile.ZipInfo(name,date_time=(1980,1,1,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED
                archive.writestr(info,content)
        if source_snapshot()[0]!=code_id:raise ValueError('Source changed during capacity reruns')
        files={p.relative_to(stage).as_posix():digest(p) for p in sorted(stage.rglob('*')) if p.is_file()}
        _write(stage/'manifest.json',dict(format='capacity-runs-v1',data_id=source.data_id,
            liquidity_data_id=liquidity.data_id,code_id=code_id,case_ids=[r['case_id'] for r in records],files=files))
        (stage/'manifest.sha256').write_text(digest(stage/'manifest.json')+'\n');stage.rename(destination)
    except BaseException:
        shutil.rmtree(stage,ignore_errors=True);raise
    return report
