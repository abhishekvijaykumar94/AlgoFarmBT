"""Known calculation oracles and causal invariants, without a selected user recipe."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext, getcontext
import unittest

from algoLibs.backtest import Backtest, SpotBar
from algoFarmBT.strategies.momentum_strategy import AuxiliaryObservation, AuxiliaryRule, MomentumRecipe, MomentumStrategy

D = Decimal
T = datetime(2026, 5, 1, tzinfo=timezone.utc)
H = timedelta(hours=1)


def generate_targets(source, selected, start, end, auxiliary=(), trace=None):
    return MomentumStrategy(selected, auxiliary, trace).prepare_targets(source,start,end)


def recipe(**changes):
    config = dict(name='synthetic-calculation-oracle-v1', histogram='ewmac', signal_span=None,
        smoothing_span=1, ema_initialisation='recursive_first_observation', return_kind='simple',
        rv_estimator='ewma_zero_mean_variance', rv_blend='variance', rv_fast_weight='.5',
        annualisation_hours=1, annual_volatility_floor='.1', normalisation='price_hourly_volatility',
        iv_mode='disabled_ablation', regime_mode='disabled_ablation', breaker_mode='disabled_ablation',
        auxiliary_rules=(), warmup_bars=3, symbol_budgets={'BTCUSDT':'1'}, weight_caps={'BTCUSDT':'1'},
        allocation_policy='long_only_budget_capped_no_redistribution', rebalance=True,
        assumptions=('Synthetic values exercise calculations; not the user strategy',),
        horizons=((1, 2, '1'),), rv_fast_span=1, rv_slow_span=2, vol_target='.01')
    config.update(changes)
    for field in ('symbol_budgets','weight_caps'):
        if hasattr(config[field], 'items'):
            config[field] = tuple(config[field].items())
    return MomentumRecipe(**config)


def bars(prices=('100','110','121','133.1','146.41'), symbols=('BTCUSDT',)):
    return [SpotBar(symbol, T+i*H, T+(i+1)*H, price, price)
            for i, price in enumerate(prices) for symbol in symbols]


def observation(time, value, field='iv', available=None, symbol='BTCUSDT'):
    return AuxiliaryObservation(symbol,field,time,available or time,value,
        'annual_decimal_volatility' if field=='iv' else 'dimensionless','synthetic-'+field)


class MomentumTests(unittest.TestCase):
    def calculate(self, selected=None, data=None, auxiliary=()):
        points=[]
        targets=list(generate_targets([data or bars()], selected or recipe(), T+3*H,T+5*H,auxiliary,points.append))
        return targets,points

    def test_independent_fraction_oracle_for_ema_normalisation_and_sizing(self):
        with localcontext() as context:
            context.prec=50
            targets,points=self.calculate()
            self.assertEqual(len(targets),2)
            self.assertEqual(targets[0].time,T+3*H)
            self.assertLess(abs(points[2].forecast-D(430)/1089),D('1e-45'))
            self.assertEqual(dict(targets[0].weights)['BTCUSDT'],D('0.039485766758494031221303'))
            self.assertEqual(points[2].hourly_volatility,D('.1'))
            self.assertTrue(all(p.target_weight==0 for p in points[:2]))

    def test_variance_and_volatility_blends_are_explicitly_different(self):
        data=bars(('100','110','99','118.8','118.8','118.8'))
        for blend in ('variance','volatility'):
            points=[]
            r=recipe(rv_slow_span=3,rv_fast_weight='.25',rv_blend=blend,warmup_bars=4,annual_volatility_floor='.0001')
            list(generate_targets([data],r,T+4*H,T+6*H,trace=points.append))
            with localcontext() as context:
                context.prec=50
                expected=D('.02875').sqrt() if blend=='variance' else D('.25')*D('.2')+D('.75')*D('.025').sqrt()
                self.assertLess(abs(points[3].hourly_volatility-expected),D('1e-45'))

    def test_histogram_signal_ema_and_clip_before_smoothing(self):
        data=bars(('100','200','200','200','200'))
        _,points=self.calculate(recipe(rv_slow_span=1,smoothing_span=3,annual_volatility_floor='.001'),data)
        # t2 EWMAC = 100/3, normalised = 1/6, smoothed = 1/12.
        # t3 normalised exceeds 2, clip to 2, then smooth: 25/24.
        self.assertAlmostEqual(float(points[2].forecast),25/24,places=13)
        _,hist=self.calculate(recipe(histogram='macd_histogram',signal_span=1),data)
        self.assertTrue(all(p.forecast==0 for p in hist))

    def test_log_returns_and_annual_units_are_configured(self):
        data=bars(('100','200','400','800','1600'))
        _,points=self.calculate(recipe(return_kind='log',annualisation_hours=4),data)
        self.assertAlmostEqual(float(points[2].hourly_volatility),__import__('math').log(2),places=13)
        with localcontext() as context:
            context.prec=50
            self.assertEqual(points[2].annual_volatility,points[2].hourly_volatility*2)
        _,other=self.calculate(recipe(return_kind='log',annualisation_hours=4,normalisation='price_annual_volatility'),data)
        self.assertAlmostEqual(float(other[2].forecast),float(points[2].forecast/2),places=13)

    def test_future_prices_and_auxiliary_cannot_change_prefix(self):
        data=bars();changed=[replace(b,close='99999') if b.start>=T+3*H else b for b in data]
        a,_=self.calculate(data=data);b,_=self.calculate(data=changed)
        self.assertEqual(a[0],b[0])
        self.assertNotEqual(a[1],b[1])
        r=recipe(iv_mode='iv_over_realised',auxiliary_rules=(AuxiliaryRule('iv','synthetic-iv',99999),))
        aux=[observation(T+H,'.2'),observation(T+4*H,'.3')]
        a,_=self.calculate(r,auxiliary=aux)
        b,_=self.calculate(r,auxiliary=aux[:1]+[observation(T+4*H,'20')])
        self.assertEqual(a[0],b[0])

    def test_delayed_auxiliary_is_joined_by_availability_and_stale_fails(self):
        r=recipe(iv_mode='iv_over_realised',auxiliary_rules=(AuxiliaryRule('iv','synthetic-iv',99999),))
        aux=[observation(T+H,'.1'),observation(T+2*H,'.2',available=T+4*H)]
        _,points=self.calculate(r,auxiliary=aux)
        self.assertEqual(points[2].iv_multiplier,D('1'))
        self.assertEqual(points[3].iv_multiplier,D('2'))
        with self.assertRaisesRegex(ValueError,'stale'):
            self.calculate(replace(r,auxiliary_rules=(AuxiliaryRule('iv','synthetic-iv',0),)),auxiliary=aux[:1])
        with self.assertRaisesRegex(ValueError,'Missing'):
            self.calculate(r)

    def test_late_old_observation_does_not_replace_newer_event(self):
        r=recipe(iv_mode='iv_over_realised',auxiliary_rules=(AuxiliaryRule('iv','synthetic-iv',99999),))
        aux=[observation(T+H,'.1'),observation(T+3*H,'.2'),observation(T+2*H,'.3',available=T+4*H)]
        _,points=self.calculate(r,auxiliary=aux)
        self.assertEqual(points[3].iv_multiplier,D('2'))

    def test_breaker_and_negative_forecasts_produce_cash_targets(self):
        r=recipe(breaker_mode='supplied_multiplier',auxiliary_rules=(AuxiliaryRule('breaker','synthetic-breaker',99999),))
        target,_=self.calculate(r,auxiliary=[observation(T+H,'0',field='breaker')])
        self.assertTrue(all(dict(t.weights)['BTCUSDT']==0 for t in target))
        targets,points=self.calculate(data=bars(('100','90','81','72.9','65.61')))
        self.assertTrue(points[-1].forecast<0)
        self.assertTrue(all(dict(t.weights)['BTCUSDT']==0 for t in targets))

    def test_chunking_and_decimal_context_do_not_change_targets(self):
        r=recipe();data=bars();a=list(generate_targets([data],r,T+3*H,T+5*H))
        with localcontext() as context:
            context.prec=6
            b=list(generate_targets([[b] for b in data],r,T+3*H,T+5*H))
        self.assertEqual(a,b)

    def test_streaming_yields_do_not_change_the_callers_decimal_context(self):
        r=recipe();data=bars()
        with localcontext() as context:
            context.prec=7
            generator=generate_targets([data],r,T+3*H,T+5*H)
            next(generator)
            self.assertEqual(getcontext().prec,7)
            generator.close()
            self.assertEqual(getcontext().prec,7)

    def test_controller_uses_the_project_strategy_hooks_and_existing_backtest(self):
        from unittest.mock import patch
        from algoLibs.backtest import SpotRules,SpotRunSpec
        from algoLibs.portfolio import BacktestPortfolio
        from algoLibs.strategies import CoreTradingStrategy
        selected=recipe();strategy=MomentumStrategy(selected)
        run=SpotRunSpec(T+3*H,T+5*H,H,D(1000),D('.01'),'USDT',
                        (SpotRules('BTCUSDT','.000001','.01','0','0','1','0'),),
                        'synthetic','test',('Synthetic framework integration',),'research_bar_next_open_v1')
        with patch.object(strategy,'apply_indicators',wraps=strategy.apply_indicators) as indicators, \
             patch.object(strategy,'generate_signal',wraps=strategy.generate_signal) as signals:
            bt=Backtest.from_prepared(run,[bars()],strategy=strategy)
            result=bt.run()
        self.assertIsInstance(strategy,CoreTradingStrategy)
        self.assertIsInstance(bt.portfolio,BacktestPortfolio)
        self.assertEqual(indicators.call_count,4)
        self.assertEqual(signals.call_count,2)
        self.assertEqual(len(bt.target_log),2)
        self.assertTrue(result.fills)

    def test_warmup_gaps_and_bad_configuration_fail(self):
        for data,start in ((bars(),T+2*H),(bars()[1:],T+3*H),(bars()[0:1]+bars()[2:],T+3*H)):
            with self.subTest(start=start),self.assertRaises(ValueError):
                list(generate_targets([data],recipe(),start,T+5*H))
        with self.assertRaises(ValueError):recipe(rv_fast_weight='1.1')
        with self.assertRaises(ValueError):recipe(iv_mode='iv_over_realised')
        with self.assertRaises(ValueError):recipe(annual_volatility_floor='0')
        with self.assertRaises(ValueError):recipe(horizons=((1,2,'.5'),))


if __name__=='__main__':unittest.main()
