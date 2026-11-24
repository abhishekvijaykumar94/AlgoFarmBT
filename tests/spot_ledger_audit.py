"""Assertion-only audit reused from the retained independent integer/lot verifier.

This reads executed fills and marks; it never generates a signal, target or trade.
"""
from fractions import Fraction as F
from datetime import timedelta
from pathlib import Path
import json
from algoLibs.market_data.types import decode

def audit_run(run,bars):
    run=Path(run)
    result=decode(json.loads((run/'result.json').read_text()))
    assert result['spec']['cash_quantum']=='0.01' or str(result['spec']['cash_quantum'])=='0.01'
    rules={r['symbol']:r for r in result['spec']['rules']};symbols=sorted(rules)
    def integral(value):
     value=F(value)
     assert value.denominator==1
     return value.numerator

    def cents(value):return integral(F(value)*100)
    def ceil(value):return -(-value.numerator//value.denominator)
    def half_even(value):
     q,r=divmod(value.numerator,value.denominator)
     return q+int(2*r>value.denominator or (2*r==value.denominator and q%2))

    cash=cents(result['spec']['initial_cash']);initial=cash
    lots={s:0 for s in symbols};basis={s:0 for s in symbols};fee_total=0;realised=0;pointer=0;slippage=F(0)
    start,end=result['spec']['start'],result['spec']['end']
    fills=result['fills']
    for equity in result['equity']:
     while pointer<len(fills) and fills[pointer]['time']<equity['time']:
      fill=fills[pointer];pointer+=1;s=fill['symbol'];r=rules[s]
      assert fill['id']==f'fill-{pointer:08d}'
      assert start<=fill['time']<end and (fill['time']-start)%timedelta(hours=1)==timedelta(0)
      if pointer>1:assert fills[pointer-2]['time']<=fill['time']
      n=integral(F(fill['quantity'])/F(r['lot_size']));assert n>0
      opening=F(bars[(s,fill['time'])].open);tick=F(r['tick_size']);adverse=F(r['slippage_bps'])/10000
      adjusted=opening*(1+adverse if fill['side']=='buy' else 1-adverse)
      ticks=ceil(adjusted/tick) if fill['side']=='buy' else (adjusted/tick).numerator//(adjusted/tick).denominator
      assert F(fill['price'])==ticks*tick
      raw=F(fill['quantity'])*F(fill['price'])*100
      notional=ceil(raw) if fill['side']=='buy' else raw.numerator//raw.denominator
      assert notional==cents(fill['notional']) and F(fill['notional'])>=F(r['min_notional'])
      fee=ceil(F(notional)*F(r['fee_rate'])+F(r['fixed_fee'])*100)
      assert fee==cents(fill['fee'])
      slippage+=F(fill['quantity'])*abs(F(fill['price'])-opening)
      if fill['side']=='buy':
       cash-=notional+fee;lots[s]+=n;basis[s]+=notional+fee
       pnl=0
      else:
       assert fill['side']=='sell' and n<=lots[s]
       removed=basis[s] if n==lots[s] else basis[s]*n//lots[s]
       pnl=notional-fee-removed
       cash+=notional-fee;basis[s]-=removed;lots[s]-=n;realised+=pnl
      fee_total+=fee
      assert cash>=0 and cash==cents(fill['cash_after'])
      assert basis[s]==cents(fill['cost_basis_after']) and pnl==cents(fill['realised_pnl'])
      assert F(fill['quantity_after'])==lots[s]*F(r['lot_size'])
     quantities=dict(equity['quantities'])
     assert all(F(quantities[s])==lots[s]*F(rules[s]['lot_size']) for s in symbols)
     value=sum((F(quantities[s])*F(bars[(s,equity['time']-timedelta(hours=1))].close)*100 for s in symbols if quantities[s]),F(0))
     marked=half_even(value)
     assert cash==cents(equity['cash']) and marked==cents(equity['holdings_value'])
     assert cash+marked==cents(equity['equity'])
     assert fee_total==cents(equity['fees']) and realised==cents(equity['realised_pnl'])
    assert pointer==len(fills)
    assert fee_total==cents(result['total_fees']) and cash==cents(result['final_cash'])
    assert realised==cents(result['realised_pnl'])
    assert all(lots[s]*F(rules[s]['lot_size'])==F(dict(result['final_quantities'])[s]) for s in symbols)
    assert all(basis[s]==cents(dict(result['final_cost_basis'])[s]) for s in symbols)
    return dict(fills=len(fills),equity_marks=len(result['equity']),final_cash=str(result['final_cash']),
        final_equity=str(result['equity'][-1]['equity']),fees=str(result['total_fees']),
        checks=['integer_lots','adverse_tick_prices','minimum_notional','cash_quantum','fees','cash_after_each_fill',
                'cost_basis_after_each_fill','realised_pnl','positions_at_every_mark','equity_at_every_mark','final_balances'])
