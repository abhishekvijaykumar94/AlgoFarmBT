"""Causal multi-horizon momentum, with all unspecified recipe choices explicit.

This implements configurable calculations, not a claim that one choice of the
missing formulas reproduces the screenshot. No full-sample normalisation or
implicit IV/regime/circuit-breaker substitutes are permitted.
"""
from dataclasses import dataclass, fields, MISSING
from datetime import timedelta
from decimal import Decimal, ROUND_DOWN, localcontext
from itertools import chain, groupby
import json
from pathlib import Path

from algoLibs.backtest.types import SpotTarget, number
from algoLibs.market_data.types import utc
from algoLibs.strategies import CoreTradingStrategy
from algoFarmBT.indicators import ExponentialMovingAverage


HORIZONS = ((4, 16, '.05'), (8, 32, '.25'), (16, 64, '.25'),
            (32, 128, '.25'), (64, 256, '.15'), (128, 512, '.05'))
HOUR = timedelta(hours=1)


def load_recipe(path):
    raw = json.loads(Path(path).read_text())
    known = {f.name for f in fields(MomentumRecipe)}
    required = {f.name for f in fields(MomentumRecipe) if f.default is MISSING and f.default_factory is MISSING}
    missing, extra = required - set(raw), set(raw) - known
    if missing or extra:
        raise ValueError('Recipe choices missing=%s; unknown=%s' % (sorted(missing),sorted(extra)))
    raw['auxiliary_rules'] = tuple(AuxiliaryRule(**r) for r in raw['auxiliary_rules'])
    for field in ('symbol_budgets','weight_caps'):
        if hasattr(raw[field],'items'):
            raw[field] = tuple(raw[field].items())
    return MomentumRecipe(**raw)


def positive_int(value, name):
    if type(value) is not int or value < 1:
        raise ValueError(name + ' must be a positive integer')


@dataclass(frozen=True)
class AuxiliaryRule:
    field: str
    source_id: str
    max_age_seconds: int

    def __post_init__(self):
        if self.field not in ('iv', 'regime', 'breaker') or not isinstance(self.source_id, str) or not self.source_id:
            raise ValueError('Explicit auxiliary field and source identity required')
        if type(self.max_age_seconds) is not int or self.max_age_seconds < 0:
            raise ValueError('Auxiliary maximum age must be nonnegative seconds')


@dataclass(frozen=True)
class AuxiliaryObservation:
    symbol: str
    field: str
    observed_at: object
    available_at: object
    value: Decimal
    units: str
    source_id: str

    def __post_init__(self):
        object.__setattr__(self, 'observed_at', utc(self.observed_at))
        object.__setattr__(self, 'available_at', utc(self.available_at))
        object.__setattr__(self, 'value', number(self.value))
        if self.available_at < self.observed_at:
            raise ValueError('Observation cannot be available before it is observed')
        units = 'annual_decimal_volatility' if self.field == 'iv' else 'dimensionless'
        if self.field not in ('iv', 'regime', 'breaker') or self.units != units:
            raise ValueError('Explicit, compatible auxiliary units required')
        if not self.source_id or not self.symbol:
            raise ValueError('Auxiliary symbol and source identity required')


