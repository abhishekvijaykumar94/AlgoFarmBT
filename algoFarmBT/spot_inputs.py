"""Qualify existing hourly database rows against pinned Binance spot archives.

No network or database writes occur here. Qualification is column-specific:
quote-volume fields are diagnosed but never enter a price-bar snapshot.
The archive layout and timestamp units follow Binance's public-data README:
https://github.com/binance/binance-public-data
"""
from bisect import bisect_right
import calendar
import copy
import csv
from datetime import datetime, timedelta, timezone
from dataclasses import asdict
import hashlib
import heapq
import io
from itertools import chain, groupby, zip_longest
import json
from pathlib import Path
import re
import shutil
import tempfile
import zipfile

from algoLibs.backtest.types import SpotBar, number
from algoLibs.market_data.types import DataRequest, decode, encode, utc

_VALIDATED_INDEX = {}  # Small byte-offset checkpoints; never cached prices.
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


class _ReferenceRows(dict):
    def __init__(self):
        super().__init__()
        self.excluded=[]


def _observed_archive_rows(root, comparison):
    """Independently decode actual selected observations; never fill absences."""
    symbol=comparison['symbol']
    if not re.fullmatch(r'[A-Z0-9]+',symbol):raise ValueError('Invalid symbol')
    start,end=(utc(comparison.get('selection_'+k,comparison[k])) for k in ('start','end'))
    if start>=end or any(t.minute or t.second or t.microsecond for t in (start,end)):
        raise ValueError('Hourly aligned source selection required')
    rows=_ReferenceRows();calendars=[]
    for item in comparison['source_files']:
        match=re.fullmatch(r'https://data\.binance\.vision/data/spot/(monthly|daily)/klines/'+symbol+r'/1h/('+symbol+r'-1h-(\d{4}-\d{2}(?:-\d{2})?)\.zip)',item['source_url'])
        if not match or item['file']!=match[2]:raise ValueError('Matching official Spot hourly source required')
        kind,filename,date=match.groups();pattern='%Y-%m' if kind=='monthly' else '%Y-%m-%d'
        origin=datetime.strptime(date,pattern).replace(tzinfo=timezone.utc)
        if origin.strftime(pattern)!=date:raise ValueError('Canonical archive date required')
        limit=(origin+timedelta(days=calendar.monthrange(origin.year,origin.month)[1]) if kind=='monthly' else origin+timedelta(days=1))
        calendars.append((origin,limit))
        path=local_file(root,filename);actual=digest(path);check=local_file(root,filename+'.CHECKSUM').read_text().split()
        if len(check)!=2 or check[0]!=actual or actual!=item['sha256'] or check[1].lstrip('*')!=filename:
            raise ValueError('Archive checksum mismatch')
        if path.stat().st_size>16*1024*1024:raise ValueError('Hourly archive exceeds size limit')
        with zipfile.ZipFile(path) as archive:
            if archive.namelist()!=[filename[:-4]+'.csv'] or archive.infolist()[0].file_size>64*1024*1024:
                raise ValueError('Matching bounded CSV member required')
            with archive.open(archive.namelist()[0]) as raw:
                previous=None
                for values in csv.reader(io.TextIOWrapper(raw,encoding='utf-8-sig')):
                    if tuple(values)==('open_time','open','high','low','close','volume','close_time','quote_volume','count','taker_buy_volume','taker_buy_quote_volume','ignore'):continue
                    if len(values)!=12:raise ValueError('Unexpected kline column count')
                    factor=1000000 if origin.year>=2025 else 1000
                    def timestamp(value):
                        seconds,fraction=divmod(int(value),factor)
                        return datetime.fromtimestamp(seconds,timezone.utc).replace(microsecond=fraction*(1000000//factor))
                    stamp=timestamp(values[0]);closing=timestamp(values[6])
                    if not origin<=stamp<limit or (previous is not None and stamp<=previous):
                        raise ValueError('Unordered or out-of-partition source observation')
                    previous=stamp
                    if not start<=stamp<end:continue
                    if stamp.minute or stamp.second or stamp.microsecond or closing!=stamp+HOUR-timedelta(microseconds=1000000//factor):
                        rows.excluded.append(dict(time=stamp,reason='invalid_hourly_boundary_or_close',source_url=item['source_url']))
                        continue
                    if stamp in rows:raise ValueError('Overlapping duplicate source timestamp')
                    rows[stamp]=dict(zip(('open','high','low','close','volume','quote_volume','trade_count','taker_buy_base_volume','taker_buy_quote_volume'),
                        [number(values[i]) for i in (1,2,3,4,5,7,8,9,10)]))
    covered=start
    for a,b in sorted(calendars):
        if a>covered:break
        covered=max(covered,b)
    if covered<end:raise ValueError('Source archive calendars do not cover the selection')
    return rows


def archive_rows(root, comparison):
    """Verify checksum, spot source identity, units and a full hourly month."""
    if 'source_files' in comparison:return _observed_archive_rows(root,comparison)
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
    observed=report.get('availability_policy')=='observed_hourly_cash_for_unready'
    if 'availability_policy' in report and not observed:raise ValueError('Unsupported availability policy')
    comparisons = {}
    for item in report['comparisons']:
        stamp=utc(item['start'])
        key = item['symbol'], stamp.replace(day=1,hour=0,minute=0,second=0,microsecond=0) if observed else stamp
        if key in comparisons:
            raise ValueError('Duplicate comparison selection')
        comparisons[key] = item
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix='.spot-prices-', dir=destination.parent))
    manifest = dict(format='qualified-spot-price-bars-v1', start=report['start'], end=report['end'],
                    native_sha256=report['native_sha256'], verified_native_fields=['ts'] + list(FIELDS),
                    exported_fields=['symbol', 'start', 'end', 'open', 'close'],
                    excluded_fields=list(DIAGNOSTIC_FIELDS), entries=[], sources=[], diagnostics={})
    if observed:manifest.update(format='qualified-spot-price-bars-v2',availability_policy=report['availability_policy'],availability_exclusions=[])
    seen, streams, counts, previous_times = set(), {}, {}, {}
    first_times={};gaps={}
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
                if observed:manifest['availability_exclusions'].extend(dict(symbol=symbol,**r) for r in getattr(reference,'excluded',()))
                if symbol not in streams:
                    streams[symbol] = (stage / (symbol + '.jsonl')).open('w')
                    counts[symbol] = 0
                    gaps[symbol]=[]
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
                        if not observed or stamp<=previous_times[symbol]:raise ValueError('Native hourly gap or ordering error')
                        gaps[symbol].append(dict(start=previous_times[symbol]+HOUR,end=stamp))
                    first_times.setdefault(symbol,stamp)
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
            if not observed and counts[symbol] != expected:
                raise ValueError('Incomplete per-symbol artifact coverage')
            filename = symbol + '.jsonl'
            manifest['entries'].append(dict(symbol=symbol, file=filename, rows=counts[symbol], sha256=digest(stage / filename)))
            if observed:manifest['entries'][-1].update(first_observed=first_times[symbol],end=previous_times[symbol]+HOUR,gaps=gaps[symbol])
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
    def __init__(self, directory, batch_size=1000, *, repository=None, bounds=None):
        self.directory = Path(directory)
        if type(batch_size) is not int or not 1 <= batch_size <= 100000:
            raise ValueError('Invalid batch size')
        self.batch_size = batch_size
        self.repository = repository
        self.read_audit = []
        raw = (self.directory / 'manifest.json').read_bytes()
        expected = (self.directory / 'manifest.sha256').read_text().strip()
        if hashlib.sha256(raw).hexdigest() != expected:
            raise ValueError('Price-bar manifest hash mismatch')
        self.data_id = 'sha256:' + expected
        self._manifest = decode(json.loads(raw))
        if self._manifest['format'] not in ('qualified-spot-price-bars-v1','qualified-spot-price-bars-v2'):
            raise ValueError('Unsupported price-bar artifact')
        self.availability_policy=self._manifest.get('availability_policy','strict')
        if self.availability_policy not in ('strict','observed_hourly_cash_for_unready'):
            raise ValueError('Unsupported source availability policy')
        self.asset_first_observed={e['symbol']:utc(e['first_observed']) for e in self._manifest['entries']} if self.availability_policy!='strict' else {}
        self.bounds = tuple(utc(t) for t in bounds) if bounds is not None else None
        if self.bounds is not None:
            start, end = self.bounds
            if (not utc(self._manifest['start']) <= start < end <= utc(self._manifest['end'])
                    or any(t.minute or t.second or t.microsecond for t in self.bounds)):
                raise ValueError('Bounded prices require covered, hourly aligned bounds')

    @property
    def manifest(self):
        manifest = copy.deepcopy(self._manifest)
        if self.bounds is not None:
            manifest['qualified_bounds'] = dict(start=utc(manifest['start']), end=utc(manifest['end']))
            manifest['start'], manifest['end'] = self.bounds
            manifest['view'] = dict(qualification_data_id=self.data_id,
                policy='Bounded view; entry hashes and row counts describe the complete qualification files')
        return manifest

    def __iter__(self):
        if self.repository is None:
            yield from self._file_batches()
            return
        # The repository supplies the actual simulation bars. The existing
        # immutable price artifact is the independent qualification oracle;
        # mismatches fail, with no file-reader fallback.
        file_batches = self._file_batches()
        queried = self._repository_bars()
        batch = []
        try:
            for actual, expected in zip_longest(queried, chain.from_iterable(file_batches)):
                if actual != expected:
                    raise ValueError('Repository price bars differ from the qualified snapshot')
                batch.append(actual)
                if len(batch) == self.batch_size:
                    yield batch
                    batch = []
            if batch:
                yield batch
        finally:
            queried.close()
            file_batches.close()

    def _repository_bars(self):
        manifest = self.manifest
        symbols = {e['symbol'] for e in manifest['entries']}
        identities = {s: {(r['instrument_id'], r['venue_id']) for r in manifest['sources']
                          if r['symbol'] == s} for s in symbols}
        if any(len(pairs) != 1 for pairs in identities.values()):
            raise ValueError('Repository price bars require one qualified canonical identity per symbol')
        pairs = {s: next(iter(values)) for s, values in identities.items()}
        if len(set(pairs.values())) != len(pairs):
            raise ValueError('Qualified symbols must have distinct canonical identities')

        def read(symbol):
            instrument, venue = pairs[symbol]
            request = DataRequest('public.md_bar', manifest['start'], manifest['end'],
                filters={'instrument_id': instrument, 'venue_id': venue, 'interval': '1h', 'bar_type': 'trade'},
                columns=('ts', 'instrument_id', 'venue_id', 'interval', 'bar_type', 'open', 'close'),
                batch_size=self.batch_size)
            audit = dict(symbol=symbol, request=asdict(request), rows=0, complete=False)
            self.read_audit.append(audit)
            batches = iter(self.repository.query(request))
            try:
                for rows in batches:
                    for row in rows:
                        if (row['instrument_id'], row['venue_id'], row['interval'], row['bar_type']) != (instrument, venue, '1h', 'trade'):
                            raise ValueError('Repository row differs from its qualified market selection')
                        audit['rows'] += 1
                        yield SpotBar(symbol, row['ts'], row['ts'] + HOUR, row['open'], row['close'])
                audit['complete'] = True
            finally:
                close = getattr(batches, 'close', None)
                if close is not None:
                    close()

        streams = [read(s) for s in sorted(symbols)]
        try:
            yield from heapq.merge(*streams, key=lambda b: (b.start, b.symbol))
        finally:
            for stream in streams:
                stream.close()

    def _file_batches(self):
        manifest = self._manifest
        observed=self.availability_policy!='strict'
        entries = manifest['entries']
        if not entries or len({e['symbol'] for e in entries}) != len(entries):
            raise ValueError('Unique nonempty artifact entries required')
        for entry in entries:
            if digest(local_file(self.directory, entry['file'])) != entry['sha256']:
                raise ValueError('Price-bar data hash mismatch')
        def read(entry):
            path=local_file(self.directory,entry['file'])
            key=(str(path.resolve()),entry['sha256'],_json(entry),_json((manifest['start'],manifest['end'],self.availability_policy)))
            if key not in _VALIDATED_INDEX:
                previous=None;first=None;count=0;gaps=[];checkpoints=[]
                with path.open('rb') as stream:
                    while True:
                        offset=stream.tell();line=stream.readline()
                        if not line:break
                        bar=SpotBar(**decode(json.loads(line)))
                        if bar.symbol!=entry['symbol'] or bar.end-bar.start!=HOUR:
                            raise ValueError('Unexpected artifact symbol or interval')
                        if not utc(manifest['start'])<=bar.start<bar.end<=utc(manifest['end']):
                            raise ValueError('Artifact row outside declared bounds')
                        if previous is not None:
                            if bar.start<=previous or (not observed and bar.start!=previous+HOUR):
                                raise ValueError('Artifact gap or ordering error')
                            if bar.start!=previous+HOUR:gaps.append(dict(start=previous+HOUR,end=bar.start))
                        if count%1024==0:checkpoints.append((bar.start,offset))
                        first=first or bar.start;previous=bar.start;count+=1
                if count!=entry['rows'] or not count:raise ValueError('Artifact row count mismatch')
                if observed and (first!=utc(entry['first_observed']) or previous+HOUR!=utc(entry['end']) or gaps!=entry['gaps']):
                    raise ValueError('Artifact availability metadata differs from observed rows')
                if len(_VALIDATED_INDEX)>=32:_VALIDATED_INDEX.pop(next(iter(_VALIDATED_INDEX)))
                _VALIDATED_INDEX[key]=checkpoints
            checkpoints=_VALIDATED_INDEX[key]
            start,end=self.bounds or (utc(manifest['start']),utc(manifest['end']))
            index=max(0,bisect_right([t for t,_ in checkpoints],start)-1)
            selected=0
            with path.open('rb') as stream:
                stream.seek(checkpoints[index][1])
                for line in stream:
                    bar=SpotBar(**decode(json.loads(line)))
                    if bar.start>=end:break
                    if bar.start<start:continue
                    if not observed and bar.start!=start+selected*HOUR:
                        raise ValueError('Incomplete or unaligned bounded price coverage')
                    selected+=1
                    yield bar
            if not observed and self.bounds is not None and selected!=int((end-start)/HOUR):
                raise ValueError('Incomplete bounded price coverage')
        batch = []
        for bar in heapq.merge(*(read(e) for e in entries), key=lambda b: (b.start, b.symbol)):
            batch.append(bar)
            if len(batch) == self.batch_size:
                yield batch
                batch = []
        if batch:
            yield batch
