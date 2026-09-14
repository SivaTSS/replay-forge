from pathlib import Path

import cv2
import numpy as np
import pytest

from replayforge.capabilities.assets import CapabilityAssetError, LocalCapabilityAssetStore
from replayforge.surfaces.models import SurfaceError
from replayforge.surfaces.privacy import mask_evidence_frame


def test_retained_frame_has_no_original_pixels_or_metadata() -> None:
    original = np.full((90, 320, 3), 255, dtype=np.uint8)
    cv2.putText(original, "Synthetic Person", (5, 45), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0))
    success, encoded = cv2.imencode(".png", original)
    assert success
    retained = mask_evidence_frame(encoded.tobytes())
    masked = cv2.imdecode(np.frombuffer(retained.content, dtype=np.uint8), cv2.IMREAD_COLOR)
    assert masked is not None
    assert masked.shape == original.shape
    assert np.all(masked == (39, 24, 17))
    assert retained.redaction_directives == ("mask:full-viewport",)
    assert b"Synthetic Person" not in retained.content
    assert np.any(original != masked)


def test_invalid_frame_fails_closed() -> None:
    with pytest.raises(SurfaceError, match="valid image"):
        mask_evidence_frame(b"not a PNG image")


def test_disabled_pixel_capture_preserves_reading_of_existing_assets(tmp_path: Path) -> None:
    trusted_setup = LocalCapabilityAssetStore(tmp_path, capture_enabled=True)
    png = mask_evidence_frame(
        cv2.imencode(".png", np.zeros((10, 10, 3), dtype=np.uint8))[1].tobytes()
    ).content
    key, digest = trusted_setup.write(png)
    runtime_store = LocalCapabilityAssetStore(tmp_path, capture_enabled=False)
    assert runtime_store.read(key, digest) == png
    before = set(tmp_path.iterdir())
    with pytest.raises(CapabilityAssetError, match="disabled"):
        runtime_store.write(png)
    assert set(tmp_path.iterdir()) == before
