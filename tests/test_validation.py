"""Chronological validation through the established application CLI seam."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from openpyxl import load_workbook
import math
from dataclasses import asdict
from algoLibs.backtest import SpotBar
from algoLibs.market_data.types import encode

from algoLibs.market_data.types import decode
from algoFarmBT.backtests.execute_momentum_backtest import main
from algoFarmBT.spot_inputs import qualify_price_bars
from test_spot_inputs import make_inputs, T, H


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);inputs=self.root/'inputs';inputs.mkdir();make_inputs(inputs,trend=True)
        self.snapshot=self.root/'prices';qualify_price_bars(inputs,self.snapshot)
        recipe=json.loads((Path(__file__).resolve().parents[1]/'examples/momentum.baseline.json').read_text())
        recipe.update(warmup_bars=600,histogram='ewmac',signal_span=None)
        self.recipe=self.root/'recipe.json';self.recipe.write_text(json.dumps(recipe))
        self.config=self.root/'spec.json'
        self.config.write_text(json.dumps(dict(start=(T+600*H).isoformat(),end=(T+648*H).isoformat(),
            interval_seconds=3600,initial_cash='1000',cash_quantum='.01',quote_currency='USDT',
            execution_profile='research_bar_next_open_v1',assumptions=['Synthetic validation fixture'],
            rules=[dict(symbol=s,lot_size='.01',tick_size='.01',min_notional='0',fee_rate='.001',fixed_fee='0',slippage_bps='5')
                   for s in ('BTCUSDT','ETHUSDT','SOLUSDT')])))
        self.plan=dict(schema='chronological-validation-v1',name='fixture-v1',hypothesis='Frozen recipe chronological evaluation',
            capital_policy='independent_reset_accounts',selection_rule='frozen_recipe',
            recipe_sha256=hashlib.sha256(self.recipe.read_bytes()).hexdigest(),inspected_through=(T+550*H).isoformat(),
            prior_trials=dict(status='unknown'),statistics=dict(seed=23,block_days=2,replications=200,confidence=.95,
                iid_assumption=False,benchmark_annual_sharpe='0'),
            folds=[dict(id='fold-one',designation='development',development_start=T.isoformat(),
                development_end=(T+500*H).isoformat(),calibration_end=(T+500*H).isoformat(),
                selected_at=(T+550*H).isoformat(),test_start=(T+600*H).isoformat(),test_end=(T+624*H).isoformat()),
                dict(id='fold-two',designation='development',development_start=T.isoformat(),
                development_end=(T+500*H).isoformat(),calibration_end=(T+500*H).isoformat(),
                selected_at=(T+550*H).isoformat(),test_start=(T+624*H).isoformat(),test_end=(T+648*H).isoformat())])

    def run_plan(self,name='validation',extra_args=()):
        path=self.root/'plan.json';path.write_text(json.dumps(self.plan));output=self.root/name
        with contextlib.redirect_stdout(io.StringIO()):
            main(['validate','--snapshot',str(self.snapshot),'--spec',str(self.config),'--recipe',str(self.recipe),
                  '--plan',str(path),'--output',str(output)]+list(extra_args))
        return output,decode(json.loads((output/'validation.json').read_text()))

    def long_unseen_inputs(self):
        """Explicit synthetic instruments, not claimed historical market coverage."""
        symbols=['TESTA','TESTB','TESTC'];count=2040;entries=[]
        directory=self.root/'long-prices';directory.mkdir()
        for index,symbol in enumerate(symbols):
            rows=[]
            for i in range(count):
                price=str(round(100+i*.02+2*math.sin(i/15+index),6))
                close=str(round(100+(i+1)*.02+2*math.sin((i+1)/15+index),6))
                rows.append(json.dumps(encode(asdict(SpotBar(symbol,T+i*H,T+(i+1)*H,price,close)))))
            path=directory/(symbol+'.jsonl');path.write_text('\n'.join(rows)+'\n')
            entries.append(dict(symbol=symbol,file=path.name,rows=count,sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
        manifest=dict(format='qualified-spot-price-bars-v1',start=T,end=T+count*H,entries=entries,nature='synthetic fixture')
        raw=json.dumps(encode(manifest)).encode();(directory/'manifest.json').write_bytes(raw)
        (directory/'manifest.sha256').write_text(hashlib.sha256(raw).hexdigest()+'\n');self.snapshot=directory
        recipe=json.loads(self.recipe.read_text())
        for field in ('symbol_budgets','weight_caps'):recipe[field]=dict(zip(symbols,recipe[field].values()))
        self.recipe.write_text(json.dumps(recipe));self.plan['recipe_sha256']=hashlib.sha256(self.recipe.read_bytes()).hexdigest()
        config=json.loads(self.config.read_text());config['end']=(T+count*H).isoformat()
        for rule,symbol in zip(config['rules'],symbols):rule['symbol']=symbol
        self.config.write_text(json.dumps(config))
        for index,fold in enumerate(self.plan['folds']):
            fold.update(designation='test',test_start=(T+(600+index*720)*H).isoformat(),test_end=(T+(1320+index*720)*H).isoformat())

    def test_cli_retains_two_independent_fold_accounts_without_fictitious_compounding(self):
        output,report=self.run_plan()
        self.assertEqual([r['status'] for r in report['folds']],['succeeded','succeeded'])
        self.assertEqual(report['capital_policy'],'independent_reset_accounts')
        self.assertIsNone(report['primary']['continuous_account_return'])
        for row in report['folds']:
            account=decode(json.loads((output/row['artifacts']/'run/result.json').read_text()))
            self.assertEqual(account['equity'][0]['equity'],1000)
            self.assertEqual(row['path'][0]['rebased_equity'],1)
        self.assertTrue((output/'validation.xlsx').exists())

    def test_overlapping_test_folds_are_rejected_before_any_trial(self):
        self.plan['folds'][1]['test_start']=(T+623*H).isoformat()
        with self.assertRaisesRegex(ValueError,'overlap'):
            self.run_plan()
        self.assertFalse((self.root/'validation').exists())

    def test_test_recipe_selection_after_first_decision_is_rejected(self):
        fold=self.plan['folds'][0];fold['designation']='test'
        fold['selected_at']=(T+601*H).isoformat()
        with self.assertRaisesRegex(ValueError,'selection'):
            self.run_plan()

    def test_calibration_cannot_use_observations_after_recipe_selection(self):
        fold=self.plan['folds'][0];fold['designation']='test'
        fold['calibration_end']=(T+551*H).isoformat()
        with self.assertRaisesRegex(ValueError,'calibration'):
            self.run_plan()

    def test_previously_inspected_history_cannot_be_relabelled_out_of_sample(self):
        for fold in self.plan['folds']:fold['designation']='test'
        _,report=self.run_plan()
        self.assertEqual(report['primary']['eligible_folds'],0)
        self.assertTrue(all('previously_inspected' in r['eligibility_reasons'] for r in report['folds']))

    def test_ineligible_test_request_remains_development_in_each_child_report(self):
        for fold in self.plan['folds']:fold['designation']='test'
        output,report=self.run_plan()
        child=decode(json.loads((output/report['folds'][0]['artifacts']/'run/research.json').read_text()))
        self.assertEqual(child['research_record']['designation'],'development')
        self.assertIn('previously_inspected',child['run_details']['context']['validation_fold']['eligibility_reasons'])
        self.assertEqual(report['folds'][0]['requested_designation'],'test')

    def test_short_samples_expose_missing_uncertainty_and_trial_history(self):
        _,report=self.run_plan()
        stats=report['exploratory']['statistics']
        self.assertEqual(stats['observation_count'],2)
        self.assertIsNone(stats['bootstrap']['mean_daily_return_interval'])
        self.assertIsNone(stats['sharpe_evidence']['psr'])
        self.assertIsNone(stats['sharpe_evidence']['dsr'])
        self.assertIn('short',stats['bootstrap']['reason'])

    def test_declared_block_bootstrap_preserves_segments_and_is_reproducible(self):
        self.long_unseen_inputs()
        output,report=self.run_plan();_,replay=self.run_plan('replay')
        self.assertEqual(report['primary']['eligible_folds'],2)
        stats=report['primary']['statistics'];bootstrap=stats['bootstrap']
        self.assertEqual(stats['segment_lengths'],[30,30])
        self.assertEqual(bootstrap['status'],'available')
        self.assertEqual(bootstrap['method'],'stationary_per_segment')
        self.assertEqual(bootstrap,replay['primary']['statistics']['bootstrap'])
        self.assertLess(bootstrap['mean_daily_return_interval'][0],stats['mean_daily_return'])
        self.assertGreater(bootstrap['mean_daily_return_interval'][1],stats['mean_daily_return'])
        workbook=load_workbook(output/'validation.xlsx')
        rows={r[0].value:r for r in workbook['Performance'].iter_rows(min_row=2)}
        self.assertIn('%',rows['bootstrap.mean_daily_return_interval.0'][1].number_format)
        self.assertNotIn('%',rows['bootstrap.annualised_sharpe_interval.0'][1].number_format)
        workbook.close()

    def test_selected_recipe_hash_cannot_change_after_plan_declaration(self):
        recipe=json.loads(self.recipe.read_text());recipe['name']='changed-after-declaration'
        self.recipe.write_text(json.dumps(recipe))
        with self.assertRaisesRegex(ValueError,'recipe'):
            self.run_plan()

    def test_conditional_psr_and_track_record_match_independent_formula_fixture(self):
        self.long_unseen_inputs();self.plan['statistics']['iid_assumption']=True
        _,report=self.run_plan();evidence=report['primary']['statistics']['sharpe_evidence']
        # Independently evaluated from the fixed 60-day return vector with
        # NumPy moments and Bailey/Lopez de Prado (2012), equations 11/13.
        self.assertAlmostEqual(evidence['psr'],.7936606250972983,places=12)
        self.assertEqual(evidence['minimum_track_record_observations'],239)
        self.assertIsNone(evidence['dsr'])  # no invented prior trial count

    def test_invalid_statistical_plan_is_rejected_before_creating_trials(self):
        self.plan['statistics']['confidence']=2
        with self.assertRaisesRegex(ValueError,'confidence'):
            self.run_plan()

    def test_dsr_uses_documented_effective_trials_and_preserves_complete_history(self):
        self.long_unseen_inputs();self.plan['statistics']['iid_assumption']=True
        self.plan['prior_trials']=dict(status='complete',complete_through=(T+550*H).isoformat(),
            dependence_method='external_effective_trials',dependence_evidence='Synthetic fixture: predeclared dependent-trial grouping',
            effective_trials=2.5,records=[dict(id='trial-'+str(i),daily_sharpe=str(value),evaluated_at=(T+500*H).isoformat())
                                        for i,value in enumerate([-.02,0,.02,.04])])
        _,report=self.run_plan();evidence=report['primary']['statistics']['sharpe_evidence']
        self.assertAlmostEqual(evidence['dsr_reference_daily_sharpe'],0.01839546068208082,places=12)
        self.assertLess(evidence['dsr'],evidence['psr'])
        self.assertEqual(evidence['effective_trials'],2.5)
        self.assertEqual(report['plan']['prior_trials'],self.plan['prior_trials'])

    def test_full_sample_selection_is_not_a_supported_test_rule(self):
        self.plan['selection_rule']='best_full_sample'
        with self.assertRaisesRegex(ValueError,'selection'):
            self.run_plan()

    def test_unsupported_fold_capital_policy_is_not_silently_ignored(self):
        self.plan['folds'][0]['capital_policy']='continuous_account'
        with self.assertRaisesRegex(ValueError,'fold fields'):
            self.run_plan()

    def test_workbook_separates_eligible_and_exploratory_native_metrics(self):
        output,report=self.run_plan();book=load_workbook(output/'validation.xlsx')
        sheet=book['Summary'];metrics={r[0].value:r[1] for r in sheet.iter_rows(min_row=2)}
        self.assertEqual(metrics['statistics.observation_count'].value,0)
        performance=book['Performance'];rows={r[0].value:r for r in performance.iter_rows(min_row=2)}
        self.assertEqual(rows['observation_count'][2].value,2)
        self.assertEqual(rows['observation_count'][2].data_type,'n')
        self.assertEqual(book['Folds']['F2'].hyperlink.target,report['folds'][0]['report'])
        self.assertEqual(book['OOS segments'].max_row,1)
        book.close()

    def test_out_of_sample_paths_keep_a_gap_and_reset_as_separate_segments(self):
        self.long_unseen_inputs()
        self.plan['folds'][1]['test_start']=(T+1344*H).isoformat()
        _,report=self.run_plan()
        segments=report['oos_segments']
        self.assertEqual(len(segments),2)
        self.assertEqual(segments[1]['path'][0]['time']-segments[0]['path'][-1]['time'],24*H)
        self.assertEqual([s['path'][0]['rebased_equity'] for s in segments],[1,1])
        self.assertIsNone(report['primary']['continuous_account_return'])

    def test_failed_fold_receipts_and_later_success_are_retained(self):
        recipe=json.loads(self.recipe.read_text());recipe['warmup_bars']=610
        self.recipe.write_text(json.dumps(recipe));self.plan['recipe_sha256']=hashlib.sha256(self.recipe.read_bytes()).hexdigest()
        output,report=self.run_plan()
        self.assertEqual([r['status'] for r in report['folds']],['failed','succeeded'])
        self.assertTrue((output/'cases/fold-one/failure.json').exists())
        self.assertTrue((output/'common/recipe.json').exists())
        self.assertIn('failed_fold',report['folds'][0]['eligibility_reasons'])

    def test_constant_series_does_not_fabricate_sharpe_or_probability(self):
        self.long_unseen_inputs();self.plan['statistics']['iid_assumption']=True
        manifest=decode(json.loads((self.snapshot/'manifest.json').read_text()))
        for entry in manifest['entries']:
            path=self.snapshot/entry['file'];rows=[]
            for line in path.read_text().splitlines():
                bar=decode(json.loads(line));bar.update(open='100',close='100');rows.append(json.dumps(encode(bar)))
            path.write_text('\n'.join(rows)+'\n');entry['sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
        raw=json.dumps(encode(manifest)).encode();(self.snapshot/'manifest.json').write_bytes(raw)
        (self.snapshot/'manifest.sha256').write_text(hashlib.sha256(raw).hexdigest()+'\n')
        _,report=self.run_plan();stats=report['primary']['statistics']
        self.assertIsNone(stats['annualised_daily_sharpe'])
        self.assertIsNone(stats['sharpe_evidence']['psr'])
        self.assertEqual(stats['sharpe_evidence']['reason'],'zero variance')
        self.assertEqual(stats['bootstrap']['mean_daily_return_interval'],[0,0])
        self.assertIsNone(stats['bootstrap']['annualised_sharpe_interval'])

    def test_fold_results_include_ledger_net_performance_and_costs(self):
        output,report=self.run_plan()
        for fold in report['folds']:
            run=output/fold['artifacts']/'run'
            account=decode(json.loads((run/'result.json').read_text()))
            self.assertEqual(fold['metrics']['net_pnl'],account['equity'][-1]['equity']-1000)
            self.assertEqual(fold['metrics']['total_fees'],account['total_fees'])

    def test_nearly_one_effective_trial_does_not_inflate_deflated_sharpe(self):
        self.long_unseen_inputs();self.plan['statistics']['iid_assumption']=True
        self.plan['prior_trials']=dict(status='complete',complete_through=(T+550*H).isoformat(),
            dependence_method='external_effective_trials',dependence_evidence='Strongly dependent synthetic trials',
            effective_trials=1.1,records=[dict(id='trial-'+str(i),daily_sharpe=str(v),evaluated_at=(T+500*H).isoformat())
                                        for i,v in enumerate([-.02,0,.02,.04])])
        _,report=self.run_plan();evidence=report['primary']['statistics']['sharpe_evidence']
        self.assertIsNone(evidence['dsr'])
        self.assertIn('2',evidence['dsr_reason'])

    def test_validation_preserves_supported_auxiliary_inputs_and_freezes_them(self):
        from algoFarmBT.backtests.execute_momentum_backtest import run_momentum
        recipe=json.loads(self.recipe.read_text())
        recipe.update(regime_mode='supplied_multiplier',auxiliary_rules=[dict(field='regime',source_id='fixture-regime',max_age_seconds=10000000)])
        self.recipe.write_text(json.dumps(recipe));self.plan['recipe_sha256']=hashlib.sha256(self.recipe.read_bytes()).hexdigest()
        rows=[dict(symbol=s,field='regime',observed_at=T.isoformat(),available_at=T.isoformat(),
                   value='1',units='dimensionless',source_id='fixture-regime') for s in ('BTCUSDT','ETHUSDT','SOLUSDT')]
        auxiliary=self.root/'auxiliary.json';auxiliary.write_text(json.dumps(rows))
        # Positive control: the existing momentum workflow supports this recipe.
        direct=run_momentum(self.snapshot,self.config,self.recipe,self.root/'direct',auxiliary_path=auxiliary)
        self.assertEqual(len(direct.equity),49)
        output,report=self.run_plan(extra_args=['--auxiliary',str(auxiliary)])
        self.assertEqual([r['status'] for r in report['folds']],['succeeded','succeeded'])
        self.assertEqual((output/'common/auxiliary.json').read_bytes(),auxiliary.read_bytes())
        self.assertEqual(report['auxiliary_sha256'],hashlib.sha256(auxiliary.read_bytes()).hexdigest())
        for fold in report['folds']:
            self.assertEqual((output/fold['artifacts']/'run/auxiliary.json').read_bytes(),auxiliary.read_bytes())
        replay,_=self.run_plan('aux-replay',extra_args=['--auxiliary',str(output/'common/auxiliary.json')])
        self.assertEqual((replay/'manifest.sha256').read_bytes(),(output/'manifest.sha256').read_bytes())
        # Only availability changes; the established strategy must not use
        # these otherwise valid values before their declared availability.
        for row in rows:row['available_at']=(T+2*H).isoformat()
        auxiliary.write_text(json.dumps(rows))
        _,future=self.run_plan('future-aux',extra_args=['--auxiliary',str(auxiliary)])
        self.assertEqual([r['status'] for r in future['folds']],['failed','failed'])
        self.assertTrue(all('Missing or stale regime' in r['error'] for r in future['folds']))


if __name__=='__main__':unittest.main()
