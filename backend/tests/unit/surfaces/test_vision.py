from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from time import sleep
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
    RenderedFieldValueCandidate,
    RenderedGroupImageCandidate,
    RenderedLabeledControlCandidate,
    RenderedTextCandidate,
)
from replayforge.surfaces.models import ScreenRegion, SurfaceError, Viewport, VisualToken
from replayforge.surfaces.vision import (
    RapidOcrTextRecognizer,
    VisionGrounder,
    VisualLayoutGraph,
    VisualNode,
)
from replayforge.surfaces.vision_policy import load_vision_policy


@dataclass(frozen=True)
class StubRecognizer:
    result: tuple[VisualToken, ...]

    def recognize(self, png: bytes) -> tuple[VisualToken, ...]:
        del png
        return self.result


def semantic_vision(tmp_path: Path, tokens: tuple[VisualToken, ...]) -> VisionGrounder:
    return VisionGrounder(
        StubRecognizer(tokens),
        LocalCapabilityAssetStore(tmp_path),
        load_vision_policy(Path("config/vision-policy.yaml")),
    )


def png_with_icon(x: int, y: int) -> bytes:
    image = np.full((240, 400, 3), 245, dtype=np.uint8)
    cv2.rectangle(image, (x, y), (x + 60, y + 50), (20, 80, 140), 3)
    cv2.line(image, (x + 22, y + 15), (x + 39, y + 25), (20, 80, 140), 4)
    cv2.line(image, (x + 39, y + 25), (x + 22, y + 35), (20, 80, 140), 4)
    ok, encoded = cv2.imencode(".png", image)
    assert ok
    return encoded.tobytes()


def blank_png(width: int = 400, height: int = 240) -> bytes:
    image = np.full((height, width, 3), 245, dtype=np.uint8)
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


def test_rendered_text_resolves_current_frame_phrase(tmp_path: Path) -> None:
    token = VisualToken("Search", 0.98, ScreenRegion(200, 80, 70, 24))
    vision = semantic_vision(tmp_path, (token,))

    resolved = vision.resolve(
        RenderedTextCandidate(strategy="rendered_text", value="search"),
        b"rendered-frame",
        Viewport(400, 240),
    )

    assert resolved.region == token.region
    assert resolved.method == "rendered_text"


def test_rendered_labeled_control_uses_detected_control_rectangle(tmp_path: Path) -> None:
    image = np.full((240, 400, 3), 245, dtype=np.uint8)
    cv2.rectangle(image, (150, 80), (300, 130), (20, 80, 140), 3)
    ok, encoded = cv2.imencode(".png", image)
    assert ok
    vision = semantic_vision(
        tmp_path, (VisualToken("Member ID", 0.99, ScreenRegion(20, 90, 90, 20)),)
    )

    resolved = vision.resolve(
        RenderedLabeledControlCandidate(
            strategy="rendered_labeled_control", label="Member ID", control_kind="text_input"
        ),
        encoded.tobytes(),
        Viewport(400, 240),
    )

    assert resolved.method == "rendered_labeled_control"
    assert resolved.region.x <= 150 <= resolved.region.x + resolved.region.width
    assert resolved.region.y <= 80 <= resolved.region.y + resolved.region.height


@pytest.mark.parametrize("label_height", [20, 26])
def test_labeled_control_uses_local_label_scale_on_mixed_typography(
    tmp_path: Path, label_height: int
) -> None:
    image = np.full((300, 500, 3), 245, dtype=np.uint8)
    cv2.rectangle(image, (20, 70), (300, 106), (20, 80, 140), 2)
    ok, encoded = cv2.imencode(".png", image)
    assert ok
    vision = semantic_vision(
        tmp_path,
        (
            VisualToken("Query", 0.99, ScreenRegion(20, 43, 90, label_height)),
            VisualToken("Larger heading", 0.99, ScreenRegion(20, 160, 210, 32)),
            VisualToken("Larger row", 0.99, ScreenRegion(20, 210, 210, 32)),
        ),
    )
    resolved = vision.resolve(
        RenderedLabeledControlCandidate(
            strategy="rendered_labeled_control", label="Query", control_kind="text_input"
        ),
        encoded.tobytes(),
        Viewport(500, 300),
    )
    assert 65 <= resolved.region.y <= 75
    assert resolved.region.y + resolved.region.height < 115


