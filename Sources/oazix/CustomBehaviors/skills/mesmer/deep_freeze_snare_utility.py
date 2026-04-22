import time
from typing import Any, Generator, override

from Py4GWCoreLib import Routines
from Sources.oazix.CustomBehaviors.primitives.behavior_state import BehaviorState
from Sources.oazix.CustomBehaviors.primitives.bus.event_bus import EventBus
from Sources.oazix.CustomBehaviors.primitives.helpers import custom_behavior_helpers
from Sources.oazix.CustomBehaviors.primitives.helpers.behavior_result import BehaviorResult
from Sources.oazix.CustomBehaviors.primitives.scores.score_per_agent_quantity_definition import ScorePerAgentQuantityDefinition
from Sources.oazix.CustomBehaviors.primitives.skills.custom_skill import CustomSkill
from Sources.oazix.CustomBehaviors.skills.generic.raw_aoe_attack_utility import RawAoeAttackUtility

COOLDOWN_SECONDS = 20.0


class DeepFreezeSnareUtility(RawAoeAttackUtility):
    """
    Casts Deep Freeze at maximum priority (95), then enforces a hard 30s cooldown
    regardless of in-game recharge reductions from items.
    """

    def __init__(self,
                 event_bus: EventBus,
                 current_build: list[CustomSkill],
                 score_definition: ScorePerAgentQuantityDefinition = ScorePerAgentQuantityDefinition(lambda q: 95),
                 mana_required_to_cast: int = 10
                 ) -> None:
        super().__init__(
            event_bus=event_bus,
            skill=CustomSkill("Deep_Freeze"),
            current_build=current_build,
            score_definition=score_definition,
            mana_required_to_cast=mana_required_to_cast,
        )
        self._last_cast_time: float = 0.0

    def _is_on_cooldown(self) -> bool:
        return (time.time() - self._last_cast_time) < COOLDOWN_SECONDS

    @override
    def _evaluate(self, current_state: BehaviorState, previously_attempted_skills: list[CustomSkill]) -> float | None:
        if self._is_on_cooldown():
            return None
        return super()._evaluate(current_state, previously_attempted_skills)

    @override
    def _execute(self, state: BehaviorState) -> Generator[Any, None, BehaviorResult]:
        result = yield from super()._execute(state)
        if result == BehaviorResult.ACTION_PERFORMED:
            if not Routines.Checks.Skills.IsSkillIDReady(self.custom_skill.skill_id):
                self._last_cast_time = time.time()
        return result
