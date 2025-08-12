from .fake_breakout_soft import process as soft_strategy
from .fake_breakout_strict import process as strict_strategy
from .gpt_trader import process as gpt_strategy
from .sp500_mean_revert import process as sp500_mean_revert_strategy
from .spx_breakout_pullback_filtered import process as spx_breakout_pullback_filtered
from .spx_fakebreakout_pro import process as spx_fakebreakout_pro
from .gpt_trader_old import process as gpt_strategy_old



def get_all_strategies():
    return [sp500_mean_revert_strategy]
    
# def get_all_strategies():
#     return [soft_strategy, strict_strategy, sp500_mean_revert_strategy, spx_breakout_pullback_filtered, 
#             spx_fakebreakout_pro, gpt_strategy, gpt_strategy_old]
