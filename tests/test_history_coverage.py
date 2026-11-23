"""Excluded historical partitions must not count as continuous warmup."""
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from algoFarmBT.backtests.execute_momentum_backtest import main
from algoFarmBT.strategies.momentum_strategy import HORIZONS
from algoLibs.backtest import SpotBar
from algoLibs.market_data.types import encode
from test_momentum import recipe


class ExcludedHistoryTests(unittest.TestCase):
    def test_excluded_february_cannot_satisfy_march_warmup_by_row_count(self):
        hour = timedelta(hours=1)
        january = datetime(2026, 1, 7, tzinfo=timezone.utc)
        march = datetime(2026, 3, 1, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshot = root / 'snapshot'
            snapshot.mkdir()
            # Two retained, regular segments on opposite sides of an excluded
            # month. There are 816 actual bars before March 10, exceeding the
            # 512-bar recipe requirement; row counts alone would accept them.
            starts = ([january + i * hour for i in range(600)] +
                      [march + i * hour for i in range(360)])
            bars = [SpotBar('BTCUSDT', stamp, stamp + hour, '100', '101')
                    for stamp in starts]
            path = snapshot / 'BTCUSDT.jsonl'
            path.write_text(''.join(json.dumps(encode(asdict(bar))) + '\n'
                                    for bar in bars))
            manifest = dict(format='qualified-spot-price-bars-v1',
                            start=january.isoformat(),
                            end=(march + 360 * hour).isoformat(),
                            entries=[dict(symbol='BTCUSDT', file=path.name,
                                          rows=960,
                                          sha256=hashlib.sha256(path.read_bytes()).hexdigest())])
            raw = json.dumps(encode(manifest)).encode()
            (snapshot / 'manifest.json').write_bytes(raw)
            (snapshot / 'manifest.sha256').write_text(hashlib.sha256(raw).hexdigest())
            selected = recipe(horizons=HORIZONS, rv_fast_span=72,
                              rv_slow_span=336, warmup_bars=512)
            config = dict(start='2026-03-10T00:00:00+00:00',
                          end='2026-03-10T04:00:00+00:00',
                          interval_seconds=3600, initial_cash='1000',
                          cash_quantum='.01', quote_currency='USDT',
                          execution_profile='research_bar_next_open_v1',
                          assumptions=['Synthetic excluded-partition fixture'],
                          rules=[dict(symbol='BTCUSDT', lot_size='.001',
                                      tick_size='.01', min_notional='0',
                                      fee_rate='0', fixed_fee='0', slippage_bps='0')])
            spec_path, recipe_path = root / 'run.json', root / 'recipe.json'
            spec_path.write_text(json.dumps(config))
            recipe_path.write_text(json.dumps(asdict(selected), default=str))
            output = root / 'run'
            with self.assertRaisesRegex(ValueError, 'gap'):
                main(['momentum', '--snapshot', str(snapshot), '--spec', str(spec_path),
                      '--recipe', str(recipe_path), '--output', str(output)])
            self.assertFalse(output.exists())


if __name__ == '__main__':
    unittest.main()
