# SoO Bot — Work Context

> **For a new Claude session:** read this file first, then `git log --oneline -30`. The most recent decisions are at the top of each section. Code is the source of truth — this file captures intent and the *why*.

---

## Overview

This branch (`Hands`, local-only, never pushed) hosts the operator's **Shards of Orr (SoO)** multibox dungeon-farming bot for Guild Wars 1, built on the Py4GW framework. The party leader (the torch runner) runs a torch-and-brazier route while 7 alts follow and clear combat. The repo is a fork of the upstream Py4GW project; our work lives in:

- **`Widgets/Automation/Bots/Missions/Dungeons/SoOFinal.py`** — the main bot script. State-machine driven, manages the full L1→L2→L3→Fendi→chest→reset loop. Sister scripts `SoOHeroAI.py` and `SoOTesting.py` are experimental variants (CustomBehaviors-engine vs. HeroAI-engine).
- **`Sources/oazix/CustomBehaviors/`** — per-skill utility classes, skillbar wirings, and follow-behavior overrides for the alts.

Per-character details (emails, character names) are loaded from `Widgets/Config/Shards of Orr Final.ini` (NOT tracked) under `[Accounts]` and `[Character Names]` sections. The committed source contains zero personal identifiers.

---

## Active Workstreams

- **Clear-time optimization for personal record** — sub-stage checkpoints instrumented on L3 approach and arena walk. PR definition: **time until Soul of Fendi's last phase dies** (not `run_time` — that includes ~20s stable-verify overhead). Measured run_time overstates PR-equivalent by ~15-20s. Best run so far: 26:17. Median: 29:20. Target: sub-26:00.
- **RoJ gate stabilization (BIP Lever C applied, tagged `stable-bip-lever-c`)** — multi-round tuning landed this session. Gate now reliably protects RoJ while letting supporting skills through. Key fixes: BIP uint32 sentinel sanitize, EVAS-hold affordability, EVAS-hold target validation, `max(natural, 93)` evas_hold return, Gate A tolerance=2. Verified across several Fendi fights.
- **BIP necro tuned for SoO smite support (Levers A+B+C)** — explicit overrides in `necromancer_bip_restoration.py`: `sacrifice_life_limit_percent=0.35`, `required_target_mana_lower_than_percent=0.50`, `score=55`. Moved BIP from ~3% uptime to ~20% on the Fendi fight. Still room to grow but no longer the binding constraint.
- **Fendi-zone hardcoded target override** — local check in each alt's `_handle()` short-circuits the pin mechanism in the Fendi arena, forcing target to Soul of Fendi (7065) or Fendi Nin (7064). Bypasses IPC lag and 500ms pin update cycle. Responds to phase transitions in ~50ms.
- **Arena-walk variance source (unverified theory)** — `_reenable_party_member_behind` re-enables the `OnPartyMemberBehind` callback right before the Kill-Brigant waypoints. the torch runner's movement pauses at each waypoint when alts straggle behind. Need more sub-stage checkpoint data to confirm.
- **Finish Him! still firing on non-Fendi targets in the Fendi room** — flagged for next pass. Suspected causes: predicate not being applied, AutoCombat bypass, or hero positioned outside the 3500u Fendi anchor zone.
- **BDS drop miscount for the torch runner** — spawned as a separate task earlier. User observed a Bone Dragon Staff drop that wasn't logged in the Statistics tab; likely a "picks up" vs "You pick up" chat parser gap.

---

## Recent Decisions & Rationale

### 2026-04-21 (this session — multi-day tuning session)

