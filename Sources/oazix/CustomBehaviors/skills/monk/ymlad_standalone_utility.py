from typing import Any, Generator, override

from Py4GWCoreLib import GLOBAL_CACHE
from Sources.oazix.CustomBehaviors.primitives.behavior_state import BehaviorState
from Sources.oazix.CustomBehaviors.primitives.bus.event_bus import EventBus
from Sources.oazix.CustomBehaviors.primitives.helpers.behavior_result import BehaviorResult
from Sources.oazix.CustomBehaviors.primitives.scores.score_static_definition import ScoreStaticDefinition
from Sources.oazix.CustomBehaviors.primitives.skills.custom_skill import CustomSkill
from Sources.oazix.CustomBehaviors.skills.generic.raw_simple_attack_utility import RawSimpleAttackUtility
from Sources.oazix.CustomBehaviors.skills.monk.ray_of_judgment_ymlawd_combo_utility import RayOfJudgmentYmladwComboUtility

YMLAD_RECHARGE_MS = 8000  # ~8s after 20% consumable reduction


class YmladStandaloneUtility(RawSimpleAttackUtility):
    """
    Casts You Move Like A Dwarf! once between RoJ casts, but only if RoJ has
    more than YMLAD's recharge time remaining — ensuring YMLAD is always available
    for the next RoJ chain regardless of weapon procs.
    """

    def __init__(self,
                 event_bus: EventBus,
                 current_build: list[CustomSkill],
                 roj_combo: RayOfJudgmentYmladwComboUtility,
                 score_definition: ScoreStaticDefinition = ScoreStaticDefinition(90),
                 ) -> None:
        super().__init__(
            event_bus=event_bus,
            skill=CustomSkill("You_Move_Like_a_Dwarf"),
            current_build=current_build,
            score_definition=score_definition,
            mana_required_to_cast=0,
        )
        self._roj_combo = roj_combo

    def _roj_recharge_remaining_ms(self) -> float:
        skill_data = GLOBAL_CACHE.SkillBar.GetSkillData(self._roj_combo.custom_skill.skill_slot)
        if skill_data is None:
            return 0.0
        return skill_data.get_recharge

    @override
    def _evaluate(self, current_state: BehaviorState, previously_attempted_skills: list[CustomSkill]) -> float | None:
        if self._roj_recharge_remaining_ms() <= YMLAD_RECHARGE_MS:
            return None
        return super()._evaluate(current_state, previously_attempted_skills)
