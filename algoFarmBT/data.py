"""Inspect/export native historical datasets without constructing a simulator.

Examples:
  python -m algoFarmBT.data catalog
  python -m algoFarmBT.data read --request request.json
  python -m algoFarmBT.data prepare --request request.json --output snapshot
  python -m algoFarmBT.data read --request request.json --snapshot snapshot
"""
import argparse
import json
from pathlib import Path
from algoLibs.market_data import DataRequest, MarketDataReader, SnapshotReader
from algoLibs.market_data.types import encode


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['catalog', 'read', 'prepare'])
    parser.add_argument('--request', help='JSON request object or list of request objects')
    parser.add_argument('--output', help='New immutable snapshot directory')
    sources = parser.add_mutually_exclusive_group()
    sources.add_argument('--snapshot', help='Read a prepared snapshot offline')
    sources.add_argument('--duckdb', help='Read an existing local DuckDB database file')
    sources.add_argument('--parquet', help='JSON mapping of registered datasets to local Parquet file lists')
    args = parser.parse_args(argv)
    client = None
    if args.snapshot:
        reader = SnapshotReader(args.snapshot)
    elif args.duckdb or args.parquet:
        from algoLibs.dao.data_repository import DataRepository
        from algoLibs.market_data.duckdb import DuckDBClientManager
        mapping = json.loads(Path(args.parquet).read_text()) if args.parquet else None
        client = DuckDBClientManager(path=args.duckdb or ':memory:', parquet_sources=mapping)
        reader = MarketDataReader(repository=DataRepository(market_data_client_manager=client))
    else:
        reader = MarketDataReader()
    try:
        return _execute(parser, args, reader)
    finally:
        if client is not None:
            client.close()


def _execute(parser, args, reader):
    if args.action == 'catalog':
        if args.snapshot:
            print(json.dumps(encode(reader.manifest), indent=2))
        else:
            print(json.dumps(reader.catalog(), indent=2))
        return
    if not args.request:
        parser.error('--request is required')
    raw = json.loads(Path(args.request).read_text())
    requests = [DataRequest(**row) for row in (raw if isinstance(raw, list) else [raw])]
    if args.action == 'prepare':
        if not args.output or args.snapshot:
            parser.error('prepare requires --output and a database reader')
        snapshot = reader.prepare(requests, args.output)
        print(str(snapshot.directory))
    else:
        for request in requests:
            for batch in reader.read(request):
                for row in batch:
                    print(json.dumps(encode({'dataset': request.dataset, 'row': row}), allow_nan=False))


if __name__ == '__main__':
    main()