#### BIP necro tuning for SoO smite-monk support (tagged `stable-bip-lever-c`)
**Problem**: Fendi-fight log analysis showed <10% BIP uptime across smite monks despite BIP necro being alive. Monks were energy-starved, force-firing RoJ at E=10 (gate's floor) every cycle with nothing else able to fire.

**Three levers applied to `necromancer_bip_restoration.py` BIP instantiation, all scoped to this skillbar only (other BIP users — dark_aura_support, minion_master — keep defaults):**
- **Lever A**: `sacrifice_life_limit_percent 0.55 → 0.35`. Necro can BIP at 68% HP (was 88%). Tolerates combat chip damage.
- **Lever B**: `required_target_mana_lower_than_percent 0.40 → 0.50`. Fires at monks below 50% energy (was 40%).
- **Lever C**: `score 33 → 55`. Beats low-urgency heals (Spirit Light/PWK/MBAS/Soothing Memories all use ScorePerHealthGravity which scales with damage, typically 30-60 in active combat). Still yields to emergency heals (HP < 50% → heal scores 60-80+).

**Verified effect**: ~2x BIP cast rate, force-fire median E 14 → 37. No party deaths during Lever-C-on Fendi fight.

**Important caveat about BloodIsPowerUtility persisted config**: the utility reads via `PersistenceLocator().skills.read_or_default(...)`. If previous "Save for account" actions persisted old values, they override code defaults. Check debug UI and click "Delete" to clear persisted config if old values appear.

#### Fendi-zone hardcoded target override (framework-level)
- Added to `Sources/oazix/CustomBehaviors/primitives/parties/custom_behavior_party.py`, alt branch of `_handle()`.
- When alt is on L3 map (583) within 3500u of Fendi anchor (-16022.9, 17889.9), local scan picks Soul of Fendi (7065) > Fendi Nin (7064), short-circuits the generic pin logic.
- Why: the shared-memory pin had 500ms+ update cycles (the torch runner's `resolve_fendi_fight` loop), causing alts to drift during Fendi Nin ↔ Soul transitions. Local scan responds in 50ms.
- Generic pin logic still runs for non-Fendi scenarios (trash focus-fire, torch-split strict-flank).

#### Tight target re-pin (framework-level, default-on)
- Removed the 150ms inner throttler on alt re-pinning in `_handle()`. Re-pin now runs every `act()` iteration (50ms).
- Removed the `IsCasting` guard — `ChangeTarget` during cast is safe in GW1 (cast completes on original target).
- Previously opt-in via `enable_tight_target_pin(True)` flag; removed the flag because it was process-local and didn't propagate to alts.
- Net effect: post-utility-cast auto-attack snaps back to pinned target within 50ms (was up to 500ms).

#### RoJ gate v2 — post-v1 tuning pass
Multiple fixes after verification runs showed issues with the original gate:

1. **BIP uint32 sentinel sanitize** (`monk_smite.py`): `Effects.GetEffectTimeRemaining` returns max uint32 (~4.29e9 ms) when effect inactive. Without sanitization, spend formula treated billions of seconds of regen as available, making Gate A permissive during "no BIP" windows. Fix: `if bip_ms < 0 or bip_ms > 15000: bip_ms = 0`.

2. **EVAS-hold affordability fix**: Gate B's evas_hold originally triggered when `E >= EVAS_COST = 10`. But Gate A blocks EVAS at `E < EVAS_COST + ROJ_COST = 20`, creating deadlock at E=10–19: RoJ held for EVAS, EVAS blocked, nothing progressed. Fix: evas_hold threshold → `E >= 20`.

3. **EVAS-hold target validation**: Changed from `_get_targets()` existence check to calling `EVAS._evaluate()` directly. Catches lock-manager conflicts and other mode-specific gates. If EVAS can't actually fire, force-fire RoJ.

4. **`max(natural, 93.0)` in evas_hold**: During hold, RoJ returned natural score (65 for single target, 80 pair, 92 cluster). CoF at 91 beat RoJ at 65 → "RoJ passed over" on low-density. Fix: return max(natural, 93) so RoJ beats all non-EVAS skills during hold.

5. **Gate A tolerance=2**: Changed `if cost > spend` to `if cost - spend > _GATE_A_TOLERANCE = 2`. Wire-thin blocks (like Finish Him at spend=8.6) now pass; deep starvation cases still block. Trade ~0.4s RoJ delay for more opportunistic skill fires.

#### Consumable reliability fix (reverted earlier optimization)
- Originally tried moving consumables upfront on L2/L3 (match L1 pattern). Saved ~2s per run but caused alts to miss the first consumable (Celerity) during map-load windows — alts were still loading when the SharedCommandType.PCon broadcast landed.
- **Reverted L2 and L3 to post-blessing consumables** (original proven pattern). L1 untouched because it has natural buffer from pre-run setup states.
- Also added Candy Corn (+1 all attributes) to all 4 consumable points (Arbor Bay, L1, L2, L3).

#### RoJ spread-fire (Ray of Judgment ylawd combo)
- `ray_of_judgment_ymlawd_combo_utility.py`: each smite monk picks `enemies[login_number % len(enemies)]` instead of `enemies[0]`.
- Single-enemy case (Fendi): all heroes → enemies[0] → focus-fire preserved.
- Multi-enemy case: heroes spread across top-N density-sorted targets → more AoE coverage per salvo instead of 4-way overlap wasting damage.
- Rationale: operator noted one RoJ generally clears its ~200u radius, so overlap is overkill that could cover outliers instead.

#### Midline follow loot yield
- `midline_follow_party_leader_utility.py`: urgent catchup (99.001) now yields to loot if nearby loot exists and alt is NOT IN_AGGRO.
- Uses shared `LootUtility` cache key `filtered_loot_earshot` for deduplication.
- Why: urgent follow was preempting LOOT (score 1.1) when midline necros lagged past 600u from the torch runner, causing drops to be missed.

#### Blessing flow cleanup
- Removed redundant `Move.XY` before `XYAndInteractNPC` on all 3 levels.
- Removed L3's post-dialog settle wait (L1 works without it).
- Removed two redundant `Move.XY` on L2 (between OOC wait and interact).
- Net save: ~3-5s per run.

#### P7-P15 OoC wait tightening + combat poll interval
- `_party_waypoint_manager` in SoOFinal.py: `OOC_REQUIRED_CONSECUTIVE = 1 if wp_idx >= 7 else 2` (was always 2). Cuts post-waypoint OoC verify by 500ms for P7-P15.
- `OOC_POLL_INTERVAL_S = 0.25` (was 0.5). `COMBAT_CONFIRM_POLLS = 10` (was 5) to preserve 2.5s sustained-stance confirmation threshold.
- Net save: ~3-5s per run.

#### B7/B8/B9 staged party-combat wait (the torch runner brazier run)
- At each of B7, B8, B9 during L3 torch brazier sequence, after lighting brazier, check if main party is in combat. If yes:
  - **B7**: wait up to 5s, early-exit when party clears combat
  - **B8**: wait up to 5s
  - **B9**: wait indefinitely until party clears
- After wait, re-light the current brazier to refresh the torch buff before moving to next. Prevents the torch runner from outrunning the alts during heavy combat moments near the end of the brazier circuit.

### 2026-04-20 (earlier session — RoJ gate v1 stabilization)

#### RoJ energy-reservation gate (stable, tagged `stable-roj-gate-v1`)
- Lives in `Sources/oazix/CustomBehaviors/skillbars/monk_smite.py`. Two-part gate:
  - **Gate A (per-skill reservation):** Blocks any utility whose cost would leave RoJ under-funded given future regen across its recharge window. BiP-aware via `Effects.GetEffectTimeRemaining("Blood_is_Power")`. Formula: `spendable = E + regen_over_T - ROJ_COST`. Applied by monkey-patching `_evaluate` on every utility whose skill_id matches the cost map.
  - **Gate B (RoJ force-fire):** Scores RoJ at 99 when ready+payable, yielding to EVAS when EVAS is also ready+payable (engage priority).
  - **Critical fix:** Gate A does NOT short-circuit when `T_ms == 0`. The reservation must stay in force continuously so instant-cast skills (YMLAD, signets) can't race RoJ's 1s cast window and drain the 10e reservation.
- Installed via `get_skills_final_list` override so it catches both explicitly-wired utilities AND framework-generated AutoCombat/generic wrappers (covers Air of Superiority without an explicit utility).
- Wired 5 previously-unwired physical-bar utilities explicitly: Mistrust, Overload (interrupt-only, hex-spread neutered), Judge's Insight, EBSOW, Great Dwarf Weapon.
- Overload scoring: interrupt=75, hex-spread disabled via `utility.get_hex_spread_targets = lambda: []`.
- Mistrust 76/40 density-tiered, Finish Him 74. Tight 76→75→74 stack by operator preference.
- Logging: dual (console + `Py4GW/logs/roj_gate.log`). Dedup by (channel, state) suppresses repeat same-state decisions within 2s. Transitions always log.
- **Verified across 43 clean runs**: 0 gate violations (min E at RoJ force-fire = 10 exactly, n=3921 casts).

#### Fendi Phase Monitor (new background coroutine)
- `_fendi_phase_monitor` in SoOFinal.py, started from `_mark_l3_start`, self-exits on leaving L3 map.
- Watches agent array every 500ms for Fendi Nin (model 7064) / Soul of Fendi (model 7065) transitions. Logs phase enter/exit as `FENDI_MONITOR_PHASE` events (distinct from in-state `FENDI_PHASE` events) + first-sight checkpoints + a summary at L3 exit.
- Runs only on `TORCH_RUNNER_EMAIL`; silent no-op on other accounts.
- **Why:** `resolve_fendi_fight` only tracks phases while it's the active FSM state. If the party kills Fendi before the torch runner enters that state (the "fly-by" pattern), phase counts stay zero and we lose all fight analytics. Baseline data: 11/46 completed runs matched this pattern. With fast builds (Esurge) it'd be even worse. Moving phase tracking upstream to an independent coroutine decouples analytics from FSM timing.

#### Sub-stage instrumentation (telemetry only, zero gameplay impact)
- Added 8 pure-telemetry checkpoints slicing the two highest-variance stages:
  - **L3 approach** (L3_START → TORCH_SPLIT_START, 45s best-to-median gap): `L3_APPROACH_PATH_START`, `L3_APPROACH_PATH_END`, `L3_APPROACH_OOC_DONE`. Measures path-walk vs OoC-wait sub-stages.
  - **Arena walk** (TORCH_SPLIT_END → FENDI_FIGHT_START, **88s best-to-median gap — largest single opportunity**): `ARENA_REJOIN_DONE`, `ARENA_BRIGANT_DONE`, `ARENA_DOOR_APPROACH`, `ARENA_DOOR_DONE`, `ARENA_PATH_END`. Measures rejoin, Brigant path, door interaction, and path_bds traversal separately.
- Implementation: named single-line generator functions (`_cp_*`) each doing `_log_checkpoint + yield`. Wired into the FSM as custom states between existing moves.
- Needs ~10-20 runs of new data, then re-analyze to identify the specific slow sub-stage.

#### Esurge test (reverted)
- Swapped 3-4 monk smite heroes for Esurge mesmer heroes for comparison.
- Added `_dash_caster` background coroutine for the torch runner's new Dash skill (Python-level cast because CustomBehaviors is disabled on the runner during the torch split).
- Ran 1 test; operator didn't like the style, reverted team build to RoJ.
- **Dash caster and its hooks were ripped out** — SoOFinal.py is back to the RoJ build shape.
- Baseline data preserved at `logs/roj_build_baseline.txt`: 43 clean runs, medians recorded for later comparisons.

#### Overnight RoJ stability verification
- 55 total runs (46 completed, 3 outliers flagged, 0 errors on completed).
- First-10-runs avg 1822s → Last-10-runs avg 1738s = **1:24 improvement** per run, directly attributable to the session's tuning work (gate, YMLAD race fix, Overload tune, FinishHim Fendi predicate, brazier buff-refresh early-exit).
- Worst outlier: 2026-04-17 05:22:10 — L2 door failed 4x retries, bot spent 4h9m in a stuck loop before crashing out. Same bug class as the L1 door retry we already fixed; needs mirroring to L2 (task spawned).

#### Misc housekeeping
- `logs/soo/acc_pain.log` (6.9 MB stale debug) deleted.
- BDS drop miscount on the torch runner's Bone Dragon Staff drop: task spawned to investigate. Chat parser may be matching "picks up" (third-person) but not "You pick up" (first-person-only for local player).

---

### 2026-04-19 (previous session)

#### Brazier sequence early-exit (perf)
- **What:** Modified `_interact_brazier` to exit its 4-attempt loop as soon as the torch-buff `time_remaining` jumps up by >1000ms (proof of refresh). Outer `_brazier_sequence_gen` skips its post-interact `wait(500)` when the inner loop confirmed refresh.
- **Why:** Each brazier visit was statically dwelling ~2.95s regardless of whether the first interact lit it. Estimated savings: ~2.15s × 18 braziers/run × 90 runs/day = ~57min/day.
- **Mechanic note:** the torch runner arrives at the next brazier *with* the buff already active (carried from previous brazier). `HasEffect` alone returns true regardless. Refresh detection requires the time-remaining jump (~+20s on a successful interact vs. natural ~−550ms decay during a non-refresh attempt). Threshold of +1000ms is unambiguous.
- **Failure path is unchanged:** if interact 1 fails, attempts 2/3/4 still run as before.

#### Branch creation: `Hands`
- New local-only branch sanitized of all operator identifiers. Hardcoded character names (`PORTAL_RIDER_CHARACTER_NAME`, `_SUMMONER_CHARACTER_NAME`, `COMBAT_DETECTION_EXCLUDE_NAMES`) refactored to load from the existing `_settings_ini` `[Character Names]` section. INI is excluded from commits.
- **Privacy rule:** never `git push` from this branch. Backup via repo-folder copy, not git remote.

#### Midline follow utility (positioning)
- Created `Sources/oazix/CustomBehaviors/skills/following/midline_follow_party_leader_utility.py`. Subclass of `FollowPartyLeaderUtility` with two changes:
  1. **Tighter leash**: `MIDLINE_AGGRO_DISTANCE = 225.0` (default ~624u). She moves back to formation when she's drifted >225u from leader.
  2. **Distance-tiered score**: 250–600u = low score (1.0, yields to combat); >600u = `FOLLOW_VECTOR_FIELD` value (99.001, **preempts combat skills**) so she can catch up when severely lagged.
- Wired into `necromancer_bip_restoration.py` and `necromancer_xinrae_restoration.py` via `additional_autonomous_skills` override that:
  - Filters out the default `FollowPartyLeaderUtility`
  - Filters out `SpreadDuringCombatUtility` entirely (its vector field was shoving support casters back to the rear once leader-attraction disengaged at close range)
  - Appends our `MidlineFollowPartyLeaderUtility` instance
- **Operator-rejected alternatives:** different anchor than the torch runner (operator: "the torch runner is the perfect anchor — she's first to encounter combat"), bumping YMLAD score higher (operator wants positioning fix, not score fix).

#### YMLAD on BIP necro (range-gated)
- Added YMLAD via `RawSimpleAttackUtility` with a **custom Earshot-distance predicate** (`Range.Earshot.value × 0.9 = ~910u`) instead of the utility's default `Range.Spellcast = 1248u`. Without this gate, the utility was scoring on targets in the 1012–1248u band where YMLAD-the-shout couldn't actually land → cast fail → re-evaluate → loop, locking the skillbar.

#### Finish Him! wiring
- `FinishHimUtility` registered on `monk_smite.py` (was imported but never instantiated → AutoCombat fallback was firing it without HP gating).
- Added optional `custom_agent_targeting_predicate` parameter to `FinishHimUtility.__init__`. The monk_smite registration passes the same `roj_predicate` used for RoJ — in the Fendi anchor zone (3500u radius around `-16022.9, 17889.9` on L3), targeting is restricted to model IDs 7064 (Fendi Nin) and 7065 (Soul of Fendi).
- **Watchlist:** operator reports it's still firing on non-Fendi targets in the Fendi room. Investigation pending.

#### L1 dungeon-door retry hardening
- Added `_l1_door_interact_with_retry()` in SoOFinal.py. Wraps the previously-bare `bot.Move.XYAndInteractGadget(15100, 5443)` step with: bounded OOC wait → re-Move to door coords → try `bot.Move._coro_xy_and_interact_gadget` in `try/except`, up to 3 attempts.
- **Why:** observed run 93 crash where InteractWithAgentXY timed out, framework's "On Unmanaged Fail" caught it, bot.Stop'd. Root cause was the torch runner on fire / combat shoving her out of interact range. Same defense-in-depth pattern as `_l2_transit_with_retry`.

#### L3 torch path tuning
- **Removed P10** (`-6838.3, 3495.2`). Path now goes P9 → P11 directly. Total `path2_part2` is now 15 waypoints. No indexed-waypoint constants needed updating (STRICT_FLANK={5,6}, CALL_TARGET={4,7,8}, CLEAR_PARTY_TARGET=8 all ≤9).
- **SAFE_WAYPOINTS = {1, 2, 3}**: at these guaranteed-enemy-free waypoints, the per-waypoint OOC verification loop is skipped (saves ~1–2s polling per waypoint). Leader-arrival wait kept (still need physical traversal). Logged as `WP_OOC_SKIPPED_SAFE` checkpoint.

#### Shandra handler stability fixes
- **Critical bug fix in `wait_for_map_change`** (line ~5328): the function was `yield; return` (no value) so `ok = yield from wait_for_map_change(...)` always got `None`. The Shandra inside-dungeon handler's `if not ok: continue` always tripped, even on successful map changes. Run 91 hit `[HARD STOP] Shandra quest never became active after 3 attempts` after a successfully-accepted quest. Fix: `return True/False` instead.
- **Defensive final state check** in `_check_shandra_inside_dungeon`: after the for-loop exits, recheck `_shandra_quest_state()`; only call `bot.Stop()` if still not active. Catches any other timing race.
- **Operator-rejected fix:** "Fix 3" (consume both reward+accept dialogs in one Shandra visit) — GW enforces a rezone between reward dismiss and quest re-accept, so it's mechanically impossible.

#### Logging cleanup
- Removed `_acc_pain_log` file-write spam (~7MB log file generating per session).
- Removed AccPain cast monitor (`_accpain_cast_monitor`, `_accpain_log_file`, `_start_accpain_cast_monitor`) from SoOFinal.py — was flooding GW chat system with `[AccPain/MON]` `print()` calls that triggered "Invalid text fixup" errors.
- AccumulatedPainUtility now silent — gating logic intact (`>=2 hexes`).
- Operator removed Accumulated Pain from skillbar after this cleanup.

---

## Known Issues / Watchlist

- **Finish Him! firing on non-boss targets in Fendi room** (see Recent Decisions). Need to verify the `roj_predicate` is being evaluated on the utility path, not bypassed by AutoCombat fallback.
- **`Py4GW_injection_log.txt`** has an unmerged (`UU`) state at repo root from some prior merge. Untracked log file but blocks operations until resolved. Currently `git rm --cached`'d on `Hands` branch.
- **L3 brazier 8→9 dwell occasionally too long**: in run with the 1822s outlier (12:16 run), the torch runner moved too far from brazier 8 between light 8 and light 9, buff expired mid-travel, had to relight 8 twice — cost ~90s. Recovery worked, but tightening that dwell would trim L3 tail.
- **Native gw.exe crashes at "Apply widget policy" startup step** happened once today, resolved by reboot. Cause unknown, possibly OS-level socket/driver state. If it recurs: try `SoO.py` (the stable sibling) on same character to discriminate environmental vs. SoOFinal-specific.

---

## Operator Setup Notes (Local INI)

These keys are required in `Widgets/Config/Shards of Orr Final.ini` (NOT tracked) for the bot to function:

```ini
[Accounts]
torch_runner_email = <torch runner's account email>
<character_name_underscored> = <email>   # one entry per alt account

[Character Names]
<email_with_underscores_and_at_replacement> = <character name>   # one per email
portal_rider                 = <name of portal-rider character>
summoner                     = <name of Tengu Support Flare summoner>
combat_detection_excludes    = name1, name2, name3, name4   # support classes whose maintained item spells keep them in combat stance permanently
```

---

## File Map

### Bot scripts
- `Widgets/Automation/Bots/Missions/Dungeons/SoOFinal.py` — main bot (5700+ lines).
- `Widgets/Automation/Bots/Missions/Dungeons/SoOHeroAI.py` — HeroAI-engine variant.
- `Widgets/Automation/Bots/Missions/Dungeons/SoOTesting.py` — testbed.
- `Widgets/Automation/Bots/Missions/Dungeons/SoO.py` — stable reference (untouched, fallback if `SoOFinal.py` breaks).

### Map visualizations (dev tooling)
- `Widgets/Automation/Bots/Missions/Dungeons/l1_map.html`, `l2_map.html`, `l3_map.html`, `l1_overlay.html`, `l1_flank_preview.html`, `l3_flank_compare.html`, `torch_split_map.html`, `soo_l1_map.jpg`. Served on localhost:8765 for path tuning.

### Custom skill utilities (CustomBehaviors framework)
- `Sources/oazix/CustomBehaviors/skills/common/finish_him_utility.py` — execute shout w/ optional predicate.
- `Sources/oazix/CustomBehaviors/skills/mesmer/accumulated_pain_utility.py` — 2+ hex Deep Wound proc.
- `Sources/oazix/CustomBehaviors/skills/mesmer/{cry_of_frustration,overload,deep_freeze_snare,fragility}_utility.py`.
- `Sources/oazix/CustomBehaviors/skills/monk/{ray_of_judgment_ymlawd_combo,ymlad_standalone}_utility.py`.
- `Sources/oazix/CustomBehaviors/skills/common/ebon_vanguard_assassin_support_utility.py`.
- `Sources/oazix/CustomBehaviors/skills/ranger/edge_of_extinction_utility.py`.
- `Sources/oazix/CustomBehaviors/skills/following/midline_follow_party_leader_utility.py` — tighter follow leash + tiered score for support casters.

### Modified skillbars
- `Sources/oazix/CustomBehaviors/skillbars/monk_smite.py` — Smite Monk for SoO. Hosts the RoJ→YMLAD→EVAS combo + Finish Him with Fendi predicate.
- `Sources/oazix/CustomBehaviors/skillbars/necromancer_bip_restoration.py` — BIP necro w/ midline follow + YMLAD (Earshot-gated).
- `Sources/oazix/CustomBehaviors/skillbars/necromancer_xinrae_restoration.py` — Xinrae's Weapon necro w/ midline follow.
- `Sources/oazix/CustomBehaviors/skillbars/{mesmer_esurgery,mesmer_ineptitude,mesmer_keystone,ritualist_soul_twisting}.py`.

### Framework-level edits
- `Sources/oazix/CustomBehaviors/primitives/helpers/custom_behavior_helpers.py`
- `Sources/oazix/CustomBehaviors/primitives/helpers/sortable_agent_data.py`
- `Sources/oazix/CustomBehaviors/primitives/parties/custom_behavior_party.py`
- `Sources/oazix/CustomBehaviors/primitives/skills/custom_skill_utility_base.py`

### Logs (NOT tracked)
- `Widgets/Automation/Bots/Missions/Dungeons/logs/soo/runs_summary.jsonl` — per-run timing + checkpoint events.
- `Widgets/Automation/Bots/Missions/Dungeons/logs/soo/soo_run_*.log`, `console/console_*.log` — per-run detail.

---

## Revert chain (tags, newest first)

Each tag is a proven-working state. Use `git reset --hard <tag>` to roll back to it.

| Tag | Commit | What it captures |
|---|---|---|
| `stable-bip-lever-c` | ef26a60f | **Current** — BIP score 55 + Lever A/B/C applied. Fendi uptime ~20%, median force-fire E=37. |
| `stable-pre-bip-lever-c` | 76f3eb04 | Fendi-zone override + RoJ gate v2 tuning + blessing revert. BIP at 33 (pre-Lever-C). |
| `stable-pre-fendi-zone-override` | db56d949 | After Fendi phase monitor + sub-stage instrumentation. Before Fendi-zone hardcoded target override. |
| `stable-blessing-flow` | 51d3bd63 | After blessing-flow cleanup + Candy Corn. Before RoJ/targeting deep tuning. |
| `stable-roj-gate-v1` | 66b69119 | RoJ gate v1 (pre-v2 tuning). YMLAD race fix. Overload interrupt-only. |
| `stable-pre-roj-gate` | a7cdb064 | Clean monk_smite baseline before any energy gate. |

---

## Quick reference for new sessions

```bash
# Get oriented
git log --oneline Hands -20
cat WORK_CONTEXT.md

# What's modified vs. last commit
git status
git diff

# Find a feature
grep -nE "<keyword>" Widgets/Automation/Bots/Missions/Dungeons/SoOFinal.py

# What's the operator's setup
cat "Widgets/Config/Shards of Orr Final.ini"   # operator-only — INI is gitignored

# Full hard revert to most recent stable
git reset --hard stable-bip-lever-c
```

## Gotchas for future sessions

- **`BloodIsPowerUtility` persists config via `PersistenceLocator`** — code defaults can be silently overridden by saved values from the CustomBehaviors debug UI. After any change to BIP's parameters, check the debug UI on the BIP necro and click "Delete" to clear persisted config if old values appear.
- **Framework changes to `custom_behavior_party.py` affect ALL bots using CustomBehaviors** (Underworld, modular bot, GUI pin). Current fixes are strictly better (tighter focus-fire, Fendi-zone local override) — no known downside — but any future framework edit needs the same cross-bot audit.
- **BIP uptime ceiling is ~25-30% at score 55** — raising further (to 60-70) risks neglecting heals. If monks still feel starved, investigate: is BIP necro taking heavy damage and cycling below 68% HP? Is she getting killed?
- **Consumables on L2/L3 MUST fire after the blessing dialog**, not before. Moving them upfront causes alts to miss Celerity during map-load windows. L1 is fine because of natural pre-run buffer.
- **`run_time` ≠ PR time**. Operator's PR definition: time until Soul of Fendi's last phase dies. `run_time` includes ~20s post-fight stable-verify. When analyzing clear times, subtract ~15-20s from `run_time` for PR-equivalent comparison.
- **Sub-stage checkpoints** (`L3_APPROACH_PATH_*`, `ARENA_*`) are live in `runs_summary.jsonl` from this session. ~10-20 runs of new data needed before targeted optimization of the slow sub-stage.
