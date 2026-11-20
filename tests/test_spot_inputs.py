"""Source identity, exact field comparison and artifact integrity checks."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import csv
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from algoFarmBT.spot_inputs import QualifiedSpotSource, qualify_price_bars
from algoLibs.market_data.types import encode, decode

T = datetime(2026, 5, 1, tzinfo=timezone.utc)
H = timedelta(hours=1)


def make_inputs(root, trend=False):
    report = dict(kind='spot-archive-comparison', interval='1h', start=T.isoformat(),
                  end=(T + 744 * H).isoformat(), native_file='native.jsonl', comparisons=[])
    native = []
    for ident, symbol in enumerate(('BTCUSDT', 'ETHUSDT', 'SOLUSDT'), 1):
        stream = io.StringIO()
        writer = csv.writer(stream)
        for i in range(744):
            stamp = T + i * H
            micros = int(stamp.timestamp()) * 1000000
            base=100+i if trend else 100
            writer.writerow([micros, str(base), str(base+10), str(base-10), str(base+5), '10',
                             micros + 3600000000 - 1, '1000', '5', '4', '400', '0'])
            native.append(dict(symbol=symbol, native=dict(ts=stamp, instrument_id=ident, venue_id=1,
                interval='1h', bar_type='trade', open=Decimal(base), high=Decimal(base+10),
                low=Decimal(base-10), close=Decimal(base+5), volume=Decimal('10'), trade_count=5,
                quote_volume=Decimal('1000'), taker_buy_base_volume=Decimal('4'),
                taker_buy_quote_volume=Decimal('400'))))
        filename = f'{symbol}-1h-2026-05.zip'
        with zipfile.ZipFile(root / filename, 'w') as archive:
            archive.writestr(filename.replace('.zip', '.csv'), stream.getvalue())
        sha = hashlib.sha256((root / filename).read_bytes()).hexdigest()
        (root / (filename + '.CHECKSUM')).write_text(sha + '  ' + filename)
        report['comparisons'].append(dict(symbol=symbol, instrument_id=ident, venue_id=1,
            start=report['start'], end=report['end'], sha256=sha,
            source_url=f'https://data.binance.vision/data/spot/monthly/klines/{symbol}/1h/{filename}'))
    write_native(root, report, native)
    return report, native


def write_native(root, report, rows):
    path = root / report['native_file']
    path.write_text(''.join(json.dumps(encode(row)) + '\n' for row in rows))
    report['native_sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
    (root / 'qualification.json').write_text(json.dumps(report))


class SpotInputTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.report, self.rows = make_inputs(self.root)
        self.output = self.root / 'qualified'

    def test_round_trip_sorted_merge_and_chunk_invariance(self):
        source = qualify_price_bars(self.root, self.output)
        a = [r for batch in source for r in batch]
        b = [r for batch in QualifiedSpotSource(self.output, batch_size=1) for r in batch]
        self.assertEqual(a, b)
        self.assertEqual(len(a), 2232)
        self.assertEqual([r.symbol for r in a[:3]], ['BTCUSDT', 'ETHUSDT', 'SOLUSDT'])
        self.assertEqual(a[0].close, Decimal('105'))
        with self.assertRaises(FileExistsError):
            qualify_price_bars(self.root, self.output)

    def test_diagnostic_quote_volume_difference_does_not_certify_that_field(self):
        self.rows[0]['native']['quote_volume'] += Decimal('.00000001')
        write_native(self.root, self.report, self.rows)
        source = qualify_price_bars(self.root, self.output)
        self.assertEqual(source.manifest['diagnostics']['BTCUSDT:quote_volume'], 1)
        self.assertIn('quote_volume', source.manifest['excluded_fields'])
        bar = next(iter(source))[0]
        self.assertFalse(hasattr(bar, 'volume'))

    def test_changed_price_fails_even_with_updated_native_hash(self):
        self.rows[0]['native']['close'] = Decimal('104')
        write_native(self.root, self.report, self.rows)
        with self.assertRaisesRegex(ValueError, 'differ'):
            qualify_price_bars(self.root, self.output)
        self.assertFalse(self.output.exists())

    def test_wrong_instrument_identity_is_rejected(self):
        self.rows[0]['native']['instrument_id'] = 999
        write_native(self.root, self.report, self.rows)
        with self.assertRaises(ValueError):
            qualify_price_bars(self.root, self.output)

    def test_missing_or_duplicate_rows_fail_without_publishing(self):
        for rows in (self.rows[1:], self.rows[:1] + self.rows):
            write_native(self.root, self.report, rows)
            with self.assertRaises(ValueError):
                qualify_price_bars(self.root, self.output)
            self.assertFalse(self.output.exists())

    def test_futures_source_url_cannot_qualify_spot(self):
        self.report['comparisons'][0]['source_url'] = self.report['comparisons'][0]['source_url'].replace('/spot/', '/futures/um/')
        write_native(self.root, self.report, self.rows)
        with self.assertRaisesRegex(ValueError, 'spot'):
            qualify_price_bars(self.root, self.output)

    def test_archive_and_native_tampering_fail(self):
        archive = self.root / 'BTCUSDT-1h-2026-05.zip'
        with archive.open('ab') as stream:
            stream.write(b'changed')
        with self.assertRaisesRegex(ValueError, 'checksum'):
            qualify_price_bars(self.root, self.output)
        (self.root / 'native.jsonl').write_text('tampered')
        with self.assertRaisesRegex(ValueError, 'checksum'):
            qualify_price_bars(self.root, self.output)

    def test_exported_data_tampering_fails_before_simulation(self):
        source = qualify_price_bars(self.root, self.output)
        path = self.output / 'BTCUSDT.jsonl'
        path.write_text(path.read_text().replace('100', '101', 1))
        with self.assertRaisesRegex(ValueError, 'hash'):
            list(source)

    def test_cli_run_persists_reproducible_economic_evidence(self):
        from algoFarmBT.backtests.execute_momentum_backtest import run_snapshot
        source = qualify_price_bars(self.root, self.output)
        config = dict(start=T.isoformat(), end=(T + 2 * H).isoformat(), interval_seconds=3600,
                      initial_cash='1000', cash_quantum='.01', quote_currency='USDT',
                      execution_profile='research_bar_next_open_v1', assumptions=['Synthetic cost and precision rules'],
                      rules=[dict(symbol=e['symbol'], lot_size='.001', tick_size='.01', min_notional='0',
                                  fee_rate='0', fixed_fee='1', slippage_bps='0') for e in source.manifest['entries']])
        config_path, targets_path = self.root / 'config.json', self.root / 'targets.json'
        config_path.write_text(json.dumps(config))
        targets_path.write_text(json.dumps([dict(time=T.isoformat(), weights={'BTCUSDT': '.1'}),
                                           dict(time=(T + H).isoformat(), weights={'BTCUSDT': '0'})]))
        first, second = self.root / 'run-1', self.root / 'run-2'
        result = run_snapshot(self.output, config_path, targets_path, first)
        run_snapshot(self.output, config_path, targets_path, second)
        self.assertEqual(result.final_cash, Decimal('998'))
        self.assertEqual(result.total_fees, Decimal('2'))
        self.assertEqual(result.realised_pnl, Decimal('-2'))
        self.assertEqual((first / 'manifest.sha256').read_bytes(), (second / 'manifest.sha256').read_bytes())
        saved = decode(json.loads((first / 'result.json').read_text()))
        self.assertEqual(saved['final_cash'], result.final_cash)
        manifest = decode(json.loads((first / 'manifest.json').read_text()))
        self.assertEqual(manifest['data_id'], source.data_id)
        self.assertEqual(manifest['strategy'], 'externally-supplied-targets')
        self.assertIn('report.xlsx',manifest['files'])
        from openpyxl import load_workbook
        workbook=load_workbook(first/'report.xlsx',data_only=True)
        metrics={r[0].value:r[1].value for r in workbook['Run Summary'].iter_rows(min_row=2)}
        report=decode(json.loads((first/'report.json').read_text()))
        self.assertEqual(metrics['net_pnl'],float(report['net_pnl']))
        self.assertEqual(metrics['max_drawdown'],float(report['max_drawdown']))
        self.assertEqual(metrics['annualised_sharpe'],'N/A')
        self.assertEqual(metrics['run.timezone'],'UTC')
        workbook.close()
        with zipfile.ZipFile(first / 'source.zip') as source_bundle:
            self.assertIn('algoLibs/backtest/backtest.py', source_bundle.namelist())
            self.assertIn('algoLibs/portfolio/portfolio.py', source_bundle.namelist())
            self.assertNotIn('algoLibs/backtest/cash_spot.py', source_bundle.namelist())
            self.assertIn('algoFarmBT/backtests/execute_momentum_backtest.py', source_bundle.namelist())
        for name, expected in manifest['files'].items():
            self.assertEqual(hashlib.sha256((first / name).read_bytes()).hexdigest(), expected)
        with self.assertRaises(FileExistsError):
            run_snapshot(self.output, config_path, targets_path, first)

    def test_six_horizon_three_asset_cli_runs_real_calculations_and_benchmarks(self):
        from dataclasses import asdict
        from algoFarmBT.backtests.execute_momentum_backtest import run_momentum
        from algoFarmBT.strategies.momentum_strategy import HORIZONS
        from test_momentum import recipe
        make_inputs(self.root,trend=True)
        source=qualify_price_bars(self.root,self.output)
        budgets={e['symbol']:'.3' for e in source.manifest['entries']}
        selected=recipe(horizons=HORIZONS,rv_fast_span=72,rv_slow_span=336,warmup_bars=512,
                        symbol_budgets=budgets,weight_caps=budgets)
        config=dict(start=(T+512*H).isoformat(),end=(T+516*H).isoformat(),interval_seconds=3600,
                    initial_cash='1000',cash_quantum='.01',quote_currency='USDT',
                    execution_profile='research_bar_next_open_v1',assumptions=['Synthetic fixture rules'],
                    rules=[dict(symbol=s,lot_size='.001',tick_size='.01',min_notional='0',
                                fee_rate='0',fixed_fee='1',slippage_bps='0') for s in budgets])
        config_path,recipe_path=self.root/'run.json',self.root/'recipe.json'
        config_path.write_text(json.dumps(config))
        recipe_path.write_text(json.dumps(asdict(selected),default=str))
        first,second=self.root/'momentum-1',self.root/'momentum-2'
        result=run_momentum(self.output,config_path,recipe_path,first,retain_trace=True)
        run_momentum(self.output,config_path,recipe_path,second,retain_trace=True)
        self.assertEqual((first/'manifest.sha256').read_bytes(),(second/'manifest.sha256').read_bytes())
        self.assertEqual({f.symbol for f in result.fills},set(budgets))
        self.assertTrue(all(f.time>=T+512*H for f in result.fills))
        self.assertTrue(all(f.cash_after>=0 for f in result.fills))
        self.assertEqual(len((first/'calculation-trace.jsonl').read_text().splitlines()),515*3)
        manifest=decode(json.loads((first/'manifest.json').read_text()))
        self.assertEqual(manifest['strategy']['kind'],'configured-momentum-recipe')
        benchmarks=decode(json.loads((first/'benchmarks.json').read_text()))
        self.assertEqual(benchmarks['results']['cash']['report']['net_pnl'],0)
        self.assertEqual(benchmarks['results']['buy_and_hold']['report']['fill_count'],3)
        report=decode(json.loads((first/'report.json').read_text()))
        self.assertEqual(report['total_fees'],result.total_fees)
        self.assertEqual(report['fill_count'],len(result.fills))
        strict=asdict(selected)
        strict['iv_mode']='iv_over_realised'
        strict['auxiliary_rules']=[dict(field='iv',source_id='required-iv',max_age_seconds=3600)]
        recipe_path.write_text(json.dumps(strict,default=str))
        with self.assertRaisesRegex(ValueError,'Missing or stale'):
            run_momentum(self.output,config_path,recipe_path,self.root/'strict-missing-iv')
        self.assertFalse((self.root/'strict-missing-iv').exists())


if __name__ == '__main__':
    unittest.main()