def test_labeled_control_cannot_skip_intervening_section_text(tmp_path: Path) -> None:
    image = np.full((300, 500, 3), 245, dtype=np.uint8)
    cv2.rectangle(image, (20, 180), (300, 230), (20, 80, 140), 2)
    ok, encoded = cv2.imencode(".png", image)
    assert ok
    vision = semantic_vision(
        tmp_path,
        (
            VisualToken("Query", 0.99, ScreenRegion(20, 45, 90, 20)),
            VisualToken("Results", 0.99, ScreenRegion(20, 125, 90, 20)),
        ),
    )
    with pytest.raises(SurfaceError) as error:
        vision.resolve(
            RenderedLabeledControlCandidate(
                strategy="rendered_labeled_control", label="Query", control_kind="text_input"
            ),
            encoded.tobytes(),
            Viewport(500, 300),
        )
    assert error.value.code == "target_absent"


def test_rendered_text_prefers_unique_match_inside_a_control(tmp_path: Path) -> None:
    image = np.full((240, 400, 3), 245, dtype=np.uint8)
    cv2.rectangle(image, (130, 150), (300, 205), (20, 80, 140), 3)
    ok, encoded = cv2.imencode(".png", image)
    assert ok
    button = ScreenRegion(165, 165, 100, 22)
    vision = semantic_vision(
        tmp_path,
        (
            VisualToken("Find Card", 0.99, ScreenRegion(20, 30, 120, 24)),
            VisualToken("Find card", 0.99, button),
        ),
    )

    resolved = vision.resolve(
        RenderedTextCandidate(strategy="rendered_text", value="Find card"),
        encoded.tobytes(),
        Viewport(400, 240),
    )

    assert resolved.region == button


def test_rendered_labeled_control_tolerates_ocr_box_touching_control(
    tmp_path: Path,
) -> None:
    image = np.full((240, 400, 3), 245, dtype=np.uint8)
    cv2.rectangle(image, (20, 108), (380, 158), (20, 80, 140), 3)
    cv2.rectangle(image, (20, 180), (380, 230), (20, 80, 140), 3)
    ok, encoded = cv2.imencode(".png", image)
    assert ok
    vision = semantic_vision(
        tmp_path, (VisualToken("Merchant", 0.99, ScreenRegion(20, 88, 90, 22)),)
    )

    resolved = vision.resolve(
        RenderedLabeledControlCandidate(
            strategy="rendered_labeled_control", label="Merchant", control_kind="text_input"
        ),
        encoded.tobytes(),
        Viewport(400, 240),
    )

    assert resolved.region.y <= 108 <= resolved.region.y + resolved.region.height


def test_rendered_labeled_control_accepts_input_shaped_container(tmp_path: Path) -> None:
    image = np.full((240, 400, 3), 245, dtype=np.uint8)
    cv2.rectangle(image, (20, 88), (380, 148), (20, 80, 140), 3)
    cv2.rectangle(image, (20, 165), (380, 220), (20, 80, 140), 3)
    ok, encoded = cv2.imencode(".png", image)
    assert ok
    vision = semantic_vision(
        tmp_path,
        (
            VisualToken("Payoff date", 0.99, ScreenRegion(20, 78, 100, 22)),
            VisualToken("Enter value", 0.99, ScreenRegion(35, 110, 90, 20)),
        ),
    )

    resolved = vision.resolve(
        RenderedLabeledControlCandidate(
            strategy="rendered_labeled_control",
            label="Payoff date",
            control_kind="text_input",
        ),
        encoded.tobytes(),
        Viewport(400, 240),
    )

    assert resolved.region.y <= 88 <= resolved.region.y + resolved.region.height


def test_rendered_field_value_associates_horizontal_value(tmp_path: Path) -> None:
    vision = semantic_vision(
        tmp_path,
        (
            VisualToken("Currency", 0.99, ScreenRegion(20, 80, 80, 20)),
            VisualToken("USD", 0.98, ScreenRegion(150, 80, 45, 20)),
        ),
    )

    resolved = vision.resolve(
        RenderedFieldValueCandidate(strategy="rendered_field_value", label="Currency"),
        blank_png(),
        Viewport(400, 240),
    )

    assert resolved.region == ScreenRegion(150, 80, 45, 20)
    assert resolved.method == "rendered_field_value"


def test_rendered_field_value_unions_every_token_in_the_selected_value(tmp_path: Path) -> None:
    frame = blank_png()
    vision = semantic_vision(
        tmp_path,
        (
            VisualToken("Available", 0.99, ScreenRegion(20, 80, 80, 20)),
            VisualToken("balance", 0.99, ScreenRegion(105, 80, 65, 20)),
            VisualToken("USD", 0.98, ScreenRegion(210, 80, 35, 20)),
            VisualToken("1,420.57", 0.97, ScreenRegion(250, 80, 75, 20)),
        ),
    )

    resolved = vision.resolve(
        RenderedFieldValueCandidate(strategy="rendered_field_value", label="Available balance"),
        frame,
        Viewport(400, 240),
    )

    assert resolved.region == ScreenRegion(210, 80, 115, 20)
    assert (
        vision.extract(frame, resolved.region, expected_frame_hash=resolved.frame_hash)
        == "USD 1,420.57"
    )


