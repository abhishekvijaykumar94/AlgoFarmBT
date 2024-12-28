import pandas as pd
import algoLibs as libs

# Moving Average base class
class MovingAverage(libs.BaseIndicator):

    def __init__(self, period: int, name):
        super().__init__(period, name=name)

    def __str__(self):
        return f"MA_{self.period}"

# Simple Moving Average (SMA)
class SimpleMovingAverage(MovingAverage):

    def __init__(self, period: int):
        super().__init__(period, name="simpleMovingAverage")

    def calculate(self, data: pd.DataFrame, columns=None):
        result = pd.DataFrame(index=data.index)  # Preserve the original index
        columns = columns if columns else data.columns

        for column in columns:
            result[column + f"_{self.period}SMA"] = data[column].rolling(window=self.period, min_periods=self.period).mean()

        return result

# Exponential Moving Average (EMA)
class ExponentialMovingAverage(MovingAverage):

    def __init__(self, period: int):
        super().__init__(period, name="exponentialMovingAverage")

    def calculate(self, data: pd.DataFrame, columns=None):
        result = pd.DataFrame(index=data.index)  # Preserve the original index
        columns = columns if columns else data.columns

        for column in columns:
            result[column + f"_{self.period}EMA"] = data[column].ewm(span=self.period, min_periods=self.period).mean()

        return result
