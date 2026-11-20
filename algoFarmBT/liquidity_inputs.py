"""Column-specific qualification of saved Binance spot bar liquidity.

Archive volumes are observed; event-time availability is an explicit scenario
assumption. Archives do not establish historical spreads, depth or account fees.
"""
from collections import defaultdict
from datetime import timedelta
import hashlib
import json
from pathlib import Path
import shutil
import tempfile

from algoLibs.market_data.types import decode, utc
from algoLibs.backtest.liquidity import LIQUIDITY_SYMBOLS
from .spot_inputs import QualifiedSpotSource, archive_rows, local_file, digest, _json, HOUR

UNITS = dict(base_volume='base_asset', quote_volume='USDT', trade_count='count')


def qualify_liquidity(directory, prices, destination, *, availability_lag_seconds):
    if type(availability_lag_seconds) is not int or not 0 <= availability_lag_seconds <= 86400:
        raise ValueError('Explicit availability lag from 0 to 86400 seconds required')
    root, destination = Path(directory), Path(destination)
    if destination.exists():
        raise FileExistsError(destination)
    source = QualifiedSpotSource(prices)
    for _ in source:
        pass
    pinned = source.manifest
    if not {e['symbol'] for e in pinned['entries']}<=LIQUIDITY_SYMBOLS:
        raise ValueError('Liquidity profile supports BTC/ETH/SOL USDT instruments only')
    report = json.loads((root/'qualification.json').read_text())
    if report['kind']!='spot-archive-comparison' or report['interval']!='1h':
        raise ValueError('Unsupported liquidity comparison')
    native = local_file(root,report['native_file'])
    if digest(native)!=pinned['native_sha256'] or report['native_sha256']!=pinned['native_sha256']:
        raise ValueError('Native checksum differs from pinned prices')
    if report['comparisons']!=pinned['sources']:
        raise ValueError('Liquidity source identity differs from pinned prices')
    references, identities = {}, {}
    for item in report['comparisons']:
        key=item['symbol'],utc(item['start'])
        if key in references:
            raise ValueError('Duplicate liquidity selection')
        references[key]=archive_rows(root,item)
        ident=(item['instrument_id'],item['venue_id'])
        if key[0] in identities and identities[key[0]]!=ident:
            raise ValueError('Instrument identity changed across selections')
        identities[key[0]]=ident
    destination.parent.mkdir(parents=True,exist_ok=True)
    stage=Path(tempfile.mkdtemp(prefix='.spot-liquidity-',dir=destination.parent))
    manifest=dict(format='qualified-spot-liquidity-v1',profile='binance_spot_1h_base_volume_v1',
        price_data_id=source.data_id,native_sha256=pinned['native_sha256'],start=pinned['start'],end=pinned['end'],
        units=UNITS,availability=dict(status='assumed_not_capture_verified',lag_seconds=availability_lag_seconds,
            policy='bar_end_plus_declared_lag; archives published later do not verify live capture latency'),
        fields=dict(base_volume='independently_archive_matched',trade_count='independently_archive_matched',
            quote_volume='rowwise_archive_matched_or_unavailable',spread='unavailable',depth='unavailable',
            fees='assumed_in_run_rules',precision='assumed_in_run_rules',impact='uncalibrated_scenario'),
        sources=pinned['sources'],entries=[],gaps=[],diagnostics={})
    streams,counts,previous={},defaultdict(int),{}
    try:
        with native.open() as stream:
            for line in stream:
                envelope=decode(json.loads(line)); symbol=envelope['symbol']; row=envelope['native']; stamp=utc(row['ts'])
                key=symbol,stamp.replace(day=1,hour=0,minute=0,second=0,microsecond=0)
                expected=references.get(key,{}).pop(stamp,None)
                if expected is None or (row['instrument_id'],row['venue_id'])!=identities.get(symbol):
                    raise ValueError('Unexpected liquidity row identity or timestamp')
                if row['interval']!='1h' or row['bar_type']!='trade':
                    raise ValueError('Only hourly trade liquidity qualifies')
                if any(row.get(k)!=expected[k] for k in ('open','high','low','close','volume','trade_count')):
                    raise ValueError('Liquidity fields differ from checked source')
                if row['volume']<0 or row['trade_count']<0 or row['trade_count']!=int(row['trade_count']):
                    raise ValueError('Invalid liquidity units or values')
                if stamp!=(previous.get(symbol,utc(pinned['start'])-HOUR)+HOUR):
                    raise ValueError('Liquidity gap or duplicate')
                previous[symbol]=stamp
                matched=row.get('quote_volume')==expected['quote_volume'] and expected['quote_volume']>=0
                if not matched:
                    manifest['diagnostics'][symbol+':quote_volume_mismatch']=manifest['diagnostics'].get(symbol+':quote_volume_mismatch',0)+1
                if symbol not in streams:
                    streams[symbol]=(stage/(symbol+'.jsonl')).open('w')
                observation=dict(symbol=symbol,start=stamp,end=stamp+HOUR,
                    available_at=stamp+HOUR+timedelta(seconds=availability_lag_seconds),
                    base_volume=row['volume'],trade_count=row['trade_count'],
                    quote_volume=row['quote_volume'] if matched else None,
                    quote_volume_status='archive_matched' if matched else 'unavailable_native_source_mismatch',
                    instrument_id=row['instrument_id'],venue_id=row['venue_id'],
                    source_id='sha256:'+next(i['sha256'] for i in pinned['sources'] if i['symbol']==symbol and utc(i['start'])==key[1]))
                streams[symbol].write(_json(observation)+'\n');counts[symbol]+=1
        if any(references.values()) or set(counts)!=set(identities):
            raise ValueError('Incomplete liquidity source coverage')
        for symbol in sorted(streams):
            streams[symbol].close()
            if counts[symbol]!=int((utc(pinned['end'])-utc(pinned['start']))/HOUR):
                raise ValueError('Incomplete liquidity bounds')
            filename=symbol+'.jsonl'
            manifest['entries'].append(dict(symbol=symbol,file=filename,rows=counts[symbol],
                sha256=digest(stage/filename),instrument_id=identities[symbol][0],venue_id=identities[symbol][1]))
        raw=_json(manifest).encode();(stage/'manifest.json').write_bytes(raw)
        (stage/'manifest.sha256').write_text(hashlib.sha256(raw).hexdigest()+'\n')
        stage.rename(destination)
    except BaseException:
        for stream in streams.values():stream.close()
        shutil.rmtree(stage,ignore_errors=True);raise
    return destination


