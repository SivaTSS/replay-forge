"""Deterministic OCR and image-anchor grounding over rendered surface frames."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from math import exp
from threading import Lock
from time import monotonic
from typing import Literal, Protocol, cast

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
from replayforge.surfaces.models import (
    ScreenRegion,
    SurfaceError,
    Viewport,
    VisualTargetData,
    VisualToken,
)
from replayforge.surfaces.vision_policy import VisionGroundingPolicy


class TextRecognizer(Protocol):
    def recognize(self, png: bytes) -> tuple[VisualToken, ...]: ...


# Runs already have their own workers. OpenCV's machine-wide default thread pool
# oversubscribes large hosts and can exhaust a grounding deadline on a small frame.
# Configure once at import, before any session workers are started.
cv2.setNumThreads(1)

VisualNodeKind = Literal["phrase", "control", "text_enclosure", "image", "container"]


@dataclass(frozen=True, slots=True)
class VisualNode:
    id: str
    kind: VisualNodeKind
    region: ScreenRegion
    edge: np.ndarray | None = field(default=None, compare=False, repr=False)
    token: VisualToken | None = None


@dataclass(frozen=True, slots=True)
class VisualLayoutGraph:
    frame_hash: str
    viewport: Viewport
    median_text_height: float
    nodes: tuple[VisualNode, ...]
    containment: tuple[tuple[str, str], ...]

    def of_kind(self, kind: VisualNodeKind) -> tuple[VisualNode, ...]:
        return tuple(node for node in self.nodes if node.kind == kind)


@dataclass(slots=True)
class RapidOcrTextRecognizer:
    """Lazy local OCR adapter; model files must be provisioned for offline startup."""

    inference_threads: int = 2
    _engine: RapidOCR | None = field(default=None, init=False, repr=False)
    _lock: Lock = field(default_factory=Lock, init=False, repr=False)

    def __post_init__(self) -> None:
        if type(self.inference_threads) is not int or not 1 <= self.inference_threads <= 4:
            raise ValueError("OCR inference threads must be between one and four")

    def recognize(self, png: bytes) -> tuple[VisualToken, ...]:
        # RapidOCR mutates its runtime parameters during calls; the recognizer is
        # shared by runs, so initialization and inference must have one owner.
        with self._lock:
            if self._engine is None:
                self._engine = RapidOCR(
                    params={
                        "EngineConfig.onnxruntime.intra_op_num_threads": self.inference_threads,
                        "EngineConfig.onnxruntime.inter_op_num_threads": 1,
                    }
                )
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
    _graph_cache: dict[str, VisualLayoutGraph] = field(default_factory=dict, init=False)

    def resolve(
        self, candidate: VisualLocatorCandidate, png: bytes, viewport: Viewport
    ) -> VisualTargetData:
        started = monotonic()
        if self.policy is not None:
            self._validate_frame_budget(png)
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
            if len(anchors) != 1 and self.policy is not None:
                anchors = self._semantic_matches_in_region(
                    png,
                    candidate.anchor,
                    candidate.anchor_match,
                    candidate.search_region,
                    viewport,
                    started,
                )
            if not anchors:
                raise self._cardinality_error(len(anchors), "OCR anchor")
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
                    if any(
                        self._has_relation(anchor, token, candidate.relation) for anchor in anchors
                    )
                )
                if len(related) != 1 and self.policy is not None:
                    semantic_targets = self._semantic_matches_in_region(
                        png,
                        candidate.target_text,
                        MatchMode.EXACT,
                        candidate.search_region,
                        viewport,
                        started,
                    )
                    related = tuple(
                        token
                        for token in semantic_targets
                        if any(
                            self._has_relation(anchor, token, candidate.relation)
                            for anchor in anchors
                        )
                    )
                if len(related) > 1 and self.policy is not None:
                    related = self._prefer_unique_control(related, png, viewport, started)
                return self._unique_text_target(related, "ocr_relative_text", frame_hash)
            assert candidate.relative_region is not None
            if len(anchors) != 1:
                raise self._cardinality_error(len(anchors), "OCR anchor")
            anchor = anchors[0]
            region = self._relative_region(anchor.region, candidate.relative_region, viewport)
            return VisualTargetData(region, "ocr_relative_region", anchor.confidence, frame_hash)
        if isinstance(candidate, ImageAnchorCandidate):
            return self._match_template(candidate, png, viewport, frame_hash)
        if isinstance(candidate, RenderedTextCandidate):
            matches = self._semantic_matching_tokens(png, candidate.value, candidate.match, started)
            if len(matches) > 1:
                matches = self._prefer_unique_control(matches, png, viewport, started)
            result = self._unique_text_target(matches, "rendered_text", frame_hash)
            self._check_deadline(started)
            return result
        if isinstance(candidate, RenderedLabeledControlCandidate):
            return self._resolve_labeled_control(candidate, png, viewport, frame_hash, started)
        if isinstance(candidate, RenderedFieldValueCandidate):
            return self._resolve_field_value(candidate, png, viewport, frame_hash, started)
        if isinstance(candidate, RenderedGroupImageCandidate):
            return self._resolve_group_image(candidate, png, viewport, frame_hash, started)
        raise TypeError("unsupported visual locator candidate")

    def _prefer_unique_control(
        self, matches: tuple[VisualToken, ...], png: bytes, viewport: Viewport, started: float
    ) -> tuple[VisualToken, ...]:
        """Distinguish a bounded action from repeated plain text, never by row ordinal."""
        graph = self._layout_graph(png, viewport, started)
        controls = tuple(
            control
            for control in (*graph.of_kind("control"), *graph.of_kind("text_enclosure"))
            if sum(self._center_in(match.region, control.region) for match in matches) == 1
        )
        contained = tuple(
            match
            for match in matches
            if any(self._center_in(match.region, control.region) for control in controls)
        )
        return contained if len(contained) == 1 else matches

    def tokens(self, png: bytes) -> tuple[VisualToken, ...]:
        return self._tokens(png)

    def extract(
        self,
        png: bytes,
        region: ScreenRegion,
        minimum_confidence: float = 0.75,
        expected_frame_hash: str | None = None,
    ) -> str:
        started = monotonic()
        if self.policy is not None:
            self._validate_frame_budget(png)
        if expected_frame_hash is not None and self.frame_hash(png) != expected_frame_hash:
            raise SurfaceError(
                "visual_frame_changed",
                "The rendered frame changed between target resolution and extraction.",
                recoverable=True,
                effect_absent=True,
            )
        tokens = [
            token
            for token in self._tokens(png, started)
            if token.confidence >= minimum_confidence and self._center_in(token.region, region)
        ]
        tokens.sort(key=lambda token: (token.region.y, token.region.x))
        if not tokens:
            raise SurfaceError(
                "visual_text_absent",
                "No readable text was found inside the resolved visual region.",
                effect_absent=True,
            )
        value = " ".join(token.text for token in tokens)
        if self.policy is not None:
            self._check_deadline(started)
        return value

    def contains_text(
        self,
        png: bytes,
        value: str,
        match: MatchMode,
        minimum_confidence: float,
        search_region: NormalizedRegion | None,
        viewport: Viewport,
    ) -> bool:
        started = monotonic()
        if self.policy is not None:
            self._validate_frame_budget(png)
        result = bool(
            self._matching_tokens(
                png, value, match, minimum_confidence, search_region, viewport, started
            )
        )
        if self.policy is not None:
            self._check_deadline(started)
        return result

    def contains_rendered_text(self, png: bytes, value: str, match: MatchMode) -> bool:
        started = monotonic()
        self._validate_frame_budget(png)
        result = bool(self._semantic_matching_tokens(png, value, match, started))
        self._check_deadline(started)
        return result

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
        started = monotonic()
        self._validate_frame_budget(png)
        graph = self._layout_graph(png, self._png_viewport(png), started)
        clipped = self._clip_region(region, graph.viewport)
        components = graph.of_kind("image")
        matching_components = [
            component
            for component in components
            if self._region_iou(clipped, component.region)
            > policy.image.signature_capture_minimum_iou
        ]
        if not matching_components:
            raise SurfaceError(
                "target_absent",
                "The discovery target is not a distinct non-text visual component.",
                recoverable=True,
                effect_absent=True,
            )
        component = max(
            matching_components,
            key=lambda item: self._region_iou(clipped, item.region),
        )
        assert component.edge is not None
        edge = component.edge
        if (
            edge.size < policy.image.minimum_signature_pixels
            or int(np.count_nonzero(edge)) < policy.image.minimum_signature_edge_pixels
        ):
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
            result = self.assets.write(encoded.tobytes())
        except CapabilityAssetError as error:
            raise SurfaceError(
                "template_storage_failed", "The visual signature could not be stored."
            ) from error
        self._check_deadline(started)
        return result

    @staticmethod
    def frame_hash(png: bytes) -> str:
        return f"sha256:{hashlib.sha256(png).hexdigest()}"

    def _tokens(self, png: bytes, started: float | None = None) -> tuple[VisualToken, ...]:
        if started is not None and self.policy is not None:
            self._check_deadline(started)
        key = self.frame_hash(png)
        if key not in self._ocr_cache:
            if len(self._ocr_cache) >= 8:
                self._ocr_cache.pop(next(iter(self._ocr_cache)))
            self._ocr_cache[key] = self.recognizer.recognize(png)
        if self.policy is not None and len(self._ocr_cache[key]) > self.policy.ocr.maximum_tokens:
            raise SurfaceError(
                "visual_ocr_budget_exceeded",
                "The rendered frame exceeds the visual OCR token budget.",
                effect_absent=True,
            )
        if started is not None and self.policy is not None:
            self._check_deadline(started)
        return self._ocr_cache[key]

    def _matching_tokens(
        self,
        png: bytes,
        value: str,
        match: MatchMode,
        minimum_confidence: float,
        search_region: NormalizedRegion | None,
        viewport: Viewport,
        started: float | None = None,
    ) -> tuple[VisualToken, ...]:
        region = self._normalized_region(search_region, viewport) if search_region else None
        expected = self._normalize(value)
        matches: list[VisualToken] = []
        for token in self._tokens(png, started):
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
            recoverable=True,
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
        started: float,
    ) -> VisualTargetData:
        labels = self._semantic_matching_tokens(
            png, candidate.label, candidate.label_match, started
        )
        if len(labels) != 1:
            raise self._cardinality_error(len(labels), "rendered control label")
        label = labels[0]
        graph = self._layout_graph(png, viewport, started)
        policy = self._required_policy()
        controls = (
            *graph.of_kind("control"),
            *(
                node
                for node in (*graph.of_kind("container"), *graph.of_kind("image"))
                # A detected input need only fit the observed local text line.
                # Page-wide control heuristics include padding and can classify
                # compact empty inputs as images on mixed-typography screens.
                if node.region.height >= label.region.height
                and node.region.height
                <= policy.association.maximum_following_gap_in_text_heights
                * graph.median_text_height
                and node.region.width / max(1, node.region.height)
                >= policy.segmentation.minimum_control_aspect_ratio
            ),
        )
        best = self._select_structural_row(label.region, controls, graph.median_text_height)
        best = tuple(
            node
            for node in best
            if not self._intervening_control_text(label.region, node.region, self.tokens(png))
        )
        if len(best) != 1:
            raise self._cardinality_error(len(best), "rendered labeled control")
        self._check_deadline(started)
        return VisualTargetData(
            best[0].region, "rendered_labeled_control", label.confidence, frame_hash
        )

    @staticmethod
    def _intervening_control_text(
        label: ScreenRegion, control: ScreenRegion, tokens: tuple[VisualToken, ...]
    ) -> bool:
        """Do not associate a label across another field or section's visible text."""
        for token in tokens:
            x, y = token.region.center
            if control.x >= label.x + label.width:
                if label.x + label.width < x < control.x and max(label.y, control.y) <= y <= min(
                    label.y + label.height, control.y + control.height
                ):
                    return True
            elif (
                label.y + label.height < y < control.y
                and token.region.x < min(label.x + label.width, control.x + control.width)
                and token.region.x + token.region.width > max(label.x, control.x)
            ):
                return True
        return False

    def _resolve_field_value(
        self,
        candidate: RenderedFieldValueCandidate,
        png: bytes,
        viewport: Viewport,
        frame_hash: str,
        started: float,
    ) -> VisualTargetData:
        policy = self._required_policy()
        labels = self._semantic_matching_tokens(
            png, candidate.label, candidate.label_match, started
        )
        if len(labels) != 1:
            raise self._cardinality_error(len(labels), "rendered field label")
        label = labels[0]
        graph = self._layout_graph(png, viewport, started)
        tokens = tuple(
            token
            for token in self._tokens(png, started)
            if token.confidence >= policy.ocr.minimum_confidence
            and not self._center_in(token.region, label.region)
        )
        value_tokens = self._field_value_tokens(graph, label.region, tokens, candidate.relation)
        if not value_tokens:
            raise SurfaceError(
                "target_absent",
                "No visual field value was associated with the rendered label.",
                recoverable=True,
                effect_absent=True,
            )
        region: ScreenRegion | None = None
        for token in value_tokens:
            region = self._union_region(region, token.region)
        assert region is not None
        self._check_deadline(started)
        return VisualTargetData(
            region,
            "rendered_field_value",
            min(token.confidence for token in value_tokens),
            frame_hash,
        )

    def _resolve_group_image(
        self,
        candidate: RenderedGroupImageCandidate,
        png: bytes,
        viewport: Viewport,
        frame_hash: str,
        started: float,
    ) -> VisualTargetData:
        policy = self._required_policy()
        labels = self._semantic_matching_tokens(
            png, candidate.group_label, candidate.group_label_match, started
        )
        if len(labels) != 1:
            raise self._cardinality_error(len(labels), "rendered image group label")
        label = labels[0]
        graph = self._layout_graph(png, viewport, started)
        images = self._nodes_in_anchor_container(graph, label.region, "image")
        related = self._select_structural_row(
            label.region, images, graph.median_text_height, image=True
        )
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
        scored: list[tuple[float, VisualNode]] = []
        for node in related:
            if node.edge is not None:
                scored.append((self._signature_score(normalized_template, node.edge, policy), node))
            self._check_deadline(started)
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
        self._check_deadline(started)
        return VisualTargetData(
            best_component.region, "rendered_group_image", best_score, frame_hash
        )

    def _semantic_matching_tokens(
        self, png: bytes, value: str, match: MatchMode, started: float
    ) -> tuple[VisualToken, ...]:
        policy = self._required_policy()
        expected = self._normalize(value)
        matches: list[VisualToken] = []
        for token in self._semantic_phrases(png, policy, started):
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

    def _semantic_matches_in_region(
        self,
        png: bytes,
        value: str,
        match: MatchMode,
        search_region: NormalizedRegion | None,
        viewport: Viewport,
        started: float,
    ) -> tuple[VisualToken, ...]:
        matches = self._semantic_matching_tokens(png, value, match, started)
        if search_region is None:
            return matches
        region = self._normalized_region(search_region, viewport)
        return tuple(token for token in matches if self._center_in(token.region, region))

    def _semantic_phrases(
        self, png: bytes, policy: VisionGroundingPolicy, started: float
    ) -> tuple[VisualToken, ...]:
        tokens = [
            token
            for token in self._tokens(png, started)
            if token.confidence >= policy.ocr.minimum_confidence
        ]
        if not tokens:
            return ()
        median_height = self._median_text_height(tokens)
        lines = self._text_lines(tuple(tokens), median_height)
        phrases: list[VisualToken] = []
        for line in lines:
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
            self._check_deadline(started)
        return tuple(phrases)

    def _layout_graph(self, png: bytes, viewport: Viewport, started: float) -> VisualLayoutGraph:
        policy = self._required_policy()
        key = self.frame_hash(png)
        cached = self._graph_cache.get(key)
        if cached is not None and cached.viewport == viewport:
            self._check_deadline(started)
            return cached
        self._validate_frame_budget(png)
        image = self._decode(png)
        tokens = tuple(
            token
            for token in self._tokens(png, started)
            if token.confidence >= policy.ocr.minimum_confidence
        )
        median_height = self._median_text_height(tokens)
        phrases = self._semantic_phrases(png, policy, started)
        edge = self._edge_map(image)
        boxes = self._segmented_boxes(image, edge, started)
        image_area = max(1, image.shape[0] * image.shape[1])
        nodes: list[VisualNode] = [
            VisualNode(
                "container:viewport",
                "container",
                ScreenRegion(0, 0, image.shape[1], image.shape[0]),
            )
        ]
        nodes.extend(
            VisualNode(f"phrase:{index}", "phrase", phrase.region, token=phrase)
            for index, phrase in enumerate(phrases)
        )
        component_nodes: list[VisualNode] = []
        for index, box in enumerate(boxes):
            contained_tokens = sum(self._center_in(token.region, box) for token in tokens)
            area_ratio = box.width * box.height / image_area
            aspect_ratio = box.width / max(1, box.height)
            text_overlap = self._text_overlap_ratio(box, tokens)
            if (
                area_ratio >= policy.segmentation.minimum_container_area_ratio
                and contained_tokens >= policy.segmentation.minimum_container_text_tokens
            ):
                kind: VisualNodeKind = "container"
            elif (
                box.height
                >= policy.segmentation.minimum_control_height_in_text_heights * median_height
                and aspect_ratio >= policy.segmentation.minimum_control_aspect_ratio
            ):
                kind = "control"
            elif any(self._strictly_contains(box, token.region) for token in tokens):
                # A compact bordered label may be shorter than the page's median text
                # (large headings or OCR padding). Preserve its actual enclosure for
                # text-click disambiguation, without treating it as an editable field.
                kind = "text_enclosure"
            elif text_overlap <= policy.segmentation.text_overlap_threshold:
                kind = "image"
            else:
                continue
            component_nodes.append(
                VisualNode(
                    f"{kind}:{index}",
                    kind,
                    box,
                    edge=self._trim_edges(
                        edge[box.y : box.y + box.height, box.x : box.x + box.width]
                    ),
                )
            )
        nodes.extend(component_nodes)
        containers = tuple(node for node in nodes if node.kind == "container")
        containment = tuple(
            (container.id, child.id)
            for container in containers
            for child in nodes
            if container.id != child.id and self._contains(container.region, child.region)
        )
        graph = VisualLayoutGraph(key, viewport, median_height, tuple(nodes), containment)
        if len(self._graph_cache) >= 8:
            self._graph_cache.pop(next(iter(self._graph_cache)))
        self._graph_cache[key] = graph
        self._check_deadline(started)
        return graph

    def _segmented_boxes(
        self, image: np.ndarray, edge: np.ndarray, started: float
    ) -> tuple[ScreenRegion, ...]:
        policy = self._required_policy()
        image_area = max(1, image.shape[0] * image.shape[1])
        scale = min(
            1.0,
            (policy.segmentation.maximum_analysis_pixels / image_area) ** 0.5,
        )
        if scale < 1:
            analysis_size = (
                max(1, round(image.shape[1] * scale)),
                max(1, round(image.shape[0] * scale)),
            )
            analysis_image = cv2.resize(image, analysis_size, interpolation=cv2.INTER_AREA)
            analysis_edge = cv2.resize(edge, analysis_size, interpolation=cv2.INTER_NEAREST)
        else:
            analysis_image = image
            analysis_edge = edge
        kernel_size = policy.segmentation.morphology_kernel_size
        kernel = np.ones((kernel_size, kernel_size), dtype=np.uint8)
        sources = [cv2.dilate(analysis_edge, kernel, iterations=1)]
        gray = cv2.cvtColor(analysis_image, cv2.COLOR_BGR2GRAY)
        for mode in (cv2.THRESH_BINARY, cv2.THRESH_BINARY_INV):
            _threshold, binary = cv2.threshold(gray, 0, 255, mode | cv2.THRESH_OTSU)
            sources.append(binary)
        minimum_area = image_area * policy.segmentation.minimum_component_area_ratio
        maximum_area = image_area * policy.segmentation.maximum_component_area_ratio
        boxes: list[ScreenRegion] = []
        for source in sources:
            contours, _hierarchy = cv2.findContours(source, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
            for contour in contours[: policy.segmentation.maximum_components]:
                source_x, source_y, source_width, source_height = cv2.boundingRect(contour)
                x = round(source_x / scale)
                y = round(source_y / scale)
                width = max(1, round(source_width / scale))
                height = max(1, round(source_height / scale))
                area = width * height
                if (
                    area < minimum_area
                    or area > maximum_area
                    or width < policy.segmentation.minimum_component_dimension
                    or height < policy.segmentation.minimum_component_dimension
                ):
                    continue
                boxes.append(ScreenRegion(x, y, width, height))
            self._check_deadline(started)
        boxes.sort(key=lambda region: region.width * region.height, reverse=True)
        distinct: list[ScreenRegion] = []
        for box in boxes:
            if any(
                self._region_iou(box, existing) > policy.segmentation.duplicate_iou_threshold
                for existing in distinct
            ):
                continue
            distinct.append(box)
        return tuple(distinct[: policy.segmentation.maximum_components])

    def _nodes_in_anchor_container(
        self, graph: VisualLayoutGraph, anchor: ScreenRegion, kind: VisualNodeKind
    ) -> tuple[VisualNode, ...]:
        container = self._anchor_container(graph, anchor, kind)
        contained_ids = {
            child_id for container_id, child_id in graph.containment if container_id == container.id
        }
        return tuple(node for node in graph.of_kind(kind) if node.id in contained_ids)

    def _anchor_container(
        self, graph: VisualLayoutGraph, anchor: ScreenRegion, child_kind: VisualNodeKind
    ) -> VisualNode:
        children = graph.of_kind(child_kind)
        child_ids = {child.id for child in children}
        child_regions = {child.id: child.region for child in children}
        contained_by = {
            container.id: {
                child_id
                for container_id, child_id in graph.containment
                if container_id == container.id
            }
            for container in graph.of_kind("container")
        }
        containers = [
            container
            for container in graph.of_kind("container")
            if self._contains(container.region, anchor)
            and any(
                child_id in child_ids
                and (
                    child_kind != "phrase" or self._region_iou(child_regions[child_id], anchor) == 0
                )
                for child_id in contained_by[container.id]
            )
        ]
        if not containers:
            raise SurfaceError(
                "target_absent",
                "No visual container associated the rendered anchor with a target.",
                recoverable=True,
                effect_absent=True,
            )
        smallest_area = min(node.region.width * node.region.height for node in containers)
        smallest = [
            node for node in containers if node.region.width * node.region.height == smallest_area
        ]
        if len(smallest) != 1:
            raise self._cardinality_error(len(smallest), "visual anchor container")
        return smallest[0]

    def _select_structural_row(
        self,
        anchor: ScreenRegion,
        candidates: tuple[VisualNode, ...],
        median_height: float,
        *,
        image: bool = False,
    ) -> tuple[VisualNode, ...]:
        policy = self._required_policy().association
        same_row: list[VisualNode] = []
        following: list[VisualNode] = []
        tolerance = policy.row_tolerance_in_text_heights * median_height
        border_overlap_tolerance = median_height
        for candidate in candidates:
            # OCR boxes can extend a few pixels into a control's border.  Treat
            # that scale-relative overlap as "following" while still rejecting
            # a control that actually contains the label (for example a button).
            if (
                self._center_in(anchor, candidate.region)
                and anchor.center[1] - candidate.region.y > border_overlap_tolerance
            ):
                continue
            vertical_overlap = self._overlap_length(
                anchor.y,
                anchor.y + anchor.height,
                candidate.region.y,
                candidate.region.y + candidate.region.height,
            )
            overlap_ratio = vertical_overlap / max(1, min(anchor.height, candidate.region.height))
            center_delta = abs(
                candidate.region.y + candidate.region.height / 2 - (anchor.y + anchor.height / 2)
            )
            if candidate.region.x >= anchor.x + anchor.width and (
                overlap_ratio >= policy.minimum_axis_overlap_ratio
                or (
                    image
                    and center_delta
                    <= policy.image_center_tolerance_in_text_heights * median_height
                )
            ):
                same_row.append(candidate)
                continue
            horizontal_overlap = self._overlap_length(
                anchor.x,
                anchor.x + anchor.width,
                candidate.region.x,
                candidate.region.x + candidate.region.width,
            )
            gap = candidate.region.y - (anchor.y + anchor.height)
            if (
                horizontal_overlap > 0
                and gap >= -border_overlap_tolerance
                and gap <= policy.maximum_following_gap_in_text_heights * median_height
            ):
                following.append(candidate)
        if same_row:
            return tuple(same_row)
        if not following:
            return ()
        nearest_y = min(node.region.y for node in following)
        return tuple(node for node in following if abs(node.region.y - nearest_y) <= tolerance)

    def _field_value_tokens(
        self,
        graph: VisualLayoutGraph,
        label: ScreenRegion,
        tokens: tuple[VisualToken, ...],
        relation: Literal["right_of", "below"] | None = None,
    ) -> tuple[VisualToken, ...]:
        # Segmentation can identify a table's label column as a container without
        # its adjacent value cells. Search enclosing containers, not just the first
        # rectangle containing two labels. A plausible stacked value in a smaller
        # group prevents silently escaping to an unrelated neighboring group.
        containers = sorted(
            (node for node in graph.of_kind("container") if self._contains(node.region, label)),
            key=lambda node: node.region.width * node.region.height,
        )
        stacked: tuple[VisualToken, ...] = ()
        for container in containers:
            scoped = tuple(
                token for token in tokens if self._contains(container.region, token.region)
            )
            horizontal = (
                self._horizontal_value_tokens(label, scoped, graph.median_text_height)
                if relation != "below"
                else ()
            )
            if horizontal:
                if stacked:
                    raise self._cardinality_error(2, "competing field value layouts")
                return horizontal
            if not stacked and relation != "right_of":
                stacked = self._stacked_value_tokens(label, scoped, graph.median_text_height)
                if stacked and relation == "below":
                    return stacked
        return stacked

    def _horizontal_value_tokens(
        self, label: ScreenRegion, tokens: tuple[VisualToken, ...], median_height: float
    ) -> tuple[VisualToken, ...]:
        # Filter per token BEFORE building lines. Unrelated navigation text to the
        # left must not disqualify the actual value on the same rendered baseline.
        horizontal = tuple(
            token
            for token in tokens
            if token.region.x >= label.x + label.width
            and self._axis_overlap_ratio(token.region, label, vertical=True)
            >= self._required_policy().association.minimum_axis_overlap_ratio
        )
        if not horizontal:
            return ()
        groups = self._token_groups(list(horizontal), median_height)
        if len(groups) != 1:
            raise self._cardinality_error(len(groups), "rendered field value")
        return groups[0]

    def _associated_value_tokens(
        self,
        label: ScreenRegion,
        tokens: tuple[VisualToken, ...],
        median_height: float,
    ) -> tuple[VisualToken, ...]:
        horizontal = self._horizontal_value_tokens(label, tokens, median_height)
        if horizontal:
            return horizontal
        return self._stacked_value_tokens(label, tokens, median_height)

    def _stacked_value_tokens(
        self,
        label: ScreenRegion,
        tokens: tuple[VisualToken, ...],
        median_height: float,
    ) -> tuple[VisualToken, ...]:
        policy = self._required_policy()
        lines = self._text_lines(tokens, median_height)
        following = [
            line
            for line in lines
            if min(token.region.y for token in line) >= label.y + label.height
            and self._line_horizontal_overlap(line, label) > 0
            and min(token.region.y for token in line) - (label.y + label.height)
            <= policy.association.maximum_following_gap_in_text_heights * median_height
        ]
        if not following:
            return ()
        nearest_y = min(min(token.region.y for token in line) for line in following)
        nearest = [
            line
            for line in following
            if abs(min(token.region.y for token in line) - nearest_y)
            <= policy.association.row_tolerance_in_text_heights * median_height
        ]
        groups = tuple(
            group
            for line in nearest
            for group in self._token_groups(line, median_height)
            if self._line_horizontal_overlap(group, label) > 0
        )
        if len(groups) != 1:
            raise self._cardinality_error(len(groups), "rendered field value")
        return groups[0]

    def _text_lines(
        self, tokens: tuple[VisualToken, ...], median_height: float
    ) -> list[list[VisualToken]]:
        del median_height
        policy = self._required_policy().association
        lines: list[list[VisualToken]] = []
        for token in sorted(tokens, key=lambda item: (item.region.y, item.region.x)):
            matching_line = next(
                (
                    line
                    for line in lines
                    if self._axis_overlap_ratio(token.region, line[0].region, vertical=True)
                    >= policy.minimum_axis_overlap_ratio
                ),
                None,
            )
            if matching_line is None:
                lines.append([token])
            else:
                matching_line.append(token)
        for line in lines:
            line.sort(key=lambda item: item.region.x)
        return lines

    def _token_groups(
        self, line: list[VisualToken], median_height: float
    ) -> tuple[tuple[VisualToken, ...], ...]:
        maximum_gap = (
            self._required_policy().phrases.maximum_line_gap_in_text_heights * median_height
        )
        groups: list[list[VisualToken]] = []
        for token in sorted(line, key=lambda item: item.region.x):
            if (
                not groups
                or token.region.x - (groups[-1][-1].region.x + groups[-1][-1].region.width)
                > maximum_gap
            ):
                groups.append([token])
            else:
                groups[-1].append(token)
        return tuple(tuple(group) for group in groups)

    def _line_overlaps_region(self, line: list[VisualToken], region: ScreenRegion) -> bool:
        return any(
            self._axis_overlap_ratio(token.region, region, vertical=True)
            >= self._required_policy().association.minimum_axis_overlap_ratio
            for token in line
        )

    @staticmethod
    def _line_horizontal_overlap(
        line: list[VisualToken] | tuple[VisualToken, ...], region: ScreenRegion
    ) -> int:
        line_region: ScreenRegion | None = None
        for token in line:
            line_region = VisionGrounder._union_region(line_region, token.region)
        if line_region is None:
            return 0
        return VisionGrounder._overlap_length(
            line_region.x, line_region.x + line_region.width, region.x, region.x + region.width
        )

    @staticmethod
    def _overlap_length(
        first_start: int, first_end: int, second_start: int, second_end: int
    ) -> int:
        return max(0, min(first_end, second_end) - max(first_start, second_start))

    @staticmethod
    def _contains(container: ScreenRegion, child: ScreenRegion) -> bool:
        return (
            child.x >= container.x
            and child.y >= container.y
            and child.x + child.width <= container.x + container.width
            and child.y + child.height <= container.y + container.height
        )

    @staticmethod
    def _strictly_contains(container: ScreenRegion, child: ScreenRegion) -> bool:
        return (
            child.x > container.x
            and child.y > container.y
            and child.x + child.width < container.x + container.width
            and child.y + child.height < container.y + container.height
        )

    @staticmethod
    def _axis_overlap_ratio(first: ScreenRegion, second: ScreenRegion, *, vertical: bool) -> float:
        if vertical:
            overlap = VisionGrounder._overlap_length(
                first.y, first.y + first.height, second.y, second.y + second.height
            )
            denominator = min(first.height, second.height)
        else:
            overlap = VisionGrounder._overlap_length(
                first.x, first.x + first.width, second.x, second.x + second.width
            )
            denominator = min(first.width, second.width)
        return overlap / max(1, denominator)

    @staticmethod
    def _text_overlap_ratio(region: ScreenRegion, tokens: tuple[VisualToken, ...]) -> float:
        overlap = 0
        for token in tokens:
            left = max(region.x, token.region.x)
            top = max(region.y, token.region.y)
            right = min(region.x + region.width, token.region.x + token.region.width)
            bottom = min(region.y + region.height, token.region.y + token.region.height)
            overlap += max(0, right - left) * max(0, bottom - top)
        return min(1.0, overlap / max(1, region.width * region.height))

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
        padding = policy.image.signature_padding_pixels
        scale = min(
            (policy.image.canonical_width - padding) / max(1, trimmed.shape[1]),
            (policy.image.canonical_height - padding) / max(1, trimmed.shape[0]),
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
        chamfer = exp(
            -((forward + backward) / 2)
            / max(1.0, template.shape[0] * policy.image.chamfer_decay_ratio)
        )
        return float(
            policy.image.overlap_weight * overlap + (1 - policy.image.overlap_weight) * chamfer
        )

    def _required_policy(self) -> VisionGroundingPolicy:
        if self.policy is None:
            raise SurfaceError(
                "visual_policy_missing",
                "Semantic visual grounding requires a configured visual policy.",
                effect_absent=True,
            )
        return self.policy

    def _validate_frame_budget(self, png: bytes) -> None:
        policy = self._required_policy()
        dimensions = self._png_dimensions(png)
        if dimensions is not None:
            width, height = dimensions
            if width <= 0 or height <= 0 or width * height > policy.budgets.maximum_frame_pixels:
                raise SurfaceError(
                    "visual_frame_budget_exceeded",
                    "The rendered frame exceeds the visual grounding pixel budget.",
                    effect_absent=True,
                )

    @staticmethod
    def _png_dimensions(png: bytes) -> tuple[int, int] | None:
        if len(png) < 24 or not png.startswith(b"\x89PNG\r\n\x1a\n"):
            return None
        return int.from_bytes(png[16:20], "big"), int.from_bytes(png[20:24], "big")

    @classmethod
    def _png_viewport(cls, png: bytes) -> Viewport:
        dimensions = cls._png_dimensions(png)
        if dimensions is None:
            raise SurfaceError("frame_invalid", "The rendered surface frame is not a valid PNG.")
        return Viewport(*dimensions)

    def _check_deadline(self, started: float) -> None:
        policy = self._required_policy()
        if (monotonic() - started) * 1000 > policy.budgets.maximum_grounding_milliseconds:
            raise SurfaceError(
                "visual_grounding_budget_exceeded",
                "Visual grounding exceeded its bounded execution budget.",
                effect_absent=True,
            )

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
        horizontal_overlap = max(0, min(a.x + a.width, t.x + t.width) - max(a.x, t.x))
        same_column = horizontal_overlap >= min(a.width, t.width) * 0.5
        return same_column and t.y >= a.y + a.height

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
