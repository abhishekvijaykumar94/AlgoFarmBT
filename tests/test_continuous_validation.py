"""Continuous-account validation through the existing public CLI and runner."""
import json
import importlib.util
import unittest
from algoLibs.market_data.types import decode
from algoFarmBT.backtests.execute_momentum_backtest import run_snapshot,run_momentum,main
import test_walk_forward as fixture
import test_training_selection as training_fixture
from test_spot_inputs import T,H


@unittest.skipUnless(importlib.util.find_spec('duckdb'),'Requires algoLibs[duckdb]')
class ContinuousValidationTests(unittest.TestCase):
    setUp=fixture.WalkForwardTests.setUp
    command=fixture.WalkForwardTests.command

    def inputs(self):
        fixture.WalkForwardTests.inputs(self,mode='rolling',end=545)
        self.plan['capital_policy']='continuous_account'
        self.plan_path.write_text(json.dumps(self.plan))

    def study(self,*options,name='study'):
        preview=self.root/(name+'-preview');self.command(preview,*options,'--dry-run')
        output=self.root/name;self.command(output,*options,'--resolved-plan',preview/'resolved-plan.json')
        return output,decode(json.loads((output/'validation.json').read_text()))

    def test_contiguous_fold_targets_run_in_one_existing_account(self):
        self.inputs();self.database.close()
        output,report=self.study('--duckdb',self.path,'--trace')
        account=report['continuous'];self.assertEqual(account['status'],'succeeded',account['error'])
        context=decode(json.loads((output/account['artifacts']/'run/research.json').read_text()))['run_details']['context']
        self.assertEqual(context['strategy']['kind'],'scheduled-momentum-recipes')
        folder=output/account['artifacts'];actual=folder/'run'
        targets=[]
        for fold in report['folds']:
            targets.extend(json.loads((output/fold['artifacts']/'run/targets.json').read_text()))
        supplied=self.root/'targets.json';supplied.write_text(json.dumps(targets))
        spec=json.loads(self.spec.read_text());spec.update(start=(T+529*H).isoformat(),end=(T+545*H).isoformat())
        config=self.root/'continuous-spec.json';config.write_text(json.dumps(spec))
        direct=self.root/'direct'
        run_snapshot(self.prices,config,supplied,direct,retain_diagnostics=True,annualisation_hours=8760,source_bounds=(T+17*H,T+545*H))
        for name in ('result.json','report.json','execution-trace.jsonl'):
            if name.endswith('.jsonl'):
                self.assertEqual([decode(json.loads(r)) for r in (actual/name).read_text().splitlines()],
                    [decode(json.loads(r)) for r in (direct/name).read_text().splitlines()],name)
            else:self.assertEqual(decode(json.loads((actual/name).read_text())),decode(json.loads((direct/name).read_text())),name)
        result=decode(json.loads((actual/'result.json').read_text()))
        self.assertEqual(len(result['equity']),17)
        self.assertEqual([s['start'] for s in account['switches']],[T+529*H,T+537*H])
        self.assertFalse(account['eligible'])
        self.assertIsNone(report['primary']['continuous_account_return'])
        self.assertEqual(decode(json.loads((output/'resolved-plan.json').read_text()))['evaluation_count'],3)

    def test_gapped_tests_are_rejected_before_preview_or_any_trial(self):
        self.inputs();self.plan['window']['step_bars']=16
        self.plan['bounds']['end']=(T+553*H).isoformat()
        self.plan_path.write_text(json.dumps(self.plan))
        preview=self.root/'invalid-preview'
        with self.assertRaisesRegex(ValueError,'contiguous'):
            self.command(preview,'--dry-run')
        self.assertFalse(preview.exists())

    def test_selected_recipes_and_continuous_results_are_distinct_in_json_and_excel(self):
        from openpyxl import load_workbook
        training_fixture.TrainingSelectionTests.inputs(self)
        self.plan.update(capital_policy='continuous_account',mode='rolling')
        self.plan['bounds']['end']=(T+617*H).isoformat()
        self.plan_path.write_text(json.dumps(self.plan))
        output,report=self.study()
        account=report['continuous'];self.assertEqual(account['status'],'succeeded',account['error'])
        self.assertEqual([s['recipe_sha256'] for s in account['switches']],
            [report['selections'][f]['selected_recipe_sha256'] for f in ('fold-001','fold-002')])
        book=load_workbook(output/'validation.xlsx');self.addCleanup(book.close)
        self.assertIn('Continuous account',book.sheetnames)
        self.assertIn('Recipe switches',book.sheetnames)
        cells=dict(book['Continuous account'].values)
        self.assertAlmostEqual(cells['metrics.net_return'],float(account['metrics']['net_return']))
        switches=list(book['Recipe switches'].values)
        self.assertEqual(len(switches),3)
        self.assertIn('history_start',switches[0]);self.assertIn('execution_start',switches[0])
        self.assertIn('Training',book.sheetnames);self.assertIn('Selection',book.sheetnames)

    def test_continuous_study_replays_offline_with_verified_switches_and_ledger(self):
        self.inputs();self.database.close()
        output,report=self.study('--duckdb',self.path,'--portable','--trace')
        self.path.rename(self.root/'unavailable.duckdb')
        replay=self.root/'replay';main(['validate','--replay',str(output),'--output',str(replay)])
        proof=decode(json.loads((replay/'replay-verification.json').read_text()))
        self.assertEqual(proof['status'],'verified')
        self.assertIn('cases/continuous-account/run/recipe-schedule.json',proof['deterministic_files'])
        self.assertEqual((output/'cases/continuous-account/run/result.json').read_bytes(),
            (replay/'cases/continuous-account/run/result.json').read_bytes())

    def test_continuous_run_keeps_the_fold_development_classification(self):
        self.inputs();output,report=self.study()
        self.assertEqual(report['continuous']['designation'],'development')
        path=output/report['continuous']['artifacts']/'run/research.json'
        context=decode(json.loads(path.read_text()))['run_details']['context']
        self.assertEqual(context['research_record']['designation'],'development')
        self.assertFalse(context['validation_continuous']['eligible'])
        self.assertTrue(context['validation_continuous']['eligibility_reasons'])

    def test_delayed_decisions_cross_switches_with_actual_holdings_and_costs(self):
        self.inputs();output,report=self.study('--execution-delay-bars',2,'--trace')
        targets=[]
        for row in report['folds']:
            folder=output/row['artifacts'];direct=self.root/(row['case_id']+'-decisions')
            window=next(w for w in decode(json.loads((output/'resolved-plan.json').read_text()))['windows'] if w['id']==row['case_id'])
            run_momentum(self.prices,folder/'spec-input.json',self.recipe,direct,
                source_bounds=(window['history_start'],window['test_end']))
            targets.extend(json.loads((direct/'targets.json').read_text()))
        target_file=self.root/'decisions.json';target_file.write_text(json.dumps(targets))
        config=self.root/'account-spec.json';spec=json.loads(self.spec.read_text())
        spec.update(start=(T+529*H).isoformat(),end=(T+545*H).isoformat());config.write_text(json.dumps(spec))
        ordinary=self.root/'ordinary'
        run_snapshot(self.prices,config,target_file,ordinary,annualisation_hours=8760,
            execution_delay_bars=2,retain_diagnostics=True,source_bounds=(T+17*H,T+545*H))
        actual=output/report['continuous']['artifacts']/'run'
        result=decode(json.loads((actual/'result.json').read_text()))
        self.assertEqual(result,decode(json.loads((ordinary/'result.json').read_text())))
        timing=[decode(json.loads(r)) for r in (actual/'target-timing.jsonl').read_text().splitlines()]
        self.assertIn(dict(decision_time=T+535*H,execution_time=T+537*H,delay_bars=2,status='scheduled'),timing)
        self.assertEqual(sum(r['status']=='censored_at_run_end' for r in timing),2)
        trace=[decode(json.loads(r)) for r in (actual/'execution-trace.jsonl').read_text().splitlines()]
        boundary=[r for r in trace if r['time']==T+537*H]
        self.assertTrue(boundary)
        before=next(e for e in result['equity'] if e['time']==T+537*H)
        from decimal import Decimal
        for row in boundary:
            self.assertEqual(row['held_lots'],int(dict(before['quantities'])[row['symbol']]/Decimal('.001')))
        self.assertTrue(any(r['held_lots']>0 for r in boundary))
        self.assertTrue(any(f['time']==T+539*H and f['fee']>0 for f in result['fills']))

    def test_later_test_prices_cannot_change_earlier_selection_or_continuous_fills(self):
        import csv,hashlib,io,zipfile
        from test_spot_inputs import write_native
        from algoFarmBT.spot_inputs import qualify_price_bars
        training_fixture.TrainingSelectionTests.inputs(self)
        self.plan.update(capital_policy='continuous_account',mode='expanding')
        self.plan['bounds']['end']=(T+617*H).isoformat();self.plan_path.write_text(json.dumps(self.plan))
        original,first=self.study(name='original')
        comparison=json.loads((self.root/'qualification.json').read_text())
        rows=[decode(json.loads(r)) for r in (self.root/'native.jsonl').read_text().splitlines()]
        for envelope in rows:
            if envelope['native']['ts']>=T+609*H:
                for field in ('open','high','low','close'):envelope['native'][field]+=1000
        for source in comparison['comparisons']:
            filename=source['symbol']+'-1h-2026-05.zip';stream=io.StringIO();writer=csv.writer(stream)
            for envelope in rows:
                if envelope['symbol']!=source['symbol']:continue
                row=envelope['native'];micros=int(row['ts'].timestamp())*1000000
                writer.writerow([micros,row['open'],row['high'],row['low'],row['close'],row['volume'],
                    micros+3600000000-1,row['quote_volume'],row['trade_count'],row['taker_buy_base_volume'],row['taker_buy_quote_volume'],0])
            with zipfile.ZipFile(self.root/filename,'w') as archive:archive.writestr(filename[:-4]+'.csv',stream.getvalue())
            source['sha256']=hashlib.sha256((self.root/filename).read_bytes()).hexdigest()
            (self.root/(filename+'.CHECKSUM')).write_text(source['sha256']+'  '+filename)
        write_native(self.root,comparison,rows);self.prices=self.root/'changed-prices';qualify_price_bars(self.root,self.prices)
        changed,second=self.study(name='changed')
        for fold in ('fold-001','fold-002'):
            a,b=first['selections'][fold],second['selections'][fold]
            self.assertEqual(a['selected_recipe_sha256'],b['selected_recipe_sha256'])
            self.assertEqual([t['score'] for t in a['trials']],[t['score'] for t in b['trials']])
        a=decode(json.loads((original/first['continuous']['artifacts']/'run/result.json').read_text()))
        b=decode(json.loads((changed/second['continuous']['artifacts']/'run/result.json').read_text()))
        self.assertEqual([f for f in a['fills'] if f['time']<T+609*H],[f for f in b['fills'] if f['time']<T+609*H])
        self.assertNotEqual(a['equity'][-1]['equity'],b['equity'][-1]['equity'])
