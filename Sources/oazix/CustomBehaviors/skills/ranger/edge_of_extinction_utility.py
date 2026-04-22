from typing import Any, Generator, override

from Py4GWCoreLib import Agent, Range
from Py4GWCoreLib.enums import SpiritModelID
from Sources.oazix.CustomBehaviors.primitives.behavior_state import BehaviorState
from Sources.oazix.CustomBehaviors.primitives.bus.event_bus import EventBus
from Sources.oazix.CustomBehaviors.primitives.helpers import custom_behavior_helpers
from Sources.oazix.CustomBehaviors.primitives.helpers.behavior_result import BehaviorResult
from Sources.oazix.CustomBehaviors.primitives.scores.score_static_definition import ScoreStaticDefinition
from Sources.oazix.CustomBehaviors.primitives.skills.custom_skill import CustomSkill
from Sources.oazix.CustomBehaviors.primitives.skills.custom_skill_utility_base import CustomSkillUtilityBase

_EOE_MODEL_ID: int = SpiritModelID.EDGE_OF_EXTINCTION.value


class EdgeOfExtinctionUtility(CustomSkillUtilityBase):
    """
    Casts Edge of Extinction whenever no living instance of the spirit exists
    within spellcast range.  Follows the same cast logic as the protective
    spirits on the Soul Twisting bar, without any Soul Twisting dependency
    since Edge of Extinction is a nature ritual.
    """

    def __init__(
        self,
        event_bus: EventBus,
        current_build: list[CustomSkill],
        score_definition: ScoreStaticDefinition = ScoreStaticDefinition(70),
        mana_required_to_cast: int = 10,
        allowed_states: list[BehaviorState] = [BehaviorState.IN_AGGRO, BehaviorState.CLOSE_TO_AGGRO],
    ) -> None:
        super().__init__(
            event_bus=event_bus,
            skill=CustomSkill("Edge_of_Extinction"),
            in_game_build=current_build,
            score_definition=score_definition,
            mana_required_to_cast=mana_required_to_cast,
            allowed_states=allowed_states,
        )
        self.score_definition: ScoreStaticDefinition = score_definition

    @override
    def _evaluate(self, current_state: BehaviorState, previously_attempted_skills: list[CustomSkill]) -> float | None:
        spirit_exists = custom_behavior_helpers.Resources.is_spirit_exist(
            within_range=Range.Spellcast,
            condition=lambda agent_id: Agent.GetPlayerNumber(agent_id) == _EOE_MODEL_ID,
        )
        if spirit_exists:
            return None
        return self.score_definition.get_score()

    @override
    def _execute(self, state: BehaviorState) -> Generator[Any, None, BehaviorResult]:
        result = yield from custom_behavior_helpers.Actions.cast_skill(self.custom_skill)
        return result
