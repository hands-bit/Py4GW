import functools
import os
import time
from typing import override

from Py4GWCoreLib import GLOBAL_CACHE, Agent, Map, Player
from Py4GWCoreLib.Py4GWcorelib import ThrottledTimer
from Py4GWCoreLib.py4gwcorelib_src.Console import ConsoleLog
from Sources.oazix.CustomBehaviors.primitives.behavior_state import BehaviorState
from Sources.oazix.CustomBehaviors.primitives.scores.score_per_agent_quantity_definition import ScorePerAgentQuantityDefinition
from Sources.oazix.CustomBehaviors.primitives.scores.score_static_definition import ScoreStaticDefinition
from Sources.oazix.CustomBehaviors.primitives.skillbars.custom_behavior_base_utility import CustomBehaviorBaseUtility
from Sources.oazix.CustomBehaviors.primitives.skills.custom_skill import CustomSkill
from Sources.oazix.CustomBehaviors.primitives.skills.custom_skill_utility_base import CustomSkillUtilityBase
from Sources.oazix.CustomBehaviors.skills.plugins.preconditions.should_wait_for_effect import ShouldWaitForEffect
from Sources.oazix.CustomBehaviors.skills.plugins.preconditions.should_wait_for_heroic_refrain import ShouldWaitForHeroicRefrain
from Sources.oazix.CustomBehaviors.skills.common.by_urals_hammer_utility import ByUralsHammerUtility
from Sources.oazix.CustomBehaviors.skills.common.finish_him_utility import FinishHimUtility
from Sources.oazix.CustomBehaviors.skills.common.i_am_unstoppable_utility import IAmUnstoppableUtility
from Sources.oazix.CustomBehaviors.skills.generic.keep_self_effect_up_utility import KeepSelfEffectUpUtility
from Sources.oazix.CustomBehaviors.skills.generic.raw_aoe_attack_utility import RawAoeAttackUtility
from Sources.oazix.CustomBehaviors.skills.common.ebon_vanguard_assassin_support_utility import EbonVanguardAssassinSupportUtility
from Sources.oazix.CustomBehaviors.skills.monk.ymlad_standalone_utility import YmladStandaloneUtility
from Sources.oazix.CustomBehaviors.skills.monk.castigation_signet_utility import CastigationSignetUtility
from Sources.oazix.CustomBehaviors.skills.monk.ray_of_judgment_ymlawd_combo_utility import RayOfJudgmentYmladwComboUtility
from Sources.oazix.CustomBehaviors.skills.monk.smite_hex_utility import SmiteHexUtility
from Sources.oazix.CustomBehaviors.skills.monk.smite_condition_utility import SmiteConditionUtility
from Sources.oazix.CustomBehaviors.skills.mesmer.accumulated_pain_utility import AccumulatedPainUtility
from Sources.oazix.CustomBehaviors.skills.mesmer.cry_of_frustration_utility import CryOfFrustrationUtility
from Sources.oazix.CustomBehaviors.skills.mesmer.unnatural_signet_utility import UnnaturalSignetUtility
from Sources.oazix.CustomBehaviors.skills.mesmer.mistrust_utility import MistrustUtility
from Sources.oazix.CustomBehaviors.skills.mesmer.overload_utility import OverloadUtility
from Sources.oazix.CustomBehaviors.skills.monk.judges_insight_utility import JudgesInsightUtility
from Sources.oazix.CustomBehaviors.skills.common.ebon_battle_standard_of_wisdom_utility import EbonBattleStandardOfWisdom
from Sources.oazix.CustomBehaviors.skills.common.great_dwarf_weapon_utility import GreatDwarfWeaponUtility
from Sources.oazix.CustomBehaviors.skills.paragon.fall_back_utility import FallBackUtility


