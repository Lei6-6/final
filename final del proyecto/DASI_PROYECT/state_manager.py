import asyncio
from loguru import logger


class StateManager:
    """
    Centralized, concurrency-safe state for the agent.

    - inventory: resources currently owned
    - goal_needs: how many units of each target resource are still needed
    - target_resources: resources blocked from trading (still needed for goal)
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._inventory: dict[str, int] = {}
        self._goal_needs: dict[str, int] = {}
        self._initial_goal: dict[str, int] = {}
        self._target_resources: set[str] = set()

    # ------------------------------------------------------------------
    # Initialization
    # ------------------------------------------------------------------

    def initialize(self, inventory: dict[str, int], goal: dict[str, int]) -> None:
        """Set up state from Butler data. Must be called once at startup."""
        self._inventory = {
            k: v for k, v in inventory.items() if isinstance(v, (int, float)) and v >= 0
        }
        self._goal_needs = {
            k: int(v) for k, v in goal.items() if isinstance(v, (int, float)) and v > 0
        }
        self._initial_goal = dict(self._goal_needs)
        self._target_resources = set(self._goal_needs.keys())
        logger.info(
            f"State initialized | inventory={self._inventory} "
            f"| goal_needs={self._goal_needs}"
        )

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def snapshot(self) -> dict:
        """Return a safe copy of the current state (no lock needed for reads)."""
        return {
            "inventory": dict(self._inventory),
            "goal_needs": dict(self._goal_needs),
            "initial_goal": dict(self._initial_goal),
            "target_resources": set(self._target_resources),
        }

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    async def deduct_resources(self, resources: dict[str, int]) -> bool:
        """
        Atomically deduct resources after sending them to another agent.

        Returns True on success. Returns False (without mutating state) if any
        resource is unavailable or if a quantity is invalid.
        """
        async with self._lock:
            # Validate the entire batch before touching state
            for resource, qty in resources.items():
                if not isinstance(qty, int) or qty <= 0:
                    logger.warning(f"Deduct rejected: invalid qty for '{resource}': {qty}")
                    return False
                if self._inventory.get(resource, 0) < qty:
                    logger.warning(
                        f"Deduct rejected: insufficient '{resource}' "
                        f"(have {self._inventory.get(resource, 0)}, need {qty})"
                    )
                    return False

            # Apply
            for resource, qty in resources.items():
                self._inventory[resource] -= qty

            logger.info(f"Deducted {resources} | inventory now: {self._inventory}")
            return True

    async def sync_from_butler(self, butler_inventory: dict[str, int]) -> None:
        """
        Reconcile local state with Butler's authoritative inventory.
        Called periodically so deliveries credited by Butler show up locally.
        """
        async with self._lock:
            for resource, raw_qty in butler_inventory.items():
                butler_qty = int(raw_qty) if isinstance(raw_qty, (int, float)) else 0
                local_qty = self._inventory.get(resource, 0)

                if butler_qty > local_qty:
                    # Butler has more → we received resources we didn't know about
                    gained = butler_qty - local_qty
                    self._inventory[resource] = butler_qty
                    if resource in self._goal_needs:
                        self._goal_needs[resource] = max(0, self._goal_needs[resource] - gained)
                        if self._goal_needs[resource] == 0:
                            del self._goal_needs[resource]
                            self._target_resources.discard(resource)
                            logger.info(f"[SYNC] Goal satisfied for '{resource}'")
                elif butler_qty < local_qty:
                    # Butler has less → accept Butler (deduction already confirmed)
                    self._inventory[resource] = butler_qty

            # Remove resources Butler no longer tracks
            for resource in list(self._inventory.keys()):
                if resource not in butler_inventory:
                    self._inventory[resource] = 0

            logger.info(f"[SYNC] inventory={dict(self._inventory)} | goal_needs={dict(self._goal_needs)}")

    async def add_resources(self, resources: dict[str, int]) -> None:
        """
        Atomically add delivered resources to inventory and update goal tracking.

        When a target resource reaches its goal quantity, it is removed from
        both goal_needs and target_resources so it can be traded freely.
        """
        async with self._lock:
            for resource, qty in resources.items():
                if not isinstance(qty, int) or qty <= 0:
                    logger.warning(f"Add skipped: invalid qty for '{resource}': {qty}")
                    continue

                self._inventory[resource] = self._inventory.get(resource, 0) + qty

                if resource in self._goal_needs:
                    self._goal_needs[resource] = max(0, self._goal_needs[resource] - qty)
                    if self._goal_needs[resource] == 0:
                        del self._goal_needs[resource]
                        self._target_resources.discard(resource)
                        logger.info(
                            f"Goal satisfied for '{resource}': removed from target_resources"
                        )

            logger.info(
                f"Added {resources} | inventory={self._inventory} "
                f"| goal_needs={self._goal_needs}"
            )


# Module-level singleton — all other modules import this instance
state = StateManager()
