"""Boot-time refusal package. See PRD §Boot-time refusal (8 checks).

Public entry point: BootValidator.validate_all() — orchestrates the 8 checks
in order and lets the first InfraError propagate to the lifespan, which
prints `BOOT FAIL #N: ...` and exits 1.
"""

from app.boot.validator import BootValidator

__all__ = ["BootValidator"]
