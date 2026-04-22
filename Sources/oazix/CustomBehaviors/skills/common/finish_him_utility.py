from typing import Any, Callable, Generator, override

from Py4GWCoreLib import Agent, Range
from Sources.oazix.CustomBehaviors.primitives.behavior_state import BehaviorState
from Sources.oazix.CustomBehaviors.primitives.bus.event_bus import EventBus
from Sources.oazix.CustomBehaviors.primitives.helpers import custom_behavior_helpers
from Sources.oazix.CustomBehaviors.primitives.helpers.behavior_result import BehaviorResult
from Sources.oazix.CustomBehaviors.primitives.helpers.targeting_order import TargetingOrder
from Sources.oazix.CustomBehaviors.primitives.scores.score_static_definition import ScoreStaticDefinition
from Sources.oazix.CustomBehaviors.primitives.skills.custom_skill import CustomSkill
from Sources.oazix.CustomBehaviors.primitives.skills.custom_skill_utility_base import CustomSkillUtilityBase


class FinishHimUtility(CustomSkillUtilityBase):
    """
    Utility for the 'Finish Him!' shout.

    Behavior:
    - Shout that deals strong damage and applies Cracked Armor + Deep Wound if the target has < 50% health.
    - This utility will only consider enemies whose health fraction is below 50% and will prefer the
      lowest-health valid enemy in spellcast range.
    - By default it's only considered while engaged (IN_AGGRO) to avoid wasting it out of combat.

    Fendi boss-fight override (SoO L3):
    - If Fendi Nin (model 7064) or Soul of Fendi (model 7065) is in range, has HP < 25%,
      and is NOT already suffering Deep Wound → return FENDI_FORCE_FIRE_SCORE (99) so
      this utility preempts other combat skills on the bar. The normal < 50% logic
      still applies as a fallback when no Fendi form qualifies.

    Note: This implementation assumes Agent.GetHealth(agent_id) returns a normalized fraction (0.0 - 1.0).
    If your code uses absolute HP values, tell me and I'll change the check to compare against max HP.
    """

    REQUIRED_TARGET_HP_FRACTION = 0.5

    # Fendi boss-fight override constants
    _FENDI_NIN_MODEL_ID         = 7064
    _SOUL_OF_FENDI_MODEL_ID     = 7065
    _FENDI_FORCE_HP_FRACTION    = 0.25   # force-fire when Fendi form below this
    _FENDI_FORCE_FIRE_SCORE     = 99.0   # high enough to preempt normal combat skills

    def __init__(
        self,
        event_bus: EventBus,
        current_build: list[CustomSkill],
        score_definition: ScoreStaticDefinition = ScoreStaticDefinition(75),
        mana_required_to_cast: int = 0,
        allowed_states: list[BehaviorState] = [BehaviorState.IN_AGGRO],
        custom_agent_targeting_predicate: Callable[[int], bool] | None = None,
    ) -> None:
        super().__init__(
            event_bus=event_bus,
            skill=CustomSkill("Finish_Him"),
            in_game_build=current_build,
            score_definition=score_definition,
            mana_required_to_cast=mana_required_to_cast,
            allowed_states=allowed_states,
        )

        self.score_definition = score_definition
        self.custom_agent_targeting_predicate: Callable[[int], bool] | None = custom_agent_targeting_predicate

    def _get_candidates(self) -> tuple[int, ...]:
        """
        Return enemy agent IDs ordered by priority (lowest HP, then distance) within shout/spellcast range.

        Only include enemies with known health fraction > 0 and < REQUIRED_TARGET_HP_FRACTION.
        Optionally narrowed by `custom_agent_targeting_predicate` (e.g. to restrict to specific
        boss models in a boss room).
        """
        def condition(agent_id: int) -> bool:
            hp = Agent.GetHealth(agent_id)
            if hp is None or hp <= 0.0 or hp >= self.REQUIRED_TARGET_HP_FRACTION:
                return False
            if self.custom_agent_targeting_predicate is not None and not self.custom_agent_targeting_predicate(agent_id):
                return False
            return True

        return custom_behavior_helpers.Targets.get_all_possible_enemies_ordered_by_priority(
            within_range=Range.Spellcast,
            condition=condition,
            sort_key=(TargetingOrder.HP_ASC, TargetingOrder.DISTANCE_ASC),
        )

    def _get_best_target(self) -> int | None:
        candidates = self._get_candidates()
        if not candidates:
            return None
        return candidates[0]

    def _get_fendi_force_fire_target(self) -> int | None:
        """
        Fendi boss-fight override: return an in-range Fendi form that meets the
        force-fire conditions (HP < 25%, no Deep Wound), else None.

        Scans any enemy of the Fendi Nin (7064) or Soul of Fendi (7065) model
        within spellcast range. If multiple qualify (rare — only one form alive
        at a time during normal fight flow) the lowest-HP one wins. Rejects
        candidates already carrying Deep Wound so we don't waste the shout
        refreshing an existing debuff.
        """
        def is_fendi_force_candidate(agent_id: int) -> bool:
            try:
                model_id = Agent.GetModelID(agent_id)
            except Exception:
                return False
            if model_id not in (self._FENDI_NIN_MODEL_ID, self._SOUL_OF_FENDI_MODEL_ID):
                return False
            try:
                hp = Agent.GetHealth(agent_id)
            except Exception:
                return False
            if hp is None or hp <= 0.0 or hp >= self._FENDI_FORCE_HP_FRACTION:
                return False
            try:
                if Agent.IsDeepWounded(agent_id):
                    return False
            except Exception:
                # If the check fails we err on the side of firing (better to
                # re-shout than to silently miss the force-fire window).
                pass
            # Respect any caller-supplied predicate (e.g. boss-room restriction)
            if self.custom_agent_targeting_predicate is not None and not self.custom_agent_targeting_predicate(agent_id):
                return False
            return True

        candidates = custom_behavior_helpers.Targets.get_all_possible_enemies_ordered_by_priority(
            within_range=Range.Spellcast,
            condition=is_fendi_force_candidate,
            sort_key=(TargetingOrder.HP_ASC, TargetingOrder.DISTANCE_ASC),
        )
        if not candidates:
            return None
        return candidates[0]

    @override
    def _evaluate(self, current_state: BehaviorState, previously_attempted_skills: list[CustomSkill]) -> float | None:
        """
        Fendi override first: if a Fendi form is <25% HP and not deep-wounded,
        return the force-fire score so this utility beats the rest of the bar.
        Otherwise fall back to the normal <50% HP logic.
        """
        # --- Fendi force-fire path ---
        fendi_target = self._get_fendi_force_fire_target()
        if fendi_target is not None:
            return self._FENDI_FORCE_FIRE_SCORE

        # --- Normal path ---
        target = self._get_best_target()
        if target is None:
            return None

        try:
            target_health = Agent.GetHealth(target)
        except Exception:
            return None

        if target_health is None:
            return None

        if target_health >= self.REQUIRED_TARGET_HP_FRACTION:
            return None

        return self.score_definition.get_score()

    @override
    def _execute(self, state: BehaviorState) -> Generator[Any, None, BehaviorResult]:
        """
        Cast the shout. Prefers the Fendi override target when it applies so
        the force-fire score in _evaluate and the actual cast target agree.
        """
        target = self._get_fendi_force_fire_target() or self._get_best_target()
        if target is None:
            return BehaviorResult.ACTION_SKIPPED

        result = yield from custom_behavior_helpers.Actions.cast_skill_to_target(self.custom_skill, target)
        return result