@pytest.mark.parametrize("scale", [0.75, 1.0, 1.5])
def test_field_value_ignores_left_navigation_on_the_same_baseline(
    tmp_path: Path, scale: float
) -> None:
    def region(x: int, y: int, width: int, height: int) -> ScreenRegion:
        return ScreenRegion(*(round(value * scale) for value in (x, y, width, height)))

    value = region(370, 80, 110, 20)
    vision = semantic_vision(
        tmp_path,
        (
            VisualToken("Navigation", 0.99, region(10, 80, 100, 20)),
            VisualToken("Valid until", 0.99, region(170, 80, 100, 20)),
            VisualToken("2026-10-02", 0.99, value),
            VisualToken("Another label", 0.99, region(170, 115, 120, 20)),
        ),
    )
    frame = blank_png(round(600 * scale), round(240 * scale))
    target = vision.resolve(
        RenderedFieldValueCandidate(strategy="rendered_field_value", label="Valid until"),
        frame,
        Viewport(round(600 * scale), round(240 * scale)),
    )
    assert target.region == value
    assert vision.extract(frame, target.region) == "2026-10-02"


def test_field_value_expands_beyond_a_label_only_table_column(tmp_path: Path) -> None:
    label = VisualToken("Total", 0.99, ScreenRegion(30, 100, 50, 20))
    value = VisualToken("42.50", 0.99, ScreenRegion(210, 100, 70, 20))
    previous_label = VisualToken("Subtotal", 0.99, ScreenRegion(30, 65, 80, 20))
    frame = blank_png()
    vision = semantic_vision(tmp_path, (label, value, previous_label))
    graph = VisualLayoutGraph(
        vision.frame_hash(frame),
        Viewport(400, 240),
        20,
        (
            VisualNode("column", "container", ScreenRegion(20, 55, 160, 80)),
            VisualNode("viewport", "container", ScreenRegion(0, 0, 400, 240)),
        ),
        (),
    )
    vision._graph_cache[graph.frame_hash] = graph
    target = vision.resolve(
        RenderedFieldValueCandidate(strategy="rendered_field_value", label="Total"),
        frame,
        graph.viewport,
    )
    assert target.region == value.region


def test_field_value_does_not_escape_a_plausible_stacked_group(tmp_path: Path) -> None:
    vision = semantic_vision(tmp_path, ())
    graph = VisualLayoutGraph(
        "frame",
        Viewport(400, 240),
        20,
        (
            VisualNode("card", "container", ScreenRegion(10, 40, 150, 100)),
            VisualNode("viewport", "container", ScreenRegion(0, 0, 400, 240)),
        ),
        (),
    )
    with pytest.raises(SurfaceError, match="competing field value layouts"):
        vision._field_value_tokens(
            graph,
            ScreenRegion(20, 50, 80, 20),
            (
                VisualToken("Stacked value", 0.99, ScreenRegion(20, 82, 100, 20)),
                VisualToken("Neighbor", 0.99, ScreenRegion(250, 50, 80, 20)),
            ),
        )


def test_rendered_field_value_unions_a_stacked_value_line(tmp_path: Path) -> None:
    vision = semantic_vision(
        tmp_path,
        (
            VisualToken("Account", 0.99, ScreenRegion(20, 50, 65, 20)),
            VisualToken("owner", 0.99, ScreenRegion(90, 50, 55, 20)),
            VisualToken("Ada", 0.98, ScreenRegion(20, 82, 35, 20)),
            VisualToken("Lovelace", 0.98, ScreenRegion(60, 82, 75, 20)),
        ),
    )

    resolved = vision.resolve(
        RenderedFieldValueCandidate(strategy="rendered_field_value", label="Account owner"),
        blank_png(),
        Viewport(400, 240),
    )

    assert resolved.region == ScreenRegion(20, 82, 115, 20)


def test_rendered_field_value_rejects_duplicate_labels(tmp_path: Path) -> None:
    vision = semantic_vision(
        tmp_path,
        (
            VisualToken("Currency", 0.99, ScreenRegion(20, 80, 80, 20)),
            VisualToken("Currency", 0.99, ScreenRegion(20, 140, 80, 20)),
            VisualToken("USD", 0.98, ScreenRegion(150, 80, 45, 20)),
        ),
    )

    with pytest.raises(SurfaceError) as error:
        vision.resolve(
            RenderedFieldValueCandidate(strategy="rendered_field_value", label="Currency"),
            b"duplicate-field-frame",
            Viewport(400, 240),
        )

    assert error.value.code == "target_ambiguous"


