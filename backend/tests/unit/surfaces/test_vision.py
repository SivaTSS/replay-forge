from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import cv2
import numpy as np
import pytest

from replayforge.capabilities.assets import CapabilityAssetError, LocalCapabilityAssetStore
from replayforge.capabilities.models import (
    ImageAnchorCandidate,
    MatchMode,
    OcrRelativeCandidate,
    OcrTextCandidate,
    RelativeRegion,
)
from replayforge.surfaces.models import ScreenRegion, SurfaceError, Viewport, VisualToken
from replayforge.surfaces.vision import RapidOcrTextRecognizer, VisionGrounder


@dataclass(frozen=True)
class StubRecognizer:
    result: tuple[VisualToken, ...]

    def recognize(self, png: bytes) -> tuple[VisualToken, ...]:
        del png
        return self.result


def png_with_icon(x: int, y: int) -> bytes:
    image = np.full((240, 400, 3), 245, dtype=np.uint8)
    cv2.rectangle(image, (x, y), (x + 60, y + 50), (20, 80, 140), 3)
    cv2.line(image, (x + 22, y + 15), (x + 39, y + 25), (20, 80, 140), 4)
    cv2.line(image, (x + 39, y + 25), (x + 22, y + 35), (20, 80, 140), 4)
    ok, encoded = cv2.imencode(".png", image)
    assert ok
    return encoded.tobytes()


def test_ocr_requires_exactly_one_match(tmp_path: Path) -> None:
    token = VisualToken("Search", 0.98, ScreenRegion(200, 80, 70, 24))
    vision = VisionGrounder(StubRecognizer((token,)), LocalCapabilityAssetStore(tmp_path))

    resolved = vision.resolve(
        OcrTextCandidate(strategy="ocr_text", value="search"), b"frame", Viewport(400, 240)
    )

    assert resolved.region == token.region
    ambiguous = VisionGrounder(
        StubRecognizer((token, VisualToken("Search", 0.98, ScreenRegion(20, 80, 70, 24)))),
        LocalCapabilityAssetStore(tmp_path),
    )
    with pytest.raises(SurfaceError) as error:
        ambiguous.resolve(
            OcrTextCandidate(strategy="ocr_text", value="Search"),
            b"other-frame",
            Viewport(400, 240),
        )
    assert error.value.code == "target_ambiguous"


def test_ocr_relative_region_is_derived_from_fresh_anchor(tmp_path: Path) -> None:
    anchor = VisualToken("Member ID", 0.99, ScreenRegion(40, 50, 90, 20))
    vision = VisionGrounder(StubRecognizer((anchor,)), LocalCapabilityAssetStore(tmp_path))

    resolved = vision.resolve(
        OcrRelativeCandidate(
            strategy="ocr_relative",
            anchor="Member ID",
            relation="right_of",
            relative_region=RelativeRegion(x=5, y=-0.5, width=8, height=2),
        ),
        b"frame",
        Viewport(400, 240),
    )

    assert resolved.region == ScreenRegion(140, 40, 160, 40)


def test_ocr_relative_text_and_region_extraction(tmp_path: Path) -> None:
    tokens = (
        VisualToken("Account type", 0.99, ScreenRegion(30, 40, 100, 20)),
        VisualToken("Savings", 0.98, ScreenRegion(180, 40, 80, 20)),
        VisualToken("Below", 0.97, ScreenRegion(30, 100, 60, 20)),
    )
    vision = VisionGrounder(StubRecognizer(tokens), LocalCapabilityAssetStore(tmp_path))

    resolved = vision.resolve(
        OcrRelativeCandidate(
            strategy="ocr_relative",
            anchor="Account type",
            target_text="Savings",
            relation="right_of",
        ),
        b"frame",
        Viewport(400, 240),
    )

    assert resolved.region == tokens[1].region
    assert vision.extract(b"frame", ScreenRegion(150, 20, 150, 60)) == "Savings"
    assert vision.contains_text(b"frame", "sav", MatchMode.CONTAINS, 0.9, None, Viewport(400, 240))
    assert vision.contains_text(b"frame", r"Sav.*", MatchMode.REGEX, 0.9, None, Viewport(400, 240))
    with pytest.raises(SurfaceError) as error:
        vision.extract(b"frame", ScreenRegion(300, 200, 20, 20))
    assert error.value.code == "visual_text_absent"


