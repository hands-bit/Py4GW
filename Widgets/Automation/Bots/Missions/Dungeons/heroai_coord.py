"""
HeroAI coordination layer for multibox bot scripts.

Replaces CustomBehaviors party-coordination primitives with HeroAI-native
shared-memory writes. Single-property atomic writes go through
GLOBAL_CACHE.ShMem.SetHeroAIPropertyByEmail to avoid torn-struct reads on
peer clients; read helpers pull straight from AccountStruct.

Public surface (all idempotent, silently no-op on unknown accounts):

    # Mutations
    HeroAICoord.set_combat(email, enabled)
    HeroAICoord.set_following(email, enabled)
    HeroAICoord.set_flag_xy(email, x, y)
    HeroAICoord.clear_flag(email)
    HeroAICoord.freeze(email)                   # combat + following off, unflagged
    HeroAICoord.unfreeze(email)                 # combat + following on, unflagged
    HeroAICoord.broadcast_pixel_stack(targets, x, y)

    # Reads (return None when the account is not yet attached to ShMem)
    HeroAICoord.is_loaded(email)      -> bool
    HeroAICoord.is_dead(email)        -> bool | None
    HeroAICoord.is_in_combat(email)   -> bool | None
    HeroAICoord.get_position(email)   -> tuple[float, float] | None
    HeroAICoord.get_party_position(email) -> int | None

Non-goals:
    * Skill-level tuning (delegated to HeroAI custom_skill system).
    * Formation geometry (delegated to HeroAI INI configuration).
    * Party-leader promotion (HeroAI follows the GW-native party leader;
      scripts should use flag-based control instead of swapping leaders).
"""

from __future__ import annotations

import logging
from typing import Final, Iterable

import Py4GW
from Py4GWCoreLib import GLOBAL_CACHE, Player
from Py4GWCoreLib.enums_src.Multiboxing_enums import SharedCommandType
from Py4GWCoreLib.GlobalCache.shared_memory_src.AccountStruct import AccountStruct
from Py4GWCoreLib.py4gwcorelib_src.Console import ConsoleLog


# HeroAIOptionStruct property names. Kept as module-level constants so typos
# surface at import time (paired with a linter) and field renames stay local.
_PROP_COMBAT: Final[str] = "Combat"
_PROP_FOLLOWING: Final[str] = "Following"
_PROP_IS_FLAGGED: Final[str] = "IsFlagged"
_PROP_FLAG_POS_X: Final[str] = "FlagPosX"
_PROP_FLAG_POS_Y: Final[str] = "FlagPosY"

_LOG_TAG: Final[str] = "HeroAICoord"

_logger = logging.getLogger(_LOG_TAG)


class HeroAICoordError(RuntimeError):
    """Raised for unrecoverable coordinator errors (ShMem unavailable, etc)."""


