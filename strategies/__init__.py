from strategies.active_pulse import ActivePulseStrategy
from strategies.adaptive_trend_pullback import AdaptiveTrendPullbackStrategy
from strategies.base import Signal, Strategy
from strategies.donchian_breakout import DonchianBreakoutStrategy
from strategies.edge_compression import EdgeCompressionStrategy
from strategies.kinetic_equilibrium import KineticEquilibriumStrategy
from strategies.macd_pullback import MacdPullbackStrategy
from strategies.rsi_mean_reversion import RsiMeanReversionStrategy
from strategies.squeeze_breakout import SqueezeBreakoutStrategy
from strategies.squeeze_bidirectional import SqueezeBidirectionalStrategy
from strategies.donchian_bidirectional import DonchianBidirectionalStrategy
from strategies.macd_bear_pullback import MacdBearPullbackStrategy
from strategies.rsi_bidirectional import RsiBidirectionalStrategy

from strategies.forex_asian_fade import ForexAsianFadeStrategy
from strategies.forex_daily_breakout import ForexDailyBreakoutStrategy
from strategies.forex_donchian_trend import ForexDonchianTrendStrategy
from strategies.forex_ema_pullback import ForexEmaPullbackStrategy
from strategies.forex_london_breakout import ForexLondonBreakoutStrategy
from strategies.forex_macd_swing import ForexMacdSwingStrategy
from strategies.forex_overlap_momentum import ForexOverlapMomentumStrategy
from strategies.forex_range_fade import ForexRangeFadeStrategy
from strategies.forex_harmonic import ForexHarmonicStrategy
from strategies.forex_rsi_reversion import ForexRsiReversionStrategy
from strategies.forex_bollinger_fade import ForexBollingerFadeStrategy
from strategies.forex_short_breakout import ForexShortBreakoutStrategy
from strategies.forex_session_momentum import ForexSessionMomentumStrategy
from strategies.forex_smart_donchian import ForexSmartDonchianStrategy
from strategies.forex_mtf_breakout import ForexMtfBreakoutStrategy
from strategies.forex_atr_vol_breakout import ForexAtrVolBreakoutStrategy
from strategies.triple_tf_confluence import TripleTfConfluenceStrategy
from strategies.velocity_rejection import VelocityRejectionScalpStrategy
from strategies.funding_confluence import FundingConfluenceStrategy
from strategies.macd_bidirectional import MacdBidirectionalStrategy
from strategies.micro_orb import MicroOrbStrategy
from strategies.rsi2_reversion import Rsi2ReversionStrategy

STRATEGIES: dict[str, type[Strategy]] = {
    "micro_orb": MicroOrbStrategy,
    "rsi2_reversion": Rsi2ReversionStrategy,
    "macd_pullback": MacdPullbackStrategy,
    "donchian_breakout": DonchianBreakoutStrategy,
    "rsi_mean_reversion": RsiMeanReversionStrategy,
    "adaptive_trend_pullback": AdaptiveTrendPullbackStrategy,
    "squeeze_breakout": SqueezeBreakoutStrategy,
    "squeeze_bidirectional": SqueezeBidirectionalStrategy,
    "donchian_bidirectional": DonchianBidirectionalStrategy,
    "macd_bear_pullback": MacdBearPullbackStrategy,
    "rsi_bidirectional": RsiBidirectionalStrategy,
    "kinetic_equilibrium": KineticEquilibriumStrategy,
    "edge_compression": EdgeCompressionStrategy,
    "active_pulse": ActivePulseStrategy,
    "triple_tf_confluence": TripleTfConfluenceStrategy,
    "velocity_rejection": VelocityRejectionScalpStrategy,
    "forex_london_breakout": ForexLondonBreakoutStrategy,
    "forex_asian_fade": ForexAsianFadeStrategy,
    "forex_overlap_momentum": ForexOverlapMomentumStrategy,
    "forex_donchian_trend": ForexDonchianTrendStrategy,
    "forex_daily_breakout": ForexDailyBreakoutStrategy,
    "forex_macd_swing": ForexMacdSwingStrategy,
    "forex_ema_pullback": ForexEmaPullbackStrategy,
    "forex_range_fade": ForexRangeFadeStrategy,
    "forex_harmonic": ForexHarmonicStrategy,
    "forex_rsi_reversion": ForexRsiReversionStrategy,
    "forex_bollinger_fade": ForexBollingerFadeStrategy,
    "forex_short_breakout": ForexShortBreakoutStrategy,
    "forex_session_momentum": ForexSessionMomentumStrategy,
    "forex_smart_donchian": ForexSmartDonchianStrategy,
    "forex_mtf_breakout": ForexMtfBreakoutStrategy,
    "forex_atr_vol_breakout": ForexAtrVolBreakoutStrategy,
    "funding_confluence": FundingConfluenceStrategy,
    "macd_bidirectional": MacdBidirectionalStrategy,
}
__all__ = ["Signal", "Strategy", "STRATEGIES"]
