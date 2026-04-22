import ctypes
import time
from dataclasses import dataclass
import inspect
import importlib
import pkgutil
from typing import Callable, Generator, Any, List

from PyAgent import AttributeClass

from Py4GWCoreLib import Routines, Map, Agent, AgentArray, Player
from Py4GWCoreLib.GlobalCache import GLOBAL_CACHE
from Py4GWCoreLib.GlobalCache.SharedMemory import AccountStruct
from Py4GWCoreLib.enums_src.Multiboxing_enums import SharedCommandType
from Py4GWCoreLib.py4gwcorelib_src.Timer import ThrottledTimer


from Sources.oazix.CustomBehaviors.primitives.behavior_state import BehaviorState
from Sources.oazix.CustomBehaviors.primitives.following_behavior_priority import FollowingBehaviorPriority
from Sources.oazix.CustomBehaviors.primitives import constants

from Sources.oazix.CustomBehaviors.primitives.helpers import custom_behavior_helpers
from Sources.oazix.CustomBehaviors.primitives.parties.party_command_contants import PartyCommandConstants
from Sources.oazix.CustomBehaviors.primitives.parties.party_command_handler_manager import PartyCommandHandlerManager
from Sources.oazix.CustomBehaviors.primitives.parties.party_flagging_manager import PartyFlaggingManager
from Sources.oazix.CustomBehaviors.primitives.parties.party_following_manager import PartyFollowingManager
from Sources.oazix.CustomBehaviors.primitives.parties.shared_lock_manager import SharedLockManager
from Sources.oazix.CustomBehaviors.primitives.parties.party_teambuild_manager import PartyTeamBuildManager
from Sources.oazix.CustomBehaviors.primitives.skills.utility_skill_typology import UtilitySkillTypology
from Sources.oazix.CustomBehaviors.primitives.parties.party_command_contants import PartyCommandConstants
from Sources.oazix.CustomBehaviors.primitives.parties.custom_behavior_shared_memory import CustomBehaviorWidgetData, CustomBehaviorWidgetMemoryManager

@dataclass
class PartyData:
    account_email: str
    skillbar_template: str
    
