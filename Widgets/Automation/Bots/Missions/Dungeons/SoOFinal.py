import os
import json
import traceback
from pathlib import Path
from typing import Callable, Generator, Optional
import random, time, math
import inspect
import PyInventory
import PyImGui
import Py4GW
from Py4GWCoreLib import (
    Agent,
    Botting,
    ConsoleLog,
    Effects,
    GLOBAL_CACHE,
    Map,
    Party,
    Player,
    Quest,
    Range,
    Routines,
    SharedCommandType,
    AgentArray,
    IniHandler,
)
from Py4GWCoreLib.py4gwcorelib_src.WidgetManager import get_widget_handler as _get_wh
from Sources.oazix.CustomBehaviors.primitives.botting.botting_helpers import BottingHelpers
from Sources.oazix.CustomBehaviors.primitives.custom_behavior_loader import CustomBehaviorLoader
from Sources.oazix.CustomBehaviors.primitives.helpers import custom_behavior_helpers
from Sources.oazix.CustomBehaviors.primitives.parties.custom_behavior_party import CustomBehaviorParty
from Py4GWCoreLib.routines_src.Yield import Utils
from Py4GWCoreLib.routines_src.Yield import Yield

# ==================== CONFIGURATION ====================
BOT_NAME = "Shards of Orr Final"
MODULE_ICON = "Textures\\Module_Icons\\Shards of Orr.png"
MODULE_TAGS = ["Bone Dragon Staff", "BDS", "Bones", "Asura", "Drawf", "Rep"]

# Widgets you want to force-manage at startup.
# Edit these lists to choose which widgets to enable/disable.
WIDGETS_TO_ENABLE: tuple[str, ...] = (
    "LootManager",
    "CustomBehaviors",
    "ResurrectionScroll",
    "Return to outpost on defeat",
)
WIDGETS_TO_DISABLE: tuple[str, ...] = ()
_ALT_ONLY_DISABLE_WIDGETS: tuple[str, ...] = (BOT_NAME,)

# ==================== CONFIG ====================
_SETTINGS_SECTION     = "Settings"
_STATS_SECTION        = "Statistics"
_BDS_DROPS_SECTION    = "BDS Drops"
_BDS_SNAPSHOT_SECTION = "BDS Snapshot"
_BDS_RUN_SECTION      = "BDS Run"
_GB_DROPS_SECTION     = "GB Drops"
_GB_SNAPSHOT_SECTION  = "GB Snapshot"
_GB_RUN_SECTION       = "GB Run"
_MERCHANT_SECTION     = "BDS Merchant"
_ALT_SALVAGE_SECTION  = "BDS Alt Salvage Kits"
_CHAR_NAMES_SECTION   = "Character Names"
_ACCOUNTS_SECTION     = "Accounts"

_settings_ini_path     = os.path.join(Py4GW.Console.get_projects_path(), "Widgets", "Config", f"{BOT_NAME}.ini")
_settings_ini_rel_path = os.path.join("Widgets", "Config", f"{BOT_NAME}.ini")  # short form for IPC (fits in 64-char ExtraData)
os.makedirs(os.path.dirname(_settings_ini_path), exist_ok=True)
_settings_ini  = IniHandler(_settings_ini_path)
_settings_loaded: bool = False
_save_requested: bool  = False

# ==================== SETTINGS ====================
_use_hard_mode:      bool = True
_randomize_district: bool = True

_FIXED_ID_KITS_TARGET          = 3
_FIXED_SALVAGE_KITS_TARGET     = 10
_ALT_SALVAGE_TRIGGER_THRESHOLD = 2
_ALT_SALVAGE_POLL_TIMEOUT_MS   = 200
_ALT_SALVAGE_POLL_MAX_TOTAL_MS = 10_000
_BDS_IPC_POLL_TIMEOUT_MS       = 200
_BDS_IPC_POLL_MAX_TOTAL_MS     = 10_000
_merchant_enabled:                    bool = False
_merchant_id_kits_target:             int  = _FIXED_ID_KITS_TARGET
_merchant_salvage_kits_target:        int  = _FIXED_SALVAGE_KITS_TARGET
_inventory_slots_threshold:           int  = 4
_merchant_store_consumable_materials: bool = False
_merchant_sell_materials:             bool = False
_merchant_sell_rare_mats:             bool = False
_merchant_buy_ectos:                  bool = False
_merchant_ecto_threshold:             int  = 800_000
_DEFAULT_ALT_SETTLE_WAIT_MS    = 2000
_MAX_ALT_SETTLE_WAIT_MS        = 5000
_merchant_alt_wait_ms:                int  = _DEFAULT_ALT_SETTLE_WAIT_MS
_POST_RETURN_TO_ARBOR_SETTLE_MS = 4000
_POST_WIDGET_REENABLE_SETTLE_MS = 2500

# Named timing constants
DIALOG_SETTLE_MS:          int = 4000
NPC_INTERACT_SETTLE_MS:    int = 1200
POST_MAP_CHANGE_SETTLE_MS: int = 2000
COMBAT_POLL_INTERVAL_MS:   int = 500

# ==================== BDS STATISTICS ====================
BDS_MODEL_IDS    = list(range(1987, 2008))  # all BDS variants (domination -> channeling)
BDS_MODEL_ID_MIN = BDS_MODEL_IDS[0]
BDS_MODEL_ID_MAX = BDS_MODEL_IDS[-1]

GB_MODEL_ID = 2474  # Glacial Blades

# Persistent stats (loaded from INI, accumulated across all sessions)
_total_runs:     int   = 0
_timed_runs:     int   = 0
_total_run_time: float = 0.0
_fastest_run:    float = float('inf')
_slowest_run:    float = 0.0
_l1_total_time:  float = 0.0
_l1_fastest:     float = float('inf')
_l1_slowest:     float = 0.0
_l2_total_time:  float = 0.0
_l2_fastest:     float = float('inf')
_l2_slowest:     float = 0.0
_l3_total_time:  float = 0.0
_l3_fastest:     float = float('inf')
_l3_slowest:     float = 0.0
_bds_drops:  dict[str, int] = {}  # account_key -> all-time total (lazily loaded from INI)
_gb_drops:   dict[str, int] = {}  # account_key -> all-time total (lazily loaded from INI)
_char_names: dict[str, str] = {}  # account_key -> character name (populated from live shared memory)

# In-memory session stats (leader only, reset on reload)
_session_runs: int = 0
_session_bds:  dict[str, int] = {}  # account_key -> drops this session
_session_gb:   dict[str, int] = {}  # account_key -> drops this session

# UI display toggle — not persisted
_scramble_accounts: bool = False

# Torch-split flag: when True the persistent OnPartyMemberBehind callback is a no-op
_ignore_party_member_behind: bool = False

# Run timing anchors (in-memory, reset each run)
_t_run_start: float = 0.0
_t_l2_start:  float = 0.0
_t_l3_start:  float = 0.0

# Most-recently-completed times this session (in-memory, updated as each floor/run finishes)
_current_run_time: float = 0.0
_current_l1_time:  float = 0.0
_current_l2_time:  float = 0.0
_current_l3_time:  float = 0.0

# Leader pre-chest inventory snapshot
_bds_pre_snapshot: dict[int, int] = {}  # model_id -> count before chest open
_gb_pre_snapshot:  int            = 0   # GB count before chest open

_BDS_ICON_PATH = os.path.join(Py4GW.Console.get_projects_path(), "Widgets", "Automation", "Bots", "Missions", "Dungeons", "bds.png")

# ==================== BOT SETUP ====================
TEXTURE = _BDS_ICON_PATH

# Map IDs
VLOXS_FALL_MAP_ID = 624
ARBOR_BAY_MAP_ID = 485
SOO_LVL1_MAP_ID = 581
SOO_LVL2_MAP_ID = 582
SOO_LVL3_MAP_ID = 583
Great_Temple_of_Balthazar = 248
EyeOfTheNorth = 642

# Quest IDs
LOST_SOULS_QUEST_ID = 0x324  # Lost Souls - abandon when in Vloxs Fall


# Dialog IDs
DWARVEN_BLESSING_DIALOG = 0x84
SHANDRA_TAKE_DIALOGS = 0x832401
SHANDRA_QUEST_REWARD_DIALOG = 0x832407

# Coordinates
FENDI_CHEST_POSITION = (-15800.98,16901.23)
SHANDRA_POSITION = (14067.01, -17253.24)

# ==================== GLOBAL VARIABLES ====================
bot = Botting(
    bot_name=BOT_NAME,
    upkeep_auto_combat_active=False,
    upkeep_auto_loot_active=True,
    upkeep_morale_active=True,
    upkeep_auto_inventory_management_active=True,
)

# ==================== CORE ROUTINE ====================
def farm_bds_routine(bot_instance: Botting) -> None:

    # ===== INITIAL CONFIGURATION =====
    bot_instance.Templates.Routines.UseCustomBehaviors(
        on_player_critical_death=BottingHelpers.botting_unrecoverable_issue,
        on_party_death=BottingHelpers.botting_unrecoverable_issue,
        on_player_critical_stuck=BottingHelpers.botting_unrecoverable_issue)
    # Register wipe callback
    bot_instance.Events.OnPartyWipeCallback(lambda: on_party_wipe(bot_instance))
    

    
    # ===== START OF BOT =====
    bot_instance.States.AddHeader("Startup - Widget Setup")
    bot_instance.Properties.Enable("pause_on_danger")
    bot_instance.States.AddCustomState(apply_widget_policy_step, "Apply widget policy")
    bot_instance.States.AddCustomState(lambda: _gh_merchant_setup(leave_party=True), "GH Merchant Setup")
    bot_instance.Templates.Aggressive()
    bot_instance.Multibox.AbandonQuest(LOST_SOULS_QUEST_ID)
    bot_instance.States.AddHeader("Startup - Party Setup")
    bot_instance.Events.OnPartyMemberBehindCallback(lambda: None if _ignore_party_member_behind else bot_instance.Templates.Routines.OnPartyMemberBehind())
    bot_instance.Events.OnPartyMemberInDangerCallback(lambda: bot_instance.Templates.Routines.OnPartyMemberInDanger())
    bot_instance.Events.OnPartyMemberDeadBehindCallback(lambda: bot_instance.Templates.Routines.OnPartyMemberDeathBehind())
    bot_instance.Multibox.KickAllAccounts()
    bot_instance.States.AddCustomState(lambda: _coro_travel_random_district(VLOXS_FALL_MAP_ID), "Travel to Vlox's Falls")
    bot_instance.States.AddCustomState(loop_marker, "Reset Post Merchant")
    bot_instance.States.AddCustomState(lambda: _summon_and_invite_party(), "Initial Party Invite")
    bot_instance.States.AddCustomState(lambda: _reenable_merchant_widgets(), "Re-enable widgets (all in Vlox's Falls)")
    bot_instance.Multibox.RestockAllPcons()
    bot_instance.Multibox.RestockConset()
    bot_instance.Multibox.RestockResurrectionScroll(250)


    
    # ===== START OF LOOP =====
    bot_instance.States.AddHeader("Run Loop")
    _ensure_ini_initialized()
    bot_instance.Party.SetHardMode(_use_hard_mode)
    # Enable properties
    bot_instance.Properties.Enable('auto_combat')
    bot_instance.States.AddCustomState(_step_anchor, "Reset farm")  # anchor for secure return on wipe    
    # ===== GO TO DUNGEON =====
    bot_instance.States.AddHeader("Go to Dungeon")
    bot_instance.Move.XYAndExitMap(15505.38, 12460.59, target_map_id=ARBOR_BAY_MAP_ID)
    bot_instance.Wait.UntilOnExplorable()
    bot_instance.Wait.ForTime(POST_MAP_CHANGE_SETTLE_MS)

    # First blessing in Arbor Bay
    bot_instance.Move.XYAndInteractNPC(16327, 11607)
    bot_instance.Wait.ForTime(DIALOG_SETTLE_MS)
    bot_instance.Multibox.SendDialogToTarget(DWARVEN_BLESSING_DIALOG)
    bot_instance.Wait.ForTime(DIALOG_SETTLE_MS)
    bot_instance.Multibox.UseEssenceOfCelerity()
    bot_instance.Multibox.UseGrailOfMight()
    bot_instance.Multibox.UseArmorOfSalvation()
    bot_instance.Multibox.UseGoldenEgg()
    bot_instance.Multibox.UseCandyCorn()

    # Path to Shandra
    path = [
    (13455.43, 10678.00),
    (9850.00, 5025.00),
    (11207.11, 1872.32),
    (10452.02, 178.50),
    (10782.86, -3321.00),
    (8360.94, -6550.00),
    (10382.85, -12342.00),
    (10080.30, -13995.00),
    (10667.00, -16116.00),
    (10747.49, -17546.00),
    (11156.00, -17802.00),
]
    bot_instance.Move.FollowAutoPath(path)
    bot_instance.Wait.UntilOutOfCombat()
    
    # ===== LOOP RESTART POINT =====
    bot_instance.States.AddCustomState(loop_marker, "LOOP_RESTART_POINT")

    # Walk to Shandra first (pathfinding), then interact if needed
    bot_instance.Move.XY(12056.00, -17882)
    bot_instance.States.AddCustomState(lambda: _handle_shandra(bot_instance), "Shandra Quest Handler")

    # Enter the dungeon
    bot_instance.Move.XY(11177, -17683)
    bot_instance.Move.XY(10218, -18864)
    bot_instance.Move.XY(9519, -19968)
    bot_instance.Move.XY(9240.07, -20260.95)


    # Wait for change to Level 1
    bot_instance.States.AddCustomState(lambda: wait_for_map_change(SOO_LVL1_MAP_ID, 60), "Wait for Level 1")
    bot_instance.Wait.UntilOnExplorable()
    bot_instance.Wait.ForTime(POST_MAP_CHANGE_SETTLE_MS)
    

    # =========================
    #           Level 1
    # =========================
    bot_instance.States.AddHeader("Level 1 - Entry and Blessing")

    # Ensure quest state is Active and not Complete. Loops Shandra interaction until this condition is met.
    bot_instance.States.AddCustomState(lambda: _check_shandra_inside_dungeon(bot_instance), "Check Quest State (inside dungeon)")
    bot_instance.States.AddCustomState(_mark_run_start, "Mark Run Start")
    bot_instance.States.AddCustomState(_start_disconnect_watchdog, "Start Disconnect Watchdog")
    bot_instance.States.AddCustomState(_start_party_death_monitor, "Start Party Death Monitor")
    bot_instance.States.AddCustomState(_take_dungeon_entry_snapshot, "Pre-Dungeon Snapshot (All Accounts)")

    # First blessing Level 1
    bot_instance.States.AddCustomState(lambda: S_BlacklistModels(TORCH_MODEL_IDS), "Blacklist torchs")
    bot_instance.Multibox.UseEssenceOfCelerity()
    bot_instance.Multibox.UseGrailOfMight()
    bot_instance.Multibox.UseArmorOfSalvation()
    bot_instance.Multibox.UseGoldenEgg()
    bot_instance.Multibox.UseCandyCorn()
    # XYAndInteractNPC already walks — removed redundant pre-Move.XY
    bot_instance.Move.XYAndInteractNPC(-11686, 10427)
    bot_instance.Wait.ForTime(POST_MAP_CHANGE_SETTLE_MS)
    bot_instance.Multibox.SendDialogToTarget(DWARVEN_BLESSING_DIALOG)

    bot_instance.Templates.Aggressive()

    # Original L1 path (flank maneuver removed — restored from SoOTesting.py)
    l1_path = [
        (-11685.5, 10475.5),
        (-10682.6,  9841.2),
        (-9670.9,   9744.2),
        (-8661.9,   9975.7),
        (-7653.5,  10063.4),
        (-6652.0,  10156.2),
        (-5646.1,  10717.7),
        (-4642.3,  11376.3),
        (-3640.8,  11984.6),
        (-2634.2,  12702.1),
        (-1630.8,  13315.2),
        (-628.5,   14075.6),
        (379.8,    14700.8),
        (1384.7,   15324.0),
        (2394.5,   15950.3),
        (3409.5,   15710.4),
        (4157.9,   14705.9),
        (5089.4,   13698.1),
        (6090.8,   13172.6),
        (7091.1,   13482.8),
        (8093.3,   13148.6),
        (8503.9,   12143.5),
        (7496.9,   11676.0),
        (6494.3,   10739.2),
    ]
    bot_instance.Templates.Aggressive()
    bot_instance.Wait.UntilOutOfCombat()
    bot_instance.Move.FollowAutoPath(l1_path)

    # --- L1 Dungeon Key pickup ---
    # The Cursed Brigand drops the L1 dungeon key roughly between
    # (6319, 10687) and (6073, 10308). Auto-loot picks it up when any
    # character walks within ~250u. Move the torch runner over the drop zone and
    # linger 2s so auto-loot reliably fires. Without this, if the party
    # fight happens off-path, the key sits on the ground and the
    # dungeon-lock gadget interaction later fails silently (infinite hang).
    bot_instance.Move.XY(6196, 10497)
    bot_instance.Wait.ForTime(2000)

    bot_instance.States.AddHeader("Level 1 - Secure Return Checkpoint 1")
    bot_instance.States.AddCustomState(_step_anchor, "Secure return - L1")  # anchor for secure return on wipe

    path_before_door= [
        (9196.0,11484.4),
        (10196.0,12469.4),
        (11198.7,13401.8),
        (12201.3,14284.4),
        (13202.8,15176.3),
        (14207.0,16116.2),
        (15208.8,16871.6),
        (16213.2,16417.3),
        (16643.4,15416.6),
        (16994.9,14410.6),
        (17115.6,13405.6),
        (16689.2,12400.4),
        ]
    bot_instance.Templates.Aggressive()
    bot_instance.Move.FollowAutoPath(path_before_door)
    bot_instance.Wait.UntilOutOfCombat()
    bot_instance.Move.XY(15953, 11902)
    bot_instance.States.AddHeader("Level 1 - Secure Return Checkpoint 2")
    bot_instance.States.AddCustomState(_step_anchor, "Secure return 1 - L1")  # anchor for secure return on wipe    
    
    path_before_door2 = [    
        (15927.4,11684.7),
        (16037.8,10679.9),
        (15761.1,9679.7),
        (15289.5,8672.6),
        (14447.3,7672.0),
        (14526.2,6664.2),
        (14951.6,5657.9),
    ]

    bot_instance.Properties.Disable("pause_on_danger")
    bot_instance.Move.FollowAutoPath(path_before_door2)
    # Door gadget — hardened with bounded OOC wait + retry to prevent the
    # InteractWithAgentXY timeout → On Unmanaged Fail crash seen in run 93.
    bot_instance.Move.XY(15100, 5443)
    bot_instance.States.AddCustomState(
        lambda: _l1_door_interact_with_retry(door_x=15100.0, door_y=5443.0),
        "L1 Door — interact (retry-wrapped)",
    )

    # Path after door — fight through to the enemy group near (17202, 2607)
    path_after_door_combat = [
        (15364.9,4858.7),
        (15689.5,3857.7),
        (16026.7,2857.1),
        (17030.7,2262.6),
    ]
    bot_instance.Move.FollowAutoPath(path_after_door_combat)
    bot_instance.Wait.UntilOutOfCombat()

    # Now disable combat and rush the torch runner to portal
    bot_instance.Properties.Disable("auto_combat")
    bot_instance.Properties.Disable("pause_on_danger")
    bot_instance.States.AddCustomState(_disable_torch_runner_custom_behaviors, "Disable the torch runner CustomBehaviors - Portal Push")

    # Distract-and-sneak through the portal — alts aggro enemies guarding
    # the portal while the torch runner advances via path_to_portal.
    # NOTE: waypoints stop AT the portal trigger, NOT past it. The
    # map-change detection inside _portal_push_distract handles zone entry —
    # issuing Move.XY commands while already past the portal (i.e. mid
    # loading-screen) has been linked to GW client crashes. The previous
    # 6-waypoint path had two waypoints past the trigger; this one stops
    # at WP5 which is at/just-before the trigger boundary.
    path_to_portal = [
        (18035.7, 1888.8),
        (19037.1, 1384.6),
        (19679.2, 1009.5),
        (20181.6, 1203.7),
        (20400.5, 1300.0),   # at/near portal trigger — map-change check handles the rest
    ]
    bot_instance.States.AddCustomState(
        lambda: _portal_push_distract(
            label="L1",
            alt_forward_stack=(19700.0, 1100.0),
            torch_runner_path=path_to_portal,
            final_map_id=SOO_LVL2_MAP_ID,
            aggro_wait_timeout_s=5.0,
        ),
        "L1 Portal Push (distract + sneak)",
    )

    # Wait for change to Level 2
    bot_instance.Wait.ForMapToChange(SOO_LVL2_MAP_ID)
    bot_instance.Wait.UntilOnExplorable()
    bot_instance.States.AddCustomState(_enable_torch_runner_custom_behaviors, "Re-enable the torch runner CustomBehaviors")
    bot_instance.States.AddCustomState(_mark_l2_start, "Mark L2 Start")
    bot_instance.Properties.Enable("auto_combat")
    bot_instance.Properties.Enable("pause_on_danger")
    bot_instance.Wait.ForTime(POST_MAP_CHANGE_SETTLE_MS)

    # =========================
    #          Level 2
    # =========================
    bot_instance.States.AddHeader("Level 2 - Entry and Blessing")
    # --- Entry + Blessing ---
    # Walk + blessing first. Consumables are deferred to AFTER the blessing
    # dialog — by that point alts have had ~10s of natural buffer to finish
    # map-load after the L1→L2 portal transition. Broadcasting consumables
    # too early (before the blessing) caused alts to miss the first
    # consumable (Essence of Celerity) because they were still mid-load.
    bot_instance.Wait.UntilOutOfCombat()
    bot_instance.Move.XYAndInteractNPC(-14076, -19457)
    bot_instance.Multibox.SendDialogToTarget(DWARVEN_BLESSING_DIALOG)
    bot_instance.Wait.ForTime(POST_MAP_CHANGE_SETTLE_MS)
    bot_instance.States.AddHeader("Level 2 - Secure Return Checkpoint")
    bot_instance.States.AddCustomState(_step_anchor, "Secure return - L2")  # anchor for secure return on wipe

    # Use consumables AFTER blessing — reliable map-load completion by now.
    bot_instance.Multibox.UseEssenceOfCelerity()
    bot_instance.Multibox.UseGrailOfMight()
    bot_instance.Multibox.UseArmorOfSalvation()
    bot_instance.Multibox.UseGoldenEgg()
    bot_instance.Multibox.UseCandyCorn()
    bot_instance.Templates.Aggressive()
    # --- Path to torch area (atomisÃ©) ---
    path_before_torch = [
        (-14977.9,-16480.2),
        (-15985.6,-16838.1),
        (-16985.9,-16929.4),
    ]
    bot_instance.Templates.Aggressive()
    bot_instance.Move.FollowAutoPath(path_before_torch)
    bot_instance.Wait.UntilOutOfCombat()

    # --- Torch chest + pickup ---
    bot_instance.States.AddCustomState(bot_instance.Move.XYAndInteractGadget(-14709, -16548), "Open Torch Chest")

    bot_instance.States.AddCustomState(pickup_torch, "Pickup Torch")
    # --- Move to brazier sequence 1 ---
    bot_instance.Move.XY(-11002, -17001)



    bot_instance.Move.XY(-9259, -17322)
    bot_instance.Move.XY(-9971.23, -17633.08)
    bot_instance.Move.XY(-11136.85, -17201.66)
    bot_instance.Move.XY(-11030.3,-17474.0)
    bot_instance.Move.XY(-11303, -14596)

    # --- Brazier sequence 1 ---
    bot_instance.States.AddHeader("Level 2 - Brazier Route 1")
    run_brazier_sequence([(float(x), float(y)) for x, y in BDS_L2_PART1])
    bot_instance.States.AddHeader("Level 2 - Clear Remaining Enemies")
    bot_instance.States.AddCustomState(_log_cleaning_room, "Making sure no enemys are left")
    bot_instance.Move.FollowAutoPath(BDS_L2_CLEANING)

    bot_instance.States.AddHeader("Level 2 - Move to Next Room")
    bot_instance.Move.XY(-11061.1,-7578.5)
    bot_instance.Move.XY(-11013.7,-6381.7)

    path_room2 = [
    (-11013.7,-6381.7),
    (-11081.9,-5378.8),
    (-10071.6,-4396.5),
    (-9069.4,-4301.1),
    (-8066.1,-4222.4),
    (-7058.8,-4191.0)]
    bot_instance.Templates.Aggressive()
    bot_instance.Move.FollowAutoPath(path_room2)
    bot_instance.Wait.UntilOutOfCombat()



    bot_instance.Move.XY(-4245.2,-2101)

    # --- Brazier sequence 2 ---
    bot_instance.States.AddHeader("Level 2 - Brazier Route 2")

    run_brazier_sequence([(float(x), float(y)) for x, y in BDS_L2_PART2])
    bot_instance.UI.Keybinds.DropBundle()

    bot_instance.Move.XY(-6798.8, -2436.4)

    bot_instance.States.AddHeader("Level 2 - Move to Dungeon Lock")
    path_after_second_room_part1 = [
    (-9069.4,-4301.1),
    (-10071.6,-4396.5),
    (-11106.6,-4747.1),
    (-10970.9,-5754.5),
    (-11033.4,-6755.6),
    ]
    bot_instance.Move.FollowAutoPath(path_after_second_room_part1)
    bot_instance.States.AddCustomState(_log_l2_post_brazier_combat, "Log L2 post-brazier combat position")
    bot_instance.Wait.UntilOutOfCombat()

    path_after_second_room_part2 = [
    (-11318.0,-7767.2),
    (-12320.7,-8417.1),
    (-13324.0,-8649.0),
    (-14326.3,-8773.0),
    (-15331.0,-8905.6),
    (-16335.1,-9004.5),
    ]
    bot_instance.Properties.Disable("auto_combat")
    bot_instance.Properties.Disable("pause_on_danger")
    bot_instance.States.AddCustomState(_disable_torch_runner_custom_behaviors, "Disable the torch runner CustomBehaviors - Portal Push")
    bot_instance.Move.FollowAutoPath(path_after_second_room_part2)

    bot_instance.States.AddHeader("Level 2 - Open Door + Transit (retry wrapper)")
    # Consolidated: distract + door interact + walk through portal, wrapped in
    # an outer retry loop with a hard timeout. Prevents run 63-style infinite
    # hangs where the door fails to open and Wait.ForMapToChange waited forever.
    bot_instance.States.AddCustomState(
        lambda: _l2_transit_with_retry(
            door_x=-18725.0,
            door_y=-9171.0,
            alt_forward_stack=(-18000.0, -9150.0),
            portal_points=[
                (-18610.0, -8636.0),
                (-19571.61, -8459.0),
                (-20358.0, -8314.0),   # extended past portal to force zone trigger
            ],
        ),
        "L2 Open Door + Transit (retry)",
    )
    # CustomBehaviors re-enable already fired inside _l2_transit_with_retry the
    # moment map change was detected — no separate state needed. Proceed
    # directly to L3 start marker + property enables.
    bot_instance.States.AddCustomState(_mark_l3_start, "Mark L3 Start")
    bot_instance.Properties.Enable("auto_combat")
    bot_instance.Properties.Enable("pause_on_danger")

    # =========================
    #           Level 3
    # =========================
    bot_instance.States.AddHeader("Level 3 - Entry and Blessing")
    # --- Blessing ---
    # Walk + blessing first. Consumables are deferred to AFTER the blessing
    # dialog — gives alts the natural ~8s buffer (walk + pre-dialog wait +
    # dialog processing) to finish map-load after the L2→L3 portal transition
    # before any consumable broadcast lands. Broadcasting too early caused
    # some alts to miss the first consumable (Celerity) on L2/L3.
    bot_instance.Move.XYAndInteractNPC(17544, 18810)
    bot_instance.Wait.ForTime(POST_MAP_CHANGE_SETTLE_MS)
    bot_instance.Multibox.SendDialogToTarget(DWARVEN_BLESSING_DIALOG)
    bot_instance.States.AddHeader("Level 3 - Secure Return Checkpoint")
    bot_instance.States.AddCustomState(_step_anchor, "Secure return 1 - L3")

    # Use consumables AFTER blessing — reliable map-load completion by now.
    bot_instance.Multibox.UseEssenceOfCelerity()
    bot_instance.Multibox.UseGrailOfMight()
    bot_instance.Multibox.UseArmorOfSalvation()
    bot_instance.Multibox.UseGoldenEgg()
    bot_instance.Multibox.UseCandyCorn()
    bot_instance.Templates.Aggressive()

    bot_instance.States.AddHeader("Level 3 - Clear Main Path")
    path_before_flag = [
        (17544.5,18530.2),
        (17231.2,17523.3),
        (16811.3,16513.4),
        (15803.0,17071.6),
        (15004.8,18075.5),
        (13998.4,18866.7),
        (12990.9,19299.5),
        (11988.8,19353.2),
        (10986.4,19188.9),
        (9985.7,18719.2),
        (9402.1,17715.6),
        (9076.9,17383.4),
        (9133.0,16373.0),
        (8496.5,15367.3),
        (7978.0,14357.9),
        (7105.7,13350.9),
        (6236.1,12349.0),
        (5524.4,11344.1),
        (4813.8,10340.7),
        (4095.0,9332.7),
        (3091.4,8424.8),
        (2078.2,8286.5),
        (1926,5848),
        (1069.7,8045.3),
        (619.8,7044.0),
        (-385.8,6478.3)]
    bot_instance.Templates.Aggressive()
    # Instrumentation: slice L3 approach into path-walk vs OoC-wait sub-stages.
    bot_instance.States.AddCustomState(_cp_l3_path_start, "CP L3 Path Start")
    bot_instance.Move.FollowAutoPath(path_before_flag)
    bot_instance.States.AddCustomState(_cp_l3_path_end, "CP L3 Path End")
    bot_instance.Wait.UntilOutOfCombat()
    bot_instance.States.AddCustomState(_cp_l3_ooc_done, "CP L3 OoC Done")

    # --- Split: the torch runner breaks off for torch, party clears last flag waypoint + all of path2 ---
    bot_instance.States.AddHeader("Level 3 - Torch Runner Split")
    bot_instance.States.AddCustomState(_disable_party_member_behind, "Disable OnPartyMemberBehind")
    bot_instance.States.AddCustomState(_torch_runner_split, "Torch Runner Split")

    # --- the torch runner: backtrack to torch chest and run brazier sequence ---
    bot_instance.States.AddHeader("Level 3 - Path to Torch")
    path_to_take_torch = [
        (1962.00, 7079.00),
        (3089.73, 8511.00),
        (4963.00, 9974.00),
        (9918.64, 19108.00),
        (14709.00, 19526.00),
        (16111.00, 17556.00),
    ]
    bot_instance.Move.FollowAutoPath(path_to_take_torch)
    bot_instance.States.AddCustomState(bot_instance.Move.XYAndInteractGadget(16111.00, 17556), "Open Torch Chest")
    bot_instance.States.AddCustomState(pickup_torch, "Pickup Torch")
    bot_instance.States.AddCustomState(_start_torch_drop_scanner, "Start Torch Drop Scanner")

    # --- Brazier sequence ---
    bot_instance.States.AddHeader("Level 3 - Brazier Route")
    bot_instance.States.AddCustomState(lambda: _toggle_wait_for_party(False), "Disable WaitIfPartyMemberTooFar")
    run_brazier_sequence([(float(x), float(y)) for x, y in BDS_L3])
    bot_instance.States.AddCustomState(lambda: _toggle_wait_for_party(True), "Enable WaitIfPartyMemberTooFar")

    # --- Torch runner rejoins ---
    bot_instance.States.AddHeader("Level 3 - Torch Runner Rejoin")
    bot_instance.States.AddCustomState(lambda: drop_bundle_safe(2, 250), "Drop bundle")
    bot_instance.States.AddCustomState(_torch_runner_rejoin, "Torch Runner Rejoin")
    bot_instance.States.AddCustomState(_reenable_party_member_behind, "Re-enable OnPartyMemberBehind")
    # Instrumentation: slice the arena walk into rejoin→brigant→door→path→engage.
    bot_instance.States.AddCustomState(_cp_arena_rejoin_done, "CP Arena Rejoin Done")
    bot_instance.States.AddHeader("Level 3 - Kill Brigant")
    bot_instance.Move.XY(-11878.79, 2166.51)
    bot_instance.Move.XY(-9686.32, 2632)
    bot_instance.States.AddCustomState(_cp_arena_brigant_done, "CP Arena Brigant Done")
    bot_instance.States.AddCustomState(_set_l3_boss_route_flag, "Set L3 boss route flag")
    bot_instance.States.AddHeader("Level 3 - Boss Checkpoint")
    bot_instance.States.AddCustomState(_step_anchor, "Secure return boss - L3")
    bot_instance.States.AddHeader("Level 3 - Open Boss Door")
    bot_instance.Move.XY(-9252.32, 6396.40)
    bot_instance.States.AddCustomState(_cp_arena_door_approach, "CP Arena Door Approach")
    bot_instance.States.AddCustomState(bot_instance.Move.XYAndInteractGadget(-9252.32, 6396.40), "Open Door")
    bot_instance.States.AddCustomState(_cp_arena_door_done, "CP Arena Door Done")

    bot_instance.States.AddHeader("Level 3 - Path to Fendi")
    # --- Boss path ---
    path_bds = [
        (-8871.19, 6152.95),
        (-9326.33, 6862.55),
        (-10044.56, 7921.78),
        (-8408.54, 9475.41),
        (-10049.41, 11259.31),
        (-11381.15, 12387.01),
        (-12304.50, 13319.24),
        (-14736.33, 15054.21),
        (-15000, 16850),
    ]

    bot_instance.Templates.Aggressive()
    bot_instance.Move.FollowAutoPath(path_bds)
    bot_instance.States.AddCustomState(_cp_arena_path_end, "CP Arena Path End")
    bot_instance.States.AddCustomState(resolve_fendi_fight, "Resolve Fendi Fight")
    bot_instance.States.AddCustomState(_record_run_end, "Record Run End")
    bot_instance.States.AddHeader("Final Chest")
        # ===== OPEN FINAL CHEST =====
    bot_instance.Move.XY(-15821, 16834)
    bot_instance.States.AddCustomState(open_fendi_chest, "Open Chest (All Accounts)")
    bot_instance.States.AddCustomState(_record_drops_after_loot, "Record Drop Stats After Loot")
    bot_instance.States.AddCustomState(lambda: _collect_shandra_reward_in_dungeon(bot_instance), "Collect Quest Reward (in dungeon)")
    # ===== NEXT RUN =====
    bot_instance.Wait.ForMapToChange(target_map_name="Arbor Bay")
    bot_instance.States.AddCustomState(_gh_merchant_setup_if_inventory_full, "GH Merchant if inventory full")
    bot_instance.States.AddCustomState(_gh_merchant_setup_for_alt_salvage_threshold, "GH Merchant if alt salvage kits are low")
    bot_instance.States.AddCustomState(lambda: _handle_shandra(bot_instance), "Shandra Quest Handler")
    
    # ===== LOOP =====
    bot_instance.States.JumpToStepName("LOOP_RESTART_POINT")
# ==================== CUSTOM HELPERS ====================


# --- Merchant Setup and Inventory Helpers ---

def _find_npc_xy_by_name(name_fragment: str, max_dist: float = 15000.0) -> Optional[tuple[float, float]]:
    """Find the nearest NPC whose display name contains name_fragment."""
    npcs = AgentArray.GetNPCMinipetArray()
    npcs = AgentArray.Filter.ByDistance(npcs, Player.GetXY(), max_dist)
    for npc_id in npcs:
        npc_name = Agent.GetNameByID(int(npc_id))
        if name_fragment.lower() in npc_name.lower():
            return Agent.GetXY(int(npc_id))
    return None


def _count_model_in_inventory(model_id: int) -> int:
    bag_list = GLOBAL_CACHE.ItemArray.CreateBagList(1, 2, 3, 4)
    item_array = GLOBAL_CACHE.ItemArray.GetItemArray(bag_list)
    count = 0
    for item_id in item_array:
        if int(GLOBAL_CACHE.Item.GetModelID(item_id)) == int(model_id):
            count += max(1, int(GLOBAL_CACHE.Item.Properties.GetQuantity(item_id)))
    return count


def _account_key(email: str) -> str:
    return email.replace("@", "_at_").replace(".", "_")

def _display_email(key: str) -> str:
    """Reverse _account_key for display — converts storage key back to email format."""
    return key.replace("_at_", "@").replace("_", ".")

def _masked_email(key: str) -> str:
    """Return display email or a stable fake label if _scramble_accounts is enabled."""
    if not _scramble_accounts:
        return _char_names.get(key) or _display_email(key)
    all_keys = sorted(set(list(_bds_drops.keys()) + list(_session_bds.keys()) + list(_gb_drops.keys()) + list(_session_gb.keys())))
    idx = all_keys.index(key) + 1 if key in all_keys else 0
    return f"Player {idx}"


def _write_local_salvage_kit_count() -> None:
    from Py4GWCoreLib.enums_src.Model_enums import ModelID as _ModelID

    email = Player.GetAccountEmail()
    salvage_count = int(GLOBAL_CACHE.Inventory.GetModelCount(_ModelID.Salvage_Kit.value))
    _settings_ini.write_key(_ALT_SALVAGE_SECTION, _account_key(email), str(salvage_count))


