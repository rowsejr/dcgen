"""dcgen - agentic datacentre RIBA Stage 2 test-fit generator (scaffold)."""
from .schema import Brief, SiteInput, Scene, Element, Receptor
from .layout import generate, generate_options, compare_topologies, move_element, Variant

__version__ = "0.1.0"
