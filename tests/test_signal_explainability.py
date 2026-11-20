"""Owning-stage arithmetic, exact binding flags and instrumentation invariance."""
from dataclasses import asdict, replace
from decimal import Decimal, localcontext
import unittest

from algoLibs.backtest import Backtest, SpotRules, SpotRunSpec
from algoLibs.backtest.signal_analytics import build_signal_analytics
from algoLibs.market_data.types import decode, encode
from algoFarmBT.strategies.momentum_strategy import HORIZONS, MomentumStrategy
from test_momentum import recipe, bars, T, H

D=Decimal


class SignalExplainabilityTests(unittest.TestCase):
    def capture(self, selected=None, data=None):
        points=[]
        targets=list(MomentumStrategy(selected or recipe(),trace=points.append).prepare_targets(
            [data or bars()],T+3*H,T+5*H))
        return targets,points

    def test_raw_normalised_clipped_smoothed_weighted_independent_oracle(self):
        _,points=self.capture()
        h=points[2].horizons[0]
        with localcontext() as context:
            context.prec=50
            self.assertLess(abs(h.raw-D(43)/9),D('1e-45'))
            self.assertLess(abs(h.normalised-D(430)/1089),D('1e-45'))
        self.assertEqual(h.clipped,h.normalised)
        self.assertEqual(h.smoothed,h.clipped)
        self.assertEqual(h.weighted,points[2].forecast)
        self.assertFalse(h.clip_low_binding)
        self.assertFalse(h.clip_high_binding)
        self.assertTrue(points[0].volatility_floor_binding)
        self.assertFalse(points[2].volatility_floor_binding) # exactly .1 equals floor, strict flag
        self.assertTrue(points[0].warmup_binding)
        self.assertFalse(points[2].warmup_binding)
        self.assertTrue(points[2].weight_precision_binding)

    def test_equal_clip_and_cap_are_not_binding_and_exceeded_clip_is(self):
        selected=recipe(horizons=((1,3,'1'),),rv_slow_span=1,forecast_clip='.25',vol_target='1',
                        weight_caps={'BTCUSDT':'.25'})
        _,points=self.capture(selected,bars(('100','200','400','800','1600')))
        self.assertEqual(points[1].horizons[0].normalised,D('.25'))
        self.assertFalse(points[1].horizons[0].clip_high_binding)
        self.assertEqual(points[1].uncapped_weight,D('.25'))
        self.assertFalse(points[1].cap_binding)
        self.assertTrue(points[2].horizons[0].clip_high_binding)
        self.assertEqual(points[2].horizons[0].clipped,D('.25'))
        self.assertFalse(points[2].cap_binding)

    def test_cap_coincident_budget_and_long_only_flags(self):
        _,points=self.capture(recipe(vol_target='100',weight_caps={'BTCUSDT':'.02'}))
        self.assertTrue(points[2].cap_binding)
        self.assertFalse(points[2].budget_binding)
        self.assertEqual(points[2].target_weight,D('.02'))
        _,points=self.capture(recipe(vol_target='100',symbol_budgets={'BTCUSDT':'.02'},
                                   weight_caps={'BTCUSDT':'.02'}))
        self.assertTrue(points[2].cap_binding)
        self.assertTrue(points[2].budget_binding)
        _,negative=self.capture(recipe(forecast_clip='.1'),bars(('100','90','81','72.9','65.61')))
        self.assertTrue(negative[2].forecast_long_only_binding)
        self.assertTrue(negative[2].horizons[0].clip_low_binding)
        self.assertEqual(negative[2].target_weight,D(0))

    def test_clip_precedes_recursive_smoothing(self):
        _,points=self.capture(recipe(rv_slow_span=1,smoothing_span=3,annual_volatility_floor='.001'),
                              bars(('100','200','200','200','200')))
        h=points[2].horizons[0]
        self.assertGreater(h.normalised,D(2))
        self.assertEqual(h.clipped,D(2))
        self.assertAlmostEqual(float(h.smoothed),25/24,places=13)
        self.assertEqual(h.weighted,h.smoothed)

    def test_recording_changes_neither_targets_nor_fills_cash_and_equity(self):
        run=SpotRunSpec(T+3*H,T+5*H,H,'1000','.01','USDT',
            (SpotRules('BTCUSDT','.000001','.01','0','.001','1','5'),),
            'fixture','code',(),'research_bar_next_open_v1')
        plain=Backtest.from_prepared(run,[bars()],strategy=MomentumStrategy(recipe()))
        a=plain.run()
        points=[]
        traced=Backtest.from_prepared(run,[bars()],strategy=MomentumStrategy(recipe(),trace=points.append),
                                      retain_diagnostics=True)
        b=traced.run()
        self.assertEqual(a,b)
        self.assertEqual(plain.target_log,traced.target_log)
        self.assertIsNone(plain.execution_audit)
        self.assertTrue(traced.execution_audit)
        # No application dataclass import is needed by analyzer; saved-input
        # replay uses decoded records and obtains exactly the same payload.
        first=traced.signal_analytics(points)
        restored=decode(encode([asdict(p) for p in points]))
        replay=build_signal_analytics(b,[bars()],dict(points=restored,execution=traced.execution_audit),traced.target_log)
        self.assertEqual(first,replay)
        self.assertEqual(first['coverage']['retained_asset_decisions'],2)
        self.assertEqual(first['coverage']['excluded_warmup_records'],2)
        self.assertTrue(first['coverage']['complete'])

    def test_future_prices_cannot_change_earlier_trace_or_targets(self):
        data=bars();changed=[replace(b,close='99999') if b.start>=T+3*H else b for b in data]
        a,pa=self.capture(data=data);b,pb=self.capture(data=changed)
        self.assertEqual(a[0],b[0])
        self.assertEqual(pa[:3],pb[:3])
        self.assertNotEqual(pa[3],pb[3])
        with localcontext() as context:
            context.prec=6
            chunk_points=[]
            chunk_targets=list(MomentumStrategy(recipe(),trace=chunk_points.append).prepare_targets(
                [[bar] for bar in data],T+3*H,T+5*H))
        self.assertEqual(a,chunk_targets)
        self.assertEqual(pa,chunk_points)

    def test_six_horizon_contributions_sum_to_composite(self):
        prices=tuple(str(100+i) for i in range(516))
        selected=recipe(horizons=HORIZONS,warmup_bars=512)
        points=[]
        list(MomentumStrategy(selected,trace=points.append).prepare_targets([bars(prices)],T+512*H,T+516*H))
        with localcontext() as context:
            context.prec=50
            for p in points:
                self.assertEqual(sum((h.weighted for h in p.horizons),D(0)),p.forecast)
                self.assertEqual(tuple(h.smoothed for h in p.horizons),p.horizon_forecasts)


if __name__=='__main__':unittest.main()