def _request_alt_salvage_kit_counts() -> Generator:
    my_email = Player.GetAccountEmail()
    alt_accounts = [acc for acc in GLOBAL_CACHE.ShMem.GetAllAccountData() if acc.AccountEmail != my_email]
    for acc in alt_accounts:
        _settings_ini.write_key(_ALT_SALVAGE_SECTION, _account_key(acc.AccountEmail), str(-1))

    pending_accounts = alt_accounts
    max_attempts = max(1, _ALT_SALVAGE_POLL_MAX_TOTAL_MS // max(1, _ALT_SALVAGE_POLL_TIMEOUT_MS))
    for _attempt in range(max_attempts):
        if not pending_accounts:
            break

        for acc in pending_accounts:
            GLOBAL_CACHE.ShMem.SendMessage(
                my_email,
                acc.AccountEmail,
                SharedCommandType.MerchantItems,
                (0, 0, 0, 0),
                ("report_salvage_kits", _settings_ini_path, _ALT_SALVAGE_SECTION, _account_key(acc.AccountEmail)),
            )

        yield from Routines.Yield.wait(_ALT_SALVAGE_POLL_TIMEOUT_MS)
        pending_accounts = [
            acc for acc in pending_accounts
            if _settings_ini.read_int(_ALT_SALVAGE_SECTION, _account_key(acc.AccountEmail), -1) < 0
        ]

    if pending_accounts:
        pending_names = [acc.AgentData.CharacterName or acc.AccountEmail for acc in pending_accounts]
        ConsoleLog(
            BOT_NAME,
            f"[Merchant] No salvage count reply after {max_attempts} attempts ({_ALT_SALVAGE_POLL_MAX_TOTAL_MS} ms max) from: {', '.join(pending_names)}. Skipping them this check.",
            Py4GW.Console.MessageType.Warning,
        )


def _alts_need_salvage_restock() -> tuple[bool, list[str], list[str]]:
    my_email = Player.GetAccountEmail()
    ini_reader = IniHandler(_settings_ini_path)
    low_accounts: list[str] = []
    unknown_accounts: list[str] = []
    for acc in GLOBAL_CACHE.ShMem.GetAllAccountData():
        if acc.AccountEmail == my_email:
            continue
        count = ini_reader.read_int(_ALT_SALVAGE_SECTION, _account_key(acc.AccountEmail), -1)
        char_name = acc.AgentData.CharacterName or acc.AccountEmail
        if count < 0:
            unknown_accounts.append(char_name)
            continue
        if count < _ALT_SALVAGE_TRIGGER_THRESHOLD:
            low_accounts.append(f"{char_name} ({count})")
    return len(low_accounts) > 0, low_accounts, unknown_accounts


def _coro_sell_rare_mats_at_trader(x: float, y: float, model_ids: set[int]) -> Generator:
    """Sell rare material items (by model ID) to the trader at (x, y), one unit at a time.
    Bypasses SellMaterialsAtTrader which skips IsRareMaterial items."""
    yield from Routines.Yield.Movement.FollowPath([(x, y)])
    yield from Routines.Yield.wait(100)
    yield from Routines.Yield.Agents.InteractWithAgentXY(x, y)
    yield from Routines.Yield.wait(1000)

    bag_list = GLOBAL_CACHE.ItemArray.CreateBagList(1, 2, 3, 4)
    item_array = GLOBAL_CACHE.ItemArray.GetItemArray(bag_list)
    sold_total = 0
    for item_id in item_array:
        if int(GLOBAL_CACHE.Item.GetModelID(item_id)) not in model_ids:
            continue
        stack_qty = int(GLOBAL_CACHE.Item.Properties.GetQuantity(item_id))
        while stack_qty > 0:
            quoted = yield from Routines.Yield.Merchant._wait_for_quote(
                GLOBAL_CACHE.Trading.Trader.RequestSellQuote, item_id,
                timeout_ms=750, step_ms=10)
            if quoted <= 0:
                break
            GLOBAL_CACHE.Trading.Trader.SellItem(item_id, quoted)
            new_qty = yield from Routines.Yield.Merchant._wait_for_stack_quantity_drop(
                item_id, stack_qty, timeout_ms=750, step_ms=10)
            if new_qty >= stack_qty:
                break
            sold_total += stack_qty - new_qty
            stack_qty = new_qty
    ConsoleLog(BOT_NAME, f"[Merchant] Sold {sold_total} rare material unit(s) at trader")


def _get_leftover_material_item_ids(batch_size: int = 10) -> list[int]:
    """Return item IDs of common (non-rare) material stacks with quantity < batch_size."""
    bag_list = GLOBAL_CACHE.ItemArray.CreateBagList(1, 2, 3, 4)
    item_array = GLOBAL_CACHE.ItemArray.GetItemArray(bag_list)
    leftovers: list[int] = []
    for item_id in item_array:
        if not GLOBAL_CACHE.Item.Type.IsMaterial(item_id):
            continue
        if GLOBAL_CACHE.Item.Type.IsRareMaterial(item_id):
            continue
        qty = int(GLOBAL_CACHE.Item.Properties.GetQuantity(item_id))
        if 0 < qty < batch_size:
            leftovers.append(int(item_id))
    return leftovers


def _broadcast_widget_command(
    command: SharedCommandType,
    widget_names: tuple[str, ...],
    *,
    exclude_self: bool = True,
    exclude_email: Optional[str] = None,
) -> None:
    """Send a DisableWidget/EnableWidget command for each widget name to all accounts."""
    my_email = Player.GetAccountEmail()
    for acc in GLOBAL_CACHE.ShMem.GetAllAccountData():
        if exclude_self and acc.AccountEmail == my_email:
            continue
        if exclude_email and acc.AccountEmail == exclude_email:
            continue
        for name in widget_names:
            GLOBAL_CACHE.ShMem.SendMessage(
                my_email, acc.AccountEmail, command,
                (0, 0, 0, 0), (name, "", "", ""),
            )


def _disable_widgets_on_alts_only(widget_names: tuple[str, ...]) -> Generator:
    if not widget_names:
        yield
        return
    _broadcast_widget_command(SharedCommandType.DisableWidget, widget_names)
    yield from Routines.Yield.wait(500)
def _get_material_item_ids_by_models(selected_models: set[int]) -> list[int]:
    bag_list = GLOBAL_CACHE.ItemArray.CreateBagList(1, 2, 3, 4)
    item_array = GLOBAL_CACHE.ItemArray.GetItemArray(bag_list)
    result: list[int] = []
    for item_id in item_array:
        if not GLOBAL_CACHE.Item.Type.IsMaterial(item_id):
            continue
        if GLOBAL_CACHE.Item.Type.IsRareMaterial(item_id):
            continue
        model_id = int(GLOBAL_CACHE.Item.GetModelID(item_id))
        if model_id in selected_models:
            result.append(int(item_id))
    return result


def _coro_deposit_crafting_materials_to_storage(selected_models: set[int]) -> Generator:
    if not selected_models:
        yield
        return
    if not GLOBAL_CACHE.Inventory.IsStorageOpen():
        GLOBAL_CACHE.Inventory.OpenXunlaiWindow()
        yield from Routines.Yield.wait(1000)
    if not GLOBAL_CACHE.Inventory.IsStorageOpen():
        ConsoleLog(BOT_NAME, "[Merchant] Storage not open; skipping crafting material deposit", Py4GW.Console.MessageType.Warning)
        yield
        return

    item_ids = _get_material_item_ids_by_models(selected_models)
    if not item_ids:
        ConsoleLog(BOT_NAME, "[Merchant] No crafting materials to deposit")
        yield
        return

    for item_id in item_ids:
        GLOBAL_CACHE.Inventory.DepositItemToStorage(item_id)
        yield from Routines.Yield.wait(40)

    ConsoleLog(BOT_NAME, f"[Merchant] Deposited {len(item_ids)} crafting material stack(s) to storage")
    yield


_SCROLL_MODEL_IDS = {5594, 5595, 5611, 5853, 5975, 5976, 21233}
_SCROLL_MODEL_FILTER = "5594,5595,5611,5853,5975,5976,21233"


def _coro_sell_scrolls(mx: float, my: float) -> Generator:
    """Sell XP/insight scrolls to the GH merchant."""
    bag_list = GLOBAL_CACHE.ItemArray.CreateBagList(1, 2, 3, 4)
    item_array = GLOBAL_CACHE.ItemArray.GetItemArray(bag_list)
    sell_ids = [int(item_id) for item_id in item_array
                if int(GLOBAL_CACHE.Item.GetModelID(item_id)) in _SCROLL_MODEL_IDS]
    if not sell_ids:
        ConsoleLog(BOT_NAME, "[Merchant] No scrolls to sell in bags 1-4")
        storage_hits = [(mid, GLOBAL_CACHE.Inventory.GetModelCountInStorage(mid))
                        for mid in _SCROLL_MODEL_IDS]
        storage_hits = [(mid, cnt) for mid, cnt in storage_hits if cnt > 0]
        if storage_hits:
            ConsoleLog(BOT_NAME, f"[Merchant] WARNING: scrolls found in STORAGE (InventoryPlus deposited them): {storage_hits}")
        yield
        return
    for item_id in sell_ids:
        val = GLOBAL_CACHE.Item.Properties.GetValue(item_id)
        qty = GLOBAL_CACHE.Item.Properties.GetQuantity(item_id)
        mid = GLOBAL_CACHE.Item.GetModelID(item_id)
        ConsoleLog(BOT_NAME, f"[Merchant] Scroll queued: item_id={item_id} model={mid} qty={qty} value={val}")
    yield from bot.Move._coro_xy_and_interact_npc(mx, my, "GH Merchant (scrolls)")
    yield from Routines.Yield.wait(NPC_INTERACT_SETTLE_MS)
    ConsoleLog(BOT_NAME, f"[Merchant] Selling {len(sell_ids)} scroll(s) at merchant")
    yield from Routines.Yield.Merchant.SellItems(sell_ids, log=True)
    yield from Routines.Yield.wait(300)


def _coro_sell_nonsalvageable_golds(mx: float, my: float) -> Generator:
    """Sell all identified, non-salvageable gold items (e.g. anniversary weapons) to the GH merchant."""
    bag_list = GLOBAL_CACHE.ItemArray.CreateBagList(1, 2, 3, 4)
    item_array = GLOBAL_CACHE.ItemArray.GetItemArray(bag_list)
    sell_ids = []
    for item_id in item_array:
        _, rarity = GLOBAL_CACHE.Item.Rarity.GetRarity(item_id)
        if rarity != "Gold":
            continue
        if not GLOBAL_CACHE.Item.Usage.IsIdentified(item_id):
            continue
        if GLOBAL_CACHE.Item.Usage.IsSalvageable(item_id):
            continue
        sell_ids.append(int(item_id))
    if not sell_ids:
        ConsoleLog(BOT_NAME, "[Merchant] No non-salvageable gold items to sell")
        yield
        return
    yield from bot.Move._coro_xy_and_interact_npc(mx, my, "GH Merchant (non-salvageable golds)")
    yield from Routines.Yield.wait(NPC_INTERACT_SETTLE_MS)
    ConsoleLog(BOT_NAME, f"[Merchant] Selling {len(sell_ids)} non-salvageable gold item(s) at merchant")
    yield from Routines.Yield.Merchant.SellItems(sell_ids, log=True)
    yield from Routines.Yield.wait(300)


_MERCHANT_MANAGED_WIDGETS = ("InventoryPlus", "CustomBehaviors")
_PRETRAVEL_DISABLE_WIDGETS = ("InventoryPlus",)  # disable before GH travel so deposit cycle doesn't run on GH entry


def _disable_merchant_widgets() -> Generator:
    """Disable InventoryPlus and CustomBehaviors on leader + all alts during GH merchant ops."""

    ConsoleLog(BOT_NAME, "[Merchant] Disabling managed widgets on all accounts")
    wh = _get_wh()
    for name in _MERCHANT_MANAGED_WIDGETS:
        wh.disable_widget(name)
    _broadcast_widget_command(SharedCommandType.DisableWidget, _MERCHANT_MANAGED_WIDGETS)
    ConsoleLog(BOT_NAME, f"[Merchant] Disabled {_MERCHANT_MANAGED_WIDGETS} on all accounts")
    yield

def _move_to(x: float, y: float, tolerance: float = 180.0, max_tries: int = 60) -> Generator:
    Player.Move(x, y)

    for _ in range(max_tries):
        px, py = Player.GetXY()
        dist = Utils.Distance((px, py), (x, y))

        if dist <= tolerance:
            return True

        yield from Routines.Yield.wait(100)

    return False


def _wait_for_map(map_name: str, max_tries: int = 120) -> Generator:
    for _ in range(max_tries):
        if Map.GetMapName() == map_name:
            return True
        yield from Routines.Yield.wait(500)
    return False

# --- Quest State and Shandra Interaction ---

def _shandra_quest_state() -> str:
    """Returns 'none' (not in log), 'active' (in progress), or 'complete' (reward ready)."""
    quest_ids = Quest.GetQuestLogIds()
    if LOST_SOULS_QUEST_ID not in quest_ids:
        return "none"
    if Quest.IsQuestCompleted(LOST_SOULS_QUEST_ID):
        return "complete"
    return "active"

def _handle_shandra(bot: Botting) -> Generator:
    """
    Unified Shandra handler. Checks quest state and acts accordingly.
    Caller is responsible for moving the bot to Shandra's position first.
      active   -> skip, proceed to dungeon
      complete -> collect reward (one dialog per map load; accept deferred to next visit)
      none     -> accept quest
    """
    state = _shandra_quest_state()
    ConsoleLog(BOT_NAME, f"[Shandra] Quest state: {state}", log=True)

    if state == "active":
        ConsoleLog(BOT_NAME, "[Shandra] Quest active — proceeding to dungeon", log=True)
        yield
        return

    if state == "complete":
        ConsoleLog(BOT_NAME, "[Shandra] Collecting reward", log=True)
        ok = yield from _interact_with_Shandra(bot, SHANDRA_QUEST_REWARD_DIALOG)
        if not ok:
            ConsoleLog(BOT_NAME, "[Shandra] Reward interaction failed", log=True)
        ConsoleLog(BOT_NAME, "[Shandra] Reward collected — quest accept deferred to next map load", log=True)
        yield
        return

    # state == "none"
    ConsoleLog(BOT_NAME, "[Shandra] Accepting quest", log=True)
    ok = yield from _interact_with_Shandra(bot, SHANDRA_TAKE_DIALOGS)
    if not ok:
        ConsoleLog(BOT_NAME, "[Shandra] Accept quest failed", log=True)
        yield
        return

    ConsoleLog(BOT_NAME, "[Shandra] Handler complete", log=True)
    yield

def _collect_shandra_reward_in_dungeon(bot: Botting) -> Generator:
    
    ConsoleLog(BOT_NAME, "[Shandra] Collecting reward inside dungeon", log=True)
    
    bot.Wait.ForTime(4000)
    ok = yield from _interact_with_Shandra(bot, SHANDRA_QUEST_REWARD_DIALOG)
    bot.Wait.ForTime(4000)
    
    if not ok:
        ConsoleLog(BOT_NAME, "[Shandra] In-dungeon reward collection failed — will retry in Arbor Bay", log=True)
    yield

def _check_shandra_inside_dungeon(bot: Botting) -> Generator:
    """
    Inside dungeon check. Loops until the quest is confirmed active or
    max retries are exhausted (hard stop).
      active   -> nothing to do
      none     -> exit dungeon, handle Shandra, re-enter, re-check
      complete -> exit dungeon, handle Shandra, re-enter, re-check
    """
    _max_attempts = 3
    for _attempt in range(_max_attempts):
        state = _shandra_quest_state()
        ConsoleLog(BOT_NAME, f"[Shandra] Inside dungeon state: {state} (attempt {_attempt + 1}/{_max_attempts})", log=True)

        if state == "active":
            yield
            return

        ConsoleLog(BOT_NAME, f"[Shandra] Quest '{state}' inside dungeon — exiting to Arbor Bay", log=True)

        yield from Routines.Yield.Movement.FollowPath([(-15650.00, 8900.00)])

        ok = yield from _wait_for_map("Arbor Bay")
        if not ok:
            ConsoleLog(BOT_NAME, "[Shandra] Failed to return to Arbor Bay", Py4GW.Console.MessageType.Warning)
            continue

        # Give the map time to fully load before attempting pathfinding
        yield from Routines.Yield.wait(10000)

        ConsoleLog(BOT_NAME, "[Shandra] Back in Arbor Bay — moving to Shandra", log=True)
        shandra_x, shandra_y = SHANDRA_POSITION
        yield from Routines.Yield.Movement.FollowPath([
            (10218.0, -18864.0),
            (12056, -17882),
        ], stop_on_party_wipe=False)

        yield from _handle_shandra(bot)

        # Re-enter the dungeon so Level 1 routing executes on the correct map
        ConsoleLog(BOT_NAME, "[Shandra] Re-entering dungeon", log=True)
        yield from Routines.Yield.Movement.FollowPath([
            (10218.0, -18864.0),
            (9519.0,  -19968.0),
            (9240.07, -20260.95),
        ], stop_on_party_wipe=False)

        ok = yield from wait_for_map_change(SOO_LVL1_MAP_ID, 60)
        if not ok:
            ConsoleLog(BOT_NAME, "[Shandra] Failed to re-enter SoO Level 1 — retrying", Py4GW.Console.MessageType.Warning)
            continue

        yield from Routines.Yield.wait(2000)
        # Loop back to re-check quest state at the top

    # Final defensive check — a map-change race may have tripped `continue` even though the quest is now active
    final_state = _shandra_quest_state()
    if final_state == "active":
        ConsoleLog(BOT_NAME, f"[Shandra] Final check recovered — quest is active, proceeding", log=True)
        yield
        return

    # Exhausted all attempts — quest never became active
    ConsoleLog(BOT_NAME, f"[HARD STOP] Shandra quest never became active after {_max_attempts} attempts (final state={final_state}) — stopping bot.", Py4GW.Console.MessageType.Error)
    bot.Stop()
    yield

def find_nearest_npc_by_name(name_fragment: str, max_dist: float = 2000.0) -> int:
    """Find nearest NPC whose name contains name_fragment."""
    player_pos = Player.GetXY()

    npcs = AgentArray.GetNPCMinipetArray()
    npcs = AgentArray.Filter.ByDistance(npcs, player_pos, max_dist)
    npcs = AgentArray.Sort.ByDistance(npcs, player_pos)

    for npc_id in npcs:
        npc_id = int(npc_id)

        try:
            npc_name = Agent.GetNameByID(npc_id)
        except Exception as exc:
            ConsoleLog(BOT_NAME, f"[find_nearest_npc_by_name] Error: {exc}", Py4GW.Console.MessageType.Warning)
            continue

        if name_fragment.lower() in npc_name.lower():
            return npc_id

    return 0

def _interact_with_Shandra(bot: Botting, dialog_id: int, tolerance: float = 220.0) -> Generator:
    npc_name = "Crewmember Shandra"

    # Retry a few times in case the agent list hasn't fully loaded yet (common on first map entry)
    agent_id = 0
    for _retry in range(5):
        agent_id = find_nearest_npc_by_name(npc_name, 2000.0)
        if agent_id:
            break
        yield from Routines.Yield.wait(500)

    if not agent_id:
        ConsoleLog(BOT_NAME, f"[Shandra] {npc_name} not found nearby", log=True)
        return False

    x, y = Agent.GetXY(agent_id)
    ConsoleLog(BOT_NAME, f"[Shandra] Found {npc_name} at ({x}, {y}) agent_id={agent_id}", log=True)

    ok = yield from _move_to(x, y, tolerance=tolerance)
    if not ok:
        ConsoleLog(BOT_NAME, "[Shandra] Impossible to approach Shandra", log=True)
        return False
    
    Player.ChangeTarget(agent_id)
    yield from Routines.Yield.wait(800)
    Player.Interact(agent_id)
    yield from Routines.Yield.wait(800)
    Player.SendDialog(dialog_id)
    yield from Routines.Yield.wait(1500)

    # Dispatch the same dialog to all alt accounts
    sender_email = Player.GetAccountEmail()
    for account in GLOBAL_CACHE.ShMem.GetAllAccountData():
        if account.AccountEmail != sender_email:
            GLOBAL_CACHE.ShMem.SendMessage(
                sender_email, account.AccountEmail,
                SharedCommandType.SendDialogToTarget, (agent_id, dialog_id, 0, 0)
            )

    yield from Routines.Yield.wait(1500)
    return True

L3_BOSS_ROUTE_UNLOCKED = False

def _reset_l3_boss_route_flag() -> Generator:
    global L3_BOSS_ROUTE_UNLOCKED
    L3_BOSS_ROUTE_UNLOCKED = False
    ConsoleLog(BOT_NAME, "[L3] Boss route unlocked = False")
    yield


def _set_l3_boss_route_flag() -> Generator:
    global L3_BOSS_ROUTE_UNLOCKED
    L3_BOSS_ROUTE_UNLOCKED = True
    ConsoleLog(BOT_NAME, "[L3] Boss route unlocked = True")
    yield


def _disable_inventoryplus_pretravel() -> Generator:
    """Disable InventoryPlus on leader + alts BEFORE GH travel, so InventoryPlus cannot
    run its auto-deposit cycle when accounts enter GH (which would send scrolls to storage)."""

    ConsoleLog(BOT_NAME, "[Merchant] Pre-travel: disabling InventoryPlus on all accounts")
    wh = _get_wh()
    for name in _PRETRAVEL_DISABLE_WIDGETS:
        wh.disable_widget(name)
    _broadcast_widget_command(SharedCommandType.DisableWidget, _PRETRAVEL_DISABLE_WIDGETS)
    ConsoleLog(BOT_NAME, "[Merchant] Pre-travel: InventoryPlus disabled — waiting 1.5s for alts to process")
    yield from Routines.Yield.wait(1500)


def _reenable_merchant_widgets() -> Generator:
    """Re-enable InventoryPlus and CustomBehaviors on leader + all alts after GH merchant ops.
    Called once all accounts are back in Vlox's Falls, ready to enter the dungeon."""

    ConsoleLog(BOT_NAME, "[Merchant] Re-enabling managed widgets on all accounts")

    # Enable on leader immediately
    wh = _get_wh()
    for name in _MERCHANT_MANAGED_WIDGETS:
        wh.enable_widget(name)

    # Send EnableWidget to each alt for each widget, collecting message refs
    _my_email = Player.GetAccountEmail()
    _refs: list[tuple[str, int]] = []
    for acc in GLOBAL_CACHE.ShMem.GetAllAccountData():
        if acc.AccountEmail != _my_email:
            for name in _MERCHANT_MANAGED_WIDGETS:
                msg_index = int(GLOBAL_CACHE.ShMem.SendMessage(
                    _my_email, acc.AccountEmail,
                    SharedCommandType.EnableWidget, (0, 0, 0, 0), (name, "", "", ""),
                ))
                if msg_index >= 0:
                    _refs.append((acc.AccountEmail, msg_index))

    # Wait for every alt to call MarkMessageAsFinished (sets Active=False)
    ConsoleLog(BOT_NAME, f"[Merchant] Waiting for {len(_refs)} EnableWidget message(s) to complete")
    _pending = {(email, idx): None for email, idx in _refs}
    _deadline = time.monotonic() + 15.0
    while _pending and time.monotonic() < _deadline:
        for _key in list(_pending):
            _email, _idx = _key
            _msg = GLOBAL_CACHE.ShMem.GetInbox(_idx)
            _still_active = (
                bool(getattr(_msg, "Active", False))
                and str(getattr(_msg, "ReceiverEmail", "") or "") == _email
                and str(getattr(_msg, "SenderEmail", "") or "") == _my_email
                and int(getattr(_msg, "Command", -1)) == int(SharedCommandType.EnableWidget)
            )
            if not _still_active:
                _pending.pop(_key, None)
        if _pending:
            yield from Routines.Yield.wait(100)

    if _pending:
        ConsoleLog(BOT_NAME, f"[Merchant] EnableWidget timeout — {len(_pending)} message(s) unconfirmed. Proceeding.", Py4GW.Console.MessageType.Warning)
        yield from Routines.Yield.wait(_POST_WIDGET_REENABLE_SETTLE_MS)
    else:
        ConsoleLog(BOT_NAME, "[Merchant] All widgets successfully re-enabled on all accounts")
    yield


def _gh_merchant_setup(leave_party: bool = True) -> Generator:
    """Travel to Guild Hall (all accounts via SharedMemory), restock kits, sell materials,
    sell leftover stacks and optionally buy ectos. Mirrors the FoW modular bot pattern."""
    from Py4GWCoreLib.enums_src.Model_enums import ModelID as _ModelID

    _ensure_ini_initialized()
    if not _merchant_enabled:
        yield
        return

    _my_email = Player.GetAccountEmail()

    def _dispatch_to_alts(command, params, extra_data=("", "", "", "")) -> list[tuple[str, int]]:
        refs: list[tuple[str, int]] = []
        for _acc in GLOBAL_CACHE.ShMem.GetAllAccountData():
            if _acc.AccountEmail != _my_email:
                msg_index = int(
                    GLOBAL_CACHE.ShMem.SendMessage(_my_email, _acc.AccountEmail, command, params, extra_data)
                )
                refs.append((_acc.AccountEmail, msg_index))
        return refs

    def _wait_for_alt_dispatch_completion(
        stage_name: str,
        message_refs: list[tuple[str, int]],
        command,
        timeout_ms: int = 30_000,
    ):
        if not message_refs:
            return
        pending: dict[tuple[str, int], None] = {
            (acc_email, msg_index): None
            for acc_email, msg_index in message_refs
            if int(msg_index) >= 0
        }
        if not pending:
            return
        deadline = time.monotonic() + (max(0, int(timeout_ms)) / 1000.0)
        while pending and time.monotonic() < deadline:
            completed: list[tuple[str, int]] = []
            for acc_email, msg_index in list(pending.keys()):
                message = GLOBAL_CACHE.ShMem.GetInbox(msg_index)
                is_same_message = (
                    bool(getattr(message, "Active", False))
                    and str(getattr(message, "ReceiverEmail", "") or "") == acc_email
                    and str(getattr(message, "SenderEmail", "") or "") == _my_email
                    and int(getattr(message, "Command", -1)) == int(command)
                )
                if not is_same_message:
                    completed.append((acc_email, msg_index))
            for key in completed:
                pending.pop(key, None)
            if pending:
                yield from Routines.Yield.wait(50)
        if pending:
            pending_accounts = ", ".join(sorted({email for email, _ in pending}))
            ConsoleLog(
                BOT_NAME,
                f"[Merchant] {stage_name}: timeout waiting for alt completion after {timeout_ms} ms. Pending: {pending_accounts}",
                Py4GW.Console.MessageType.Warning,
            )

    # —— Step 0 (startup only): Leave current party on all accounts ————————————
    if leave_party:
        ConsoleLog(BOT_NAME, "[Merchant] Leaving party on all accounts before GH travel")
        for acc in GLOBAL_CACHE.ShMem.GetAllAccountData():
            if acc.AccountEmail != _my_email:
                GLOBAL_CACHE.ShMem.SendMessage(_my_email, acc.AccountEmail, SharedCommandType.LeaveParty, (0, 0, 0, 0), ("", "", "", ""))
        GLOBAL_CACHE.Party.LeaveParty()
        yield from Routines.Yield.wait(2000)

    # —— Pre-travel: Disable InventoryPlus BEFORE GH entry so its auto-deposit cycle
    #    cannot send scrolls (or other items) to storage when accounts enter GH. ——
    yield from _disable_inventoryplus_pretravel()

    # —— Step 1: Send ALL accounts to their own Guild Hall ———————————————————
    # Snapshot alt count BEFORE dispatching travel — alts temporarily drop out of
    # ShMem during map transitions so the count must be captured while everyone
    # is still fully settled in Vlox's Fall.
    _expected_gh_alts = len([
        acc for acc in GLOBAL_CACHE.ShMem.GetAllAccountData()
        if acc.AccountEmail != _my_email
    ])
    ConsoleLog(BOT_NAME, "[Merchant] Dispatching GH travel to all accounts")
    _gh_refs = _dispatch_to_alts(SharedCommandType.TravelToGuildHall, (0, 0, 0, 0))
    if not Map.IsGuildHall():
        Map.TravelGH()
    yield from _wait_for_alt_dispatch_completion("travel_gh", _gh_refs, SharedCommandType.TravelToGuildHall, timeout_ms=10_000)

    # Wait for leader to arrive at GH
    _gh_deadline = time.monotonic() + 30
    while not Map.IsGuildHall() and time.monotonic() < _gh_deadline:
        yield from Routines.Yield.wait(500)

    if not Map.IsGuildHall():
        ConsoleLog(BOT_NAME, "[Merchant] Failed to reach Guild Hall — skipping merchant step")
        yield
        return

    # Wait for all alts to arrive at GH (match leader's map ID)
    _gh_map = int(Map.GetMapID())
    _arrival_deadline = time.monotonic() + 60
    while time.monotonic() < _arrival_deadline:
        _accounts = GLOBAL_CACHE.ShMem.GetAllAccountData()
        _in_gh = sum(
            1 for acc in _accounts
            if acc.AccountEmail != _my_email and int(acc.AgentData.Map.MapID) == _gh_map
        )
        if _in_gh >= _expected_gh_alts:
            ConsoleLog(BOT_NAME, f"[Merchant] All {_expected_gh_alts} alt(s) arrived at GH")
            break
        ConsoleLog(BOT_NAME, f"[Merchant] {_in_gh}/{_expected_gh_alts} alt(s) at GH — waiting")
        yield from Routines.Yield.wait(500)
    else:
        _accounts = GLOBAL_CACHE.ShMem.GetAllAccountData()
        _in_gh = sum(
            1 for acc in _accounts
            if acc.AccountEmail != _my_email and int(acc.AgentData.Map.MapID) == _gh_map
        )
        ConsoleLog(BOT_NAME, f"[Merchant] GH arrival timeout — {_in_gh}/{_expected_gh_alts} alts at GH. Proceeding.", Py4GW.Console.MessageType.Warning)

    # wait for Merchant NPC to spawn (handles fresh GH instances)
    _npc_deadline = time.monotonic() + 20.0
    while _find_npc_xy_by_name("Merchant") is None:
        if time.monotonic() > _npc_deadline:
            ConsoleLog(BOT_NAME, "[Merchant] Merchant NPC not found after 20s — proceeding anyway", Py4GW.Console.MessageType.Warning)
            break
        yield from Routines.Yield.wait(500)

    # —— Disable CustomBehavior and InventoryPlus on all accounts during merchant ops ——
    yield from _disable_merchant_widgets()

    # —— Step 2: Find NPC coordinates ——————————————————————————————————————————
    _RARE_MAT_MODELS = {935, 936}  # Diamond=935, Onyx Gemstone=936
    _RARE_MAT_FILTER  = "935,936"  # encoded for ShMem dispatch
    _CRAFTING_MAT_MODELS = {
        int(_ModelID.Pile_Of_Glittering_Dust.value),
        int(_ModelID.Bone.value),
        int(_ModelID.Iron_Ingot.value),
        int(_ModelID.Feather.value),
        int(_ModelID.Plant_Fiber.value),
    }
    _CRAFTING_MAT_FILTER = ",".join(str(mid) for mid in sorted(_CRAFTING_MAT_MODELS))

    merchant_xy   = _find_npc_xy_by_name("Merchant")
    mat_xy        = _find_npc_xy_by_name("Material Trader") if _merchant_sell_materials else None
    rare_xy       = _find_npc_xy_by_name("Rare") if (_merchant_buy_ectos or _merchant_sell_rare_mats) else None

    # —— Step 2.5: Store consumable crafting mats before trader sales (leader + alts)
    if _merchant_store_consumable_materials:
        ConsoleLog(BOT_NAME, "[Merchant] Depositing consumable crafting materials to storage on all accounts")
        deposit_refs = _dispatch_to_alts(
            SharedCommandType.MerchantMaterials,
            (0, 0, 0, 0),
            ("deposit", _CRAFTING_MAT_FILTER, "", "0"),
        )
        yield from _coro_deposit_crafting_materials_to_storage(_CRAFTING_MAT_MODELS)
        yield from _wait_for_alt_dispatch_completion("deposit_materials", deposit_refs, SharedCommandType.MerchantMaterials)

    # —— Step 3: Sell materials at trader (leader + alts) —————————————————————
    if _merchant_sell_materials:
        if mat_xy:
            tmx, tmy = mat_xy
            ConsoleLog(BOT_NAME, f"[Merchant] Dispatching sell_materials to alts, trader at ({tmx:.0f}, {tmy:.0f})")
            sell_mat_refs = _dispatch_to_alts(
                SharedCommandType.MerchantMaterials,
                (tmx, tmy, 0, 0),
                ("sell", "", "", ""),
            )
            ConsoleLog(BOT_NAME, "[Merchant] Selling materials at trader (leader)")
            yield from Routines.Yield.Merchant.SellMaterialsAtTrader(tmx, tmy)
            yield from _wait_for_alt_dispatch_completion("sell_materials", sell_mat_refs, SharedCommandType.MerchantMaterials)
        else:
            ConsoleLog(BOT_NAME, "[Merchant] No Material Trader NPC found")

        # —— Step 4: Sell leftover stacks < 10 to regular merchant (leader + alts)
        if merchant_xy:
            mx, my = merchant_xy
            ConsoleLog(BOT_NAME, "[Merchant] Dispatching sell_merchant_leftovers to alts")
            leftover_refs = _dispatch_to_alts(
                SharedCommandType.MerchantMaterials,
                (mx, my, 0, 0),
                ("sell_merchant_leftovers", "", "10", ""),
            )
            leftover_ids = _get_leftover_material_item_ids()
            if leftover_ids:
                ConsoleLog(BOT_NAME, f"[Merchant] Selling {len(leftover_ids)} leftover stacks (leader)")
                yield from bot.Move._coro_xy_and_interact_npc(mx, my, "GH Merchant (leftovers)")
                yield from Routines.Yield.wait(NPC_INTERACT_SETTLE_MS)
                yield from Routines.Yield.Merchant.SellItems(leftover_ids, log=True)
                yield from Routines.Yield.wait(300)
            yield from _wait_for_alt_dispatch_completion(
                "sell_merchant_leftovers",
                leftover_refs,
                SharedCommandType.MerchantMaterials,
            )

    # —— Step 5: Sell non-salvageable gold items (anniversary weapons) to merchant —
    if merchant_xy:
        mx, my = merchant_xy
        ConsoleLog(BOT_NAME, "[Merchant] Dispatching sell_nonsalvageable_golds to alts")
        sell_gold_refs = _dispatch_to_alts(
            SharedCommandType.MerchantMaterials,
            (mx, my, 0, 0),
            ("sell_nonsalvageable_golds", "", "", ""),
        )
        yield from _coro_sell_nonsalvageable_golds(mx, my)
        yield from _wait_for_alt_dispatch_completion(
            "sell_nonsalvageable_golds",
            sell_gold_refs,
            SharedCommandType.MerchantMaterials,
        )

    # —— Step 6: Sell XP/insight scrolls to merchant (leader + alts) ——————————
    if merchant_xy:
        mx, my = merchant_xy
        ConsoleLog(BOT_NAME, "[Merchant] Dispatching sell_scrolls to alts")
        sell_scroll_refs = _dispatch_to_alts(
            SharedCommandType.MerchantMaterials,
            (mx, my, 0, 0),
            ("sell_scrolls", _SCROLL_MODEL_FILTER, "", ""),
        )
        yield from _coro_sell_scrolls(mx, my)
        yield from _wait_for_alt_dispatch_completion("sell_scrolls", sell_scroll_refs, SharedCommandType.MerchantMaterials)

    # —— Step 7: Restock kits (leader + alts) — after all selling to maximise free space
    if merchant_xy:
        mx, my = merchant_xy
        ConsoleLog(BOT_NAME, f"[Merchant] Merchant at ({mx:.0f}, {my:.0f}) — dispatching kits to alts")
        kit_refs = _dispatch_to_alts(
            SharedCommandType.MerchantItems,
            (mx, my, _merchant_id_kits_target, _merchant_salvage_kits_target),
        )
        yield from bot.Move._coro_xy_and_interact_npc(mx, my, "GH Merchant")
        yield from Routines.Yield.wait(NPC_INTERACT_SETTLE_MS)
        id_kits     = _count_model_in_inventory(_ModelID.Identification_Kit.value)
        sup_id_kits = _count_model_in_inventory(_ModelID.Superior_Identification_Kit.value)
        salvage_kits = _count_model_in_inventory(_ModelID.Salvage_Kit.value)
        id_to_buy      = max(0, _merchant_id_kits_target     - (id_kits + sup_id_kits))
        salvage_to_buy = max(0, _merchant_salvage_kits_target - salvage_kits)
        ConsoleLog(BOT_NAME, f"[Merchant] Buying {id_to_buy} ID kits, {salvage_to_buy} salvage kits")
        yield from Routines.Yield.Merchant.BuyIDKits(id_to_buy, log=True)
        yield from Routines.Yield.Merchant.BuySalvageKits(salvage_to_buy, log=True)
        yield from _wait_for_alt_dispatch_completion("restock_kits", kit_refs, SharedCommandType.MerchantItems)
        yield from Routines.Yield.wait(300)
    else:
        ConsoleLog(BOT_NAME, "[Merchant] No Merchant NPC found — skipping kit purchase")

    # —— Step 6: Sell Diamonds & Onyx to Rare Material Trader (leader + alts) ——
    if _merchant_sell_rare_mats:
        if rare_xy:
            rx, ry = rare_xy
            ConsoleLog(BOT_NAME, "[Merchant] Dispatching sell_rare_mats (Diamond/Onyx) to alts")
            rare_sell_refs = _dispatch_to_alts(
                SharedCommandType.MerchantMaterials,
                (rx, ry, 0, 0),
                ("sell_rare_mats", _RARE_MAT_FILTER, "", ""),
            )
            ConsoleLog(BOT_NAME, "[Merchant] Selling Diamond/Onyx at Rare Material Trader (leader)")
            yield from _coro_sell_rare_mats_at_trader(rx, ry, _RARE_MAT_MODELS)
            yield from _wait_for_alt_dispatch_completion(
                "sell_rare_mats",
                rare_sell_refs,
                SharedCommandType.MerchantMaterials,
            )
        else:
            ConsoleLog(BOT_NAME, "[Merchant] No Rare Material Trader found — skipping rare mat sell")

    # —— Step 7: Buy ectos from storage excess (leader + alts independently)
    # Storage is PER-ACCOUNT in GW — each account checks its own storage independently.
    # Always dispatch to alts so each alt can buy if ITS OWN storage exceeds threshold.
    if _merchant_buy_ectos and rare_xy:
        rx, ry = rare_xy
        ConsoleLog(BOT_NAME, f"[Merchant] Dispatching buy_ectoplasm to all alts (threshold={_merchant_ecto_threshold:,})")
        buy_ecto_refs = _dispatch_to_alts(
            SharedCommandType.MerchantMaterials,
            (rx, ry, _merchant_ecto_threshold, _merchant_ecto_threshold),
            ("buy_ectoplasm", "1", "0", ""),  # use_storage_gold=True; each alt checks own storage
        )
        # Leader buys from its own storage independently
        leader_storage = int(GLOBAL_CACHE.Inventory.GetGoldInStorage())
        if leader_storage > _merchant_ecto_threshold:
            ConsoleLog(BOT_NAME, f"[Merchant] Leader buying ectos (storage={leader_storage:,}, threshold={_merchant_ecto_threshold:,})")
            yield from Routines.Yield.Merchant.BuyEctoplasm(
                rx, ry,
                use_storage_gold=True,
                start_threshold=_merchant_ecto_threshold,
                stop_threshold=_merchant_ecto_threshold,
            )
        else:
            ConsoleLog(BOT_NAME, f"[Merchant] Leader storage ({leader_storage:,}) at/below threshold — skipping leader ecto buy")
        yield from _wait_for_alt_dispatch_completion("buy_ectoplasm", buy_ecto_refs, SharedCommandType.MerchantMaterials)
    elif _merchant_buy_ectos:
        ConsoleLog(BOT_NAME, "[Merchant] Ecto buy skipped — no Rare Material Trader found")

    # —— Step 8: Wait for alts to finish their queued actions —————————————————
    if _merchant_alt_wait_ms > 0:
        ConsoleLog(BOT_NAME, f"[Merchant] Final settle wait {_merchant_alt_wait_ms}ms")
        yield from Routines.Yield.wait(_merchant_alt_wait_ms)

    # —— Step 9: Return to Vlox's Fall ————————————————————————————————————————
    ConsoleLog(BOT_NAME, "[Merchant] Returning to Vlox's Fall")
    yield from _coro_travel_random_district(VLOXS_FALL_MAP_ID)
    ConsoleLog(BOT_NAME, "[Merchant] Guild Hall merchant run complete")
    yield

def _resign_all_to_outpost_before_merchant() -> Generator:
    ConsoleLog(BOT_NAME, "[Merchant] Resigning all accounts before Guild Hall merchant routine")
    start_map_id = int(Map.GetMapID())
    my_email = Player.GetAccountEmail()
    for acc in GLOBAL_CACHE.ShMem.GetAllAccountData():
        if acc.AccountEmail != my_email:
            GLOBAL_CACHE.ShMem.SendMessage(my_email, acc.AccountEmail, SharedCommandType.Resign, (0, 0, 0, 0), ("", "", "", ""))
    Player.SendChatCommand("resign")
    yield from Routines.Yield.wait(500)

    map_change_deadline = time.monotonic() + 45.0
    while time.monotonic() < map_change_deadline:
        if int(Map.GetMapID()) != start_map_id:
            break
        yield from Routines.Yield.wait(250)

    yield from bot.Wait._coro_until_on_outpost()


def _summon_and_invite_party(settle_ms: int = 1000) -> Generator:
    """Summon all alts to the leader's current map+district, then invite with retries.

    settle_ms -- extra wait after all alts have arrived before sending invites.
                 Use a larger value (e.g. 2500) after a Guild Hall merchant run
                 to give accounts more time to fully settle.
    """
    _my_email = Player.GetAccountEmail()
    _live_map = int(Map.GetMapID())

    # --- Wait for the leader's own ShMem to reflect the live map ---
    # Required so the district/region/language comparison below is valid.
    _ld_deadline = time.monotonic() + 15.0
    while time.monotonic() < _ld_deadline:
        _ld = GLOBAL_CACHE.ShMem.GetAccountDataFromEmail(_my_email)
        if _ld and int(_ld.AgentData.Map.MapID) == _live_map:
            break
        yield from Routines.Yield.wait(250)
    else:
        ConsoleLog(BOT_NAME, "[Party] Leader ShMem did not update in time — proceeding anyway", Py4GW.Console.MessageType.Warning)

    _ld = GLOBAL_CACHE.ShMem.GetAccountDataFromEmail(_my_email)
    if not _ld:
        ConsoleLog(BOT_NAME, "[Party] Could not read leader ShMem — skipping invite", Py4GW.Console.MessageType.Warning)
        yield
        return

    # --- Snapshot alt count BEFORE dispatching travel commands ---
    # GetAllAccountData() shrinks while alts are mid-travel (they temporarily
    # drop out of ShMem during map transitions). A fixed denominator prevents
    # the arrival check from satisfying itself prematurely.
    _expected_alt_count = len([
        acc for acc in GLOBAL_CACHE.ShMem.GetAllAccountData()
        if acc.AccountEmail != _my_email
    ])
    _expected_size = _expected_alt_count + 1  # leader + all alts

    ConsoleLog(BOT_NAME, f"[Party] Summoning {_expected_alt_count} alt(s) to current map")
    yield from bot.Multibox._helpers.Multibox._summon_all_accounts()

    # --- Wait until every alt's ShMem shows the same MapID+Region+Language+District ---
    # All four fields are required: Europe-Spanish-District1 and Europe-German-District1
    # share a MapID but are completely different zone instances.
    _arrival_deadline = time.monotonic() + 90.0
    ConsoleLog(BOT_NAME, f"[Party] Waiting for {_expected_alt_count} alt(s) to arrive on map {_live_map} "
                         f"(region={_ld.AgentData.Map.Region} language={_ld.AgentData.Map.Language} district={_ld.AgentData.Map.District})")
    while time.monotonic() < _arrival_deadline:
        _accounts = GLOBAL_CACHE.ShMem.GetAllAccountData()
        _arrived  = sum(
            1 for acc in _accounts
            if (acc.AccountEmail != _my_email                              and
                int(acc.AgentData.Map.MapID) == _live_map                  and
                acc.AgentData.Map.Region     == _ld.AgentData.Map.Region   and
                acc.AgentData.Map.Language   == _ld.AgentData.Map.Language and
                acc.AgentData.Map.District   == _ld.AgentData.Map.District)
        )
        if _arrived >= _expected_alt_count:
            ConsoleLog(BOT_NAME, f"[Party] All {_expected_alt_count} alt(s) on correct district — settling")
            break
        ConsoleLog(BOT_NAME, f"[Party] {_arrived}/{_expected_alt_count} alts on correct district — waiting")
        yield from Routines.Yield.wait(1000)
    else:
        _accounts = GLOBAL_CACHE.ShMem.GetAllAccountData()
        _arrived  = sum(
            1 for acc in _accounts
            if (acc.AccountEmail != _my_email                              and
                int(acc.AgentData.Map.MapID) == _live_map                  and
                acc.AgentData.Map.Region     == _ld.AgentData.Map.Region   and
                acc.AgentData.Map.Language   == _ld.AgentData.Map.Language and
                acc.AgentData.Map.District   == _ld.AgentData.Map.District)
        )
        ConsoleLog(BOT_NAME, f"[Party] Arrival timeout — {_arrived}/{_expected_alt_count} on correct district. Proceeding.", Py4GW.Console.MessageType.Warning)

    yield from Routines.Yield.wait(settle_ms)

    # --- Invite with retries ---
    # The framework method handles per-account matching (MapID+Region+Language+District+PartyID)
    # and profession-priority ordering.
    for _attempt in range(3):
        ConsoleLog(BOT_NAME, f"[Party] Sending invites (attempt {_attempt + 1})")
        yield from bot.Multibox._helpers.Multibox._invite_all_accounts()

        _party_deadline = time.monotonic() + 10.0
        while time.monotonic() < _party_deadline:
            if Party.GetPartySize() >= _expected_size:
                break
            yield from Routines.Yield.wait(500)

        _actual = Party.GetPartySize()
        if _actual >= _expected_size:
            ConsoleLog(BOT_NAME, f"[Party] Party full ({_actual}/{_expected_size}) after attempt {_attempt + 1}", log=True)
            yield
            return

        ConsoleLog(BOT_NAME, f"[Party] Party incomplete ({_actual}/{_expected_size}) — retrying", Py4GW.Console.MessageType.Warning)
        yield from Routines.Yield.wait(1500)

    _actual = Party.GetPartySize()
    ConsoleLog(BOT_NAME, f"[Party] Still incomplete after 3 attempts ({_actual}/{_expected_size}) — proceeding", Py4GW.Console.MessageType.Warning)
    yield

def _gh_merchant_setup_for_alt_salvage_threshold() -> Generator:
    _write_local_salvage_kit_count()
    yield from _request_alt_salvage_kit_counts()
    needs_restock, low_accounts, unknown_accounts = _alts_need_salvage_restock()
    if unknown_accounts:
        ConsoleLog(
            BOT_NAME,
            f"[Merchant] Alt salvage count unknown this pass: {', '.join(unknown_accounts)}",
            Py4GW.Console.MessageType.Warning,
        )
    if not needs_restock:
        yield
        return

    ConsoleLog(
        BOT_NAME,
        f"[Merchant] Alt salvage trigger hit: {', '.join(low_accounts)}. Running Guild Hall merchant routine.",
    )
    yield from _resign_all_to_outpost_before_merchant()
    yield from Routines.Yield.wait(10000)
    yield from _gh_merchant_setup(leave_party=True)
    bot.States.JumpToStepName("Reset Post Merchant")


def _gh_merchant_setup_if_inventory_full() -> Generator:
    """After quest reward: if only 1 free inventory slot remains, resign to outpost then run the full GH merchant routine."""
    free_slots = int(GLOBAL_CACHE.Inventory.GetFreeSlotCount())
    if free_slots > _inventory_slots_threshold:
        yield
        return
    ConsoleLog(BOT_NAME, f"[Merchant] Inventory nearly full ({free_slots} free slot) — resigning to outpost then triggering GH merchant run")

    yield from _resign_all_to_outpost_before_merchant()
    yield from Routines.Yield.wait(10000)
    yield from _gh_merchant_setup(leave_party=True)
    bot.States.JumpToStepName("Reset Post Merchant")

# --- Config Load / Save ---

def _ensure_ini_initialized() -> bool:
    """Load all settings and statistics from INI on first call. Returns True when ready."""
    global _settings_loaded
    global _use_hard_mode, _randomize_district
    global _merchant_enabled, _merchant_id_kits_target, _merchant_salvage_kits_target
    global _inventory_slots_threshold, _merchant_store_consumable_materials
    global _merchant_sell_materials, _merchant_sell_rare_mats, _merchant_buy_ectos
    global _merchant_ecto_threshold, _merchant_alt_wait_ms
    global _total_runs, _timed_runs, _total_run_time, _fastest_run, _slowest_run
    global _l1_total_time, _l1_fastest, _l1_slowest
    global _l2_total_time, _l2_fastest, _l2_slowest
    global _l3_total_time, _l3_fastest, _l3_slowest
    global _bds_drops, _gb_drops
    global TORCH_RUNNER_EMAIL, CHARACTER_EMAIL_MAP

    if _settings_loaded:
        return True

    _S = _SETTINGS_SECTION
    _use_hard_mode      = _settings_ini.read_bool(_S, "use_hard_mode",      True)
    _randomize_district = _settings_ini.read_bool(_S, "randomize_district", True)

    _M = _MERCHANT_SECTION
    _merchant_enabled                    = _settings_ini.read_bool(_M, "enabled",                    False)
    _merchant_id_kits_target             = _settings_ini.read_int( _M, "id_kits_target",             _FIXED_ID_KITS_TARGET)
    _merchant_salvage_kits_target        = _settings_ini.read_int( _M, "salvage_kits_target",        _FIXED_SALVAGE_KITS_TARGET)
    _inventory_slots_threshold           = max(0, _settings_ini.read_int(_M, "inventory_threshold",  1))
    _merchant_store_consumable_materials = _settings_ini.read_bool(_M, "store_consumable_materials", False)
    _merchant_sell_materials             = _settings_ini.read_bool(_M, "sell_materials",             False)
    _merchant_sell_rare_mats             = _settings_ini.read_bool(_M, "sell_rare_mats",             False)
    _merchant_buy_ectos                  = _settings_ini.read_bool(_M, "buy_ectos",                  False)
    _merchant_ecto_threshold             = _settings_ini.read_int( _M, "ecto_threshold",             800_000)
    _merchant_alt_wait_ms                = max(0, min(_MAX_ALT_SETTLE_WAIT_MS, _settings_ini.read_int(_M, "alt_wait_ms", _DEFAULT_ALT_SETTLE_WAIT_MS)))

    _SS = _STATS_SECTION
    _total_runs      = _settings_ini.read_int(  _SS, "total_runs",     0)
    _timed_runs      = _settings_ini.read_int(  _SS, "timed_runs",     _total_runs)
    _total_run_time  = _settings_ini.read_float(_SS, "total_run_time", 0.0)
    _f  = _settings_ini.read_float(_SS, "fastest_run", 0.0)
    _fastest_run     = float('inf') if _f  == 0.0 else _f
    _slowest_run     = _settings_ini.read_float(_SS, "slowest_run",    0.0)
    _l1_total_time   = _settings_ini.read_float(_SS, "l1_total_time",  0.0)
    _f1 = _settings_ini.read_float(_SS, "l1_fastest", 0.0)
    _l1_fastest      = float('inf') if _f1 == 0.0 else _f1
    _l1_slowest      = _settings_ini.read_float(_SS, "l1_slowest",     0.0)
    _l2_total_time   = _settings_ini.read_float(_SS, "l2_total_time",  0.0)
    _f2 = _settings_ini.read_float(_SS, "l2_fastest", 0.0)
    _l2_fastest      = float('inf') if _f2 == 0.0 else _f2
    _l2_slowest      = _settings_ini.read_float(_SS, "l2_slowest",     0.0)
    _l3_total_time   = _settings_ini.read_float(_SS, "l3_total_time",  0.0)
    _f3 = _settings_ini.read_float(_SS, "l3_fastest", 0.0)
    _l3_fastest      = float('inf') if _f3 == 0.0 else _f3
    _l3_slowest      = _settings_ini.read_float(_SS, "l3_slowest",     0.0)

    # Load all-time BDS drop totals so the UI shows correct values from the start
    # and _accumulate_bds adds on top of the correct base rather than starting from 0.
    _D = _BDS_DROPS_SECTION
    for _drop_key in _settings_ini.list_keys(_D):
        _bds_drops[_drop_key] = _settings_ini.read_int(_D, _drop_key, 0)

    # Seed any accounts seen in other sections that don't have a [BDS Drops] entry yet.
    # This ensures the UI shows all known accounts with 0 even before their first drop.
    for _seed_section in (_ALT_SALVAGE_SECTION, _BDS_SNAPSHOT_SECTION, _BDS_RUN_SECTION):
        for _seed_key in _settings_ini.list_keys(_seed_section):
            if _seed_key not in _bds_drops:
                _bds_drops[_seed_key] = 0

    _GD = _GB_DROPS_SECTION
    for _drop_key in _settings_ini.list_keys(_GD):
        _gb_drops[_drop_key] = _settings_ini.read_int(_GD, _drop_key, 0)
    for _seed_section in (_ALT_SALVAGE_SECTION, _GB_SNAPSHOT_SECTION, _GB_RUN_SECTION):
        for _seed_key in _settings_ini.list_keys(_seed_section):
            if _seed_key not in _gb_drops:
                _gb_drops[_seed_key] = 0

    _CN = _CHAR_NAMES_SECTION
    for _cn_key in _settings_ini.list_keys(_CN):
        _name = str(_settings_ini.read_key(_CN, _cn_key, "") or "").strip()
        if _name:
            _char_names[_cn_key] = _name

    # Load account email configuration — never hardcode emails in source.
    # On first run these keys will not exist; the bot will log a warning and
    # fall back to empty strings until the operator populates the INI manually.
    _A = _ACCOUNTS_SECTION
    TORCH_RUNNER_EMAIL = _settings_ini.read_key(_A, "torch_runner_email", "")
    if not TORCH_RUNNER_EMAIL:
        ConsoleLog(
            BOT_NAME,
            f"[Config] 'torch_runner_email' not set in {_settings_ini_rel_path} "
            f"under [{_ACCOUNTS_SECTION}]. Torch split will not function.",
            Py4GW.Console.MessageType.Warning,
        )

    CHARACTER_EMAIL_MAP.clear()
    for _char_key in _settings_ini.list_keys(_A):
        if _char_key == "torch_runner_email":
            continue
        _email = _settings_ini.read_key(_A, _char_key, "").strip()
        if _email:
            # Keys stored as character names with spaces replaced by underscores
            CHARACTER_EMAIL_MAP[_char_key.replace("_", " ").title()] = _email

    if not CHARACTER_EMAIL_MAP:
        ConsoleLog(
            BOT_NAME,
            f"[Config] No character email entries found in [{_ACCOUNTS_SECTION}] "
            f"in {_settings_ini_rel_path}. Slot-2 promotion will not function.",
            Py4GW.Console.MessageType.Warning,
        )

    _settings_loaded = True
    return True


def _write_settings() -> None:
    """Write all settings, statistics, and BDS drop totals to INI if a save has been requested."""
    global _save_requested
    if not _save_requested:
        return

    _S = _SETTINGS_SECTION
    _settings_ini.write_key(_S, "use_hard_mode",      str(_use_hard_mode))
    _settings_ini.write_key(_S, "randomize_district", str(_randomize_district))

    _M = _MERCHANT_SECTION
    _settings_ini.write_key(_M, "enabled",                    str(_merchant_enabled))
    _settings_ini.write_key(_M, "id_kits_target",             str(_merchant_id_kits_target))
    _settings_ini.write_key(_M, "salvage_kits_target",        str(_merchant_salvage_kits_target))
    _settings_ini.write_key(_M, "inventory_threshold",        str(_inventory_slots_threshold))
    _settings_ini.write_key(_M, "store_consumable_materials", str(_merchant_store_consumable_materials))
    _settings_ini.write_key(_M, "sell_materials",             str(_merchant_sell_materials))
    _settings_ini.write_key(_M, "sell_rare_mats",             str(_merchant_sell_rare_mats))
    _settings_ini.write_key(_M, "buy_ectos",                  str(_merchant_buy_ectos))
    _settings_ini.write_key(_M, "ecto_threshold",             str(_merchant_ecto_threshold))
    _settings_ini.write_key(_M, "alt_wait_ms",                str(_merchant_alt_wait_ms))

    _SS = _STATS_SECTION
    _settings_ini.write_key(_SS, "total_runs",     str(_total_runs))
    _settings_ini.write_key(_SS, "timed_runs",    str(_timed_runs))
    _settings_ini.write_key(_SS, "total_run_time", str(_total_run_time))
    _f  = 0.0 if _fastest_run == float('inf') else _fastest_run
    _settings_ini.write_key(_SS, "fastest_run",    str(_f))
    _settings_ini.write_key(_SS, "slowest_run",    str(_slowest_run))
    _f1 = 0.0 if _l1_fastest == float('inf') else _l1_fastest
    _settings_ini.write_key(_SS, "l1_total_time",  str(_l1_total_time))
    _settings_ini.write_key(_SS, "l1_fastest",     str(_f1))
    _settings_ini.write_key(_SS, "l1_slowest",     str(_l1_slowest))
    _f2 = 0.0 if _l2_fastest == float('inf') else _l2_fastest
    _settings_ini.write_key(_SS, "l2_total_time",  str(_l2_total_time))
    _settings_ini.write_key(_SS, "l2_fastest",     str(_f2))
    _settings_ini.write_key(_SS, "l2_slowest",     str(_l2_slowest))
    _f3 = 0.0 if _l3_fastest == float('inf') else _l3_fastest
    _settings_ini.write_key(_SS, "l3_total_time",  str(_l3_total_time))
    _settings_ini.write_key(_SS, "l3_fastest",     str(_f3))
    _settings_ini.write_key(_SS, "l3_slowest",     str(_l3_slowest))

    _D = _BDS_DROPS_SECTION
    for key, total in _bds_drops.items():
        _settings_ini.write_key(_D, key, str(total))

    _GD = _GB_DROPS_SECTION
    for key, total in _gb_drops.items():
        _settings_ini.write_key(_GD, key, str(total))

    _CN = _CHAR_NAMES_SECTION
    for key, name in _char_names.items():
        _settings_ini.write_key(_CN, key, name)

    _save_requested = False


def _save_settings() -> None:
    global _save_requested
    _save_requested = True


# --- Debug Logging System ---
# Captures comprehensive diagnostic info for each run so bugs/failures can be
# investigated after unattended runs. Three outputs per run:
#   1. Per-run .log file: full mirror of all debug events + ConsoleLog pass-through
#   2. Checkpoint list: major events (level starts, flanks, portal pushes, braziers, Fendi)
#   3. JSONL summary: structured per-run entry appended to runs_summary.jsonl

_DEBUG_LOG_DIR = Path(__file__).parent / "logs" / "soo"
try:
    _DEBUG_LOG_DIR.mkdir(parents=True, exist_ok=True)
except Exception:
    pass
_RUN_SUMMARY_FILE = _DEBUG_LOG_DIR / "runs_summary.jsonl"

_current_run_log_path: Optional[Path] = None
_current_run_checkpoints: list = []
_current_run_events: list = []
_current_run_errors: list = []

# =============================================================================
# Console capture — tees the torch runner's console output to per-run files so the full
# diagnostic history (ConsoleLog + print) is preserved without manual copy/paste.
# Files land at logs/soo/console/console_{YYYYMMDD_HHMMSS}.log.
# Crash-safe: every write is flushed immediately.
# =============================================================================
_CONSOLE_LOG_DIR: Path = _DEBUG_LOG_DIR / "console"
try:
    _CONSOLE_LOG_DIR.mkdir(parents=True, exist_ok=True)
except Exception:
    pass
_CONSOLE_LOG_KEEP = 30  # keep last N per-run files; older ones get deleted
_console_file = None                # current open file handle (or None)
_console_file_path: Optional[Path] = None
_console_capture_installed = False
_original_py4gw_log = None          # saved reference to Py4GW.Console.Log for unhooking
_original_stdout = None             # saved reference to sys.stdout

class _ConsoleTeeStdout:
    """Writes to both the original stdout and the per-run console file."""
    def __init__(self, original):
        self._original = original
    def write(self, msg: str):
        try:
            self._original.write(msg)
        except Exception:
            pass
        try:
            if _console_file is not None and not _console_file.closed and msg.strip():
                ts = time.strftime('%H:%M:%S')
                _console_file.write(f"[{ts}] [stdout] {msg.rstrip()}\n")
                _console_file.flush()
        except Exception:
            pass
    def flush(self):
        try:
            self._original.flush()
        except Exception:
            pass
        try:
            if _console_file is not None and not _console_file.closed:
                _console_file.flush()
        except Exception:
            pass
    def __getattr__(self, name):
        return getattr(self._original, name)

def _install_console_capture() -> None:
    """Install Py4GW.Console.Log monkeypatch + stdout tee. Idempotent."""
    global _console_capture_installed, _original_py4gw_log, _original_stdout
    if _console_capture_installed:
        return
    # Hook Py4GW.Console.Log if assignable
    try:
        import Py4GW as _py4gw_mod
        _original_py4gw_log = _py4gw_mod.Console.Log
        def _wrapped_log(sender, message, *args, **kwargs):
            # Always call original first so in-game console still works
            try:
                result = _original_py4gw_log(sender, message, *args, **kwargs)
            except Exception:
                result = None
            # Tee to file
            try:
                if _console_file is not None and not _console_file.closed:
                    ts = time.strftime('%H:%M:%S')
                    _console_file.write(f"[{ts}] [{sender}] {message}\n")
                    _console_file.flush()
            except Exception:
                pass
            return result
        try:
            _py4gw_mod.Console.Log = _wrapped_log
        except Exception:
            # Attribute not assignable (C binding) — we fall back to stdout-only capture
            _original_py4gw_log = None
    except Exception:
        _original_py4gw_log = None
    # Tee sys.stdout (captures raw print() calls)
    try:
        _original_stdout = sys.stdout
        sys.stdout = _ConsoleTeeStdout(_original_stdout)
    except Exception:
        _original_stdout = None
    _console_capture_installed = True

def _console_rotate_for_run() -> None:
    """Close any existing console file and open a fresh one for the new run."""
    global _console_file, _console_file_path
    # Close previous
    try:
        if _console_file is not None and not _console_file.closed:
            _console_file.write(f"[{time.strftime('%H:%M:%S')}] [console-capture] rotated\n")
            _console_file.close()
    except Exception:
        pass
    _console_file = None
    # Cleanup old files: keep last _CONSOLE_LOG_KEEP
    try:
        files = sorted(_CONSOLE_LOG_DIR.glob("console_*.log"), key=lambda p: p.stat().st_mtime)
        for old in files[:-_CONSOLE_LOG_KEEP]:
            try:
                old.unlink()
            except Exception:
                pass
    except Exception:
        pass
    # Open new file
    try:
        ts = time.strftime("%Y%m%d_%H%M%S")
        _console_file_path = _CONSOLE_LOG_DIR / f"console_{ts}.log"
        _console_file = open(_console_file_path, "a", encoding="utf-8", buffering=1)
        _console_file.write(f"[{time.strftime('%H:%M:%S')}] === Console capture started {ts} ===\n")
        _console_file.flush()
    except Exception:
        _console_file = None
        _console_file_path = None

def _console_finalize(marker: str = "RUN_END") -> None:
    """Flush + close the current console file. Safe to call multiple times."""
    global _console_file
    try:
        if _console_file is not None and not _console_file.closed:
            _console_file.write(f"[{time.strftime('%H:%M:%S')}] === {marker} ===\n")
            _console_file.flush()
            _console_file.close()
    except Exception:
        pass
    _console_file = None


def _debug_write(msg: str) -> None:
    """Append a line to the per-run log file. Silent on failure."""
    if _current_run_log_path is None:
        return
    try:
        with open(_current_run_log_path, "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
    except Exception:
        pass

def _debug_init_run() -> None:
    """Start a new per-run log file and clear in-memory buffers."""
    global _current_run_log_path, _current_run_checkpoints, _current_run_events, _current_run_errors
    # Ensure console capture is installed and rotate to a fresh console file
    _install_console_capture()
    _console_rotate_for_run()
    ts = time.strftime("%Y%m%d_%H%M%S")
    _current_run_log_path = _DEBUG_LOG_DIR / f"soo_run_{ts}.log"
    _current_run_checkpoints = []
    _current_run_events = []
    _current_run_errors = []
    _debug_write(f"=== SoO run started {ts} ===")
    ConsoleLog(BOT_NAME, f"[Debug] Logging to {_current_run_log_path.name}")
    if _console_file_path is not None:
        ConsoleLog(BOT_NAME, f"[Debug] Console capture → {_console_file_path.name}")

def _log_checkpoint(label: str, **kwargs) -> None:
    """Log a major milestone event. Also writes to per-run log + console."""
    try:
        px, py = Player.GetXY()
    except Exception:
        px, py = (0.0, 0.0)
    elapsed = time.time() - _t_run_start if _t_run_start > 0 else 0.0
    entry = {
        "t": time.time(),
        "elapsed": round(elapsed, 1),
        "label": label,
        "pos": [round(px, 1), round(py, 1)],
        **kwargs,
    }
    _current_run_checkpoints.append(entry)
    kv = " ".join(f"{k}={v}" for k, v in kwargs.items())
    msg = f"[CHECKPOINT] {label} @t+{elapsed:.0f}s pos=({px:.0f},{py:.0f}) {kv}".rstrip()
    ConsoleLog(BOT_NAME, msg)
    _debug_write(msg)

def _log_event(category: str, message: str, **kwargs) -> None:
    """Log a secondary event (death, unstuck, retry, warning)."""
    elapsed = time.time() - _t_run_start if _t_run_start > 0 else 0.0
    entry = {
        "t": time.time(),
        "elapsed": round(elapsed, 1),
        "category": category,
        "message": message,
        **kwargs,
    }
    _current_run_events.append(entry)
    kv = " ".join(f"{k}={v}" for k, v in kwargs.items())
    _debug_write(f"[{category}] {message} {kv}".rstrip())

def _log_error(where: str, exc: Exception) -> None:
    """Log an exception with traceback."""
    tb = traceback.format_exc()
    elapsed = time.time() - _t_run_start if _t_run_start > 0 else 0.0
    entry = {
        "t": time.time(),
        "elapsed": round(elapsed, 1),
        "where": where,
        "exception": repr(exc),
        "traceback": tb,
    }
    _current_run_errors.append(entry)
    _debug_write(f"[ERROR] {where}: {exc}\n{tb}")
    ConsoleLog(BOT_NAME, f"[ERROR] {where}: {exc}")

def _debug_finalize_run(run_time: float, l1_time: float, l2_time: float,
                        l3_time: float, is_outlier: bool, completed: bool = True) -> None:
    """Append a JSON summary entry for this run and close the per-run log."""
    try:
        summary = {
            "start_ts": _t_run_start,
            "end_ts": time.time(),
            "start_time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(_t_run_start)) if _t_run_start > 0 else "",
            "run_time": round(run_time, 1),
            "l1_time": round(l1_time, 1),
            "l2_time": round(l2_time, 1),
            "l3_time": round(l3_time, 1),
            "is_outlier": is_outlier,
            "completed": completed,
            "checkpoint_count": len(_current_run_checkpoints),
            "event_count": len(_current_run_events),
            "error_count": len(_current_run_errors),
            "checkpoints": _current_run_checkpoints,
            "events": _current_run_events,
            "errors": _current_run_errors,
            "log_file": _current_run_log_path.name if _current_run_log_path else None,
        }
        with open(_RUN_SUMMARY_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(summary) + "\n")
        _debug_write(
            f"=== Run complete: total={run_time:.0f}s "
            f"L1={l1_time:.0f}s L2={l2_time:.0f}s L3={l3_time:.0f}s "
            f"completed={completed} outlier={is_outlier} "
            f"checkpoints={len(_current_run_checkpoints)} "
            f"events={len(_current_run_events)} "
            f"errors={len(_current_run_errors)} ==="
        )
    except Exception as exc:
        ConsoleLog(BOT_NAME, f"[Debug] Failed to write run summary: {exc}")
    # Finalize the console capture file for this run
    marker = "RUN_END" if completed else "RUN_END_INCOMPLETE"
    _console_finalize(marker)


# --- Run Timing ---

def _mark_run_start() -> Generator:
    global _t_run_start, _t_l2_start, _t_l3_start, _current_l1_time, _current_l2_time, _current_l3_time
    _t_run_start     = time.time()
    _t_l2_start      = 0.0
    _t_l3_start      = 0.0
    _current_l1_time = 0.0
    _current_l2_time = 0.0
    _current_l3_time = 0.0
    _session_bds.clear()
    _session_gb.clear()
    _debug_init_run()
    _log_checkpoint("RUN_START", run_number=_total_runs + 1)
    yield


def _mark_l2_start() -> Generator:
    global _t_l2_start, _current_l1_time
    _t_l2_start     = time.time()
    _current_l1_time = _t_l2_start - _t_run_start if _t_run_start > 0 else 0.0
    _log_checkpoint("L2_START", l1_time=round(_current_l1_time, 1))
    yield


def _fendi_phase_monitor() -> Generator:
    """Background coroutine: continuously tracks Fendi Nin / Soul of Fendi
    phase transitions across the entire L3 floor.

    Why this exists: `resolve_fendi_fight` only tracks phases while it's the
    active FSM state. If the party kills Fendi before the torch runner reaches the arena
    (the "fly-by" pattern — 24% of RoJ runs, more common with faster builds),
    the state's phase counters remain zero and we lose all fight analytics.

    This monitor runs independent of the FSM from L3_START to RUN_END,
    so it captures phase data regardless of which state is active during the
    actual fight.

    Emits events under category `FENDI_MONITOR_PHASE` (distinct from the
    in-state `FENDI_PHASE` category) so analysis can use either source, with
    monitor data as the fallback/authoritative record for fly-by cases.

    Runs on the torch runner only — all run analytics live there.
    """
    my_email = Player.GetAccountEmail()
    if my_email != TORCH_RUNNER_EMAIL:
        return

    FENDI_NIN_MODEL_ID = 7064
    SOUL_OF_FENDI_MODEL_ID = 7065

    ConsoleLog(BOT_NAME, "[FENDI_MON] Phase monitor active")
    _log_checkpoint("FENDI_MONITOR_START")

    fendi_nin_present = False
    soul_present = False
    fendi_nin_phase_count = 0
    soul_phase_count = 0
    fendi_nin_phase_start: Optional[float] = None
    soul_phase_start: Optional[float] = None
    fendi_nin_total = 0.0
    soul_total = 0.0
    fendi_nin_first_sight: Optional[float] = None
    soul_first_sight: Optional[float] = None

    while True:
        try:
            # Stop when we leave L3 (run ended, died and returned, etc.)
            if Map.GetMapID() != SOO_LVL3_MAP_ID:
                # If a phase is still "open" when we leave, close it out
                now_t = time.time()
                if fendi_nin_present and fendi_nin_phase_start is not None:
                    fendi_nin_total += now_t - fendi_nin_phase_start
                if soul_present and soul_phase_start is not None:
                    soul_total += now_t - soul_phase_start
                _log_checkpoint("FENDI_MONITOR_SUMMARY",
                                fendi_nin_phases=fendi_nin_phase_count,
                                soul_phases=soul_phase_count,
                                total_fendi_nin_time=round(fendi_nin_total, 1),
                                total_soul_time=round(soul_total, 1),
                                saw_fendi_nin=fendi_nin_first_sight is not None,
                                saw_soul_of_fendi=soul_first_sight is not None)
                ConsoleLog(BOT_NAME,
                           f"[FENDI_MON] Exiting L3 — fn_phases={fendi_nin_phase_count} "
                           f"soul_phases={soul_phase_count} fn_t={fendi_nin_total:.1f}s "
                           f"soul_t={soul_total:.1f}s")
                return

            if not Routines.Checks.Map.MapValid():
                yield from Routines.Yield.wait(500)
                continue

            # Scan the entire enemy array for boss forms
            fendi_nin_seen_tick = False
            soul_seen_tick = False
            for agent_id in AgentArray.GetEnemyArray():
                if not Agent.IsAlive(agent_id):
                    continue
                model_id = Agent.GetModelID(agent_id)
                if model_id == FENDI_NIN_MODEL_ID:
                    fendi_nin_seen_tick = True
                elif model_id == SOUL_OF_FENDI_MODEL_ID:
                    soul_seen_tick = True
                if fendi_nin_seen_tick and soul_seen_tick:
                    break  # both flagged, no need to keep scanning

            now_t = time.time()

            # Fendi Nin transitions
            if fendi_nin_seen_tick and not fendi_nin_present:
                fendi_nin_present = True
                fendi_nin_phase_count += 1
                fendi_nin_phase_start = now_t
                if fendi_nin_first_sight is None:
                    fendi_nin_first_sight = now_t
                    _log_checkpoint("FENDI_MONITOR_FIRST_SIGHT_FENDI_NIN")
                _log_event("FENDI_MONITOR_PHASE",
                           f"Fendi Nin phase #{fendi_nin_phase_count} started",
                           phase=fendi_nin_phase_count, form="Fendi_Nin")
            elif not fendi_nin_seen_tick and fendi_nin_present:
                fendi_nin_present = False
                if fendi_nin_phase_start is not None:
                    dur = now_t - fendi_nin_phase_start
                    fendi_nin_total += dur
                    _log_event("FENDI_MONITOR_PHASE",
                               f"Fendi Nin phase #{fendi_nin_phase_count} ended after {dur:.1f}s",
                               phase=fendi_nin_phase_count, form="Fendi_Nin",
                               duration=round(dur, 1))
                    fendi_nin_phase_start = None

            # Soul of Fendi transitions
            if soul_seen_tick and not soul_present:
                soul_present = True
                soul_phase_count += 1
                soul_phase_start = now_t
                if soul_first_sight is None:
                    soul_first_sight = now_t
                    _log_checkpoint("FENDI_MONITOR_FIRST_SIGHT_SOUL_OF_FENDI")
                _log_event("FENDI_MONITOR_PHASE",
                           f"Soul of Fendi phase #{soul_phase_count} started",
                           phase=soul_phase_count, form="Soul_of_Fendi")
            elif not soul_seen_tick and soul_present:
                soul_present = False
                if soul_phase_start is not None:
                    dur = now_t - soul_phase_start
                    soul_total += dur
                    _log_event("FENDI_MONITOR_PHASE",
                               f"Soul of Fendi phase #{soul_phase_count} ended after {dur:.1f}s",
                               phase=soul_phase_count, form="Soul_of_Fendi",
                               duration=round(dur, 1))
                    soul_phase_start = None

            yield from Routines.Yield.wait(500)
        except Exception as exc:
            _log_error("fendi_phase_monitor", exc)
            yield from Routines.Yield.wait(1000)


def _mark_l3_start() -> Generator:
    global _t_l3_start, _current_l2_time
    _t_l3_start     = time.time()
    _current_l2_time = _t_l3_start - _t_l2_start if _t_l2_start > 0 else 0.0
    _log_checkpoint("L3_START", l2_time=round(_current_l2_time, 1))
    # Start the Fendi phase monitor as a background coroutine (only on the
    # torch runner; the coroutine self-exits on other accounts).
    bot.config.FSM.RemoveManagedCoroutine("FendiPhaseMonitor")
    bot.config.FSM.AddManagedCoroutine("FendiPhaseMonitor", _fendi_phase_monitor())
    yield


# ── Sub-stage instrumentation (telemetry only, no gameplay impact) ──
# These checkpoints slice the two highest-variance stages into sub-stages
# so we can target specific slow segments for optimization. Pure analytics:
# a _log_checkpoint call + yield. ~10us per call.
def _cp_l3_path_start() -> Generator:
    _log_checkpoint("L3_APPROACH_PATH_START")
    yield

def _cp_l3_path_end() -> Generator:
    _log_checkpoint("L3_APPROACH_PATH_END")
    yield

def _cp_l3_ooc_done() -> Generator:
    _log_checkpoint("L3_APPROACH_OOC_DONE")
    yield

def _cp_arena_rejoin_done() -> Generator:
    _log_checkpoint("ARENA_REJOIN_DONE")
    yield

def _cp_arena_brigant_done() -> Generator:
    _log_checkpoint("ARENA_BRIGANT_DONE")
    yield

def _cp_arena_door_approach() -> Generator:
    _log_checkpoint("ARENA_DOOR_APPROACH")
    yield

def _cp_arena_door_done() -> Generator:
    _log_checkpoint("ARENA_DOOR_DONE")
    yield

def _cp_arena_path_end() -> Generator:
    _log_checkpoint("ARENA_PATH_END")
    yield


def _record_run_end() -> Generator:
    """Record per-floor and overall run timings, increment run counter, persist."""
    global _total_runs, _timed_runs, _session_runs
    global _total_run_time, _fastest_run, _slowest_run
    global _l1_total_time, _l1_fastest, _l1_slowest
    global _l2_total_time, _l2_fastest, _l2_slowest
    global _l3_total_time, _l3_fastest, _l3_slowest
    global _current_run_time, _current_l3_time

    now = time.time()
    if _t_run_start > 0 and _t_l2_start > 0 and _t_l3_start > 0:
        run_time = now - _t_run_start
        l1_time  = _t_l2_start - _t_run_start
        l2_time  = _t_l3_start - _t_l2_start
        l3_time  = now - _t_l3_start

        _current_run_time = run_time
        _current_l3_time  = l3_time

        # Outlier detection: discard timing if >20% above current average
        is_outlier = False
        if _timed_runs > 0:
            avg_run = _total_run_time / _timed_runs
            avg_l1  = _l1_total_time  / _timed_runs
            avg_l2  = _l2_total_time  / _timed_runs
            avg_l3  = _l3_total_time  / _timed_runs
            threshold = 1.2
            outlier_reasons = []
            if run_time > avg_run * threshold: outlier_reasons.append(f"total {run_time:.0f}s > {avg_run * threshold:.0f}s")
            if l1_time  > avg_l1  * threshold: outlier_reasons.append(f"L1 {l1_time:.0f}s > {avg_l1 * threshold:.0f}s")
            if l2_time  > avg_l2  * threshold: outlier_reasons.append(f"L2 {l2_time:.0f}s > {avg_l2 * threshold:.0f}s")
            if l3_time  > avg_l3  * threshold: outlier_reasons.append(f"L3 {l3_time:.0f}s > {avg_l3 * threshold:.0f}s")
            if outlier_reasons:
                is_outlier = True
                ConsoleLog(BOT_NAME, f"[Run Timing] OUTLIER discarded — {', '.join(outlier_reasons)}")

        if not is_outlier:
            _total_run_time += run_time
            if run_time < _fastest_run: _fastest_run = run_time
            if run_time > _slowest_run: _slowest_run = run_time

            _l1_total_time += l1_time
            if l1_time < _l1_fastest: _l1_fastest = l1_time
            if l1_time > _l1_slowest: _l1_slowest = l1_time

            _l2_total_time += l2_time
            if l2_time < _l2_fastest: _l2_fastest = l2_time
            if l2_time > _l2_slowest: _l2_slowest = l2_time

            _l3_total_time += l3_time
            if l3_time < _l3_fastest: _l3_fastest = l3_time
            if l3_time > _l3_slowest: _l3_slowest = l3_time

            _timed_runs += 1

        ConsoleLog(BOT_NAME, f"[Run Timing] Total: {run_time:.0f}s  L1: {l1_time:.0f}s  L2: {l2_time:.0f}s  L3: {l3_time:.0f}s{' (OUTLIER)' if is_outlier else ''}")

        _log_checkpoint("RUN_END",
                        total=round(run_time, 1),
                        l1=round(l1_time, 1),
                        l2=round(l2_time, 1),
                        l3=round(l3_time, 1),
                        outlier=is_outlier)
        _debug_finalize_run(run_time, l1_time, l2_time, l3_time, is_outlier, completed=True)
    else:
        # Partial/failed run — still write summary for diagnosis
        now_t = time.time()
        partial_run = now_t - _t_run_start if _t_run_start > 0 else 0.0
        partial_l1 = (_t_l2_start - _t_run_start) if _t_l2_start > 0 and _t_run_start > 0 else 0.0
        partial_l2 = (_t_l3_start - _t_l2_start) if _t_l3_start > 0 and _t_l2_start > 0 else 0.0
        partial_l3 = (now_t - _t_l3_start) if _t_l3_start > 0 else 0.0
        _log_checkpoint("RUN_END_INCOMPLETE",
                        partial_total=round(partial_run, 1),
                        reached_l2=_t_l2_start > 0,
                        reached_l3=_t_l3_start > 0)
        _debug_finalize_run(partial_run, partial_l1, partial_l2, partial_l3, False, completed=False)

    _total_runs   += 1
    _session_runs += 1
    _save_settings()
    yield


# --- BDS Drop Tracking ---

def _take_dungeon_entry_snapshot() -> Generator:
    """Take dungeon-entry snapshot of BDS and GB counts: leader counts own inventory, alts report via IPC.

    Alts are queried ONE AT A TIME so each alt has exclusive access to the INI file
    before the next write goes out.  IniHandler does a full read-modify-write of the
    entire file on every write_key() call; if two alts write concurrently the last
    writer silently overwrites the other's result, leaving that account at the -1
    sentinel and causing its drop to be lost.
    """
    global _bds_pre_snapshot, _gb_pre_snapshot
    my_email     = Player.GetAccountEmail()
    alt_accounts = [acc for acc in GLOBAL_CACHE.ShMem.GetAllAccountData() if acc.AccountEmail != my_email]

    # Leader: take own in-memory snapshot and also write it to the INI so the
    # leader's key appears in [BDS Snapshot] / [BDS Run] the same as alts.
    _bds_pre_snapshot.clear()
    for model_id in BDS_MODEL_IDS:
        count = int(GLOBAL_CACHE.Inventory.GetModelCount(model_id))
        if count > 0:
            _bds_pre_snapshot[model_id] = count
    leader_snap_total = sum(_bds_pre_snapshot.values())
    _settings_ini.write_key(_BDS_SNAPSHOT_SECTION, _account_key(my_email), str(leader_snap_total))
    ConsoleLog(BOT_NAME, f"[BDS Stats] Leader dungeon-entry snapshot: {leader_snap_total} BDS")

    _gb_pre_snapshot = int(GLOBAL_CACHE.Inventory.GetModelCount(GB_MODEL_ID))
    _settings_ini.write_key(_GB_SNAPSHOT_SECTION, _account_key(my_email), str(_gb_pre_snapshot))
    ConsoleLog(BOT_NAME, f"[BDS Stats] Leader dungeon-entry GB snapshot: {_gb_pre_snapshot}")

    if not alt_accounts:
        yield
        return

    max_attempts = max(1, _BDS_IPC_POLL_MAX_TOTAL_MS // max(1, _BDS_IPC_POLL_TIMEOUT_MS))

    # Query each alt sequentially so their INI writes never race each other.
    for acc in alt_accounts:
        acc_key = _account_key(acc.AccountEmail)

        _settings_ini.write_key(_BDS_SNAPSHOT_SECTION, acc_key, str(-1))
        GLOBAL_CACHE.ShMem.SendMessage(
            my_email, acc.AccountEmail,
            SharedCommandType.InventoryQuery,
            (float(BDS_MODEL_ID_MIN), float(BDS_MODEL_ID_MAX), 0.0, 0.0),
            ("report_inventory_count", _settings_ini_rel_path, _BDS_SNAPSHOT_SECTION, acc_key),
        )
        responded = False
        for _ in range(max_attempts):
            yield from Routines.Yield.wait(_BDS_IPC_POLL_TIMEOUT_MS)
            if _settings_ini.read_int(_BDS_SNAPSHOT_SECTION, acc_key, -1) >= 0:
                responded = True
                break
        if not responded:
            name = acc.AgentData.CharacterName or acc.AccountEmail
            ConsoleLog(BOT_NAME, f"[BDS Stats] BDS snapshot timeout for: {name}", Py4GW.Console.MessageType.Warning)

        _settings_ini.write_key(_GB_SNAPSHOT_SECTION, acc_key, str(-1))
        GLOBAL_CACHE.ShMem.SendMessage(
            my_email, acc.AccountEmail,
            SharedCommandType.InventoryQuery,
            (float(GB_MODEL_ID), float(GB_MODEL_ID), 0.0, 0.0),
            ("report_inventory_count", _settings_ini_rel_path, _GB_SNAPSHOT_SECTION, acc_key),
        )
        responded = False
        for _ in range(max_attempts):
            yield from Routines.Yield.wait(_BDS_IPC_POLL_TIMEOUT_MS)
            if _settings_ini.read_int(_GB_SNAPSHOT_SECTION, acc_key, -1) >= 0:
                responded = True
                break
        if not responded:
            name = acc.AgentData.CharacterName or acc.AccountEmail
            ConsoleLog(BOT_NAME, f"[BDS Stats] GB snapshot timeout for: {name}", Py4GW.Console.MessageType.Warning)

    yield


def _record_drops_after_loot() -> Generator:
    """Request post-chest BDS and GB counts from alts, compute deltas, accumulate totals."""
    my_email     = Player.GetAccountEmail()
    my_key       = _account_key(my_email)
    alt_accounts = [acc for acc in GLOBAL_CACHE.ShMem.GetAllAccountData() if acc.AccountEmail != my_email]

    # Leader: compute own post-chest total and write it to [BDS Run] / [GB Run].
    leader_post_total = sum(int(GLOBAL_CACHE.Inventory.GetModelCount(m)) for m in BDS_MODEL_IDS)
    _settings_ini.write_key(_BDS_RUN_SECTION, my_key, str(leader_post_total))
    ConsoleLog(BOT_NAME, f"[BDS Stats] Leader post-chest total: {leader_post_total} BDS", log=True)

    leader_gb_post = int(GLOBAL_CACHE.Inventory.GetModelCount(GB_MODEL_ID))
    _settings_ini.write_key(_GB_RUN_SECTION, my_key, str(leader_gb_post))
    ConsoleLog(BOT_NAME, f"[BDS Stats] Leader post-chest GB total: {leader_gb_post}", log=True)

    if alt_accounts:
        max_attempts = max(1, _BDS_IPC_POLL_MAX_TOTAL_MS // max(1, _BDS_IPC_POLL_TIMEOUT_MS))

        # Query each alt sequentially — concurrent INI writes race and the last writer
        # silently overwrites the others.
        for acc in alt_accounts:
            acc_key = _account_key(acc.AccountEmail)

            _settings_ini.write_key(_BDS_RUN_SECTION, acc_key, str(-1))
            GLOBAL_CACHE.ShMem.SendMessage(
                my_email, acc.AccountEmail,
                SharedCommandType.InventoryQuery,
                (float(BDS_MODEL_ID_MIN), float(BDS_MODEL_ID_MAX), 0.0, 0.0),
                ("report_inventory_count", _settings_ini_rel_path, _BDS_RUN_SECTION, acc_key),
            )
            responded = False
            for _ in range(max_attempts):
                yield from Routines.Yield.wait(_BDS_IPC_POLL_TIMEOUT_MS)
                if _settings_ini.read_int(_BDS_RUN_SECTION, acc_key, -1) >= 0:
                    responded = True
                    break
            if not responded:
                name = acc.AgentData.CharacterName or acc.AccountEmail
                ConsoleLog(BOT_NAME, f"[BDS Stats] BDS count timeout for: {name}", Py4GW.Console.MessageType.Warning)

            _settings_ini.write_key(_GB_RUN_SECTION, acc_key, str(-1))
            GLOBAL_CACHE.ShMem.SendMessage(
                my_email, acc.AccountEmail,
                SharedCommandType.InventoryQuery,
                (float(GB_MODEL_ID), float(GB_MODEL_ID), 0.0, 0.0),
                ("report_inventory_count", _settings_ini_rel_path, _GB_RUN_SECTION, acc_key),
            )
            responded = False
            for _ in range(max_attempts):
                yield from Routines.Yield.wait(_BDS_IPC_POLL_TIMEOUT_MS)
                if _settings_ini.read_int(_GB_RUN_SECTION, acc_key, -1) >= 0:
                    responded = True
                    break
            if not responded:
                name = acc.AgentData.CharacterName or acc.AccountEmail
                ConsoleLog(BOT_NAME, f"[BDS Stats] GB count timeout for: {name}", Py4GW.Console.MessageType.Warning)

    # Accumulate deltas for ALL accounts (leader + alts) from the INI uniformly.
    all_accounts_keys = [my_key] + [_account_key(acc.AccountEmail) for acc in alt_accounts]
    total_bds_this_run = 0
    total_gb_this_run  = 0
    for acc_key in all_accounts_keys:
        post_count = max(0, _settings_ini.read_int(_BDS_RUN_SECTION,      acc_key, 0))
        snap_count = max(0, _settings_ini.read_int(_BDS_SNAPSHOT_SECTION, acc_key, 0))
        delta      = max(0, post_count - snap_count)
        ConsoleLog(BOT_NAME, f"[BDS Stats] {acc_key}: snap={snap_count} post={post_count} delta={delta}", log=True)
        _accumulate_drop(_BDS_DROPS_SECTION, _bds_drops, _session_bds, acc_key, delta)
        total_bds_this_run += delta

        gb_post = max(0, _settings_ini.read_int(_GB_RUN_SECTION,      acc_key, 0))
        gb_snap = max(0, _settings_ini.read_int(_GB_SNAPSHOT_SECTION, acc_key, 0))
        gb_delta = max(0, gb_post - gb_snap)
        ConsoleLog(BOT_NAME, f"[BDS Stats] {acc_key} GB: snap={gb_snap} post={gb_post} delta={gb_delta}", log=True)
        _accumulate_drop(_GB_DROPS_SECTION, _gb_drops, _session_gb, acc_key, gb_delta)
        total_gb_this_run += gb_delta

    ConsoleLog(BOT_NAME, f"[BDS Stats] Run complete. BDS={total_bds_this_run} GB={total_gb_this_run}", log=True)
    _save_settings()
    yield


def _accumulate_drop(
    ini_section: str,
    alltime: dict[str, int],
    session: dict[str, int],
    account_key: str,
    run_count: int,
) -> None:
    """Add run_count to both session and all-time totals for account_key."""
    if run_count <= 0:
        return
    current_total = alltime.get(account_key)
    if current_total is None:
        current_total = _settings_ini.read_int(ini_section, account_key, 0)
    alltime[account_key] = current_total + run_count
    session[account_key] = session.get(account_key, 0) + run_count


# --- Statistics and Run Tracking ---

def _draw_bds_stats() -> None:
    from Py4GWCoreLib import ImGui, Color

    _ensure_ini_initialized()

    # Refresh character name cache from live shared memory (mirrors Isolation Manager pattern)
    _new_names_found = False
    # Include the local account itself (not returned by GetAllAccountData)
    _my_em = str(Player.GetAccountEmail() or "").strip()
    _my_cn = str(Player.GetName() or "").strip()
    if _my_em and _my_cn:
        _my_key = _account_key(_my_em)
        if _char_names.get(_my_key) != _my_cn:
            _char_names[_my_key] = _my_cn
            _new_names_found = True
    for _acc in (GLOBAL_CACHE.ShMem.GetAllAccountData(sort_results=False, include_isolated=True) or []):
        _em  = str(_acc.AccountEmail or "").strip()
        _cn  = str(_acc.AgentData.CharacterName or "").strip()
        if _em and _cn:
            _acc_key = _account_key(_em)
            if _char_names.get(_acc_key) != _cn:
                _char_names[_acc_key] = _cn
                _new_names_found = True
    if _new_names_found:
        _save_settings()

    gold = Color(255, 210,  80, 255).to_tuple_normalized()
    cyan = Color( 80, 210, 255, 255).to_tuple_normalized()
    live = Color(100, 180, 255, 255).to_tuple_normalized()

    def _fmt_time(seconds: float) -> str:
        if seconds <= 0.0 or seconds == float('inf'):
            return "--:--"
        m, s = divmod(int(seconds), 60)
        return f"{m:02d}:{s:02d}"

    def _avg_time(total: float, runs: int) -> str:
        return _fmt_time(total / runs) if runs > 0 else "--:--"

    def _runs_per_drop(runs: int, drops: int) -> str:
        return f"{runs / drops:.1f}" if drops > 0 else "-"

    tbl_flags = (
        PyImGui.TableFlags.Borders        |
        PyImGui.TableFlags.RowBg          |
        PyImGui.TableFlags.SizingFixedFit |
        PyImGui.TableFlags.NoHostExtendX
    )
    _COL_W = 60.0  # standard column width — header text is always the widest element
    _ROW_H = 22    # uniform row height for vertical-centering helpers
    _HDR_COLOR = 26 | (38 << 8) | (51 << 16) | (255 << 24)

    def _vcenter() -> None:
        th = PyImGui.get_text_line_height()
        PyImGui.set_cursor_pos_y(PyImGui.get_cursor_pos_y() + max(0.0, (_ROW_H - th) / 2))

    def _ltext(s: str) -> None:
        _vcenter()
        PyImGui.text(s)

    def _ctext(s: str) -> None:
        _vcenter()
        avail = PyImGui.get_content_region_avail()[0]
        tw    = PyImGui.calc_text_size(s)[0]
        PyImGui.set_cursor_pos_x(PyImGui.get_cursor_pos_x() + max(0.0, (avail - tw) / 2))
        PyImGui.text(s)


    def _rtext(s: str) -> None:
        _vcenter()
        avail = PyImGui.get_content_region_avail()[0]
        tw    = PyImGui.calc_text_size(s)[0]
        PyImGui.set_cursor_pos_x(PyImGui.get_cursor_pos_x() + max(0.0, avail - tw))
        PyImGui.text(s)

    def _rtext_colored(s: str, color) -> None:
        _vcenter()
        avail = PyImGui.get_content_region_avail()[0]
        tw    = PyImGui.calc_text_size(s)[0]
        PyImGui.set_cursor_pos_x(PyImGui.get_cursor_pos_x() + max(0.0, avail - tw))
        PyImGui.text_colored(s, color)

    # ── Header ────────────────────────────────────────────────
    _bds_icon_exists = os.path.isfile(_BDS_ICON_PATH)
    if _bds_icon_exists:
        ImGui.image(_BDS_ICON_PATH, (24, 24))
        PyImGui.same_line(0, 8)
    PyImGui.text_colored("BDS Statistics", gold)
    PyImGui.separator()
    PyImGui.spacing()

    global _scramble_accounts
    _scramble_accounts = PyImGui.checkbox("Hide Account Names", _scramble_accounts)

    # ── Table 1: Overview ─────────────────────────────────────
    alltime_total_bds = sum(_bds_drops.values())
    session_total_bds = sum(_session_bds.values())

    layout_flags = PyImGui.TableFlags.NoBordersInBody | PyImGui.TableFlags.SizingStretchProp
    if PyImGui.begin_table("##bds_overview_layout", 2, layout_flags):
        PyImGui.table_setup_column("##col_session", PyImGui.TableColumnFlags.WidthStretch, 2.0)
        PyImGui.table_setup_column("##col_alltime", PyImGui.TableColumnFlags.WidthStretch, 3.0)

        PyImGui.table_next_row()
        PyImGui.table_set_column_index(0); PyImGui.text_colored("Session Overview", cyan)
        PyImGui.table_set_column_index(1); PyImGui.text_colored("Total Overview", cyan)

        PyImGui.table_next_row()

        PyImGui.table_set_column_index(0)
        if PyImGui.begin_table("##bds_session", 2, tbl_flags):
            PyImGui.table_setup_column("Runs", PyImGui.TableColumnFlags.WidthFixed, _COL_W)
            PyImGui.table_setup_column("BDS",  PyImGui.TableColumnFlags.WidthFixed, _COL_W)
            PyImGui.table_next_row(0, _ROW_H); PyImGui.table_set_bg_color(2, _HDR_COLOR, -1)
            PyImGui.table_set_column_index(0); _ctext("Runs")
            PyImGui.table_set_column_index(1); _ctext("BDS")
            PyImGui.table_next_row(0, _ROW_H)
            PyImGui.table_set_column_index(0); _rtext(str(_session_runs))
            PyImGui.table_set_column_index(1); _rtext(str(session_total_bds))
            PyImGui.end_table()

        PyImGui.table_set_column_index(1)
        if PyImGui.begin_table("##bds_alltime", 3, tbl_flags):
            PyImGui.table_setup_column("Runs", PyImGui.TableColumnFlags.WidthFixed, _COL_W)
            PyImGui.table_setup_column("BDS",  PyImGui.TableColumnFlags.WidthFixed, _COL_W)
            PyImGui.table_setup_column("Avg", PyImGui.TableColumnFlags.WidthFixed, _COL_W)
            PyImGui.table_next_row(0, _ROW_H); PyImGui.table_set_bg_color(2, _HDR_COLOR, -1)
            PyImGui.table_set_column_index(0); _ctext("Runs")
            PyImGui.table_set_column_index(1); _ctext("BDS")
            PyImGui.table_set_column_index(2); _ctext("Avg")
            PyImGui.table_next_row(0, _ROW_H)
            PyImGui.table_set_column_index(0); _rtext(str(_total_runs))
            PyImGui.table_set_column_index(1); _rtext(str(alltime_total_bds))
            PyImGui.table_set_column_index(2); _rtext(_runs_per_drop(_total_runs, alltime_total_bds))
            PyImGui.end_table()

        PyImGui.end_table()

    PyImGui.spacing()

    # ── Table 2: Run Timings ───────────────────────────────────
    PyImGui.text_colored("Run Timings", cyan)
    if PyImGui.begin_table("##bds_timings", 5, tbl_flags):
        PyImGui.table_setup_column("Floor",   PyImGui.TableColumnFlags.WidthFixed, _COL_W)
        PyImGui.table_setup_column("Current", PyImGui.TableColumnFlags.WidthFixed, _COL_W)
        PyImGui.table_setup_column("Avg",     PyImGui.TableColumnFlags.WidthFixed, _COL_W)
        PyImGui.table_setup_column("Best",    PyImGui.TableColumnFlags.WidthFixed, _COL_W)
        PyImGui.table_setup_column("Worst",   PyImGui.TableColumnFlags.WidthFixed, _COL_W)
        PyImGui.table_next_row(0, _ROW_H); PyImGui.table_set_bg_color(2, _HDR_COLOR, -1)
        PyImGui.table_set_column_index(0); _ctext("Floor")
        PyImGui.table_set_column_index(1); _ctext("Current")
        PyImGui.table_set_column_index(2); _ctext("Avg")
        PyImGui.table_set_column_index(3); _ctext("Best")
        PyImGui.table_set_column_index(4); _ctext("Worst")

        # Live clocks: tick while the floor/run is in progress, show last completed otherwise
        _now        = time.time()
        _run_active = _t_run_start > 0
        _l1_active  = _run_active and _t_l2_start == 0
        _l2_active  = _t_l2_start > 0 and _t_l3_start == 0
        _l3_active  = _t_l3_start > 0
        _live_run   = (_now - _t_run_start) if _run_active else _current_run_time
        _live_l1    = (_now - _t_run_start) if _l1_active  else _current_l1_time
        _live_l2    = (_now - _t_l2_start)  if _l2_active  else _current_l2_time
        _live_l3    = (_now - _t_l3_start)  if _l3_active  else _current_l3_time

        timing_rows = [
            ("Overall", _live_run, _run_active, _total_run_time, _fastest_run, _slowest_run),
            ("Floor 1", _live_l1,  _l1_active,  _l1_total_time,  _l1_fastest,  _l1_slowest),
            ("Floor 2", _live_l2,  _l2_active,  _l2_total_time,  _l2_fastest,  _l2_slowest),
            ("Floor 3", _live_l3,  _l3_active,  _l3_total_time,  _l3_fastest,  _l3_slowest),
        ]
        for label, current, is_live, total, fastest, slowest in timing_rows:
            PyImGui.table_next_row(0, _ROW_H)
            PyImGui.table_set_column_index(0); _ltext(label)
            if is_live:
                PyImGui.table_set_column_index(1); _rtext_colored(_fmt_time(current), live)
            else:
                PyImGui.table_set_column_index(1); _rtext(_fmt_time(current))
            PyImGui.table_set_column_index(2); _rtext(_avg_time(total, _timed_runs))
            PyImGui.table_set_column_index(3); _rtext(_fmt_time(fastest))
            PyImGui.table_set_column_index(4); _rtext(_fmt_time(slowest))

        PyImGui.end_table()

    PyImGui.spacing()

    def _draw_drop_table(
        title: str,
        table_id: str,
        alltime: dict[str, int],
        session: dict[str, int],
    ) -> None:
        PyImGui.text_colored(title, cyan)
        if PyImGui.begin_table(table_id, 4, tbl_flags):
            PyImGui.table_setup_column("Account",   PyImGui.TableColumnFlags.WidthStretch)
            PyImGui.table_setup_column("Session",   PyImGui.TableColumnFlags.WidthFixed, _COL_W)
            PyImGui.table_setup_column("All Time",  PyImGui.TableColumnFlags.WidthFixed, _COL_W)
            PyImGui.table_setup_column("Runs/Drop", PyImGui.TableColumnFlags.WidthFixed, _COL_W)
            PyImGui.table_next_row(0, _ROW_H); PyImGui.table_set_bg_color(2, _HDR_COLOR, -1)
            PyImGui.table_set_column_index(0); _ltext("Account")
            PyImGui.table_set_column_index(1); _ctext("Session")
            PyImGui.table_set_column_index(2); _ctext("All Time")
            PyImGui.table_set_column_index(3); _ctext("Avg")

            all_accounts = sorted(set(list(alltime.keys()) + list(session.keys())))
            session_total      = 0
            alltime_acct_total = 0
            for acct in all_accounts:
                s_count = session.get(acct, 0)
                a_count = alltime.get(acct, 0)
                session_total      += s_count
                alltime_acct_total += a_count
                PyImGui.table_next_row(0, _ROW_H)
                PyImGui.table_set_column_index(0); _ltext(_masked_email(acct))
                PyImGui.table_set_column_index(1); _rtext(str(s_count))
                PyImGui.table_set_column_index(2); _rtext(str(a_count))
                PyImGui.table_set_column_index(3); _rtext(_runs_per_drop(_total_runs, a_count))

            PyImGui.table_next_row(0, _ROW_H)
            PyImGui.table_set_column_index(0); _ltext("Total")
            PyImGui.table_set_column_index(1); _rtext_colored(str(session_total), gold)
            PyImGui.table_set_column_index(2); _rtext_colored(str(alltime_acct_total), gold)
            PyImGui.table_set_column_index(3); _rtext_colored(_runs_per_drop(_total_runs, alltime_acct_total), gold)

            PyImGui.end_table()

    # ── Table 3: BDS Drops per account ────────────────────────
    _draw_drop_table("BDS Drops", "##bds_drops", _bds_drops, _session_bds)

    PyImGui.spacing()

    # ── Table 4: Glacial Blades Drops per account ──────────────
    _draw_drop_table("Glacial Blades Drops", "##gb_drops", _gb_drops, _session_gb)




# ==================== AUTO SHRINE + STEP REGISTRY ====================

# Move step registry (per map)
_STEP_BY_NAME: dict[str, int] = {}  # name -> global index
_STEP_META: list[dict[str, object]] = []  # {idx,name,map_id,x,y}

# â€œLearned shrinesâ€ per map (from rez positions)
_SHRINES: dict[int, list[tuple[float, float]]] = {}

_LAST_STEP_NAME: Optional[str] = None
_LAST_STEP_IDX: int = -1

# Tune these
SHRINE_MERGE_DIST = 450.0   # merge learned shrines within this radius
RESUME_SEARCH_DIST = 1200.0 # max dist to find a nearby move step at rez

# --- Pathing, Shrines, and Step Registry ---

def S_BlacklistModel(model_id: int) -> Generator:
    """Custom FSM step: add a MODEL ID to loot blacklist (script-only)."""
    from Py4GWCoreLib.Routines import Routines
    from Py4GWCoreLib.py4gwcorelib_src.Lootconfig_src import LootConfig

    def _gen():
        loot = LootConfig()
        loot.AddToBlacklist(model_id)     # <- MODEL blacklist
        yield from Routines.Yield.wait(100)
        yield
    return _gen()


def S_BlacklistModels(model_ids) -> Generator:
    from Py4GWCoreLib.Routines import Routines
    from Py4GWCoreLib.py4gwcorelib_src.Lootconfig_src import LootConfig

    def _gen():
        loot = LootConfig()
        for model_id in model_ids:
            loot.AddToBlacklist(int(model_id))
        yield from Routines.Yield.wait(100)
        yield

    return _gen()

# --- Torch Runner Split Helpers ---

# Loaded at runtime from INI [Accounts] section — never hardcode emails in source.
TORCH_RUNNER_EMAIL:  str                = ""
CHARACTER_EMAIL_MAP: dict[str, str]     = {}

def _get_slot2_email() -> str | None:
    accounts = GLOBAL_CACHE.ShMem.GetAllAccountData()
    slot2 = next((acc for acc in accounts if acc.AgentPartyData.PartyPosition == 1), None)
    if slot2:
        char_name = slot2.AgentData.CharacterName
        # Direct lookup first; fall back to case-insensitive search in case INI casing differs
        email = CHARACTER_EMAIL_MAP.get(char_name)
        if email is None:
            char_name_lower = char_name.lower()
            email = next((v for k, v in CHARACTER_EMAIL_MAP.items() if k.lower() == char_name_lower), None)
        return email
    return None

def _disable_combat_for_skip() -> Generator:
    """Disable auto_combat and pause_on_danger so the torch runner ignores enemies during portal push."""
    bot.Properties.ApplyNow("auto_combat", "active", False)
    bot.Properties.ApplyNow("pause_on_danger", "active", False)
    ConsoleLog(BOT_NAME, "[Portal Push] Disabled auto_combat and pause_on_danger")
    yield


def _enable_combat_after_skip() -> Generator:
    """Re-enable auto_combat and pause_on_danger after portal transition."""
    bot.Properties.ApplyNow("auto_combat", "active", True)
    bot.Properties.ApplyNow("pause_on_danger", "active", True)
    ConsoleLog(BOT_NAME, "[Portal Push] Re-enabled auto_combat and pause_on_danger")
    yield


# OPERATOR-CONFIG: set `portal_rider` under `[Character Names]` in your local
# settings INI. Empty string disables the portal-rider behavior.
PORTAL_RIDER_CHARACTER_NAME = _settings_ini.read_key(_CHAR_NAMES_SECTION, "portal_rider", "")


def _portal_push_distract(
    label: str,
    alt_forward_stack: tuple[float, float],
    torch_runner_path: list[tuple[float, float]],
    final_map_id: int,
    aggro_wait_timeout_s: float = 10.0,
    distract_broadcast_interval_s: float = 1.0,
    max_attempts: int = 5,
    retry_wait_s: float = 4.0,
) -> Generator:
    """Generic distract-and-sneak portal push with body-block retry.
    1. PixelStacks other alts forward to `alt_forward_stack` to pull aggro
    2. PixelStacks the portal rider (configured via PORTAL_RIDER_CHARACTER_NAME) to the final
       waypoint so she attempts the portal crossing in parallel with the torch runner
    3. the torch runner follows `torch_runner_path` to reach the portal
    4. If map didn't change (the torch runner body-blocked by enemies), waits
       `retry_wait_s` for party to finish killing, then retries. Up to
       `max_attempts` total cycles.
    5. Waits for map change to `final_map_id` (success signal)

    Having two characters push the portal doubles the chance at least one
    reaches the trigger when an enemy is body-blocking a path.

    Used by L1→L2 and (analogous pattern) for any portal push where the torch runner
    walks into the portal rather than interacting with a gadget."""
    my_email = Player.GetAccountEmail()
    all_accounts = GLOBAL_CACHE.ShMem.GetAllAccountData()
    # Separate the portal rider from the distracting alts. The rider gets
    # sent to the torch runner's final portal coord; everyone else gets sent to the
    # aggro-distraction stack.
    rider_account = None
    alt_accounts = []
    for acc in all_accounts:
        if acc.AccountEmail == my_email:
            continue
        try:
            cname = acc.AgentData.CharacterName
        except Exception:
            cname = ""
        if cname == PORTAL_RIDER_CHARACTER_NAME:
            rider_account = acc
        else:
            alt_accounts.append(acc)
    portal_target = torch_runner_path[-1]

    torch_runner_x, torch_runner_y = Player.GetXY()
    _log_checkpoint(
        f"{label}_PORTAL_DISTRACT_START",
        torch_runner_pos=[round(torch_runner_x, 1), round(torch_runner_y, 1)],
        alt_stack=[alt_forward_stack[0], alt_forward_stack[1]],
        waypoints=len(torch_runner_path),
        alts=len(alt_accounts),
    )
    ConsoleLog(
        BOT_NAME,
        f"[{label} Portal] Sending {len(alt_accounts)} alt(s) forward to "
        f"({alt_forward_stack[0]:.0f},{alt_forward_stack[1]:.0f}) — the torch runner advancing in parallel",
    )

    # Snapshot the starting map ID so we can bail out the instant the map
    # starts changing — issuing move commands during a loading screen has
    # been observed to crash the GW client.
    starting_map_id = Map.GetMapID()

    for attempt in range(1, max_attempts + 1):
        # Phase 1 (parallel): Fire distract PixelStack to aggro-alts so they
        # pull enemies off the torch runner, AND fire a PixelStack to the portal rider
        # so she races to the portal coord alongside the torch runner.
        # Shandra-aware offset on both: if she's blocking either target,
        # the adjusted-target helper redirects each recipient around her.
        for acc in alt_accounts:
            tx, ty = alt_forward_stack[0], alt_forward_stack[1]
            ap = _safe_pos(acc)
            if ap is not None:
                tx, ty = _shandra_adjusted_target(
                    alt_forward_stack[0], alt_forward_stack[1], ap[0], ap[1],
                    context=f"{label}-portal-distract",
                )
            GLOBAL_CACHE.ShMem.SendMessage(
                my_email, acc.AccountEmail,
                SharedCommandType.PixelStack,
                (tx, ty, 0, 0),
            )
        if rider_account is not None:
            rtx, rty = portal_target[0], portal_target[1]
            rap = _safe_pos(rider_account)
            if rap is not None:
                rtx, rty = _shandra_adjusted_target(
                    portal_target[0], portal_target[1], rap[0], rap[1],
                    context=f"{label}-portal-rider",
                )
            GLOBAL_CACHE.ShMem.SendMessage(
                my_email, rider_account.AccountEmail,
                SharedCommandType.PixelStack,
                (rtx, rty, 0, 0),
            )
        yield from Routines.Yield.wait(1500)

        # Clear target so the torch runner doesn't try to interact with enemies en route
        try:
            Player.ChangeTarget(0)
        except Exception:
            pass

        _log_checkpoint(
            f"{label}_PORTAL_APPROACH",
            attempt=attempt,
            waypoints=len(torch_runner_path),
        )
        for wp_idx, (wx, wy) in enumerate(torch_runner_path, 1):
            # Quick exit if the map has changed (either to final map or mid-load).
            current_map = Map.GetMapID()
            if current_map != starting_map_id:
                _log_checkpoint(
                    f"{label}_PORTAL_MAP_CHANGED",
                    attempt=attempt,
                    at_waypoint=wp_idx - 1,
                    reason="pre_waypoint",
                    new_map=current_map,
                )
                return
            try:
                yield from bot.Move._coro_xy(wx, wy)
            except Exception as exc:
                _log_error(f"_portal_push_distract.move_wp{wp_idx}[{label}]", exc)
            # Also check immediately after the move completes — the map may have
            # transitioned mid-coroutine while we were moving.
            current_map = Map.GetMapID()
            if current_map != starting_map_id:
                _log_checkpoint(
                    f"{label}_PORTAL_MAP_CHANGED",
                    attempt=attempt,
                    at_waypoint=wp_idx,
                    reason="post_waypoint",
                    new_map=current_map,
                )
                return

        # Walked the full path — did we make it?
        current_map = Map.GetMapID()
        if current_map != starting_map_id:
            _log_checkpoint(
                f"{label}_PORTAL_PATH_DONE",
                attempt=attempt,
                final_pos=list(torch_runner_path[-1]),
                result="map_changed",
            )
            return

        # Path complete but still on the same map — the torch runner was likely body-
        # blocked by an enemy standing on the path. Wait for party to clear
        # the block, then retry.
        if attempt < max_attempts:
            ConsoleLog(
                BOT_NAME,
                f"[{label} Portal] Attempt {attempt}/{max_attempts}: map unchanged "
                f"(body-block?). Waiting {retry_wait_s:.1f}s then retrying.",
                Py4GW.Console.MessageType.Warning,
            )
            _log_checkpoint(
                f"{label}_PORTAL_RETRY",
                attempt=attempt,
                final_pos=list(torch_runner_path[-1]),
                wait_s=retry_wait_s,
            )
            yield from Routines.Yield.wait(int(retry_wait_s * 1000))
        else:
            ConsoleLog(
                BOT_NAME,
                f"[{label} Portal] FAILED after {max_attempts} attempts — "
                f"map still {current_map}, expected {final_map_id}",
                Py4GW.Console.MessageType.Error,
            )
            _log_checkpoint(
                f"{label}_PORTAL_PATH_DONE",
                attempt=attempt,
                final_pos=list(torch_runner_path[-1]),
                result="max_attempts_exceeded",
            )
    yield


def _l1_door_interact_with_retry(
    door_x: float,
    door_y: float,
    max_attempts: int = 3,
    ooc_timeout_s: float = 8.0,
) -> Generator:
    """Hardened L1 dungeon door interaction. Replaces the bare
    `bot.Move.XYAndInteractGadget(15100, 5443)` step which has no resilience —
    one InteractWithAgentXY timeout would trigger 'On Unmanaged Fail' and stop
    the bot (observed run 93 @ 20:15:35). This wrapper:
      1. Bounded wait for out-of-combat (so auto-attack/movement doesn't
         interfere with the gadget click).
      2. Re-Move to the door coords (refresh in case combat shoved the torch runner off).
      3. Try the gadget interact via the lower-level coroutine wrapped in
         try/except, so a single timeout doesn't bubble up as unmanaged.
      4. Up to `max_attempts` cycles; on total failure logs an error and yields
         control back — downstream path traversal still fires and the run can
         either recover or fail downstream organically.
    """
    for attempt in range(1, max_attempts + 1):
        # Bounded OOC wait — don't trust the unbounded coro_until_out_of_combat.
        ooc_deadline = time.time() + ooc_timeout_s
        while time.time() < ooc_deadline:
            try:
                if not Routines.Checks.Agents.InDanger(aggro_area=Range.Earshot):
                    break
            except Exception:
                break
            yield from Routines.Yield.wait(250)

        # Refresh position — combat may have nudged the torch runner out of interact range.
        try:
            yield from Routines.Yield.Movement.FollowPath([(door_x, door_y)])
        except Exception as exc:
            _log_error(f"_l1_door_interact_with_retry.move_attempt_{attempt}", exc)

        yield from Routines.Yield.wait(300)

        # Interact attempt. The lower-level coroutine yields path-then-interact;
        # we catch exceptions so a single failure doesn't escape to the FSM as
        # "unmanaged fail".
        try:
            yield from bot.Move._coro_xy_and_interact_gadget(door_x, door_y)
            ConsoleLog(BOT_NAME, f"[L1 Door] Interact attempt {attempt}/{max_attempts} completed", log=True)
            return
        except Exception as exc:
            _log_error(f"_l1_door_interact_with_retry.interact_attempt_{attempt}", exc)
            ConsoleLog(
                BOT_NAME,
                f"[L1 Door] Interact attempt {attempt}/{max_attempts} failed — retrying",
                Py4GW.Console.MessageType.Warning,
            )
            yield from Routines.Yield.wait(1000)

    ConsoleLog(
        BOT_NAME,
        f"[L1 Door] All {max_attempts} interact attempts failed — proceeding anyway, downstream may recover",
        Py4GW.Console.MessageType.Error,
    )
    yield


def _l2_transit_with_retry(
    door_x: float,
    door_y: float,
    alt_forward_stack: tuple[float, float],
    portal_points: list[tuple[float, float]],
    max_outer_attempts: int = 4,
    total_timeout_s: float = 240.0,
) -> Generator:
    """Outer retry wrapper for the L2 door + portal transition. Each cycle:
       1. Distract alts forward and interact with door
       2. Walk the torch runner through portal waypoints
       3. Check if map transitioned to L3 → done
       4. Otherwise retry from step 1.
    Hard timeout prevents the infinite hang we saw in run 63.
    """
    overall_start = time.time()
    outer_attempt = 0

    while Map.GetMapID() != SOO_LVL3_MAP_ID:
        # Hard timeout check — give up after total_timeout_s
        elapsed = time.time() - overall_start
        if elapsed > total_timeout_s:
            ConsoleLog(
                BOT_NAME,
                f"[L2 Transit] HARD TIMEOUT after {elapsed:.0f}s across {outer_attempt} attempts — giving up",
                Py4GW.Console.MessageType.Error,
            )
            _log_event(
                "L2_TRANSIT_TIMEOUT",
                f"Hard timeout after {elapsed:.0f}s, {outer_attempt} attempts",
                elapsed_s=round(elapsed, 1),
                attempts=outer_attempt,
            )
            return

        if outer_attempt >= max_outer_attempts:
            ConsoleLog(
                BOT_NAME,
                f"[L2 Transit] Max outer attempts ({max_outer_attempts}) reached — giving up",
                Py4GW.Console.MessageType.Error,
            )
            _log_event(
                "L2_TRANSIT_MAX_ATTEMPTS",
                f"Gave up after {max_outer_attempts} outer attempts",
                attempts=outer_attempt,
            )
            return

        outer_attempt += 1
        ConsoleLog(BOT_NAME, f"[L2 Transit] Outer attempt {outer_attempt}/{max_outer_attempts}")
        _log_checkpoint("L2_TRANSIT_OUTER_ATTEMPT", attempt=outer_attempt)

        # Phase A: distract + door interact (reuse existing _l2_distract_and_sneak)
        yield from _l2_distract_and_sneak(
            door_x=door_x,
            door_y=door_y,
            alt_forward_stack=alt_forward_stack,
        )
        if Map.GetMapID() == SOO_LVL3_MAP_ID:
            _enable_torch_runner_custom_behaviors_immediate()
            _log_checkpoint("L2_TRANSIT_SUCCESS", stage="after_door", attempt=outer_attempt)
            return

        # Phase B: push through portal (reuse existing _l2_portal_push)
        yield from _l2_portal_push(portal_points)
        if Map.GetMapID() == SOO_LVL3_MAP_ID:
            _enable_torch_runner_custom_behaviors_immediate()
            _log_checkpoint("L2_TRANSIT_SUCCESS", stage="after_portal", attempt=outer_attempt)
            return

        # Still not transitioned — small pause and retry
        ConsoleLog(
            BOT_NAME,
            f"[L2 Transit] Outer attempt {outer_attempt} didn't transition — retrying",
            Py4GW.Console.MessageType.Warning,
        )
        yield from Routines.Yield.wait(2000)

    # Natural while-loop exit: the map flipped to L3 between outer attempts
    # (most commonly during the 2s retry wait after a PORTAL_PUSH_END that
    # fired with pos=(0,0) while still loading). CustomBehaviors was disabled
    # at portal-push start and must be re-enabled here too — otherwise the torch runner
    # enters L3 with no combat AI.
    _enable_torch_runner_custom_behaviors_immediate()
    _log_checkpoint("L2_TRANSIT_SUCCESS", stage="already_in_l3", attempt=outer_attempt)


def _l2_distract_and_sneak(
    door_x: float,
    door_y: float,
    alt_forward_stack: tuple[float, float],
    aggro_wait_timeout_s: float = 10.0,
    distract_broadcast_interval_s: float = 1.0,
    max_door_attempts: int = 4,
    verify_wait_s: float = 3.0,
) -> Generator:
    """L2 door failsafe — "distract and sneak" approach.

    1. PixelStacks all alts forward to `alt_forward_stack` (near the door enemy
       cluster) to pull aggro off the torch runner. the torch runner holds position at her current
       location until alts engage.
    2. Once party is in combat (aggro detected) OR timeout elapses, the torch runner
       advances to the door with auto_combat/pause_on_danger still disabled.
    3. Interacts with the gadget and verifies via map change. Retries the
       interact up to `max_door_attempts` times if the transition doesn't
       register (e.g. knocked down during interact animation).

    Logs each phase transition via _log_checkpoint / _log_event for debugging
    and easy rollback if the approach doesn't work as intended.
    """
    my_email = Player.GetAccountEmail()
    all_accounts = GLOBAL_CACHE.ShMem.GetAllAccountData()
    alt_accounts = [acc for acc in all_accounts if acc.AccountEmail != my_email]

    torch_runner_x, torch_runner_y = Player.GetXY()
    _log_checkpoint(
        "L2_DISTRACT_START",
        torch_runner_pos=[round(torch_runner_x, 1), round(torch_runner_y, 1)],
        alt_stack=[alt_forward_stack[0], alt_forward_stack[1]],
        door=[door_x, door_y],
        alts=len(alt_accounts),
    )
    ConsoleLog(
        BOT_NAME,
        f"[L2 Distract] Sending {len(alt_accounts)} alt(s) forward to "
        f"({alt_forward_stack[0]:.0f},{alt_forward_stack[1]:.0f}) — the torch runner advancing in parallel",
    )

    # Phase 1 (parallel): Fire distract PixelStack, wait 1.5s for alts to
    # physically move in front of the torch runner, then she advances. Shandra-aware:
    # if she's at the alt_forward_stack point, offset per alt so they route
    # around her instead of stacking on her.
    for acc in alt_accounts:
        tx, ty = alt_forward_stack[0], alt_forward_stack[1]
        ap = _safe_pos(acc)
        if ap is not None:
            tx, ty = _shandra_adjusted_target(
                alt_forward_stack[0], alt_forward_stack[1], ap[0], ap[1],
                context="L2-distract",
            )
        GLOBAL_CACHE.ShMem.SendMessage(
            my_email, acc.AccountEmail,
            SharedCommandType.PixelStack,
            (tx, ty, 0, 0),
        )
    yield from Routines.Yield.wait(1500)

    # Clear target so the torch runner doesn't interact with enemies en route
    try:
        Player.ChangeTarget(0)
    except Exception:
        pass
    _log_checkpoint("L2_DOOR_APPROACH", target=[door_x, door_y])

    # Phase 2: Single gadget interact, no retry, no verify wait.
    # The outer _l2_transit_with_retry handles genuine failures by re-running
    # the whole distract + door + portal cycle. The portal walk that follows
    # has its own early-exit on map change, so if the door opened the transit
    # succeeds regardless of whether the verify caught it here.
    if Map.GetMapID() == SOO_LVL3_MAP_ID:
        _log_checkpoint("L2_DOOR_OPENED", attempts=0, method="pre-interact")
        return

    ConsoleLog(BOT_NAME, "[L2 Door] Interacting with gadget (single attempt)")

    # Suppress framework's "On Unmanaged Fail" handler for the duration of the
    # door interact. Without this, InteractWithGadgetXY's internal "No target
    # after targeting" path calls `bot.Stop()` directly via
    # `self._Events.on_unmanaged_fail()` — which is NOT a Python exception, so
    # try/except can't catch it (observed run 137 @ 09:02:08: framework halted
    # the bot on a single door-interact miss despite the try/except wrapper).
    # By swapping in a handler that returns False, the gadget coroutine
    # returns False cleanly and the outer `_l2_transit_with_retry` gets to
    # re-run the whole distract+door+portal cycle on its own retry.
    _l2_door_fail_suppressed = {"fired": False}

    def _suppress_unmanaged_fail() -> bool:
        _l2_door_fail_suppressed["fired"] = True
        return False  # don't stop the bot; let the outer retry handle it

    try:
        bot.helpers.Events.set_on_unmanaged_fail(_suppress_unmanaged_fail)
        try:
            yield from bot.Move._coro_xy_and_interact_gadget(door_x, door_y)
        except Exception as exc:
            _log_error("_l2_distract_and_sneak.door_interact", exc)
    finally:
        bot.helpers.Events.reset_on_unmanaged_fail()

    if _l2_door_fail_suppressed["fired"]:
        ConsoleLog(
            BOT_NAME,
            "[L2 Door] Interact hit framework unmanaged-fail — suppressed; outer retry will handle",
            Py4GW.Console.MessageType.Warning,
        )
        _log_event(
            "L2_DOOR_UNMANAGED_FAIL_SUPPRESSED",
            "InteractWithGadgetXY failed; outer L2 transit retry will re-run",
        )

    _log_checkpoint("L2_DOOR_INTERACT_DONE", map_changed=(Map.GetMapID() == SOO_LVL3_MAP_ID))
    yield


def _l2_portal_push(points: list[tuple[float, float]]) -> Generator:
    """After unlocking the L2 dungeon door, clear the torch runner's target and force her
    through the remaining waypoints via PixelStack. Prevents her from engaging
    enemies the main party is fighting nearby.

    The portal rider (PORTAL_RIDER_CHARACTER_NAME from INI) is continuously pushed to the FINAL
    waypoint throughout the entire sequence — not the current intermediate
    waypoint like other alts. This way if the torch runner gets body-blocked, Winged
    Supremacy is already racing ahead to the portal trigger; whichever
    character reaches it first zones the whole party."""
    my_email = Player.GetAccountEmail()
    all_accounts = GLOBAL_CACHE.ShMem.GetAllAccountData()
    rider_account = None
    alt_accounts = []
    for acc in all_accounts:
        if acc.AccountEmail == my_email:
            continue
        try:
            cname = acc.AgentData.CharacterName
        except Exception:
            cname = ""
        if cname == PORTAL_RIDER_CHARACTER_NAME:
            rider_account = acc
        else:
            alt_accounts.append(acc)
    portal_target = points[-1] if points else None

    # Clear any current/called target so she doesn't try to interact with enemies
    try:
        Player.ChangeTarget(0)
    except Exception as exc:
        _log_error("_l2_portal_push.ChangeTarget", exc)

    push_start = time.time()
    _log_checkpoint(
        "L2_PORTAL_PUSH_START",
        waypoints=len(points), alts=len(alt_accounts),
        rider_found=(rider_account is not None),
    )

    # Remember the L2 map ID at start so we can detect the zone transition.
    starting_map_id = Map.GetMapID()

    for wp_idx, (x, y) in enumerate(points, 1):
        # If we've already zoned (into L3), stop pushing — any further PixelStack
        # sends would use stale L2 coordinates on L3 and make alts run toward
        # invalid map positions.
        if Map.GetMapID() != starting_map_id:
            ConsoleLog(BOT_NAME, f"[L2 Portal Push] Map changed — halting PixelStack push to prevent stale coord drift")
            break

        ConsoleLog(BOT_NAME, f"[L2 Portal Push] Waypoint {wp_idx}/{len(points)} -> ({x}, {y})")
        wp_start = time.time()
        move_deadline = time.monotonic() + 15.0
        last_push = 0.0
        timed_out = True
        while time.monotonic() < move_deadline:
            now = time.monotonic()
            # Early exit on zone transition — critical so we don't broadcast
            # L2 waypoint coords to alts after they've loaded into L3.
            if Map.GetMapID() != starting_map_id:
                timed_out = False
                break
            # Re-send PixelStack + leader Move every second to override any combat state
            if now - last_push >= 1.0:
                # Also re-clear target in case CustomBehaviors re-assigns one
                try:
                    Player.ChangeTarget(0)
                except Exception:
                    pass
                # Shandra-aware dispatch: offset each alt around her if she's
                # on the current waypoint, and offset the rider around her if
                # she's at the portal trigger.
                for acc in alt_accounts:
                    tx, ty = x, y
                    ap = _safe_pos(acc)
                    if ap is not None:
                        tx, ty = _shandra_adjusted_target(
                            x, y, ap[0], ap[1],
                            context="L2-portal-wp",
                        )
                    GLOBAL_CACHE.ShMem.SendMessage(
                        my_email, acc.AccountEmail,
                        SharedCommandType.PixelStack, (tx, ty, 0, 0),
                    )
                # Portal rider goes to the FINAL waypoint, not the current
                # intermediate one, so she races ahead to the portal trigger.
                if rider_account is not None and portal_target is not None:
                    rtx, rty = portal_target[0], portal_target[1]
                    rap = _safe_pos(rider_account)
                    if rap is not None:
                        rtx, rty = _shandra_adjusted_target(
                            portal_target[0], portal_target[1], rap[0], rap[1],
                            context="L2-portal-rider",
                        )
                    GLOBAL_CACHE.ShMem.SendMessage(
                        my_email, rider_account.AccountEmail,
                        SharedCommandType.PixelStack,
                        (rtx, rty, 0, 0),
                    )
                Player.Move(x, y)
                last_push = now

            px, py = Player.GetXY()
            dist = ((px - x) ** 2 + (py - y) ** 2) ** 0.5
            if dist < 300:
                timed_out = False
                break
            yield

        wp_elapsed = time.time() - wp_start
        if timed_out:
            _log_event("L2_PORTAL_PUSH", f"Waypoint {wp_idx} TIMED OUT after {wp_elapsed:.1f}s",
                       waypoint=wp_idx, target=[x, y])

    _log_checkpoint("L2_PORTAL_PUSH_END", duration=round(time.time() - push_start, 1))
    yield


def _disable_torch_runner_custom_behaviors() -> Generator:
    """Disable CustomBehaviors on the torch runner only so she pushes to the door while the party fights."""
    my_email = Player.GetAccountEmail()
    GLOBAL_CACHE.ShMem.SendMessage(my_email, TORCH_RUNNER_EMAIL, SharedCommandType.DisableWidget, (0, 0, 0, 0), ("CustomBehaviors", "", "", ""))
    ConsoleLog(BOT_NAME, "[Portal Push] Disabled CustomBehaviors on the torch runner")
    _log_checkpoint("PORTAL_PUSH_START")
    yield


def _enable_torch_runner_custom_behaviors_immediate() -> None:
    """Re-enable CustomBehaviors on the torch runner with minimum latency. Since this runs
    on the torch runner's own account, we bypass the shared-memory message roundtrip and
    enable the widget directly via the local widget handler — saves ~100-500ms
    vs sending EnableWidget via SendMessage to self."""
    try:
        wh = _get_wh()
        wh.enable_widget("CustomBehaviors")
        ConsoleLog(BOT_NAME, "[Portal Push] Re-enabled CustomBehaviors on the torch runner (local/instant)")
        _log_checkpoint("PORTAL_PUSH_END")
    except Exception as exc:
        _log_error("_enable_torch_runner_custom_behaviors_immediate", exc)
        # Fallback to shared-memory approach if local enable fails
        try:
            my_email = Player.GetAccountEmail()
            GLOBAL_CACHE.ShMem.SendMessage(my_email, TORCH_RUNNER_EMAIL, SharedCommandType.EnableWidget, (0, 0, 0, 0), ("CustomBehaviors", "", "", ""))
            ConsoleLog(BOT_NAME, "[Portal Push] Re-enabled CustomBehaviors on the torch runner (fallback shmem)")
        except Exception as exc2:
            _log_error("_enable_torch_runner_custom_behaviors_immediate.fallback", exc2)


def _enable_torch_runner_custom_behaviors() -> Generator:
    """Generator wrapper used by L1→L2 state flow (kept for back-compat)."""
    _enable_torch_runner_custom_behaviors_immediate()
    yield


def _disable_party_member_behind() -> Generator:
    """Disable OnPartyMemberBehind and OnPartyMemberDeadBehind during torch split.

    Replaces BOTH callbacks with no-op lambdas directly. We used to rely on a
    module-level flag checked inside the original lambda, but that proved
    unreliable — the behind routine kept firing during torch split. Direct
    replacement is authoritative.
    """
    global _ignore_party_member_behind
    _ignore_party_member_behind = True
    bot.Events.OnPartyMemberBehindCallback(lambda: None)
    bot.Events.OnPartyMemberDeadBehindCallback(lambda: None)
    ConsoleLog(BOT_NAME, "[Torch Split] Disabled OnPartyMemberBehind and OnPartyMemberDeadBehind callbacks (direct override)")
    yield


def _reenable_party_member_behind() -> Generator:
    """Restore OnPartyMemberBehind and OnPartyMemberDeadBehind after torch split."""
    global _ignore_party_member_behind
    _ignore_party_member_behind = False
    bot.Events.OnPartyMemberBehindCallback(lambda: bot.Templates.Routines.OnPartyMemberBehind())
    bot.Events.OnPartyMemberDeadBehindCallback(lambda: bot.Templates.Routines.OnPartyMemberDeathBehind())
    ConsoleLog(BOT_NAME, "[Torch Split] Re-enabled OnPartyMemberBehind and OnPartyMemberDeadBehind callbacks")
    yield


# =============================================================================
# AccountStruct position / liveness helpers (module scope so portal pushes,
# torch split, and Shandra offset logic can all share one implementation).
# =============================================================================
# Multi-run diagnosis (Run 130, 131): reading position from a remote
# account's own agent ID does NOT work from the torch runner's process. Each GW client
# assigns its own local agent IDs; `AccountStruct.AgentData.AgentID` is that
# submitting account's local ID and is meaningless in another process. The
# Run 131 log confirmed the broken behavior: every `CONVERGE_ENTER` reported
# `slot2_pos=[0.0, 0.0]` and every downstream Shandra-offset/convergence
# decision ran as a silent no-op.
#
# Correct approach: resolve email → the torch runner's local agent ID by matching
# character names. `Party.Players.GetPlayers()` gives the torch runner's view of the
# party (login numbers + character names visible in her instance); each
# AccountStruct carries its own character name. Match by name to get the
# local agent ID the torch runner's process can actually use with Agent.GetXY() etc.
#
# Caching: keyed by map_id. Agent IDs are stable within a map but change on
# zone transitions; the cache auto-invalidates by never being re-queried for
# a stale map_id (new map = new cache entry).

_email_to_local_agent_id_cache: dict[int, dict[str, int]] = {}


def _resolve_party_agent_ids() -> dict[str, int]:
    """Return email → local-agent-id mapping for the current map.

    Lazily built per-map. Matches each party player's character name (as
    visible in the torch runner's local instance) against each AccountStruct's
    CharacterName. Returns empty dict if the scan fails.
    """
    try:
        map_id = int(Map.GetMapID())
    except Exception:
        return {}
    cached = _email_to_local_agent_id_cache.get(map_id)
    if cached is not None:
        return cached

    email_to_id: dict[str, int] = {}
    try:
        # the torch runner's-view name → local agent id
        name_to_agent_id: dict[str, int] = {}
        try:
            players = GLOBAL_CACHE.Party.Players.GetPlayers()
        except Exception:
            players = []
        for player in players:
            try:
                aid = GLOBAL_CACHE.Party.Players.GetAgentIDByLoginNumber(
                    player.login_number
                )
                if aid:
                    name = (Agent.GetNameByID(int(aid)) or "").strip()
                    if name:
                        name_to_agent_id[name] = int(aid)
            except Exception:
                continue

        # Match AccountStruct.CharacterName → local agent id
        try:
            accs = GLOBAL_CACHE.ShMem.GetAllAccountData()
        except Exception:
            accs = []
        for acc in accs:
            try:
                char_name = (getattr(acc.AgentData, "CharacterName", "") or "").strip()
                email = getattr(acc, "AccountEmail", "") or ""
                if char_name and email and char_name in name_to_agent_id:
                    email_to_id[email] = name_to_agent_id[char_name]
            except Exception:
                continue
    except Exception as exc:
        _log_error("_resolve_party_agent_ids", exc)

    _email_to_local_agent_id_cache[map_id] = email_to_id
    try:
        _log_event(
            "PARTY_AGENT_ID_RESOLVED",
            f"map={map_id} resolved {len(email_to_id)} email→agent_id mappings",
            map_id=map_id,
            count=len(email_to_id),
        )
    except Exception:
        pass
    return email_to_id


def _safe_pos(acc) -> tuple[float, float] | None:
    """Defensively read an AccountStruct's position. Returns None if unreadable.

    Resolution chain (first success wins):
      1. torch-runner-local agent id via email → name → agent_id mapping (primary).
         This is the only path that reliably returns real coords for remote
         accounts, because agent IDs are per-client/per-instance.
      2. AccountStruct.AgentData.AgentID → Agent.GetXY (works for the LOCAL
         player only — kept as a fallback for single-client code paths).
      3. AccountStruct.AgentData.Position.X/Y (usually unpopulated for remotes,
         but kept as a last resort for anything that does write to it).
    """
    # Primary: resolve via the torch runner's local agent-id map
    try:
        email = getattr(acc, "AccountEmail", "") or ""
        if email:
            id_map = _resolve_party_agent_ids()
            aid = id_map.get(email, 0)
            if aid:
                xy = Agent.GetXY(int(aid))
                if xy and xy[0] is not None and xy[1] is not None:
                    return (float(xy[0]), float(xy[1]))
    except Exception:
        pass
    # Fallback 1: account's own agent ID (useful for the local player)
    try:
        aid = acc.AgentData.AgentID
        if aid:
            xy = Agent.GetXY(int(aid))
            if xy and xy[0] is not None and xy[1] is not None:
                return (float(xy[0]), float(xy[1]))
    except Exception:
        pass
    # Fallback 2: raw AccountStruct position
    try:
        return (acc.AgentData.Position.X, acc.AgentData.Position.Y)
    except Exception:
        return None


def _safe_is_dead(acc) -> bool:
    """Defensively read Is_Dead; missing attr → treat as alive."""
    try:
        return bool(getattr(acc.AgentData, "Is_Dead", False))
    except Exception:
        return False


# =============================================================================
# Shandra body-block mitigation (dungeon-wide)
# =============================================================================
# Crewmember Shandra is the quest-giver NPC that follows the party through all
# three dungeon levels. She regularly body-blocks alts at chokepoints when
# the torch runner broadcasts PixelStack messages to multiple alts aimed at the same
# point (L1 portal push, L2 door distract, L2 portal push, L3 torch split
# strict flank / convergence push). BruteForceUnstuck CAN usually strafe
# around her, but during combat the strafe loop is slower than the incoming
# PixelStack queue, leading to the party-wide thrash observed in Run 130.
#
# Strategy: before dispatching PixelStack to multiple alts at a shared target,
# check if Shandra is within _SHANDRA_BLOCK_RADIUS of that point. If so,
# offset each alt's broadcast perpendicular to THEIR approach vector, away
# from Shandra, so they naturally path around her instead of stacking.
# The existing strafe recovery still runs; this just reduces how often it's
# needed by preventing the stale-message pileup.
_SHANDRA_NPC_NAME         = "Crewmember Shandra"
_SHANDRA_SCAN_RADIUS      = 20000.0   # whole-instance scan
_SHANDRA_BLOCK_RADIUS     = 300.0     # consider Shandra blocking within this
_SHANDRA_OFFSET_DISTANCE  = 350.0     # perpendicular offset applied

# Cache keyed by map_id → agent_id. Shandra's agent id is stable within a
# map but may change on zone transition, so we re-resolve per-map.
_shandra_cache: dict[int, int] = {}


def _resolve_shandra() -> int:
    """Resolve Shandra's current-map agent_id, caching by map_id. Returns 0
    if she can't be found (scan whiffed, or she isn't in this map).
    Safe to call repeatedly — cache hit is a dict lookup."""
    try:
        map_id = int(Map.GetMapID())
    except Exception:
        return 0
    cached = _shandra_cache.get(map_id, -1)
    if cached != -1:
        return cached
    found = 0
    try:
        px, py = Player.GetXY()
        npcs = AgentArray.GetNPCMinipetArray()
        npcs = AgentArray.Filter.ByDistance(npcs, (px, py), _SHANDRA_SCAN_RADIUS)
        for nid in npcs:
            try:
                if _SHANDRA_NPC_NAME in (Agent.GetNameByID(int(nid)) or ""):
                    found = int(nid)
                    break
            except Exception:
                continue
    except Exception as exc:
        _log_error("_resolve_shandra", exc)
    _shandra_cache[map_id] = found
    if found:
        _log_event("SHANDRA_RESOLVED", f"map={map_id} agent_id={found}",
                   map_id=map_id, agent_id=found)
    else:
        _log_event("SHANDRA_NOT_FOUND",
                   f"map={map_id} — offset disabled for this map",
                   map_id=map_id)
    return found


def _shandra_pos() -> tuple[float, float] | None:
    """Current position of Shandra in the active map, or None."""
    aid = _resolve_shandra()
    if not aid:
        return None
    try:
        xy = Agent.GetXY(aid)
        if xy and xy[0] is not None and xy[1] is not None:
            return (float(xy[0]), float(xy[1]))
    except Exception:
        pass
    return None


def _shandra_blocks(tx: float, ty: float) -> bool:
    """True iff Shandra is within _SHANDRA_BLOCK_RADIUS of (tx, ty)."""
    pos = _shandra_pos()
    if pos is None:
        return False
    dx = pos[0] - tx
    dy = pos[1] - ty
    return (dx * dx + dy * dy) <= (_SHANDRA_BLOCK_RADIUS ** 2)


def _shandra_offset(tx: float, ty: float, from_x: float, from_y: float) -> tuple[float, float]:
    """If Shandra is blocking (tx, ty), return a perpendicular-offset target
    (away from her side) so the approach routes around her. Offset is applied
    along the axis perpendicular to (from -> target); it does NOT move the
    target along the approach axis (that could push alts past a combat point).
    If not blocking, returns (tx, ty) unchanged."""
    pos = _shandra_pos()
    if pos is None:
        return (tx, ty)
    sx, sy = pos
    dsx = sx - tx
    dsy = sy - ty
    if (dsx * dsx + dsy * dsy) > (_SHANDRA_BLOCK_RADIUS ** 2):
        return (tx, ty)
    ax = tx - from_x
    ay = ty - from_y
    amag = (ax * ax + ay * ay) ** 0.5
    if amag < 1.0:
        return (tx, ty)
    ux = ax / amag
    uy = ay / amag
    pxu, pyu = -uy, ux  # perp (rotate 90°)
    # Pick sign AWAY from Shandra (flip if she's on the chosen perp side)
    if dsx * pxu + dsy * pyu > 0:
        pxu, pyu = -pxu, -pyu
    return (tx + pxu * _SHANDRA_OFFSET_DISTANCE, ty + pyu * _SHANDRA_OFFSET_DISTANCE)


def _shandra_adjusted_target(
    tx: float, ty: float, from_x: float, from_y: float,
    *, context: str = "",
) -> tuple[float, float]:
    """One-liner for broadcast sites: given a dispatch target and the approach
    source (usually the recipient alt's current position), return the target
    to actually dispatch. Logs SHANDRA_OFFSET_APPLIED when the target was
    redirected. Transparently pass-through when Shandra isn't blocking, isn't
    in this map, or can't be resolved."""
    new_tx, new_ty = _shandra_offset(tx, ty, from_x, from_y)
    if new_tx != tx or new_ty != ty:
        _log_event(
            "SHANDRA_OFFSET_APPLIED",
            f"{context} offset applied",
            context=context,
            orig=[round(tx, 0), round(ty, 0)],
            offset=[round(new_tx, 0), round(new_ty, 0)],
        )
    return (new_tx, new_ty)


def _party_waypoint_manager() -> Generator:
    """
    Background managed coroutine: sends path2_part2 waypoints to the party one at a time,
    gated by slot 2's combat state. Runs in parallel with the torch runner's torch run.
    """
    my_email = Player.GetAccountEmail()
    path2_part2 = [
        (-1123.5,  7481.9),
        (-2964.1,  7302.1),
        (-3139.7,  7022.7),
        (-4152.0,  6469.6),
        (-3716.0,  5706.0),
        (-3094.0,  4714.0),
        (-4418.0,  3966.0),
        (-6364.0,  4925.0),
        (-6633.0,  5849.0),   # detour N to cover enemy group often missed
        (-7845.7,  4397.5),
        (-8049.0,  5403.5),
        (-9049.9,  5289.2),
        (-10051.1, 4604.6),
        (-11057.4, 4039.1),
        (-10381.7, 3037.7),
    ]
    party_accounts = [
        acc for acc in GLOBAL_CACHE.ShMem.GetAllAccountData()
        if acc.AccountEmail != TORCH_RUNNER_EMAIL
    ]
    # Enforcement parameters
    PIXELSTACK_REPEAT_INTERVAL_S = 2.0   # re-send PixelStack every 2s during waits
    ARRIVAL_RADIUS = 700.0                # alt considered "arrived" within this distance
    ARRIVAL_TIMEOUT_S = 12.0              # max time to wait for stragglers after combat clears

    # Safe waypoints — these never have enemies, so we skip the combat detection
    # and OOC-wait polling to save ~1-2s of latency per waypoint. The leader
    # arrival wait is preserved so path traversal still completes.
    SAFE_WAYPOINTS = {1, 2, 3}            # P1, P2, P3 are guaranteed enemy-free

    # Strict flank waypoints — hard-force all alts through with aggressive PixelStack
    # and fixed wait duration. Prevents hero drift toward the 2nd enemy group.
    # These are 1-indexed waypoints matching `enumerate(path2_part2, 1)`.
    STRICT_FLANK_WAYPOINTS = {5, 6}       # (-3716, 5706) and (-3094, 4714)
    STRICT_HOLD_S = 5.0                   # total hold duration per strict waypoint
    # Broadcast interval tripled from 0.5s → 1.5s. Prior rate flooded each alt's
    # inbox with 10-16 stale PixelStack messages across wp5+wp6 (5s × 0.5s +
    # 3s × 0.5s = 16 broadcasts × 7 alts = 112 msgs). Run 130 post-mortem:
    # slot 2 spent 14.5s grinding through queued stale targets via
    # BruteForceUnstuck before ever reaching wp7's PixelStack — drifted to
    # (-5244, 4975), aggro'd solo, died. At 1.5s interval the queue is ~3
    # broadcasts per wp instead of 10; combined with straggler-only filtering
    # below, typical alts receive 0-2 messages per strict waypoint.
    STRICT_PUSH_INTERVAL_S = 1.5
    # Only broadcast to alts further than this from the strict-wp target.
    # Alts already inside the radius don't need pushing and shouldn't have
    # their inbox polluted with stale destinations.
    STRICT_STRAGGLER_RADIUS = 700.0
    # Leader gets a SINGLE PixelStack at strict-wp entry, not the broadcast
    # flood. Same logic: prevents leader inbox saturation that caused slot 2's
    # overshoot in Run 130 while keeping her moving forward through the flank.
    # CB disable/enable still applies to her (combat still suppressed).
    # Safety cap for the EnableWidget-ack wait at wp6 start. If the alts
    # haven't all confirmed by this point, proceed anyway and log the miss.
    ENABLE_WIDGET_ACK_MAX_S = 6.0

    # Call-target waypoints — at these enemy-group waypoints, scan for a priority
    # target (monk > non-melee caster > nearest) and broadcast it as the party
    # called target so heroes focus-fire.
    CALL_TARGET_WAYPOINTS = {4, 7, 8}     # P4, P7, P8 enemy groups
    CALL_TARGET_SEARCH_RADIUS = 1500.0    # look for enemies within this radius of the waypoint
    MONK_PROFESSION_ID = 3                # GW primary profession ID for Monk
    # After this waypoint, clear the party called target so utilities resume
    # natural cluster/nearest targeting for the rest of the torch-split path.
    CLEAR_PARTY_TARGET_AFTER_WAYPOINT = 8

    # --- Mechanism A: Squad convergence gate (fixes #1 + #2) ---
    # Root cause of Run 127 flank failure: slot 2 (slot 2 / promoted
    # leader) walked into P7 ahead of the main party, aggro'd burst damage
    # alone, died out of earshot of any ally carrying a res scroll — party
    # stalled, P7 was never cleared, the torch runner couldn't return.
    #
    # Gate: before slot 2 arrives at an enemy-group waypoint, confirm
    # ≥ CONVERGE_MIN_ALLIES followers are within Earshot of her. If not,
    # aggressively re-push stragglers toward her position and wait up to
    # CONVERGE_MAX_WAIT_S. Proceeds anyway after the cap (with warning log)
    # so we don't deadlock on a genuinely-stuck alt.
    CONVERGE_MIN_ALLIES        = 2
    CONVERGE_EARSHOT_SQ        = (Range.Earshot.value * 1.0) ** 2
    CONVERGE_MAX_WAIT_S        = 4.0
    CONVERGE_PUSH_INTERVAL_S   = 0.5
    CONVERGE_POLL_INTERVAL_S   = 0.25

    # --- Mechanism B: Slot 2 death-recovery (fixes #3 + #4) ---
    # If slot 2 dies mid-split, find the nearest living non-the torch runner ally and
    # push them toward her corpse until within res-scroll range; the normal
    # GenericResurrectionUtility on that ally will fire and res her. Gives
    # up after SLOT2_DEATH_RECOVERY_MAX_S and logs TORCH_SPLIT_UNRECOVERABLE;
    # the IsPartyDefeated() bail at the top of the loop handles wipe.
    SLOT2_RES_RANGE_U           = Range.Spellcast.value * 1.5  # matches GenericResurrectionUtility
    SLOT2_RES_RANGE_SQ          = SLOT2_RES_RANGE_U ** 2
    SLOT2_DEATH_RECOVERY_MAX_S  = 15.0
    SLOT2_DEATH_PUSH_INTERVAL_S = 0.5
    SLOT2_DEATH_POLL_INTERVAL_S = 0.25

    # Shandra body-block mitigation is provided by module-level helpers
    # (_shandra_blocks, _shandra_offset, _shandra_adjusted_target) wired in
    # at every multi-alt PixelStack broadcast site across the dungeon.
    # Prime the per-map cache here so the first convergence push doesn't
    # pay the scan cost inline.
    _resolve_shandra()

    def _find_priority_target(wp_x: float, wp_y: float) -> tuple[int, str]:
        """Find a priority target near a waypoint: monk > non-melee caster > nearest.
        Returns (agent_id, label). agent_id=0 if none found."""
        source = (wp_x, wp_y)
        try:
            Targets = custom_behavior_helpers.Targets
            # 1. nearest monk
            monk_id = Targets.get_nearest_or_default_from_enemy_ordered_by_priority_custom_source(
                source_agent_pos=source,
                within_range=CALL_TARGET_SEARCH_RADIUS,
                should_prioritize_party_target=False,
                condition=lambda agent_id: Agent.GetProfessions(agent_id)[0] == MONK_PROFESSION_ID,
            )
            if monk_id:
                return monk_id, "monk"
            # 2. nearest non-melee caster
            caster_id = Targets.get_nearest_or_default_from_enemy_ordered_by_priority_custom_source(
                source_agent_pos=source,
                within_range=CALL_TARGET_SEARCH_RADIUS,
                should_prioritize_party_target=False,
                condition=lambda agent_id: not Agent.IsMelee(agent_id),
            )
            if caster_id:
                return caster_id, "caster"
            # 3. nearest enemy (any)
            nearest_id = Targets.get_nearest_or_default_from_enemy_ordered_by_priority_custom_source(
                source_agent_pos=source,
                within_range=CALL_TARGET_SEARCH_RADIUS,
                should_prioritize_party_target=False,
            )
            if nearest_id:
                return nearest_id, "nearest"
        except Exception as exc:
            _log_error("_find_priority_target", exc)
        return 0, "none"

    def _call_party_target(wp_idx: int, wp_x: float, wp_y: float) -> None:
        """Send a call-priority-target command to all alts (including slot 2 who
        is physically at the waypoint). Each receiver scans from its own position
        for monk > caster > nearest and sets party_custom_target. the torch runner's local
        scan would miss these enemies since she's at the torch chest area."""
        try:
            sender = Player.GetAccountEmail()
            # Params[0] = 2.0 signals "call priority target" to custom_behavior_party
            # message handler. ExtraData is unused for this action.
            for acc in party_accounts:
                GLOBAL_CACHE.ShMem.SendMessage(
                    sender, acc.AccountEmail,
                    SharedCommandType.CustomBehaviors, (2.0, 0.0, 0.0, 0.0),
                    ("", "", "", ""),
                )
            ConsoleLog(
                BOT_NAME,
                f"[Torch Split] wp{wp_idx} dispatched call-target command to {len(party_accounts)} alts",
            )
            _log_event(
                "TORCH_SPLIT_CALL_TARGET_DISPATCH",
                f"wp{wp_idx} dispatched call-target command",
                waypoint=wp_idx, alts=len(party_accounts),
            )
            return
        except Exception as exc:
            _log_error("_call_party_target.dispatch", exc)

        # Fallback: local scan from the torch runner (likely misses — she's far away)
        target_id, target_type = _find_priority_target(wp_x, wp_y)
        if target_id:
            try:
                CustomBehaviorParty().set_party_custom_target(target_id)
                ConsoleLog(
                    BOT_NAME,
                    f"[Torch Split] wp{wp_idx} called {target_type} target "
                    f"(agent={target_id}) for party focus-fire (fallback)",
                )
                _log_event(
                    "TORCH_SPLIT_CALLED_TARGET",
                    f"wp{wp_idx} called {target_type} (fallback)",
                    waypoint=wp_idx, target_type=target_type, agent_id=target_id,
                )
            except Exception as exc:
                _log_error("_call_party_target.fallback", exc)
        else:
            ConsoleLog(
                BOT_NAME,
                f"[Torch Split] wp{wp_idx} no priority target found within {CALL_TARGET_SEARCH_RADIUS:.0f}u",
                Py4GW.Console.MessageType.Warning,
            )

    def _log_alt_positions(wp_idx: int, wp_x: float, wp_y: float, label: str) -> None:
        """Log each alt's distance from the current waypoint for debugging."""
        try:
            accs = GLOBAL_CACHE.ShMem.GetAllAccountData()
            parts = []
            for acc in accs:
                if acc.AccountEmail == TORCH_RUNNER_EMAIL:
                    continue
                try:
                    ax = acc.AgentData.Position.X
                    ay = acc.AgentData.Position.Y
                    dist = ((ax - wp_x) ** 2 + (ay - wp_y) ** 2) ** 0.5
                    name = acc.AccountEmail.split("@")[0][:10]  # short form
                    parts.append(f"{name}={dist:.0f}")
                except Exception:
                    pass
            if parts:
                ConsoleLog(BOT_NAME, f"[Torch Split] wp{wp_idx} {label} dists: {', '.join(parts)}")
        except Exception as exc:
            _log_error("_party_waypoint_manager._log_alt_positions", exc)

    def _repush_stragglers(wp_x: float, wp_y: float) -> int:
        """PixelStack any alts still outside ARRIVAL_RADIUS of (wp_x, wp_y). Returns count pushed."""
        pushed = 0
        try:
            accs = GLOBAL_CACHE.ShMem.GetAllAccountData()
            for acc in accs:
                if acc.AccountEmail == TORCH_RUNNER_EMAIL:
                    continue
                try:
                    ax = acc.AgentData.Position.X
                    ay = acc.AgentData.Position.Y
                except Exception:
                    continue
                dx = ax - wp_x
                dy = ay - wp_y
                if (dx * dx + dy * dy) > (ARRIVAL_RADIUS ** 2):
                    # Shandra-aware: route straggler around her if blocking
                    tx, ty = _shandra_adjusted_target(
                        wp_x, wp_y, ax, ay,
                        context="repush-straggler",
                    )
                    GLOBAL_CACHE.ShMem.SendMessage(
                        my_email, acc.AccountEmail,
                        SharedCommandType.PixelStack, (tx, ty, 0, 0),
                    )
                    pushed += 1
        except Exception as exc:
            _log_error("_party_waypoint_manager._repush_stragglers", exc)
        return pushed

    def _get_slot2_acc():
        """Return the AccountStruct for slot 2 (party leader during split), or None."""
        try:
            for acc in GLOBAL_CACHE.ShMem.GetAllAccountData():
                try:
                    if acc.AgentPartyData.PartyPosition == 1:
                        return acc
                except Exception:
                    continue
        except Exception as exc:
            _log_error("_party_waypoint_manager._get_slot2_acc", exc)
        return None

    # _safe_pos and _safe_is_dead are module-scope (see top of file) so they
    # can be shared with L1/L2 portal-push sites outside this function.

    def _snapshot_followers_vs_slot2(slot2_acc) -> tuple[int, int, list[tuple[str, float]]] | None:
        """Snapshot main-party followers relative to slot 2's position.
        Returns (alive_count, in_earshot_count, stragglers) where stragglers
        is [(email, dist_u), ...] for living followers OUTSIDE earshot.
        Returns None if slot 2's position can't be read."""
        s_pos = _safe_pos(slot2_acc)
        if s_pos is None:
            return None
        sx, sy = s_pos
        alive = 0
        in_earshot = 0
        stragglers: list[tuple[str, float]] = []
        try:
            for acc in GLOBAL_CACHE.ShMem.GetAllAccountData():
                # Exclude the torch runner (torch runner) and slot 2 herself
                if acc.AccountEmail == TORCH_RUNNER_EMAIL:
                    continue
                if acc.AccountEmail == slot2_acc.AccountEmail:
                    continue
                if _safe_is_dead(acc):
                    continue
                alive += 1
                a_pos = _safe_pos(acc)
                if a_pos is None:
                    continue
                ax, ay = a_pos
                dx = ax - sx
                dy = ay - sy
                dist_sq = dx * dx + dy * dy
                if dist_sq <= CONVERGE_EARSHOT_SQ:
                    in_earshot += 1
                else:
                    stragglers.append((acc.AccountEmail, dist_sq ** 0.5))
        except Exception as exc:
            _log_error("_party_waypoint_manager._snapshot_followers_vs_slot2", exc)
        return alive, in_earshot, stragglers

    def _wait_for_squad_convergence(wp_idx: int) -> Generator:
        """Mechanism A: before slot 2 engages an enemy-group waypoint, wait for
        ≥CONVERGE_MIN_ALLIES followers within Earshot of her. Pushes stragglers
        toward slot 2 every CONVERGE_PUSH_INTERVAL_S. Caps at CONVERGE_MAX_WAIT_S.
        Emits debug logs for each dispatch/resolution/timeout."""
        slot2_acc = _get_slot2_acc()
        if slot2_acc is None:
            _log_event(
                "CONVERGE_SKIP_NO_SLOT2",
                f"wp{wp_idx} convergence gate skipped — slot 2 not resolved",
                waypoint=wp_idx,
            )
            return
        snap = _snapshot_followers_vs_slot2(slot2_acc)
        if snap is None:
            # Position unreadable this tick — skip gate, don't block the path.
            _log_event(
                "CONVERGE_SKIP_NO_POS",
                f"wp{wp_idx} convergence gate skipped — slot 2 position unreadable",
                waypoint=wp_idx,
            )
            return
        alive, in_earshot, stragglers = snap
        s_pos = _safe_pos(slot2_acc) or (0.0, 0.0)
        sx, sy = s_pos
        _log_checkpoint(
            "CONVERGE_ENTER", waypoint=wp_idx,
            slot2_pos=[round(sx, 1), round(sy, 1)],
            alive=alive, in_earshot=in_earshot,
            required=CONVERGE_MIN_ALLIES,
            stragglers=[[e.split("@")[0][:10], round(d, 0)] for e, d in stragglers],
        )
        if in_earshot >= CONVERGE_MIN_ALLIES:
            # Already converged; fast-path out.
            _log_event(
                "CONVERGE_PASS_IMMEDIATE",
                f"wp{wp_idx} squad already converged ({in_earshot}/{alive} in earshot)",
                waypoint=wp_idx, in_earshot=in_earshot, alive=alive,
            )
            return

        ConsoleLog(
            BOT_NAME,
            f"[Torch Split] wp{wp_idx} convergence gate: {in_earshot}/{alive} in earshot, "
            f"pushing {len(stragglers)} stragglers toward slot 2 ({round(sx, 0)}, {round(sy, 0)})",
            Py4GW.Console.MessageType.Warning,
        )
        start = time.monotonic()
        deadline = start + CONVERGE_MAX_WAIT_S
        last_push = 0.0
        last_poll = 0.0
        pushes = 0
        final_alive = alive
        final_in_earshot = in_earshot
        resolved = False
        while time.monotonic() < deadline:
            now = time.monotonic()
            if now - last_push >= CONVERGE_PUSH_INTERVAL_S:
                last_push = now
                # Re-resolve slot 2's position each push (she may still be moving).
                s2 = _get_slot2_acc()
                if s2 is not None:
                    cur_pos = _safe_pos(s2)
                    if cur_pos is not None:
                        sx, sy = cur_pos
                    cur_snap = _snapshot_followers_vs_slot2(s2)
                    if cur_snap is not None:
                        _, _, current_stragglers = cur_snap
                        # Per-straggler Shandra-aware dispatch. Each alt's
                        # offset is computed from its own approach vector, so
                        # they splay around her from different angles.
                        for email, _dist in current_stragglers:
                            tx, ty = sx, sy
                            stra_acc = None
                            try:
                                for acc in GLOBAL_CACHE.ShMem.GetAllAccountData():
                                    if acc.AccountEmail == email:
                                        stra_acc = acc
                                        break
                            except Exception:
                                pass
                            if stra_acc is not None:
                                sp = _safe_pos(stra_acc)
                                if sp is not None:
                                    tx, ty = _shandra_adjusted_target(
                                        sx, sy, sp[0], sp[1],
                                        context=f"wp{wp_idx}-converge/{email.split('@')[0][:10]}",
                                    )
                            try:
                                GLOBAL_CACHE.ShMem.SendMessage(
                                    my_email, email,
                                    SharedCommandType.PixelStack, (tx, ty, 0, 0),
                                )
                                pushes += 1
                            except Exception:
                                pass
            if now - last_poll >= CONVERGE_POLL_INTERVAL_S:
                last_poll = now
                s2 = _get_slot2_acc()
                if s2 is not None:
                    cur_snap = _snapshot_followers_vs_slot2(s2)
                    if cur_snap is not None:
                        final_alive, final_in_earshot, _ = cur_snap
                        if final_in_earshot >= CONVERGE_MIN_ALLIES:
                            resolved = True
                            break
            yield

        elapsed = round(time.monotonic() - start, 2)
        if resolved:
            ConsoleLog(
                BOT_NAME,
                f"[Torch Split] wp{wp_idx} convergence gate resolved in {elapsed}s "
                f"({final_in_earshot}/{final_alive} in earshot, {pushes} pushes)",
            )
            _log_event(
                "CONVERGE_RESOLVED",
                f"wp{wp_idx} converged in {elapsed}s",
                waypoint=wp_idx, elapsed_s=elapsed,
                in_earshot=final_in_earshot, alive=final_alive, pushes=pushes,
            )
        else:
            ConsoleLog(
                BOT_NAME,
                f"[Torch Split] wp{wp_idx} convergence gate TIMEOUT after {elapsed}s "
                f"({final_in_earshot}/{final_alive} in earshot, {pushes} pushes) — proceeding anyway",
                Py4GW.Console.MessageType.Warning,
            )
            _log_event(
                "CONVERGE_TIMEOUT",
                f"wp{wp_idx} convergence timed out after {elapsed}s — proceeding",
                waypoint=wp_idx, elapsed_s=elapsed,
                in_earshot=final_in_earshot, alive=final_alive, pushes=pushes,
            )

    def _handle_slot2_death(wp_idx: int) -> Generator[None, None, bool]:
        """Mechanism B: detect slot 2 death and broker a res. Finds the nearest
        living non-the torch runner ally, pushes them toward slot 2's corpse until within
        res-scroll range, then holds so GenericResurrectionUtility can fire.
        Returns True if recovery was attempted and slot 2 came back alive,
        False if unrecoverable (caller should bail)."""
        slot2_acc = _get_slot2_acc()
        if slot2_acc is None:
            _log_event(
                "SLOT2_DEATH_SKIP_NO_SLOT2",
                f"wp{wp_idx} slot 2 not resolved during death check",
                waypoint=wp_idx,
            )
            return True  # can't verify — don't abort
        if not _safe_is_dead(slot2_acc):
            return True  # alive, nothing to do

        # Slot 2 is confirmed dead. Capture corpse position.
        corpse_pos = _safe_pos(slot2_acc)
        if corpse_pos is None:
            _log_event(
                "SLOT2_DEATH_NO_POS",
                f"wp{wp_idx} slot 2 dead but position unreadable",
                waypoint=wp_idx,
            )
            return False
        cx, cy = corpse_pos

        ConsoleLog(
            BOT_NAME,
            f"[Torch Split] wp{wp_idx} SLOT 2 DEAD at ({round(cx, 0)}, {round(cy, 0)}) — starting res recovery",
            Py4GW.Console.MessageType.Warning,
        )
        _log_checkpoint(
            "SLOT2_DEATH_DETECTED", waypoint=wp_idx,
            corpse_pos=[round(cx, 1), round(cy, 1)],
        )

        start = time.monotonic()
        deadline = start + SLOT2_DEATH_RECOVERY_MAX_S
        last_push = 0.0
        last_poll = 0.0
        pushes = 0
        nearest_email = ""
        nearest_dist = float("inf")

        while time.monotonic() < deadline:
            now = time.monotonic()

            # Poll: is slot 2 back up? Is nobody alive?
            if now - last_poll >= SLOT2_DEATH_POLL_INTERVAL_S:
                last_poll = now
                s2 = _get_slot2_acc()
                if s2 is not None and not _safe_is_dead(s2):
                    elapsed = round(now - start, 2)
                    ConsoleLog(
                        BOT_NAME,
                        f"[Torch Split] wp{wp_idx} slot 2 RESSED in {elapsed}s ({pushes} pushes)",
                    )
                    _log_event(
                        "SLOT2_DEATH_RECOVERED",
                        f"wp{wp_idx} slot 2 alive again after {elapsed}s",
                        waypoint=wp_idx, elapsed_s=elapsed, pushes=pushes,
                    )
                    return True

                # Global defeat — GW wiped, no point continuing
                if GLOBAL_CACHE.Party.IsPartyDefeated():
                    _log_event(
                        "SLOT2_DEATH_PARTY_DEFEATED",
                        f"wp{wp_idx} party defeated during res recovery",
                        waypoint=wp_idx,
                    )
                    return False

            # Push: find the closest living non-the torch runner non-slot2 ally, vector
            # them toward the corpse. Repeat on interval so they keep walking
            # even if combat interrupts their movement.
            if now - last_push >= SLOT2_DEATH_PUSH_INTERVAL_S:
                last_push = now
                best_email = ""
                best_dist = float("inf")
                try:
                    for acc in GLOBAL_CACHE.ShMem.GetAllAccountData():
                        if acc.AccountEmail == TORCH_RUNNER_EMAIL:
                            continue
                        if acc.AccountEmail == slot2_acc.AccountEmail:
                            continue
                        if _safe_is_dead(acc):
                            continue
                        a_pos = _safe_pos(acc)
                        if a_pos is None:
                            continue
                        ax, ay = a_pos
                        dsq = (ax - cx) ** 2 + (ay - cy) ** 2
                        if dsq < best_dist:
                            best_dist = dsq
                            best_email = acc.AccountEmail
                except Exception as exc:
                    _log_error("_handle_slot2_death.scan", exc)

                if best_email:
                    nearest_email = best_email
                    nearest_dist = best_dist ** 0.5
                    # Shandra-aware: if she's hovering near slot 2's corpse
                    # (likely, since she was following the party leader),
                    # offset the res-ally's approach so they can reach res range.
                    tx, ty = cx, cy
                    try:
                        bap_x, bap_y = Agent.GetXY(0) or (cx, cy)  # placeholder; use the ally's pos
                    except Exception:
                        bap_x, bap_y = cx, cy
                    try:
                        for acc in GLOBAL_CACHE.ShMem.GetAllAccountData():
                            if acc.AccountEmail == best_email:
                                ap = _safe_pos(acc)
                                if ap is not None:
                                    bap_x, bap_y = ap
                                break
                    except Exception:
                        pass
                    tx, ty = _shandra_adjusted_target(
                        cx, cy, bap_x, bap_y,
                        context=f"wp{wp_idx}-res-corpse",
                    )
                    try:
                        GLOBAL_CACHE.ShMem.SendMessage(
                            my_email, best_email,
                            SharedCommandType.PixelStack, (tx, ty, 0, 0),
                        )
                        pushes += 1
                    except Exception:
                        pass
                    # Debug: log every 2s that we're vectoring
                    if pushes % 4 == 1:
                        _log_event(
                            "SLOT2_DEATH_VECTORING",
                            f"wp{wp_idx} pushing {best_email.split('@')[0][:10]} "
                            f"dist={round(nearest_dist, 0)}u to corpse",
                            waypoint=wp_idx,
                            ally=best_email.split("@")[0][:10],
                            dist_u=round(nearest_dist, 0),
                            in_res_range=nearest_dist <= SLOT2_RES_RANGE_U,
                        )
                else:
                    _log_event(
                        "SLOT2_DEATH_NO_LIVING_ALLY",
                        f"wp{wp_idx} no living ally to vector toward corpse",
                        waypoint=wp_idx,
                    )
                    return False

            yield

        elapsed = round(time.monotonic() - start, 2)
        ConsoleLog(
            BOT_NAME,
            f"[Torch Split] wp{wp_idx} SLOT 2 UNRECOVERABLE after {elapsed}s "
            f"(nearest ally {nearest_email.split('@')[0][:10] if nearest_email else 'none'} "
            f"at {round(nearest_dist, 0)}u, res-range {round(SLOT2_RES_RANGE_U, 0)}u)",
            Py4GW.Console.MessageType.Error,
        )
        _log_event(
            "TORCH_SPLIT_UNRECOVERABLE",
            f"wp{wp_idx} slot 2 dead, no ally reached res range in {elapsed}s",
            waypoint=wp_idx, elapsed_s=elapsed,
            nearest_ally=nearest_email.split("@")[0][:10] if nearest_email else "",
            nearest_dist_u=round(nearest_dist, 0),
            res_range_u=round(SLOT2_RES_RANGE_U, 0),
            pushes=pushes,
        )
        return False

    # Resolve the party leader (slot 2) once. All non-flank waypoint broadcasts
    # go to the leader only; the other 6 alts follow via CustomBehaviors
    # follow-party-leader mode. (Strict-flank waypoints still blast all 7.)
    _leader_email = None
    for acc in party_accounts:
        try:
            if acc.AgentPartyData.PartyPosition == 1:
                _leader_email = acc.AccountEmail
                break
        except Exception:
            continue
    if _leader_email:
        ConsoleLog(BOT_NAME, f"[Torch Split] Leader resolved: {_leader_email}")
    else:
        ConsoleLog(BOT_NAME, "[Torch Split] WARNING: leader (slot 2) email not found; falling back to all-alt broadcast", Py4GW.Console.MessageType.Warning)

    for wp_idx, (x, y) in enumerate(path2_part2, 1):
        # Bail if party wiped
        if GLOBAL_CACHE.Party.IsPartyDefeated():
            ConsoleLog(BOT_NAME, f"[Torch Split] Party defeated at waypoint {wp_idx}/{len(path2_part2)} — aborting")
            _log_event("TORCH_SPLIT_DEFEAT",
                       f"Party defeated at waypoint {wp_idx}/{len(path2_part2)}",
                       at_waypoint=wp_idx, total_waypoints=len(path2_part2))
            return

        # --- Mechanism B: slot 2 death-recovery probe ---
        # Each waypoint iteration, check if the promoted leader (slot 2) is dead.
        # If so, broker a res by vectoring the nearest living ally to her corpse.
        # Caps out at SLOT2_DEATH_RECOVERY_MAX_S; returning False means we couldn't
        # recover and should abort the torch-split path cleanly.
        recovered = yield from _handle_slot2_death(wp_idx)
        if not recovered:
            ConsoleLog(
                BOT_NAME,
                f"[Torch Split] Aborting — slot 2 unrecoverable at waypoint {wp_idx}",
                Py4GW.Console.MessageType.Error,
            )
            _log_event(
                "TORCH_SPLIT_ABORT_SLOT2_DEAD",
                f"Torch split aborted at waypoint {wp_idx}/{len(path2_part2)} — slot 2 dead",
                at_waypoint=wp_idx, total_waypoints=len(path2_part2),
            )
            return

        # Initial broadcast: leader-only (other alts follow via CB). Capture
        # msg_idx so we can poll for slot 2's FollowPath completion. Shandra
        # offset applied so leader routes around her if she's on the target.
        # This is the SINGLE authoritative PixelStack the leader receives
        # per waypoint — including strict-flank waypoints (no duplicate
        # dispatch inside the strict block; see below).
        _leader_msg_idx = -1
        if _leader_email:
            _leader_tx, _leader_ty = x, y
            _leader_acc = next(
                (a for a in party_accounts if a.AccountEmail == _leader_email),
                None,
            )
            if _leader_acc is not None:
                _lap = _safe_pos(_leader_acc)
                if _lap is not None:
                    _leader_tx, _leader_ty = _shandra_adjusted_target(
                        x, y, _lap[0], _lap[1],
                        context=f"wp{wp_idx}-leader-dispatch",
                    )
            _leader_msg_idx = GLOBAL_CACHE.ShMem.SendMessage(
                my_email, _leader_email,
                SharedCommandType.PixelStack, (_leader_tx, _leader_ty, 0, 0),
            )
        else:
            # Fallback: broadcast to all if leader unresolved
            for acc in party_accounts:
                GLOBAL_CACHE.ShMem.SendMessage(
                    my_email, acc.AccountEmail,
                    SharedCommandType.PixelStack, (x, y, 0, 0),
                )
        ConsoleLog(BOT_NAME, f"[Torch Split] Party waypoint {wp_idx}/{len(path2_part2)} -> ({x}, {y}) msg_idx={_leader_msg_idx}")
        _log_alt_positions(wp_idx, x, y, "dispatch")
        _wp_enter_time = time.monotonic()
        _log_checkpoint("WP_ENTER", waypoint=wp_idx, target=[x, y], leader_msg_idx=_leader_msg_idx)

        # --- CALL TARGET for enemy-group waypoints ---
        # At P4, P7, P8 (enemy groups), pick a priority target (monk first, then
        # caster, then nearest) and broadcast it as the party called target so
        # heroes focus-fire instead of attacking whichever enemy is closest to them.
        if wp_idx in CALL_TARGET_WAYPOINTS:
            # --- Mechanism A: squad convergence gate ---
            # Before we commit the party target at an enemy-group waypoint, make
            # sure slot 2 isn't about to engage alone. Pushes stragglers forward
            # and waits up to CONVERGE_MAX_WAIT_S for ≥CONVERGE_MIN_ALLIES to be
            # within Earshot of her. This is the primary defense against the
            # Run 127 failure mode (slot 2 over-extending into burst
            # damage alone and dying out of res-scroll range).
            yield from _wait_for_squad_convergence(wp_idx)
            _call_party_target(wp_idx, x, y)

        # --- STRICT FLANK WAYPOINT HANDLING ---
        # For the first two flank waypoints (P5, P6), aggressively PixelStack all
        # alts every 0.5s for 10 seconds. Also disable CustomBehaviors combat on
        # alts for the duration of the strict flank so they don't engage enemies
        # from the 1st group (P4) or 2nd group (P7, P8) during transit.
        # Re-enable combat after the last strict waypoint completes.
        if wp_idx in STRICT_FLANK_WAYPOINTS:
            # Cache the set of non-leader alts for this waypoint. Leader is
            # excluded from the broadcast loop (fix B from Run 130 post-mortem:
            # leader inbox flooding caused slot 2's 14.5s drift to -5244,4975
            # and subsequent solo death). Leader receives a single PixelStack
            # at waypoint entry instead — enough to move her forward without
            # queuing 10+ stale targets.
            non_leader_accounts = [
                a for a in party_accounts if a.AccountEmail != _leader_email
            ] if _leader_email else list(party_accounts)

            # First strict waypoint: disable combat on all alts (leader too)
            if wp_idx == min(STRICT_FLANK_WAYPOINTS):
                ConsoleLog(BOT_NAME, f"[Torch Split] Disabling CustomBehaviors on alts for strict flank")
                for acc in party_accounts:
                    GLOBAL_CACHE.ShMem.SendMessage(
                        my_email, acc.AccountEmail,
                        SharedCommandType.DisableWidget, (0, 0, 0, 0),
                        ("CustomBehaviors", "", "", ""),
                    )
                _log_event(
                    "TORCH_SPLIT_STRICT_FLANK_COMBAT_OFF",
                    f"Disabled CustomBehaviors on {len(party_accounts)} alts for strict flank",
                    waypoint=wp_idx,
                )
            # Last strict waypoint: re-enable CB on all alts and WAIT FOR
            # ACKNOWLEDGMENT. Fix A from Run 130 post-mortem: previous fire-
            # and-forget dispatch was silently failing because each alt's
            # inbox was saturated with pending PixelStacks from wp5; the
            # EnableWidget message queued behind them and didn't process
            # before wp7 fired. Result: 6 alts still had CB disabled, no
            # FollowPartyLeader active, main party frozen at wp6 endpoint
            # while slot 2 over-extended alone.
            # Mirror the merchant re-enable pattern: capture msg_idx for each
            # send, then poll GetInbox(idx).Active until all 7 are inactive
            # (or ENABLE_WIDGET_ACK_MAX_S elapses).
            _enable_msg_idxs: list[tuple[str, int]] = []
            if wp_idx == max(STRICT_FLANK_WAYPOINTS):
                ConsoleLog(BOT_NAME, f"[Torch Split] Re-enabling CustomBehaviors on alts (at start of wp{wp_idx})")
                for acc in party_accounts:
                    _idx = GLOBAL_CACHE.ShMem.SendMessage(
                        my_email, acc.AccountEmail,
                        SharedCommandType.EnableWidget, (0, 0, 0, 0),
                        ("CustomBehaviors", "", "", ""),
                    )
                    if _idx >= 0:
                        _enable_msg_idxs.append((acc.AccountEmail, _idx))
                _log_event(
                    "TORCH_SPLIT_STRICT_FLANK_COMBAT_ON",
                    f"Dispatched EnableWidget to {len(party_accounts)} alts; awaiting ack",
                    waypoint=wp_idx,
                    dispatched=len(_enable_msg_idxs),
                )

            # NOTE: leader PixelStack for this waypoint was already sent by the
            # initial WP_ENTER dispatch at the top of the loop (with Shandra
            # offset applied). We intentionally do NOT send a second one here
            # — prior versions did, causing duplicate inbox messages on the
            # leader for every strict waypoint (observed 2-per-wp in Run 131's
            # slot 2 log at 00:30:22 and 00:30:27). The single initial
            # dispatch is sufficient to keep the leader moving forward; the
            # broadcast loop below handles only the non-leader stragglers.

            # Per-waypoint hold duration. wp6 needs less time because its
            # main job is just to give CB re-enable messages time to
            # propagate; the physical flank traversal mostly happens in wp5.
            _this_hold_s = 3.0 if wp_idx == max(STRICT_FLANK_WAYPOINTS) else STRICT_HOLD_S
            ConsoleLog(
                BOT_NAME,
                f"[Torch Split] STRICT FLANK wp{wp_idx} — hold {_this_hold_s:.0f}s "
                f"(reduced PixelStack rate, leader-excluded broadcast)",
            )
            _log_checkpoint("TORCH_SPLIT_STRICT_FLANK_START", waypoint=wp_idx, target=[x, y])
            strict_start = time.monotonic()
            last_push = 0.0
            last_pos_log = 0.0
            broadcast_count = 0
            pushed_count = 0              # total per-alt pushes across this waypoint
            skipped_arrived = 0            # alts skipped due to STRAGGLER_RADIUS
            enable_acked = not _enable_msg_idxs   # True if no acks needed (wp5)
            enable_ack_time: float | None = None
            # Log positions every 2.5s during the hold
            STRICT_POS_LOG_INTERVAL_S = 2.5
            while time.monotonic() - strict_start < _this_hold_s:
                now = time.monotonic()

                # Poll EnableWidget acks. Once all go inactive, mark done so
                # we stop polling. If the cap elapses first, log and proceed.
                if not enable_acked:
                    all_done = True
                    for _email, _idx in _enable_msg_idxs:
                        try:
                            _msg = GLOBAL_CACHE.ShMem.GetInbox(_idx)
                            if _msg.Active:
                                all_done = False
                                break
                        except Exception:
                            # Treat unreadable inbox as "not yet done"
                            all_done = False
                            break
                    if all_done:
                        enable_acked = True
                        enable_ack_time = now - strict_start
                        _log_event(
                            "TORCH_SPLIT_ENABLE_ACK_ALL",
                            f"wp{wp_idx} all EnableWidget acks received in {enable_ack_time:.2f}s",
                            waypoint=wp_idx,
                            elapsed_s=round(enable_ack_time, 2),
                            alts=len(_enable_msg_idxs),
                        )

                # Broadcast to non-leader stragglers only
                if now - last_push >= STRICT_PUSH_INTERVAL_S:
                    for acc in non_leader_accounts:
                        ap = _safe_pos(acc)
                        if ap is not None:
                            dxp = ap[0] - x
                            dyp = ap[1] - y
                            if (dxp * dxp + dyp * dyp) <= (STRICT_STRAGGLER_RADIUS ** 2):
                                skipped_arrived += 1
                                continue
                            tx, ty = _shandra_adjusted_target(
                                x, y, ap[0], ap[1],
                                context=f"wp{wp_idx}-strict",
                            )
                        else:
                            tx, ty = x, y
                        GLOBAL_CACHE.ShMem.SendMessage(
                            my_email, acc.AccountEmail,
                            SharedCommandType.PixelStack, (tx, ty, 0, 0),
                        )
                        pushed_count += 1
                    last_push = now
                    broadcast_count += 1
                if now - last_pos_log >= STRICT_POS_LOG_INTERVAL_S:
                    elapsed_s = round(now - strict_start, 1)
                    _log_alt_positions(wp_idx, x, y, f"strict-t+{elapsed_s}s")
                    last_pos_log = now
                yield

            # Cap-hit safety log for EnableWidget.
            if _enable_msg_idxs and not enable_acked:
                _unacked = []
                for _email, _idx in _enable_msg_idxs:
                    try:
                        if GLOBAL_CACHE.ShMem.GetInbox(_idx).Active:
                            _unacked.append(_email.split('@')[0][:10])
                    except Exception:
                        _unacked.append(_email.split('@')[0][:10])
                _log_event(
                    "TORCH_SPLIT_ENABLE_ACK_TIMEOUT",
                    f"wp{wp_idx} {len(_unacked)}/{len(_enable_msg_idxs)} EnableWidget acks outstanding at cap",
                    waypoint=wp_idx,
                    cap_s=_this_hold_s,
                    unacked=_unacked,
                )
                ConsoleLog(
                    BOT_NAME,
                    f"[Torch Split] WARNING: {len(_unacked)} alts did not ack CB re-enable "
                    f"within {_this_hold_s}s — proceeding anyway (unacked: {', '.join(_unacked)})",
                    Py4GW.Console.MessageType.Warning,
                )

            _log_alt_positions(wp_idx, x, y, "strict-end")
            _log_checkpoint(
                "TORCH_SPLIT_STRICT_FLANK_END",
                waypoint=wp_idx,
                broadcasts=broadcast_count,
                pushed=pushed_count,
                skipped_arrived=skipped_arrived,
                duration=round(time.monotonic() - strict_start, 1),
                enable_ack_s=round(enable_ack_time, 2) if enable_ack_time is not None else None,
            )

            continue  # skip normal Phase 1/2/3 for strict flank waypoints

        # --- Wait for slot 2's FollowPath to finish, then OOC wait ---
        # Slot 2's PixelStack handler runs Movement.FollowPath(timeout=10s) +
        # recovery sequence. We poll the shared-memory Inbox slot: when the
        # message is marked as not-Active, slot 2 has finished (succeeded or
        # failed+recovered). LEADER_WAIT_MAX_S is a hard safety cap.
        LEADER_WAIT_MAX_S      = 20.0  # handler is ~10s timeout + ~5s recovery
        OOC_POLL_INTERVAL_S    = 0.25  # halved from 0.5; see COMBAT_CONFIRM_POLLS below
                                       # for the paired change that preserves the
                                       # 2.5s sustained-stance confirmation threshold
        my_email_pc = Player.GetAccountEmail()

        # Helper: scan all alts and return a list of
        # (email, name, pos) tuples for those currently showing combat stance.
        # Excludes the torch runner and dead alts.
        def _scan_combat_all():
            in_combat: list[tuple[str, str, tuple[float, float] | None]] = []
            for acc in GLOBAL_CACHE.ShMem.GetAllAccountData():
                try:
                    if not acc.AccountEmail or acc.AccountEmail == my_email_pc:
                        continue
                    ad = acc.AgentData
                    if getattr(ad, "Is_Dead", False):
                        continue
                    # Skip alts whose stance flag we don't trust
                    try:
                        _name_check = ad.CharacterName or ""
                    except Exception:
                        _name_check = ""
                    if _name_check in COMBAT_DETECTION_EXCLUDE_NAMES:
                        continue
                    if getattr(ad, "Is_InCombatStance", False):
                        name = _name_check or acc.AccountEmail
                        try:
                            pos = (round(ad.Position.X, 1), round(ad.Position.Y, 1))
                        except Exception:
                            pos = None
                        in_combat.append((acc.AccountEmail, name, pos))
                except Exception:
                    continue
            return in_combat

        # Per-poll update: increments streak for alts currently in stance,
        # clears streak for alts who've left stance. Returns (any_confirmed,
        # first_confirmed_name, first_confirmed_pos) where "confirmed" means
        # streak >= COMBAT_CONFIRM_POLLS. `any_uncleared` = any alt has a
        # nonzero streak (used by OOC loop to decide "nobody is fighting").
        def _scan_combat():
            snapshot = _scan_combat_all()
            seen_emails = {e for e, _, _ in snapshot}
            # Clear streaks for alts who are no longer in stance
            for email in list(combat_streak.keys()):
                if email not in seen_emails:
                    combat_streak.pop(email, None)
            # Update streaks + pick first confirmed
            first_name = ""
            first_pos: tuple[float, float] | None = None
            any_confirmed = False
            for email, name, pos in snapshot:
                combat_streak[email] = combat_streak.get(email, 0) + 1
                if combat_streak[email] >= COMBAT_CONFIRM_POLLS and not any_confirmed:
                    any_confirmed = True
                    first_name = name
                    first_pos = pos
            any_uncleared = len(combat_streak) > 0
            return any_confirmed, first_name, first_pos, any_uncleared

        # Track first combat detection across the whole waypoint.
        # An alt must show Is_InCombatStance for COMBAT_CONFIRM_POLLS
        # consecutive polls before we count it as real combat. Item-spell
        # recasts (Protective Was Kaolai, Splinter Weapon, etc.) flip stance
        # for only ~1-2s; actual combat sustains for many seconds. The streak
        # is tracked per-alt-email so different alts recasting at different
        # times don't each contribute a single tick that sums to a false pos.
        COMBAT_CONFIRM_POLLS = 10  # 10 polls × 0.25s = 2.5s of sustained stance
                                   # (doubled from 5 when OOC_POLL_INTERVAL_S was
                                   # halved to 0.25s; preserves the 2.5s threshold
                                   # that avoids false-positives on item-spell
                                   # recasts like Protective Was Kaolai)
        # Characters whose Is_InCombatStance flag is unreliable due to
        # maintained item spells (Protective Was Kaolai, etc.). These alts
        # hold ashes or similar items that keep stance flagged True
        # indefinitely, which would either fire false positives or deadlock
        # the OOC wait loop. The remaining 5 alts are enough to witness
        # actual combat, so excluding these is safe.
        # OPERATOR-CONFIG: set `combat_detection_excludes` under `[Character Names]`
        # in your local INI as a comma-separated list of character names to exclude
        # from in-combat-stance detection (typically support classes whose maintained
        # item spells keep them flagged in stance permanently).
        _excl_raw = _settings_ini.read_key(_CHAR_NAMES_SECTION, "combat_detection_excludes", "")
        COMBAT_DETECTION_EXCLUDE_NAMES = {n.strip() for n in _excl_raw.split(",") if n.strip()}
        combat_seen = False
        combat_start_alt = ""
        combat_start_pos: tuple[float, float] | None = None
        combat_start_elapsed = 0.0
        combat_streak: dict[str, int] = {}   # alt_email -> consecutive in-combat polls

        # --- Wait for leader to finish FollowPath (or safety timeout) ---
        _leader_wait_start = time.monotonic()
        _leader_wait_deadline = _leader_wait_start + LEADER_WAIT_MAX_S
        last_poll = 0.0
        leader_done = False
        leader_wait_reason = "no_msg_idx"
        while time.monotonic() < _leader_wait_deadline:
            now = time.monotonic()
            # Check completion via shared-memory Inbox slot
            if _leader_msg_idx >= 0:
                try:
                    msg = GLOBAL_CACHE.ShMem.GetInbox(_leader_msg_idx)
                    if not msg.Active:
                        leader_done = True
                        leader_wait_reason = "msg_finished"
                        break
                except Exception:
                    pass
            # Combat detection during wait (confirmed = stance held N polls)
            if now - last_poll >= OOC_POLL_INTERVAL_S:
                last_poll = now
                any_confirmed, name, pos, _any_uncleared = _scan_combat()
                if any_confirmed and not combat_seen:
                    combat_seen = True
                    combat_start_alt = name
                    combat_start_pos = pos
                    combat_start_elapsed = round(now - _wp_enter_time, 2)
                    _log_checkpoint(
                        "WP_COMBAT_START", waypoint=wp_idx,
                        alt=combat_start_alt,
                        combat_pos=list(combat_start_pos) if combat_start_pos else None,
                        elapsed_s=combat_start_elapsed,
                        during="leader_wait",
                    )
            yield
        if not leader_done and _leader_msg_idx >= 0:
            leader_wait_reason = "safety_timeout"
        _log_checkpoint(
            "WP_LEADER_DONE", waypoint=wp_idx,
            leader_wait_s=round(time.monotonic() - _leader_wait_start, 2),
            reason=leader_wait_reason,
            combat_seen=combat_seen,
            total_elapsed_s=round(time.monotonic() - _wp_enter_time, 2),
        )

        # --- OOC wait (also detects late combat start) ---
        # Skip entirely for SAFE_WAYPOINTS — these are guaranteed enemy-free,
        # so polling combat stance is wasted work that adds ~1-2s of latency.
        if wp_idx in SAFE_WAYPOINTS:
            _log_alt_positions(wp_idx, x, y, "post-waypoint")
            _log_checkpoint(
                "WP_OOC_SKIPPED_SAFE", waypoint=wp_idx,
                total_elapsed_s=round(time.monotonic() - _wp_enter_time, 2),
            )
        else:
            # OOC verification tightened for P7-P15 (post-flank convergence toward
            # the dungeon door). 1 confirmed OoC poll = 500ms minimum, vs the 2-poll
            # (1s) default used on earlier waypoints. Saves ~500ms per waypoint
            # across 9 post-flank waypoints (~4.5s per run). Safe because the
            # combat-detection side (COMBAT_CONFIRM_POLLS=5, 2.5s sustained stance)
            # is unchanged — any genuinely-fighting alt still resets ooc_streak via
            # any_uncleared before the single-poll window completes.
            OOC_REQUIRED_CONSECUTIVE = 1 if wp_idx >= 7 else 2
            _ooc_start = time.monotonic()
            last_poll = 0.0
            ooc_streak = 0
            while True:
                now = time.monotonic()
                if now - last_poll >= OOC_POLL_INTERVAL_S:
                    last_poll = now
                    any_confirmed, name, pos, any_uncleared = _scan_combat()
                    if any_confirmed and not combat_seen:
                        combat_seen = True
                        combat_start_alt = name
                        combat_start_pos = pos
                        combat_start_elapsed = round(now - _wp_enter_time, 2)
                        _log_checkpoint(
                            "WP_COMBAT_START", waypoint=wp_idx,
                            alt=combat_start_alt,
                            combat_pos=list(combat_start_pos) if combat_start_pos else None,
                            elapsed_s=combat_start_elapsed,
                            during="ooc_wait",
                        )
                    # OOC = nobody currently has an in-progress stance streak
                    if any_uncleared:
                        ooc_streak = 0
                    else:
                        ooc_streak += 1
                        if ooc_streak >= OOC_REQUIRED_CONSECUTIVE:
                            break
                yield

            _log_alt_positions(wp_idx, x, y, "post-waypoint")
            _log_checkpoint(
                "WP_OOC_CLEAR", waypoint=wp_idx,
                combat_seen=combat_seen,
                combat_alt=combat_start_alt,
                combat_pos=list(combat_start_pos) if combat_start_pos else None,
                combat_start_elapsed_s=combat_start_elapsed,
                ooc_wait_s=round(time.monotonic() - _ooc_start, 2),
                total_elapsed_s=round(time.monotonic() - _wp_enter_time, 2),
            )

        # Clear the party called target after the last call-target waypoint so
        # subsequent waypoints revert to natural cluster/nearest targeting.
        if wp_idx == CLEAR_PARTY_TARGET_AFTER_WAYPOINT:
            try:
                CustomBehaviorParty().set_party_custom_target(None)
                ConsoleLog(BOT_NAME, f"[Torch Split] wp{wp_idx} cleared party called target")
                _log_event("TORCH_SPLIT_CLEAR_CALLED_TARGET", f"Cleared after wp{wp_idx}", waypoint=wp_idx)
            except Exception as exc:
                _log_error("clear_party_target", exc)

    ConsoleLog(BOT_NAME, "[Torch Split] Party finished path2_part2")


def _torch_runner_split() -> Generator:
    """
    Splits the torch runner off from the party to do the torch/brazier sequence independently.
    - Disables CustomBehaviors on the torch runner so she stops following the party.
    - Promotes slot 2 as party leader so the rest of the party follows them.
    - Starts a background coroutine to feed path2_part2 waypoints to the party.
    - Returns immediately so the torch runner can begin her torch run in parallel.
    """
    my_email = Player.GetAccountEmail()
    slot2_email = _get_slot2_email()

    # Disable CustomBehaviors on the torch runner so she stops following
    GLOBAL_CACHE.ShMem.SendMessage(
        my_email, TORCH_RUNNER_EMAIL,
        SharedCommandType.DisableWidget, (0, 0, 0, 0), ("CustomBehaviors", "", "", ""),
    )
    yield from Routines.Yield.wait(250)

    # Promote slot 2 as party leader so the rest of the party follows them
    if slot2_email:
        CustomBehaviorParty().set_party_leader_email(slot2_email)
        ConsoleLog(BOT_NAME, f"[Torch Split] Promoted slot 2 ({slot2_email}) as party leader")
    else:
        ConsoleLog(BOT_NAME, "[Torch Split] Could not find slot 2 email", Py4GW.Console.MessageType.Warning)

    # Dispatch "use Tengu Support Flare" to the designated summoner. Only one
    # instance of summoned NPCs can exist per dungeon, so only one hero fires
    # the stone. The summoned allies persist until they die, which should
    # cover most of the torch-split combat window.
    # OPERATOR-CONFIG: set `summoner` under `[Character Names]` in your local INI.
    _TENGU_SUPPORT_FLARE_MODEL_ID = 30209
    _SUMMONER_CHARACTER_NAME      = _settings_ini.read_key(_CHAR_NAMES_SECTION, "summoner", "")
    summoner_email = CHARACTER_EMAIL_MAP.get(_SUMMONER_CHARACTER_NAME, "")
    if summoner_email:
        try:
            GLOBAL_CACHE.ShMem.SendMessage(
                my_email, summoner_email,
                SharedCommandType.CustomBehaviors,
                (3.0, float(_TENGU_SUPPORT_FLARE_MODEL_ID), 0.0, 0.0),
                ("", "", "", ""),
            )
            ConsoleLog(
                BOT_NAME,
                f"[Torch Split] Dispatched Tengu Support Flare summon to {_SUMMONER_CHARACTER_NAME}",
            )
            _log_event(
                "TORCH_SPLIT_SUMMON_DISPATCH",
                f"Dispatched summon to {_SUMMONER_CHARACTER_NAME}",
                summoner=_SUMMONER_CHARACTER_NAME,
                model_id=_TENGU_SUPPORT_FLARE_MODEL_ID,
            )
        except Exception as exc:
            _log_error("torch_split.summon_dispatch", exc)
    else:
        ConsoleLog(
            BOT_NAME,
            f"[Torch Split] {_SUMMONER_CHARACTER_NAME} not in CHARACTER_EMAIL_MAP — skipping summon dispatch",
            Py4GW.Console.MessageType.Warning,
        )
        _log_event(
            "TORCH_SPLIT_SUMMON_SKIPPED",
            f"Summoner {_SUMMONER_CHARACTER_NAME} not configured",
            summoner=_SUMMONER_CHARACTER_NAME,
        )

    # Launch waypoint manager as a background coroutine — the torch runner starts torch run immediately
    bot.config.FSM.RemoveManagedCoroutine("TorchPartyWaypointManager")
    bot.config.FSM.AddManagedCoroutine("TorchPartyWaypointManager", _party_waypoint_manager())
    ConsoleLog(BOT_NAME, "[Torch Split] Party waypoint manager started — the torch runner beginning torch run")
    _log_checkpoint("TORCH_SPLIT_START")
    yield


def _torch_runner_rejoin() -> Generator:
    """Re-enable CustomBehaviors on the torch runner and restore her as party leader."""
    my_email = Player.GetAccountEmail()

    # Stop the waypoint manager if it is still running
    bot.config.FSM.RemoveManagedCoroutine("TorchPartyWaypointManager")
    ConsoleLog(BOT_NAME, "[Torch Split] Waypoint manager stopped")

    # Stop the torch drop scanner
    bot.config.FSM.RemoveManagedCoroutine("TorchDropScanner")
    ConsoleLog(BOT_NAME, "[Torch Split] Torch drop scanner stopped")

    # Re-enable CustomBehaviors on the torch runner
    GLOBAL_CACHE.ShMem.SendMessage(
        my_email, TORCH_RUNNER_EMAIL,
        SharedCommandType.EnableWidget, (0, 0, 0, 0), ("CustomBehaviors", "", "", ""),
    )
    yield from Routines.Yield.wait(250)

    # Restore the torch runner as party leader
    CustomBehaviorParty().set_party_leader_email(TORCH_RUNNER_EMAIL)
    ConsoleLog(BOT_NAME, "[Torch Split] the torch runner restored as party leader")
    _log_checkpoint("TORCH_SPLIT_END")
    yield


def _torch_drop_scanner() -> Generator:
    """
    Background managed coroutine: polls every 500ms to check the torch runner is still
    holding the torch bundle. Detection only — does NOT attempt re-pickup,
    because that would issue competing Player.Move() commands against the
    brazier sequence. The brazier sequence's own buff-expiry / _go_relight
    path handles recovery; this scanner provides diagnostic logging.
    """
    ConsoleLog(BOT_NAME, "[TORCH] Drop scanner active")
    while True:
        # Stop scanning if dead or party wiped
        if Agent.IsDead(Player.GetAgentID()) or GLOBAL_CACHE.Party.IsPartyDefeated():
            ConsoleLog(BOT_NAME, "[TORCH] Drop scanner stopping — player dead or party defeated")
            return
        holding = Agent.IsHoldingItem(Player.GetAgentID())
        if not holding:
            px, py = Player.GetXY()
            ConsoleLog(BOT_NAME, f"[TORCH] Torch dropped! the torch runner pos: ({px:.0f}, {py:.0f})", Py4GW.Console.MessageType.Warning)
        yield from Routines.Yield.wait(500)


def _start_torch_drop_scanner() -> Generator:
    """Start the torch drop scanner as a background managed coroutine."""
    bot.config.FSM.RemoveManagedCoroutine("TorchDropScanner")
    bot.config.FSM.AddManagedCoroutine("TorchDropScanner", _torch_drop_scanner())
    ConsoleLog(BOT_NAME, "[TORCH] Drop scanner started")
    yield


# --- (EBSoW stack broadcaster removed — alts no longer forced into AoE) ---

def _ebsow_stack_monitor_REMOVED_UNUSED():
    return
    my_email = Player.GetAccountEmail()
    # Only active when script is running on the torch runner's account
    if my_email != TORCH_RUNNER_EMAIL:
        return

    slot = GLOBAL_CACHE.SkillBar.GetSlotBySkillID(EBSOW_SKILL_ID)
    if slot <= 0:
        ConsoleLog(BOT_NAME, f"[EBSoW Stack] Skill {EBSOW_SKILL_ID} not on skillbar — monitor exiting")
        return

    ConsoleLog(BOT_NAME, f"[EBSoW Stack] Monitor active on slot {slot}")
    was_ready = True  # Assume ready at start

    while True:
        try:
            # Only monitor in explorable
            if not Routines.Checks.Map.MapValid():
                yield from Routines.Yield.wait(500)
                continue

            # Re-check slot in case skillbar changed
            cur_slot = GLOBAL_CACHE.SkillBar.GetSlotBySkillID(EBSOW_SKILL_ID)
            if cur_slot <= 0:
                yield from Routines.Yield.wait(1000)
                continue

            skill_data = GLOBAL_CACHE.SkillBar.GetSkillData(cur_slot)
            is_ready = (skill_data.recharge == 0)

            # Detect transition: ready -> recharging = cast event
            if was_ready and not is_ready:
                stack_x, stack_y = Player.GetXY()
                _log_checkpoint("EBSOW_CAST", pos=[round(stack_x, 1), round(stack_y, 1)])
                ConsoleLog(BOT_NAME, f"[EBSoW Stack] Cast detected at ({stack_x:.0f}, {stack_y:.0f}) — broadcasting stack")

                # Broadcast PixelStack to nearby alts for up to EBSOW_STACK_DURATION_S.
                # Exit early once all in-range alts are within the arrived radius.
                stack_end = time.monotonic() + EBSOW_STACK_DURATION_S
                last_broadcast = 0.0
                alts_targeted = set()       # all alts we sent to at least once
                broadcast_count = 0
                exit_reason = "duration"
                arrived_sq = EBSOW_STACK_ARRIVED_RADIUS ** 2

                max_sq = EBSOW_STACK_MAX_ALT_DISTANCE ** 2
                while time.monotonic() < stack_end:
                    now = time.monotonic()
                    if now - last_broadcast >= EBSOW_STACK_BROADCAST_INTERVAL_S:
                        # The banner was dropped at the cast location — that's
                        # where we want the party to stack, NOT at the torch runner's
                        # current position (she moves during the broadcast window).
                        needs_push = 0  # alts still outside arrived radius but in range
                        within_arrived = 0
                        out_of_range = 0
                        # Per-alt distance snapshot for the FIRST broadcast pass only
                        first_pass = (broadcast_count == 0)
                        alt_dists: list[str] = []
                        for acc in GLOBAL_CACHE.ShMem.GetAllAccountData():
                            if acc.AccountEmail == my_email:
                                continue
                            try:
                                ax = acc.AgentData.Position.X
                                ay = acc.AgentData.Position.Y
                            except Exception:
                                continue
                            dx = ax - stack_x
                            dy = ay - stack_y
                            dist_sq = dx * dx + dy * dy
                            if first_pass:
                                try:
                                    _char_name = acc.AgentData.CharacterName or acc.AccountEmail
                                except Exception:
                                    _char_name = acc.AccountEmail
                                alt_dists.append(f"{_char_name}={dist_sq ** 0.5:.0f}")
                            # Skip alts too far away (torch split, etc.)
                            if dist_sq > max_sq:
                                out_of_range += 1
                                continue
                            # Skip alts already stacked
                            if dist_sq <= arrived_sq:
                                within_arrived += 1
                                continue
                            # This alt needs a push to the banner location
                            GLOBAL_CACHE.ShMem.SendMessage(
                                my_email, acc.AccountEmail,
                                SharedCommandType.PixelStack, (stack_x, stack_y, 0, 0),
                            )
                            alts_targeted.add(acc.AccountEmail)
                            needs_push += 1

                        if first_pass:
                            ConsoleLog(
                                BOT_NAME,
                                f"[EBSoW Stack] Distances from banner "
                                f"(arrived<={EBSOW_STACK_ARRIVED_RADIUS:.0f}, max<={EBSOW_STACK_MAX_ALT_DISTANCE:.0f}): "
                                f"{', '.join(alt_dists)} | "
                                f"within_arrived={within_arrived} needs_push={needs_push} out_of_range={out_of_range}",
                            )

                        broadcast_count += 1
                        last_broadcast = now

                        # Early exit: no alts need pushing.
                        # Now distinguish "all already stacked" from "all out of
                        # range" — they look identical in the old log but mean
                        # very different things in practice.
                        if needs_push == 0:
                            if out_of_range > 0 and within_arrived == 0:
                                exit_reason = "all_out_of_range"
                            elif within_arrived > 0 and out_of_range == 0:
                                exit_reason = "all_already_stacked"
                            else:
                                exit_reason = "mixed_no_push_needed"
                            break
                    yield

                _log_event("EBSOW_STACK_END",
                           f"Stack complete ({len(alts_targeted)} alts pushed, {broadcast_count} broadcasts, exit={exit_reason})",
                           alts=len(alts_targeted),
                           broadcasts=broadcast_count,
                           exit_reason=exit_reason)

            was_ready = is_ready
        except Exception as exc:
            _log_error("_ebsow_stack_monitor", exc)
            yield from Routines.Yield.wait(1000)
            continue

        yield from Routines.Yield.wait(EBSOW_POLL_INTERVAL_MS)


# ===== Party Death Monitor =====
# Tracks party-member death transitions so we can quantify wipes in run logs
# without having to cross-reference 8 separate console windows.
_PARTY_DEATH_POLL_MS = 2000                # 0.5 Hz poll
_PARTY_WIPE_CLUSTER_WINDOW_S = 10.0        # deaths within this window count as a cluster
_PARTY_WIPE_CLUSTER_THRESHOLD = 3          # ≥N deaths in window triggers wipe cluster log


def _party_death_monitor() -> Generator:
    """Background coroutine: polls party-member death state at low frequency
    and emits [PARTY_DEATH] / [PARTY_REVIVE] events when transitions occur.
    Also emits [PARTY_WIPE_CLUSTER] when ≥3 deaths occur within 10 seconds.

    Only runs on the torch runner (the leader) so we have one authoritative log source.
    Uses SHARED MEMORY (GetAllAccountData) rather than the local agent table
    so deaths are still detected when the torch runner is far from the party (torch split).
    Each alt client self-publishes its own Is_Dead + Health data.
    """
    my_email = Player.GetAccountEmail()
    if my_email != TORCH_RUNNER_EMAIL:
        return

    ConsoleLog(BOT_NAME, "[Party Death] Monitor started (shared-memory mode)")
    # {account_email: was_dead_bool}
    prev_dead: dict[str, bool] = {}
    # Cached character name per account for friendlier logs
    char_name: dict[str, str] = {}
    # Rolling record of recent death timestamps for wipe-cluster detection
    recent_deaths: list[float] = []
    wipe_cluster_flagged_until: float = 0.0  # debounce

    while True:
        try:
            if not Routines.Checks.Map.MapValid():
                yield from Routines.Yield.wait(_PARTY_DEATH_POLL_MS)
                continue

            try:
                accounts = GLOBAL_CACHE.ShMem.GetAllAccountData() or []
            except Exception:
                accounts = []
            if not accounts:
                yield from Routines.Yield.wait(_PARTY_DEATH_POLL_MS)
                continue

            now = time.time()
            # Prune old death timestamps outside the cluster window
            cutoff = now - _PARTY_WIPE_CLUSTER_WINDOW_S
            recent_deaths[:] = [t for t in recent_deaths if t >= cutoff]

            dead_count = 0
            total_members = 0
            for acc in accounts:
                try:
                    email = acc.AccountEmail
                    if not email:
                        continue
                    agent_data = acc.AgentData
                    # Skip entries that haven't populated yet (agent_id=0 / empty)
                    aid = getattr(agent_data, "AgentID", 0)
                    if not aid:
                        continue
                    # Cache char name for log readability
                    name = getattr(agent_data, "CharacterName", "") or email
                    char_name[email] = name
                    # Shared memory publishes both Is_Dead and Health.Current.
                    # Treat as dead when either signal says so.
                    is_dead = bool(getattr(agent_data, "Is_Dead", False))
                    try:
                        hp_cur = float(agent_data.Health.Current)
                    except Exception:
                        hp_cur = 1.0
                    if hp_cur <= 0.0:
                        is_dead = True
                except Exception:
                    continue

                total_members += 1
                if is_dead:
                    dead_count += 1
                was_dead = prev_dead.get(email, False)
                if is_dead and not was_dead:
                    # alive → dead transition
                    try:
                        ax = agent_data.Pos.x
                        ay = agent_data.Pos.y
                    except Exception:
                        try:
                            ax = agent_data.Position.X
                            ay = agent_data.Position.Y
                        except Exception:
                            ax, ay = (0.0, 0.0)
                    alive_remaining = max(0, total_members - dead_count)
                    _log_event(
                        "PARTY_DEATH",
                        f"{name} died at ({ax:.0f},{ay:.0f})",
                        member=name,
                        agent=aid,
                        pos=[round(ax, 1), round(ay, 1)],
                        alive_remaining=alive_remaining,
                    )
                    recent_deaths.append(now)
                elif (not is_dead) and was_dead:
                    # dead → alive transition (rez/scroll)
                    _log_event(
                        "PARTY_REVIVE",
                        f"{name} revived",
                        member=name,
                        agent=aid,
                    )
                prev_dead[email] = is_dead

            # Wipe-cluster detection: if N+ deaths occurred in the last window
            # and we haven't already flagged this cluster, emit a checkpoint
            # with the last death's coordinates so the hot-spot is obvious.
            if len(recent_deaths) >= _PARTY_WIPE_CLUSTER_THRESHOLD and now >= wipe_cluster_flagged_until:
                try:
                    vx, vy = Player.GetXY()
                except Exception:
                    vx, vy = (0.0, 0.0)
                _log_checkpoint(
                    "PARTY_WIPE_CLUSTER",
                    deaths_in_window=len(recent_deaths),
                    window_s=_PARTY_WIPE_CLUSTER_WINDOW_S,
                    leader_pos=[round(vx, 1), round(vy, 1)],
                    dead_count=dead_count,
                )
                # Suppress re-firing this cluster until the window fully clears
                wipe_cluster_flagged_until = now + _PARTY_WIPE_CLUSTER_WINDOW_S

        except Exception as exc:
            _log_error("_party_death_monitor", exc)
            yield from Routines.Yield.wait(_PARTY_DEATH_POLL_MS)
            continue

        yield from Routines.Yield.wait(_PARTY_DEATH_POLL_MS)


_DISCONNECT_POLL_MS = 2000
# Normal map transitions (outpost→L1, L1→L2, L2→L3) flip IsMapReady False for
# up to ~15s. 30s of continuous "not ready" is well past any normal loading
# screen and almost certainly means the client is sitting on a "network error"
# reconnect dialog (or equivalent disconnect state).
_DISCONNECT_NOT_READY_THRESHOLD_S = 30.0


def _disconnect_watchdog() -> Generator:
    """Background coroutine: detects GW client disconnects and hard-stops the
    bot when one is confirmed.

    Why this exists: run 136 hung for ~5 hours on L3 because the torch runner's client hit
    an in-game "A network error has caused you to disconnect" dialog. The bot
    script kept running but produced zero progress — `Player.GetXY()` fell back
    to (0,0), the Fendi Phase Monitor false-fired "Exiting L3", and the FSM
    eventually wedged on chest/Shandra logic that could not resolve.

    Detection heuristic: `Map.IsMapReady()` is the framework's own "the client
    is in a playable instance right now" gate. It flips False during legit
    transitions too, so we require it to stay False continuously for 30s before
    deciding we're disconnected. Each client self-detects (runs on every
    account); when triggered it calls `bot.Stop()` on its own process.
    """
    not_ready_since: Optional[float] = None
    while True:
        try:
            if Map.IsMapReady():
                not_ready_since = None
            else:
                now = time.time()
                if not_ready_since is None:
                    not_ready_since = now
                elif now - not_ready_since >= _DISCONNECT_NOT_READY_THRESHOLD_S:
                    duration = now - not_ready_since
                    ConsoleLog(
                        BOT_NAME,
                        f"[HARD STOP] Client not map-ready for {duration:.0f}s — "
                        f"assuming disconnect. Stopping bot.",
                        Py4GW.Console.MessageType.Error,
                    )
                    _log_event(
                        "DISCONNECT_DETECTED",
                        f"Map.IsMapReady() False for {duration:.0f}s — bot.Stop() called",
                        not_ready_seconds=round(duration, 1),
                    )
                    try:
                        bot.Stop()
                    except Exception as exc:
                        _log_error("_disconnect_watchdog.bot_stop", exc)
                    return
            yield from Routines.Yield.wait(_DISCONNECT_POLL_MS)
        except Exception as exc:
            _log_error("_disconnect_watchdog", exc)
            yield from Routines.Yield.wait(_DISCONNECT_POLL_MS)


def _start_disconnect_watchdog() -> Generator:
    """Start the disconnect watchdog as a background managed coroutine."""
    bot.config.FSM.RemoveManagedCoroutine("DisconnectWatchdog")
    bot.config.FSM.AddManagedCoroutine("DisconnectWatchdog", _disconnect_watchdog())
    ConsoleLog(BOT_NAME, "[Disconnect] Watchdog scheduled")
    yield


def _start_party_death_monitor() -> Generator:
    """Start the party death monitor as a background managed coroutine."""
    bot.config.FSM.RemoveManagedCoroutine("PartyDeathMonitor")
    bot.config.FSM.AddManagedCoroutine("PartyDeathMonitor", _party_death_monitor())
    ConsoleLog(BOT_NAME, "[Party Death] Monitor scheduled")
    yield


def drop_bundle_safe(times: int = 2, delay_ms: int = 250, max_verify_retries: int = 5) -> Generator:
    """Press DropBundle `times` times, then verify the bundle actually dropped.
    If still holding, retry up to `max_verify_retries` more times with longer delays
    between presses. Logs a warning if we give up while still holding."""
    player_id = Player.GetAgentID()

    # Initial best-effort drops (original behavior)
    for _ in range(times):
        yield from Routines.Yield.Keybinds.DropBundle()
        yield from Routines.Yield.wait(delay_ms)

    # Verify + retry if still holding
    for attempt in range(max_verify_retries):
        if not Agent.IsHoldingItem(player_id):
            if attempt > 0:
                ConsoleLog(BOT_NAME, f"[DropBundle] Verified drop after {attempt} retry attempt(s)")
            yield
            return

        ConsoleLog(
            BOT_NAME,
            f"[DropBundle] Still holding item after initial drops — retry {attempt + 1}/{max_verify_retries}",
            Py4GW.Console.MessageType.Warning,
        )
        yield from Routines.Yield.Keybinds.DropBundle()
        yield from Routines.Yield.wait(400)  # longer delay between verify retries

    # Final check after all retries
    if Agent.IsHoldingItem(player_id):
        ConsoleLog(
            BOT_NAME,
            f"[DropBundle] FAILED to drop bundle after {times + max_verify_retries} total attempts — "
            f"check DropBundle keybind in GW options",
            Py4GW.Console.MessageType.Error,
        )
        _log_event("DROP_BUNDLE_FAILED",
                   f"Could not drop bundle after {times + max_verify_retries} attempts",
                   attempts=times + max_verify_retries)
    else:
        ConsoleLog(BOT_NAME, f"[DropBundle] Verified drop after {max_verify_retries} retry attempt(s)")
    yield

def _toggle_wait_for_party(enabled: bool) -> Generator:
    _set_custom_utility_enabled(
        enabled,
        skill_names=("wait_if_party_member_too_far",),
        class_names=("WaitIfPartyMemberTooFarUtility",),
    )
    yield


def _set_custom_utility_enabled(
    enabled: bool,
    *,
    skill_names: tuple[str, ...] = (),
    class_names: tuple[str, ...] = (),
) -> bool:
    behavior = _get_custom_behavior(initialize_if_needed=True)
    if behavior is None:
        return False

    for utility in behavior.get_skills_final_list():
        utility_skill_name = getattr(getattr(utility, "custom_skill", None), "skill_name", None)
        utility_class_name = utility.__class__.__name__

        if utility_skill_name in skill_names or utility_class_name in class_names:
            utility.is_enabled = enabled
            return True

    return False


def _get_custom_behavior(initialize_if_needed: bool = True) -> object:
    loader = CustomBehaviorLoader()
    behavior = loader.custom_combat_behavior

    if behavior is None and initialize_if_needed:
        loader.initialize_custom_behavior_candidate()
        behavior = loader.custom_combat_behavior

    return behavior

def track_current_step(bot: "Botting") -> None:
    """Update last step name + idx (best-effort)."""
    global _LAST_STEP_NAME, _LAST_STEP_IDX

    cur = getattr(bot.config.FSM, "current_step_name", None)
    if not cur:
        cur = getattr(bot.States, "CurrentStepName", None)

    if isinstance(cur, str) and cur and cur != _LAST_STEP_NAME:
        _LAST_STEP_NAME = cur
        _LAST_STEP_IDX = _STEP_BY_NAME.get(cur, _LAST_STEP_IDX)
        ConsoleLog("STEP", f"ðŸ‘€ {cur} (idx={_LAST_STEP_IDX})")

def _dist(ax: float, ay: float, bx: float, by: float) -> float:
    return math.hypot(ax - bx, ay - by)


BDS_L2_PART1 = [
    (-11303, -14596),  # allumage torche (premier brasier)
    (-11019, -11550),
    (-9028,  -9021),
    (-6805,  -11511),
    (-8984,  -13842),
]


BDS_L2_PART2 = [
    (-3717, -4254),
    (-8251, -3240),
    (-8278, -1670),
]
BDS_L2_CLEANING = [
    (-7506.89, -12236.26),
    (-7435.12, -10649.25),
    (-9013.61, -9772.06),
    (-10324.58, -10434.43),
    (-10371.20, -12510.16),
    (-8836.63, -11471.01),
]
BDS_L3 = [
    (15692, 17111),
    (12969, 19842),
    (8236,  16950),
    (5549,  9920),
    (-536,  6109),
    (-3814, 5599),
    (-4959, 7558),
    (-7532, 4536),
    (-10984, 486),
    (-12621, 2948),
]

# --- Torch, Brazier, and Boss Helpers ---

def command_type_routine_in_message_is_active(account_email: str, shared_command_type: SharedCommandType) -> bool:
    """Checks if a multibox command is active for an account"""
    index, message = GLOBAL_CACHE.ShMem.PreviewNextMessage(account_email)
    if index == -1 or message is None:
        return False
    if message.Command != shared_command_type:
        return False
    return True


try:
    from Py4GWCoreLib import Item
except Exception:
    Item = None

TORCH_MODEL_IDS = {22341, 22342}
PICKUP_DIST = 180.0
MOVE_TIMEOUT_MS = 9000



def pickup_torch(max_scan_dist: float = 5000, attempts: int = 40) -> Generator:
    inv = PyInventory.PyInventory()
    me = int(Player.GetAgentID())

    ConsoleLog("TORCH", "Scanning for Torch")

    for _ in range(attempts):
        arr = AgentArray.GetItemArray()
        arr = AgentArray.Filter.ByDistance(arr, Player.GetXY(), max_scan_dist)
        arr = AgentArray.Sort.ByDistance(arr, Player.GetXY())

        target_agent: int = 0
        ground_item_id: int = 0
        owner: int = -1

        for a in arr:
            aid = int(a)
            it = Agent.GetItemAgentByID(aid)
            if not it:
                continue

            try:
                owner = int(it.owner)
                if owner not in (0, me):
                    continue
            except Exception as exc:
                ConsoleLog(BOT_NAME, f"[pickup_torch] Error reading owner: {exc}", Py4GW.Console.MessageType.Warning)
                owner = -1

            try:
                gid = int(Agent.GetItemAgentItemID(aid))
            except Exception as exc:
                ConsoleLog(BOT_NAME, f"[pickup_torch] Error getting item ID: {exc}", Py4GW.Console.MessageType.Warning)
                continue

            mid: Optional[int] = None
            if Item is not None:
                try:
                    m = Item.GetModelID(gid)
                    mid = int(m) if isinstance(m, int) else None
                except Exception as exc:
                    ConsoleLog(BOT_NAME, f"[pickup_torch] Error getting model ID: {exc}", Py4GW.Console.MessageType.Warning)
                    mid = None

            if mid in TORCH_MODEL_IDS:
                target_agent = aid
                ground_item_id = gid
                break

        if not target_agent:
            yield from Routines.Yield.wait(150)
            continue

        tx, ty = Agent.GetXY(target_agent)

        # Approche
        try:
            Player.Move(tx, ty)
        except Exception as exc:
            ConsoleLog(BOT_NAME, f"[pickup_torch] Error moving to torch: {exc}", Py4GW.Console.MessageType.Warning)

        start = time.monotonic() * 1000
        while True:
            px, py = Player.GetXY()
            if _dist(px, py, tx, ty) <= PICKUP_DIST:
                break
            if (time.monotonic() * 1000) - start > MOVE_TIMEOUT_MS:
                ConsoleLog("TORCH", "cant reach -> retry")
                target_agent = 0
                break
            yield from Routines.Yield.wait(100)

        if not target_agent:
            continue

        # stop-move pour Ã©viter annulation
        try:
            px, py = Player.GetXY()
            Player.Move(px, py)
        except Exception as exc:
            ConsoleLog(BOT_NAME, f"[pickup_torch] Error stop-move: {exc}", Py4GW.Console.MessageType.Warning)
        yield from Routines.Yield.wait(80)

        # Ciblage
        Player.ChangeTarget(target_agent)
        yield from Routines.Yield.wait(120)

        ConsoleLog("TORCH", f"pickup try agent={target_agent} ground_item_id={ground_item_id} owner={owner}")

        # Essais : agent_id puis ground_item_id (compat multi-build)
        for _try in range(2):
            try:
                inv.PickUpItem(target_agent, True)
            except Exception as exc:
                ConsoleLog(BOT_NAME, f"[pickup_torch] Error picking up by agent: {exc}", Py4GW.Console.MessageType.Warning)
            yield from Routines.Yield.wait(250)

            try:
                inv.PickUpItem(ground_item_id, True)
            except Exception as exc:
                ConsoleLog(BOT_NAME, f"[pickup_torch] Error picking up by item ID: {exc}", Py4GW.Console.MessageType.Warning)
            yield from Routines.Yield.wait(250)

            # fallback interact
            try:
                Player.Interact(target_agent, False)
            except Exception as exc:
                ConsoleLog(BOT_NAME, f"[pickup_torch] Error interacting: {exc}", Py4GW.Console.MessageType.Warning)
            yield from Routines.Yield.wait(450)

            # check disparition (ramassÃ©)
            try:
                still_there = bool(Agent.GetItemAgentByID(target_agent))
            except Exception as exc:
                ConsoleLog(BOT_NAME, f"[pickup_torch] Error checking item: {exc}", Py4GW.Console.MessageType.Warning)
                still_there = False

            if not still_there:
                ConsoleLog("TORCH", "Torch picked up")
                yield
                return

        ConsoleLog("TORCH", "Torch pickup attempt failed -> retry")
        yield from Routines.Yield.wait(200)

    ConsoleLog("TORCH", "Torch pickup failed")
    yield




def nearest_from_array(arr: list[int], max_dist: float) -> int:
    arr = AgentArray.Filter.ByDistance(arr, Player.GetXY(), max_dist)
    arr = AgentArray.Sort.ByDistance(arr, Player.GetXY())
    return int(arr[0]) if len(arr) > 0 else 0


BRAZIER_INTERACT_ATTEMPTS = 4
TORCH_BUFF_ID = 2545
BRAZIER_MAX_RETRIES = 3
BRAZIER_ARRIVE_DIST = 200.0
BRAZIER_MOVE_POLL_MS = 150
BRAZIER_MOVE_TIMEOUT_S = 30.0

def _interact_brazier(label: str, result: list, max_dist: float = 220.0, attempts: int = BRAZIER_INTERACT_ATTEMPTS, refreshed: Optional[list] = None) -> Generator:
    """Find the nearest gadget and interact with it. Logs once with the label and gadget id.
    Sets result[0] = True if a gadget was found and interacted with.

    Early-exit optimization: if the torch buff's `time_remaining` jumps UP between
    the start and end of an attempt (only possible if the brazier interact actually
    landed and renewed the buff), we set `refreshed[0] = True` and break out of the
    loop immediately. Saves ~550ms per skipped attempt on the common-case clean light.

    Threshold: +1000ms. During an attempt's ~550ms wall time, a non-refresh sees
    `time_remaining` drop by ~550ms; a refresh jumps it to the full torch duration
    (~+20s). So +1s is unambiguous — no false positives.
    """
    result[0] = False
    if refreshed is not None:
        refreshed[0] = False
    logged = False
    my_id = Player.GetAgentID()
    for attempt in range(attempts):
        gadgets = AgentArray.GetGadgetArray()
        gad_id = nearest_from_array(gadgets, max_dist)
        if not gad_id:
            yield from Routines.Yield.wait(300)
            continue

        if not logged:
            ConsoleLog(BOT_NAME, f"[BRAZIER] {label} (gadget id: {gad_id})")
            logged = True
            result[0] = True

        # Capture buff time-remaining BEFORE the interact so we can detect renewal.
        buff_before = Effects.GetEffectTimeRemaining(my_id, TORCH_BUFF_ID)

        Player.ChangeTarget(gad_id)
        yield from Routines.Yield.wait(150)
        Player.Interact(gad_id, False)
        yield from Routines.Yield.wait(400)

        # Refresh detection — see docstring.
        buff_after = Effects.GetEffectTimeRemaining(my_id, TORCH_BUFF_ID)
        if buff_after > buff_before + 1000:
            if refreshed is not None:
                refreshed[0] = True
            break

    if not logged:
        px, py = Player.GetXY()
        ConsoleLog(BOT_NAME, f"[BRAZIER] {label} - no gadget found within {max_dist} (the torch runner pos: {px:.0f}, {py:.0f})")
    yield


def _move_to_xy_gen(
    x: float,
    y: float,
    result: Optional[list] = None,
    check_abort: Optional[Callable[[], bool]] = None,
) -> Generator:
    """Move to (x, y), yielding until arrival, timeout, or check_abort() returns True.
    If result is provided, sets result[0] to 'arrived', 'timeout', or 'aborted'."""
    if result is not None:
        result[0] = "arrived"
    deadline = time.monotonic() + BRAZIER_MOVE_TIMEOUT_S
    while True:
        # Bail immediately if dead
        if Agent.IsDead(Player.GetAgentID()):
            if result is not None:
                result[0] = "aborted"
            break
        if check_abort is not None and check_abort():
            if result is not None:
                result[0] = "aborted"
            break
        px, py = Player.GetXY()
        if _dist(px, py, x, y) <= BRAZIER_ARRIVE_DIST:
            break
        if time.monotonic() > deadline:
            if result is not None:
                result[0] = "timeout"
            ConsoleLog(BOT_NAME, f"[BRAZIER] Move timeout")
            break
        Player.Move(x, y)
        yield from Routines.Yield.wait(BRAZIER_MOVE_POLL_MS)


def _is_party_in_combat() -> bool:
    """Return True if any non-excluded, non-the torch runner, non-dead alt is in combat stance.

    Instantaneous check (no sustained-stance confirmation). Used by the L3 brazier
    staged wait to decide whether to pause on arrival at B7/B8/B9 so the party
    can catch up before the torch runner moves on / runs out of buff range.
    """
    my_email = Player.GetAccountEmail()
    _excl_raw = _settings_ini.read_key(_CHAR_NAMES_SECTION, "combat_detection_excludes", "")
    excludes = {n.strip() for n in _excl_raw.split(",") if n.strip()}
    try:
        for acc in GLOBAL_CACHE.ShMem.GetAllAccountData():
            try:
                if not acc.AccountEmail or acc.AccountEmail == my_email:
                    continue
                ad = acc.AgentData
                if getattr(ad, "Is_Dead", False):
                    continue
                try:
                    name = ad.CharacterName or ""
                except Exception:
                    name = ""
                if name in excludes:
                    continue
                if getattr(ad, "Is_InCombatStance", False):
                    return True
            except Exception:
                continue
    except Exception:
        pass
    return False


# Staged party-combat waits on the torch runner's L3 brazier run. Keys are 0-indexed brazier
# positions (6 = B7, 7 = B8, 8 = B9). Value is max-ms to wait if party is in
# combat on arrival; None = wait indefinitely until OoC. Naturally scoped to L3
# since L2 sequences have <7 braziers (the 6..8 keys never fire there).
_BRAZIER_COMBAT_WAIT_CONFIG = {6: 5000, 7: 5000, 8: None}


def _brazier_sequence_gen(points: list[tuple[float, float]], interact_dist: float = 200.0) -> Generator:
    """Walk through brazier waypoints, checking the torch buff (2545) after each one.
    If the buff has expired before the next brazier can be lit, go back to the last
    successfully lit brazier, re-interact to refresh the torch, then retry."""
    total       = len(points)
    idx         = 0
    last_lit    = -1   # index of the last brazier that was successfully lit
    lit_indices: list[int] = []  # all braziers lit so far (any can relight the torch)
    interact_ok = [False]
    move_result = ["arrived"]
    _relight_failed = [False]   # set True by _go_relight if torch buff doesn't refresh

    # Guard: verify the torch runner is actually holding the torch before starting
    if not Agent.IsHoldingItem(Player.GetAgentID()):
        ConsoleLog(BOT_NAME, "[BRAZIER] Torch not held at sequence start — aborting brazier sequence", Py4GW.Console.MessageType.Warning)
        yield
        return

    brazier_start_time = time.monotonic()
    ConsoleLog(BOT_NAME, f"[BRAZIER] Sequence starting — {total} braziers, torch confirmed held")
    _log_checkpoint("BRAZIER_SEQUENCE_START", total=total)

    # Physical-stuck detection: if the torch runner's position barely changes across
    # multiple retry cycles, abort the whole sequence to avoid burning 100+s
    # looping. Samples position before each retry.
    _STUCK_RADIUS_SQ = 200.0 ** 2
    _STUCK_MAX_CONSECUTIVE = 4
    _stuck_counter = 0
    _last_stuck_pos: tuple[float, float] | None = None

    while idx < total:
        # Bail out if the torch runner is dead or party wiped
        if Agent.IsDead(Player.GetAgentID()) or GLOBAL_CACHE.Party.IsPartyDefeated():
            elapsed = time.monotonic() - brazier_start_time
            dead = Agent.IsDead(Player.GetAgentID())
            defeated = GLOBAL_CACHE.Party.IsPartyDefeated()
            ConsoleLog(BOT_NAME, f"[BRAZIER] Aborting at brazier {idx + 1}/{total} — player dead or party defeated ({elapsed:.1f}s elapsed, {last_lit + 1} lit)")
            _log_event("BRAZIER_ABORT",
                       f"Aborted at brazier {idx + 1}/{total}",
                       at_brazier=idx + 1,
                       lit=last_lit + 1,
                       total=total,
                       player_dead=dead,
                       party_defeated=defeated,
                       duration=round(elapsed, 1))
            yield
            return

        x, y  = points[idx]
        label = f"Brazier {idx + 1}/{total}"
        need_buff_check = idx > 0

        def _buff_expired():
            return not Effects.HasEffect(Player.GetAgentID(), TORCH_BUFF_ID)

        def _go_relight():
            """Re-light the torch at a previously lit brazier.

            Failsafe protocol:
              1. Travel to the closest previously-lit brazier
              2. Interact with the gadget
              3. Poll for the torch-flame buff for up to RELIGHT_BUFF_WAIT_MS
              4. If buff didn't apply, retry the interaction (up to
                 RELIGHT_MAX_ATTEMPTS total per brazier). If a brazier is
                 unresponsive across attempts, fall through to the next-closest
                 lit brazier and try again
              5. Only mark _relight_failed after exhausting all lit braziers
                 with no successful re-application of the buff

            After a successful relight the outer loop's `continue` will bring
            the torch runner back to the failed brazier to retry it.
            """
            RELIGHT_MAX_ATTEMPTS      = 3        # per-brazier retries
            RELIGHT_BUFF_WAIT_MS      = 2000     # total poll window for buff
            RELIGHT_BUFF_POLL_MS      = 100      # sub-poll interval
            RELIGHT_BACKOFF_MS        = 500      # between retries at same brazier

            if not lit_indices:
                return

            my_id = Player.GetAgentID()
            # Build a queue of candidate braziers sorted by distance (closest first)
            px0, py0 = Player.GetXY()
            candidates = sorted(
                lit_indices,
                key=lambda i: (points[i][0] - px0) ** 2 + (points[i][1] - py0) ** 2,
            )

            for brazier_idx in candidates:
                # Defensive: if buff somehow came back (e.g. someone walked past a
                # brazier for us, network lag) skip the relight entirely.
                if Effects.HasEffect(my_id, TORCH_BUFF_ID):
                    ConsoleLog(BOT_NAME, "[BRAZIER] Relight not needed — torch buff already active")
                    return

                bx, by = points[brazier_idx]

                for attempt in range(1, RELIGHT_MAX_ATTEMPTS + 1):
                    px, py = Player.GetXY()
                    dist = ((bx - px) ** 2 + (by - py) ** 2) ** 0.5
                    holding_before = Agent.IsHoldingItem(my_id)
                    ConsoleLog(
                        BOT_NAME,
                        f"[BRAZIER] Relight brazier {brazier_idx + 1}/{total} "
                        f"attempt {attempt}/{RELIGHT_MAX_ATTEMPTS} "
                        f"(dist={dist:.0f}, candidates_left={len(candidates) - candidates.index(brazier_idx)}, "
                        f"lit_count={len(lit_indices)}, holding={holding_before}, the torch runner: {px:.0f},{py:.0f})",
                    )

                    # If the torch got dropped somehow we can't relight — abort.
                    if not holding_before:
                        ConsoleLog(
                            BOT_NAME,
                            f"[BRAZIER] Relight aborted — torch not held (brazier {brazier_idx + 1}, attempt {attempt})",
                            Py4GW.Console.MessageType.Error,
                        )
                        _log_event(
                            "BRAZIER_RELIGHT_NO_TORCH",
                            f"Relight aborted — torch not held",
                            brazier=brazier_idx + 1,
                            attempt=attempt,
                        )
                        _relight_failed[0] = True
                        return

                    yield from _move_to_xy_gen(bx, by)
                    yield from Routines.Yield.wait(250)
                    yield from _interact_brazier(
                        f"Re-lighting brazier {brazier_idx + 1}/{total} (attempt {attempt})",
                        interact_ok,
                        interact_dist,
                    )

                    # Poll for the buff for up to RELIGHT_BUFF_WAIT_MS — the
                    # original fixed 500ms wait was too short on some runs;
                    # the buff applies with latency after the interaction.
                    waited_ms = 0
                    buff_after = False
                    while waited_ms < RELIGHT_BUFF_WAIT_MS:
                        if Effects.HasEffect(my_id, TORCH_BUFF_ID):
                            buff_after = True
                            break
                        yield from Routines.Yield.wait(RELIGHT_BUFF_POLL_MS)
                        waited_ms += RELIGHT_BUFF_POLL_MS

                    holding_after = Agent.IsHoldingItem(my_id)
                    ConsoleLog(
                        BOT_NAME,
                        f"[BRAZIER] Relight brazier {brazier_idx + 1} attempt {attempt}: "
                        f"interact_ok={interact_ok[0]} holding={holding_after} buff={buff_after} waited={waited_ms}ms",
                    )

                    if buff_after:
                        _log_event(
                            "BRAZIER_RELIGHT_SUCCESS",
                            f"Relit at brazier {brazier_idx + 1}/{total} on attempt {attempt}",
                            brazier=brazier_idx + 1,
                            attempt=attempt,
                            waited_ms=waited_ms,
                        )
                        return  # Buff active — outer loop retries the failed brazier

                    # Torch dropped during attempt — don't keep trying
                    if not holding_after:
                        ConsoleLog(
                            BOT_NAME,
                            f"[BRAZIER] Relight aborted — torch dropped during attempt "
                            f"(brazier {brazier_idx + 1}, attempt {attempt})",
                            Py4GW.Console.MessageType.Error,
                        )
                        _log_event(
                            "BRAZIER_RELIGHT_TORCH_DROPPED",
                            f"Torch dropped during relight",
                            brazier=brazier_idx + 1,
                            attempt=attempt,
                        )
                        _relight_failed[0] = True
                        return

                    # Back off briefly before next attempt on this brazier
                    yield from Routines.Yield.wait(RELIGHT_BACKOFF_MS)

                # All attempts on this brazier failed; try the next candidate
                ConsoleLog(
                    BOT_NAME,
                    f"[BRAZIER] Brazier {brazier_idx + 1} unresponsive after "
                    f"{RELIGHT_MAX_ATTEMPTS} attempts — falling back to next lit brazier",
                    Py4GW.Console.MessageType.Warning,
                )
                _log_event(
                    "BRAZIER_RELIGHT_CANDIDATE_FAILED",
                    f"Brazier {brazier_idx + 1} failed {RELIGHT_MAX_ATTEMPTS} relight attempts",
                    brazier=brazier_idx + 1,
                )

            # Every lit brazier tried; none applied the buff
            ConsoleLog(
                BOT_NAME,
                f"[BRAZIER] Relight failed — exhausted all {len(lit_indices)} lit braziers "
                f"({RELIGHT_MAX_ATTEMPTS} attempts each) without buff refresh",
                Py4GW.Console.MessageType.Error,
            )
            _log_event(
                "BRAZIER_RELIGHT_FAILED",
                f"Exhausted all {len(lit_indices)} lit braziers without buff refresh",
                at_brazier=idx + 1,
                lit_count=len(lit_indices),
                total=total,
            )
            _relight_failed[0] = True

        for retry in range(BRAZIER_MAX_RETRIES):
            if retry:
                ConsoleLog(BOT_NAME, f"[BRAZIER] Retry {retry} for {label}")

            # Physical-stuck detection: compare current pos to the last retry pos
            _cur_px, _cur_py = Player.GetXY()
            if _last_stuck_pos is not None:
                _dx = _cur_px - _last_stuck_pos[0]
                _dy = _cur_py - _last_stuck_pos[1]
                if (_dx * _dx + _dy * _dy) < _STUCK_RADIUS_SQ:
                    _stuck_counter += 1
                else:
                    _stuck_counter = 0
            _last_stuck_pos = (_cur_px, _cur_py)
            if _stuck_counter >= _STUCK_MAX_CONSECUTIVE:
                ConsoleLog(
                    BOT_NAME,
                    f"[BRAZIER] STUCK: the torch runner hasn't moved meaningfully across {_stuck_counter} retries — aborting sequence",
                    Py4GW.Console.MessageType.Error,
                )
                _log_event(
                    "BRAZIER_STUCK_ABORT",
                    f"Aborted at brazier {idx + 1}/{total} — physically stuck",
                    at_brazier=idx + 1,
                    lit=last_lit + 1,
                    total=total,
                    stuck_pos=[round(_cur_px, 1), round(_cur_py, 1)],
                )
                elapsed = time.monotonic() - brazier_start_time
                _log_checkpoint("BRAZIER_SEQUENCE_END",
                                lit=last_lit + 1,
                                total=total,
                                duration=round(elapsed, 1),
                                all_lit=False,
                                aborted="stuck")
                yield
                return

            move_result[0] = "arrived"
            check_abort = _buff_expired if need_buff_check else None
            yield from _move_to_xy_gen(x, y, move_result, check_abort)

            if move_result[0] == "aborted":
                holding = Agent.IsHoldingItem(Player.GetAgentID())
                ConsoleLog(BOT_NAME, f"[BRAZIER] Buff expired during move to {label} (holding={holding})")
                yield from _go_relight()
                if _relight_failed[0]:
                    break
                continue

            yield from Routines.Yield.wait(250)
            buff_refreshed = [False]
            yield from _interact_brazier(label, interact_ok, interact_dist, refreshed=buff_refreshed)
            # Skip the post-interact buff-settle wait when the inner loop already
            # confirmed a refresh — the buff is verified present, no need to wait.
            if not buff_refreshed[0]:
                yield from Routines.Yield.wait(500)

            if not interact_ok[0]:
                ConsoleLog(BOT_NAME, f"[BRAZIER] {label} - could not interact")
                yield from _go_relight()
                if _relight_failed[0]:
                    break
                continue

            if idx == 0:
                ConsoleLog(BOT_NAME, f"[BRAZIER] {label} lit (start) t={time.monotonic():.2f}")
                last_lit = idx
                if idx not in lit_indices:
                    lit_indices.append(idx)
                _stuck_counter = 0
                _last_stuck_pos = None
                break

            my_id = Player.GetAgentID()
            if Effects.HasEffect(my_id, TORCH_BUFF_ID):
                ConsoleLog(BOT_NAME, f"[BRAZIER] {label} lit t={time.monotonic():.2f}")
                last_lit = idx
                if idx not in lit_indices:
                    lit_indices.append(idx)
                _stuck_counter = 0
                _last_stuck_pos = None
                break

            holding = Agent.IsHoldingItem(Player.GetAgentID())
            ConsoleLog(BOT_NAME, f"[BRAZIER] Buff expired after interacting with {label} (holding={holding})")
            yield from _go_relight()
            if _relight_failed[0]:
                break
        else:
            ConsoleLog(BOT_NAME, f"[BRAZIER] {label} failed after {BRAZIER_MAX_RETRIES} retries")

        if _relight_failed[0]:
            ConsoleLog(BOT_NAME, "[BRAZIER] Aborting sequence — re-light failed to refresh torch buff")
            elapsed = time.monotonic() - brazier_start_time
            _log_checkpoint("BRAZIER_SEQUENCE_END",
                            lit=last_lit + 1,
                            total=total,
                            duration=round(elapsed, 1),
                            all_lit=False,
                            aborted="relight_failed")
            yield
            return

        # Staged party-combat wait at B7/B8/B9, AFTER the brazier is successfully
        # lit. If the main party is still in combat, pause so they can catch up
        # before the torch runner moves to the next brazier. If we actually wait, re-light
        # the current brazier before leaving so the torch buff is fresh for
        # the next leg (buff drains during the wait).
        #   B7 (idx=6): wait up to 5s, early-exit when they clear combat
        #   B8 (idx=7): wait up to 5s, early-exit when they clear combat
        #   B9 (idx=8): wait until they clear combat (no timeout)
        # Only runs if the brazier was successfully lit this iteration (i.e.
        # last_lit == idx). On first-brazier retry paths where last_lit was
        # already this idx, the wait still applies — benign because the
        # re-light step just refreshes the buff.
        if last_lit == idx and idx in _BRAZIER_COMBAT_WAIT_CONFIG and _is_party_in_combat():
            max_ms = _BRAZIER_COMBAT_WAIT_CONFIG[idx]
            wait_start = time.monotonic()
            ConsoleLog(
                BOT_NAME,
                f"[BRAZIER] Party in combat after lighting B{idx+1} — waiting "
                f"{'until OoC' if max_ms is None else f'up to {max_ms/1000:.0f}s'}",
            )
            _log_event(
                "BRAZIER_PARTY_COMBAT_WAIT_START",
                f"B{idx+1} party in combat post-light",
                brazier=idx + 1,
                max_ms=max_ms,
            )
            _COMBAT_POLL_MS = 250
            _HOLD_PIXELSTACK_INTERVAL_MS = 1500
            # Pin the torch runner at the brazier for the wait duration.
            #
            # Why we need to pin her: the `_interact_brazier` step above
            # arrives "close enough" to the gadget (interact range > 200u
            # check in the post-wait re-light) but not necessarily at zero
            # distance. Combat auto-chase or residual GW movement can drift
            # her out of the 200u re-light window during the wait (observed
            # run 138 B8: 500u+ drift, "no gadget found within 200.0").
            #
            # Why PixelStack every 1500ms (not every 250ms like before):
            # each PixelStack message queues in the torch runner's OWN inbox and the
            # Messaging widget processes them serially (~1.5-2s each,
            # movement+timeout+recovery). At 250ms cadence a 5s wait
            # queued 20 messages → 30-40s of stale backlog after the wait
            # ended (observed run 140: B7 hold held correctly but post-wait
            # backlog stalled the torch runner 33s, buff expired, B9 backtrack). At
            # 1500ms cadence a 5s wait queues 3-4 messages — minimal
            # backlog, buff safe for the next brazier traversal.
            #
            # Combat polling stays at 250ms so end-of-combat is still
            # detected promptly.
            brazier_x, brazier_y = points[idx]
            hold_target_x, hold_target_y = _shandra_adjusted_target(
                brazier_x, brazier_y, brazier_x, brazier_y,
                context=f"B{idx+1}-combat-wait-hold",
            )
            torch_runner_email = Player.GetAccountEmail()
            last_hold_send_ms = -_HOLD_PIXELSTACK_INTERVAL_MS  # fire immediately on first tick
            while _is_party_in_combat():
                elapsed_ms = (time.monotonic() - wait_start) * 1000
                if elapsed_ms - last_hold_send_ms >= _HOLD_PIXELSTACK_INTERVAL_MS:
                    try:
                        GLOBAL_CACHE.ShMem.SendMessage(
                            torch_runner_email, torch_runner_email,
                            SharedCommandType.PixelStack,
                            (hold_target_x, hold_target_y, 0, 0),
                        )
                    except Exception as exc:
                        _log_error(f"_brazier_sequence_gen.combat_wait_hold_B{idx+1}", exc)
                    last_hold_send_ms = elapsed_ms
                if max_ms is not None and elapsed_ms >= max_ms:
                    _log_event(
                        "BRAZIER_PARTY_COMBAT_WAIT_TIMEOUT",
                        f"B{idx+1} wait timed out after {max_ms/1000:.0f}s",
                        brazier=idx + 1,
                    )
                    break
                yield from Routines.Yield.wait(_COMBAT_POLL_MS)
            waited_ms = (time.monotonic() - wait_start) * 1000
            _log_event(
                "BRAZIER_PARTY_COMBAT_WAIT_END",
                f"B{idx+1} waited {waited_ms:.0f}ms",
                brazier=idx + 1,
                elapsed_ms=round(waited_ms),
            )

            # Re-light the current brazier so the torch runner departs with a fresh torch
            # buff. She's still standing next to it from the light-step above,
            # so the interact range check is satisfied. Uses the same
            # _interact_brazier helper so retry/backoff is consistent.
            ConsoleLog(BOT_NAME, f"[BRAZIER] Re-lighting B{idx+1} for fresh buff before next leg")
            _log_event("BRAZIER_RELIGHT_POST_WAIT", f"Re-lighting B{idx+1}", brazier=idx + 1)
            relight_ok = [False]
            relight_refreshed = [False]
            yield from _interact_brazier(
                f"{label} (post-wait re-light)",
                relight_ok,
                interact_dist,
                refreshed=relight_refreshed,
            )
            if not relight_ok[0]:
                ConsoleLog(
                    BOT_NAME,
                    f"[BRAZIER] Post-wait re-light of B{idx+1} did not confirm — relying on existing buff",
                    Py4GW.Console.MessageType.Warning,
                )

        idx += 1

    elapsed = time.monotonic() - brazier_start_time
    ConsoleLog(BOT_NAME, f"[BRAZIER] Sequence complete ({total} braziers) — {last_lit + 1}/{total} lit in {elapsed:.1f}s")
    _log_checkpoint("BRAZIER_SEQUENCE_END",
                    lit=last_lit + 1,
                    total=total,
                    duration=round(elapsed, 1),
                    all_lit=(last_lit + 1 == total))
    yield


def _log_cleaning_room() -> Generator:
    """Log message when L2 - Cleaning header state is reached."""
    ConsoleLog(BOT_NAME, "Clearing room level 2")
    yield

def _log_l2_post_brazier_combat() -> Generator:
    """Log position when entering combat after L2 brazier room 2."""
    px, py = Player.GetXY()
    ConsoleLog(BOT_NAME, f"[L2 Post-Brazier] Entered combat area at ({px:.0f}, {py:.0f})")
    yield


def run_brazier_sequence(points: list[tuple[float, float]], interact_dist: float = 200.0) -> None:
    bot.States.AddCustomState(
        lambda p=points, d=interact_dist: _brazier_sequence_gen(p, d),
        "Brazier sequence"
    )


FENDI_GADGET_ID = 8934
FENDI_SCAN_RADIUS = 700.0  # un peu plus large que 500 pour Ãªtre safe

def _target_fendi_chest_agent_id() -> int:
    """Retourne l'agent_id du coffre de Fendi (filtrÃ© par gadget_id)."""
    gadgets = AgentArray.GetGadgetArray()
    gadgets = AgentArray.Filter.ByDistance(gadgets, FENDI_CHEST_POSITION, FENDI_SCAN_RADIUS)
    gadgets = AgentArray.Sort.ByDistance(gadgets, FENDI_CHEST_POSITION)

    best = 0
    for a in gadgets:
        aid = int(a)
        g = Agent.GetGadgetAgentByID(aid)
        if not g:
            continue

        # g.gadget_id est la signature la plus fiable ici
        try:
            if int(g.gadget_id) == int(FENDI_GADGET_ID):
                best = aid
                break
        except Exception as exc:
            ConsoleLog(BOT_NAME, f"[_target_fendi_chest_agent_id] Error: {exc}", Py4GW.Console.MessageType.Warning)
            continue

    return best



def target_nearest_npc() -> None:
    npc_array = AgentArray.GetNPCMinipetArray()
    npc_array = AgentArray.Filter.ByDistance(npc_array,Player.GetXY(), 200)
    npc_array = AgentArray.Sort.ByDistance(npc_array, Player.GetXY())
    if len(npc_array) > 0:
        Player.ChangeTarget(npc_array[0])

CHEST_OPEN_ATTEMPTS = 3  # number of interact attempts per account

def open_fendi_chest() -> Generator:
    """Multibox coordination for opening the final chest"""
    ConsoleLog(BOT_NAME, "Opening final chest with multibox...")

    target = _target_fendi_chest_agent_id()
    if target == 0:
        ConsoleLog(BOT_NAME, "No Fendi chest found (gadget_id filter)!")
        yield
        return

    sender_email = Player.GetAccountEmail()
    accounts = GLOBAL_CACHE.ShMem.GetAllAccountData()

    Player.ChangeTarget(target)
    yield from Routines.Yield.wait(150)

    # --- LEADER: interact multiple times to ensure chest opens ---
    for attempt in range(CHEST_OPEN_ATTEMPTS):
        ConsoleLog(BOT_NAME, f"Leader opening chest (attempt {attempt + 1}/{CHEST_OPEN_ATTEMPTS})")
        Player.Interact(target, False)
        yield from Routines.Yield.wait(500)

    # Wait for the leader to finish
    while command_type_routine_in_message_is_active(sender_email, SharedCommandType.InteractWithTarget):
        yield from Routines.Yield.wait(250)
    while command_type_routine_in_message_is_active(sender_email, SharedCommandType.PickUpLoot):
        yield from Routines.Yield.wait(1000)
    yield from Routines.Yield.wait(5000)

    # Command opening for all members with multiple attempts
    for account in accounts:
        if not account.AccountEmail or sender_email == account.AccountEmail:
            continue
        ConsoleLog(BOT_NAME, f"Ordering {account.AccountEmail} to open chest")

        for attempt in range(CHEST_OPEN_ATTEMPTS):
            ConsoleLog(BOT_NAME, f"{account.AccountEmail} attempt {attempt + 1}/{CHEST_OPEN_ATTEMPTS}")
            GLOBAL_CACHE.ShMem.SendMessage(
                sender_email,
                account.AccountEmail,
                SharedCommandType.InteractWithTarget,
                (target, 0, 0, 0),
            )
            yield from Routines.Yield.wait(1000)

        while command_type_routine_in_message_is_active(account.AccountEmail, SharedCommandType.InteractWithTarget):
            yield from Routines.Yield.wait(1000)
        while command_type_routine_in_message_is_active(account.AccountEmail, SharedCommandType.PickUpLoot):
            yield from Routines.Yield.wait(1000)
        yield from Routines.Yield.wait(5000)

    ConsoleLog(BOT_NAME, "ALL accounts opened chest!")
    yield


def resolve_fendi_fight() -> Generator:
    """
    Hold near Fendi's position, kill all enemies in compass range,
    and require 20s stable verification with neither Fendi Nin
    nor Soul of Fendi present before finishing.
    """
    FENDI_NIN_MODEL_ID = 7064
    SOUL_OF_FENDI_MODEL_ID = 7065
    boss_model_ids = {FENDI_NIN_MODEL_ID, SOUL_OF_FENDI_MODEL_ID}
    anchor_x, anchor_y = (-16022.9, 17889.9)
    compass_sq = Range.Compass.value ** 2
    anchor_soft_radius_sq = 750.0 ** 2
    stable_verify_ms = 0

    # Fendi is a cyclic fight: kill Fendi Nin -> Soul of Fendi briefly appears
    # (the only real damage window) -> reverts to Fendi Nin -> repeat until
    # Soul of Fendi dies. Strategy: 100% focus the boss forms, ignore trash
    # (trash dies as AoE collateral and doesn't block Soul phase progress).
    FENDI_NIN_CALL_WEIGHT = 1  # 100% priority when Fendi Nin is up
    TRASH_CALL_WEIGHT = 0      # never intentionally call trash
    cycle_length = FENDI_NIN_CALL_WEIGHT + TRASH_CALL_WEIGHT
    tick_counter = 0

    fight_start = time.time()
    _log_checkpoint("FENDI_FIGHT_START")
    fendi_nin_seen = False
    soul_of_fendi_seen = False
    soul_of_fendi_start: Optional[float] = None

    # Phase tracking: we toggle each time a form enters/exits the compass range.
    fendi_nin_currently_present = False
    soul_currently_present = False
    fendi_nin_phase_count = 0
    soul_phase_count = 0
    last_soul_start_time: Optional[float] = None
    soul_phase_total_duration = 0.0
    fendi_nin_phase_total_duration = 0.0
    last_fendi_nin_start_time: Optional[float] = None

    while stable_verify_ms < 20000:
        if Map.GetMapID() != SOO_LVL3_MAP_ID:
            break
        if not Routines.Checks.Map.MapValid():
            yield from Routines.Yield.wait(500)
            continue

        player_pos = Player.GetXY()
        if not player_pos:
            yield from Routines.Yield.wait(500)
            continue

        dx_a = anchor_x - player_pos[0]
        dy_a = anchor_y - player_pos[1]
        if (dx_a * dx_a + dy_a * dy_a) > anchor_soft_radius_sq:
            Player.Move(anchor_x, anchor_y)

        nearest_trash_id = 0
        nearest_trash_dist_sq = float("inf")
        nearest_trash_monk_id = 0
        nearest_trash_monk_dist_sq = float("inf")
        nearest_trash_caster_id = 0
        nearest_trash_caster_dist_sq = float("inf")
        fendi_nin_id = 0
        fendi_nin_dist_sq = float("inf")
        soul_of_fendi_id = 0
        soul_of_fendi_dist_sq = float("inf")
        boss_present = False

        for agent_id in AgentArray.GetEnemyArray():
            if not Agent.IsAlive(agent_id):
                continue
            enemy_pos = Agent.GetXY(agent_id)
            if not enemy_pos:
                continue
            ax = enemy_pos[0] - anchor_x
            ay = enemy_pos[1] - anchor_y
            if (ax * ax + ay * ay) > compass_sq:
                continue
            px = enemy_pos[0] - player_pos[0]
            py = enemy_pos[1] - player_pos[1]
            dist_sq = px * px + py * py
            model_id = Agent.GetModelID(agent_id)
            if model_id == FENDI_NIN_MODEL_ID:
                boss_present = True
                if dist_sq < fendi_nin_dist_sq:
                    fendi_nin_dist_sq = dist_sq
                    fendi_nin_id = agent_id
            elif model_id == SOUL_OF_FENDI_MODEL_ID:
                boss_present = True
                if dist_sq < soul_of_fendi_dist_sq:
                    soul_of_fendi_dist_sq = dist_sq
                    soul_of_fendi_id = agent_id
            else:
                if dist_sq < nearest_trash_dist_sq:
                    nearest_trash_dist_sq = dist_sq
                    nearest_trash_id = agent_id
                # Classify trash by profession — monk first priority, then caster
                try:
                    primary_prof = Agent.GetProfessions(agent_id)[0]
                except Exception:
                    primary_prof = 0
                if primary_prof == 3:  # Monk
                    if dist_sq < nearest_trash_monk_dist_sq:
                        nearest_trash_monk_dist_sq = dist_sq
                        nearest_trash_monk_id = agent_id
                else:
                    try:
                        is_melee = Agent.IsMelee(agent_id)
                    except Exception:
                        is_melee = True
                    if not is_melee and dist_sq < nearest_trash_caster_dist_sq:
                        nearest_trash_caster_dist_sq = dist_sq
                        nearest_trash_caster_id = agent_id

        # First-sighting flags (preserved for summary)
        if fendi_nin_id and not fendi_nin_seen:
            fendi_nin_seen = True
            _log_checkpoint("FENDI_NIN_ENGAGED")
        if soul_of_fendi_id and not soul_of_fendi_seen:
            soul_of_fendi_seen = True
            soul_of_fendi_start = time.time()
            _log_checkpoint("SOUL_OF_FENDI_ENGAGED")

        # Phase cycle tracking: detect enter/exit transitions to count cycles
        now_t = time.time()

        # Fendi Nin present now
        fendi_nin_present_now = fendi_nin_id != 0
        if fendi_nin_present_now and not fendi_nin_currently_present:
            # Fendi Nin just appeared (new phase start)
            fendi_nin_currently_present = True
            fendi_nin_phase_count += 1
            last_fendi_nin_start_time = now_t
            _log_event("FENDI_PHASE", f"Fendi Nin phase #{fendi_nin_phase_count} started",
                       phase=fendi_nin_phase_count, form="Fendi_Nin")
        elif not fendi_nin_present_now and fendi_nin_currently_present:
            # Fendi Nin just disappeared (phase ended)
            fendi_nin_currently_present = False
            if last_fendi_nin_start_time is not None:
                phase_dur = now_t - last_fendi_nin_start_time
                fendi_nin_phase_total_duration += phase_dur
                _log_event("FENDI_PHASE",
                           f"Fendi Nin phase #{fendi_nin_phase_count} ended after {phase_dur:.1f}s",
                           phase=fendi_nin_phase_count, form="Fendi_Nin",
                           duration=round(phase_dur, 1))
                last_fendi_nin_start_time = None

        # Soul of Fendi present now
        soul_present_now = soul_of_fendi_id != 0
        if soul_present_now and not soul_currently_present:
            # Soul of Fendi just appeared (damage window opened)
            soul_currently_present = True
            soul_phase_count += 1
            last_soul_start_time = now_t
            _log_event("FENDI_PHASE", f"Soul of Fendi phase #{soul_phase_count} started",
                       phase=soul_phase_count, form="Soul_of_Fendi")
        elif not soul_present_now and soul_currently_present:
            # Soul of Fendi just disappeared (damage window closed)
            soul_currently_present = False
            if last_soul_start_time is not None:
                phase_dur = now_t - last_soul_start_time
                soul_phase_total_duration += phase_dur
                _log_event("FENDI_PHASE",
                           f"Soul of Fendi phase #{soul_phase_count} ended after {phase_dur:.1f}s",
                           phase=soul_phase_count, form="Soul_of_Fendi",
                           duration=round(phase_dur, 1))
                last_soul_start_time = None

        # Targeting logic:
        # - Soul of Fendi always wins (100% priority — real damage window)
        # - Fendi Nin weighted cycle with trash (currently 100% Fendi Nin)
        # - Trash fallback uses monk > caster > nearest priority so healers die first
        def _pick_trash_target() -> int:
            return nearest_trash_monk_id or nearest_trash_caster_id or nearest_trash_id

        if soul_of_fendi_id:
            priority_target = soul_of_fendi_id
        elif fendi_nin_id:
            # Call trash only if this tick falls in the trash slot AND trash exists
            is_trash_tick = tick_counter >= FENDI_NIN_CALL_WEIGHT
            trash_pick = _pick_trash_target() if is_trash_tick else 0
            if trash_pick:
                priority_target = trash_pick
            else:
                priority_target = fendi_nin_id
        else:
            priority_target = _pick_trash_target()

        tick_counter = (tick_counter + 1) % cycle_length

        if priority_target:
            stable_verify_ms = 0
            Player.ChangeTarget(priority_target)
            Player.Interact(priority_target, True)
            # Broadcast to party so every CustomBehaviors alt focus-fires this target
            try:
                CustomBehaviorParty().set_party_custom_target(priority_target)
            except Exception as exc:
                _log_error("resolve_fendi_fight.set_party_custom_target", exc)
            target_pos = Agent.GetXY(priority_target)
            if target_pos:
                dx = target_pos[0] - player_pos[0]
                dy = target_pos[1] - player_pos[1]
                if (dx * dx + dy * dy) > (Range.Earshot.value ** 2):
                    Player.Move(target_pos[0], target_pos[1])
        else:
            if not boss_present:
                stable_verify_ms += 500
            else:
                stable_verify_ms = 0
            Player.Move(anchor_x, anchor_y)

        yield from Routines.Yield.wait(500)

    ConsoleLog(BOT_NAME, "Fendi is dead -- area clear for 20s. Goodluck on chest ^.^")
    # Clear party called target so alts revert to natural targeting post-fight
    try:
        CustomBehaviorParty().set_party_custom_target(None)
    except Exception as exc:
        _log_error("resolve_fendi_fight.clear_party_target", exc)
    fight_duration = time.time() - fight_start
    soul_duration = (time.time() - soul_of_fendi_start) if soul_of_fendi_start else 0.0

    # If Fendi died mid-phase, close out the open phase durations
    now_t = time.time()
    if fendi_nin_currently_present and last_fendi_nin_start_time is not None:
        fendi_nin_phase_total_duration += (now_t - last_fendi_nin_start_time)
    if soul_currently_present and last_soul_start_time is not None:
        soul_phase_total_duration += (now_t - last_soul_start_time)

    _log_checkpoint("FENDI_FIGHT_END",
                    duration=round(fight_duration, 1),
                    saw_fendi_nin=fendi_nin_seen,
                    saw_soul_of_fendi=soul_of_fendi_seen,
                    soul_phase_duration=round(soul_duration, 1),
                    fendi_nin_phases=fendi_nin_phase_count,
                    soul_phases=soul_phase_count,
                    total_fendi_nin_time=round(fendi_nin_phase_total_duration, 1),
                    total_soul_time=round(soul_phase_total_duration, 1))
    yield


# --- Wipe Recovery and Step Anchors ---

def wait_for_map_change(target_map_id: int, timeout_seconds: int = 60) -> Generator:
    """Wait for map change with timeout"""
    ConsoleLog(BOT_NAME, f"Waiting for map change to {target_map_id}...")
    timeout = time.monotonic() + timeout_seconds
    while True:
        current_map = Map.GetMapID()
        if current_map == target_map_id:
            ConsoleLog(BOT_NAME, f"Map change detected! Now in map {target_map_id}")
            return True
        if time.monotonic() > timeout:
            ConsoleLog(BOT_NAME, f"Timeout waiting for map {target_map_id}")
            return False
        yield from Routines.Yield.wait(500)


def _on_party_wipe(bot: "Botting") -> Generator:
    global L3_BOSS_ROUTE_UNLOCKED
    # Wait until we are alive again
    while Agent.IsDead(Player.GetAgentID()):
        yield from bot.Wait._coro_for_time(1000)
        if not Routines.Checks.Map.MapValid():
            bot.config.FSM.resume()
            return

    ConsoleLog("Res Check", "We ressed retrying!")
    yield from bot.Wait._coro_for_time(3000)

    # Map-safe anchors (YOU said you replaced jumps by headers)
    # These should be the JUMPABLE step names (anchors), not just visual headers.
    SHRINES_BY_MAP = {
        SOO_LVL1_MAP_ID: [
            ("Secure return - L1", 8503.9,12143.5),
            ("Secure return 1 - L1", 15953.0, 11902.0)
        ],
        SOO_LVL2_MAP_ID: [
            ("Secure return - L2", -14076.0, -19457.0)
        ],
        SOO_LVL3_MAP_ID: [
            ("Secure return 1 - L3", 17544.0, 18810.0),
            ("Secure return boss - L3", -9686.32, 2632)
        ],
    }

    def pick_nearest_anchor(map_id: int, px: float, py: float) -> str:
        candidates = SHRINES_BY_MAP.get(map_id)
        if not candidates:
            return "Reset farm"  # generic fallback anchor

        best_name = candidates[0][0]
        best_d2 = float("inf")
        for name, sx, sy in candidates:
            d2 = (px - sx) ** 2 + (py - sy) ** 2
            if d2 < best_d2:
                best_d2 = d2
                best_name = name
        return best_name

    player_x, player_y = Player.GetXY()
    map_id = int(Map.GetMapID())
    torch_runner_dead = Agent.IsDead(Player.GetAgentID())
    party_defeated = GLOBAL_CACHE.Party.IsPartyDefeated()
    ConsoleLog("Res Check", f"[WIPE] map={map_id} pos=({player_x:.0f},{player_y:.0f}) torch_runner_dead={torch_runner_dead} party_defeated={party_defeated}")

    bot.config.FSM.pause()

    # Not in dungeon maps -> resign and go to generic secure return
    if map_id not in (SOO_LVL1_MAP_ID, SOO_LVL2_MAP_ID, SOO_LVL3_MAP_ID):
        bot.Multibox.ResignParty()
        yield from bot.Wait._coro_for_time(10000)
        bot.config.FSM.jump_to_state_by_name("Reset farm")
        bot.config.FSM.resume()
        return

    # Full party defeated -> let widget handle return
    if GLOBAL_CACHE.Party.IsPartyDefeated():
        yield from bot.Wait._coro_for_time(10000)
        bot.config.FSM.jump_to_state_by_name("Reset farm")
        bot.config.FSM.resume()
        return

    if map_id == SOO_LVL3_MAP_ID:
        if L3_BOSS_ROUTE_UNLOCKED:
            chosen = "Secure return boss - L3"
        else:
            chosen = "Secure return 1 - L3"
    else:
        chosen = pick_nearest_anchor(map_id, float(player_x), float(player_y))                          

    ConsoleLog("Res Check", f"â†© wipe-route -> {chosen} (map={map_id}, pos=({player_x:.0f},{player_y:.0f}))")
    bot.config.FSM.jump_to_state_by_name(chosen)

    bot.config.FSM.resume()
    return


def on_party_wipe(bot: "Botting") -> None:
    ConsoleLog("on_party_wipe", "event triggered")
    try:
        map_id = Map.GetMapID()
    except Exception:
        map_id = 0
    _log_event("PARTY_WIPE", f"Wipe triggered on map {map_id}", map_id=map_id)
    _log_checkpoint("PARTY_WIPE", map_id=map_id)
    fsm = bot.config.FSM
    fsm.pause()
    # Clean up torch-related coroutines to stop spam
    bot.States.RemoveManagedCoroutine("TorchDropScanner")
    bot.States.RemoveManagedCoroutine("TorchPartyWaypointManager")
    bot.States.RemoveManagedCoroutine("OnWipe_OPD")
    bot.States.AddManagedCoroutine("OnWipe_OPD", lambda: _on_party_wipe(bot))

def S_Path(name: str, points: list[tuple[float, float]], map_id: Optional[int] = None) -> None:
    bot.States.AddHeader(name)

    # âœ… Step "ancre" jumpable
    bot.States.AddCustomState(_step_anchor, name)

    n = len(points)
    for i, (x, y) in enumerate(points, start=1):
        bot.Move.XY(float(x), float(y), step_name=f"{name} - {i}/{n}")

def use_summons() -> Generator:
    """
    Uses:
    - Summons (model ID 30209)
    - Legionnary Summoning Crystal (model ID 37810)
    - Mysterious 31155
    """

    summons = [
        ("Summons", 30209),
        ("Legionnary Crystal", 37810),
        ("Mysterious", 31155),
    ]

    for name, model_id in summons:
        ConsoleLog("use_summons", f"Searching for {name}...", log=True)

        item_id = GLOBAL_CACHE.Inventory.GetFirstModelID(model_id)

        if item_id:
            ConsoleLog("use_summons", f"{name} found (item_id: {item_id}), using...", log=True)
            GLOBAL_CACHE.Inventory.UseItem(item_id)
            yield from Routines.Yield.wait(1000)
            ConsoleLog("use_summons", f"{name} used!", log=True)
        else:
            ConsoleLog("use_summons", f"{name} not found in inventory", log=True)

    yield

_RANDOM_DISTRICTS = [
    6,  # EuropeItalian
    7,  # EuropeSpanish
    8,  # EuropePolish
    9,  # EuropeRussian
]

def _coro_travel_random_district(target_map_id: int) -> Generator:
    if _randomize_district:
        district = random.choice(_RANDOM_DISTRICTS)
        ConsoleLog(BOT_NAME, f"Traveling to map {target_map_id} with random district {district}")
        Map.TravelToDistrict(target_map_id, district=district)
        yield from Routines.Yield.wait(500)
        yield from bot.Wait._coro_for_map_load(target_map_id=target_map_id)
    else:
        yield from bot.Map._coro_travel(target_map_id, "")

def _step_anchor() -> Generator:
    yield

def loop_marker() -> Generator:
    """Empty marker for loop restart point"""
    ConsoleLog(BOT_NAME, "Starting new dungeon run...")
    yield

def apply_widget_policy_step() -> Generator:
    bot.Multibox.ApplyWidgetPolicy(
        enable_widgets=WIDGETS_TO_ENABLE,
        disable_widgets=WIDGETS_TO_DISABLE,
        apply_local=True,
    )
    yield from _disable_widgets_on_alts_only(_ALT_ONLY_DISABLE_WIDGETS)
    yield

# --- Settings and Bot UI Helpers ---

def _draw_difficulty_setting() -> None:
    global _use_hard_mode

    _ensure_ini_initialized()
    new_hard_mode = PyImGui.checkbox("Hard Mode (HM)", _use_hard_mode)
    if new_hard_mode != _use_hard_mode:
        _use_hard_mode = new_hard_mode
        _save_settings()

def _draw_district_setting() -> None:
    global _randomize_district

    _ensure_ini_initialized()
    new_val = PyImGui.checkbox("Randomize EU District", _randomize_district)
    if new_val != _randomize_district:
        _randomize_district = new_val
        _save_settings()

def _draw_merchant_settings() -> None:
    global _merchant_enabled, _merchant_id_kits_target, _merchant_salvage_kits_target, _inventory_slots_threshold, _merchant_store_consumable_materials, _merchant_sell_materials, _merchant_sell_rare_mats, _merchant_buy_ectos, _merchant_ecto_threshold, _merchant_alt_wait_ms

    _ensure_ini_initialized()

    PyImGui.separator()
    PyImGui.text("Merchant (Guild Hall) — runs once on startup")
    PyImGui.separator()

    new_enabled = PyImGui.checkbox("Restock kits / sell materials on startup", _merchant_enabled)
    if new_enabled != _merchant_enabled:
        _merchant_enabled = new_enabled
        _save_settings()

    if _merchant_enabled:
        PyImGui.push_item_width(100)
        new_id = PyImGui.input_int("ID Kits target##bds_id", _merchant_id_kits_target)
        if new_id != _merchant_id_kits_target:
            _merchant_id_kits_target = max(0, new_id)
            _save_settings()

        new_sal = PyImGui.input_int("Salvage Kits target##bds_sal", _merchant_salvage_kits_target)
        if new_sal != _merchant_salvage_kits_target:
            _merchant_salvage_kits_target = max(0, new_sal)
            _save_settings()

        new_inv = PyImGui.input_int("Free Inventory Slots target##bds_inv_thresh", _inventory_slots_threshold)
        if new_inv != _inventory_slots_threshold:
            _inventory_slots_threshold = max(0, new_inv)
            _save_settings()
        PyImGui.pop_item_width()

        new_sell = PyImGui.checkbox("Sell common materials##bds_sell", _merchant_sell_materials)
        if new_sell != _merchant_sell_materials:
            _merchant_sell_materials = new_sell
            _save_settings()

        new_store = PyImGui.checkbox(
            "Store consumable materials (Dust/Iron/Feather/Bone/Fiber)##bds_store_cons_mats",
            _merchant_store_consumable_materials,
        )
        if new_store != _merchant_store_consumable_materials:
            _merchant_store_consumable_materials = new_store
            _save_settings()

        new_rare = PyImGui.checkbox("Sell Diamond & Onyx to Rare Material Trader##bds_rare_mats", _merchant_sell_rare_mats)
        if new_rare != _merchant_sell_rare_mats:
            _merchant_sell_rare_mats = new_rare
            _save_settings()

        new_ectos = PyImGui.checkbox("Buy Glob of Ectoplasm when storage over threshold##bds_ectos", _merchant_buy_ectos)
        if new_ectos != _merchant_buy_ectos:
            _merchant_buy_ectos = new_ectos
            _save_settings()

        if _merchant_buy_ectos:
            new_thresh = PyImGui.input_int("Storage threshold (gold)##bds_ecto_thresh", _merchant_ecto_threshold)
            if new_thresh != _merchant_ecto_threshold:
                _merchant_ecto_threshold = max(0, new_thresh)
                _save_settings()

        PyImGui.push_item_width(100)
        new_wait = PyImGui.input_int("Alt settle wait (ms)##bds_alt_wait", _merchant_alt_wait_ms)
        if new_wait != _merchant_alt_wait_ms:
            _merchant_alt_wait_ms = max(0, min(_MAX_ALT_SETTLE_WAIT_MS, new_wait))
            _save_settings()
        PyImGui.pop_item_width()
        PyImGui.same_line(0, 6)
        PyImGui.text("(time given to alts to reach NPCs and finish)")



def _draw_bds_settings() -> None:
    PyImGui.text("BDS Settings")
    PyImGui.separator()
    _draw_difficulty_setting()
    _draw_district_setting()
    _draw_merchant_settings()

# ==================== INITIALIZATION ====================

bot.SetMainRoutine(farm_bds_routine)
bot.UI.override_draw_config(_draw_bds_settings)


# ==================== UI AND ENTRYPOINT ====================

def _draw_bds_window_with_stats_tab() -> None:
    from Py4GWCoreLib import ImGui, IniManager, Routines

    main_child_dimensions = (500, 350)
    iconwidth = 96

    if not bot.config.ini_key_initialized:
        bot.config.ini_key = IniManager().ensure_key(
            f"BottingClass/bot_{bot.config.bot_name}",
            f"bot_{bot.config.bot_name}.ini",
        )
        IniManager().load_once(bot.config.ini_key)
        bot.config.ini_key_initialized = True
        _ensure_ini_initialized()

    if not bot.config.ini_key:
        return

    if ImGui.Begin(
        ini_key=bot.config.ini_key,
        name=bot.config.bot_name,
        p_open=True,
        flags=PyImGui.WindowFlags.AlwaysAutoResize,
    ):
        if PyImGui.begin_tab_bar(bot.config.bot_name + "_tabs"):
            if PyImGui.begin_tab_item("Main"):
                if PyImGui.begin_child(f"{bot.config.bot_name} - Main", main_child_dimensions, True, PyImGui.WindowFlags.NoFlag):
                    bot.UI._draw_main_child(main_child_dimensions, TEXTURE, iconwidth)
                    PyImGui.end_child()
                PyImGui.end_tab_item()

            if PyImGui.begin_tab_item("Navigation"):
                PyImGui.text("Jump to step (filtered by step index):")
                bot.UI._draw_fsm_jump_button()
                PyImGui.separator()
                bot.UI.draw_fsm_tree_selector_ranged(child_size=main_child_dimensions)
                PyImGui.end_tab_item()

            if PyImGui.begin_tab_item("Settings"):
                bot.UI._draw_settings_child()
                PyImGui.end_tab_item()

            if PyImGui.begin_tab_item("Help"):
                bot.UI._draw_help_child()
                PyImGui.end_tab_item()

            if PyImGui.begin_tab_item("Debug"):
                bot.UI.draw_debug_window()
                PyImGui.end_tab_item()

            if PyImGui.begin_tab_item("Statistics"):
                _draw_bds_stats()
                PyImGui.end_tab_item()

            PyImGui.end_tab_bar()

    ImGui.End(bot.config.ini_key)

    if Routines.Checks.Map.MapValid():
        bot.UI.DrawPath(
            bot.config.config_properties.follow_path_color.get("value"),
            bot.config.config_properties.use_occlusion.is_active(),
            bot.config.config_properties.snap_to_ground_segments.get("value"),
            bot.config.config_properties.floor_offset.get("value"),
        )

def tooltip() -> None:
    from Py4GWCoreLib import ImGui, Color
    PyImGui.begin_tooltip()

    # Title
    title_color = Color(255, 200, 100, 255)
    ImGui.push_font("Regular", 20)
    PyImGui.text_colored("Bone Dragon Staff Farmer bot", title_color.to_tuple_normalized())
    ImGui.pop_font()
    PyImGui.spacing()
    PyImGui.separator()
    # Description
    PyImGui.text("multi-account bot to farm Bone Dragon Staff")
    PyImGui.spacing()
    PyImGui.bullet_text("Requirements:")
    PyImGui.bullet_text("- Any number of accounts, but for best performance, 8 well-geared accounts is recommended")
    PyImGui.bullet_text("- Custom Behavior widget enabled on all accounts")
    PyImGui.bullet_text("- Launch the script on the party leader only")
    PyImGui.bullet_text("Designed for Normal Mode (NM) and Hard Mode (HM), check bot settings for more details.")
    
    # Credits
    PyImGui.text_colored("Credits:", title_color.to_tuple_normalized())
    PyImGui.bullet_text("Developed by Oo SKY oO")
    PyImGui.bullet_text("Contributors: Wick-Divinus, Sloppynacho, XLeek, Yods, Le Z, NotNobu")
    PyImGui.end_tooltip()

def main() -> None:
    bot.Update()
    _write_settings()
    draw_window_sig = inspect.signature(bot.UI.draw_window)
    if "extra_tabs" in draw_window_sig.parameters:
        bot.UI.draw_window(
            icon_path=TEXTURE,
            main_child_dimensions=(500, 350),
            extra_tabs=[("Statistics", _draw_bds_stats)],
        )
    else:
        _draw_bds_window_with_stats_tab()


if __name__ == "__main__":
    main()
