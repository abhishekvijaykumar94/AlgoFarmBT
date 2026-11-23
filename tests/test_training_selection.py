"""Parameter selection through the existing validation command and native visitor."""
import contextlib
from decimal import localcontext
from datetime import datetime, timezone
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import unittest

from algoLibs.market_data.types import decode
from algoLibs.market_data.types import utc
from algoFarmBT.backtests.execute_momentum_backtest import run_momentum
import test_walk_forward as walk_fixture
from test_spot_inputs import T, H


@unittest.skipUnless(importlib.util.find_spec('duckdb'),'Requires algoLibs[duckdb]')
class TrainingSelectionTests(unittest.TestCase):
    setUp=walk_fixture.WalkForwardTests.setUp
    command=walk_fixture.WalkForwardTests.command

    def inputs(self):
        walk_fixture.WalkForwardTests.inputs(self,end=609)
        spec=json.loads(self.spec.read_text());spec.update(start=(T+601*H).isoformat(),end=(T+609*H).isoformat())
        self.spec.write_text(json.dumps(spec))
        self.plan.update(selection_rule='training_rank',bounds=dict(start=(T+528*H).isoformat(),end=(T+609*H).isoformat()),
            window=dict(train_bars=72,test_bars=8,step_bars=8,gap_bars=1,partial_final_fold='drop'),
            selection=dict(rule='training_rank',objective='net_return',minimum_complete_training_days=2,
                tie_break='resolved_recipe_sha256_ascending',candidate_failure_policy='fail_fold',
                candidates=[dict(id='low',recipe_overrides={'vol_target':'.25'}),dict(id='high',recipe_overrides={'vol_target':'.75'})]))
        self.plan_path.write_text(json.dumps(self.plan))

    def study(self,name='study',*options):
        preview=self.root/(name+'-preview');self.command(preview,*options,'--dry-run')
        frozen=preview/'resolved-plan.json';output=self.root/name
        self.command(output,*options,'--resolved-plan',frozen)
        return output,decode(json.loads((output/'validation.json').read_text())),decode(json.loads(frozen.read_text()))

    def test_validation_parquet_cli_queries_visitor_and_replays_the_saved_native_rows(self):
        self.inputs()
        exported=self.root/'bars.parquet'
        with self.database.transaction(readonly=False) as connection:
            connection.execute("COPY public.md_bar TO ? (FORMAT PARQUET)",[str(exported)])
        mapping=self.root/'parquet.json';mapping.write_text(json.dumps({'public.md_bar':[str(exported)]}))
        self.database.close()
        output,report,_=self.study('parquet','--parquet',mapping,'--portable')
        self.assertEqual(report['folds'][0]['status'],'succeeded')
        audit=decode(json.loads((output/'cases/fold-001/run/data-access.json').read_text()))
        self.assertTrue(audit['reads'])
        self.assertTrue(all(r['complete'] for r in audit['reads']))
        exported.unlink()
        from algoFarmBT.backtests.validation import replay_validation
        replay=replay_validation(output,self.root/'offline')
        self.assertEqual(replay['folds'][0]['status'],'succeeded')

    def test_predeclared_fallback_only_when_successful_trials_have_no_eligible_scores(self):
        self.inputs();self.plan['selection'].update(minimum_complete_training_days=300,no_eligible_score_fallback='low')
        self.plan_path.write_text(json.dumps(self.plan))
        output,report,_=self.study('fallback')
        selected=report['selections']['fold-001']
        self.assertTrue(selected['fallback_used'])
        self.assertEqual(selected['selected_candidate'],'low')
        self.assertTrue(all(t['status']=='succeeded' and not t['eligible'] for t in selected['trials']))
        self.assertEqual(report['folds'][0]['status'],'succeeded')

    def test_duckdb_training_selects_and_freezes_recipe_before_an_equivalent_ordinary_test(self):
        self.inputs();self.database.close()
        before=datetime.now(timezone.utc)
        output,report,preview=self.study('study','--duckdb',self.path)
        self.assertEqual(preview['evaluation_count'],3)
        self.assertEqual(len(preview['candidates']),2)
        fold=report['folds'][0];self.assertEqual(fold['status'],'succeeded',fold['error'])
        folder=output/fold['artifacts'];selection=decode(json.loads((folder/'selection.json').read_text()))
        self.assertEqual(selection['selected_candidate'],'high')
        self.assertGreaterEqual(selection['performed_at'],before)
        self.assertEqual(selection['training_data_cutoff'],T+600*H)
        self.assertEqual(selection['evidence_kind'],'historical_walk_forward')
        selected=folder/'selected-recipe.json'
        self.assertEqual(hashlib.sha256(selected.read_bytes()).hexdigest(),selection['selected_recipe_sha256'])
        for trial in selection['trials']:
            run=folder/'training'/trial['artifacts']/'run'
            canonical=decode(json.loads((run/'report.json').read_text()))
            self.assertEqual(trial['score'],canonical['net_return'])
            audit=decode(json.loads((run/'data-access.json').read_text()))
            self.assertTrue(all(r['request']['end']==T+600*H and r['request']['start']==T+16*H for r in audit['reads']))
        direct=self.root/'direct'
        run_momentum(self.prices,self.spec,selected,direct,source_bounds=(T+89*H,T+609*H))
        for name in ('result.json','targets.json'):
            self.assertEqual((folder/'run'/name).read_bytes(),(direct/name).read_bytes(),name)
        self.assertEqual(report['evidence_kind'],'historical_walk_forward')
        self.assertEqual(report['trial_history']['training_trial_count'],2)

    def test_daily_sharpe_and_workbook_use_the_retained_canonical_training_evidence(self):
        from openpyxl import load_workbook
        self.inputs();self.plan['selection']['objective']='annualised_daily_sharpe'
        self.plan_path.write_text(json.dumps(self.plan))
        output,report,preview=self.study()
        selection=report['selections']['fold-001'];self.assertEqual(selection['status'],'selected')
        for trial in selection['trials']:
            self.assertEqual(trial['score'],trial['metrics']['annualised_daily_sharpe'])
            self.assertEqual(trial['metrics']['complete_day_count'],3)
        self.assertEqual(preview['candidates'][0]['recipe']['rv_slow_span'],336)
        book=load_workbook(output/'validation.xlsx')
        self.addCleanup(book.close)
        training=list(book['Training'].values);headers=list(training[0])
        self.assertEqual(len(training),3)
        for excel,trial in zip(training[1:],selection['trials']):
            self.assertEqual(excel[headers.index('candidate')],trial['case_id'])
            self.assertAlmostEqual(excel[headers.index('score')],float(trial['score']))
        chosen=list(book['Selection'].values);self.assertEqual(len(chosen),2)
        self.assertIn(selection['selected_recipe_sha256'],chosen[1])
        self.assertIn('performed_at',chosen[0]);self.assertIn('training_data_cutoff',chosen[0])

    def test_preview_counts_training_and_testing_and_enforces_the_total_budget(self):
        self.inputs();self.plan.update(mode='rolling',max_evaluations=64)
        self.plan['bounds']['end']=(T+625*H).isoformat()
        self.plan_path.write_text(json.dumps(self.plan))
        output=self.root/'preview';self.command(output,'--dry-run')
        receipt=decode(json.loads((output/'resolved-plan.json').read_text()))
        self.assertEqual(receipt['evaluation_count'],9)
        self.assertEqual(len(receipt['folds']),3)
        self.plan['max_evaluations']=8;self.plan_path.write_text(json.dumps(self.plan))
        with self.assertRaisesRegex(ValueError,'budget'):
            self.command(self.root/'over-budget','--dry-run')
        self.assertFalse((self.root/'over-budget').exists())

    def test_candidate_comparisons_keep_allocation_and_metric_units_fixed(self):
        self.inputs()
        for field,value in [('weight_caps',{'BTCUSDT':'.1','ETHUSDT':'.1','SOLUSDT':'.1'}),
                            ('annualisation_hours',24),('rebalance',False)]:
            with self.subTest(field=field):
                self.plan['selection']['candidates'][0]['recipe_overrides']={field:value}
                self.plan_path.write_text(json.dumps(self.plan))
                with self.assertRaisesRegex(ValueError,'comparison assumptions'):
                    self.command(self.root/field,'--dry-run')
                self.assertFalse((self.root/field).exists())

    def test_new_correlated_candidate_trials_do_not_reuse_a_previously_complete_dsr_count(self):
        self.inputs()
        self.plan['prior_trials']=dict(status='complete',complete_through=(T+600*H).isoformat(),
            dependence_method='independent_trials_declared',dependence_evidence='Prior fixture trials only',effective_trials=2,
            records=[dict(id=str(i),daily_sharpe=str(i/100),evaluated_at=(T+500*H).isoformat()) for i in range(2)])
        self.plan_path.write_text(json.dumps(self.plan))
        _,report,_=self.study()
        evidence=report['exploratory']['statistics']['sharpe_evidence']
        self.assertEqual(evidence['trial_history_status'],'incomplete_after_training_selection')
        self.assertIsNone(evidence['dsr'])
        self.assertEqual(report['trial_history']['prior'],self.plan['prior_trials'])
        self.assertIsNone(report['trial_history']['effective_independent_trials'])

    def test_changed_gap_and_test_prices_cannot_change_training_scores_or_selection(self):
        import csv
        import zipfile
        from algoFarmBT.spot_inputs import qualify_price_bars
        from algoLibs.market_data import DuckDBDatabase
        from test_spot_inputs import write_native
        self.inputs();self.database.close()
        first,baseline,_=self.study('baseline','--duckdb',self.path)
        comparison=json.loads((self.root/'qualification.json').read_text())
        rows=[decode(json.loads(line)) for line in (self.root/'native.jsonl').read_text().splitlines()]
        for envelope in rows:
            row=envelope['native']
            if row['ts']>=T+600*H:
                increment=500+int((row['ts']-T)/H)
                for field in ('open','high','low','close'):row[field]+=increment
        for source in comparison['comparisons']:
            symbol=source['symbol'];filename=f'{symbol}-1h-2026-05.zip';stream=io.StringIO();writer=csv.writer(stream)
            for envelope in rows:
                if envelope['symbol']!=symbol:continue
                row=envelope['native'];micros=int(row['ts'].timestamp())*1000000
                writer.writerow([micros,row['open'],row['high'],row['low'],row['close'],row['volume'],
                    micros+3600000000-1,row['quote_volume'],row['trade_count'],row['taker_buy_base_volume'],row['taker_buy_quote_volume'],0])
            with zipfile.ZipFile(self.root/filename,'w') as archive:
                archive.writestr(filename[:-4]+'.csv',stream.getvalue())
            source['sha256']=hashlib.sha256((self.root/filename).read_bytes()).hexdigest()
            (self.root/(filename+'.CHECKSUM')).write_text(source['sha256']+'  '+filename)
        write_native(self.root,comparison,rows)
        self.prices=self.root/'changed-prices';qualify_price_bars(self.root,self.prices)
        db=DuckDBDatabase(self.path,read_only=False)
        try:
            with db.transaction(readonly=False) as conn:
                conn.executemany('UPDATE public.md_bar SET open=?,high=?,low=?,close=? WHERE ts=? AND instrument_id=?',
                    [[e['native'][k] for k in ('open','high','low','close','ts','instrument_id')]
                     for e in rows if e['native']['ts']>=T+600*H])
        finally:db.close()
        second,changed,_=self.study('changed','--duckdb',self.path)
        a=baseline['selections']['fold-001'];b=changed['selections']['fold-001']
        self.assertEqual(a['selected_recipe_sha256'],b['selected_recipe_sha256'])
        self.assertEqual([r['score'] for r in a['trials']],[r['score'] for r in b['trials']])
        for candidate in ('low','high'):
            name=f'cases/fold-001/training/cases/{candidate}/run/targets.json'
            self.assertEqual((first/name).read_bytes(),(second/name).read_bytes())
        self.assertNotEqual((first/'cases/fold-001/run/result.json').read_bytes(),(second/'cases/fold-001/run/result.json').read_bytes())

    def test_undefined_metrics_fail_the_fold_with_all_trials_retained_and_no_test(self):
        self.inputs();self.plan['selection']['objective']='annualised_daily_sharpe'
        for i,candidate in enumerate(self.plan['selection']['candidates'],1):
            candidate['recipe_overrides']['vol_target']=str(i/10**12)
        self.plan_path.write_text(json.dumps(self.plan))
        output,report,_=self.study()
        self.assertEqual(report['folds'][0]['status'],'failed')
        selection=report['selections']['fold-001']
        self.assertEqual(len(selection['trials']),2)
        self.assertTrue(all(t['status']=='succeeded' and t['score'] is None and
                            t['ineligible_reason']=='undefined_metric' for t in selection['trials']))
        self.assertIsNone(selection['selected_candidate'])
        self.assertFalse((output/'cases/fold-001/run').exists())
        self.assertFalse((output/'cases/fold-001/selected-recipe.json').exists())
        self.assertTrue((output/'cases/fold-001/failure.json').exists())

    def test_candidate_warmup_ranges_are_frozen_and_the_selected_test_uses_its_own_history(self):
        self.inputs();self.database.close()
        self.plan['selection']['candidates'][1]['recipe_overrides']['warmup_bars']=520
        self.plan_path.write_text(json.dumps(self.plan))
        output,report,preview=self.study('mixed-warmup','--duckdb',self.path)
        self.assertEqual(preview['windows'][0]['warmup_bars'],520)
        ranges=preview['candidate_windows']
        self.assertEqual([w['training_history_start'] for w in ranges],[T+16*H,T+8*H])
        selection=report['selections']['fold-001'];self.assertEqual(selection['selected_candidate'],'high')
        self.assertEqual(selection['test_history_start'],T+81*H)
        for candidate,start in [('low',T+16*H),('high',T+8*H)]:
            path=output/f'cases/fold-001/training/cases/{candidate}/run/data-access.json'
            audit=decode(json.loads(path.read_text()))
            self.assertTrue(all(r['request']['start']==start and r['request']['end']==T+600*H for r in audit['reads']))
        folder=output/'cases/fold-001'
        audit=decode(json.loads((folder/'run/data-access.json').read_text()))
        self.assertTrue(all(r['request']['start']==T+81*H for r in audit['reads']))
        direct=self.root/'direct'
        run_momentum(self.prices,self.spec,folder/'selected-recipe.json',direct,source_bounds=(T+81*H,T+609*H))
        self.assertEqual((folder/'run/result.json').read_bytes(),(direct/'result.json').read_bytes())

    def test_a_failed_candidate_fails_the_fold_without_hiding_successful_trials(self):
        self.inputs()
        self.plan['selection']['candidates'][1]['recipe_overrides'].update(regime_mode='supplied_multiplier',
            auxiliary_rules=[dict(field='regime',source_id='missing',max_age_seconds=3600)])
        self.plan_path.write_text(json.dumps(self.plan))
        output,report,_=self.study()
        selection=report['selections']['fold-001']
        self.assertEqual([t['status'] for t in selection['trials']],['succeeded','failed'])
        self.assertTrue(selection['trials'][0]['eligible'])
        self.assertEqual(selection['status'],'failed')
        self.assertIn('Missing or stale regime',selection['trials'][1]['error'])
        self.assertTrue((output/'cases/fold-001/training/cases/high/failure.json').exists())
        self.assertTrue((output/'cases/fold-001/training/cases/low/run/report.xlsx').exists())
        self.assertFalse((output/'cases/fold-001/run').exists())

    def test_insufficient_samples_cannot_win_even_with_a_defined_return_score(self):
        self.inputs();self.plan['selection']['minimum_complete_training_days']=30
        self.plan_path.write_text(json.dumps(self.plan))
        output,report,_=self.study()
        selection=report['selections']['fold-001']
        self.assertTrue(all(t['score'] is not None and not t['eligible'] and
                            t['ineligible_reason']=='insufficient_complete_training_days' for t in selection['trials']))
        self.assertEqual(selection['status'],'failed')
        self.assertFalse((output/'cases/fold-001/run').exists())

    def test_equal_scores_use_recipe_hash_and_ignore_candidate_input_order(self):
        self.inputs()
        for i,candidate in enumerate(self.plan['selection']['candidates'],1):
            candidate['recipe_overrides']['vol_target']=str(i/10**12)
        self.plan_path.write_text(json.dumps(self.plan))
        _,first,preview=self.study('first')
        self.plan['selection']['candidates'].reverse();self.plan_path.write_text(json.dumps(self.plan))
        _,second,_=self.study('reordered')
        self.assertEqual([t['score'] for t in first['selections']['fold-001']['trials']],[0,0])
        chosen=first['selections']['fold-001']['selected_recipe_sha256']
        self.assertEqual(chosen,min(c['recipe_sha256'] for c in preview['candidates']))
        self.assertEqual(second['selections']['fold-001']['selected_recipe_sha256'],chosen)

    def test_training_preserves_auxiliary_liquidity_and_censored_execution_at_the_cutoff(self):
        from algoFarmBT.liquidity_inputs import qualify_liquidity
        self.inputs()
        recipe=json.loads(self.recipe.read_text());recipe.update(regime_mode='supplied_multiplier',
            auxiliary_rules=[dict(field='regime',source_id='regime-fixture',max_age_seconds=10000000)])
        self.recipe.write_text(json.dumps(recipe));self.plan['recipe_sha256']=hashlib.sha256(self.recipe.read_bytes()).hexdigest()
        self.plan_path.write_text(json.dumps(self.plan))
        spec=json.loads(self.spec.read_text());spec['execution_profile']='research_lagged_volume_v1';self.spec.write_text(json.dumps(spec))
        liquidity=qualify_liquidity(self.root,self.prices,self.root/'liquidity',availability_lag_seconds=0)
        policy=dict(max_participation='.01',impact_coefficient_bps='2',max_age_seconds=3600)
        policy_path=self.root/'policy.json';policy_path.write_text(json.dumps(policy))
        auxiliary=self.root/'auxiliary.json';auxiliary.write_text(json.dumps([dict(symbol=s,field='regime',
            observed_at=T.isoformat(),available_at=T.isoformat(),value='.5',units='dimensionless',source_id='regime-fixture')
            for s in ('BTCUSDT','ETHUSDT','SOLUSDT')]))
        self.database.close()
        output,report,_=self.study('side-inputs','--duckdb',self.path,'--liquidity',liquidity,
            '--liquidity-policy',policy_path,'--auxiliary',auxiliary,'--execution-delay-bars',2)
        self.assertEqual(report['folds'][0]['status'],'succeeded',report['folds'][0]['error'])
        training_spec=self.root/'training-spec.json'
        training_spec.write_text(json.dumps(dict(spec,start=(T+528*H).isoformat(),end=(T+600*H).isoformat())))
        direct=self.root/'direct-training'
        run_momentum(self.prices,training_spec,output/'common/candidates/high.json',direct,
            source_bounds=(T+16*H,T+600*H),auxiliary_path=auxiliary,liquidity_path=liquidity,liquidity_policy=policy,
            execution_delay_bars=2)
        actual=output/'cases/fold-001/training/cases/high/run'
        for name in ('result.json','targets.json','target-timing.jsonl','auxiliary.json'):
            self.assertEqual((actual/name).read_bytes(),(direct/name).read_bytes(),name)
        timing=[decode(json.loads(line)) for line in (actual/'target-timing.jsonl').read_text().splitlines()]
        self.assertTrue(any(r['status']=='censored_at_run_end' for r in timing))

    def test_ranking_compares_exact_canonical_scores_independently_of_caller_precision(self):
        self.inputs()
        self.plan['selection']['candidates']=[dict(id='vol'+suffix,recipe_overrides={'vol_target':target})
            for suffix,target in [('500','.50'),('505','.505'),('510','.51'),('515','.515'),('520','.52')]]
        self.plan_path.write_text(json.dumps(self.plan))
        selections=[]
        for precision in (28,1):
            with localcontext() as context:
                context.prec=precision
                _,report,_=self.study('precision-'+str(precision))
                selections.append(report['selections']['fold-001'])
        normal,small=selections
        self.assertEqual([t['score'] for t in normal['trials']],[t['score'] for t in small['trials']])
        self.assertEqual(normal['selected_candidate'],'vol510')
        self.assertEqual(small['selected_candidate'],'vol510')
