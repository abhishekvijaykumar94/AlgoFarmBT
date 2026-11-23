"""Full-history inputs retain genuine hourly absences and listing boundaries."""
import csv,hashlib,io,json,tempfile,unittest,zipfile
from pathlib import Path
from datetime import datetime,timedelta,timezone
from algoFarmBT.spot_inputs import qualify_price_bars,QualifiedSpotSource
from algoLibs.market_data.types import encode

T=datetime(2026,5,1,tzinfo=timezone.utc);H=timedelta(hours=1)


def observed_fixture(root,policy='observed_hourly_cash_for_unready'):
    native=[];comparisons=[]
    for ident,symbol in enumerate(('BTCUSDT','SOLUSDT'),1):
        stream=io.StringIO();writer=csv.writer(stream)
        for i in range(24):
            if (symbol=='BTCUSDT' and i==2) or (symbol=='SOLUSDT' and i<4):continue
            stamp=T+i*H;micros=int(stamp.timestamp())*1000000
            writer.writerow([micros,'10','12','9','11','5',micros+3600000000-1,'50',1,'2','20',0])
            native.append(dict(symbol=symbol,native=dict(ts=stamp,instrument_id=ident,venue_id=1,
                interval='1h',bar_type='trade',open='10',high='12',low='9',close='11',volume='5',
                close_time=stamp+H-timedelta(microseconds=1),quote_volume='50',trade_count=1,
                taker_buy_base_volume='2',taker_buy_quote_volume='20')))
        filename=symbol+'-1h-2026-05-01.zip'
        with zipfile.ZipFile(root/filename,'w') as archive:archive.writestr(filename[:-4]+'.csv',stream.getvalue())
        sha=hashlib.sha256((root/filename).read_bytes()).hexdigest();(root/(filename+'.CHECKSUM')).write_text(sha+'  '+filename)
        comparisons.append(dict(symbol=symbol,instrument_id=ident,venue_id=1,start=T.isoformat(),end=(T+24*H).isoformat(),
            source_files=[dict(file=filename,sha256=sha,source_url='https://data.binance.vision/data/spot/daily/klines/'+symbol+'/1h/'+filename)]))
    # The native typed projection is independently compared to CSV values.
    from decimal import Decimal
    for row in native:
        for k in ('open','high','low','close','volume','quote_volume','taker_buy_base_volume','taker_buy_quote_volume'):row['native'][k]=Decimal(row['native'][k])
    raw=''.join(json.dumps(encode(r))+'\n' for r in native).encode();(root/'native.jsonl').write_bytes(raw)
    (root/'qualification.json').write_text(json.dumps(dict(kind='spot-archive-comparison',interval='1h',
        start=T.isoformat(),end=(T+24*H).isoformat(),native_file='native.jsonl',native_sha256=hashlib.sha256(raw).hexdigest(),
        availability_policy=policy,comparisons=comparisons)))
    return qualify_price_bars(root,root/'prices')


class FullHistoryInputTests(unittest.TestCase):
    def test_full_history_snapshot_uses_existing_runner_and_reports_only_observed_marks(self):
        from algoFarmBT.backtests.execute_momentum_backtest import run_snapshot
        from algoLibs.market_data.types import decode
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);observed_fixture(root)
            spec=root/'spec.json';spec.write_text(json.dumps(dict(start=T.isoformat(),end=(T+24*H).isoformat(),
                interval_seconds=3600,initial_cash='100',cash_quantum='.01',quote_currency='USDT',
                assumptions=['Synthetic full-history fixture'],execution_profile='research_bar_next_open_v1',
                rules=[dict(symbol=s,lot_size='1',tick_size='1',min_notional='0',fee_rate='.01',fixed_fee='0',slippage_bps='0') for s in ('BTCUSDT','SOLUSDT')])))
            targets=root/'targets.json';targets.write_text(json.dumps([
                dict(time=T.isoformat(),weights={'BTCUSDT':'.5','SOLUSDT':'0'}),
                dict(time=(T+2*H).isoformat(),weights={'BTCUSDT':'0','SOLUSDT':'0'}),
                dict(time=(T+4*H).isoformat(),weights={'BTCUSDT':'0','SOLUSDT':'.5'})]))
            output=root/'run';run_snapshot(root/'prices',spec,targets,output,retain_diagnostics=True)
            result=decode(json.loads((output/'result.json').read_text()))
            report=decode(json.loads((output/'report.json').read_text()))
            self.assertEqual(str(result['equity'][-1]['equity']),'103.50')
            self.assertEqual(str(result['total_fees']),'1.50')
            self.assertIsNone(report['annualised_sharpe'])
            self.assertTrue((output/'availability.json').exists())
            self.assertTrue((output/'report.xlsx').exists())

    def test_daily_sources_qualify_actual_hours_without_filling_gaps_or_prelisting_history(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=observed_fixture(root)
            bars=[b for batch in source for b in batch]
            self.assertEqual(len(bars),43)
            self.assertEqual(source.availability_policy,'observed_hourly_cash_for_unready')
            self.assertEqual(source.asset_first_observed['SOLUSDT'],T+4*H)
            self.assertFalse(any(b.symbol=='BTCUSDT' and b.start==T+2*H for b in bars))
            bounded=[b for batch in QualifiedSpotSource(root/'prices',bounds=(T,T+4*H)) for b in batch]
            self.assertEqual([(b.symbol,b.start) for b in bounded],[('BTCUSDT',T),('BTCUSDT',T+H),('BTCUSDT',T+3*H)])
