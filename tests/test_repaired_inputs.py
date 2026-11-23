"""Independent CSV oracle verifies repaired rows through public qualification."""
import csv,hashlib,io,json,tempfile,unittest,zipfile
from pathlib import Path
from datetime import timedelta
from decimal import Decimal as D
from algoLibs.market_data.types import decode,encode
from algoFarmBT.spot_inputs import qualify_price_bars
from test_full_history_inputs import observed_fixture,T,H

class RepairedInputTests(unittest.TestCase):
 def test_independent_minute_oracle_accepts_exact_sum_and_rejects_changed_volume(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);observed_fixture(root);filename='BTCUSDT-1m-2026-05-01.zip';out=io.StringIO();writer=csv.writer(out)
   for i in range(60):
    n=int((T+2*H+timedelta(minutes=i)).timestamp())*1000000
    writer.writerow([n,'10','14','8','12','2',n+60000000-1,'22',3,'1','11',0])
   with zipfile.ZipFile(root/filename,'w') as z:z.writestr(filename[:-4]+'.csv',out.getvalue())
   sha=hashlib.sha256((root/filename).read_bytes()).hexdigest();(root/(filename+'.CHECKSUM')).write_text(sha+'  '+filename)
   report=json.loads((root/'qualification.json').read_text());report['availability_policy']='observed_hourly_preserve_state_v1'
   for c in report['comparisons']:
    c['repair_targets']=[(T+2*H).isoformat()] if c['symbol']=='BTCUSDT' else []
    c['repair_sources']=[dict(file=filename,sha256=sha,source_url='https://data.binance.vision/data/spot/daily/klines/BTCUSDT/1m/'+filename)] if c['repair_targets'] else []
   rows=[decode(json.loads(l)) for l in (root/'native.jsonl').read_text().splitlines()];r=dict(rows[0]['native']);r.update(ts=T+2*H,close_time=T+3*H-timedelta(microseconds=1),open=D(10),high=D(14),low=D(8),close=D(12),volume=D(120),quote_volume=D(1320),trade_count=180,taker_buy_base_volume=D(60),taker_buy_quote_volume=D(660));rows.append(dict(symbol='BTCUSDT',native=r));rows.sort(key=lambda r:(r['symbol'],r['native']['ts']))
   def write():
    raw=''.join(json.dumps(encode(r))+'\n' for r in rows).encode();(root/'native.jsonl').write_bytes(raw);report['native_sha256']=hashlib.sha256(raw).hexdigest();(root/'qualification.json').write_text(json.dumps(report))
   write();source=qualify_price_bars(root,root/'repaired');bars=[b for batch in source for b in batch]
   self.assertEqual(len(bars),44);self.assertEqual(next(b.close for b in bars if b.symbol=='BTCUSDT' and b.start==T+2*H),D(12))
   r['volume']=D(119);write()
   with self.assertRaisesRegex(ValueError,'differ from spot source'):qualify_price_bars(root,root/'incorrect')
