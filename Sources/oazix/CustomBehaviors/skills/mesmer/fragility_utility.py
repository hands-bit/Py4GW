from typing import Any, Generator, override

from Py4GWCoreLib import Effects, Range
from Sources.oazix.CustomBehaviors.primitives.behavior_state import BehaviorState
from Sources.oazix.CustomBehaviors.primitives.bus.event_bus import EventBus
from Sources.oazix.CustomBehaviors.primitives.helpers import custom_behavior_helpers
from Sources.oazix.CustomBehaviors.primitives.helpers.behavior_result import BehaviorResult
from Sources.oazix.CustomBehaviors.primitives.helpers.targeting_order import TargetingOrder
from Sources.oazix.CustomBehaviors.primitives.parties.custom_behavior_party import CustomBehaviorParty
from Sources.oazix.CustomBehaviors.primitives.scores.score_static_definition import ScoreStaticDefinition
from Sources.oazix.CustomBehaviors.primitives.skills.custom_skill import CustomSkill
from Sources.oazix.CustomBehaviors.primitives.skills.custom_skill_utility_base import CustomSkillUtilityBase

_CLUSTER_COUNT_RANGE: float = 300.0  # nearby radius used to rank targets by density


class FragilityUtility(CustomSkillUtilityBase):
    """
    Casts Fragility on the densest cluster of un-hexed enemies within spellcast
    range.  Priority score is static (default 94) so it reliably fires right
    after Deep Freeze (95) regardless of cluster size.  Fragility is a
    single-target hex with no AOE range in skill data, so a fixed cluster
    radius is used for target selection only — not for scoring.
    Enemies already under Fragility are excluded to avoid redundant casts.
    """

    def __init__(
        self,
        event_bus: EventBus,
        current_build: list[CustomSkill],
        score_definition: ScoreStaticDefinition = ScoreStaticDefinition(94),
        mana_required_to_cast: int = 10,
        allowed_states: list[BehaviorState] = [BehaviorState.IN_AGGRO],
    ) -> None:
        super().__init__(
            event_bus=event_bus,
            skill=CustomSkill("Fragility"),
            in_game_build=current_build,
            score_definition=score_definition,
            mana_required_to_cast=mana_required_to_cast,
            allowed_states=allowed_states,
        )
        self.score_definition: ScoreStaticDefinition = score_definition

    def _get_lock_key(self, agent_id: int) -> str:
        return f"Fragility_{agent_id}"

    def _get_targets(self) -> list[custom_behavior_helpers.SortableAgentData]:
        """Select best target: densest cluster first, then lowest HP.
        Enemies already hexed with Fragility are excluded."""
        fragility_id = self.custom_skill.skill_id
        return custom_behavior_helpers.Targets.get_all_possible_enemies_ordered_by_priority_raw(
            within_range=Range.Spellcast,
            condition=lambda agent_id: not Effects.HasEffect(agent_id, fragility_id),
            sort_key=(TargetingOrder.AGENT_QUANTITY_WITHIN_RANGE_DESC, TargetingOrder.HP_DESC),
            range_to_count_enemies=_CLUSTER_COUNT_RANGE,
        )

    @override
    def _evaluate(self, current_state: BehaviorState, previously_attempted_skills: list[CustomSkill]) -> float | None:
        targets = self._get_targets()
        if not targets:
            return None
        lock_key = self._get_lock_key(targets[0].agent_id)
        if CustomBehaviorParty().get_shared_lock_manager().is_lock_taken(lock_key):
            return None
        return self.score_definition.get_score()

    @override
    def _execute(self, state: BehaviorState) -> Generator[Any, None, BehaviorResult]:
        enemies = self._get_targets()
        if not enemies:
            return BehaviorResult.ACTION_SKIPPED
        target = enemies[0]

        lock_key = self._get_lock_key(target.agent_id)
        if not CustomBehaviorParty().get_shared_lock_manager().try_aquire_lock(lock_key):
            return BehaviorResult.ACTION_SKIPPED

        try:
            result = yield from custom_behavior_helpers.Actions.cast_skill_to_target(
                self.custom_skill, target_agent_id=target.agent_id
            )
        finally:
            CustomBehaviorParty().get_shared_lock_manager().release_lock(lock_key)
        return result