@dataclass(frozen=True)
class MomentumRecipe:
    name: str
    histogram: str
    signal_span: object
    smoothing_span: int
    ema_initialisation: str
    return_kind: str
    rv_estimator: str
    rv_blend: str
    rv_fast_weight: Decimal
    annualisation_hours: int
    annual_volatility_floor: Decimal
    normalisation: str
    iv_mode: str
    regime_mode: str
    breaker_mode: str
    auxiliary_rules: tuple
    warmup_bars: int
    symbol_budgets: tuple
    weight_caps: tuple
    allocation_policy: str
    rebalance: bool
    assumptions: tuple
    horizons: tuple = HORIZONS
    rv_fast_span: int = 72
    rv_slow_span: int = 336
    vol_target: Decimal = Decimal('.50')
    forecast_clip: Decimal = Decimal('2')

    def __post_init__(self):
        if not isinstance(self.name, str) or not self.name:
            raise ValueError('Versioned recipe name required')
        for field in ('rv_fast_weight', 'annual_volatility_floor', 'vol_target', 'forecast_clip'):
            object.__setattr__(self, field, number(getattr(self, field)))
        object.__setattr__(self, 'horizons', tuple((f, s, number(w)) for f, s, w in self.horizons))
        for field in ('symbol_budgets', 'weight_caps'):
            value = getattr(self, field)
            values = value.items() if hasattr(value, 'items') else value
            object.__setattr__(self, field, tuple(sorted((s, number(w)) for s, w in values)))
        object.__setattr__(self, 'auxiliary_rules', tuple(AuxiliaryRule(**r) if isinstance(r, dict) else r for r in self.auxiliary_rules))
        object.__setattr__(self, 'assumptions', tuple(self.assumptions))
        for field in ('smoothing_span', 'rv_fast_span', 'rv_slow_span', 'annualisation_hours', 'warmup_bars'):
            positive_int(getattr(self, field), field)
        if self.histogram not in ('ewmac', 'macd_histogram'):
            raise ValueError('Specify ewmac or macd_histogram')
        if self.histogram == 'macd_histogram':
            positive_int(self.signal_span, 'signal_span')
        elif self.signal_span is not None:
            raise ValueError('ewmac does not have a signal EMA')
        if self.ema_initialisation != 'recursive_first_observation':
            raise ValueError('Unsupported EMA initialisation')
        if self.return_kind not in ('log', 'simple') or self.rv_estimator not in ('ewma_zero_mean_variance', 'ewma_central_variance'):
            raise ValueError('Unsupported return or volatility estimator')
        if self.rv_blend not in ('variance', 'volatility') or not 0 <= self.rv_fast_weight <= 1:
            raise ValueError('Specify variance/volatility blend and fast weight')
        if min(self.annual_volatility_floor, self.vol_target, self.forecast_clip) <= 0:
            raise ValueError('Volatility floor, target and clip must be positive')
        if self.normalisation not in ('price_hourly_volatility', 'price_annual_volatility'):
            raise ValueError('Specify the histogram normalisation units')
        if self.iv_mode not in ('disabled_ablation', 'iv_over_realised', 'realised_over_iv'):
            raise ValueError('Specify an explicit IV formula or ablation')
        if any(mode not in ('disabled_ablation', 'supplied_multiplier') for mode in (self.regime_mode, self.breaker_mode)):
            raise ValueError('Specify explicit regime and breaker inputs or ablations')
        needed = {f for f, mode in (('iv', self.iv_mode), ('regime', self.regime_mode), ('breaker', self.breaker_mode)) if mode != 'disabled_ablation'}
        if {r.field for r in self.auxiliary_rules} != needed or len(self.auxiliary_rules) != len(needed):
            raise ValueError('Every enabled auxiliary input needs exactly one source/freshness rule')
        if not self.horizons or len({(f, s) for f, s, _ in self.horizons}) != len(self.horizons):
            raise ValueError('Unique momentum horizons required')
        for fast, slow, weight in self.horizons:
            positive_int(fast, 'fast span'); positive_int(slow, 'slow span')
            if fast >= slow or weight <= 0:
                raise ValueError('Fast spans must be shorter than slow spans; weights must be positive')
        with localcontext() as context:
            context.prec = 80
            if sum((w for _, _, w in self.horizons), Decimal(0)) != 1:
                raise ValueError('Horizon weights must sum to one')
            budgets, caps = dict(self.symbol_budgets), dict(self.weight_caps)
            if not budgets or set(budgets) != set(caps) or len(budgets) != len(self.symbol_budgets) or len(caps) != len(self.weight_caps):
                raise ValueError('Unique matching budgets and caps required')
            if any(not 0 < caps[s] <= budgets[s] <= 1 for s in budgets) or sum(budgets.values()) > 1:
                raise ValueError('Cash-spot caps must fit their budgets and the account')
        required = max(max(s for _, s, _ in self.horizons), self.rv_fast_span + 1,
                       self.rv_slow_span + 1, self.smoothing_span, self.signal_span or 1)
        if self.warmup_bars < required:
            raise ValueError('Warmup is shorter than a configured calculation span')
        if self.allocation_policy != 'long_only_budget_capped_no_redistribution' or type(self.rebalance) is not bool:
            raise ValueError('Explicit supported allocation and rebalance policy required')
        if not all(isinstance(v, str) and v for v in self.assumptions):
            raise ValueError('Assumptions must be explicit text entries')