def test_labeled_control_rejects_two_controls_in_the_same_structural_row(
    tmp_path: Path,
) -> None:
    image = np.full((240, 400, 3), 245, dtype=np.uint8)
    cv2.rectangle(image, (135, 80), (245, 120), (20, 80, 140), 3)
    cv2.rectangle(image, (270, 80), (390, 120), (20, 80, 140), 3)
    ok, encoded = cv2.imencode(".png", image)
    assert ok
    vision = semantic_vision(
        tmp_path, (VisualToken("Member ID", 0.99, ScreenRegion(20, 90, 90, 20)),)
    )

    with pytest.raises(SurfaceError) as error:
        vision.resolve(
            RenderedLabeledControlCandidate(
                strategy="rendered_labeled_control", label="Member ID", control_kind="text_input"
            ),
            encoded.tobytes(),
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


def test_ocr_relative_reconstructs_split_anchor_and_target_phrases(tmp_path: Path) -> None:
    tokens = (
        VisualToken("Primary", 0.99, ScreenRegion(20, 50, 55, 20)),
        VisualToken("Checking", 0.98, ScreenRegion(82, 50, 70, 20)),
        VisualToken("Open", 0.97, ScreenRegion(260, 50, 45, 20)),
        VisualToken("card", 0.96, ScreenRegion(312, 50, 38, 20)),
    )
    vision = semantic_vision(tmp_path, tokens)

    resolved = vision.resolve(
        OcrRelativeCandidate(
            strategy="ocr_relative",
            anchor="Primary Checking",
            target_text="Open card",
            relation="same_row",
        ),
        blank_png(),
        Viewport(400, 240),
    )

    assert resolved.region == ScreenRegion(260, 50, 90, 20)


def test_ocr_relative_semantic_fallback_remains_fail_closed(tmp_path: Path) -> None:
    tokens = (
        VisualToken("Primary", 0.99, ScreenRegion(20, 50, 55, 20)),
        VisualToken("Checking", 0.98, ScreenRegion(82, 50, 70, 20)),
        VisualToken("Open", 0.97, ScreenRegion(240, 50, 45, 20)),
        VisualToken("card", 0.96, ScreenRegion(292, 50, 38, 20)),
        VisualToken("Open", 0.97, ScreenRegion(340, 50, 45, 20)),
        VisualToken("card", 0.96, ScreenRegion(392, 50, 38, 20)),
    )
    vision = semantic_vision(tmp_path, tokens)

    with pytest.raises(SurfaceError) as error:
        vision.resolve(
            OcrRelativeCandidate(
                strategy="ocr_relative",
                anchor="Primary Checking",
                target_text="Open card",
                relation="same_row",
            ),
            blank_png(width=500),
            Viewport(500, 240),
        )

    assert error.value.code == "target_ambiguous"


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


def test_rendered_group_image_uses_semantic_row_context(tmp_path: Path) -> None:
    frame = png_with_icons((40, 20), (180, 100), (300, 180))
    recognizer_tokens = (VisualToken("Savings", 0.99, ScreenRegion(10, 105, 75, 20)),)
    vision = semantic_vision(tmp_path, recognizer_tokens)
    key, digest = vision.create_visual_signature(frame, ScreenRegion(180, 100, 61, 51))

    resolved = vision.resolve(
        RenderedGroupImageCandidate(
            strategy="rendered_group_image",
            group_label="Savings",
            asset_key=key,
            content_hash=digest,
        ),
        frame,
        Viewport(400, 240),
    )

    assert resolved.method == "rendered_group_image"
    assert resolved.region.x == 176
    assert resolved.region.y == 96


def test_rendered_group_image_rejects_competing_components(tmp_path: Path) -> None:
    frame = png_with_icons((160, 100), (300, 100))
    vision = semantic_vision(
        tmp_path, (VisualToken("Savings", 0.99, ScreenRegion(10, 105, 75, 20)),)
    )
    key, digest = vision.create_visual_signature(frame, ScreenRegion(160, 100, 61, 51))

    with pytest.raises(SurfaceError) as error:
        vision.resolve(
            RenderedGroupImageCandidate(
                strategy="rendered_group_image",
                group_label="Savings",
                asset_key=key,
                content_hash=digest,
            ),
            frame,
            Viewport(400, 240),
        )

    assert error.value.code == "target_ambiguous"


def test_rendered_group_image_is_scoped_to_the_smallest_detected_container(
    tmp_path: Path,
) -> None:
    image = np.full((260, 400, 3), 245, dtype=np.uint8)
    for row_y in (25, 145):
        cv2.rectangle(image, (10, row_y), (340, row_y + 85), (90, 100, 115), 2)
        cv2.rectangle(image, (270, row_y + 16), (325, row_y + 70), (20, 80, 140), 3)
        cv2.line(
            image,
            (288, row_y + 32),
            (307, row_y + 43),
            (20, 80, 140),
            4,
        )
        cv2.line(
            image,
            (307, row_y + 43),
            (288, row_y + 56),
            (20, 80, 140),
            4,
        )
    ok, encoded = cv2.imencode(".png", image)
    assert ok
    frame = encoded.tobytes()
    vision = semantic_vision(
        tmp_path,
        (
            VisualToken("Savings", 0.99, ScreenRegion(25, 50, 70, 20)),
            VisualToken("0421", 0.98, ScreenRegion(120, 50, 45, 20)),
            VisualToken("Checking", 0.99, ScreenRegion(25, 170, 80, 20)),
            VisualToken("0110", 0.98, ScreenRegion(120, 170, 45, 20)),
        ),
    )
    key, digest = vision.create_visual_signature(frame, ScreenRegion(270, 41, 56, 55))

    resolved = vision.resolve(
        RenderedGroupImageCandidate(
            strategy="rendered_group_image",
            group_label="Savings",
            asset_key=key,
            content_hash=digest,
        ),
        frame,
        Viewport(400, 260),
    )

    assert resolved.region.y < 120


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
    content = png_with_icon(40, 70)
    key, digest = store.write(content)

    assert store.write(content) == (key, digest)
    assert not tuple(tmp_path.glob("*.tmp"))

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


def test_phrase_assembly_does_not_merge_adjacent_text_lines(tmp_path: Path) -> None:
    vision = semantic_vision(
        tmp_path,
        (
            VisualToken("First", 0.99, ScreenRegion(20, 50, 45, 20)),
            VisualToken("Second", 0.99, ScreenRegion(20, 70, 60, 20)),
        ),
    )

    with pytest.raises(SurfaceError) as error:
        vision.resolve(
            RenderedTextCandidate(strategy="rendered_text", value="First Second"),
            b"two-lines",
            Viewport(400, 240),
        )

    assert error.value.code == "target_absent"


def test_semantic_grounding_rejects_png_dimensions_before_ocr(tmp_path: Path) -> None:
    policy = load_vision_policy(Path("config/vision-policy.yaml"))
    frame = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8 + (4000).to_bytes(4, "big") * 2
    vision = VisionGrounder(StubRecognizer(()), LocalCapabilityAssetStore(tmp_path), policy)

    with pytest.raises(SurfaceError) as error:
        vision.resolve(
            RenderedTextCandidate(strategy="rendered_text", value="anything"),
            frame,
            Viewport(4000, 4000),
        )

    assert error.value.code == "visual_frame_budget_exceeded"


def test_semantic_text_grounding_enforces_the_time_budget(tmp_path: Path) -> None:
    @dataclass(frozen=True)
    class SlowRecognizer:
        def recognize(self, png: bytes) -> tuple[VisualToken, ...]:
            del png
            sleep(0.005)
            return (VisualToken("Search", 0.99, ScreenRegion(20, 20, 60, 20)),)

    policy = load_vision_policy(Path("config/vision-policy.yaml"))
    policy = policy.model_copy(
        update={"budgets": policy.budgets.model_copy(update={"maximum_grounding_milliseconds": 1})}
    )
    vision = VisionGrounder(SlowRecognizer(), LocalCapabilityAssetStore(tmp_path), policy)

    with pytest.raises(SurfaceError) as error:
        vision.resolve(
            RenderedTextCandidate(strategy="rendered_text", value="Search"),
            b"slow-frame",
            Viewport(400, 240),
        )

    assert error.value.code == "visual_grounding_budget_exceeded"


def test_extract_rejects_a_region_resolved_from_another_frame(tmp_path: Path) -> None:
    token = VisualToken("USD", 0.99, ScreenRegion(20, 20, 40, 20))
    vision = semantic_vision(tmp_path, (token,))

    with pytest.raises(SurfaceError) as error:
        vision.extract(
            b"current-frame",
            token.region,
            expected_frame_hash=vision.frame_hash(b"previous-frame"),
        )

    assert error.value.code == "visual_frame_changed"
