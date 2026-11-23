"""Portable validation at the existing command/repository seams."""
import contextlib
import io
import json
from pathlib import Path
import unittest
import importlib.util

from algoLibs.market_data.types import decode
from algoFarmBT.backtests.execute_momentum_backtest import main
import test_training_selection as selection_fixture
from test_spot_inputs import T,H


@unittest.skipUnless(importlib.util.find_spec('duckdb'),'Requires algoLibs[duckdb]')
class PortableValidationTests(unittest.TestCase):
    setUp=selection_fixture.TrainingSelectionTests.setUp
    inputs=selection_fixture.TrainingSelectionTests.inputs
    command=selection_fixture.TrainingSelectionTests.command
    study=selection_fixture.TrainingSelectionTests.study

    def replay(self, study, output):
        with contextlib.redirect_stdout(io.StringIO()):
            main(['validate','--replay',str(study),'--output',str(output)])
        return decode(json.loads((output/'validation.json').read_text()))

    def test_duckdb_study_replays_through_native_snapshot_with_the_database_unavailable(self):
        self.inputs();self.database.close()
        original,report,_=self.study('original','--duckdb',self.path,'--portable','--trace')
        native=original/'common/native-market-data'
        self.assertTrue((native/'manifest.sha256').exists())
        snapshot=decode(json.loads((native/'manifest.json').read_text()))
        bars=[e for e in snapshot['entries'] if e['request']['dataset']=='public.md_bar']
        self.assertEqual([e['rows'] for e in bars],[593]*3)
        self.assertTrue(all(e['request']['start']==T+16*H and e['request']['end']==T+609*H for e in bars))
        self.path.rename(self.root/'unavailable.duckdb')
        output=self.root/'replay';replayed=self.replay(original,output)
        self.assertEqual(replayed['selections']['fold-001']['selected_recipe_sha256'],
                         report['selections']['fold-001']['selected_recipe_sha256'])
        child='cases/fold-001/run'
        for name in ('result.json','targets.json','signals.json','execution-trace.jsonl','calculation-trace.jsonl'):
            self.assertEqual((original/child/name).read_bytes(),(output/child/name).read_bytes(),name)
        research=decode(json.loads((output/child/'research.json').read_text()))
        self.assertEqual(research['run_details']['context']['storage_input']['backend'],'native_snapshot')
        audit=decode(json.loads((output/child/'data-access.json').read_text()))
        self.assertIn('DataRequest.accept',audit['route'])
        verification=decode(json.loads((output/'replay-verification.json').read_text()))
        self.assertEqual(verification['status'],'verified')
        self.assertGreater(len(verification['deterministic_files']),10)

    def test_portable_bundle_retains_only_selected_reference_rows(self):
        self.inputs()
        self.database.initialize(['public.instrument','public.venue','public.venue_instrument','public.asset','public.ingestion_log'])
        with self.database.transaction(readonly=False) as conn:
            conn.execute("INSERT INTO public.venue (venue_id,venue_code) VALUES (1,'BINANCE'),(99,'UNRELATED')")
            conn.execute("INSERT INTO public.asset (asset_id,symbol) VALUES (1,'BTC'),(2,'ETH'),(3,'SOL'),(4,'USDT'),(99,'UNRELATED')")
            conn.execute('INSERT INTO public.instrument (instrument_id,asset_base_id,asset_quote_id) VALUES (1,1,4),(2,2,4),(3,3,4),(99,99,4)')
            conn.execute("INSERT INTO public.venue_instrument (venue_id,instrument_id,venue_symbol) VALUES (1,1,'BTCUSDT'),(1,2,'ETHUSDT'),(1,3,'SOLUSDT'),(99,99,'UNRELATED')")
            conn.execute("INSERT INTO public.ingestion_log (id,error_message) VALUES (99,'not part of this study')")
        self.database.close()
        output,_,_=self.study('references','--duckdb',self.path,'--portable')
        manifest=decode(json.loads((output/'common/native-market-data/manifest.json').read_text()))
        entries={e['request']['dataset']:e for e in manifest['entries'] if e['request']['dataset']!='public.md_bar'}
        self.assertEqual(set(entries),{'public.instrument','public.venue','public.venue_instrument','public.asset'})
        self.assertEqual({name:e['rows'] for name,e in entries.items()},
            {'public.instrument':3,'public.venue':1,'public.venue_instrument':3,'public.asset':4})

    def test_selected_reference_pairs_do_not_include_other_listings_of_selected_instruments(self):
        from algoFarmBT.spot_inputs import qualify_price_bars
        from test_spot_inputs import write_native
        self.inputs()
        comparison=json.loads((self.root/'qualification.json').read_text())
        rows=[decode(json.loads(line)) for line in (self.root/'native.jsonl').read_text().splitlines()]
        for envelope in rows:
            if envelope['symbol']=='ETHUSDT':envelope['native']['venue_id']=2
        for source in comparison['comparisons']:
            if source['symbol']=='ETHUSDT':source['venue_id']=2
        write_native(self.root,comparison,rows)
        self.prices=self.root/'two-venues';qualify_price_bars(self.root,self.prices)
        self.database.initialize(['public.venue_instrument'])
        with self.database.transaction(readonly=False) as conn:
            conn.execute('UPDATE public.md_bar SET venue_id=2 WHERE instrument_id=2')
            conn.execute("INSERT INTO public.venue_instrument (venue_id,instrument_id,venue_symbol) VALUES (1,1,'BTCUSDT'),(2,2,'ETHUSDT'),(1,3,'SOLUSDT'),(2,1,'OTHER-BTC-LISTING')")
        self.database.close()
        output,_,_=self.study('pairs','--duckdb',self.path,'--portable')
        manifest=decode(json.loads((output/'common/native-market-data/manifest.json').read_text()))
        entries=[e for e in manifest['entries'] if e['request']['dataset']=='public.venue_instrument']
        self.assertEqual(sum(e['rows'] for e in entries),3)

    def test_missing_or_tampered_portable_artifacts_fail_before_replay_publication(self):
        self.inputs();self.database.close()
        original,_,_=self.study('original','--duckdb',self.path,'--portable')
        paths=[original/'common/native-market-data/0000.jsonl',original/'source.zip',
               original/'cases/fold-001/selected-recipe.json',original/'cases/fold-001/run/result.json']
        for i,path in enumerate(paths):
            saved=path.read_bytes()
            try:
                if i%2:path.write_bytes(saved+b'tampered')
                else:path.unlink()
                with self.assertRaisesRegex(ValueError,'missing or hash mismatch'):
                    self.replay(original,self.root/('invalid-'+str(i)))
                self.assertFalse((self.root/('invalid-'+str(i))).exists())
            finally:path.write_bytes(saved)

    def test_replay_does_not_trust_resealed_financial_results(self):
        import hashlib
        from algoLibs.market_data.types import encode
        self.inputs();self.database.close()
        original,_,_=self.study('original','--duckdb',self.path,'--portable')
        result=original/'cases/fold-001/run/result.json'
        value=decode(json.loads(result.read_text()));value['final_cash']+=1
        result.write_text(json.dumps(encode(value)))
        manifest=decode(json.loads((original/'manifest.json').read_text()))
        manifest['files']['cases/fold-001/run/result.json']=hashlib.sha256(result.read_bytes()).hexdigest()
        (original/'manifest.json').write_text(json.dumps(encode(manifest)))
        (original/'manifest.sha256').write_text(hashlib.sha256((original/'manifest.json').read_bytes()).hexdigest())
        with self.assertRaisesRegex(ValueError,'deterministic artifact differs'):
            self.replay(original,self.root/'invalid')
        self.assertFalse((self.root/'invalid').exists())

    def test_a_failed_candidate_receipt_replays_without_inventing_a_test(self):
        self.inputs();self.database.close()
        self.plan['selection']['candidates'][1]['recipe_overrides'].update(regime_mode='supplied_multiplier',
            auxiliary_rules=[dict(field='regime',source_id='missing',max_age_seconds=3600)])
        self.plan_path.write_text(json.dumps(self.plan))
        original,report,_=self.study('failed','--duckdb',self.path,'--portable')
        self.assertEqual(report['folds'][0]['status'],'failed')
        self.path.rename(self.root/'unavailable.duckdb')
        output=self.root/'replayed';replayed=self.replay(original,output)
        self.assertEqual(replayed['folds'][0]['status'],'failed')
        self.assertFalse((output/'cases/fold-001/run').exists())
        self.assertTrue((output/'cases/fold-001/training/cases/high/failure.json').exists())

    def test_disjoint_frozen_control_windows_export_no_unused_market_gap(self):
        self.inputs();self.database.close()
        recipe=json.loads(self.recipe.read_text())
        recipe.update(warmup_bars=16,horizons=[[2,8,'1']],rv_fast_span=4,rv_slow_span=8,signal_span=3,smoothing_span=3)
        self.recipe.write_text(json.dumps(recipe))
        import hashlib
        self.plan.pop('selection');self.plan.update(selection_rule='frozen_recipe',mode='rolling',
            recipe_sha256=hashlib.sha256(self.recipe.read_bytes()).hexdigest(),
            bounds=dict(start=(T+32*H).isoformat(),end=(T+85*H).isoformat()),
            window=dict(train_bars=8,test_bars=4,step_bars=40,gap_bars=1,partial_final_fold='drop'))
        self.plan_path.write_text(json.dumps(self.plan))
        original,report,_=self.study('gaps','--duckdb',self.path,'--portable')
        self.assertEqual([r['status'] for r in report['folds']],['succeeded']*2)
        native=decode(json.loads((original/'common/native-market-data/manifest.json').read_text()))
        bars=[e for e in native['entries'] if e['request']['dataset']=='public.md_bar']
        self.assertEqual(len(bars),6)
        self.assertEqual(sum(e['rows'] for e in bars),174)
        self.assertEqual({(e['request']['start'],e['request']['end']) for e in bars},
                         {(T+16*H,T+45*H),(T+56*H,T+85*H)})
        self.path.rename(self.root/'unavailable.duckdb')
        replayed=self.replay(original,self.root/'replay')
        self.assertEqual([r['status'] for r in replayed['folds']],['succeeded']*2)

    def test_source_study_modified_during_replay_is_rejected(self):
        from concurrent.futures import ThreadPoolExecutor
        import time
        self.inputs();self.database.close()
        original,_,_=self.study('original','--duckdb',self.path,'--portable')
        output=self.root/'replay';source=original/'source.zip';saved=source.read_bytes()
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(self.replay,original,output)
            try:
                deadline=time.monotonic()+30
                while not list(self.root.glob('.validation-replay-*')):
                    if future.done() or time.monotonic()>deadline:self.fail('Replay did not reach its verified-input stage')
                    time.sleep(.01)
                source.write_bytes(saved+b'changed-after-initial-verification')
                with self.assertRaisesRegex(ValueError,'hash mismatch|changed during replay'):
                    future.result(timeout=90)
                self.assertFalse(output.exists())
            finally:source.write_bytes(saved)

    def test_resealed_suite_financial_metrics_are_checked_against_recomputed_evidence(self):
        import hashlib
        from algoLibs.market_data.types import encode
        self.inputs();self.database.close()
        original,_,_=self.study('original','--duckdb',self.path,'--portable')
        path=original/'validation.json';report=decode(json.loads(path.read_text()))
        report['folds'][0]['metrics']['net_return']=999
        path.write_text(json.dumps(encode(report)))
        manifest=decode(json.loads((original/'manifest.json').read_text()))
        manifest['files']['validation.json']=hashlib.sha256(path.read_bytes()).hexdigest()
        (original/'manifest.json').write_text(json.dumps(encode(manifest)))
        (original/'manifest.sha256').write_text(hashlib.sha256((original/'manifest.json').read_bytes()).hexdigest())
        with self.assertRaisesRegex(ValueError,'suite financial evidence differs'):
            self.replay(original,self.root/'invalid')
        self.assertFalse((self.root/'invalid').exists())

    def test_explicit_zero_execution_delay_override_is_rejected_during_replay(self):
        self.inputs();self.database.close()
        original,_,_=self.study('original','--duckdb',self.path,'--portable','--execution-delay-bars',2)
        with contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                main(['validate','--replay',str(original),'--output',str(self.root/'invalid'),
                      '--execution-delay-bars','0'])
        self.assertFalse((self.root/'invalid').exists())
