# Shards of Orr Final — multibox farming bot

A Py4GW-based bot that farms the **Shards of Orr** dungeon with a coordinated
party of 8 characters: one torch runner (party leader) who handles the L3
brazier/torch sequence, and 7 alts who clear combat along a fixed waypoint path.

Originating script: [`SoOFinal.py`](SoOFinal.py). Framework dependencies live
under `Sources/oazix/CustomBehaviors/` (per-skill utilities, skillbars, and
party primitives) and `Py4GWCoreLib/` (upstream Py4GW — we match the fork's
main branch).

---

## What's in this fork

| Area | What we changed |
|---|---|
| `Widgets/Automation/Bots/Missions/Dungeons/SoOFinal.py` | The main bot. State machine, L1→L2→L3→Fendi→chest→reset loop. |
| `Sources/oazix/CustomBehaviors/skillbars/monk_smite.py` | Smite Monk bar tuned for SoO (RoJ energy-gate, YMLAD race fix, Overload interrupt-only, Finish Him with Fendi predicate). |
| `Sources/oazix/CustomBehaviors/skillbars/necromancer_bip_restoration.py` | BIP necro with midline follow, YMLAD (Earshot-gated), SoO-specific BIP tuning. |
| `Sources/oazix/CustomBehaviors/skillbars/necromancer_xinrae_restoration.py` | Xinrae's Weapon necro with midline follow. |
| `Sources/oazix/CustomBehaviors/skillbars/mesmer_*.py` | Esurge / Ineptitude / Keystone mesmer bars. |
| `Sources/oazix/CustomBehaviors/skillbars/ritualist_soul_twisting.py` | ST ritualist. |
| `Sources/oazix/CustomBehaviors/skills/**` | ~10 per-skill utilities (Finish Him, Overload, YMLAD, RoJ, midline follow, etc.). |
| `Sources/oazix/CustomBehaviors/primitives/parties/custom_behavior_party.py` | Framework: Fendi-zone target override + Fendi-arena spirit filter. |
| `Sources/oazix/CustomBehaviors/primitives/helpers/*.py` | Framework helpers: spirit filter, targeting utilities. |

---

## Setup

### 1. Prerequisites

- Py4GW installed and working on at least 8 GW clients (one per party member).
- CustomBehaviors widget and Multibox widget enabled on all clients.
- All 8 accounts in the same guild (script uses Guild Hall travel for
  restocking).
- Shandra quest chain available on the torch runner's character (SoO entry
  NPC in Arbor Bay).

### 2. Copy the INI template

```bash
cp "Widgets/Config/Shards of Orr Final.ini.example" "Widgets/Config/Shards of Orr Final.ini"
```

Then edit the real `.ini` (which is gitignored — it never gets committed).
Only two sections actually need manual setup: `[Accounts]` and
`[Character Names]`. Everything else is either optional (feature toggles with
sane defaults) or runtime-managed (stats and drop counters the bot writes
on its own).

Minimum viable INI:

```ini
[Accounts]
torch_runner_email = torch@example.com
my_alt_monk = alt1@example.com
my_alt_mesmer = alt2@example.com
# ... one entry per alt ...

[Character Names]
torch_at_example_com = My Torch Runner
alt1_at_example_com  = My Alt Monk
alt2_at_example_com  = My Alt Mesmer
# ... one entry per alt ...

portal_rider              = My Alt Warrior
summoner                  = My Alt Monk
combat_detection_excludes = My Alt Ritualist, My Alt Mesmer
```

See the template for exhaustive documentation of each key.

### 3. First run

1. Launch Py4GW on all 8 clients.
2. On each alt, enable the CustomBehaviors and Multibox widgets.
3. On the torch runner, load `Widgets/Automation/Bots/Missions/Dungeons/SoOFinal.py`
   via the bot selector.
4. The bot assumes the torch runner is parked in Arbor Bay with the Shandra
   quest accepted (or collectable).

---

## Reload ordering (important)

Py4GW caches Python modules in `sys.modules` once loaded. The reload path
depends on what you edited.

| Edit location | Required reload |
|---|---|
| `SoOFinal.py` only | Stop + start the script on the torch runner. No client restart needed. |
| `Sources/oazix/CustomBehaviors/**` (any file under CustomBehaviors) | **GW client restart** on every affected alt. A widget toggle does NOT force a re-import of nested modules. |
| `Widgets/Config/Shards of Orr Final.ini` | Automatic on next run-start; no manual action needed. |
| `Py4GWCoreLib/**` (framework) | GW client restart on every affected client. |

The "widget toggle picks up changes" pattern works only for widgets that Py4GW
explicitly `importlib.reload`s — that's not most files in the CustomBehaviors
tree. When in doubt: restart the client.

---

## Contribution guide

### Branch model

- `main` tracks upstream Py4GW. Rebase/merge periodically.
- `release/soofinal` is the shared SoO line. All SoO-specific work lands here.
- Feature branches: `feat/<short-name>` off `release/soofinal`. Open a PR back
  into `release/soofinal` for review.

### Privacy rules

- **Never commit `Widgets/Config/Shards of Orr Final.ini`** (gitignored, but
  double-check before pushing).
- **Never hardcode account emails or character names in source.** The code
  loads them from the INI at runtime; new code must follow that pattern.
- If you add a new per-account INI key, document it in the
  `Shards of Orr Final.ini.example` template with the same structure
  (commented section explaining what it does).
- Session logs under `Widgets/Automation/Bots/Missions/Dungeons/logs/` are
  gitignored and contain per-run account names — never paste raw log content
  into commit messages, issues, or PR descriptions.

### Code style

- Skillbar edits should be reversible — tag the pre-change state before
  invasive changes (see "Revert chain" in `WORK_CONTEXT.md`).
- When adding instrumentation: use `_log_checkpoint` (one-shot events) or
  `_log_event` (ongoing telemetry). Both write to JSONL for post-mortem.
- Keep per-run log overhead low — the brazier sequence runs at 250ms polling
  and the RoJ gate patches every combat utility. Don't add more polling
  unless measured.

### Testing

- Run a full dungeon cycle end-to-end before merging (~30 minutes).
- Grep the console logs for `ERROR` and your new feature's log prefix after
  the run.
- For skillbar changes, also observe the in-game behavior: energy curves,
  skill firing cadence, and kill times on representative enemy groups.

---

## Background context

See [`WORK_CONTEXT.md`](../../../../WORK_CONTEXT.md) for:
- Active workstreams and open questions.
- Recent decisions with rationale (the _why_ behind current gates/thresholds).
- Known issues and watchlist.
- File map and revert-tag chain for safe rollbacks.
