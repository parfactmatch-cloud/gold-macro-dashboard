"""ARES-XAU Quant Terminal - Strategy Logic.

This file reads Jev's seven judgments to dictate execution actions.
Tune thresholds below or modify the apply_strategy hook to suit your edge.
"""

from typing import Any, Dict, Tuple


class Strategy:
    def __init__(self):
        # The seven tunable thresholds for `compose_action()`
        self.pull_quotes_stress_threshold = 0.8
        self.widen_stress_threshold = 0.5
        self.wide_toxic_threshold = 0.6
        self.stand_down_degrading_threshold = 0.7
        self.directional_confidence_min = 0.75
        self.inventory_pressure_max = 0.8
        self.volatility_mult = 1.5

    def apply_strategy(
        self,
        base_action: str,
        side: str,
        notional: float,
        state: Dict[str, Any],
        answers: Dict[str, Any],
    ) -> Tuple[str, str, float]:
        """Hook called on every tick after policy computation.

        Returns (action, side, notional).
        Free to inspect, alter, or veto (returning action='STAND_DOWN').
        """
        # Safety override: Veto and stand down if market spread widens past acceptable limits
        if state.get("spread_bps", 0) > 70.0:
            return "STAND_DOWN", side, notional

        return base_action, side, notional

