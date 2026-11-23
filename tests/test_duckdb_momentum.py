"""Prepared backtests retain their engine/qualification contracts with DuckDB."""
from pathlib import Path
import json
import tempfile
import unittest
import importlib.util

from algoLibs.dao import DataRepository
from algoLibs.market_data import DuckDBDatabase, DuckDBClientManager
from algoFarmBT.spot_inputs import QualifiedSpotSource, qualify_price_bars
from test_spot_inputs import make_inputs, T, H


@unittest.skipUnless(importlib.util.find_spec('duckdb'), 'Requires algoLibs[duckdb]')
class DuckDBMomentumContracts(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        _, rows = make_inputs(self.root, trend=True)
        self.prices = self.root/'prices'
        qualify_price_bars(self.root, self.prices)
        self.path = self.root/'prices.duckdb'
        self.database = DuckDBDatabase(self.path, read_only=False)
        self.addCleanup(self.database.close)
        self.database.initialize(['public.md_bar'])
        with self.database.transaction(readonly=False) as conn:
            columns = tuple(rows[0]['native'])
            names = ','.join('"'+c+'"' for c in columns)
            conn.executemany('INSERT INTO public.md_bar ('+names+') VALUES ('+
                ','.join('?' for c in columns)+')', [[r['native'][c] for c in columns] for r in rows])
        self.client = DuckDBClientManager(self.database)
        self.repository = DataRepository(market_data_client_manager=self.client)

    def test_repository_bars_match_pinned_prices_and_obey_query_policy(self):
        source = QualifiedSpotSource(self.prices, batch_size=7, repository=self.repository)
        expected = [b for batch in QualifiedSpotSource(self.prices) for b in batch]
        actual = [b for batch in source for b in batch]
        self.assertEqual(actual, expected)
        self.assertEqual(source.data_id, QualifiedSpotSource(self.prices).data_id)
        self.assertEqual(sum(r['rows'] for r in source.read_audit), 2232)
        self.assertTrue(all(r['complete'] for r in source.read_audit))

        class DeniedRepository(DataRepository):
            def query(self, request, *args, **kwargs):
                raise PermissionError('repository policy denied')
        source = QualifiedSpotSource(self.prices, repository=DeniedRepository())
        with self.assertRaisesRegex(PermissionError, 'policy denied'):
            list(source)

    def test_repository_price_change_fails_without_a_file_fallback(self):
        with self.database.transaction(readonly=False) as conn:
            conn.execute('UPDATE public.md_bar SET close=999 WHERE instrument_id=1 AND ts=?', [T])
        with self.assertRaisesRegex(ValueError, 'differ from the qualified'):
            list(QualifiedSpotSource(self.prices, repository=self.repository))

    def test_actual_momentum_cli_preserves_economics_and_saves_repository_evidence(self):
        from algoFarmBT.backtests.execute_momentum_backtest import main, run_momentum
        self.database.close()
        config = dict(start=(T+512*H).isoformat(),end=(T+528*H).isoformat(),interval_seconds=3600,
            initial_cash='1000',cash_quantum='.01',quote_currency='USDT',
            execution_profile='research_bar_next_open_v1',assumptions=['Synthetic integration fixture'],
            rules=[dict(symbol=s,lot_size='.001',tick_size='.01',min_notional='0',fee_rate='.001',
                        fixed_fee='0',slippage_bps='5') for s in ('BTCUSDT','ETHUSDT','SOLUSDT')])
        recipe = json.loads(Path(__file__).parents[1].joinpath('examples/momentum.baseline.json').read_text())
        recipe.update(name='duckdb-flow-fixture',warmup_bars=512,assumptions=['Synthetic integration fixture'])
        spec = self.root/'spec.json'; spec.write_text(json.dumps(config))
        recipe_path = self.root/'recipe.json'; recipe_path.write_text(json.dumps(recipe))
        original = self.root/'file-run'; actual = self.root/'duckdb-run'
        run_momentum(self.prices,spec,recipe_path,original,retain_trace=True)
        main(['momentum','--snapshot',str(self.prices),'--spec',str(spec),'--recipe',str(recipe_path),
              '--duckdb',str(self.path),'--output',str(actual),'--trace'])
        for name in ('result.json','targets.json'):
            self.assertEqual((actual/name).read_bytes(),(original/name).read_bytes(),name)
        from algoLibs.market_data.types import decode
        from itertools import zip_longest
        # DuckDB retains its declared decimal scale; equal Decimal values may
        # carry different trailing zeros in intermediate diagnostic records.
        for name in ('calculation-trace.jsonl','execution-trace.jsonl'):
            with (actual/name).open() as a, (original/name).open() as b:
                for index, (left,right) in enumerate(zip_longest(a,b)):
                    self.assertIsNotNone(left,(name,index))
                    self.assertIsNotNone(right,(name,index))
                    self.assertEqual(decode(json.loads(left)),decode(json.loads(right)),(name,index))
        evidence = decode(json.loads((actual/'data-access.json').read_text()))
        self.assertEqual(evidence['route'],'DataRepository.query -> DataRequest.accept -> backend visitor')
        self.assertGreaterEqual(len(evidence['reads']),3)
        result=decode(json.loads((actual/'result.json').read_text()))
        self.assertGreater(len(result['fills']),0)
        self.assertTrue((actual/'report.xlsx').is_file())
