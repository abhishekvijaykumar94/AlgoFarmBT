"""Qualify existing hourly database rows against pinned Binance spot archives.

No network or database writes occur here. Qualification is column-specific:
quote-volume fields are diagnosed but never enter a price-bar snapshot.
The archive layout and timestamp units follow Binance's public-data README:
https://github.com/binance/binance-public-data
"""
import calendar
import copy
import csv
from datetime import datetime, timedelta, timezone
from dataclasses import asdict
import hashlib
import heapq
import io
from itertools import groupby
import json
from pathlib import Path
import re
import shutil
import tempfile
import zipfile

from algoLibs.backtest.types import SpotBar, number
from algoLibs.market_data.types import decode, encode, utc

HOUR = timedelta(hours=1)
FIELDS = ('open', 'high', 'low', 'close', 'volume', 'trade_count', 'taker_buy_base_volume')
DIAGNOSTIC_FIELDS = ('quote_volume', 'taker_buy_quote_volume')


def _json(value):
    return json.dumps(encode(value), sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(chunk)
    return result.hexdigest()


def local_file(root, name):
    path = (root / name).resolve()
    if path.parent != root.resolve():
        raise ValueError('Input file must be directly inside its artifact directory')
    return path


def archive_rows(root, comparison):
    """Verify checksum, spot source identity, units and a full hourly month."""
    symbol = comparison['symbol']
    if not re.fullmatch(r'[A-Z0-9]+', symbol):
        raise ValueError('Invalid symbol')
    start, end = utc(comparison['start']), utc(comparison['end'])
    if start.day != 1 or start != start.replace(hour=0, minute=0, second=0, microsecond=0):
        raise ValueError('Archive must start at a UTC month boundary')
    expected_end = start + timedelta(days=calendar.monthrange(start.year, start.month)[1])
    if end != expected_end:
        raise ValueError('Only full monthly hourly archives are supported')
    filename = f'{symbol}-1h-{start:%Y-%m}.zip'
    url = f'https://data.binance.vision/data/spot/monthly/klines/{symbol}/1h/{filename}'
    if comparison['source_url'] != url:
        raise ValueError('Source must be the matching Binance spot hourly archive')
    path = local_file(root, filename)
    actual = digest(path)
    checksum = local_file(root, filename + '.CHECKSUM').read_text().split()
    if not checksum or checksum[0] != actual or actual != comparison['sha256']:
        raise ValueError('Archive checksum mismatch')
    if path.stat().st_size > 16 * 1024 * 1024:
        raise ValueError('Hourly archive exceeds size limit')
    rows = {}
    with zipfile.ZipFile(path) as archive:
        if archive.namelist() != [filename[:-4] + '.csv']:
            raise ValueError('Archive must contain one matching CSV')
        member = archive.infolist()[0]
        if member.file_size > 64 * 1024 * 1024:
            raise ValueError('Uncompressed hourly archive exceeds size limit')
        with archive.open(member) as raw:
            for values in csv.reader(io.TextIOWrapper(raw)):
                if len(values) != 12:
                    raise ValueError('Unexpected kline column count')
                # Binance spot changed from milliseconds to microseconds in 2025.
                factor = 1000000 if start.year >= 2025 else 1000
                def timestamp(value):
                    seconds, fraction = divmod(int(value), factor)
                    return datetime.fromtimestamp(seconds, timezone.utc).replace(microsecond=fraction * (1000000 // factor))
                stamp = timestamp(values[0])
                closing = timestamp(values[6])
                if closing != stamp + HOUR - timedelta(microseconds=1000000 // factor):
                    raise ValueError('Archive close time or timestamp units are invalid')
                if stamp in rows:
                    raise ValueError('Duplicate archive timestamp')
                rows[stamp] = dict(zip(('open', 'high', 'low', 'close', 'volume', 'quote_volume',
                                        'trade_count', 'taker_buy_base_volume', 'taker_buy_quote_volume'),
                                       [number(values[i]) for i in (1, 2, 3, 4, 5, 7, 8, 9, 10)]))
    expected = [start + i * HOUR for i in range(int((end - start) / HOUR))]
    if sorted(rows) != expected:
        raise ValueError('Archive is incomplete or contains out-of-period timestamps')
    return rows


def qualify_price_bars(directory, destination):
    """Produce a new immutable price-only projection from matched native rows."""
    root, destination = Path(directory), Path(destination)
    if destination.exists():
        raise FileExistsError(destination)
    report = json.loads((root / 'qualification.json').read_text())
    if report['kind'] != 'spot-archive-comparison' or report['interval'] != '1h':
        raise ValueError('Unsupported comparison input')
    native = local_file(root, report['native_file'])
    if digest(native) != report['native_sha256']:
        raise ValueError('Native extraction checksum mismatch')
    comparisons = {}
    for item in report['comparisons']:
        key = item['symbol'], utc(item['start'])
        if key in comparisons:
            raise ValueError('Duplicate comparison selection')
        comparisons[key] = item
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix='.spot-prices-', dir=destination.parent))
    manifest = dict(format='qualified-spot-price-bars-v1', start=report['start'], end=report['end'],
                    native_sha256=report['native_sha256'], verified_native_fields=['ts'] + list(FIELDS),
                    exported_fields=['symbol', 'start', 'end', 'open', 'close'],
                    excluded_fields=list(DIAGNOSTIC_FIELDS), entries=[], sources=[], diagnostics={})
    seen, streams, counts, previous_times = set(), {}, {}, {}
    try:
        with native.open() as source:
            def rows():
                for line in source:
                    row = decode(json.loads(line))
                    stamp = utc(row['native']['ts'])
                    yield row['symbol'], stamp.replace(day=1, hour=0, minute=0, second=0, microsecond=0), row['native']
            for key, group in groupby(rows(), key=lambda row: row[:2]):
                if key in seen or key not in comparisons:
                    raise ValueError('Native selections are duplicated, unordered or unmapped')
                seen.add(key)
                comparison = comparisons[key]
                reference = archive_rows(root, comparison)
                manifest['sources'].append(dict(comparison))
                symbol = key[0]
                if symbol not in streams:
                    streams[symbol] = (stage / (symbol + '.jsonl')).open('w')
                    counts[symbol] = 0
                for _, _, row in group:
                    stamp = utc(row['ts'])
                    if (row['instrument_id'], row['venue_id']) != (comparison['instrument_id'], comparison['venue_id']):
                        raise ValueError('Native instrument identity differs from selection')
                    if row['interval'] != '1h' or row['bar_type'] != 'trade':
                        raise ValueError('Only hourly trade bars qualify')
                    expected = reference.pop(stamp, None)
                    if expected is None or any(row.get(k) != expected[k] for k in FIELDS):
                        raise ValueError('Required price-bar fields differ from spot source')
                    if symbol in previous_times and stamp != previous_times[symbol] + HOUR:
                        raise ValueError('Native hourly gap or ordering error')
                    if not utc(report['start']) <= stamp < utc(report['end']):
                        raise ValueError('Native row is outside artifact bounds')
                    previous_times[symbol] = stamp
                    for name in DIAGNOSTIC_FIELDS:
                        if row.get(name) != expected[name]:
                            key_name = symbol + ':' + name
                            manifest['diagnostics'][key_name] = manifest['diagnostics'].get(key_name, 0) + 1
                    if min(row['open'], row['high'], row['low'], row['close']) <= 0 or row['volume'] < 0:
                        raise ValueError('Invalid price or volume')
                    if row['high'] < max(row['open'], row['close']) or row['low'] > min(row['open'], row['close']):
                        raise ValueError('Invalid OHLC range')
                    bar = SpotBar(symbol, stamp, stamp + HOUR, row['open'], row['close'])
                    streams[symbol].write(_json(asdict(bar)) + '\n')
                    counts[symbol] += 1
                if reference:
                    raise ValueError('Native extraction is missing source rows')
        if seen != set(comparisons) or not seen:
            raise ValueError('Some requested spot selections are missing')
        for symbol, stream in streams.items():
            stream.close()
            expected = int((utc(report['end']) - utc(report['start'])) / HOUR)
            if counts[symbol] != expected:
                raise ValueError('Incomplete per-symbol artifact coverage')
            filename = symbol + '.jsonl'
            manifest['entries'].append(dict(symbol=symbol, file=filename, rows=counts[symbol], sha256=digest(stage / filename)))
        raw = _json(manifest).encode()
        (stage / 'manifest.json').write_bytes(raw)
        (stage / 'manifest.sha256').write_text(hashlib.sha256(raw).hexdigest() + '\n')
        stage.rename(destination)
    except BaseException:
        for stream in streams.values():
            stream.close()
        shutil.rmtree(stage, ignore_errors=True)
        raise
    return QualifiedSpotSource(destination)


class QualifiedSpotSource:
    """Repeatable, hashed, chronological bar batches using O(symbols) merge state."""
    def __init__(self, directory, batch_size=1000):
        self.directory = Path(directory)
        if type(batch_size) is not int or not 1 <= batch_size <= 100000:
            raise ValueError('Invalid batch size')
        self.batch_size = batch_size
        raw = (self.directory / 'manifest.json').read_bytes()
        expected = (self.directory / 'manifest.sha256').read_text().strip()
        if hashlib.sha256(raw).hexdigest() != expected:
            raise ValueError('Price-bar manifest hash mismatch')
        self.data_id = 'sha256:' + expected
        self._manifest = decode(json.loads(raw))
        if self._manifest['format'] != 'qualified-spot-price-bars-v1':
            raise ValueError('Unsupported price-bar artifact')

    @property
    def manifest(self):
        return copy.deepcopy(self._manifest)

    def __iter__(self):
        manifest = self.manifest
        entries = manifest['entries']
        if not entries or len({e['symbol'] for e in entries}) != len(entries):
            raise ValueError('Unique nonempty artifact entries required')
        for entry in entries:
            if digest(local_file(self.directory, entry['file'])) != entry['sha256']:
                raise ValueError('Price-bar data hash mismatch')
        def read(entry):
            previous, count = None, 0
            with local_file(self.directory, entry['file']).open() as stream:
                for line in stream:
                    bar = SpotBar(**decode(json.loads(line)))
                    if bar.symbol != entry['symbol'] or bar.end - bar.start != HOUR:
                        raise ValueError('Unexpected artifact symbol or interval')
                    if not utc(manifest['start']) <= bar.start < bar.end <= utc(manifest['end']):
                        raise ValueError('Artifact row outside declared bounds')
                    if previous is not None and bar.start != previous + HOUR:
                        raise ValueError('Artifact gap or ordering error')
                    previous = bar.start
                    count += 1
                    yield bar
            if count != entry['rows']:
                raise ValueError('Artifact row count mismatch')
        batch = []
        for bar in heapq.merge(*(read(e) for e in entries), key=lambda b: (b.start, b.symbol)):
            batch.append(bar)
            if len(batch) == self.batch_size:
                yield batch
                batch = []
        if batch:
            yield batch
