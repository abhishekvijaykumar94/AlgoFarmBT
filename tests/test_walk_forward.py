"""Generated validation through public source and command interfaces."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import unittest

from algoLibs.market_data.types import decode, encode, utc
from algoFarmBT.spot_inputs import QualifiedSpotSource
from algoFarmBT.backtests.execute_momentum_backtest import main, run_momentum
import test_duckdb_momentum as duckdb_fixture
import test_validation as legacy_fixture
import importlib.util
from test_spot_inputs import T, H


@unittest.skipUnless(importlib.util.find_spec('duckdb'),'Requires algoLibs[duckdb]')
class WalkForwardTests(unittest.TestCase):
    setUp = duckdb_fixture.DuckDBMomentumContracts.setUp

    def inputs(self, mode='holdout', end=537):
        self.spec = self.root/'spec.json'
        self.spec.write_text(json.dumps(dict(start=(T+529*H).isoformat(), end=(T+end*H).isoformat(),
            interval_seconds=3600, initial_cash='1000', cash_quantum='.01', quote_currency='USDT',
            execution_profile='research_bar_next_open_v1', assumptions=['Synthetic walk-forward fixture'],
            rules=[dict(symbol=s,lot_size='.001',tick_size='.01',min_notional='0',fee_rate='.001',
                        fixed_fee='0',slippage_bps='5') for s in ('BTCUSDT','ETHUSDT','SOLUSDT')])))
        recipe=json.loads(Path(__file__).parents[1].joinpath('examples/momentum.baseline.json').read_text())
        recipe.update(name='walk-forward-fixture',warmup_bars=512)
        self.recipe=self.root/'recipe.json'; self.recipe.write_text(json.dumps(recipe))
        self.plan=dict(schema='chronological-validation-v2', name='generated-fixture', hypothesis='Frozen control',
            capital_policy='independent_reset_accounts',selection_rule='frozen_recipe',
            recipe_sha256=hashlib.sha256(self.recipe.read_bytes()).hexdigest(),inspected_through=None,
            prior_trials=dict(status='unknown'), statistics=dict(seed=23,block_days=2,replications=200,
                confidence=.95,iid_assumption=False,benchmark_annual_sharpe='0'),mode=mode,
            bounds=dict(start=(T+512*H).isoformat(),end=(T+end*H).isoformat()),designation='development',
            window=dict(train_bars=16,test_bars=8,step_bars=8,gap_bars=1,partial_final_fold='drop'),
            feature_history_policy='restart_from_declared_warmup',max_evaluations=8)
        self.plan_path=self.root/'plan.json';self.plan_path.write_text(json.dumps(self.plan))

    def command(self, destination, *options):
        with contextlib.redirect_stdout(io.StringIO()):
            main(['validate','--snapshot',str(self.prices),'--spec',str(self.spec),'--recipe',str(self.recipe),
                  '--plan',str(self.plan_path),'--output',str(destination),*map(str,options)])

    def test_preview_then_duckdb_holdout_matches_an_ordinary_bounded_run(self):
        self.inputs(); self.database.close()
        preview=self.root/'preview'
        self.command(preview,'--duckdb',self.path,'--dry-run')
        frozen=preview/'resolved-plan.json'; receipt=decode(json.loads(frozen.read_text()))
        self.assertEqual(receipt['evaluation_count'],1)
        self.assertEqual(receipt['windows'][0]['test_start'],T+529*H)
        self.assertEqual(receipt['windows'][0]['history_start'],T+17*H)
        self.assertEqual(receipt['windows'][0]['training_history_start'],T)
        self.assertFalse((preview/'cases').exists())
        output=self.root/'validation'
        self.command(output,'--duckdb',self.path,'--resolved-plan',frozen)
        report=decode(json.loads((output/'validation.json').read_text()))
        self.assertEqual([r['status'] for r in report['folds']],['succeeded'])
        child=output/report['folds'][0]['artifacts']/'run'
        direct=self.root/'direct'
        run_momentum(self.prices,self.spec,self.recipe,direct,source_bounds=(T+17*H,T+537*H))
        self.assertEqual((child/'result.json').read_bytes(),(direct/'result.json').read_bytes())
        self.assertEqual((child/'targets.json').read_bytes(),(direct/'targets.json').read_bytes())
        audit=decode(json.loads((child/'data-access.json').read_text()))
        self.assertTrue(all(r['request']['start']==T+17*H and r['request']['end']==T+537*H
                            for r in audit['reads']))
        research=decode(json.loads((child/'research.json').read_text()))
        self.assertEqual(research['run_details']['context']['coverage']['available_pre_run_bars'],512)
        self.assertTrue((output/'validation.xlsx').exists())

    def test_bounded_repository_view_returns_only_declared_history_and_scoring_bars(self):
        source = QualifiedSpotSource(self.prices, repository=self.repository,
                                    bounds=(T+500*H, T+528*H), batch_size=7)
        bars = [bar for batch in source for bar in batch]
        self.assertEqual(len(bars), 84)
        self.assertEqual((bars[0].start, bars[-1].end), (T+500*H, T+528*H))
        self.assertEqual(source.data_id, QualifiedSpotSource(self.prices).data_id)
        self.assertTrue(all(r['request']['start'] == T+500*H and r['request']['end'] == T+528*H
                            and r['rows'] == 28 and r['complete'] for r in source.read_audit))
        self.assertEqual(source.manifest['start'], T+500*H)
        self.assertEqual(source.manifest['qualified_bounds']['start'], T)

    def test_bounded_file_view_rejects_shifted_bars_even_with_consistent_hashes(self):
        manifest_path=self.prices/'manifest.json'
        manifest=decode(json.loads(manifest_path.read_text()))
        for entry in manifest['entries']:
            path=self.prices/entry['file'];rows=[]
            for line in path.read_text().splitlines():
                row=decode(json.loads(line));row['start']+=H/2;row['end']+=H/2
                rows.append(json.dumps(encode(row)))
            path.write_text('\n'.join(rows)+'\n')
            entry['sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
        for field in ('start','end'):manifest[field]=utc(manifest[field])+H/2
        manifest_path.write_text(json.dumps(encode(manifest)))
        (self.prices/'manifest.sha256').write_text(hashlib.sha256(manifest_path.read_bytes()).hexdigest())
        source=QualifiedSpotSource(self.prices,bounds=(T+500*H,T+528*H))
        with self.assertRaisesRegex(ValueError,'aligned bounded price coverage'):
            list(source)

    def test_rolling_and_expanding_share_test_windows_but_move_training_differently(self):
        for mode, train_starts in [('rolling',[512,520,528]),('expanding',[512,512,512])]:
            with self.subTest(mode=mode):
                self.inputs(mode,end=550)
                self.plan['window']['partial_final_fold']='include'
                self.plan_path.write_text(json.dumps(self.plan))
                preview=self.root/(mode+'-preview')
                self.command(preview,'--dry-run')
                frozen=preview/'resolved-plan.json';receipt=decode(json.loads(frozen.read_text()))
                self.assertEqual([w['train_start'] for w in receipt['windows']],[T+i*H for i in train_starts])
                self.assertEqual([w['train_end'] for w in receipt['windows']],[T+i*H for i in (528,536,544)])
                self.assertEqual([(w['test_start'],w['test_end']) for w in receipt['windows']],
                                 [(T+529*H,T+537*H),(T+537*H,T+545*H),(T+545*H,T+550*H)])
                output=self.root/mode
                self.command(output,'--resolved-plan',frozen)
                report=decode(json.loads((output/'validation.json').read_text()))
                self.assertEqual([r['status'] for r in report['folds']],['succeeded']*3)
                self.assertEqual([r['path'][0]['rebased_equity'] for r in report['folds']],[1,1,1])

    def test_preview_rejects_recipe_and_price_universe_mismatch_before_trials(self):
        self.inputs()
        spec=json.loads(self.spec.read_text());spec['rules'].pop();self.spec.write_text(json.dumps(spec))
        with self.assertRaisesRegex(ValueError,'symbols'):
            self.command(self.root/'invalid','--dry-run')
        self.assertFalse((self.root/'invalid').exists())

    def test_generated_fold_forwards_frozen_liquidity_auxiliary_and_execution_inputs(self):
        from algoFarmBT.liquidity_inputs import qualify_liquidity
        self.inputs()
        spec=json.loads(self.spec.read_text());spec['execution_profile']='research_lagged_volume_v1';self.spec.write_text(json.dumps(spec))
        liquidity=qualify_liquidity(self.root,self.prices,self.root/'liquidity',availability_lag_seconds=0)
        recipe=json.loads(self.recipe.read_text())
        recipe.update(regime_mode='supplied_multiplier',auxiliary_rules=[dict(field='regime',source_id='fixture',max_age_seconds=10000000)])
        self.recipe.write_text(json.dumps(recipe));self.plan['recipe_sha256']=hashlib.sha256(self.recipe.read_bytes()).hexdigest()
        self.plan_path.write_text(json.dumps(self.plan))
        auxiliary=self.root/'auxiliary.json'
        auxiliary.write_text(json.dumps([dict(symbol=s,field='regime',observed_at=T.isoformat(),available_at=T.isoformat(),
            value='.5',units='dimensionless',source_id='fixture') for s in ('BTCUSDT','ETHUSDT','SOLUSDT')]))
        policy=dict(max_participation='.01',impact_coefficient_bps='2',max_age_seconds=3600)
        policy_path=self.root/'policy.json';policy_path.write_text(json.dumps(policy))
        reporting=self.root/'reporting.json';reporting.write_text(json.dumps(dict(fixed_reference_weights={'BTCUSDT':'.2','ETHUSDT':'.2','SOLUSDT':'.2'},report_context={'experiment':'side-input-test'})))
        options=['--liquidity',liquidity,'--liquidity-policy',policy_path,'--auxiliary',auxiliary,
                 '--execution-delay-bars','1','--trace','--report-config',reporting]
        preview=self.root/'preview';self.command(preview,*options,'--dry-run')
        output=self.root/'validation';self.command(output,*options,'--resolved-plan',preview/'resolved-plan.json')
        report=decode(json.loads((output/'validation.json').read_text()));self.assertEqual(report['folds'][0]['status'],'succeeded',report['folds'][0]['error'])
        child=output/report['folds'][0]['artifacts']/'run';direct=self.root/'direct'
        run_momentum(self.prices,self.spec,self.recipe,direct,source_bounds=(T+17*H,T+537*H),
            auxiliary_path=auxiliary,retain_trace=True,execution_delay_bars=1,liquidity_path=liquidity,liquidity_policy=policy,
            fixed_reference_weights={'BTCUSDT':'.2','ETHUSDT':'.2','SOLUSDT':'.2'})
        for name in ('result.json','targets.json','target-timing.jsonl','execution-trace.jsonl','benchmarks.json'):
            self.assertEqual((child/name).read_bytes(),(direct/name).read_bytes(),name)
        self.assertEqual((child/'auxiliary.json').read_bytes(),auxiliary.read_bytes())
        self.assertEqual((output/'common/liquidity/manifest.json').read_bytes(),(liquidity/'manifest.json').read_bytes())
        research=decode(json.loads((child/'research.json').read_text()))
        self.assertEqual(research['run_details']['context']['experiment'],'side-input-test')

    def test_preview_rejects_missing_liquidity_for_the_existing_execution_profile(self):
        self.inputs()
        spec=json.loads(self.spec.read_text());spec['execution_profile']='research_lagged_volume_v1';self.spec.write_text(json.dumps(spec))
        with self.assertRaisesRegex(ValueError,'Liquidity profile'):
            self.command(self.root/'invalid','--dry-run')
        self.assertFalse((self.root/'invalid').exists())

    def test_invalid_schedules_and_warmup_fail_before_trials(self):
        self.inputs('rolling',end=550)
        original=json.loads(json.dumps(self.plan))
        changes=[('overlap',{'window':dict(original['window'],step_bars=7)}),
                 ('partial',{'window':dict(original['window'],partial_final_fold='reject')}),
                 ('budget',{'max_evaluations':1}),('boolean',{'window':dict(original['window'],train_bars=True)}),
                 ('warmup',{'bounds':dict(start=(T+511*H).isoformat(),end=(T+550*H).isoformat())}),
                 ('coverage',{'bounds':dict(start=(T+512*H).isoformat(),end=(T+800*H).isoformat())}),
                 ('unaligned',{'bounds':dict(start=(T+512*H).isoformat(),end=(T+550*H+H/2).isoformat())}),
                 ('naive',{'bounds':dict(start='2026-05-22T08:00:00',end=(T+550*H).isoformat())}),
                 ('history',{'feature_history_policy':'use_all_future_data'})]
        for name,change in changes:
            with self.subTest(name=name):
                self.plan_path.write_text(json.dumps(dict(original,**change)))
                with self.assertRaises(ValueError):self.command(self.root/name,'--dry-run')
                self.assertFalse((self.root/name).exists())

    def test_drop_policy_keeps_only_complete_folds_and_allows_declared_test_gaps(self):
        self.inputs('rolling',end=550)
        self.plan['window']['step_bars']=10;self.plan_path.write_text(json.dumps(self.plan))
        self.command(self.root/'preview','--dry-run')
        receipt=decode(json.loads((self.root/'preview/resolved-plan.json').read_text()))
        self.assertEqual([(w['test_start'],w['test_end']) for w in receipt['windows']],
                         [(T+529*H,T+537*H),(T+539*H,T+547*H)])
        self.assertEqual(receipt['evaluation_count'],2)

    def test_changed_inputs_or_resolved_windows_are_rejected_before_execution(self):
        self.inputs();preview=self.root/'preview';self.command(preview,'--dry-run')
        frozen=preview/'resolved-plan.json';original_spec=self.spec.read_bytes()
        spec=json.loads(original_spec);spec['initial_cash']='2000';self.spec.write_text(json.dumps(spec))
        with self.assertRaisesRegex(ValueError,'Frozen resolved plan'):
            self.command(self.root/'changed-spec','--resolved-plan',frozen)
        self.spec.write_bytes(original_spec)
        original=frozen.read_bytes()
        frozen.write_bytes(original+b' ')
        with self.assertRaisesRegex(ValueError,'Frozen resolved plan'):
            self.command(self.root/'tampered','--resolved-plan',frozen)
        for name in ('changed-spec','tampered'):self.assertFalse((self.root/name).exists())

    def test_repository_mismatch_during_preflight_does_not_fall_back_to_qualified_files(self):
        self.inputs();self.database.close()
        preview=self.root/'preview';self.command(preview,'--duckdb',self.path,'--dry-run')
        from algoLibs.market_data import DuckDBDatabase
        db=DuckDBDatabase(self.path,read_only=False)
        try:
            with db.transaction(readonly=False) as conn:
                conn.execute('UPDATE public.md_bar SET close=999 WHERE instrument_id=1 AND ts=?',[T+520*H])
        finally:db.close()
        with self.assertRaisesRegex(ValueError,'differ from the qualified'):
            self.command(self.root/'changed-db','--duckdb',self.path,'--resolved-plan',preview/'resolved-plan.json')
        self.assertFalse((self.root/'changed-db').exists())


class LegacyPreviewTests(unittest.TestCase):
    setUp=legacy_fixture.ValidationTests.setUp

    def test_v1_does_not_publish_an_unverified_preview(self):
        (self.snapshot/'BTCUSDT.jsonl').unlink()
        plan_path=self.root/'plan.json';plan_path.write_text(json.dumps(self.plan))
        output=self.root/'preview'
        with self.assertRaisesRegex(ValueError,'chronological-validation-v2'):
            main(['validate','--snapshot',str(self.snapshot),'--spec',str(self.config),'--recipe',str(self.recipe),
                  '--plan',str(plan_path),'--output',str(output),'--dry-run'])
        self.assertFalse(output.exists())