class _AuxiliaryBook:
    def __init__(self, observations, recipe):
        self.iterator = iter(observations)
        self.upcoming = next(self.iterator, None)
        self.latest, self.previous_key = {}, None
        self.rules = {r.field: r for r in recipe.auxiliary_rules}
        self.symbols = dict(recipe.symbol_budgets)

    def advance(self, time):
        while self.upcoming is not None and self.upcoming.available_at <= time:
            observation = self.upcoming
            key = (observation.available_at, observation.symbol, observation.field, observation.observed_at)
            if self.previous_key is not None and key <= self.previous_key:
                raise ValueError('Auxiliary observations must be uniquely sorted by availability, symbol, field, observation time')
            if observation.field not in self.rules or observation.symbol not in self.symbols:
                raise ValueError('Auxiliary observation has no configured consumer')
            if observation.source_id != self.rules[observation.field].source_id:
                raise ValueError('Auxiliary source identity mismatch')
            slot = (observation.symbol, observation.field)
            old = self.latest.get(slot)
            if old is None or (observation.observed_at, observation.available_at) > (old.observed_at, old.available_at):
                self.latest[slot] = observation
            self.previous_key = key
            self.upcoming = next(self.iterator, None)

    def get(self, symbol, field, time):
        value = self.latest.get((symbol, field))
        if value is None or time - value.observed_at > timedelta(seconds=self.rules[field].max_age_seconds):
            raise ValueError('Missing or stale %s input for %s at %s' % (field, symbol, time))
        return value


@dataclass(frozen=True)
class MomentumPoint:
    time: object
    symbol: str
    warmup_complete: bool
    horizon_forecasts: tuple
    forecast: Decimal
    hourly_volatility: Decimal
    annual_volatility: Decimal
    iv_multiplier: Decimal
    regime_multiplier: Decimal
    breaker_multiplier: Decimal
    target_weight: Decimal
    auxiliary: tuple


class _State:
    def __init__(self, recipe):
        self.recipe = recipe
        self.emas = {span: ExponentialMovingAverage(span, adjust=False, min_periods=1) for fast, slow, _ in recipe.horizons for span in (fast, slow)}
        self.signals = [ExponentialMovingAverage(recipe.signal_span, adjust=False, min_periods=1) if recipe.signal_span else None for _ in recipe.horizons]
        self.smoothed = [ExponentialMovingAverage(recipe.smoothing_span, adjust=False, min_periods=1) for _ in recipe.horizons]
        self.means = [ExponentialMovingAverage(recipe.rv_fast_span, adjust=False, min_periods=1), ExponentialMovingAverage(recipe.rv_slow_span, adjust=False, min_periods=1)]
        self.squares = [ExponentialMovingAverage(recipe.rv_fast_span, adjust=False, min_periods=1), ExponentialMovingAverage(recipe.rv_slow_span, adjust=False, min_periods=1)]
        self.previous, self.count = None, 0

    def update(self, bar, book):
        r = self.recipe
        self.count += 1
        values = {s: ema.update(bar.close) for s, ema in self.emas.items()}
        if self.previous is not None:
            ratio = bar.close / self.previous
            ret = ratio.ln() if r.return_kind == 'log' else ratio - 1
            for mean, square in zip(self.means, self.squares):
                mean.update(ret); square.update(ret * ret)
        self.previous = bar.close
        variances = [max(Decimal(0), (square.value or Decimal(0)) -
                         (mean.value ** 2 if r.rv_estimator == 'ewma_central_variance' and mean.value is not None else 0))
                     for mean, square in zip(self.means, self.squares)]
        if r.rv_blend == 'variance':
            vol = (r.rv_fast_weight * variances[0] + (1 - r.rv_fast_weight) * variances[1]).sqrt()
        else:
            vol = r.rv_fast_weight * variances[0].sqrt() + (1 - r.rv_fast_weight) * variances[1].sqrt()
        annualiser = Decimal(r.annualisation_hours).sqrt()
        vol = max(vol, r.annual_volatility_floor / annualiser)
        annual_vol = vol * annualiser
        inputs = tuple(book.get(bar.symbol, field, bar.end) for field in sorted(book.rules))
        by_field = {v.field: v.value for v in inputs}
        iv_multiplier = Decimal(1)
        if r.iv_mode != 'disabled_ablation':
            iv = by_field['iv']
            if iv <= 0:
                raise ValueError('IV must be positive in annual decimal volatility units')
            iv_multiplier = iv / annual_vol if r.iv_mode == 'iv_over_realised' else annual_vol / iv
        regime, breaker = by_field.get('regime', Decimal(1)), by_field.get('breaker', Decimal(1))
        if regime < 0 or not 0 <= breaker <= 1:
            raise ValueError('Regime must be nonnegative and breaker between zero and one')
        denominator = bar.close * (vol if r.normalisation == 'price_hourly_volatility' else annual_vol)
        forecasts = []
        for i, (fast, slow, _) in enumerate(r.horizons):
            macd = values[fast] - values[slow]
            histogram = macd - self.signals[i].update(macd) if self.signals[i] else macd
            normalised = histogram / denominator * iv_multiplier
            clipped = min(r.forecast_clip, max(-r.forecast_clip, normalised))
            forecasts.append(self.smoothed[i].update(clipped))
        forecast = sum((v * w for v, (_, _, w) in zip(forecasts, r.horizons)), Decimal(0))
        budget, cap = dict(r.symbol_budgets)[bar.symbol], dict(r.weight_caps)[bar.symbol]
        weight = min(cap, budget * max(Decimal(0), forecast) * r.vol_target / annual_vol * regime * breaker)
        # Weights use 24 decimal places, rounded down at the simulator
        # interface so numerical rounding cannot exceed a cap.
        weight = weight.quantize(Decimal('1e-24'), rounding=ROUND_DOWN)
        ready = self.count >= r.warmup_bars
        return MomentumPoint(bar.end, bar.symbol, ready, tuple(forecasts), forecast, vol, annual_vol,
                             iv_multiplier, regime, breaker, weight if ready else Decimal(0), inputs)


