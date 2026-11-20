"""Liquidity qualification through the public command and pinned artifacts."""
from datetime import timedelta
import json
from pathlib import Path
import tempfile
import unittest
from algoFarmBT.backtests.execute_momentum_backtest import main
from algoFarmBT.spot_inputs import qualify_price_bars
from algoLibs.market_data.types import decode
from test_spot_inputs import make_inputs, T, H

class LiquidityInputTests(unittest.TestCase):
    def test_cli_qualifies_volume_separately_and_declares_assumed_availability(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); make_inputs(root)
            prices=qualify_price_bars(root,root/'prices')
            main(['qualify-liquidity','--input',str(root),'--snapshot',str(root/'prices'),
                  '--availability-lag-seconds','2','--output',str(root/'liquidity')])
            manifest=decode(json.loads((root/'liquidity/manifest.json').read_text()))
            self.assertEqual(manifest['price_data_id'],prices.data_id)
            self.assertEqual(manifest['units']['base_volume'],'base_asset')
            self.assertEqual(manifest['availability']['status'],'assumed_not_capture_verified')
            self.assertEqual(manifest['entries'][0]['rows'],744)
            row=decode(json.loads((root/'liquidity/BTCUSDT.jsonl').read_text().splitlines()[0]))
            self.assertEqual(row['base_volume'],10)
            self.assertEqual(row['available_at'],T+H+timedelta(seconds=2))

    def test_cli_constrained_run_uses_pinned_liquidity_and_retains_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); make_inputs(root)
            qualify_price_bars(root,root/'prices')
            main(['qualify-liquidity','--input',str(root),'--snapshot',str(root/'prices'),
                  '--availability-lag-seconds','2','--output',str(root/'liquidity')])
            config=dict(start=(T+2*H).isoformat(),end=(T+4*H).isoformat(),interval_seconds=3600,
                initial_cash='1000',cash_quantum='.01',quote_currency='USDT',execution_profile='research_lagged_volume_v1',
                assumptions=['Synthetic rules'],rules=[dict(symbol=s,lot_size='.001',tick_size='.01',min_notional='0',
                fee_rate='0',fixed_fee='1',slippage_bps='0') for s in ('BTCUSDT','ETHUSDT','SOLUSDT')])
            (root/'spec.json').write_text(json.dumps(config))
            (root/'targets.json').write_text(json.dumps([dict(time=(T+2*H).isoformat(),weights={'BTCUSDT':'.5'})]))
            (root/'policy.json').write_text(json.dumps(dict(max_participation='.1',impact_coefficient_bps='0',max_age_seconds=7200)))
            main(['run','--snapshot',str(root/'prices'),'--spec',str(root/'spec.json'),'--targets',str(root/'targets.json'),
                  '--liquidity',str(root/'liquidity'),'--liquidity-policy',str(root/'policy.json'),'--output',str(root/'run')])
            result=decode(json.loads((root/'run/result.json').read_text()))
            self.assertEqual(result['fills'][0]['quantity'],1)
            self.assertEqual(result['final_cash'],899)
            research=decode(json.loads((root/'run/research.json').read_text()))
            section=research['sections']['liquidity']
            self.assertEqual(section['status'],'scenario_only_not_certified')
            self.assertEqual(section['orders'][0]['reference_end'],T+H)
            self.assertEqual(section['summary']['shortfall_notional'],400)

    def test_capacity_cli_reruns_capitals_and_exports_native_comparison(self):
        from openpyxl import load_workbook
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);make_inputs(root,trend=True);qualify_price_bars(root,root/'prices')
            from algoFarmBT.liquidity_inputs import qualify_liquidity
            qualify_liquidity(root,root/'prices',root/'liquidity',availability_lag_seconds=2)
            recipe=json.loads((Path(__file__).resolve().parents[1]/'examples/momentum.baseline.json').read_text())
            recipe.update(warmup_bars=600,histogram='ewmac',signal_span=None)
            (root/'recipe.json').write_text(json.dumps(recipe))
            config=dict(start=(T+600*H).isoformat(),end=(T+604*H).isoformat(),interval_seconds=3600,
                initial_cash='1000',cash_quantum='.01',quote_currency='USDT',execution_profile='research_bar_next_open_v1',
                assumptions=['Synthetic rules'],rules=[dict(symbol=s,lot_size='.001',tick_size='.01',min_notional='0',
                fee_rate='.001',fixed_fee='1',slippage_bps='5') for s in ('BTCUSDT','ETHUSDT','SOLUSDT')])
            (root/'spec.json').write_text(json.dumps(config))
            plan=dict(schema='momentum-capacity-v1',name='fixture-capitals',hypothesis='Caps change actual fills',
                policy=dict(max_participation='.01',impact_coefficient_bps='10',max_age_seconds=7200),
                cases=[dict(id='baseline',initial_cash='1000',constrained=False),
                       dict(id='constrained-small',initial_cash='1000',constrained=True),
                       dict(id='constrained-large',initial_cash='100000',constrained=True),
                       dict(id='invalid-precision',initial_cash='1000.005',constrained=True)])
            (root/'plan.json').write_text(json.dumps(plan))
            args=['capacity','--snapshot',str(root/'prices'),'--liquidity',str(root/'liquidity'),'--spec',str(root/'spec.json'),
                  '--recipe',str(root/'recipe.json'),'--plan',str(root/'plan.json'),'--output',str(root/'capacity')]
            main(args)
            comparison=decode(json.loads((root/'capacity/comparison.json').read_text()))
            self.assertEqual([r['status'] for r in comparison['cases']],['succeeded']*3+['failed'])
            self.assertTrue((root/'capacity/cases/invalid-precision/failure.json').exists())
            self.assertEqual(comparison['capacity_status'],'scenario_only_not_certified')
            small,big=comparison['cases'][1:3]
            self.assertGreater(big['metrics']['shortfall_notional'],small['metrics']['shortfall_notional'])
            self.assertNotEqual(big['metrics']['net_pnl'],100*small['metrics']['net_pnl'])
            workbook=load_workbook(root/'capacity/comparison.xlsx')
            self.assertIn('Liquidity',workbook.sheetnames)
            self.assertEqual(workbook['Summary']['A2'].value,'baseline')
            workbook.close()
            args[-1]=str(root/'replay');main(args)
            self.assertEqual((root/'capacity/manifest.sha256').read_bytes(),(root/'replay/manifest.sha256').read_bytes())

    def test_mismatched_quote_volume_stays_unavailable_without_discarding_verified_base(self):
        from algoFarmBT.liquidity_inputs import qualify_liquidity,load_liquidity
        from test_spot_inputs import write_native
        from decimal import Decimal
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);report,rows=make_inputs(root)
            rows[0]['native']['quote_volume']+=Decimal('.00000001');write_native(root,report,rows)
            prices=qualify_price_bars(root,root/'prices')
            qualify_liquidity(root,root/'prices',root/'liquidity',availability_lag_seconds=2)
            context=load_liquidity(root/'liquidity',prices,dict(max_participation='.1',impact_coefficient_bps='0',max_age_seconds=7200))
            row=context.realised('BTCUSDT',T)
            self.assertIsNone(row.quote_volume);self.assertEqual(row.base_volume,10)
            self.assertEqual(context.evidence['diagnostics']['BTCUSDT:quote_volume_mismatch'],1)

    def test_native_anchor_and_export_hash_tampering_are_rejected(self):
        from algoFarmBT.liquidity_inputs import qualify_liquidity,load_liquidity
        from test_spot_inputs import write_native
        from decimal import Decimal
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);report,rows=make_inputs(root);prices=qualify_price_bars(root,root/'prices')
            qualify_liquidity(root,root/'prices',root/'liquidity',availability_lag_seconds=2)
            path=root/'liquidity/BTCUSDT.jsonl';path.write_text(path.read_text().replace('10','11',1))
            with self.assertRaisesRegex(ValueError,'hash'):
                load_liquidity(root/'liquidity',prices,dict(max_participation='.1',impact_coefficient_bps='0',max_age_seconds=7200))
            rows[0]['native']['volume']+=Decimal('1');write_native(root,report,rows)
            with self.assertRaisesRegex(ValueError,'checksum'):
                qualify_liquidity(root,root/'prices',root/'invalid',availability_lag_seconds=2)
            self.assertFalse((root/'invalid').exists())

    def test_resigned_identity_units_and_early_availability_are_semantically_rejected(self):
        from algoFarmBT.liquidity_inputs import qualify_liquidity,load_liquidity
        from algoLibs.market_data.types import encode
        import hashlib
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);make_inputs(root);prices=qualify_price_bars(root,root/'prices')
            qualify_liquidity(root,root/'prices',root/'liquidity',availability_lag_seconds=2)
            manifest=root/'liquidity/manifest.json';original=manifest.read_bytes()
            data=root/'liquidity/BTCUSDT.jsonl';original_data=data.read_text()
            policy=dict(max_participation='.1',impact_coefficient_bps='0',max_age_seconds=7200)
            def resign(m):
                manifest.write_text(json.dumps(encode(m),sort_keys=True))
                (root/'liquidity/manifest.sha256').write_text(hashlib.sha256(manifest.read_bytes()).hexdigest())
            m=decode(json.loads(original));m['units']['base_volume']='quote_asset';resign(m)
            with self.assertRaisesRegex(ValueError,'units'):load_liquidity(root/'liquidity',prices,policy)
            for key,value in [('instrument_id',999),('available_at',T)]:
                lines=original_data.splitlines();row=decode(json.loads(lines[0]));row[key]=value
                lines[0]=json.dumps(encode(row));data.write_text('\n'.join(lines)+'\n')
                m=decode(json.loads(original));m['entries'][0]['sha256']=hashlib.sha256(data.read_bytes()).hexdigest();resign(m)
                with self.assertRaisesRegex(ValueError,'identity|availability'):load_liquidity(root/'liquidity',prices,policy)

    def test_capacity_plan_rejects_unsafe_or_implicit_cases(self):
        from algoFarmBT.backtests.capacity import load_plan
        base=json.loads((Path(__file__).resolve().parents[1]/'examples/momentum.capacity.json').read_text())
        for change in ('unsafe','duplicate','baseline','float','unknown'):
            plan=json.loads(json.dumps(base))
            if change=='unsafe':plan['cases'][1]['id']='../escape'
            elif change=='duplicate':plan['cases'][1]['id']='baseline'
            elif change=='baseline':plan['cases'][0]['constrained']=True
            elif change=='float':plan['cases'][1]['initial_cash']=1000.5
            else:plan['policy']['unknown']=1
            with self.assertRaises((ValueError,TypeError)):load_plan(json.dumps(plan).encode())

    def test_usdt_liquidity_profile_cannot_mislabel_other_quote_assets(self):
        from algoFarmBT.liquidity_inputs import qualify_liquidity
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);make_inputs(root,symbols=('BTCUSDC','ETHUSDT','SOLUSDT'))
            qualify_price_bars(root,root/'prices')
            with self.assertRaisesRegex(ValueError,'USDT'):
                qualify_liquidity(root,root/'prices',root/'liquidity',availability_lag_seconds=2)
            self.assertFalse((root/'liquidity').exists())
