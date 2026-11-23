"""The real BT data command reads DuckDB through repository visitors."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
import importlib.util
from datetime import datetime, timezone

from algoFarmBT.data import main
from algoLibs.market_data.duckdb import DuckDBDatabase
from algoLibs.market_data.types import decode


@unittest.skipUnless(importlib.util.find_spec('duckdb'), 'Requires algoLibs[duckdb]')
class DuckDBCLIContracts(unittest.TestCase):
    def test_cli_reads_prepares_and_replays_the_same_local_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'research.duckdb'
            database = DuckDBDatabase(path, read_only=False)
            database.initialize(['public.md_bar'])
            with database.transaction(readonly=False) as conn:
                conn.execute("INSERT INTO public.md_bar (ts,instrument_id,venue_id,interval,bar_type,open) "
                    "VALUES ('2026-01-01T00:00:00Z',1,1,'1s','trade',100.125)")
            parquet = str(Path(directory)/'bars.parquet')
            with database.transaction() as conn:
                conn.execute('COPY public.md_bar TO ? (FORMAT PARQUET)', [parquet])
            database.close()
            mapping = Path(directory)/'datasets.json'
            mapping.write_text(json.dumps({'public.md_bar': [parquet]}))
            request = Path(directory)/'request.json'
            request.write_text(json.dumps(dict(dataset='public.md_bar', start='2026-01-01T00:00:00Z',
                end='2026-01-01T00:00:01Z', columns=['ts','open'])))
            args = ['--request', str(request)]
            def invoke(arguments):
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    main(arguments)
                return output.getvalue()
            live = invoke(['read', '--duckdb', str(path)]+args)
            self.assertEqual(invoke(['read', '--parquet', str(mapping)]+args), live)
            self.assertEqual(decode(json.loads(live))['row']['ts'], datetime(2026,1,1,tzinfo=timezone.utc))
            snapshot = Path(directory)/'snapshot'
            invoke(['prepare', '--duckdb', str(path), '--output', str(snapshot)]+args)
            self.assertEqual(invoke(['read','--snapshot',str(snapshot)]+args), live)
