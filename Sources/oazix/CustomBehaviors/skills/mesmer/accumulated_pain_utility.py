from typing import Any, Generator, override

from Py4GWCoreLib import GLOBAL_CACHE, Range
from Sources.oazix.CustomBehaviors.primitives.behavior_state import BehaviorState
from Sources.oazix.CustomBehaviors.primitives.bus.event_bus import EventBus
from Sources.oazix.CustomBehaviors.primitives.helpers import custom_behavior_helpers
from Sources.oazix.CustomBehaviors.primitives.helpers.behavior_result import BehaviorResult
from Sources.oazix.CustomBehaviors.primitives.helpers.targeting_order import TargetingOrder
from Sources.oazix.CustomBehaviors.primitives.parties.custom_behavior_party import CustomBehaviorParty
from Sources.oazix.CustomBehaviors.primitives.scores.score_static_definition import ScoreStaticDefinition
from Sources.oazix.CustomBehaviors.primitives.skills.custom_skill import CustomSkill
from Sources.oazix.CustomBehaviors.primitives.skills.custom_skill_utility_base import CustomSkillUtilityBase


_HEX_REQUIRED_FOR_DEEP_WOUND = 2  # Accumulated Pain needs 2+ hexes on target for Deep Wound


class AccumulatedPainUtility(CustomSkillUtilityBase):
    """
    Accumulated Pain — opportunistic Deep Wound proc.

    Only fires when an enemy in Spellcast range has >= 2 active hexes so the
    Deep Wound condition is applied. Picks the enemy with the lowest HP among
    valid targets to maximize kill pressure.
    """

    def __init__(
        self,
        event_bus: EventBus,
        current_build: list[CustomSkill],
        score_definition: ScoreStaticDefinition = ScoreStaticDefinition(62),
        mana_required_to_cast: int = 5,
        allowed_states: list[BehaviorState] = [BehaviorState.IN_AGGRO],
    ) -> None:
        super().__init__(
            event_bus=event_bus,
            skill=CustomSkill("Accumulated_Pain"),
            in_game_build=current_build,
            score_definition=score_definition,
            mana_required_to_cast=mana_required_to_cast,
            allowed_states=allowed_states,
        )
        self.score_definition: ScoreStaticDefinition = score_definition

    def _count_hexes_on(self, agent_id: int) -> int:
        # Hex classification lives in the global skill flags table; EffectType
        # itself doesn't expose an is_hex attribute.
        try:
            effects = GLOBAL_CACHE.Effects.GetEffects(agent_id)
        except Exception:
            return 0
        if not effects:
            return 0
        flags = GLOBAL_CACHE.Skill.Flags
        count = 0
        for effect in effects:
            try:
                skill_id = int(getattr(effect, "skill_id", 0))
                if skill_id > 0 and flags.IsHex(skill_id):
                    count += 1
            except Exception:
                continue
        return count

    def _get_targets(self) -> list[custom_behavior_helpers.SortableAgentData]:
        return custom_behavior_helpers.Targets.get_all_possible_enemies_ordered_by_priority_raw(
            within_range=Range.Spellcast,
            condition=lambda agent_id: self._count_hexes_on(agent_id) >= _HEX_REQUIRED_FOR_DEEP_WOUND,
            sort_key=(TargetingOrder.HP_ASC,),
        )

    def _get_lock_key(self, agent_id: int) -> str:
        return f"AccumulatedPain_{agent_id}"

    @override
    def _evaluate(self, current_state: BehaviorState, previously_attempted_skills: list[CustomSkill]) -> float | None:
        targets = self._get_targets()
        if not targets:
            return None
        target_id = targets[0].agent_id
        lock_key = self._get_lock_key(target_id)
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
