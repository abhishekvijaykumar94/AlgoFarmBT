"""The same momentum formula has an explicit observed-hourly startup policy."""
import json,unittest
from pathlib import Path
from datetime import datetime,timedelta,timezone
from algoLibs.backtest import SpotBar
from algoFarmBT.strategies.momentum_strategy import MomentumStrategy,load_recipe
import tempfile

T=datetime(2026,1,1,tzinfo=timezone.utc);H=timedelta(hours=1)


class ObservedMomentumTests(unittest.TestCase):
    def test_later_asset_stays_cash_and_a_source_gap_restarts_only_affected_features(self):
        class Source:
            availability_policy='observed_hourly_cash_for_unready'
            def __iter__(self):
                for hour in range(520):
                    frame=[]
                    if hour!=515:frame.append(SpotBar('BTC',T+hour*H,T+(hour+1)*H,str(100+hour),str(105+hour)))
                    if hour>=512:frame.append(SpotBar('SOL',T+hour*H,T+(hour+1)*H,'10','11'))
                    yield frame
        with tempfile.TemporaryDirectory() as directory:
            raw=json.loads(Path(__file__).parents[1].joinpath('examples/momentum.baseline.json').read_text())
            raw.update(warmup_bars=512,symbol_budgets={'BTC':'.5','SOL':'.5'},weight_caps={'BTC':'.5','SOL':'.5'})
            path=Path(directory)/'recipe.json';path.write_text(json.dumps(raw));points=[]
            strategy=MomentumStrategy(load_recipe(path),trace=points.append)
            targets=list(strategy.prepare_targets(Source(),T+512*H,T+520*H))
        self.assertEqual(targets[0].time,T+512*H)
        self.assertTrue(all(set(dict(t.weights))=={'BTC','SOL'} for t in targets))
        self.assertTrue(all(dict(t.weights)['SOL']==0 for t in targets))
        self.assertTrue(next(p for p in points if p.symbol=='BTC' and p.time==T+515*H).warmup_complete)
        self.assertFalse(next(p for p in points if p.symbol=='BTC' and p.time==T+517*H).warmup_complete)
        self.assertEqual(dict(next(t for t in targets if t.time==T+516*H).weights)['BTC'],0)
