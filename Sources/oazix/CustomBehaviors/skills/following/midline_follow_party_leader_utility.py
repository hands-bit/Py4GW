from typing import override

from Py4GWCoreLib import Agent, Player, Range
from Py4GWCoreLib.Py4GWcorelib import LootConfig, Utils
from Sources.oazix.CustomBehaviors.primitives.behavior_state import BehaviorState
from Sources.oazix.CustomBehaviors.primitives.helpers import custom_behavior_helpers
from Sources.oazix.CustomBehaviors.primitives.parties.memory_cache_manager import MemoryCacheManager
from Sources.oazix.CustomBehaviors.primitives.scores.comon_score import CommonScore
from Sources.oazix.CustomBehaviors.primitives.skills.custom_skill import CustomSkill
from Sources.oazix.CustomBehaviors.skills.following.follow_party_leader_utility import FollowPartyLeaderUtility


class MidlineFollowPartyLeaderUtility(FollowPartyLeaderUtility):
    """Midline variant of `FollowPartyLeaderUtility` with distance-tiered score.

    Two problems with the default follow utility for midline support casters:

    1. **Loose threshold** — default lets a hero drift up to ~624u behind the
       leader IN_AGGRO before scoring a movement. Subclass tightens this to
       ~250u so she stays close enough to land shouts and ally-targeted casts.

    2. **Score is always `CommonScore.FOLLOW = 1.0`** — the lowest in the
       entire system. Combat skills (10+) and auto-attack (9.9) always win,
       so a healer who's constantly casting gets preempted from ever moving.
       She catches up only between cast windows, which is "never" on BIP/Xinrae.

    Solution: scale the follow score with distance.
      - Within `MIDLINE_AGGRO_DISTANCE` (250u): score `None` (don't move).
      - Between 250u and `URGENT_DISTANCE` (600u): low score (1.0) — let her cast.
      - Beyond 600u: high score (`FOLLOW_VECTOR_FIELD` = 99.001) — preempt combat,
        catch up. This is the same priority the deprecated SpreadDuringCombat
        utility used for "must move now" behavior.
    """

    MIDLINE_AGGRO_DISTANCE = 225.0   # below this: don't bother moving
    URGENT_DISTANCE = 600.0           # above this: drop combat, catch up
    URGENT_SCORE = CommonScore.FOLLOW_VECTOR_FIELD.value  # 99.001 — beats combat skills

    # Shared cache key with LootUtility — both scans dedupe per eval cycle.
    _LOOT_CACHE_KEY = "filtered_loot_earshot"

    @override
    def _get_max_distance_to_party_leader(self, current_state: BehaviorState) -> float:
        if current_state == BehaviorState.IN_AGGRO:
            return self.MIDLINE_AGGRO_DISTANCE
        return super()._get_max_distance_to_party_leader(current_state)

    @override
    def _evaluate(self, current_state: BehaviorState, previously_attempted_skills: list[CustomSkill]) -> float | None:
        # Re-implement the parent's evaluate logic so we can return a distance-tiered score
        # instead of the static `CommonScore.FOLLOW.value` (1.0) the parent returns.
        if self.allowed_states is not None and current_state not in self.allowed_states:
            return None
        if custom_behavior_helpers.CustomBehaviorHelperParty.is_party_leader():
            return None

        try:
            party_leader_id = custom_behavior_helpers.CustomBehaviorHelperParty.get_party_leader_id()
            leader_pos = Agent.GetXY(party_leader_id)
            my_pos = Player.GetXY()
            distance = Utils.Distance(leader_pos, my_pos)
        except Exception:
            return None

        max_distance = self._get_max_distance_to_party_leader(current_state)
        if distance <= max_distance:
            return None  # Close enough — don't preempt anything

        # Far enough that we want to move. Decide priority:
        if distance >= self.URGENT_DISTANCE:
            # Lagged badly — preempt combat to catch up. This score (99.001) beats
            # all combat skills (10–98 range) so she'll abandon a heal mid-cast.
            #
            # Exception: if we're NOT in active combat AND there's a valid loot
            # pickup in Earshot, yield so LootUtility (score 1.100) can run.
            # Without this yield, URGENT_SCORE (99.001) preempts LOOT and the
            # alt ignores drops while rushing back to the torch runner. LootUtility itself
            # only runs in CLOSE_TO_AGGRO / FAR_FROM_AGGRO states, so we match
            # that gate here — in IN_AGGRO we still urgent-catchup normally.
            if current_state != BehaviorState.IN_AGGRO:
                try:
                    loot_items = MemoryCacheManager.get_or_set(
                        self._LOOT_CACHE_KEY,
                        lambda: LootConfig().GetfilteredLootArray(Range.Earshot.value, multibox_loot=True)
                    )
                    if loot_items:
                        return None  # yield — LootUtility will win on score
                except Exception:
                    pass  # if the loot scan errors, fall through to urgent catchup
            return self.URGENT_SCORE

        # Drifting (250–600u): low-priority follow. Yields to combat / heal casts
        # but fires the moment there's a gap in the rotation.
        return self.score_definition.get_score()
