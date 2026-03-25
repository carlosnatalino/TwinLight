"""Output generation: eye diagrams, constellation diagrams, OPM metrics."""

from tapi_twin.output.constellation import synthesize_constellation
from tapi_twin.output.eye_diagram import synthesize_eye

__all__ = ["synthesize_constellation", "synthesize_eye"]
