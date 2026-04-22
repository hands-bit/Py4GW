from typing import Any, Generator, override

from Py4GWCoreLib import GLOBAL_CACHE, Agent, Range
from Py4GWCoreLib.py4gwcorelib_src.Console import ConsoleLog
from Sources.oazix.CustomBehaviors.primitives.behavior_state import BehaviorState
from Sources.oazix.CustomBehaviors.primitives.bus.event_bus import EventBus
from Sources.oazix.CustomBehaviors.primitives.helpers import custom_behavior_helpers
from Sources.oazix.CustomBehaviors.primitives.helpers.behavior_result import BehaviorResult
from Sources.oazix.CustomBehaviors.primitives.helpers.sortable_agent_data import SortableAgentData
from Sources.oazix.CustomBehaviors.primitives.helpers.targeting_order import TargetingOrder
from Sources.oazix.CustomBehaviors.primitives.parties.custom_behavior_party import CustomBehaviorParty
from Sources.oazix.CustomBehaviors.primitives.scores.score_static_definition import ScoreStaticDefinition
from Sources.oazix.CustomBehaviors.primitives.skills.custom_skill import CustomSkill
from Sources.oazix.CustomBehaviors.primitives.skills.custom_skill_utility_base import CustomSkillUtilityBase


class OverloadUtility(CustomSkillUtilityBase):

    def __init__(self,
                event_bus: EventBus,
                current_build: list[CustomSkill],
                interrupt_score_definition: ScoreStaticDefinition = ScoreStaticDefinition(60),
                hex_spread_score_definition: ScoreStaticDefinition = ScoreStaticDefinition(45),
                mana_required_to_cast: int = 5,
                allowed_states: list[BehaviorState] = [BehaviorState.IN_AGGRO],
        ) -> None:

        super().__init__(
            event_bus=event_bus,
            skill=CustomSkill("Overload"),
            in_game_build=current_build,
            score_definition=interrupt_score_definition,
            mana_required_to_cast=mana_required_to_cast,
            allowed_states=allowed_states)

        self.interrupt_score_definition: ScoreStaticDefinition = interrupt_score_definition
        self.hex_spread_score_definition: ScoreStaticDefinition = hex_spread_score_definition

    def _get_lock_key(self, agent_id: int) -> str:
        return f"Overload_{agent_id}"

    def _get_first_unlocked_target(self, targets: list[SortableAgentData]) -> SortableAgentData | None:
        lock_manager = CustomBehaviorParty().get_shared_lock_manager()
        for target in targets:
            if not lock_manager.is_lock_taken(self._get_lock_key(target.agent_id)):
                return target
        return None

    # Minimum base activation (seconds) for a skill to be worth interrupting.
    # Filters out instants and signets (activation ~0). Set to 0.50s as a
    # compromise: catches 0.75s-and-above skills (CoF's own gate, Meteor/Fireball
    # at 2s, monk heals at 0.75-1s) while excluding ¾s-on-the-margin interrupt
    # targets. Tune up if we see too many late-fire interrupts landing post-cast,
    # down if we want Overload to chase the shorter Ritualist weapon spells.
    _MIN_BASE_ACTIVATION_S = 0.50

    def detect_casting_enemies(self) -> list[SortableAgentData]:
        """Find enemies currently casting an interruptible skill.

        Uses the same polling pattern as CoF — `Agent.IsCasting` plus a minimum
        base-activation filter. Previously this ran against
        `CombatEvents.get_cast_time_remaining` for precise remaining-cast-time
        gating, but that API returned 0 for all enemies on our alt clients
        (observed across 3 smite monks × 42 min of combat = 0 fires, 0 blocks).
        The event feed wasn't populated on these clients; the polling check is
        known-working.

        Trade-off: filters by BASE skill activation, not live remaining time,
        so Overload can fire on an enemy 90% through a long cast and land post-
        cast for marginal damage. This is strictly better than the broken
        event-based version firing zero times. If post-cast landings are
        common in the new data, we can add a simple `first-seen` timestamp
        cache per agent to approximate remaining time.
        """
        def is_interruptible_cast(agent_id: int) -> bool:
            try:
                if not Agent.IsCasting(agent_id):
                    return False
                casting_skill_id = Agent.GetCastingSkillID(agent_id)
                if not casting_skill_id:
                    return False
                activation = GLOBAL_CACHE.Skill.Data.GetActivation(casting_skill_id)
                return activation >= self._MIN_BASE_ACTIVATION_S
            except Exception:
                return False

        targets = custom_behavior_helpers.Targets.get_all_possible_enemies_ordered_by_priority_raw(
            within_range=Range.Spellcast,
            condition=is_interruptible_cast,
            sort_key=(TargetingOrder.AGENT_QUANTITY_WITHIN_RANGE_DESC, TargetingOrder.CASTER_THEN_MELEE),
            range_to_count_enemies=GLOBAL_CACHE.Skill.Data.GetAoERange(self.custom_skill.skill_id)
        )
        return targets

    def get_hex_spread_targets(self) -> list[SortableAgentData]:
        targets = custom_behavior_helpers.Targets.get_all_possible_enemies_ordered_by_priority_raw(
            within_range=Range.Spellcast,
            condition=lambda agent_id: not Agent.IsHexed(agent_id),
            sort_key=(TargetingOrder.AGENT_QUANTITY_WITHIN_RANGE_DESC, TargetingOrder.HP_DESC),
            range_to_count_enemies=GLOBAL_CACHE.Skill.Data.GetAoERange(self.custom_skill.skill_id)
        )
        return targets

    @override
    def _evaluate(self, current_state: BehaviorState, previously_attempted_skills: list[CustomSkill]) -> float | None:
        casting_target = self._get_first_unlocked_target(self.detect_casting_enemies())
        if casting_target is not None:
            return self.interrupt_score_definition.get_score()

        hex_target = self._get_first_unlocked_target(self.get_hex_spread_targets())
        if hex_target is not None:
            return self.hex_spread_score_definition.get_score()

        return None

    @override
    def _execute(self, state: BehaviorState) -> Generator[Any | None, Any | None, BehaviorResult]:
        lock_manager = CustomBehaviorParty().get_shared_lock_manager()

        casting_target = self._get_first_unlocked_target(self.detect_casting_enemies())
        if casting_target is not None:
            lock_key = self._get_lock_key(casting_target.agent_id)
            if not lock_manager.try_aquire_lock(lock_key):
                return BehaviorResult.ACTION_SKIPPED

            # Fire-time debug log: which skill were we interrupting, and how
            # long was its base activation? After switching off the broken
            # CombatEvents remaining-ms API, the only gate is base activation
            # >= _MIN_BASE_ACTIVATION_S. Lines with low activation values are
            # informational only; we can tune the threshold if post-cast
            # landings become common.
            try:
                casting_skill_id = Agent.GetCastingSkillID(casting_target.agent_id)
                activation = GLOBAL_CACHE.Skill.Data.GetActivation(casting_skill_id)
                ConsoleLog(
                    "Overload",
                    f"interrupt fire on agent {casting_target.agent_id} "
                    f"(skill_id={casting_skill_id} activation={activation:.2f}s)",
                )
            except Exception:
                pass

            try:
                result = yield from custom_behavior_helpers.Actions.cast_skill_to_target(self.custom_skill, target_agent_id=casting_target.agent_id)
            finally:
                lock_manager.release_lock(lock_key)
            return result

        hex_target = self._get_first_unlocked_target(self.get_hex_spread_targets())
        if hex_target is not None:
            lock_key = self._get_lock_key(hex_target.agent_id)
            if not lock_manager.try_aquire_lock(lock_key):
                return BehaviorResult.ACTION_SKIPPED

            # Hex-spread path — should be unreachable on monk_smite (the
            # skillbar neuters it via `get_hex_spread_targets = lambda: []`).
            # If this line ever appears in logs on a smite monk, the neuter
            # didn't apply — treat it as a regression signal.
            try:
                ConsoleLog(
                    "Overload",
                    f"hex-spread fire on agent {hex_target.agent_id} "
                    f"(non-interrupt path — verify skillbar neuter)",
                )
            except Exception:
                pass

            try:
                result = yield from custom_behavior_helpers.Actions.cast_skill_to_target(self.custom_skill, target_agent_id=hex_target.agent_id)
            finally:
                lock_manager.release_lock(lock_key)
            return result

        return BehaviorResult.ACTION_SKIPPED
