"""Gap recovery through the existing public momentum lifecycle."""
from pathlib import Path
from datetime import datetime,timedelta,timezone
from decimal import Decimal as D,localcontext
import json,tempfile,unittest
from algoFarmBT.strategies.momentum_strategy import load_recipe,MomentumStrategy
from algoLibs.backtest import SpotBar
T=datetime(2020,1,1,tzinfo=timezone.utc);H=timedelta(hours=1)

class GapPolicyTests(unittest.TestCase):
    def test_one_missing_hour_preserves_readiness_and_uses_elapsed_time(self):
        class Source:
            availability_policy='observed_hourly_preserve_state_v1'
            def __iter__(self):
                for i in range(515):
                    if i==512:continue
                    price='100' if i<512 else '121'
                    yield [SpotBar('BTC',T+i*H,T+(i+1)*H,price,price)]
        with tempfile.TemporaryDirectory() as d:
            raw=json.loads(Path(__file__).parents[1].joinpath('examples/momentum.baseline.json').read_text())
            raw.update(warmup_bars=512,symbol_budgets={'BTC':'1'},weight_caps={'BTC':'1'})
            p=Path(d)/'recipe.json';p.write_text(json.dumps(raw));points=[]
            targets=list(MomentumStrategy(load_recipe(p),trace=points.append).prepare_targets(Source(),T+512*H,T+515*H))
        point=next(p for p in points if p.time==T+514*H)
        self.assertTrue(point.warmup_complete)
        # Worked two-hour update after a constant-price history: fast EMA
        # 113.44, slow EMA 104.650519..., signal retains .8**2 of its old state.
        with localcontext() as c:
            c.prec=50
            expected_raw=(D('113.44')-(D(121)-D(21)*D(225)/D(289)))*D('.64')
            expected_r=(D(121)/100).ln()
            expected_variance=D('.5')*((1-(D(71)/73)**2)+(1-(D(335)/337)**2))*expected_r**2/2
            self.assertAlmostEqual(point.horizons[0].raw,expected_raw,places=40)
            self.assertAlmostEqual(point.hourly_volatility**2,expected_variance,places=40)
        self.assertTrue(any(t.time==T+514*H for t in targets))
        self.assertFalse(any(t.time==T+513*H for t in targets))

    def test_qualified_history_counts_observations_and_existing_runner_carries_holdings(self):
        from test_full_history_inputs import observed_fixture,T as origin,H as hour
        from algoFarmBT.backtests.execute_momentum_backtest import run_snapshot
        from algoLibs.market_data.types import decode
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);source=observed_fixture(root,policy='observed_hourly_preserve_state_v1')
            self.assertEqual(source.history_start(origin+5*hour,4),origin)
            spec=root/'spec.json';spec.write_text(json.dumps(dict(start=origin.isoformat(),end=(origin+24*hour).isoformat(),interval_seconds=3600,initial_cash='100',cash_quantum='.01',quote_currency='USDT',assumptions=['Gap policy fixture'],execution_profile='research_bar_next_open_v1',rules=[dict(symbol=s,lot_size='1',tick_size='1',min_notional='0',fee_rate='.01',fixed_fee='0',slippage_bps='0') for s in ('BTCUSDT','SOLUSDT')])))
            targets=root/'targets.json';targets.write_text(json.dumps([dict(time=origin.isoformat(),weights={'BTCUSDT':'.5','SOLUSDT':'0'}),dict(time=(origin+2*hour).isoformat(),weights={'BTCUSDT':'0','SOLUSDT':'0'}),dict(time=(origin+4*hour).isoformat(),weights={'BTCUSDT':'0','SOLUSDT':'.5'})]))
            run_snapshot(root/'prices',spec,targets,root/'run')
            result=decode(json.loads((root/'run/result.json').read_text()))
            self.assertEqual(str(result['equity'][-1]['equity']),'103.50')
            self.assertEqual(str(result['total_fees']),'1.50')

    def test_partial_active_frame_does_not_create_a_liquidation_signal(self):
        class Source:
            availability_policy='observed_hourly_preserve_state_v1'
            def __iter__(self):
                for i in range(515):
                    yield [SpotBar(s,T+i*H,T+(i+1)*H,'100','101') for s in ('BTC','ETH') if s!='BTC' or i!=512]
        with tempfile.TemporaryDirectory() as d:
            raw=json.loads(Path(__file__).parents[1].joinpath('examples/momentum.baseline.json').read_text());raw.update(warmup_bars=512,symbol_budgets={'BTC':'.5','ETH':'.5'},weight_caps={'BTC':'.5','ETH':'.5'})
            p=Path(d)/'recipe.json';p.write_text(json.dumps(raw));targets=list(MomentumStrategy(load_recipe(p)).prepare_targets(Source(),T+512*H,T+515*H))
        self.assertNotIn(T+513*H,[t.time for t in targets])
        self.assertIn(T+514*H,[t.time for t in targets])