def test_rapidocr_adapter_normalizes_polygons() -> None:
    class Result:
        def __init__(self) -> None:
            self.boxes = [[[10.2, 20.8], [50.1, 20.2], [50.6, 42.1], [10.4, 42.7]]]
            self.txts = [" Search "]
            self.scores = [0.97]

    class Engine:
        def __call__(self, png: bytes) -> Result:
            assert png == b"png"
            return Result()

    recognizer = RapidOcrTextRecognizer()
    recognizer._engine = cast(Any, Engine())

    assert recognizer.recognize(b"png") == (
        VisualToken("Search", 0.97, ScreenRegion(10, 20, 41, 23)),
    )


def test_edge_template_moves_without_persisting_target_coordinates(tmp_path: Path) -> None:
    vision = VisionGrounder(StubRecognizer(()), LocalCapabilityAssetStore(tmp_path))
    key, digest = vision.create_edge_template(png_with_icon(40, 70), ScreenRegion(40, 70, 61, 51))

    resolved = vision.resolve(
        ImageAnchorCandidate(
            strategy="image_anchor",
            asset_key=key,
            content_hash=digest,
            minimum_score=0.9,
            uniqueness_margin=0.05,
            minimum_scale=1,
            maximum_scale=1,
            scale_step=0.05,
        ),
        png_with_icon(250, 130),
        Viewport(400, 240),
    )

    assert resolved.region == ScreenRegion(250, 130, 61, 51)
    assert resolved.method == "image_anchor"


def test_asset_hash_mismatch_fails_closed(tmp_path: Path) -> None:
    store = LocalCapabilityAssetStore(tmp_path)
    key, _digest = store.write(png_with_icon(40, 70))

    with pytest.raises(CapabilityAssetError, match="disagree"):
        store.read(key, "sha256:" + "0" * 64)


def test_asset_and_template_validation_fail_closed(tmp_path: Path) -> None:
    store = LocalCapabilityAssetStore(tmp_path, maximum_bytes=500)
    with pytest.raises(CapabilityAssetError, match="PNG"):
        store.write(b"not-an-image")
    with pytest.raises(CapabilityAssetError, match="size limit"):
        store.write(b"\x89PNG\r\n\x1a\n" + b"x" * 501)
    with pytest.raises(CapabilityAssetError, match="content-addressed"):
        store.read("file://template.png", "sha256:" + "0" * 64)

    vision = VisionGrounder(StubRecognizer(()), LocalCapabilityAssetStore(tmp_path))
    blank = np.full((100, 100, 3), 255, dtype=np.uint8)
    ok, encoded = cv2.imencode(".png", blank)
    assert ok
    with pytest.raises(SurfaceError) as error:
        vision.create_edge_template(encoded.tobytes(), ScreenRegion(10, 10, 20, 20))
    assert error.value.code == "template_low_information"
    with pytest.raises(SurfaceError) as error:
        vision.resolve(
            ImageAnchorCandidate(
                strategy="image_anchor",
                asset_key="asset://sha256/" + "0" * 64,
                content_hash="sha256:" + "0" * 64,
            ),
            png_with_icon(20, 20),
            Viewport(400, 240),
        )
    assert error.value.code == "template_integrity_failed"


def test_invalid_frame_and_missing_ocr_target_fail_closed(tmp_path: Path) -> None:
    vision = VisionGrounder(StubRecognizer(()), LocalCapabilityAssetStore(tmp_path))
    with pytest.raises(SurfaceError) as error:
        vision.resolve(
            OcrTextCandidate(strategy="ocr_text", value="Search"),
            b"no-text",
            Viewport(400, 240),
        )
    assert error.value.code == "target_absent"
    with pytest.raises(SurfaceError) as error:
        vision.create_edge_template(b"invalid", ScreenRegion(0, 0, 20, 20))
    assert error.value.code == "frame_invalid"
