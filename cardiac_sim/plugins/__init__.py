"""
Plugins package.

Drop a new module here to make a pathology plugin automatically discoverable
by the GUI.  Each module must contain at least one concrete subclass of
:class:`~cardiac_sim.core.interfaces.AbstractPathologyPlugin`.

Example skeleton::

    from cardiac_sim.core.interfaces import AbstractPathologyPlugin, PluginParameter
    from cardiac_sim.core.parameter_model import SimulationParameters

    class MyPathology(AbstractPathologyPlugin):
        def get_display_name(self) -> str:
            return "My Pathology"

        def get_description(self) -> str:
            return "One-sentence clinical description."

        def get_parameters(self) -> list[PluginParameter]:
            return []

        def apply(self, params: SimulationParameters) -> SimulationParameters:
            p = params.copy()
            # modify p here
            return p
"""
