"""Local reports backed by the configured Canvas instance."""
from __future__ import annotations

from . import canvas_grades, canvas_missing

REPORTS = {"missing": canvas_missing.run, "grades": canvas_grades.run}
