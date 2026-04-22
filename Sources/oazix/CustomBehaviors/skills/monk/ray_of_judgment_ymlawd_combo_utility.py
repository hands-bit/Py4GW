from typing import Any, Callable, Generator, override

from Py4GWCoreLib import GLOBAL_CACHE, Party, Routines, Player
from Sources.oazix.CustomBehaviors.primitives.behavior_state import BehaviorState
from Sources.oazix.CustomBehaviors.primitives.bus.event_bus import EventBus
from Sources.oazix.CustomBehaviors.primitives.helpers.custom_behavior_helpers import Helpers
from Sources.oazix.CustomBehaviors.primitives.helpers.behavior_result import BehaviorResult
from Sources.oazix.CustomBehaviors.primitives.scores.score_per_agent_quantity_definition import ScorePerAgentQuantityDefinition
from Sources.oazix.CustomBehaviors.primitives.skills.custom_skill import CustomSkill
from Sources.oazix.CustomBehaviors.skills.generic.raw_aoe_attack_utility import RawAoeAttackUtility


class RayOfJudgmentYmladwComboUtility(RawAoeAttackUtility):
    """
    Casts Ray of Judgment and immediately chains You Move Like A Dwarf! afterward.
    YMLAWD is a shout with no cast time, so it fires in the same window as RoJ's aftercast.
    YMLAWD is only chained if it is ready — RoJ still casts normally if YMLAWD is on cooldown.
    """

    def __init__(self,
                 event_bus: EventBus,
                 current_build: list[CustomSkill],
                 # Score per cluster size. Lone-enemy score bumped 65 → 85 so
                 # RoJ beats Overload (75) and other mid-tier utilities during
                 # its recharge window. Prevents single-target fights from
                 # silently favoring interrupts over the main damage spell when
                 # Gate B's force-fire path can't engage (T>0 or no cluster).
                 # EVAS (95) still beats lone-RoJ 85; CoF (91) also still wins
                 # so interrupts on casting mobs remain prioritized.
                 score_definition: ScorePerAgentQuantityDefinition = ScorePerAgentQuantityDefinition(
                     lambda enemy_qte: 92 if enemy_qte >= 3 else 85 if enemy_qte >= 2 else 85),
                 mana_required_to_cast: int = 10,
                 custom_agent_targeting_predicate: Callable[[int], bool] | None = None
                 ) -> None:
        super().__init__(
            event_bus=event_bus,
            skill=CustomSkill("Ray_of_Judgment"),
            current_build=current_build,
            score_definition=score_definition,
            mana_required_to_cast=mana_required_to_cast,
            custom_agent_targeting_predicate=custom_agent_targeting_predicate,
        )
        self._ymlawd_skill: CustomSkill = CustomSkill("You_Move_Like_a_Dwarf")

    @override
    def _execute(self, state: BehaviorState) -> Generator[Any, None, BehaviorResult]:
        enemies = self._get_targets()
        if not enemies:
            return BehaviorResult.ACTION_SKIPPED

        # Spread-fire: when multiple smite monks are in the party, each one
        # picks a DIFFERENT enemy from the density-sorted candidate list so
        # RoJ circles cover more area per salvo instead of 4-way overlapping
        # the same point.
        #
        # One RoJ from a tuned smite monk reliably clears its ~200u radius,
        # so 4-way overlap is ~4× overkill on the priority target while
        # cluster outliers survive untouched. By indexing into `enemies` with
        # this hero's party login_number, each monk naturally targets a
        # distinct high-priority enemy when the cluster is large enough.
        #
        # Degenerate cases handled automatically by modulo wraparound:
        #   - Single-enemy (Fendi): all heroes → enemies[0]. Focus-fire.
        #   - Cluster smaller than party: controlled overlap, not 4-way pileup.
        #   - Cluster ≥ 4 smite monks: zero overlap, max area coverage.
        try:
            login_number = Party.Players.GetLoginNumberByAgentID(Player.GetAgentID())
            idx = login_number % len(enemies)
        except Exception:
            idx = 0  # fallback to focus-fire if login lookup fails
        target = enemies[idx]

        if not Routines.Checks.Skills.IsSkillSlotReady(self.custom_skill.skill_slot):
            yield
            return BehaviorResult.ACTION_SKIPPED

        Player.ChangeTarget(target.agent_id)
        yield from Helpers.wait_for(20)

        Routines.Sequential.Skills.CastSkillSlot(self.custom_skill.skill_slot)

        ymlad_wait_ms = 0
        if Routines.Checks.Skills.IsSkillIDReady(self._ymlawd_skill.skill_id):
            ymlad_wait_ms = 350
            yield from Helpers.wait_for(ymlad_wait_ms)
            Routines.Sequential.Skills.CastSkillSlot(self._ymlawd_skill.skill_slot)

        activation_ms = GLOBAL_CACHE.Skill.Data.GetActivation(self.custom_skill.skill_id) * 1000 * 0.76
        remaining_ms = max(activation_ms - ymlad_wait_ms - 400, 0)
        yield from Helpers.wait_for(remaining_ms)
        return BehaviorResult.ACTION_PERFORMED
