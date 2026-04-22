from dataclasses import dataclass


@dataclass
class SortableAgentData:
    agent_id: int
    distance_from_player: float
    hp: float
    is_caster: bool
    is_melee: bool
    is_martial: bool
    enemy_quantity_within_range: int
    agent_quantity_within_range: int
    energy: float
    # True for agents that came from SpiritPetArray (pets, summoned NPCs
    # like Angchu from Tengu Support Flare, etc.). Used by ally targeting
    # to deprioritize non-party allies so real party members always get
    # heals/buffs first regardless of HP.
    is_summoned_ally: bool = False

