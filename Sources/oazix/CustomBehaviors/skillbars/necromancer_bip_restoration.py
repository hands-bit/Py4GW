from typing import override

from Sources.oazix.CustomBehaviors.primitives.scores.score_per_agent_quantity_definition import ScorePerAgentQuantityDefinition
from Sources.oazix.CustomBehaviors.primitives.scores.score_per_health_gravity_definition import ScorePerHealthGravityDefinition
from Sources.oazix.CustomBehaviors.primitives.scores.score_static_definition import ScoreStaticDefinition
from Sources.oazix.CustomBehaviors.primitives.skillbars.custom_behavior_base_utility import CustomBehaviorBaseUtility
from Sources.oazix.CustomBehaviors.primitives.skills.custom_skill import CustomSkill
from Sources.oazix.CustomBehaviors.primitives.skills.custom_skill_utility_base import CustomSkillUtilityBase
from Sources.oazix.CustomBehaviors.skills.common.breath_of_the_great_dwarf_utility import BreathOfTheGreatDwarfUtility
from Sources.oazix.CustomBehaviors.skills.common.by_urals_hammer_utility import ByUralsHammerUtility
from Sources.oazix.CustomBehaviors.skills.common.ebon_battle_standard_of_wisdom_utility import EbonBattleStandardOfWisdom
from Sources.oazix.CustomBehaviors.skills.common.ebon_vanguard_assassin_support_utility import EbonVanguardAssassinSupportUtility
from Sources.oazix.CustomBehaviors.skills.generic.raw_simple_attack_utility import RawSimpleAttackUtility
from Sources.oazix.CustomBehaviors.skills.following.follow_party_leader_utility import FollowPartyLeaderUtility
from Sources.oazix.CustomBehaviors.skills.following.midline_follow_party_leader_utility import MidlineFollowPartyLeaderUtility
from Sources.oazix.CustomBehaviors.skills.following.spread_during_combat_utility import SpreadDuringCombatUtility
from Py4GWCoreLib import Agent, Player, Range
from Sources.oazix.CustomBehaviors.skills.common.great_dwarf_weapon_utility import GreatDwarfWeaponUtility
from Sources.oazix.CustomBehaviors.skills.common.i_am_unstoppable_utility import IAmUnstoppableUtility
from Sources.oazix.CustomBehaviors.skills.common.you_are_all_weaklings_utility import YouAreAllWeaklingsUtility
from Sources.oazix.CustomBehaviors.skills.generic.generic_resurrection_utility import GenericResurrectionUtility
from Sources.oazix.CustomBehaviors.skills.necromancer.blood_bond_utility import BloodBondUtility
from Sources.oazix.CustomBehaviors.skills.necromancer.blood_is_power_utility import BloodIsPowerUtility
from Sources.oazix.CustomBehaviors.skills.paragon.fall_back_utility import FallBackUtility
from Sources.oazix.CustomBehaviors.skills.ritualist.mend_body_and_soul_utility import MendBodyAndSoulUtility
from Sources.oazix.CustomBehaviors.skills.ritualist.life_utility import LifeUtility
from Sources.oazix.CustomBehaviors.skills.ritualist.protective_was_kaolai_utility import ProtectiveWasKaolaiUtility
from Sources.oazix.CustomBehaviors.skills.ritualist.soothing_memories_utility import SoothingMemoriesUtility
from Sources.oazix.CustomBehaviors.skills.ritualist.spirit_light_utility import SpiritLightUtility
from Sources.oazix.CustomBehaviors.skills.ritualist.spirit_transfer_utility import SpiritTransferUtility
from Sources.oazix.CustomBehaviors.skills.ritualist.mending_grip_utility import MendingGripUtility
from Sources.oazix.CustomBehaviors.skills.ritualist.resilient_weapon_utility import ResilientWeaponUtility
from Sources.oazix.CustomBehaviors.skills.ritualist.wielders_boon_utility import WieldersBoonUtility


