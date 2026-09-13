"""Deterministic OCR and image-anchor grounding over rendered surface frames."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from math import exp
from time import monotonic
from typing import Protocol, cast

import cv2
import numpy as np
from rapidocr import RapidOCR

from replayforge.capabilities.assets import CapabilityAssetError, CapabilityAssetStore
from replayforge.capabilities.models import (
    ImageAnchorCandidate,
    MatchMode,
    NormalizedRegion,
    OcrRelativeCandidate,
    OcrTextCandidate,
    RelativeRegion,
    RenderedFieldValueCandidate,
    RenderedGroupImageCandidate,
    RenderedLabeledControlCandidate,
    RenderedTextCandidate,
    VisualLocatorCandidate,
)
from replayforge.runtime.vision_policy import VisionGroundingPolicy
from replayforge.surfaces.models import (
    ScreenRegion,
    SurfaceError,
    Viewport,
    VisualTargetData,
    VisualToken,
)


class TextRecognizer(Protocol):
    def recognize(self, png: bytes) -> tuple[VisualToken, ...]: ...


@dataclass(frozen=True, slots=True)
class _VisualComponent:
    region: ScreenRegion
    edge: np.ndarray


@dataclass(slots=True)
class RapidOcrTextRecognizer:
    """Lazy local OCR adapter; model initialization never performs a network call."""

    _engine: RapidOCR | None = field(default=None, init=False, repr=False)

    def recognize(self, png: bytes) -> tuple[VisualToken, ...]:
        if self._engine is None:
            self._engine = RapidOCR()
        result = cast(object, self._engine(png))
        boxes = getattr(result, "boxes", None)
        texts = getattr(result, "txts", None)
        scores = getattr(result, "scores", None)
        if boxes is None or texts is None or scores is None:
            return ()
        tokens: list[VisualToken] = []
        for polygon, text, score in zip(boxes, texts, scores, strict=True):
            points = np.asarray(polygon, dtype=np.float32)
            x1, y1 = np.floor(points.min(axis=0)).astype(int)
            x2, y2 = np.ceil(points.max(axis=0)).astype(int)
            if text.strip() and x2 > x1 and y2 > y1:
                tokens.append(
                    VisualToken(
                        text=text.strip(),
                        confidence=float(score),
                        region=ScreenRegion(int(x1), int(y1), int(x2 - x1), int(y2 - y1)),
                    )
                )
        return tuple(tokens)


@dataclass(slots=True)
class VisionGrounder:
    recognizer: TextRecognizer
    assets: CapabilityAssetStore
    policy: VisionGroundingPolicy | None = None
    _ocr_cache: dict[str, tuple[VisualToken, ...]] = field(default_factory=dict, init=False)

    def resolve(
        self, candidate: VisualLocatorCandidate, png: bytes, viewport: Viewport
    ) -> VisualTargetData:
        frame_hash = self.frame_hash(png)
        if isinstance(candidate, OcrTextCandidate):
            matches = self._matching_tokens(
                png,
                candidate.value,
                candidate.match,
                candidate.minimum_confidence,
                candidate.search_region,
                viewport,
            )
            return self._unique_text_target(matches, "ocr_text", frame_hash)
        if isinstance(candidate, OcrRelativeCandidate):
            anchors = self._matching_tokens(
                png,
                candidate.anchor,
                candidate.anchor_match,
                candidate.minimum_confidence,
                candidate.search_region,
                viewport,
            )
            if len(anchors) != 1:
                raise self._cardinality_error(len(anchors), "OCR anchor")
            anchor = anchors[0]
            if candidate.target_text is not None:
                targets = self._matching_tokens(
                    png,
                    candidate.target_text,
                    MatchMode.EXACT,
                    candidate.minimum_confidence,
                    candidate.search_region,
                    viewport,
                )
                related = tuple(
                    token
                    for token in targets
                    if self._has_relation(anchor, token, candidate.relation)
                )
                return self._unique_text_target(related, "ocr_relative_text", frame_hash)
            assert candidate.relative_region is not None
            region = self._relative_region(anchor.region, candidate.relative_region, viewport)
            return VisualTargetData(region, "ocr_relative_region", anchor.confidence, frame_hash)
        if isinstance(candidate, ImageAnchorCandidate):
            return self._match_template(candidate, png, viewport, frame_hash)
        if isinstance(candidate, RenderedTextCandidate):
            matches = self._semantic_matching_tokens(png, candidate.value, candidate.match)
            return self._unique_text_target(matches, "rendered_text", frame_hash)
        if isinstance(candidate, RenderedLabeledControlCandidate):
            return self._resolve_labeled_control(candidate, png, viewport, frame_hash)
        if isinstance(candidate, RenderedFieldValueCandidate):
            return self._resolve_field_value(candidate, png, frame_hash)
        if isinstance(candidate, RenderedGroupImageCandidate):
            return self._resolve_group_image(candidate, png, viewport, frame_hash)
        raise TypeError("unsupported visual locator candidate")

    def tokens(self, png: bytes) -> tuple[VisualToken, ...]:
        return self._tokens(png)

    def extract(self, png: bytes, region: ScreenRegion, minimum_confidence: float = 0.75) -> str:
        tokens = [
            token
            for token in self._tokens(png)
            if token.confidence >= minimum_confidence and self._center_in(token.region, region)
        ]
        tokens.sort(key=lambda token: (token.region.y, token.region.x))
        if not tokens:
            raise SurfaceError(
                "visual_text_absent",
                "No readable text was found inside the resolved visual region.",
                effect_absent=True,
            )
        return " ".join(token.text for token in tokens)

    def contains_text(
        self,
        png: bytes,
        value: str,
        match: MatchMode,
        minimum_confidence: float,
        search_region: NormalizedRegion | None,
        viewport: Viewport,
    ) -> bool:
        return bool(
            self._matching_tokens(png, value, match, minimum_confidence, search_region, viewport)
        )

    def contains_rendered_text(self, png: bytes, value: str, match: MatchMode) -> bool:
        return bool(self._semantic_matching_tokens(png, value, match))

    def create_edge_template(self, png: bytes, region: ScreenRegion) -> tuple[str, str]:
        image = self._decode(png)
        clipped = self._clip_region(region, Viewport(image.shape[1], image.shape[0]))
        crop = image[
            clipped.y : clipped.y + clipped.height,
            clipped.x : clipped.x + clipped.width,
        ]
        edge = self._edge_map(crop)
        if edge.size < 64 or int(np.count_nonzero(edge)) < 12:
            raise SurfaceError(
                "template_low_information",
                "The visual target does not contain enough stable structure for a template.",
                effect_absent=True,
            )
        success, encoded = cv2.imencode(".png", edge)
        if not success:
            raise SurfaceError(
                "template_encoding_failed", "The visual template could not be encoded."
            )
        try:
            return self.assets.write(encoded.tobytes())
        except CapabilityAssetError as error:
            raise SurfaceError(
                "template_storage_failed", "The visual template could not be stored."
            ) from error

    def create_visual_signature(self, png: bytes, region: ScreenRegion) -> tuple[str, str]:
        """Store a cropped, normalized component signature for semantic image matching."""

        policy = self._required_policy()
        image = self._decode(png)
        clipped = self._clip_region(region, Viewport(image.shape[1], image.shape[0]))
        components = self._visual_components(png, Viewport(image.shape[1], image.shape[0]))
        matching_components = [
            component
            for component in components
            if self._region_iou(clipped, component.region) > 0.05
        ]
        if matching_components:
            clipped = max(
                matching_components,
                key=lambda component: self._region_iou(clipped, component.region),
            ).region
        crop = image[
            clipped.y : clipped.y + clipped.height,
            clipped.x : clipped.x + clipped.width,
        ]
        edge = self._trim_edges(self._edge_map(crop))
        if edge.size < 64 or int(np.count_nonzero(edge)) < 12:
            raise SurfaceError(
                "template_low_information",
                "The visual target does not contain enough stable structure for a signature.",
                effect_absent=True,
            )
        normalized = self._normalize_signature(edge, policy)
        success, encoded = cv2.imencode(".png", normalized)
        if not success:
            raise SurfaceError(
                "template_encoding_failed", "The visual signature could not be encoded."
            )
        try:
            return self.assets.write(encoded.tobytes())
        except CapabilityAssetError as error:
            raise SurfaceError(
                "template_storage_failed", "The visual signature could not be stored."
            ) from error

    @staticmethod
    def frame_hash(png: bytes) -> str:
        return f"sha256:{hashlib.sha256(png).hexdigest()}"

    def _tokens(self, png: bytes) -> tuple[VisualToken, ...]:
        key = self.frame_hash(png)
        if key not in self._ocr_cache:
            if len(self._ocr_cache) >= 8:
                self._ocr_cache.pop(next(iter(self._ocr_cache)))
            self._ocr_cache[key] = self.recognizer.recognize(png)
        return self._ocr_cache[key]

    def _matching_tokens(
        self,
        png: bytes,
        value: str,
        match: MatchMode,
        minimum_confidence: float,
        search_region: NormalizedRegion | None,
        viewport: Viewport,
    ) -> tuple[VisualToken, ...]:
        region = self._normalized_region(search_region, viewport) if search_region else None
        expected = self._normalize(value)
        matches: list[VisualToken] = []
        for token in self._tokens(png):
            if token.confidence < minimum_confidence or (
                region is not None and not self._center_in(token.region, region)
            ):
                continue
            observed = self._normalize(token.text)
            matched = (
                observed == expected
                if match is MatchMode.EXACT
                else expected in observed
                if match is MatchMode.CONTAINS
                else re.fullmatch(value, token.text) is not None
            )
            if matched:
                matches.append(token)
        return tuple(matches)

    @staticmethod
    def _unique_text_target(
        matches: tuple[VisualToken, ...], method: str, frame_hash: str
    ) -> VisualTargetData:
        if len(matches) != 1:
            raise VisionGrounder._cardinality_error(len(matches), "visual text")
        token = matches[0]
        return VisualTargetData(token.region, method, token.confidence, frame_hash)

    @staticmethod
    def _cardinality_error(count: int, target: str) -> SurfaceError:
        return SurfaceError(
            "target_ambiguous" if count > 1 else "target_absent",
            f"{target} did not resolve exactly once.",
            recoverable=count == 0,
            effect_absent=True,
            expected={"count": 1},
            observed={"count": count},
        )

    def _resolve_labeled_control(
        self,
        candidate: RenderedLabeledControlCandidate,
        png: bytes,
        viewport: Viewport,
        frame_hash: str,
    ) -> VisualTargetData:
        labels = self._semantic_matching_tokens(png, candidate.label, candidate.label_match)
        if len(labels) != 1:
            raise self._cardinality_error(len(labels), "rendered control label")
        label = labels[0]
        components = self._visual_components(png, viewport)
        median_height = self._median_text_height(self._tokens(png))
        # Ignore text glyph contours and the enclosing card. The group image
        # signature describes a visual component with dimensions comparable to
        # the label height, not the entire responsive row.
        components = tuple(
            component
            for component in components
            if component.region.height >= median_height * 1.8
            and component.region.width >= median_height * 1.8
            and self._region_iou(component.region, label.region) == 0
        )
        # Edge maps also contain the glyphs that formed the label and any
        # placeholder text inside the control. A text input is the larger
        # frame-local component, so reject text-sized contours before applying
        # the label-to-control relationship.
        components = tuple(
            component
            for component in components
            if component.region.height >= median_height * 1.8
            and component.region.width >= median_height * 4
            and self._region_iou(component.region, label.region) == 0
        )
        related = [
            (self._control_relation(label.region, component.region, median_height), component)
            for component in components
        ]
        related = [(rank, component) for rank, component in related if rank is not None]
        if not related:
            raise SurfaceError(
                "target_absent",
                "No visual control was associated with the rendered label.",
                recoverable=True,
                effect_absent=True,
            )
        related.sort(key=lambda item: (item[0], item[1].region.x, item[1].region.y))
        best_rank = related[0][0]
        best = [component for rank, component in related if rank == best_rank]
        if len(best) != 1:
            raise self._cardinality_error(len(best), "rendered labeled control")
        return VisualTargetData(
            best[0].region, "rendered_labeled_control", label.confidence, frame_hash
        )

    def _resolve_field_value(
        self,
        candidate: RenderedFieldValueCandidate,
        png: bytes,
        frame_hash: str,
    ) -> VisualTargetData:
        policy = self._required_policy()
        labels = self._semantic_matching_tokens(png, candidate.label, candidate.label_match)
        if len(labels) != 1:
            raise self._cardinality_error(len(labels), "rendered field label")
        label = labels[0]
        tokens = [
            token
            for token in self._tokens(png)
            if token.confidence >= policy.ocr.minimum_confidence
            and not self._regions_equal(token.region, label.region)
        ]
        median_height = self._median_text_height(tokens)
        related = [
            (self._value_relation(label.region, token.region, median_height), token)
            for token in tokens
        ]
        related = [(rank, token) for rank, token in related if rank is not None]
        if not related:
            raise SurfaceError(
                "target_absent",
                "No visual field value was associated with the rendered label.",
                recoverable=True,
                effect_absent=True,
            )
        related.sort(key=lambda item: (item[0], item[1].region.x, item[1].region.y))
        best_rank = related[0][0]
        best = [token for rank, token in related if rank == best_rank]
        if len(best) != 1:
            raise self._cardinality_error(len(best), "rendered field value")
        return VisualTargetData(
            best[0].region, "rendered_field_value", best[0].confidence, frame_hash
        )

    def _resolve_group_image(
        self,
        candidate: RenderedGroupImageCandidate,
        png: bytes,
        viewport: Viewport,
        frame_hash: str,
    ) -> VisualTargetData:
        policy = self._required_policy()
        started = monotonic()
        labels = self._semantic_matching_tokens(
            png, candidate.group_label, candidate.group_label_match
        )
        if len(labels) != 1:
            raise self._cardinality_error(len(labels), "rendered image group label")
        label = labels[0]
        components = self._visual_components(png, viewport)
        median_height = self._median_text_height(self._tokens(png))
        related = [
            (self._image_relation(label.region, component.region, median_height), component)
            for component in components
        ]
        related = [(rank, component) for rank, component in related if rank is not None]
        if not related:
            raise SurfaceError(
                "target_absent",
                "No visual component was found in the labeled group.",
                recoverable=True,
                effect_absent=True,
            )
        try:
            content = self.assets.read(candidate.asset_key, candidate.content_hash)
        except CapabilityAssetError as error:
            raise SurfaceError(
                "template_integrity_failed",
                "The visual signature is missing or failed integrity verification.",
                effect_absent=True,
            ) from error
        template = cv2.imdecode(np.frombuffer(content, np.uint8), cv2.IMREAD_GRAYSCALE)
        if template is None:
            raise SurfaceError("template_invalid", "The visual signature could not be decoded.")
        normalized_template = self._normalize_signature(template, policy)
        relation_ranks: list[tuple[int, float]] = [
            rank for rank, _component in related if rank is not None
        ]
        best_relation_tier = min(rank[0] for rank in relation_ranks)
        scored = [
            (self._signature_score(normalized_template, component.edge, policy), component)
            for rank, component in related
            if rank is not None and rank[0] == best_relation_tier
        ]
        scored.sort(key=lambda item: item[0], reverse=True)
        if not scored:
            raise SurfaceError(
                "target_absent", "No visual component could be compared.", effect_absent=True
            )
        best_score, best_component = scored[0]
        second_score = scored[1][0] if len(scored) > 1 else 0.0
        if best_score < policy.image.minimum_similarity:
            raise SurfaceError(
                "target_absent",
                "No visual component met the global similarity floor.",
                recoverable=True,
                effect_absent=True,
                observed={"score": round(best_score, 4)},
            )
        if len(scored) > 1 and best_score - second_score < policy.image.uniqueness_margin:
            raise SurfaceError(
                "target_ambiguous",
                "The semantic group contains multiple equally similar visual components.",
                effect_absent=True,
                observed={
                    "best_score": round(best_score, 4),
                    "second_score": round(second_score, 4),
                },
            )
        if (monotonic() - started) * 1000 > policy.budgets.maximum_grounding_milliseconds:
            raise SurfaceError(
                "visual_grounding_budget_exceeded",
                "Visual grounding exceeded its bounded execution budget.",
                effect_absent=True,
            )
        return VisualTargetData(
            best_component.region, "rendered_group_image", best_score, frame_hash
        )

    def _semantic_matching_tokens(
        self, png: bytes, value: str, match: MatchMode
    ) -> tuple[VisualToken, ...]:
        policy = self._required_policy()
        expected = self._normalize(value)
        matches: list[VisualToken] = []
        for token in self._semantic_phrases(png, policy):
            observed = self._normalize(token.text)
            matched = (
                observed == expected
                if match is MatchMode.EXACT
                else expected in observed
                if match is MatchMode.CONTAINS
                else re.fullmatch(value, token.text) is not None
            )
            if matched:
                matches.append(token)
        unique: dict[tuple[str, ScreenRegion], VisualToken] = {}
        for token in matches:
            unique[(self._normalize(token.text), token.region)] = token
        return tuple(unique.values())

    def _semantic_phrases(
        self, png: bytes, policy: VisionGroundingPolicy
    ) -> tuple[VisualToken, ...]:
        tokens = [
            token
            for token in self._tokens(png)
            if token.confidence >= policy.ocr.minimum_confidence
        ]
        if not tokens:
            return ()
        median_height = self._median_text_height(tokens)
        lines: list[list[VisualToken]] = []
        for token in sorted(tokens, key=lambda item: (item.region.y, item.region.x)):
            matching_line = next(
                (
                    line
                    for line in lines
                    if self._same_text_line(token.region, line[0].region, median_height)
                ),
                None,
            )
            if matching_line is None:
                lines.append([token])
            else:
                matching_line.append(token)
        phrases: list[VisualToken] = []
        for line in lines:
            line.sort(key=lambda item: item.region.x)
            for start in range(len(line)):
                words: list[str] = []
                region: ScreenRegion | None = None
                confidence = 1.0
                for end in range(start, len(line)):
                    if end > start:
                        previous = line[end - 1].region
                        current = line[end].region
                        if current.x - (previous.x + previous.width) > (
                            policy.phrases.maximum_line_gap_in_text_heights * median_height
                        ):
                            break
                    words.append(line[end].text)
                    region = self._union_region(region, line[end].region)
                    confidence = min(confidence, line[end].confidence)
                    assert region is not None
                    phrases.append(VisualToken(" ".join(words), confidence, region))
        return tuple(phrases)

    def _visual_components(self, png: bytes, viewport: Viewport) -> tuple[_VisualComponent, ...]:
        policy = self._required_policy()
        image = self._decode(png)
        if image.shape[1] * image.shape[0] > policy.budgets.maximum_frame_pixels:
            raise SurfaceError(
                "visual_frame_budget_exceeded",
                "The rendered frame exceeds the visual grounding budget.",
                effect_absent=True,
            )
        edge = self._edge_map(image)
        dilated = cv2.dilate(edge, np.ones((3, 3), dtype=np.uint8), iterations=1)
        contours, _ = cv2.findContours(dilated, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        image_area = max(1, image.shape[0] * image.shape[1])
        min_area = image_area * policy.segmentation.minimum_component_area_ratio
        max_area = image_area * policy.segmentation.maximum_component_area_ratio
        boxes: list[ScreenRegion] = []
        for contour in contours[: policy.segmentation.maximum_components]:
            x, y, width, height = cv2.boundingRect(contour)
            area = width * height
            if area < min_area or area > max_area or width < 4 or height < 4:
                continue
            boxes.append(ScreenRegion(x, y, width, height))
        boxes.sort(key=lambda region: region.width * region.height, reverse=True)
        distinct: list[ScreenRegion] = []
        for box in boxes:
            if any(self._region_iou(box, existing) > 0.5 for existing in distinct):
                continue
            distinct.append(box)
        return tuple(
            _VisualComponent(
                region=box,
                edge=self._trim_edges(edge[box.y : box.y + box.height, box.x : box.x + box.width]),
            )
            for box in distinct
        )

    @staticmethod
    def _control_relation(
        label: ScreenRegion, component: ScreenRegion, median_height: float
    ) -> tuple[int, float] | None:
        vertical_overlap = VisionGrounder._overlap_length(
            label.y, label.y + label.height, component.y, component.y + component.height
        )
        if vertical_overlap >= min(label.height, component.height) * 0.5 and component.x >= label.x:
            return 0, max(0.0, component.x - (label.x + label.width)) / max(1.0, median_height)
        horizontal_overlap = VisionGrounder._overlap_length(
            label.x, label.x + label.width, component.x, component.x + component.width
        )
        gap = component.y - (label.y + label.height)
        if horizontal_overlap > 0 and gap >= 0 and gap <= 6 * median_height:
            return 1, gap / max(1.0, median_height)
        return None

    @staticmethod
    def _value_relation(
        label: ScreenRegion, value: ScreenRegion, median_height: float
    ) -> tuple[int, float] | None:
        vertical_overlap = VisionGrounder._overlap_length(
            label.y, label.y + label.height, value.y, value.y + value.height
        )
        if (
            vertical_overlap >= min(label.height, value.height) * 0.5
            and value.x >= label.x + label.width
        ):
            return 0, max(0.0, value.x - (label.x + label.width)) / max(1.0, median_height)
        horizontal_overlap = VisionGrounder._overlap_length(
            label.x, label.x + label.width, value.x, value.x + value.width
        )
        gap = value.y - (label.y + label.height)
        if horizontal_overlap > 0 and gap >= 0 and gap <= 6 * median_height:
            return 1, gap / max(1.0, median_height)
        return None

    @staticmethod
    def _image_relation(
        label: ScreenRegion, component: ScreenRegion, median_height: float
    ) -> tuple[int, float] | None:
        label_center_y = label.y + label.height / 2
        component_center_y = component.y + component.height / 2
        if (
            component.x >= label.x + label.width
            and abs(component_center_y - label_center_y) <= 2.5 * median_height
        ):
            return 0, max(0.0, component.x - (label.x + label.width)) / max(1.0, median_height)
        horizontal_overlap = VisionGrounder._overlap_length(
            label.x, label.x + label.width, component.x, component.x + component.width
        )
        gap = component.y - (label.y + label.height)
        if horizontal_overlap > 0 and gap >= 0 and gap <= 8 * median_height:
            return 1, gap / max(1.0, median_height)
        return None

    @staticmethod
    def _same_text_line(first: ScreenRegion, second: ScreenRegion, median_height: float) -> bool:
        overlap = VisionGrounder._overlap_length(
            first.y, first.y + first.height, second.y, second.y + second.height
        )
        return (
            overlap >= min(first.height, second.height) * 0.5
            or abs(first.y - second.y) <= median_height
        )

    @staticmethod
    def _overlap_length(
        first_start: int, first_end: int, second_start: int, second_end: int
    ) -> int:
        return max(0, min(first_end, second_end) - max(first_start, second_start))

    @staticmethod
    def _union_region(first: ScreenRegion | None, second: ScreenRegion) -> ScreenRegion:
        if first is None:
            return second
        left = min(first.x, second.x)
        top = min(first.y, second.y)
        right = max(first.x + first.width, second.x + second.width)
        bottom = max(first.y + first.height, second.y + second.height)
        return ScreenRegion(left, top, right - left, bottom - top)

    @staticmethod
    def _median_text_height(tokens: list[VisualToken] | tuple[VisualToken, ...]) -> float:
        heights = [token.region.height for token in tokens if token.region.height > 0]
        return float(np.median(heights)) if heights else 1.0

    @staticmethod
    def _regions_equal(first: ScreenRegion, second: ScreenRegion) -> bool:
        return first == second

    @staticmethod
    def _region_iou(first: ScreenRegion, second: ScreenRegion) -> float:
        left = max(first.x, second.x)
        top = max(first.y, second.y)
        right = min(first.x + first.width, second.x + second.width)
        bottom = min(first.y + first.height, second.y + second.height)
        intersection = max(0, right - left) * max(0, bottom - top)
        union = first.width * first.height + second.width * second.height - intersection
        return intersection / union if union else 0.0

    @staticmethod
    def _trim_edges(edge: np.ndarray) -> np.ndarray:
        points = cv2.findNonZero(edge)
        if points is None:
            return edge
        x, y, width, height = cv2.boundingRect(points)
        return edge[y : y + height, x : x + width]

    @staticmethod
    def _normalize_signature(edge: np.ndarray, policy: VisionGroundingPolicy) -> np.ndarray:
        trimmed = VisionGrounder._trim_edges(edge)
        canvas = np.zeros(
            (policy.image.canonical_height, policy.image.canonical_width), dtype=np.uint8
        )
        if trimmed.size == 0:
            return canvas
        scale = min(
            (policy.image.canonical_width - 4) / max(1, trimmed.shape[1]),
            (policy.image.canonical_height - 4) / max(1, trimmed.shape[0]),
        )
        width = max(1, round(trimmed.shape[1] * scale))
        height = max(1, round(trimmed.shape[0] * scale))
        resized = cv2.resize(trimmed, (width, height), interpolation=cv2.INTER_AREA)
        left = (canvas.shape[1] - width) // 2
        top = (canvas.shape[0] - height) // 2
        canvas[top : top + height, left : left + width] = resized
        return canvas

    @staticmethod
    def _signature_score(
        template: np.ndarray, candidate: np.ndarray, policy: VisionGroundingPolicy
    ) -> float:
        template_edges = VisionGrounder._normalize_signature(template, policy) > 0
        candidate_edges = VisionGrounder._normalize_signature(candidate, policy) > 0
        if not np.any(template_edges) or not np.any(candidate_edges):
            return 0.0
        intersection = np.count_nonzero(template_edges & candidate_edges)
        union = np.count_nonzero(template_edges | candidate_edges)
        overlap = int(intersection) / max(1, int(union))
        template_distance = cv2.distanceTransform(
            np.where(template_edges, 0, 255).astype(np.uint8), cv2.DIST_L2, 3
        )
        candidate_distance = cv2.distanceTransform(
            np.where(candidate_edges, 0, 255).astype(np.uint8), cv2.DIST_L2, 3
        )
        forward = float(candidate_distance[template_edges].mean())
        backward = float(template_distance[candidate_edges].mean())
        chamfer = exp(-((forward + backward) / 2) / max(1.0, template.shape[0] * 0.12))
        return float(0.6 * overlap + 0.4 * chamfer)

    def _required_policy(self) -> VisionGroundingPolicy:
        if self.policy is None:
            raise SurfaceError(
                "visual_policy_missing",
                "Semantic visual grounding requires a configured visual policy.",
                effect_absent=True,
            )
        return self.policy

    def _match_template(
        self,
        candidate: ImageAnchorCandidate,
        png: bytes,
        viewport: Viewport,
        frame_hash: str,
    ) -> VisualTargetData:
        image = self._edge_map(self._decode(png))
        if candidate.context_anchor is not None:
            assert candidate.relative_search_region is not None
            anchors = self._matching_tokens(
                png,
                candidate.context_anchor.value,
                candidate.context_anchor.match,
                candidate.context_anchor.minimum_confidence,
                candidate.context_anchor.search_region,
                viewport,
            )
            if len(anchors) != 1:
                raise self._cardinality_error(len(anchors), "visual context anchor")
            search = self._relative_region(
                anchors[0].region, candidate.relative_search_region, viewport
            )
        else:
            search = (
                self._normalized_region(candidate.search_region, viewport)
                if candidate.search_region
                else ScreenRegion(0, 0, viewport.width, viewport.height)
            )
        search = self._clip_region(search, Viewport(image.shape[1], image.shape[0]))
        haystack = image[search.y : search.y + search.height, search.x : search.x + search.width]
        try:
            content = self.assets.read(candidate.asset_key, candidate.content_hash)
        except CapabilityAssetError as error:
            raise SurfaceError(
                "template_integrity_failed",
                "The visual template is missing or failed integrity verification.",
                effect_absent=True,
            ) from error
        template = cv2.imdecode(np.frombuffer(content, np.uint8), cv2.IMREAD_GRAYSCALE)
        if template is None:
            raise SurfaceError("template_invalid", "The visual template could not be decoded.")
        scored: list[tuple[float, int, int, int, int]] = []
        peak_floor = candidate.minimum_score - candidate.uniqueness_margin
        scale = candidate.minimum_scale
        while scale <= candidate.maximum_scale + 1e-9:
            width = max(1, round(template.shape[1] * scale))
            height = max(1, round(template.shape[0] * scale))
            if width <= haystack.shape[1] and height <= haystack.shape[0]:
                resized = cv2.resize(template, (width, height), interpolation=cv2.INTER_AREA)
                response = cv2.matchTemplate(haystack, resized, cv2.TM_CCOEFF_NORMED)
                scored.extend(
                    self._template_peaks(
                        response,
                        width,
                        height,
                        peak_floor,
                    )
                )
            scale += candidate.scale_step
        if not scored:
            raise SurfaceError(
                "target_absent",
                "The visual template exceeded the search region.",
                effect_absent=True,
            )
        distinct: list[tuple[float, int, int, int, int]] = []
        for detection in sorted(scored, reverse=True):
            if any(self._same_template_location(detection, existing) for existing in distinct):
                continue
            distinct.append(detection)
        best = distinct[0]
        alternatives = distinct[1:]
        second_score = alternatives[0][0] if alternatives else 0.0
        if best[0] < candidate.minimum_score:
            raise SurfaceError(
                "target_absent",
                "No visual template met the confidence floor.",
                recoverable=True,
                effect_absent=True,
                observed={"score": round(best[0], 4)},
            )
        if best[0] - second_score < candidate.uniqueness_margin:
            raise SurfaceError(
                "target_ambiguous",
                "The visual template did not have a unique match.",
                effect_absent=True,
                observed={"best_score": round(best[0], 4), "second_score": round(second_score, 4)},
            )
        region = ScreenRegion(search.x + best[1], search.y + best[2], best[3], best[4])
        return VisualTargetData(region, "image_anchor", best[0], frame_hash)

    @staticmethod
    def _template_peaks(
        response: np.ndarray,
        width: int,
        height: int,
        floor: float,
        maximum_peaks: int = 10,
    ) -> list[tuple[float, int, int, int, int]]:
        """Return bounded, spatially distinct response peaks for one scale."""

        working = response.copy()
        peaks: list[tuple[float, int, int, int, int]] = []
        radius_x = max(1, width // 2)
        radius_y = max(1, height // 2)
        for _ in range(maximum_peaks):
            _minimum, maximum, _minimum_at, maximum_at = cv2.minMaxLoc(working)
            if maximum < floor:
                break
            x, y = maximum_at
            peaks.append((float(maximum), x, y, width, height))
            left = max(0, x - radius_x)
            right = min(working.shape[1], x + radius_x + 1)
            top = max(0, y - radius_y)
            bottom = min(working.shape[0], y + radius_y + 1)
            working[top:bottom, left:right] = -1.0
        return peaks

    @staticmethod
    def _same_template_location(
        first: tuple[float, int, int, int, int],
        second: tuple[float, int, int, int, int],
    ) -> bool:
        first_center = (first[1] + first[3] / 2, first[2] + first[4] / 2)
        second_center = (second[1] + second[3] / 2, second[2] + second[4] / 2)
        return (
            abs(first_center[0] - second_center[0]) <= max(first[3], second[3]) * 0.5
            and abs(first_center[1] - second_center[1]) <= max(first[4], second[4]) * 0.5
        )

    @staticmethod
    def _has_relation(anchor: VisualToken, target: VisualToken, relation: str) -> bool:
        a, t = anchor.region, target.region
        vertical_overlap = max(0, min(a.y + a.height, t.y + t.height) - max(a.y, t.y))
        same_row = vertical_overlap >= min(a.height, t.height) * 0.5
        if relation == "same_row":
            return same_row
        if relation == "right_of":
            return same_row and t.x >= a.x + a.width
        return t.y >= a.y + a.height

    @staticmethod
    def _relative_region(
        anchor: ScreenRegion, spec: RelativeRegion, viewport: Viewport
    ) -> ScreenRegion:
        unit = anchor.height
        region = ScreenRegion(
            max(0, round(anchor.x + spec.x * unit)),
            max(0, round(anchor.y + spec.y * unit)),
            max(1, round(spec.width * unit)),
            max(1, round(spec.height * unit)),
        )
        return VisionGrounder._clip_region(region, viewport)

    @staticmethod
    def _normalized_region(spec: NormalizedRegion, viewport: Viewport) -> ScreenRegion:
        return ScreenRegion(
            round(spec.x * viewport.width),
            round(spec.y * viewport.height),
            max(1, round(spec.width * viewport.width)),
            max(1, round(spec.height * viewport.height)),
        )

    @staticmethod
    def _clip_region(region: ScreenRegion, viewport: Viewport) -> ScreenRegion:
        x = max(0, min(region.x, viewport.width - 1))
        y = max(0, min(region.y, viewport.height - 1))
        return ScreenRegion(
            x, y, min(region.width, viewport.width - x), min(region.height, viewport.height - y)
        )

    @staticmethod
    def _center_in(inner: ScreenRegion, outer: ScreenRegion) -> bool:
        x, y = inner.center
        return outer.x <= x < outer.x + outer.width and outer.y <= y < outer.y + outer.height

    @staticmethod
    def _normalize(value: str) -> str:
        return " ".join(unicodedata.normalize("NFKC", value).casefold().split())

    @staticmethod
    def _decode(png: bytes) -> np.ndarray:
        image = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            raise SurfaceError("frame_invalid", "The rendered surface frame is not a valid image.")
        return cast(np.ndarray, image)

    @staticmethod
    def _edge_map(image: np.ndarray) -> np.ndarray:
        gray = image if image.ndim == 2 else cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        normalized = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
        return cast(np.ndarray, cv2.Canny(normalized, 60, 160))
