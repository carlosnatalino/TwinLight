"""Output generation: eye diagrams, constellation diagrams, OPM metrics."""

from twinlight.output.constellation import synthesize_constellation
from twinlight.output.eye_diagram import synthesize_eye

__all__ = ["synthesize_constellation", "synthesize_eye"]
