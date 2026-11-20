"""Inspect/export native historical datasets without constructing a simulator.

Examples:
  python -m algoFarmBT.data catalog
  python -m algoFarmBT.data read --request request.json
  python -m algoFarmBT.data prepare --request request.json --output snapshot
  python -m algoFarmBT.data read --request request.json --snapshot snapshot
"""
import argparse
from dataclasses import asdict
import json
from pathlib import Path
from algoLibs.market_data import DataRequest, MarketDataReader, SnapshotReader
from algoLibs.market_data.types import encode


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['catalog', 'read', 'prepare'])
    parser.add_argument('--request', help='JSON request object or list of request objects')
    parser.add_argument('--output', help='New immutable snapshot directory')
    parser.add_argument('--snapshot', help='Read a prepared snapshot offline')
    args = parser.parse_args(argv)
    reader = SnapshotReader(args.snapshot) if args.snapshot else MarketDataReader()
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
