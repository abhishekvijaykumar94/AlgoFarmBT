import pandas as pd
import algoLibs as libs

class CrossoverIndicator(libs.BaseIndicator):

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