class MonkSmite_UtilitySkillBar(CustomBehaviorBaseUtility):

    # Faster evaluation cadence so Cry of Frustration can catch short-cast skills.
    # Overrides class-level 300ms default in CustomBehaviorBaseUtility.
    compute_throttler = ThrottledTimer(100)

    def __init__(self):
        super().__init__()
        in_game_build = list(self.skillbar_management.get_in_game_build().values())

        # Fendi Nin has two phases — the "Fendi Nin" form (7064) and the
        # "Soul of Fendi Nin" form (7065). Both are the same boss and both
        # are valid RoJ targets. RoJ will NOT fire on arena adds / minions —
        # we want every recharge spent on the kill-bottleneck boss.
        # During brief phase-transition windows (one form dying before the
        # next is alive/targetable) RoJ's _evaluate returns None and other
        # gated skills (Overload etc.) may fire; this is acceptable.
        _FENDI_MODEL_IDS:      frozenset[int] = frozenset({7064, 7065})
        _SOO_LVL3_MAP_ID:      int            = 583
        _FENDI_ANCHOR_X:       float          = -16022.9
        _FENDI_ANCHOR_Y:       float          = 17889.9
        _FENDI_RADIUS_SQ:      float          = 3500.0 ** 2

        def roj_predicate(agent_id: int) -> bool:
            # Outside L3 / outside Fendi arena: no restriction, RoJ targets
            # any valid enemy (standard cluster scoring applies).
            if Map.GetMapID() != _SOO_LVL3_MAP_ID:
                return True
            pos = Player.GetXY()
            if not pos:
                return True
            dx = pos[0] - _FENDI_ANCHOR_X
            dy = pos[1] - _FENDI_ANCHOR_Y
            if dx * dx + dy * dy > _FENDI_RADIUS_SQ:
                return True
            # Inside Fendi arena: only the Fendi Nin boss (either phase).
            return Agent.GetModelID(agent_id) in _FENDI_MODEL_IDS

        # core skills
        self.smite_hex_utility: CustomSkillUtilityBase = SmiteHexUtility(event_bus=self.event_bus, current_build=in_game_build, score_definition=ScoreStaticDefinition(60))
        self.smite_condition_utility: CustomSkillUtilityBase = SmiteConditionUtility(event_bus=self.event_bus, current_build=in_game_build, score_definition=ScoreStaticDefinition(60))
        self.castigation_signet_utility: CustomSkillUtilityBase = CastigationSignetUtility(event_bus=self.event_bus, current_build=in_game_build, score_definition=ScoreStaticDefinition(55))
        self.ray_of_judgment_utility: CustomSkillUtilityBase = RayOfJudgmentYmladwComboUtility(event_bus=self.event_bus, current_build=in_game_build, custom_agent_targeting_predicate=roj_predicate)

        self.ymlad_utility: CustomSkillUtilityBase = YmladStandaloneUtility(event_bus=self.event_bus, current_build=in_game_build, roj_combo=self.ray_of_judgment_utility)

        self.cry_of_frustration_utility: CustomSkillUtilityBase = CryOfFrustrationUtility(event_bus=self.event_bus, current_build=in_game_build, score_definition=ScoreStaticDefinition(91))
        # EVAS leads: high base score so it fires before RoJ on engage.
        # Long EVAS recharge means RoJ still dominates the rest of the fight.
        self.ebon_vanguard_assassin_support_utility: CustomSkillUtilityBase = EbonVanguardAssassinSupportUtility(event_bus=self.event_bus, current_build=in_game_build, score_definition=ScoreStaticDefinition(95))
        # Opportunistic Deep Wound proc — only fires when target has 2+ hexes.
        # Score 62 places it just below EVAS base (63) so it never displaces
        # the main rotation.
        self.accumulated_pain_utility: CustomSkillUtilityBase = AccumulatedPainUtility(event_bus=self.event_bus, current_build=in_game_build, score_definition=ScoreStaticDefinition(62))
        # Finish Him! — opportunistic execute. Only fires on enemies < 50% HP within Spellcast range.
        # Score 75 sits between Cry of Frustration (91) and Accumulated Pain (62) — strong but not displacing key utility casts.
        # Reuses the same Fendi-room predicate as RoJ: in the Fendi anchor zone on L3 it ONLY targets
        # Fendi Nin / Soul of Fendi (boss models 7064, 7065) and ignores adds — keeps execute pressure on the boss.
        self.finish_him_utility: CustomSkillUtilityBase = FinishHimUtility(event_bus=self.event_bus, current_build=in_game_build, score_definition=ScoreStaticDefinition(74), custom_agent_targeting_predicate=roj_predicate)
        # Unnatural Signet — 0-energy signet, AoE damage proc. Default scoring tiers itself by enemy density:
        # 70 when 3+ enemies in AoE range, 40 when ≤2. Targets hexed/enchanted foes first.
        self.unnatural_signet_utility: CustomSkillUtilityBase = UnnaturalSignetUtility(event_bus=self.event_bus, current_build=in_game_build)

        # Physical-bar skills — wired explicitly so they use their purpose-built
        # logic (density targeting, interrupt detection, buff-configurator, etc.)
        # instead of falling through to AutoCombatUtility. All library defaults.
        # Mistrust: density-tiered (76 in dense packs, 40 in sparse). Dense-pack
        # preempts Overload/FinishHim; sparse yields so small fights use the
        # single-target tools.
        self.mistrust_utility: CustomSkillUtilityBase = MistrustUtility(
            event_bus=self.event_bus,
            current_build=in_game_build,
            score_definition=ScorePerAgentQuantityDefinition(lambda n: 76 if n >= 3 else 40 if n <= 2 else 0),
        )
        # Overload: interrupt-only. Rapid interrupt-firing is desired (big AoE
        # payoff when interrupting mid-cast in dense packs); the hex-spread
        # fallback is what caused energy waste by firing on any unhexed foe.
        # Suppress hex-spread by replacing get_hex_spread_targets with a no-op
        # so _evaluate's hex branch never returns a score.
        self.overload_utility: CustomSkillUtilityBase = OverloadUtility(
            event_bus=self.event_bus,
            current_build=in_game_build,
            interrupt_score_definition=ScoreStaticDefinition(75),
        )
        self.overload_utility.get_hex_spread_targets = lambda: []  # type: ignore[method-assign]
        self.judges_insight_utility: CustomSkillUtilityBase = JudgesInsightUtility(event_bus=self.event_bus, current_build=in_game_build)
        self.ebon_battle_standard_of_wisdom_utility: CustomSkillUtilityBase = EbonBattleStandardOfWisdom(event_bus=self.event_bus, current_build=in_game_build)
        self.great_dwarf_weapon_utility: CustomSkillUtilityBase = GreatDwarfWeaponUtility(event_bus=self.event_bus, current_build=in_game_build)

        # ──────────────────────────────────────────────────────────────
        # RoJ energy-reservation gate (temporary, revertible).
        # Spec: protect RoJ from being starved. Per-cycle reservation:
        #   spendable = E + future_regen(T, BiP) - ROJ_COST
        # Block any utility whose cost > spendable (Gate A).
        # Force RoJ to score 99 when ready+payable, UNLESS EVAS is also
        # ready+payable (engage priority) (Gate B).
        # To revert: delete from here to the end of __init__.
        # ──────────────────────────────────────────────────────────────
        self._install_roj_energy_gate()

    # ── RoJ energy gate ─────────────────────────────────────────────
    _ROJ_COST = 10
    _EVAS_COST = 10
    _BASE_PIPS = 4                 # monk_smite baseline
    _BIP_BONUS_PIPS = 7            # Blood is Power adds +7 pips
    _PIP_ENERGY_PER_SEC = 1.0 / 3.0
    # Gate A tolerance: allow a skill through if cost exceeds spendable by no
    # more than this many energy. Tolerance=0 is strict (original behavior,
    # guarantees no RoJ starvation). Tolerance=N allows skills that would
    # starve RoJ by up to N energy — RoJ still fires when ready but may be
    # delayed by ~N/pip_rate seconds waiting for regen to catch up. Trade-off:
    # more opportunistic skill fires (esp. Finish Him executes) in exchange
    # for occasional brief RoJ delays.
    _GATE_A_TOLERANCE = 2

    # Energy costs for Gate A, keyed by GW skill name (as CustomSkill expects).
    # Any skill on the bar matching one of these keys gets gated — including
    # skills that aren't explicitly wired as utilities (they go through the
    # base class's generic/AutoCombat wrapping at final-list time).
    # 0-cost entries (signets, adrenaline) are skipped from gating.
    _GATE_A_COSTS_BY_NAME = {
        # explicitly-wired utilities
        "You_Move_Like_a_Dwarf": 10,
        "Ebon_Vanguard_Assassin_Support": 10,
        "Cry_of_Frustration": 5,
        "Accumulated_Pain": 15,
        "Finish_Him": 10,
        "Smite_Hex": 5,
        "Smite_Condition": 5,
        # autonomous / physical-bar skills (not wired, but on the bar)
        "Mistrust": 10,
        "Ebon_Battle_Standard_of_Wisdom": 10,
        "Judges_Insight": 10,
        "Overload": 5,
        "Great_Dwarf_Weapon": 10,
        "Air_of_Superiority": 5,
        # 0-cost (documented, skipped)
        # "Unnatural_Signet": 0,
    }

    def _install_roj_energy_gate(self):
        # Cache BIP skill ID once.
        try:
            self._bip_skill_id = GLOBAL_CACHE.Skill.GetID("Blood_is_Power")
        except Exception:
            self._bip_skill_id = 0

        # Log file init (append-only; operator-deletable between tuning sessions).
        try:
            logs_dir = os.path.join(os.getcwd(), "logs")
            os.makedirs(logs_dir, exist_ok=True)
            self._roj_gate_log_path = os.path.join(logs_dir, "roj_gate.log")
        except Exception:
            self._roj_gate_log_path = None

        # Resolve skill names -> ids. Build id->(name, cost) map for Gate A.
        self._gate_a_by_id: dict[int, tuple[str, int]] = {}
        for name, cost in self._GATE_A_COSTS_BY_NAME.items():
            if cost <= 0:
                continue
            try:
                sid = GLOBAL_CACHE.Skill.GetID(name)
            except Exception:
                sid = 0
            if sid:
                self._gate_a_by_id[sid] = (name, cost)

        self._roj_skill_id = self.ray_of_judgment_utility.custom_skill.skill_id
        self._gate_installed_ids: set[int] = set()  # avoid double-wrapping

    @override
    def get_skills_final_list(self):
        final_list = super().get_skills_final_list()
        # Wrap every utility on the bar whose skill id is in our gate map,
        # plus RoJ with Gate B. Idempotent — base class caches the list, but
        # we guard anyway.
        for utility in final_list:
            try:
                sid = utility.custom_skill.skill_id
            except Exception:
                continue
            if sid in self._gate_installed_ids:
                continue
            if sid == self._roj_skill_id:
                self._wrap_gate_b(utility)
                self._gate_installed_ids.add(sid)
            elif sid in self._gate_a_by_id:
                name, cost = self._gate_a_by_id[sid]
                self._wrap_gate_a(utility, name, cost)
                self._gate_installed_ids.add(sid)
        return final_list

    # Dedup window: within this many seconds, repeated same-state decisions
    # for the same channel are suppressed. Only state-transitions log.
    _LOG_DEDUP_SECONDS = 2.0

    def _roj_gate_log(self, msg: str, channel: str, state: str) -> None:
        """Console + file. Dedups repeated same-state decisions per channel.

        channel: 'roj' or a skill name for Gate A blocks.
        state:   compact state label; when unchanged within _LOG_DEDUP_SECONDS,
                 the line is suppressed to cut 100ms-eval spam.

        Plain text (no brackets) to avoid GW chat-parser collisions.
        """
        now = time.monotonic()
        last = getattr(self, "_last_gate_event", None)
        if last is None:
            self._last_gate_event = {}
            last = self._last_gate_event
        prev = last.get(channel)
        if prev is not None:
            prev_time, prev_state = prev
            if prev_state == state and (now - prev_time) < self._LOG_DEDUP_SECONDS:
                last[channel] = (now, state)  # refresh timestamp; keep suppressing
                return
        last[channel] = (now, state)

        try:
            ConsoleLog("ROJ_GATE", msg)
        except Exception:
            pass
        if self._roj_gate_log_path:
            try:
                ts = time.strftime("%H:%M:%S")
                with open(self._roj_gate_log_path, "a", encoding="utf-8") as f:
                    f.write(f"{ts} {msg}\n")
            except Exception:
                pass

    def _read_gate_inputs(self):
        """Snapshot E, T_ms (RoJ recharge), bip_ms (BIP time remaining), evas_ms."""
        pid = Player.GetAgentID()
        try:
            energy_frac = Agent.GetEnergy(pid)
            max_e = Agent.GetMaxEnergy(pid)
            E = float(energy_frac) * float(max_e)
        except Exception:
            E = 0.0
        try:
            roj_slot = self.ray_of_judgment_utility.custom_skill.skill_slot
            T_ms = float(GLOBAL_CACHE.SkillBar.GetSkillData(roj_slot).get_recharge)
        except Exception:
            T_ms = 0.0
        try:
            evas_slot = self.ebon_vanguard_assassin_support_utility.custom_skill.skill_slot
            evas_ms = float(GLOBAL_CACHE.SkillBar.GetSkillData(evas_slot).get_recharge)
        except Exception:
            evas_ms = 0.0
        try:
            bip_ms = float(GLOBAL_CACHE.Effects.GetEffectTimeRemaining(pid, self._bip_skill_id)) if self._bip_skill_id else 0.0
            # Sanitize: Effects.GetEffectTimeRemaining returns a uint32 sentinel
            # (~4.29e9 ms = max uint32) when the effect is not active. Without
            # this check, the spend formula would think BIP was active for
            # billions of seconds, inflating future_regen to infinity and
            # making Gate A pass every skill during RoJ's recharge window —
            # silently breaking the reservation. BIP lasts 10s max; clamp any
            # value outside a realistic range back to 0.
            if bip_ms < 0.0 or bip_ms > 15000.0:
                bip_ms = 0.0
        except Exception:
            bip_ms = 0.0
        return E, T_ms, bip_ms, evas_ms

    def _spendable(self, E: float, T_ms: float, bip_ms: float) -> float:
        """E + future regen over T seconds (BiP-aware) - ROJ_COST."""
        T = T_ms / 1000.0
        bip = max(0.0, bip_ms / 1000.0)
        t_bip = min(T, bip)
        t_post = max(0.0, T - bip)
        bip_rate = (self._BASE_PIPS + self._BIP_BONUS_PIPS) * self._PIP_ENERGY_PER_SEC
        base_rate = self._BASE_PIPS * self._PIP_ENERGY_PER_SEC
        future_regen = t_bip * bip_rate + t_post * base_rate
        return E + future_regen - self._ROJ_COST

    def _wrap_gate_a(self, utility, label: str, cost: int):
        original = utility._evaluate

        @functools.wraps(original)
        def gated(current_state, previously_attempted_skills):
            natural = original(current_state, previously_attempted_skills)
            if natural is None:
                return None
            E, T_ms, bip_ms, _ = self._read_gate_inputs()
            # NOTE: do NOT short-circuit when T_ms == 0. At the exact moment
            # RoJ becomes castable, the reservation must STILL hold — otherwise
            # instant-cast skills (YMLAD, signets) race RoJ's 1s cast window
            # and drain the 10e reservation before RoJ commits.
            # spend formula correctly handles T=0: spend = E - ROJ_COST.
            spend = self._spendable(E, T_ms, bip_ms)
            # Tolerance: allow skills through if they'd starve RoJ by no more
            # than _GATE_A_TOLERANCE energy. Lets opportunistic skills (Finish
            # Him executes, near-threshold Overload/CoF) fire in exchange for
            # potential brief RoJ delay while regen catches up.
            if cost - spend > self._GATE_A_TOLERANCE:
                self._roj_gate_log(
                    f"block {label} cost={cost} spend={spend:.1f} E={E:.0f} T={T_ms/1000:.1f}s bip={bip_ms/1000:.1f}s",
                    channel=f"block:{label}",
                    state="blocked",
                )
                return None
            return natural

        utility._evaluate = gated

    def _wrap_gate_b(self, roj_utility):
        original = roj_utility._evaluate

        @functools.wraps(original)
        def gated(current_state, previously_attempted_skills):
            natural = original(current_state, previously_attempted_skills)
            if natural is None:
                return None
            E, T_ms, bip_ms, evas_ms = self._read_gate_inputs()
            # Only force-fire if RoJ is actually ready and we can pay.
            if T_ms > 0 or E < self._ROJ_COST:
                return natural
            # EVAS-hold conditions: only yield to EVAS if it will actually fire.
            # Three gates, all must pass:
            #   1. EVAS off cooldown (evas_ms <= 0)
            #   2. We can afford both EVAS and the next RoJ (E >= 20) —
            #      otherwise Gate A would block EVAS and RoJ would stall
            #      while lower-priority skills (Overload 75 > RoJ natural 65)
            #      slip in.
            #   3. EVAS's OWN _evaluate returns a non-None score. We call the
            #      original evaluator (not just _get_targets) so lock-manager
            #      conflicts and other mode-specific checks are respected. If
            #      another smite monk already has EVAS's lock on the same
            #      target, EVAS.evaluate returns None — holding for it would
            #      stall RoJ for no reason.
            evas_off_cd = evas_ms <= 0
            evas_affordable = E >= self._EVAS_COST + self._ROJ_COST
            evas_will_fire = False
            if evas_off_cd and evas_affordable:
                try:
                    evas_util = self.ebon_vanguard_assassin_support_utility
                    evas_score = evas_util._evaluate(current_state, previously_attempted_skills)
                    evas_will_fire = evas_score is not None
                except Exception:
                    evas_will_fire = False
            evas_ready_and_payable = evas_off_cd and evas_affordable and evas_will_fire
            if evas_ready_and_payable:
                # Return max(natural, 93) so RoJ still beats everything EXCEPT
                # EVAS (95) during the hold. Without this bump, RoJ natural
                # (65 for lone enemy, 80 for pair, 92 for cluster) loses to
                # CoF (91), Mistrust (76), etc. — causing "RoJ passed over"
                # during evas_hold on low-density fights. 93 is tuned to:
                #   - Beat CoF 91, Mistrust 76, all other alt utilities
                #   - Lose to EVAS 95 (engage priority preserved)
                #   - Preserve natural when natural > 93 (RoJ cluster score 92
                #     stays unchanged from 93, still loses to EVAS)
                hold_score = max(natural, 93.0)
                self._roj_gate_log(
                    f"roj ready evas_hold E={E:.0f} natural={natural:.1f} hold_score={hold_score:.1f}",
                    channel="roj",
                    state="evas_hold",
                )
                return hold_score
            self._roj_gate_log(
                f"roj force-fire E={E:.0f} bip={bip_ms/1000:.1f}s score=99",
                channel="roj",
                state="force-fire",
            )
            return 99.0

        roj_utility._evaluate = gated

    @property
    @override
    def custom_skills_in_behavior(self) -> list[CustomSkillUtilityBase]:
        return [
            self.ray_of_judgment_utility,
            self.ymlad_utility,
            self.ebon_vanguard_assassin_support_utility,
            self.cry_of_frustration_utility,
            self.accumulated_pain_utility,
            self.finish_him_utility,
            self.castigation_signet_utility,
            self.unnatural_signet_utility,
            self.mistrust_utility,
            self.overload_utility,
            self.judges_insight_utility,
            self.ebon_battle_standard_of_wisdom_utility,
            self.great_dwarf_weapon_utility,
            self.smite_hex_utility,
            self.smite_condition_utility,
        ]

    @property
    @override
    def skills_required_in_behavior(self) -> list[CustomSkill]:
        return [
            self.ray_of_judgment_utility.custom_skill,
        ]

