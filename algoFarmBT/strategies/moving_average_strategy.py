from datetime import datetime
from typing import Optional, List
import algoLibs as libs
import pandas as pd

from algoFarmBT.indicators import SimpleMovingAverage, CrossoverIndicator


class MovingAverageStrategy(libs.CoreTradingStrategy):

    def __init__(self, strat_name,short_window: int,long_window: int,backtest=False):
        super().__init__(strat_name, backtest)
        self.short_ma = SimpleMovingAverage(short_window)
        self.long_ma = SimpleMovingAverage(long_window)
        self.crossover_indicator = CrossoverIndicator()
        self.indicators = [self.short_ma, self.long_ma]

    def apply_indicators(self, data: pd.DataFrame) -> pd.DataFrame:
        # Ensure the DataFrame is sorted by ticker and time
        data.sort_values([libs.TickMarketFeedColumns.tag, libs.TickMarketFeedColumns.time], inplace=True)
        data.reset_index(drop=True, inplace=True)

        data[self.crossover_indicator.name] = None

        # Get the unique tickers
        tickers = data[libs.TickMarketFeedColumns.tag].unique()

        for ticker in tickers:
            # Create a boolean mask for this ticker
            ticker_mask = data[libs.TickMarketFeedColumns.tag] == ticker

            # Slice the DataFrame using the mask
            ticker_data = data.loc[ticker_mask]

            # Calculate indicators for this ticker
            short_ma_values = self.short_ma.calculate(ticker_data, columns=[libs.TickMarketFeedColumns.last_traded_price])
            long_ma_values = self.long_ma.calculate(ticker_data, columns=[libs.TickMarketFeedColumns.last_traded_price])
            crossover_signals = self.crossover_indicator.calculate(short_ma_values, long_ma_values)

            data.loc[ticker_mask, self.crossover_indicator.name] = crossover_signals.values

        return data.dropna()

    def get_signal(self, quote, index, transaction_type: libs.TransactionType):
        if self.backtest:
            timestamp = quote.get(libs.TickMarketFeedColumns.time,0)
            time = timestamp.to_pydatetime().strftime('%Y-%m-%d %H:%M:%S')
        else:
            time = str(datetime.now())

        signal_id = int(datetime.now().timestamp() * 1000000)
        signal = libs.Signal(
            _message_type="PLACE_ORDER_MESSAGE",
            trading_symbol=quote.get(libs.TickMarketFeedColumns.tag,0),
            transaction_type=transaction_type,
            token=quote.get(libs.TickMarketFeedColumns.token,0),
            ordertype="MARKET",
            duration="DAY",
            quantity=0,  # Quantity will be determined in Portfolio
            signal_time=time,
            signal_id=signal_id,
            strategy_id=self.strat_name
        )
        return signal

    def generate_signal(self, quotes: pd.DataFrame) -> List[Optional[libs.Signal]]:
        """
        Generates signals for multiple rows of quotes in a vectorized manner.

        Args:
        - quotes (pd.DataFrame): DataFrame containing multiple rows of price data.

        Returns:
        - List[Optional[Signal]]: List of generated signals (either LONG, SHORT, or None).
        """
        signals = []

        # Apply the indicator logic in a vectorized manner
        crossover_values = quotes[self.crossover_indicator.name].fillna(0)

        # Iterate through the DataFrame rows and generate signals
        for index, crossover_value in zip(quotes.index, crossover_values):
            if crossover_value == 2:
                # Bullish crossover - potential LONG signal
                signal = self.get_signal(quotes.loc[index], index, libs.TransactionType.LONG)
                signals.append(signal)
            elif crossover_value == -2:
                # Bearish crossover - potential SHORT signal
                signal = self.get_signal(quotes.loc[index],index, libs.TransactionType.SHORT)
                signals.append(signal)

        return signals

    def track_positions(self):
        """
        Abstract method to track live positions and generate TP/SL signals.
        """
        pass


    def process_events(self):
        """
        Abstract method to process incoming events from Kafka
        """
        pass


    def run(self):
        """
        This function is called at some frequency and triggers signal generation
        """
        pass