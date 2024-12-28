from datetime import datetime
import algoLibs as libs
from algoFarmBT.strategies import MovingAverageStrategy

if __name__ == "__main__":


    #Initialise date and tenor related objects
    start_date =  start_time = datetime(2024, 6, 3, 0, 0, 0)
    end_date = datetime(2024, 6, 10, 0, 0, 0)
    calendar = libs.Calendar('/Users/abhishekvijaykumar/PycharmProjects/AlgoFarmPlus/AlgoLibs/data/Holidays.xlsx')
    tenor = libs.Tenor(libs.RollRule.MODFOLLOW,calendar)

    #Set list of tickers that can be used to query the DB
    tickers = ["AXISBANK-EQ", "APOLLOTYRE-EQ", "SBIN-EQ", "CIPLA-EQ", "POWERGRID-EQ"]

    #Add strategy related objects
    capital = 100000.0
    short_window = 20
    long_window = 50
    data_frequency = "5m"
    moving_average_startegy = MovingAverageStrategy('MovingAverageStrategy',short_window,long_window)
    allocation_strategy = libs.NaiveAllocationStrategy(capital,1000.0,5)
    angelBroking = libs.AngelBrokingBrokerage()

    backtest = libs.Backtest(tickers=tickers,
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