class HeroAICoord:
    """Static facade around GLOBAL_CACHE.ShMem HeroAI property access."""

    # --- Mutations: single-property -------------------------------------

    @staticmethod
    def set_combat(email: str, enabled: bool) -> bool:
        return HeroAICoord._set(email, _PROP_COMBAT, bool(enabled))

    @staticmethod
    def set_following(email: str, enabled: bool) -> bool:
        return HeroAICoord._set(email, _PROP_FOLLOWING, bool(enabled))

    @staticmethod
    def set_flag_xy(email: str, x: float, y: float) -> bool:
        """
        Pin `email` to world-space (x, y). Overrides formation follow.

        Write order is FlagPosX -> FlagPosY -> IsFlagged=True, so a peer
        reading between writes either sees the previous flag state (safe)
        or the new coords with IsFlagged already True (correct). The only
        tearing window is inside the two FlagPos writes; a peer reading
        there would see new-X with old-Y for one tick. For waypoint pacing
        at hundreds of ms, this is not a practical concern.
        """
        if not email:
            return False
        ok_x = HeroAICoord._set(email, _PROP_FLAG_POS_X, float(x))
        ok_y = HeroAICoord._set(email, _PROP_FLAG_POS_Y, float(y))
        ok_flag = HeroAICoord._set(email, _PROP_IS_FLAGGED, True)
        return ok_x and ok_y and ok_flag

    @staticmethod
    def clear_flag(email: str) -> bool:
        """Remove the flag; peer resumes formation follow on its next tick."""
        return HeroAICoord._set(email, _PROP_IS_FLAGGED, False)

    # --- Mutations: composite -------------------------------------------

    @staticmethod
    def freeze(email: str) -> bool:
        """Stop combat and following; clear any flag. Account stands still."""
        a = HeroAICoord.set_combat(email, False)
        b = HeroAICoord.set_following(email, False)
        c = HeroAICoord.clear_flag(email)
        return a and b and c

    @staticmethod
    def unfreeze(email: str) -> bool:
        """Restore combat and following; clear any residual flag."""
        a = HeroAICoord.set_combat(email, True)
        b = HeroAICoord.set_following(email, True)
        c = HeroAICoord.clear_flag(email)
        return a and b and c

    # --- Mutations: broadcast -------------------------------------------

    @staticmethod
    def broadcast_pixel_stack(targets: Iterable[str], x: float, y: float) -> int:
        """
        Send a PixelStack command to each target email. Returns the number
        of messages dispatched. HeroAI consumes SharedCommandType.PixelStack
        natively on the receiving client.
        """
        sender = Player.GetAccountEmail()
        if not sender:
            return 0

        materialized = [email for email in targets if email and email != sender]
        if not materialized:
            return 0

        dispatched = 0
        payload = (float(x), float(y), 0.0, 0.0)
        for email in materialized:
            try:
                GLOBAL_CACHE.ShMem.SendMessage(
                    sender, email, SharedCommandType.PixelStack, payload,
                )
                dispatched += 1
            except Exception as exc:
                ConsoleLog(
                    _LOG_TAG,
                    f"pixel_stack dispatch failed for {email}: {exc}",
                    Py4GW.Console.MessageType.Warning,
                )
        return dispatched

    # --- Reads ----------------------------------------------------------

    @staticmethod
    def is_loaded(email: str) -> bool:
        """True iff the account is attached to shared memory."""
        return HeroAICoord._account(email) is not None

    @staticmethod
    def is_dead(email: str) -> bool | None:
        """
        Returns True/False when the account's state is known; None when
        the account is not yet attached to ShMem. Pollers should treat
        None as 'unknown — check again next tick'.
        """
        acc = HeroAICoord._account(email)
        if acc is None:
            return None
        try:
            return bool(acc.AgentData.Is_Dead)
        except Exception:
            return None

    @staticmethod
    def is_in_combat(email: str) -> bool | None:
        """Returns the target's Is_InCombatStance flag, or None if unknown."""
        acc = HeroAICoord._account(email)
        if acc is None:
            return None
        try:
            return bool(acc.AgentData.Is_InCombatStance)
        except Exception:
            return None

    @staticmethod
    def get_position(email: str) -> tuple[float, float] | None:
        acc = HeroAICoord._account(email)
        if acc is None:
            return None
        try:
            return (float(acc.AgentData.Pos.x), float(acc.AgentData.Pos.y))
        except Exception:
            return None

    @staticmethod
    def get_party_position(email: str) -> int | None:
        """Zero-based party slot index (slot 1 = 0), or None if unknown."""
        acc = HeroAICoord._account(email)
        if acc is None:
            return None
        try:
            return int(acc.AgentPartyData.PartyPosition)
        except Exception:
            return None

    # --- Internals ------------------------------------------------------

    @staticmethod
    def _account(email: str) -> AccountStruct | None:
        if not email:
            return None
        try:
            return GLOBAL_CACHE.ShMem.GetAccountDataFromEmail(email)
        except Exception as exc:
            _logger.debug("account lookup failed for %s: %s", email, exc)
            return None

    @staticmethod
    def _set(email: str, prop: str, value: bool | float | int) -> bool:
        if not email:
            return False
        try:
            GLOBAL_CACHE.ShMem.SetHeroAIPropertyByEmail(email, prop, value)
            return True
        except Exception as exc:
            ConsoleLog(
                _LOG_TAG,
                f"SetHeroAIProperty({email}, {prop}, {value!r}) failed: {exc}",
                Py4GW.Console.MessageType.Warning,
            )
            return False
