from .sp_mean_revert_multi import process as mean_revert_multi
from .nasdaq_trend_follow import process as nasdaq_trend_follow


def get_all_strategies():
    return [mean_revert_multi, nasdaq_trend_follow]