class CustomBehaviorParty:
    _instance = None  # Singleton instance

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(CustomBehaviorParty, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if not self._initialized:
            self._initialized = True
            self._generator_handle = self._handle()
            
            self.party_command_handler_manager = PartyCommandHandlerManager()
            self.party_teambuild_manager = PartyTeamBuildManager()
            self.party_following_manager = PartyFollowingManager()
            self.party_shared_lock_manager = CustomBehaviorWidgetMemoryManager().GetSharedLockManager()
            self.party_flagging_manager = PartyFlaggingManager()

            # Rename GW windows to match custom behavior party names on load
            print("CustomBehaviorParty: Renaming GW windows")
            CustomBehaviorParty().schedule_action(PartyCommandConstants.rename_gw_windows)

            self.throttler = ThrottledTimer(50)
            # Alt-side target pinning happens every _handle iteration (50ms).
            # When a party_custom_target is set, we re-assert it as this alt's
            # selected target so auto-attack follows the priority target between
            # skill casts. Without re-pinning, skill utilities that target
            # non-priority enemies (e.g. Smite Hex picking a hexed trash mob)
            # leave the alt's selected target on the trash after the cast —
            # auto-attack keeps hitting the wrong thing.
            #
            # `ChangeTarget` during an active cast is safe in GW1: the cast
            # completes on its original target, only the UI selection changes.
            # No IsCasting guard is needed.
            #
            # The re-pin only fires when a target is actually pinned by
            # `set_party_custom_target` AND `current_target != pinned`; if
            # nothing is pinned or targets already match, it's a cheap no-op —
            # safe for any bot that doesn't use the mechanism.

    def _handle(self) -> Generator[Any | None, Any | None, None]:
        while True:
            self.party_command_handler_manager.execute_next_step()
            self.__messaging_process()
            self.party_teambuild_manager.act()
            
            # # ------------------------------ Custom party target ------------------------------
            if custom_behavior_helpers.CustomBehaviorHelperParty.is_party_leader():
                if Map.IsExplorable():
                    current_party_target_id = self.get_party_custom_target()
                    if current_party_target_id is not None:
                        # if not Agent.IsValid(current_party_target_id): self.set_party_custom_target(None)
                        if not Agent.IsAlive(current_party_target_id): self.set_party_custom_target(None)

                    players = GLOBAL_CACHE.Party.GetPlayers()
                    for player in players:
                        agent_id = GLOBAL_CACHE.Party.Players.GetAgentIDByLoginNumber(player.login_number)
                        if agent_id == Player.GetAgentID():
                            called_target_id = player.called_target_id
                            if called_target_id != 0:
                                self.set_party_custom_target(called_target_id)
                            break
                else:
                    self.set_party_custom_target(None)
            else:
                # Alt-side target pinning runs every _handle iteration (50ms,
                # matching the outer act() throttle). Two sources of truth:
                #
                # 1. Fendi-zone override (hardcoded L3 anchor): when the alt
                #    is inside the Fendi arena, the correct target is ALWAYS
                #    Soul of Fendi (model 7065) if alive, else Fendi Nin
                #    (model 7064). We check this locally — no dependence on
                #    the torch runner's 500ms pin update cycle, no IPC lag, instant
                #    response to Fendi Nin ↔ Soul phase transitions. This
                #    supersedes the generic pin whenever it applies.
                #
                # 2. Generic party-target pin (fallback): when not in Fendi
                #    zone (or no Fendi form present), re-assert whatever is
                #    pinned in shared memory. Used for trash focus-fire,
                #    torch-split strict-flank targets, etc.
                #
                # `ChangeTarget` only fires when `current_target != target`,
                # so the mechanism is idle unless a real mismatch exists.
                if Map.IsExplorable():
                    fendi_target = self._get_fendi_zone_target_override()
                    try:
                        if fendi_target > 0:
                            # Fendi zone: always snap to the Fendi form AND
                            # actively auto-attack it.
                            #
                            # Why both ChangeTarget + Interact:
                            # - ChangeTarget only updates the UI selection. It
                            #   does NOT redirect an in-progress auto-attack;
                            #   the hero continues swinging at whatever they
                            #   were previously attacking (typically an add
                            #   that drew initial aggro).
                            # - Interact(target, call_target=False) issues the
                            #   attack command — equivalent to pressing the
                            #   spacebar. GW's movement engine will auto-path
                            #   the hero to the target if out of range, which
                            #   is the built-in leash: if Fendi repositions,
                            #   the hero chases instead of picking a closer
                            #   enemy. `call_target=False` suppresses the
                            #   party-callout so 7 alts don't spam it.
                            #
                            # Re-assert cadence: we also re-Interact every
                            # _FENDI_ATTACK_REASSERT_S seconds even when the
                            # target is already selected. If Fendi moved out
                            # of range and auto-attack stopped, the hero needs
                            # the command again to resume pursuit — otherwise
                            # they idle or drift into a different enemy.
                            current_target = Player.GetTargetID()
                            now = time.monotonic()
                            last_reassert = getattr(self, "_fendi_last_reassert", 0.0)
                            last_target = getattr(self, "_fendi_last_target", 0)
                            target_changed = current_target != fendi_target or last_target != fendi_target
                            time_to_reassert = (now - last_reassert) >= self._FENDI_ATTACK_REASSERT_S
                            if target_changed or time_to_reassert:
                                if current_target != fendi_target:
                                    Player.ChangeTarget(fendi_target)
                                Player.Interact(fendi_target, call_target=False)
                                self._fendi_last_reassert = now
                                self._fendi_last_target = fendi_target
                        else:
                            # Outside Fendi zone: generic pin fallback.
                            pinned = self.get_party_custom_target()
                            if pinned is not None and pinned > 0 and Agent.IsAlive(pinned):
                                current_target = Player.GetTargetID()
                                if current_target != pinned:
                                    Player.ChangeTarget(pinned)
                    except Exception:
                        # Never let a target-pin error break the party handler.
                        pass


            # # ------------------------------ Party leader email ------------------------------



                
            yield
    
    def act(self):
        if not self.throttler.IsExpired(): return
        self.throttler.Reset()
        
        if not Routines.Checks.Map.MapValid(): return

        try:
            next(self._generator_handle)
        except StopIteration:
            print(f"CustomBehaviorParty.act is not expected to StopIteration.")
        except Exception as e:
            print(f"CustomBehaviorParty.act is not expected to exit : {e}")

    def schedule_action(self, action_gen: Callable[[], Generator]) -> bool:
        """Schedule a generator action. Returns True if accepted, False if busy."""
        return self.party_command_handler_manager.schedule_action(action_gen)

    def is_ready_for_action(self) -> bool:
        """Check if it's safe to schedule another action."""
        return self.party_command_handler_manager.is_ready_for_action()

    #---

    def __messaging_process(self):

        account_email = Player.GetAccountEmail()
        index, message = GLOBAL_CACHE.ShMem.GetNextMessage(account_email)

        if index == -1 or message is None:
            return

        match message.Command:
            case SharedCommandType.CustomBehaviors:
                action = message.Params[0]
                if action >= 2.5:
                    # Params[0] = 3: use an item by model id.
                    # Params[1] = model_id (e.g. 30209 = Tengu Support Flare,
                    #            30847 = Igneous Summoning Stone).
                    # Finds the first matching item in this client's bags and
                    # uses it. Silent if the item is not present.
                    try:
                        model_id = int(message.Params[1])
                        if model_id <= 0:
                            print(f"[CB Party] UseItem dispatch received invalid model_id={model_id}")
                        else:
                            item_id = GLOBAL_CACHE.Inventory.GetFirstModelID(model_id)
                            if item_id:
                                GLOBAL_CACHE.Inventory.UseItem(item_id)
                                print(f"[CB Party] UseItem model_id={model_id} item_id={item_id} — used")
                            else:
                                print(f"[CB Party] UseItem model_id={model_id} — not in bags, skipped")
                    except Exception as e:
                        print(f"[CB Party] UseItem handler error: {e}")
                elif action >= 1.5:
                    # Params[0] = 2: scan from receiver's own position for a
                    # priority target (monk > non-melee caster > nearest) and
                    # set it as the party called target via shared memory.
                    try:
                        from Py4GWCoreLib import Agent, Range
                        source = Player.GetXY()
                        if source:
                            Targets = custom_behavior_helpers.Targets
                            MONK_PROF = 3
                            radius = Range.Spellcast.value
                            target_id = Targets.get_nearest_or_default_from_enemy_ordered_by_priority_custom_source(
                                source_agent_pos=source,
                                within_range=radius,
                                should_prioritize_party_target=False,
                                condition=lambda aid: Agent.GetProfessions(aid)[0] == MONK_PROF,
                            )
                            if not target_id:
                                target_id = Targets.get_nearest_or_default_from_enemy_ordered_by_priority_custom_source(
                                    source_agent_pos=source,
                                    within_range=radius,
                                    should_prioritize_party_target=False,
                                    condition=lambda aid: not Agent.IsMelee(aid),
                                )
                            if not target_id:
                                target_id = Targets.get_nearest_or_default_from_enemy_ordered_by_priority_custom_source(
                                    source_agent_pos=source,
                                    within_range=radius,
                                    should_prioritize_party_target=False,
                                )
                            if target_id:
                                # Write directly to shared party_custom_target —
                                # all alts read from this via CustomBehavior skills
                                # with should_prioritize_party_target=True
                                self.set_party_custom_target(target_id)
                                try:
                                    Player.ChangeTarget(target_id)
                                except Exception:
                                    pass
                                print(f"[CB Party] Called priority target agent={target_id}")
                    except Exception as e:
                        print(f"[CB Party] Call-target handler error: {e}")
                else:
                    # Params[0] in (0, 1): skill toggle (existing behavior)
                    # ExtraData[0..3]: skill names to toggle (empty string = skip)
                    from Sources.oazix.CustomBehaviors.primitives.custom_behavior_loader import CustomBehaviorLoader
                    enable = action > 0.5
                    loader = CustomBehaviorLoader()
                    if loader.custom_combat_behavior is not None:
                        skills = loader.custom_combat_behavior.custom_skills_in_behavior
                        for i in range(4):
                            skill_name = str(message.ExtraData[i]).strip()
                            if not skill_name:
                                continue
                            for skill in skills:
                                if skill.custom_skill.skill_name == skill_name:
                                    skill.is_enabled = enable
                                    break
                GLOBAL_CACHE.ShMem.MarkMessageAsFinished(account_email, index)

    #---

    def get_shared_lock_manager(self) -> SharedLockManager:
        return self.party_shared_lock_manager
    
    def get_typology_is_enabled(self, skill_typology:UtilitySkillTypology):
        if skill_typology == UtilitySkillTypology.COMBAT:
            return self.get_party_is_combat_enabled()
        if skill_typology == UtilitySkillTypology.CHESTING:
            return self.get_party_is_chesting_enabled()
        if skill_typology == UtilitySkillTypology.FOLLOWING:
            return self.get_party_is_following_enabled()
        if skill_typology == UtilitySkillTypology.LOOTING :
            return self.get_party_is_looting_enabled()
        if skill_typology == UtilitySkillTypology.BLESSING :
            return self.get_party_is_blessing_enabled()
        if skill_typology == UtilitySkillTypology.INVENTORY :
            return self.get_party_is_inventory_enabled()
        return True

    #---

    def get_party_is_enabled(self) -> bool:
        shared_data:CustomBehaviorWidgetData = CustomBehaviorWidgetMemoryManager().GetCustomBehaviorWidgetData()
        return shared_data.is_enabled

    def set_party_is_enabled(self, is_enabled: bool):
        shared_data:CustomBehaviorWidgetData = CustomBehaviorWidgetMemoryManager().GetCustomBehaviorWidgetData()
        CustomBehaviorWidgetMemoryManager().SetCustomBehaviorWidgetData(
            is_enabled=is_enabled,
            is_combat_enabled=shared_data.is_combat_enabled,
            is_looting_enabled=shared_data.is_looting_enabled,
            is_chesting_enabled=shared_data.is_chesting_enabled,
            is_following_enabled=shared_data.is_following_enabled,
            is_blessing_enabled=shared_data.is_blessing_enabled,
            is_inventory_enabled=shared_data.is_inventory_enabled,
            party_target_id=shared_data.party_target_id,
            party_leader_email=shared_data.party_leader_email,
            party_forced_state=shared_data.party_forced_state)

    #---

    def get_party_is_combat_enabled(self) -> bool:
        shared_data:CustomBehaviorWidgetData = CustomBehaviorWidgetMemoryManager().GetCustomBehaviorWidgetData()
        return shared_data.is_combat_enabled

    def set_party_is_combat_enabled(self, is_combat_enabled: bool):
        shared_data:CustomBehaviorWidgetData = CustomBehaviorWidgetMemoryManager().GetCustomBehaviorWidgetData()
        CustomBehaviorWidgetMemoryManager().SetCustomBehaviorWidgetData(
            is_enabled=shared_data.is_enabled,
            is_combat_enabled=is_combat_enabled,
            is_looting_enabled=shared_data.is_looting_enabled,
            is_chesting_enabled=shared_data.is_chesting_enabled,
            is_following_enabled=shared_data.is_following_enabled,
            is_blessing_enabled=shared_data.is_blessing_enabled,
            is_inventory_enabled=shared_data.is_inventory_enabled,
            party_target_id=shared_data.party_target_id,
            party_leader_email=shared_data.party_leader_email,
            party_forced_state=shared_data.party_forced_state)

    #---

    def get_party_is_looting_enabled(self) -> bool:
        shared_data:CustomBehaviorWidgetData = CustomBehaviorWidgetMemoryManager().GetCustomBehaviorWidgetData()
        return shared_data.is_looting_enabled

    def set_party_is_looting_enabled(self, is_looting_enabled: bool):
        shared_data:CustomBehaviorWidgetData = CustomBehaviorWidgetMemoryManager().GetCustomBehaviorWidgetData()
        CustomBehaviorWidgetMemoryManager().SetCustomBehaviorWidgetData(
            is_enabled=shared_data.is_enabled,
            is_combat_enabled=shared_data.is_combat_enabled,
            is_looting_enabled=is_looting_enabled,
            is_chesting_enabled=shared_data.is_chesting_enabled,
            is_following_enabled=shared_data.is_following_enabled,
            is_blessing_enabled=shared_data.is_blessing_enabled,
            is_inventory_enabled=shared_data.is_inventory_enabled,
            party_target_id=shared_data.party_target_id,
            party_leader_email=shared_data.party_leader_email,
            party_forced_state=shared_data.party_forced_state)

    #---

    def get_party_is_chesting_enabled(self) -> bool:
        shared_data:CustomBehaviorWidgetData = CustomBehaviorWidgetMemoryManager().GetCustomBehaviorWidgetData()
        return shared_data.is_chesting_enabled

    def set_party_is_chesting_enabled(self, is_chesting_enabled: bool):
        shared_data:CustomBehaviorWidgetData = CustomBehaviorWidgetMemoryManager().GetCustomBehaviorWidgetData()
        CustomBehaviorWidgetMemoryManager().SetCustomBehaviorWidgetData(
            is_enabled=shared_data.is_enabled,
            is_combat_enabled=shared_data.is_combat_enabled,
            is_looting_enabled=shared_data.is_looting_enabled,
            is_chesting_enabled=is_chesting_enabled,
            is_following_enabled=shared_data.is_following_enabled,
            is_blessing_enabled=shared_data.is_blessing_enabled,
            is_inventory_enabled=shared_data.is_inventory_enabled,
            party_target_id=shared_data.party_target_id,
            party_leader_email=shared_data.party_leader_email,
            party_forced_state=shared_data.party_forced_state)

    #---

    def get_party_is_following_enabled(self) -> bool:
        shared_data:CustomBehaviorWidgetData = CustomBehaviorWidgetMemoryManager().GetCustomBehaviorWidgetData()
        return shared_data.is_following_enabled

    def set_party_is_following_enabled(self, is_following_enabled: bool):
        shared_data:CustomBehaviorWidgetData = CustomBehaviorWidgetMemoryManager().GetCustomBehaviorWidgetData()
        CustomBehaviorWidgetMemoryManager().SetCustomBehaviorWidgetData(
            is_enabled=shared_data.is_enabled,
            is_combat_enabled=shared_data.is_combat_enabled,
            is_looting_enabled=shared_data.is_looting_enabled,
            is_chesting_enabled=shared_data.is_chesting_enabled,
            is_following_enabled=is_following_enabled,
            is_blessing_enabled=shared_data.is_blessing_enabled,
            is_inventory_enabled=shared_data.is_inventory_enabled,
            party_target_id=shared_data.party_target_id,
            party_leader_email=shared_data.party_leader_email,
            party_forced_state=shared_data.party_forced_state)

    #---

    def get_party_is_blessing_enabled(self) -> bool:
        shared_data:CustomBehaviorWidgetData = CustomBehaviorWidgetMemoryManager().GetCustomBehaviorWidgetData()
        return shared_data.is_blessing_enabled

    def set_party_is_blessing_enabled(self, is_blessing_enabled: bool):
        shared_data:CustomBehaviorWidgetData = CustomBehaviorWidgetMemoryManager().GetCustomBehaviorWidgetData()
        CustomBehaviorWidgetMemoryManager().SetCustomBehaviorWidgetData(
            is_enabled=shared_data.is_enabled,
            is_combat_enabled=shared_data.is_combat_enabled,
            is_looting_enabled=shared_data.is_looting_enabled,
            is_chesting_enabled=shared_data.is_chesting_enabled,
            is_following_enabled=shared_data.is_following_enabled,
            is_blessing_enabled=is_blessing_enabled,
            is_inventory_enabled=shared_data.is_inventory_enabled,
            party_target_id=shared_data.party_target_id,
            party_leader_email=shared_data.party_leader_email,
            party_forced_state=shared_data.party_forced_state)

    #---

    def get_party_is_inventory_enabled(self) -> bool:
        shared_data:CustomBehaviorWidgetData = CustomBehaviorWidgetMemoryManager().GetCustomBehaviorWidgetData()
        return shared_data.is_inventory_enabled

    def set_party_is_inventory_enabled(self, is_inventory_enabled: bool):
        shared_data:CustomBehaviorWidgetData = CustomBehaviorWidgetMemoryManager().GetCustomBehaviorWidgetData()
        CustomBehaviorWidgetMemoryManager().SetCustomBehaviorWidgetData(
            is_enabled=shared_data.is_enabled,
            is_combat_enabled=shared_data.is_combat_enabled,
            is_looting_enabled=shared_data.is_looting_enabled,
            is_chesting_enabled=shared_data.is_chesting_enabled,
            is_following_enabled=shared_data.is_following_enabled,
            is_blessing_enabled=shared_data.is_blessing_enabled,
            is_inventory_enabled=is_inventory_enabled,
            party_target_id=shared_data.party_target_id,
            party_leader_email=shared_data.party_leader_email,
            party_forced_state=shared_data.party_forced_state)

    #---

    def get_party_forced_state(self) -> BehaviorState|None:
        shared_data:CustomBehaviorWidgetData = CustomBehaviorWidgetMemoryManager().GetCustomBehaviorWidgetData()
        result = BehaviorState(shared_data.party_forced_state) if shared_data.party_forced_state is not None else None
        return result

    def set_party_forced_state(self, state: BehaviorState | None):
        shared_data:CustomBehaviorWidgetData = CustomBehaviorWidgetMemoryManager().GetCustomBehaviorWidgetData()
        CustomBehaviorWidgetMemoryManager().SetCustomBehaviorWidgetData(
            is_enabled=shared_data.is_enabled,
            is_combat_enabled=shared_data.is_combat_enabled,
            is_looting_enabled=shared_data.is_looting_enabled,
            is_chesting_enabled=shared_data.is_chesting_enabled,
            is_following_enabled=shared_data.is_following_enabled,
            is_blessing_enabled=shared_data.is_blessing_enabled,
            is_inventory_enabled=shared_data.is_inventory_enabled,
            party_target_id=shared_data.party_target_id,
            party_leader_email=shared_data.party_leader_email,
            party_forced_state=state.value if state is not None else None)

    #---

    # ── Fendi-zone target override ────────────────────────────────────
    # Hardcoded Shards of Orr L3 constants. Centralized so the anchor zone
    # check is consistent across callers (RoJ/FinishHim predicates, the
    # Fendi Phase Monitor, and this target override).
    _FENDI_ZONE_MAP_ID: int    = 583
    _FENDI_ZONE_ANCHOR_X: float = -16022.9
    _FENDI_ZONE_ANCHOR_Y: float = 17889.9
    _FENDI_ZONE_RADIUS_SQ: float = 3500.0 ** 2
    _FENDI_NIN_MODEL_ID: int    = 7064
    _SOUL_OF_FENDI_MODEL_ID: int = 7065
    # Re-issue the attack command this often (seconds) to keep heroes pursuing
    # Fendi when he repositions. Any value between ~1.5 and 3 is reasonable:
    # too short spams network traffic, too long leaves heroes idle if the
    # attack drops. 2s balances responsiveness with packet economy.
    _FENDI_ATTACK_REASSERT_S: float = 2.0

    def _get_fendi_zone_target_override(self) -> int:
        """Return an alt's forced target when inside the Fendi arena.

        Returns a valid agent_id if this alt should target a Fendi form right
        now (Soul of Fendi preferred, Fendi Nin fallback), or 0 if the
        override doesn't apply (wrong map, outside anchor radius, no Fendi
        forms alive).

        Purely local — reads this alt's own map / position / agent array.
        No shared memory, no IPC, no dependence on the torch runner's state. Instant
        response to phase transitions.
        """
        try:
            if Map.GetMapID() != self._FENDI_ZONE_MAP_ID:
                return 0
            pos = Player.GetXY()
            if not pos:
                return 0
            dx = pos[0] - self._FENDI_ZONE_ANCHOR_X
            dy = pos[1] - self._FENDI_ZONE_ANCHOR_Y
            if dx * dx + dy * dy > self._FENDI_ZONE_RADIUS_SQ:
                return 0
            # Scan for a Fendi form. Soul of Fendi takes priority — it's the
            # real damage window. Fendi Nin is the fallback (always present
            # between Soul phases).
            fendi_nin_id = 0
            for agent_id in AgentArray.GetEnemyArray():
                if not Agent.IsAlive(agent_id):
                    continue
                model_id = Agent.GetModelID(agent_id)
                if model_id == self._SOUL_OF_FENDI_MODEL_ID:
                    return agent_id   # Soul takes priority — return immediately
                if model_id == self._FENDI_NIN_MODEL_ID and fendi_nin_id == 0:
                    fendi_nin_id = agent_id
            return fendi_nin_id
        except Exception:
            return 0

    def get_party_custom_target(self) -> int | None:
        shared_data:CustomBehaviorWidgetData = CustomBehaviorWidgetMemoryManager().GetCustomBehaviorWidgetData()
        return shared_data.party_target_id

    def set_party_custom_target(self, target: int | None):
        shared_data:CustomBehaviorWidgetData = CustomBehaviorWidgetMemoryManager().GetCustomBehaviorWidgetData()
        CustomBehaviorWidgetMemoryManager().SetCustomBehaviorWidgetData(
            is_enabled=shared_data.is_enabled,
            is_combat_enabled=shared_data.is_combat_enabled,
            is_looting_enabled=shared_data.is_looting_enabled,
            is_chesting_enabled=shared_data.is_chesting_enabled,
            is_following_enabled=shared_data.is_following_enabled,
            is_blessing_enabled=shared_data.is_blessing_enabled,
            is_inventory_enabled=shared_data.is_inventory_enabled,
            party_target_id=target,
            party_leader_email=shared_data.party_leader_email,
            party_forced_state=shared_data.party_forced_state)

    #---

    @staticmethod
    def get_party_leader_email() -> str | None:
        shared_data:CustomBehaviorWidgetData = CustomBehaviorWidgetMemoryManager().GetCustomBehaviorWidgetData()
        return shared_data.party_leader_email

    def set_party_leader_email(self, leader_email: str | None):
        shared_data:CustomBehaviorWidgetData = CustomBehaviorWidgetMemoryManager().GetCustomBehaviorWidgetData()
        CustomBehaviorWidgetMemoryManager().SetCustomBehaviorWidgetData(
            is_enabled=shared_data.is_enabled,
            is_combat_enabled=shared_data.is_combat_enabled,
            is_looting_enabled=shared_data.is_looting_enabled,
            is_chesting_enabled=shared_data.is_chesting_enabled,
            is_following_enabled=shared_data.is_following_enabled,
            is_blessing_enabled=shared_data.is_blessing_enabled,
            is_inventory_enabled=shared_data.is_inventory_enabled,
            party_target_id=shared_data.party_target_id,
            party_leader_email=leader_email,
            party_forced_state=shared_data.party_forced_state)

    #---

    def get_party_following_behavior(self) -> FollowingBehaviorPriority | None:
        return self.party_following_manager.party_following_behavior

    def set_party_following_behavior_priority(self, behavior: FollowingBehaviorPriority | None):
        """Set the party following behavior and apply the preset configuration to all accounts"""
        if behavior is None:
            # If None, just set the enum without applying preset
            self.party_following_manager.party_following_behavior = behavior
        else:
            # Apply the preset configuration to all accounts
            self.party_following_manager.set_party_following_behavior_state(behavior)