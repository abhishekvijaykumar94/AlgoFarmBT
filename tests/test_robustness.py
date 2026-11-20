"""Saved-input case reruns, causal ablations, failure retention and replay."""
from datetime import timedelta
from decimal import Decimal, localcontext
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from openpyxl import load_workbook
from algoLibs.market_data.types import decode
from algoFarmBT.backtests.robustness import run_robustness, load_plan
from algoFarmBT.spot_inputs import qualify_price_bars, QualifiedSpotSource
from algoFarmBT.strategies.momentum_strategy import load_recipe, MomentumStrategy
from test_spot_inputs import make_inputs, T, H


def plan():
    return dict(schema='momentum-robustness-v1',name='fixture-v1',hypothesis='Declared costs, delays and exclusions change results',
                asset_budget_policy='retain_original_budgets_cash',horizon_weight_policy='retain_original_weights',
                cases=[dict(id='baseline'),dict(id='zero-cost',fee_multiplier='0',slippage_multiplier='0'),
                       dict(id='double-cost',fee_multiplier='2',slippage_multiplier='2'),
                       dict(id='delay-two',execution_delay_bars=2),dict(id='without-btc',excluded_symbols=['BTCUSDT']),
                       dict(id='without-fast',excluded_horizons=[[4,16]]),
                       dict(id='invalid-cost',fee_multiplier='100')])


def load(path):return decode(json.loads(Path(path).read_text()))


class RobustnessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();cls.root=Path(cls.temp.name)
        inputs=cls.root/'inputs';inputs.mkdir();make_inputs(inputs,trend=True)
        cls.snapshot=cls.root/'prices';qualify_price_bars(inputs,cls.snapshot)
        example=Path(__file__).resolve().parents[1]/'examples/momentum.baseline.json'
        recipe=json.loads(example.read_text());recipe.update(warmup_bars=600,histogram='ewmac',signal_span=None)
        cls.recipe=cls.root/'recipe.json';cls.recipe.write_text(json.dumps(recipe))
        config=dict(start=(T+600*H).isoformat(),end=(T+608*H).isoformat(),interval_seconds=3600,
            initial_cash='1000',cash_quantum='.01',quote_currency='USDT',execution_profile='research_bar_next_open_v1',
            assumptions=['Synthetic fixture'],rules=[dict(symbol=s,lot_size='.01',tick_size='.01',min_notional='0',
                fee_rate='.02',fixed_fee='1',slippage_bps='5') for s in ('BTCUSDT','ETHUSDT','SOLUSDT')])
        cls.config=cls.root/'spec.json';cls.config.write_text(json.dumps(config))
        cls.cases=cls.root/'plan.json';cls.cases.write_text(json.dumps(plan()))
        cls.output=cls.root/'comparison'
        cls.result=run_robustness(cls.snapshot,cls.config,cls.recipe,cls.cases,cls.output)

    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()

    def case(self,name):return next(r for r in self.result['cases'] if r['case_id']==name)

    def test_real_zero_cost_sizing_and_fixed_fill_diagnostic_are_distinct(self):
        baseline=load(self.output/'cases/baseline/run/result.json')
        zero=load(self.output/'cases/zero-cost/run/result.json')
        self.assertEqual(zero['total_fees'],0)
        self.assertNotEqual(baseline['fills'][0]['quantity'],zero['fills'][0]['quantity'])
        self.assertNotEqual(zero['equity'][-1]['equity'],baseline['equity'][-1]['equity']+baseline['total_fees'])
        self.assertIn('endpoint reruns',self.result['rerun_cost_brackets']['convention'])
        self.assertTrue(self.result['rerun_cost_brackets']['brackets'])
        for row in self.result['cases']:
            if row['status']=='succeeded':
                account=load(self.output/row['artifacts']/'run/result.json')
                self.assertGreaterEqual(account['final_cash'],0)
                self.assertEqual(row['metrics']['net_pnl'],account['equity'][-1]['equity']-Decimal(1000))

    def test_delay_keeps_decision_identity_and_end_censoring(self):
        delayed=self.case('delay-two')
        self.assertEqual(delayed['metrics']['censored_decision_count'],2)
        path=self.output/delayed['artifacts']/'run'
        rows=[decode(json.loads(line)) for line in (path/'target-timing.jsonl').read_text().splitlines()]
        self.assertEqual(len(rows),8)
        self.assertTrue(all(r['execution_time']==r['decision_time']+2*H for r in rows))
        account=load(path/'result.json')
        self.assertGreaterEqual(account['fills'][0]['time'],T+602*H)
        self.assertTrue(all(f['time']<T+608*H for f in account['fills']))

    def test_asset_exclusion_leaves_budget_in_cash(self):
        row=self.case('without-btc');path=self.output/row['artifacts']/'run'
        account=load(path/'result.json')
        self.assertFalse(any(f['symbol']=='BTCUSDT' for f in account['fills']))
        original=json.loads((self.output/'cases/baseline/run/targets.json').read_text())
        excluded=json.loads((path/'targets.json').read_text())
        for a,b in zip(original,excluded):
            self.assertEqual(Decimal(b['weights']['BTCUSDT']),0)
            self.assertEqual(a['weights']['ETHUSDT'],b['weights']['ETHUSDT'])
            self.assertEqual(a['weights']['SOLUSDT'],b['weights']['SOLUSDT'])

    def test_horizon_exclusion_is_applied_before_nonlinear_allocation(self):
        recipe=load_recipe(self.recipe);source=QualifiedSpotSource(self.snapshot)
        points=[]
        strategy=MomentumStrategy(recipe,trace=points.append,excluded_horizons=[(4,16)])
        targets=list(strategy.prepare_targets(source,T+600*H,T+608*H))
        with localcontext() as context:
            context.prec=50
            for point in (p for p in points if p.time>=T+600*H):
                weights=[h for h in point.horizons if (h.fast_span,h.slow_span)==(4,16)]
                self.assertEqual(weights[0].weight,0)
                forecast=sum((h.smoothed*h.weight for h in point.horizons),Decimal(0))
                self.assertEqual(point.forecast,forecast)
                expected=min(point.cap,point.budget*max(Decimal(0),forecast)*recipe.vol_target/point.annual_volatility)
                expected=expected.quantize(Decimal('1e-24'),rounding='ROUND_DOWN')
                self.assertEqual(point.target_weight,expected)
        original=list(strategy.prepare_targets(source,T+600*H,T+608*H))
        class Perturbed:
            def __iter__(self):
                for batch in source:
                    yield [replace(b,close=b.close*2) if b.start>=T+604*H else b for b in batch]
        changed=list(strategy.prepare_targets(Perturbed(),T+600*H,T+608*H))
        self.assertEqual([t for t in original if t.time<=T+604*H],[t for t in changed if t.time<=T+604*H])

    def test_failed_and_losing_cases_retained_and_artifacts_pinned(self):
        bad=self.case('invalid-cost')
        self.assertEqual(bad['status'],'failed')
        self.assertTrue((self.output/bad['artifacts']/'failure.json').exists())
        self.assertEqual(len(self.result['cases']),7)
        self.assertTrue(any(r['metrics'].get('net_pnl',0)<0 for r in self.result['cases']))
        manifest=load(self.output/'manifest.json')
        self.assertNotIn('manifest.json',manifest['files'])
        for name,digest in manifest['files'].items():
            self.assertEqual(hashlib.sha256((self.output/name).read_bytes()).hexdigest(),digest,name)
        self.assertEqual(hashlib.sha256((self.output/'manifest.json').read_bytes()).hexdigest(),(self.output/'manifest.sha256').read_text().strip())

    def test_comparison_workbook_matches_payload_and_links_cases(self):
        workbook=load_workbook(self.output/'comparison.xlsx')
        sheet=workbook['Summary'];headers={c.value:c.column for c in sheet[1]}
        for cells,row in zip(sheet.iter_rows(min_row=2),self.result['cases']):
            self.assertEqual(cells[0].value,row['case_id'])
            if row['status']=='succeeded':
                self.assertAlmostEqual(cells[headers['net_pnl']-1].value,float(row['metrics']['net_pnl']))
                self.assertIn('%',cells[headers['delta_net_return_vs_baseline']-1].number_format)
                self.assertEqual(cells[headers['report']-1].hyperlink.target,row['report'])
            else:self.assertEqual(cells[headers['net_pnl']-1].value,'N/A')
        workbook.close()

    def test_same_frozen_inputs_reproduce_manifest_and_refuse_overwrite(self):
        replay=self.root/'replay'
        with localcontext() as context:
            context.prec=6
            run_robustness(self.snapshot,self.config,self.recipe,self.cases,replay)
        self.assertEqual((replay/'manifest.sha256').read_bytes(),(self.output/'manifest.sha256').read_bytes())
        with self.assertRaises(FileExistsError):run_robustness(self.snapshot,self.config,self.recipe,self.cases,replay)

    def test_invalid_plan_rejected_before_running_or_publishing(self):
        for change in (dict(cases=[dict(id='../escape')]),dict(cases=[dict(id='baseline'),dict(id='baseline')]),
                       dict(cases=[dict(id='baseline',fee_multiplier='2')]),dict(asset_budget_policy='redistribute'),
                       dict(cases=[dict(id='baseline')]*33)):
            bad=dict(plan(),**change);path=self.root/'invalid-plan.json';path.write_text(json.dumps(bad))
            with self.assertRaises(ValueError):load_plan(path)

    def test_invalid_common_spec_is_rejected_before_any_trial_is_attempted(self):
        original=json.loads(self.config.read_text())
        malformed=[dict(original,initial_cash='not-money'),{k:v for k,v in original.items() if k!='start'}]
        for i,raw in enumerate(malformed):
            path=self.root/f'invalid-spec-{i}.json';path.write_text(json.dumps(raw))
            destination=self.root/f'invalid-common-{i}'
            with patch('algoFarmBT.backtests.robustness.run_related_cases',side_effect=AssertionError('trials started')) as execute:
                with self.assertRaises((ValueError,KeyError,TypeError)):
                    run_robustness(self.snapshot,path,self.recipe,self.cases,destination)
                execute.assert_not_called()
            self.assertFalse(destination.exists())


if __name__=='__main__':unittest.main()
