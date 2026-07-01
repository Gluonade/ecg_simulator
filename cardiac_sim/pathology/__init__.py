"""
Pathology sub-package.

Contains :class:`~cardiac_sim.core.interfaces.AbstractPathologyPlugin`
(re-exported for convenience) and will house shared plugin utilities.
Concrete plugins live in ``cardiac_sim.plugins``.
"""

from cardiac_sim.core.interfaces import AbstractPathologyPlugin

__all__ = ["AbstractPathologyPlugin"]
