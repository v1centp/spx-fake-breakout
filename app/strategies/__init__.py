from .fake_breakout_soft import process as soft_strategy
from .fake_breakout_strict import process as strict_strategy

def get_all_strategies():
    return [soft_strategy, strict_strategy]
