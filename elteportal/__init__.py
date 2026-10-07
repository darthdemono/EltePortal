"""One CLI over the ELTE systems.

Canvas, Neptun, tanrend and the departmental file servers each speak a different
protocol and hold a different half of the same semester. This package puts them
behind one command with one output shape.
"""

from .api import Client, envelope

__version__ = "1.0.0"

__all__ = ["Client", "envelope", "__version__"]