def load_liquidity(directory, prices, policy):
    """Validate every pinned field and row before constructing execution evidence."""
    from algoLibs.backtest.liquidity import LiquidityContext, LiquidityObservation, LiquidityPolicy
    root=Path(directory); source=prices if isinstance(prices,QualifiedSpotSource) else QualifiedSpotSource(prices)
    raw=(root/'manifest.json').read_bytes(); sha=(root/'manifest.sha256').read_text().strip()
    if hashlib.sha256(raw).hexdigest()!=sha:
        raise ValueError('Liquidity manifest hash mismatch')
    manifest=decode(json.loads(raw)); pinned=source.manifest
    if not {e['symbol'] for e in pinned['entries']}<=LIQUIDITY_SYMBOLS:
        raise ValueError('Liquidity profile supports BTC/ETH/SOL USDT instruments only')
    if (manifest['format']!='qualified-spot-liquidity-v1' or manifest['profile']!='binance_spot_1h_base_volume_v1'
            or manifest['units']!=UNITS or manifest['price_data_id']!=source.data_id
            or manifest['native_sha256']!=pinned['native_sha256'] or manifest['sources']!=pinned['sources']
            or utc(manifest['start'])!=utc(pinned['start']) or utc(manifest['end'])!=utc(pinned['end']) or manifest['gaps']!=[]):
        raise ValueError('Liquidity identity, units or coverage differs from pinned prices')
    lag=manifest['availability']['lag_seconds']
    if type(lag) is not int or not 0<=lag<=86400 or manifest['availability']['status']!='assumed_not_capture_verified':
        raise ValueError('Invalid assumed liquidity availability')
    entries=manifest['entries']
    symbols={e['symbol'] for e in pinned['entries']}
    if len(entries)!=len(symbols) or {e['symbol'] for e in entries}!=symbols:
        raise ValueError('Liquidity symbol coverage differs from prices')
    observations=[]
    for entry in entries:
        path=local_file(root,entry['file'])
        if digest(path)!=entry['sha256']:
            raise ValueError('Liquidity data hash mismatch')
        expected_start=utc(pinned['start']); count=0
        sources=[s for s in pinned['sources'] if s['symbol']==entry['symbol']]
        with path.open() as stream:
            for line in stream:
                row=LiquidityObservation(**decode(json.loads(line)))
                selected=[s for s in sources if utc(s['start'])<=row.start<utc(s['end'])]
                if (row.symbol!=entry['symbol'] or row.start!=expected_start or row.end!=row.start+HOUR
                        or row.end>utc(pinned['end']) or row.available_at!=row.end+timedelta(seconds=lag)
                        or len(selected)!=1 or row.source_id!='sha256:'+selected[0]['sha256']
                        or (row.instrument_id,row.venue_id)!=(selected[0]['instrument_id'],selected[0]['venue_id'])
                        or (row.instrument_id,row.venue_id)!=(entry['instrument_id'],entry['venue_id'])):
                    raise ValueError('Invalid liquidity row identity, coverage or availability')
                if (row.quote_volume is None)!=(row.quote_volume_status=='unavailable_native_source_mismatch') or row.quote_volume_status not in ('archive_matched','unavailable_native_source_mismatch'):
                    raise ValueError('Invalid quote-volume verification status')
                observations.append(row); count+=1;expected_start=row.end
        if count!=entry['rows'] or expected_start!=utc(pinned['end']):
            raise ValueError('Liquidity row count or bounds mismatch')
    resolved=policy if isinstance(policy,LiquidityPolicy) else LiquidityPolicy(**policy)
    return LiquidityContext(source.data_id,observations,resolved,data_id='sha256:'+sha,evidence=manifest)
