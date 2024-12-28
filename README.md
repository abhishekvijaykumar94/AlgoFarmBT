# Backtesting Module

The Backtesting module enables you to simulate trading strategies using historical market data. By replicating real trading conditions, it helps you evaluate the performance of trading strategies and fine-tune them for live trading. Below is a guide to creating a new backtest using this module.

## Overview
To create a backtest, you need to:
1. **Define a Strategy**: Implement a strategy class.
2. **Define Indicators**: Implement indicator classes used by the strategy.
3. **Execute the Backtest**: Create a script to set up and execute the backtest.

This guide explains each step and references the provided files for illustration:
- **Strategy Class**: `moving_average_strategy.py`
- **Indicator Classes**: `crossover.py`, `moving_average.py`
- **Execution Script**: `execute_moving_average_backtest.py`

---

## 1. Defining a Strategy
A strategy encapsulates the logic for generating trading signals based on specific conditions.

### Example: Moving Average Strategy
Refer to `moving_average_strategy.py`.

```python
class MovingAverageStrategy(libs.CoreTradingStrategy):

    def __init__(self, strat_name,short_window: int,long_window: int,backtest=False):
        super().__init__(strat_name, backtest)
        self.short_ma = SimpleMovingAverage(short_window)
        self.long_ma = SimpleMovingAverage(long_window)
        self.crossover_indicator = CrossoverIndicator()
        self.indicators = [self.short_ma, self.long_ma]

    def apply_indicators(self, data: pd.DataFrame) -> pd.DataFrame:
        # Ensure the DataFrame is sorted by ticker and time
        data.sort_values([TickMarketFeedColumns.tag, TickMarketFeedColumns.time], inplace=True)
        data.reset_index(drop=True, inplace=True)

        data[self.crossover_indicator.name] = None

        # Get the unique tickers
        tickers = data[TickMarketFeedColumns.tag].unique()

        for ticker in tickers:
            # Create a boolean mask for this ticker
            ticker_mask = data[TickMarketFeedColumns.tag] == ticker

            # Slice the DataFrame using the mask
            ticker_data = data.loc[ticker_mask]

            # Calculate indicators for this ticker
            short_ma_values = self.short_ma.calculate(ticker_data, columns=[TickMarketFeedColumns.last_traded_price])
            long_ma_values = self.long_ma.calculate(ticker_data, columns=[TickMarketFeedColumns.last_traded_price])
            crossover_signals = self.crossover_indicator.calculate(short_ma_values, long_ma_values)

            data.loc[ticker_mask, self.crossover_indicator.name] = crossover_signals.values

        return data.dropna()

    def get_signal(self, quote, index, transaction_type: TransactionType):
        if self.backtest:
            timestamp = quote.get(TickMarketFeedColumns.time,0)
            time = timestamp.to_pydatetime().strftime('%Y-%m-%d %H:%M:%S')
        else:
            time = str(datetime.now())

        signal_id = int(datetime.now().timestamp() * 1000000)
        signal = Signal(
            _message_type="PLACE_ORDER_MESSAGE",
            trading_symbol=quote.get(TickMarketFeedColumns.tag,0),
            transaction_type=transaction_type,
            token=quote.get(TickMarketFeedColumns.token,0),
            ordertype="MARKET",
            duration="DAY",
            quantity=0,  # Quantity will be determined in Portfolio
            signal_time=time,
            signal_id=signal_id,
            strategy_id=self.strat_name
        )
        return signal

    def generate_signal(self, quotes: pd.DataFrame) -> List[Optional[Signal]]:
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
                signal = self.get_signal(quotes.loc[index], index, TransactionType.LONG)
                signals.append(signal)
            elif crossover_value == -2:
                # Bearish crossover - potential SHORT signal
                signal = self.get_signal(quotes.loc[index],index, TransactionType.SHORT)
                signals.append(signal)

        return signals
```

### Key Functions
- **`__init__(self, params)`**:
  Initializes the strategy with parameters like moving average window sizes or thresholds.
- **`generate_signals(self, data)`**:
  Processes historical data to generate buy/sell signals.
  - **Input**: Historical market data.
  - **Output**: A list of signals (buy, sell, or hold).
- **`evaluate_performance(self, trades)`**:
  Evaluates the strategy’s performance based on simulated trades.

### Customization
Create a new strategy by subclassing the base strategy class and overriding the methods above. Ensure your strategy handles:
- Entry and exit conditions.
- Risk management.

---

## 2. Defining Indicators
Indicators are reusable components used by strategies to analyze market data.

### Example: Crossover Indicator
Refer to `crossover.py`.

