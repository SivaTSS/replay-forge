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
    OcrAnchor,
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


def png_with_icons(*positions: tuple[int, int]) -> bytes:
    image = np.full((240, 400, 3), 245, dtype=np.uint8)
    for x, y in positions:
        cv2.rectangle(image, (x, y), (x + 60, y + 50), (20, 80, 140), 3)
        cv2.line(image, (x + 22, y + 15), (x + 39, y + 25), (20, 80, 140), 4)
        cv2.line(image, (x + 39, y + 25), (x + 22, y + 35), (20, 80, 140), 4)
    ok, encoded = cv2.imencode(".png", image)
    assert ok
    return encoded.tobytes()


def png_with_scaled_icon(x: int, y: int, scale: float) -> bytes:
    source = cv2.imdecode(np.frombuffer(png_with_icon(40, 70), np.uint8), cv2.IMREAD_COLOR)
    assert source is not None
    crop = source[70:121, 40:101]
    width = round(crop.shape[1] * scale)
    height = round(crop.shape[0] * scale)
    resized = cv2.resize(crop, (width, height), interpolation=cv2.INTER_AREA)
    image = np.full((240, 400, 3), 245, dtype=np.uint8)
    image[y : y + height, x : x + width] = resized
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


def test_ocr_relative_region_clips_at_viewport_edges(tmp_path: Path) -> None:
    anchor = VisualToken("Member ID", 0.99, ScreenRegion(5, 5, 20, 20))
    vision = VisionGrounder(StubRecognizer((anchor,)), LocalCapabilityAssetStore(tmp_path))

    resolved = vision.resolve(
        OcrRelativeCandidate(
            strategy="ocr_relative",
            anchor="Member ID",
            relation="right_of",
            relative_region=RelativeRegion(x=-2, y=-2, width=40, height=40),
        ),
        b"frame",
        Viewport(100, 80),
    )

    assert resolved.region == ScreenRegion(0, 0, 100, 80)


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


def test_global_template_matching_rejects_identical_same_scale_icons(tmp_path: Path) -> None:
    vision = VisionGrounder(StubRecognizer(()), LocalCapabilityAssetStore(tmp_path))
    key, digest = vision.create_edge_template(png_with_icon(40, 70), ScreenRegion(40, 70, 61, 51))

    with pytest.raises(SurfaceError) as error:
        vision.resolve(
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
            png_with_icons((40, 70), (180, 70), (300, 70)),
            Viewport(400, 240),
        )

    assert error.value.code == "target_ambiguous"


def test_contextual_template_matching_selects_the_anchored_row(tmp_path: Path) -> None:
    frame = png_with_icons((40, 70), (180, 70), (300, 70))
    vision = VisionGrounder(
        StubRecognizer((VisualToken("Savings", 0.99, ScreenRegion(40, 50, 65, 20)),)),
        LocalCapabilityAssetStore(tmp_path),
    )
    key, digest = vision.create_edge_template(frame, ScreenRegion(40, 70, 61, 51))

    resolved = vision.resolve(
        ImageAnchorCandidate(
            strategy="image_anchor",
            asset_key=key,
            content_hash=digest,
            context_anchor=OcrAnchor(value="Savings"),
            relative_search_region=RelativeRegion(x=10, y=0.5, width=8, height=4),
            minimum_score=0.9,
            uniqueness_margin=0.05,
            minimum_scale=1,
            maximum_scale=1,
            scale_step=0.05,
        ),
        frame,
        Viewport(400, 240),
    )

    assert resolved.region == ScreenRegion(300, 70, 61, 51)
    assert resolved.method == "image_anchor"


@pytest.mark.parametrize(
    ("tokens", "code"),
    [
        ((), "target_absent"),
        (
            (
                VisualToken("Savings", 0.99, ScreenRegion(40, 50, 65, 20)),
                VisualToken("Savings", 0.99, ScreenRegion(40, 100, 65, 20)),
            ),
            "target_ambiguous",
        ),
    ],
)
def test_contextual_template_requires_one_context_anchor(
    tmp_path: Path, tokens: tuple[VisualToken, ...], code: str
) -> None:
    vision = VisionGrounder(StubRecognizer(tokens), LocalCapabilityAssetStore(tmp_path))
    with pytest.raises(SurfaceError) as error:
        vision.resolve(
            ImageAnchorCandidate(
                strategy="image_anchor",
                asset_key="asset://sha256/" + "a" * 64,
                content_hash="sha256:" + "a" * 64,
                context_anchor=OcrAnchor(value="Savings"),
                relative_search_region=RelativeRegion(x=7, y=2, width=8, height=4),
            ),
            png_with_icon(180, 100),
            Viewport(400, 240),
        )
    assert error.value.code == code


@pytest.mark.parametrize("scale", [0.8, 1.0, 1.125])
def test_contextual_template_supports_declared_scales(tmp_path: Path, scale: float) -> None:
    vision = VisionGrounder(
        StubRecognizer((VisualToken("Savings", 0.99, ScreenRegion(40, 50, 65, 20)),)),
        LocalCapabilityAssetStore(tmp_path),
    )
    key, digest = vision.create_edge_template(png_with_icon(40, 70), ScreenRegion(40, 70, 61, 51))

    resolved = vision.resolve(
        ImageAnchorCandidate(
            strategy="image_anchor",
            asset_key=key,
            content_hash=digest,
            context_anchor=OcrAnchor(value="Savings"),
            relative_search_region=RelativeRegion(x=7, y=2, width=8, height=4),
            # The synthetic raster is resampled twice (crop then edge map), so
            # its score is lower than the browser-rendered fixture.
            minimum_score=0.5,
            uniqueness_margin=0.03,
            minimum_scale=scale,
            maximum_scale=scale,
            scale_step=0.025,
        ),
        png_with_scaled_icon(180, 100, scale),
        Viewport(400, 240),
    )

    assert resolved.region.x == 180
    assert resolved.region.y == 100
    assert resolved.region.width == round(61 * scale)
    assert resolved.region.height == round(51 * scale)


def test_template_peak_extraction_is_bounded() -> None:
    response = np.zeros((80, 120), dtype=np.float32)
    response[::8, ::8] = 0.95

    peaks = VisionGrounder._template_peaks(response, 5, 5, 0.5)

    assert len(peaks) <= 10


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
