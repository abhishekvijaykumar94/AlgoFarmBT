from setuptools import setup, find_packages
setup(name='AlgoFarmBT', version='0.1.0', packages=find_packages(),
      install_requires=['algoLibs[postgres,analytics]'],
      entry_points={'console_scripts':['algofarm-bt-data=algoFarmBT.data:main',
                                       'algofarm-bt-spot=algoFarmBT.backtests.execute_momentum_backtest:main']})
