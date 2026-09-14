"""Conservative screenshot retention when no public-pixel policy is available."""

import cv2
import numpy as np

from replayforge.surfaces.models import SanitizedSurfaceFrame, SurfaceError


def mask_evidence_frame(content: bytes) -> SanitizedSurfaceFrame:
    """Suppress all pixels in memory; OCR and DOM selectors cannot certify absent PII.

    Keep only frame dimensions and PNG encoding. Live perception/operator frames
    use a separate path and must never be passed off as sanitized evidence.
    """
    frame = cv2.imdecode(np.frombuffer(content, dtype=np.uint8), cv2.IMREAD_COLOR)
    if frame is None:
        raise SurfaceError("evidence_screenshot_failed", "The evidence frame is not a valid image.")
    frame[:] = (39, 24, 17)
    success, encoded = cv2.imencode(".png", frame)
    if not success:
        raise SurfaceError(
            "evidence_screenshot_failed", "The masked evidence frame could not be encoded."
        )
    return SanitizedSurfaceFrame(encoded.tobytes(), ("mask:full-viewport",))
