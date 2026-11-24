"""Budgeted grids through the existing public validation and account workflow."""
import hashlib,json,unittest,importlib.util
from pathlib import Path
from decimal import Decimal
from algoLibs.market_data.types import decode
import test_grid_validation as grid_fixture
from test_spot_inputs import T,H


@unittest.skipUnless(importlib.util.find_spec('duckdb'),'Requires algoLibs[duckdb]')
class GridContinuousValidationTests(unittest.TestCase):
    setUp=grid_fixture.GridValidationTests.setUp
    inputs=grid_fixture.GridValidationTests.inputs
    command=grid_fixture.GridValidationTests.command
    study=grid_fixture.GridValidationTests.study

    def test_v3_preview_budgets_all_217_folds_and_the_continuous_account(self):
        self.inputs();self.database.close()
        self.plan.update(capital_policy='continuous_account',mode='rolling',max_evaluations=1086,
            bounds=dict(start=(T+512*H).isoformat(),end=(T+737*H).isoformat()),
            window=dict(train_bars=8,test_bars=1,step_bars=1,gap_bars=0,partial_final_fold='include'))
        self.plan_path.write_text(json.dumps(self.plan))
        output=self.root/'full-preview';self.command(output,'--dry-run','--duckdb',self.path)
        preview=decode(json.loads((output/'resolved-plan.json').read_text()))
        self.assertEqual(len(preview['folds']),217)
        self.assertEqual(preview['evaluation_count'],1086)
        self.assertEqual(preview['windows'][-1]['test_end'],T+737*H)
        self.assertFalse((output/'cases').exists())

    def observed_inputs(self):
        from algoLibs.market_data.types import encode
        self.inputs()
        self.plan.update(capital_policy='continuous_account',mode='rolling',max_evaluations=11)
        self.plan['bounds']['end']=(T+617*H).isoformat()
        self.plan['selection']['search']['parameters']={'vol_target':['.25','.75'],'warmup_bars':[512,520]}
        self.plan_path.write_text(json.dumps(self.plan))
        path=self.prices/'manifest.json';manifest=decode(json.loads(path.read_text()))
        manifest.update(format='qualified-spot-price-bars-v3',availability_policy='observed_hourly_preserve_state_v1',availability_exclusions=[])
        for entry in manifest['entries']:
            symbol=entry['symbol'];file=self.prices/entry['file']
            rows=[decode(json.loads(line)) for line in file.read_text().splitlines()]
            rows=[r for r in rows if r['start']!=T+610*H and not (symbol=='BTCUSDT' and r['start']==T+100*H)
                  and not (symbol=='SOLUSDT' and r['start']<T+40*H)]
            file.write_text(''.join(json.dumps(encode(r))+'\n' for r in rows))
            gaps=([dict(start=T+100*H,end=T+101*H)] if symbol=='BTCUSDT' else [])+[dict(start=T+610*H,end=T+611*H)]
            entry.update(rows=len(rows),sha256=hashlib.sha256(file.read_bytes()).hexdigest(),first_observed=rows[0]['start'],end=T+744*H,gaps=gaps)
        path.write_text(json.dumps(encode(manifest)));(self.prices/'manifest.sha256').write_text(hashlib.sha256(path.read_bytes()).hexdigest())
        with self.database.transaction(readonly=False) as connection:
            connection.execute('DELETE FROM public.md_bar WHERE ts=? OR (instrument_id=1 AND ts=?) OR (instrument_id=3 AND ts<?)',
                               [T+610*H,T+100*H,T+40*H])
        self.database.close()

    def test_observed_grid_matches_explicit_continuous_positions_and_ordinary_ledger(self):
        from algoFarmBT.backtests.execute_momentum_backtest import run_snapshot
        from algoFarmBT.spot_inputs import QualifiedSpotSource
        from spot_ledger_audit import audit_run
        self.observed_inputs()
        grid,first,preview=self.study('grid','--duckdb',self.path,'--trace','--execution-delay-bars',2)
        self.assertEqual(preview['evaluation_count'],11)
        self.assertEqual([f['status'] for f in first['folds']],['succeeded','succeeded'])
        self.assertEqual(first['continuous']['status'],'succeeded',first['continuous']['error'])
        expected_history={512:T+15*H,520:T+7*H}
        for candidate in preview['candidates']:
            window=next(w for w in preview['candidate_windows'] if w['fold']=='fold-001' and w['candidate']==candidate['id'])
            self.assertEqual(window['training_history_start'],expected_history[candidate['recipe']['warmup_bars']])
            trial=next(t for t in first['selections']['fold-001']['trials'] if t['case_id']==candidate['id'])
            access=decode(json.loads((grid/'cases/fold-001/training'/trial['artifacts']/'run/data-access.json').read_text()))
            self.assertTrue(all(r['request']['start']==window['training_history_start'] and r['request']['end']==T+600*H for r in access['reads']))
        identities={(Decimal(c['recipe']['vol_target']),c['recipe']['warmup_bars']):c['id'] for c in preview['candidates']}
        self.plan['selection'].pop('search')
        self.plan['selection']['candidates']=[dict(id=identities[(Decimal(vol),warmup)],recipe_overrides={'vol_target':vol,'warmup_bars':warmup})
            for vol,warmup in [('.25',512),('.25',520),('.75',512),('.75',520)]]
        self.plan_path.write_text(json.dumps(self.plan))
        explicit,second,_=self.study('explicit','--duckdb',self.path,'--trace','--execution-delay-bars',2)
        self.assertEqual(first['continuous']['switches'],second['continuous']['switches'])
        for fold in ('fold-001','fold-002'):
            a,b=first['selections'][fold],second['selections'][fold]
            self.assertEqual(a['selected_candidate'],b['selected_candidate'])
            self.assertEqual([t['score'] for t in a['trials']],[t['score'] for t in b['trials']])
        for run in sorted(grid.glob('cases/**/run')):
            for name in ('result.json','report.json','targets.json','target-timing.jsonl','availability.json','execution-trace.jsonl'):
                self.assertEqual((run/name).read_bytes(),(explicit/run.relative_to(grid)/name).read_bytes(),(run,name))
        actual=grid/'cases/continuous-account/run'
        result=decode(json.loads((actual/'result.json').read_text()))
        self.assertTrue(result['fills'])
        self.assertTrue(any(e['time']==T+609*H and any(q>0 for s,q in e['quantities']) for e in result['equity']))
        timing=[decode(json.loads(line)) for line in (actual/'target-timing.jsonl').read_text().splitlines()]
        self.assertIn(dict(decision_time=T+607*H,execution_time=T+609*H,delay_bars=2,status='scheduled'),timing)
        availability=decode(json.loads((actual/'availability.json').read_text()))['events']
        self.assertTrue(any(e.get('time')==T+610*H and e['status']=='cancelled_missing_open' for e in availability))
        bars={(b.symbol,b.start):b for batch in QualifiedSpotSource(self.prices) for b in batch}
        self.assertTrue(all((f['symbol'],f['time']) in bars for f in result['fills']))
        ordinary=self.root/'ordinary'
        run_snapshot(self.prices,grid/'cases/continuous-account/spec-input.json',actual/'targets.json',ordinary,
            annualisation_hours=8760,source_bounds=(min(s['history_start'] for s in first['continuous']['switches']),T+617*H))
        self.assertEqual((actual/'result.json').read_bytes(),(ordinary/'result.json').read_bytes())
        for run in (actual,ordinary):
            audit=audit_run(run,bars)
            self.assertGreater(audit['fills'],0)
            self.assertIn('cost_basis_after_each_fill',audit['checks'])
        self.assertFalse(first['continuous']['eligible'])
        self.assertEqual(first['trial_history']['status'],'incomplete_after_training_selection')
        self.assertIsNone(first['trial_history']['effective_independent_trials'])

    def test_named_grid_fallback_is_operational_and_cannot_pass_research_eligibility(self):
        from datetime import timedelta
        from test_spot_inputs import make_inputs
        from algoFarmBT.spot_inputs import qualify_price_bars
        self.inputs();shift=timedelta(days=365)
        inputs=self.root/'unseen-inputs';inputs.mkdir();make_inputs(inputs,trend=True,start=T-shift)
        self.prices=self.root/'unseen-prices';qualify_price_bars(inputs,self.prices)
        with self.database.transaction(readonly=False) as connection:
            connection.execute("UPDATE public.md_bar SET ts=ts-INTERVAL '365 days'")
        self.database.close()
        spec=json.loads(self.spec.read_text())
        for key in ('start','end'):spec[key]=(T+(601 if key=='start' else 617)*H-shift).isoformat()
        self.spec.write_text(json.dumps(spec))
        self.plan.update(capital_policy='continuous_account',mode='rolling',designation='test',max_evaluations=7,
            inspected_through=(T-shift).isoformat(),bounds=dict(start=(T+528*H-shift).isoformat(),end=(T+617*H-shift).isoformat()))
        self.plan['selection'].update(minimum_complete_training_days=300)
        self.plan['selection']['search']['parameters']={'vol_target':['.25','.75']}
        self.plan_path.write_text(json.dumps(self.plan))
        initial=self.root/'initial';self.command(initial,'--dry-run','--duckdb',self.path)
        preview=decode(json.loads((initial/'resolved-plan.json').read_text()))
        fallback=next(c['id'] for c in preview['candidates'] if Decimal(c['recipe']['vol_target'])==Decimal('.25'))
        self.plan['selection']['no_eligible_score_fallback']=fallback;self.plan_path.write_text(json.dumps(self.plan))
        output,report,_=self.study('fallback','--duckdb',self.path)
        self.assertEqual(report['continuous']['status'],'succeeded')
        for fold in report['folds']:
            chosen=report['selections'][fold['case_id']]
            self.assertTrue(chosen['fallback_used'])
            self.assertEqual(chosen['selected_candidate'],fallback)
            self.assertTrue(all(not t['eligible'] and t['ineligible_reason']=='insufficient_complete_training_days' for t in chosen['trials']))
            self.assertFalse(fold['eligible'])
            self.assertIn('operational_fallback',fold['eligibility_reasons'])
            research=decode(json.loads((output/fold['artifacts']/'run/research.json').read_text()))
            self.assertEqual(research['run_details']['context']['research_record']['designation'],'development')
        self.assertFalse(report['continuous']['eligible'])
        self.assertIsNone(report['primary']['continuous_account_return'])

    def test_27_candidates_preview_6077_evaluations_without_truncating_folds(self):
        self.inputs();self.database.close()
        self.plan.update(capital_policy='continuous_account',mode='rolling',max_evaluations=6077,
            bounds=dict(start=(T+512*H).isoformat(),end=(T+737*H).isoformat()),
            window=dict(train_bars=8,test_bars=1,step_bars=1,gap_bars=0,partial_final_fold='include'))
        self.plan['selection']['search']['parameters']={'signal_span':[6,9,12],'smoothing_span':[3,6,9],'vol_target':['.25','.50','.75']}
        self.plan_path.write_text(json.dumps(self.plan))
        output=self.root/'27-preview';self.command(output,'--dry-run','--duckdb',self.path)
        preview=decode(json.loads((output/'resolved-plan.json').read_text()))
        self.assertEqual(len(preview['candidates']),27)
        self.assertEqual(len(preview['folds']),217)
        self.assertEqual(len(preview['candidate_windows']),5859)
        self.assertEqual(preview['evaluation_count'],6077)
        self.assertEqual(preview['windows'][-1]['test_end'],T+737*H)

    def test_budget_and_structural_overflow_fail_before_market_reads(self):
        self.inputs();original=json.loads(json.dumps(self.plan));self.prices=self.root/'missing-prices'
        cases=[
            ('ceiling',dict(max_evaluations=8450),'budget must be 1 to 8449'),
            ('too-small',dict(max_evaluations=4),'budget exceeded before trials'),
            ('217-too-small',dict(capital_policy='continuous_account',mode='rolling',max_evaluations=1085,
                bounds=dict(start=(T+512*H).isoformat(),end=(T+737*H).isoformat()),
                window=dict(train_bars=8,test_bars=1,step_bars=1,gap_bars=0,partial_final_fold='include')),'budget exceeded before trials'),
            ('fold-overflow',dict(mode='rolling',max_evaluations=8449,
                bounds=dict(start=(T+512*H).isoformat(),end=(T+800*H).isoformat()),
                window=dict(train_bars=8,test_bars=1,step_bars=1,gap_bars=0,partial_final_fold='include')),'budget exceeded before trials')]
        for name,change,error in cases:
            with self.subTest(name=name):
                self.plan=json.loads(json.dumps(original));self.plan.update(change);self.plan_path.write_text(json.dumps(self.plan))
                with self.assertRaisesRegex(ValueError,error):self.command(self.root/name,'--dry-run')
                self.assertFalse((self.root/name).exists())
        self.plan=json.loads(json.dumps(original));self.plan.update(schema='chronological-validation-v2',max_evaluations=1057)
        self.plan['selection'].pop('search')
        self.plan['selection']['candidates']=[dict(id='one',recipe_overrides={})]
        self.plan_path.write_text(json.dumps(self.plan))
        with self.assertRaisesRegex(ValueError,'budget must be 1 to 1056'):self.command(self.root/'v2','--dry-run')

    def test_v3_ceiling_is_a_budget_not_a_requirement_to_run_extra_accounts(self):
        self.inputs();self.database.close();self.plan['max_evaluations']=8449
        self.plan_path.write_text(json.dumps(self.plan))
        output=self.root/'ceiling';self.command(output,'--dry-run','--duckdb',self.path)
        preview=decode(json.loads((output/'resolved-plan.json').read_text()))
        self.assertEqual(preview['evaluation_count'],5)
        self.assertEqual(len(preview['folds']),1)
        self.assertFalse((output/'cases').exists())

    def test_failed_grid_siblings_and_ineligible_scores_gate_testing_and_continuous_account(self):
        self.inputs();self.database.close()
        raw=json.loads(self.recipe.read_text());raw.update(regime_mode='supplied_multiplier',
            auxiliary_rules=[dict(field='regime',source_id='provided',max_age_seconds=10000000)])
        self.recipe.write_text(json.dumps(raw));self.plan['recipe_sha256']=hashlib.sha256(self.recipe.read_bytes()).hexdigest()
        self.plan.update(capital_policy='continuous_account',mode='rolling',max_evaluations=7)
        self.plan['bounds']['end']=(T+617*H).isoformat()
        self.plan['selection']['search']['parameters']={'auxiliary_rules':[
            [dict(field='regime',source_id='provided',max_age_seconds=10000000)],
            [dict(field='regime',source_id='missing',max_age_seconds=10000000)]]}
        self.plan_path.write_text(json.dumps(self.plan))
        auxiliary=self.root/'auxiliary.json';auxiliary.write_text(json.dumps([dict(symbol=s,field='regime',observed_at=T.isoformat(),
            available_at=T.isoformat(),value='.5',units='dimensionless',source_id='provided') for s in ('BTCUSDT','ETHUSDT','SOLUSDT')]))
        output,report,_=self.study('failed-sibling','--duckdb',self.path,'--auxiliary',auxiliary)
        for fold in report['folds']:
            selection=report['selections'][fold['case_id']]
            self.assertEqual([t['status'] for t in selection['trials']],['succeeded','failed'])
            good,bad=selection['trials']
            folder=output/fold['artifacts']/'training'
            self.assertTrue((folder/good['artifacts']/'run/report.json').exists())
            self.assertTrue((folder/bad['artifacts']/'failure.json').exists())
            self.assertIsNone(selection['selected_candidate'])
            self.assertFalse((output/fold['artifacts']/'run').exists())
        self.assertEqual(report['continuous']['status'],'failed')
        self.assertFalse((output/'cases/continuous-account/run').exists())
        # Successful but unscorable candidates also cannot manufacture a selected test.
        for label,objective,minimum,axes in [
            ('insufficient','net_return',300,{'vol_target':['.25','.75']}),
            ('undefined','annualised_daily_sharpe',2,{'vol_target':['.000000000001','.000000000002']})]:
            self.inputs();self.plan.update(capital_policy='continuous_account',mode='rolling',max_evaluations=7)
            self.plan['bounds']['end']=(T+617*H).isoformat()
            self.plan['selection'].update(objective=objective,minimum_complete_training_days=minimum)
            self.plan['selection']['search']['parameters']=axes;self.plan_path.write_text(json.dumps(self.plan))
            _,gated,_=self.study(label,'--duckdb',self.path)
            self.assertEqual(gated['continuous']['status'],'failed')
            for selection in gated['selections'].values():
                self.assertTrue(all(t['status']=='succeeded' and not t['eligible'] for t in selection['trials']))
                self.assertIsNone(selection['selected_candidate'])
                self.assertFalse(selection['fallback_used'])
                if label=='undefined':self.assertTrue(all(t['score'] is None for t in selection['trials']))

    def test_future_gap_and_test_prices_cannot_change_grid_training_scores(self):
        import csv,io,zipfile
        from algoLibs.market_data import DuckDBDatabase
        from algoFarmBT.spot_inputs import qualify_price_bars
        from test_spot_inputs import write_native
        self.inputs();self.database.close()
        self.plan.update(capital_policy='continuous_account',mode='expanding',max_evaluations=7)
        self.plan['bounds']['end']=(T+617*H).isoformat()
        self.plan['selection']['search']['parameters']={'vol_target':['.25','.75']}
        self.plan_path.write_text(json.dumps(self.plan))
        first,baseline,_=self.study('before','--duckdb',self.path)
        comparison=json.loads((self.root/'qualification.json').read_text())
        rows=[decode(json.loads(line)) for line in (self.root/'native.jsonl').read_text().splitlines()]
        for envelope in rows:
            if envelope['native']['ts']>=T+600*H:
                for field in ('open','high','low','close'):envelope['native'][field]+=1000
        for source in comparison['comparisons']:
            filename=source['symbol']+'-1h-2026-05.zip';stream=io.StringIO();writer=csv.writer(stream)
            for envelope in rows:
                if envelope['symbol']!=source['symbol']:continue
                row=envelope['native'];micros=int(row['ts'].timestamp())*1000000
                writer.writerow([micros,row['open'],row['high'],row['low'],row['close'],row['volume'],micros+3600000000-1,
                    row['quote_volume'],row['trade_count'],row['taker_buy_base_volume'],row['taker_buy_quote_volume'],0])
            with zipfile.ZipFile(self.root/filename,'w') as archive:archive.writestr(filename[:-4]+'.csv',stream.getvalue())
            source['sha256']=hashlib.sha256((self.root/filename).read_bytes()).hexdigest()
            (self.root/(filename+'.CHECKSUM')).write_text(source['sha256']+'  '+filename)
        write_native(self.root,comparison,rows);self.prices=self.root/'changed-prices';qualify_price_bars(self.root,self.prices)
        database=DuckDBDatabase(self.path,read_only=False)
        try:
            with database.transaction(readonly=False) as connection:
                connection.executemany('UPDATE public.md_bar SET open=?,high=?,low=?,close=? WHERE ts=? AND instrument_id=?',
                    [[e['native'][k] for k in ('open','high','low','close','ts','instrument_id')] for e in rows if e['native']['ts']>=T+600*H])
        finally:database.close()
        second,changed,_=self.study('after','--duckdb',self.path)
        a,b=baseline['selections']['fold-001'],changed['selections']['fold-001']
        self.assertEqual([t['score'] for t in a['trials']],[t['score'] for t in b['trials']])
        self.assertEqual(a['selected_recipe_sha256'],b['selected_recipe_sha256'])
        for trial in a['trials']:
            path=Path('cases/fold-001/training')/trial['artifacts']/'run/targets.json'
            self.assertEqual((first/path).read_bytes(),(second/path).read_bytes())
        self.assertNotEqual((first/'cases/fold-001/run/result.json').read_bytes(),(second/'cases/fold-001/run/result.json').read_bytes())

    def test_grid_fallback_must_be_a_frozen_previewed_member(self):
        self.inputs();self.database.close()
        preview=self.root/'preview';self.command(preview,'--dry-run','--duckdb',self.path)
        frozen=decode(json.loads((preview/'resolved-plan.json').read_text()))
        self.plan['selection']['no_eligible_score_fallback']='not-previewed'
        self.plan_path.write_text(json.dumps(self.plan))
        with self.assertRaisesRegex(ValueError,'Fallback must name a predeclared candidate'):
            self.command(self.root/'invalid','--dry-run','--duckdb',self.path)
        self.plan['selection']['no_eligible_score_fallback']=frozen['candidates'][0]['id']
        self.plan_path.write_text(json.dumps(self.plan))
        with self.assertRaisesRegex(ValueError,'Frozen resolved plan or inputs changed'):
            self.command(self.root/'changed','--resolved-plan',preview/'resolved-plan.json','--duckdb',self.path)
        self.assertFalse((self.root/'changed').exists())
