"""Quote-profile example; native bars/options/funding use algoFarmBT.data.

Set MARKET_DATA_READ_URL and MARKET_DATA_VENUE. This equity portfolio example
requires real bid/ask and last-price observations; it does not model derivatives.
"""
import argparse
from datetime import datetime
import algoLibs as libs
from algoLibs.market_data import MarketDataReader
from algoLibs.market_data.compat import TimescaleDBClientManager
from algoLibs.market_data.quotes import QuoteDataSource
from algoFarmBT.strategies import MovingAverageStrategy


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--start', required=True, help='UTC ISO timestamp including offset')
    parser.add_argument('--end', required=True)
    parser.add_argument('--symbols', nargs='+', required=True)
    parser.add_argument('--dataset', choices=['live.quote', 'public.md_tick_snapshot'], default='live.quote')
    args = parser.parse_args(argv)
    client = TimescaleDBClientManager()
    source = QuoteDataSource(client.reader, args.dataset, client.resolve_instruments(args.symbols))
    tenor = libs.Tenor(libs.RollRule.NOADJUST, None)
    strategy = MovingAverageStrategy('MovingAverageStrategy', 20, 50, backtest=True)
    backtest = libs.Backtest(args.symbols, datetime.fromisoformat(args.start.replace('Z', '+00:00')),
                            datetime.fromisoformat(args.end.replace('Z', '+00:00')), '5m', '1d',
                            strategy, 100000., libs.NaiveAllocationStrategy(100000., 1000., 5),
                            libs.AngelBrokingBrokerage(), tenor, 49, data_source=source)
    backtest.run()
    return backtest


if __name__ == '__main__':
    main()