class NecromancerBipRestoration_UtilitySkillBar(CustomBehaviorBaseUtility):

    def __init__(self):
        super().__init__()
        in_game_build = list(self.skillbar_management.get_in_game_build().values())

        # core skills
        # BIP tuned for SoO smite-monk support. Defaults (33 score + 0.55 HP
        # floor + 0.40 target-energy threshold) left the monks energy-starved
        # in Fendi fights. Fendi-fight log analysis showed <10% BIP uptime
        # across 3 monk heroes despite Gate 1 (HP) and Gate 2 (target energy)
        # being loosened — because BIP necro's own heal utilities
        # (Spirit Light, PWK, MBAS, Soothing Memories use ScorePerHealthGravity
        # which scales with ally damage, typically 30-60 in active combat)
        # were outscoring BIP at 33 almost every tick.
        #
        #   sacrifice_life_limit_percent 0.55 → 0.35 (Lever A):
        #     Necro can BIP at 68% HP (was 88%). Tolerates combat damage.
        #   required_target_mana_lower_than_percent 0.40 → 0.50 (Lever B):
        #     BIP fires when a monk drops below 50% energy (was 40%).
        #   score_definition 33 → 55 (Lever C):
        #     Beats low-urgency heals on the BIP necro's bar during chip
        #     damage (the common combat state). Still loses to real heal
        #     emergencies (HP < 50% → heal scores 60-80+).
        #
        # Scoped to this skillbar — other BIP users (dark_aura_support,
        # minion_master) keep their default, safer values.
        self.blood_is_power_utility: CustomSkillUtilityBase = BloodIsPowerUtility(
            event_bus=self.event_bus,
            current_build=in_game_build,
            score_definition=ScoreStaticDefinition(55),
            sacrifice_life_limit_percent=0.35,
            required_target_mana_lower_than_percent=0.50,
        )
        self.spirit_light_utility: CustomSkillUtilityBase = SpiritLightUtility(event_bus=self.event_bus, current_build=in_game_build, score_definition=ScorePerHealthGravityDefinition(8))
        self.mend_body_and_soul_utility: CustomSkillUtilityBase = MendBodyAndSoulUtility(event_bus=self.event_bus, current_build=in_game_build, score_definition=ScorePerHealthGravityDefinition(7))
        self.soothing_memories_utility: CustomSkillUtilityBase = SoothingMemoriesUtility(event_bus=self.event_bus, current_build=in_game_build, score_definition=ScorePerHealthGravityDefinition(6))
        self.protective_was_kaolai_utility: CustomSkillUtilityBase = ProtectiveWasKaolaiUtility(event_bus=self.event_bus, current_build=in_game_build, score_definition=ScorePerHealthGravityDefinition(7))

        # optional
        self.life_utility: CustomSkillUtilityBase = LifeUtility(event_bus=self.event_bus, current_build=in_game_build, score_definition=ScorePerHealthGravityDefinition(5))
        self.spirit_transfer_utility: CustomSkillUtilityBase = SpiritTransferUtility(event_bus=self.event_bus, current_build=in_game_build, score_definition=ScorePerHealthGravityDefinition(9))
        self.great_dwarf_weapon_utility: CustomSkillUtilityBase = GreatDwarfWeaponUtility(event_bus=self.event_bus, current_build=in_game_build, score_definition=ScoreStaticDefinition(30))
        self.breath_of_the_great_dwarf_utility: CustomSkillUtilityBase = BreathOfTheGreatDwarfUtility(event_bus=self.event_bus, current_build=in_game_build, score_definition=ScorePerHealthGravityDefinition(9))
        self.blood_bond_utility: CustomSkillUtilityBase = BloodBondUtility(event_bus=self.event_bus, current_build=in_game_build, score_definition=ScorePerAgentQuantityDefinition(lambda enemy_qte: 25 if enemy_qte >= 2 else 0), mana_required_to_cast=15)
        self.wielders_boon_utility: CustomSkillUtilityBase = WieldersBoonUtility(event_bus=self.event_bus, current_build=in_game_build)
        self.resilient_weapon_utility: CustomSkillUtilityBase = ResilientWeaponUtility(event_bus=self.event_bus, current_build=in_game_build)
        self.mending_grip_utility: CustomSkillUtilityBase = MendingGripUtility(event_bus=self.event_bus, current_build=in_game_build)

        # common
        self.ebon_vanguard_assassin_support: CustomSkillUtilityBase = EbonVanguardAssassinSupportUtility(event_bus=self.event_bus, score_definition=ScoreStaticDefinition(71), current_build=in_game_build, mana_required_to_cast=15)
        self.ebon_battle_standard_of_wisdom: CustomSkillUtilityBase = EbonBattleStandardOfWisdom(event_bus=self.event_bus, score_definition= ScorePerAgentQuantityDefinition(lambda agent_qte: 80 if agent_qte >= 3 else 60 if agent_qte <= 2 else 40), current_build=in_game_build, mana_required_to_cast=18)
        self.you_are_all_weaklings_utility: CustomSkillUtilityBase = YouAreAllWeaklingsUtility(event_bus=self.event_bus, current_build=in_game_build)

        # You Move Like a Dwarf! — knockdown shout. Critical: shouts use Earshot (1012u), but
        # RawSimpleAttackUtility's default targeting filter is Spellcast (1248u). The 236u gap
        # caused this hero to repeatedly target enemies just outside shout range, fail the cast,
        # re-evaluate, re-target, fail again — locking the skillbar in a tight loop. The custom
        # predicate below restricts targets to Earshot from the caster, so we only score when
        # there's a real shoutable enemy. Slightly tighter than nominal Earshot (×0.9) for
        # margin against target movement during the cast animation.
        _ymlad_max_range_sq = (Range.Earshot.value * 0.9) ** 2
        def _ymlad_in_shout_range(agent_id: int) -> bool:
            try:
                tx, ty = Agent.GetXY(agent_id)
                px, py = Player.GetXY()
                dx, dy = tx - px, ty - py
                return (dx * dx + dy * dy) <= _ymlad_max_range_sq
            except Exception:
                return False

        self.ymlad_utility: CustomSkillUtilityBase = RawSimpleAttackUtility(
            event_bus=self.event_bus,
            skill=CustomSkill("You_Move_Like_a_Dwarf"),
            current_build=in_game_build,
            score_definition=ScoreStaticDefinition(75),
            mana_required_to_cast=0,
            custom_agent_targeting_predicate=_ymlad_in_shout_range,
        )


    @property
    @override
    def custom_skills_in_behavior(self) -> list[CustomSkillUtilityBase]:
        return [
            self.blood_is_power_utility,
            self.great_dwarf_weapon_utility,
            self.breath_of_the_great_dwarf_utility,
            self.spirit_light_utility,
            self.mend_body_and_soul_utility,
            self.soothing_memories_utility,
            self.protective_was_kaolai_utility,
            self.life_utility,
            self.spirit_transfer_utility,
            self.blood_bond_utility,
            self.wielders_boon_utility,
            self.resilient_weapon_utility,
            self.mending_grip_utility,
            self.ebon_vanguard_assassin_support,
            self.ebon_battle_standard_of_wisdom,
            self.you_are_all_weaklings_utility,
            self.ymlad_utility,
        ]

    @property
    @override
    def additional_autonomous_skills(self) -> list[CustomSkillUtilityBase]:
        # Two swaps for BIP positioning:
        #
        # 1) Default FollowPartyLeaderUtility (loose, ~624u IN_AGGRO leash) → MidlineFollowPartyLeaderUtility
        #    (~250u). Tightens out-of-combat / pre-aggro positioning.
        #
        # 2) Drop SpreadDuringCombatUtility entirely. That utility uses ally-repulsion + enemy-repulsion
        #    vector fields IN_AGGRO and once the hero is "close enough" the leader-attraction force
        #    disengages, letting repulsion shove her back to the rear. For a support caster (BIP, shouts,
        #    PwK) we want her to stay packed with the leader, not spread out for AoE-safety.
        base = [
            s for s in super().additional_autonomous_skills
            if not isinstance(s, FollowPartyLeaderUtility) and not isinstance(s, SpreadDuringCombatUtility)
        ]
        base.append(MidlineFollowPartyLeaderUtility(event_bus=self.event_bus, current_build=self.in_game_build))
        return base

    @property
    @override
    def skills_required_in_behavior(self) -> list[CustomSkill]:
        return [
            self.blood_is_power_utility.custom_skill,
        ]