```python
class CrossoverIndicator(BaseIndicator):

    def __init__(self):
        """
        Initialize the CrossoverIndicator.
        """
        super().__init__(period=None, name="crossover")  # Period is not applicable for crossover

    def calculate(self, series1: pd.DataFrame, series2: pd.DataFrame) -> pd.Series:
        """
        Calculate the crossover signals between two time series.

        :param series1: First time series as a pandas DataFrame (single column expected).
        :param series2: Second time series as a pandas DataFrame (single column expected).
        :return: pandas Series with crossover signals.
        """
        # Extract the first column from each DataFrame
        series1 = series1.iloc[:, 0]
        series2 = series2.iloc[:, 0]

        # Ensure the series are aligned, preserving the index and filling with NaN for missing values
        series1, series2 = series1.align(series2, join='outer')

        # Generate signals
        signal = (series1 > series2).astype(float) - (series1 < series2).astype(float)

        # Set signal to NaN where either series1 or series2 is NaN
        signal[series1.isna() | series2.isna()] = float('nan')

        # Calculate crossover
        crossover = signal.diff()

        # Set crossover to NaN where either series1 or series2 is NaN
        crossover[series1.isna() | series2.isna()] = float('nan')

        crossover.name = "crossover"

        return crossover

    def __str__(self):
        return "Crossover"
```

### Example: Moving Average Indicator
Refer to `moving_average.py`.

```python
class SimpleMovingAverage(MovingAverage):

    def __init__(self, period: int):
        super().__init__(period, name="simpleMovingAverage")

    def calculate(self, data: pd.DataFrame, columns=None):
        result = pd.DataFrame(index=data.index)  # Preserve the original index
        columns = columns if columns else data.columns

        for column in columns:
            result[column + f"_{self.period}SMA"] = data[column].rolling(window=self.period, min_periods=self.period).mean()

        return result
```

### Key Functions
- **`__init__(self, short_window, long_window)`**:
  Initializes the crossover indicator with short and long moving average window sizes.
- **`calculate(self, data)`**:
  Computes the crossover points between short and long moving averages.
  - **Input**: Historical market data.
  - **Output**: Crossover signals (e.g., bullish or bearish).

### Customization
Create new indicators by subclassing the base indicator class and implementing the required logic in the `calculate` function.

---

## 3. Executing the Backtest
The execution script sets up the environment, queries historical data from InfluxDB, and runs the backtest.

### Example: Execute Moving Average Backtest
Refer to `execute_moving_average_backtest.py`.

```python
 	#Initialise date and tenor related objects
    start_date =  start_time = datetime(2024, 6, 3, 0, 0, 0)
    end_date = datetime(2024, 6, 10, 0, 0, 0)
    calendar = Calendar('/Users/abhishekvijaykumar/PycharmProjects/AlgoFarmPlus/AlgoLibs/data/Holidays.xlsx')
    tenor = Tenor(RollRule.MODFOLLOW,calendar)

    #Set list of tickers that can be used to query the DB
    tickers = ["AXISBANK-EQ", "APOLLOTYRE-EQ", "SBIN-EQ", "CIPLA-EQ", "POWERGRID-EQ"]

    #Add strategy related objects
    capital = 100000.0
    short_window = 20
    long_window = 50
    data_frequency = "5m"
    moving_average_startegy = MovingAverageStrategy('MovingAverageStrategy',short_window,long_window)
    allocation_strategy = NaiveAllocationStrategy(capital,1000.0,5)
    angelBroking = AngelBrokingBrokerage()

    backtest = Backtest(tickers=tickers,
                        start_date=start_date,
                        end_date=end_date,
                        data_frequency=data_frequency,
                        batch_frequency="1d",
                        strategy=moving_average_startegy,
                        capital=capital,
                        allocation_strategy=allocation_strategy,
                        broker=angelBroking,
                        tenor=tenor,
                        last_n_points=long_window-1
                        )

    backtest.run()
    backtest.generate_report('/Users/abhishekvijaykumar/PycharmProjects/AlgoFarmPlus/reports/')

```

### Key Components
- **Querying Historical Data**:
  The backtest module fetches historical data directly from InfluxDB. Users only need to specify:
  - The tickers to query.
  - The start and end dates for the backtest.
  - The time frequency (e.g., daily, hourly).
- **Setting Up the Strategy**:
  Instantiate the strategy with necessary parameters.
- **Running the Backtest**:
  Pass the queried data and strategy to the backtesting engine.
- **Evaluating Performance**:
  Analyze results to evaluate performance.

---

## Additional Notes
- Ensure all scripts and classes adhere to the module’s standard input and output requirements.
- Reuse indicator classes where possible to avoid duplication.
- Document any custom logic in your strategy or indicator classes for clarity.

By following this guide, you can efficiently create, test, and evaluate new trading strategies using the backtesting module.