class MomentumStrategy(CoreTradingStrategy):
    """Prepared-bar target strategy using the project's strategy lifecycle."""
    def __init__(self, recipe, auxiliary=(), trace=None):
        super().__init__(recipe.name, True, recipe.warmup_bars)
        self.recipe = recipe
        self.auxiliary = auxiliary
        self.trace = trace

    def apply_indicators(self, frame):
        """Prepare causal feature records for one complete bar frame."""
        self._book.advance(frame[0].end)
        return [self._states[b.symbol].update(b,self._book) for b in sorted(frame,key=lambda b:b.symbol)]

    def generate_signal(self, points):
        """The bulk research interface emits desired weights, not order messages."""
        return [SpotTarget(points[0].time,{p.symbol:p.target_weight for p in points},self.recipe.rebalance)]

    def prepare_targets(self, source, start, end):
        """Emit targets at completed-bar ends, retaining recursive state across chunks."""
        recipe, auxiliary, trace = self.recipe, self.auxiliary, self.trace
        start, end = utc(start), utc(end)
        if start >= end or (end - start) % HOUR:
            raise ValueError('Momentum run needs complete hourly intervals')
        with localcontext() as context:
            context.prec = 50
            self._states = states = {symbol: _State(recipe) for symbol, _ in recipe.symbol_budgets}
            self._book = book = _AuxiliaryBook(auxiliary, recipe)
        previous, began = None, False
        for timestamp, rows in groupby(chain.from_iterable(source), key=lambda b: b.start):
            decision = timestamp + HOUR
            if decision >= end:
                break
            frame = list(rows)
            if len(frame) != len(states) or {b.symbol for b in frame} != set(states) or any(b.end != decision for b in frame):
                raise ValueError('Momentum requires one complete hourly bar per symbol')
            if previous is not None and timestamp != previous + HOUR:
                raise ValueError('Momentum state cannot silently bridge missing hours')
            if (decision - start) % HOUR:
                raise ValueError('Momentum and run boundaries must align')
            previous = timestamp
            target = None
            with localcontext() as context:
                context.prec = 50
                points = self.apply_indicators(frame)
                if decision >= start:
                    if not began and decision != start:
                        raise ValueError('No completed bar is available at requested run start')
                    if not all(p.warmup_complete for p in points):
                        raise ValueError('Insufficient pre-run warmup for the configured recipe')
                    began = True
                    target = self.generate_signal(points)[0]
            if trace is not None:
                for point in points:
                    trace(point)
            if target is not None:
                yield target
        if not began:
            raise ValueError('No eligible momentum decision in the requested window')
