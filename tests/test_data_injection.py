from datetime import datetime,timezone,timedelta
from types import SimpleNamespace
from unittest.mock import patch
import unittest
import pandas as pd
from algoLibs.backtest.backtest import Backtest

T=datetime(2026,9,4,tzinfo=timezone.utc)

class Portfolio:
    def __init__(self,*args):
        self.portfolio_data=pd.DataFrame(columns=['cash','holding_value','portfolio_value'])
        self.current_holdings={}
        self.trade_log=[]
    def update_portfolio(self,time,quotes):
        self.portfolio_data.loc[time]=[1000.,0.,1000.]
    def process_signal(self,*args):
        raise AssertionError('This data-contract test must not place a trade')

class Source:
    def __init__(self,rows):self.rows=rows
    def read_quotes(self,symbols,start,end,frequency):
        return self.rows[(self.rows.time>=start)&(self.rows.time<end)].copy()

class Strategy:
    def __init__(self):self.seen=[];self.prepared=[]
    def apply_indicators(self,data):self.prepared.append(len(data));return data
    def generate_signal(self,data):self.seen.extend(data[['time','ask_price']].to_dict('records'));return []

class InjectionTests(unittest.TestCase):
    def rows(self):
        return pd.DataFrame([dict(time=T+timedelta(hours=i),symbol='BTCUSDT',last_traded_price=100.,ask_price=101.,bid_price=99.,ask_volume=2.,bid_volume=2.) for i in [0,12,24,36]])
    def test_injected_data_does_not_create_clients_rescale_or_retrade_warmup(self):
        strategy=Strategy()
        tenor=SimpleNamespace(adjust_date=lambda stamp,freq:stamp+timedelta(days=1))
        with patch('algoLibs.backtest.backtest.DataRepository',side_effect=AssertionError('unexpected database construction')),patch('algoLibs.backtest.backtest.BacktestPortfolio',Portfolio):
            backtest=Backtest(['BTCUSDT'],T,T+timedelta(days=2),'1m','1d',strategy,1000.,None,None,tenor,1,data_source=Source(self.rows()))
            backtest.summarize_portfolio_performance=lambda *args:None
            backtest.run()
        self.assertEqual(strategy.prepared,[2,3])
        self.assertEqual(len(strategy.seen),4)
        self.assertEqual(len({r['time'] for r in strategy.seen}),4)
        self.assertEqual({r['ask_price'] for r in strategy.seen},{101.})
    def test_missing_prices_fail_instead_of_fabricating_quotes(self):
        rows=self.rows();rows['ask_price']=None
        with patch('algoLibs.backtest.backtest.BacktestPortfolio',Portfolio):
            bt=Backtest(['BTCUSDT'],T,T+timedelta(days=1),'1m','1d',Strategy(),1000.,None,None,SimpleNamespace(adjust_date=lambda t,f:t+timedelta(days=1)),0,data_source=Source(rows))
            with self.assertRaisesRegex(ValueError,'Missing executable quote'):bt.run()

if __name__=='__main__':unittest.main()
