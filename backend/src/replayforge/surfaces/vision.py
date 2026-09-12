"""Deterministic OCR and image-anchor grounding over rendered surface frames."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
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
    VisualLocatorCandidate,
)
from replayforge.surfaces.models import (
    ScreenRegion,
    SurfaceError,
    Viewport,
    VisualTargetData,
    VisualToken,
)


class TextRecognizer(Protocol):
    def recognize(self, png: bytes) -> tuple[VisualToken, ...]: ...


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

    def _match_template(
        self,
        candidate: ImageAnchorCandidate,
        png: bytes,
        viewport: Viewport,
        frame_hash: str,
    ) -> VisualTargetData:
        image = self._edge_map(self._decode(png))
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
        scale = candidate.minimum_scale
        while scale <= candidate.maximum_scale + 1e-9:
            width = max(1, round(template.shape[1] * scale))
            height = max(1, round(template.shape[0] * scale))
            if width <= haystack.shape[1] and height <= haystack.shape[0]:
                resized = cv2.resize(template, (width, height), interpolation=cv2.INTER_AREA)
                response = cv2.matchTemplate(haystack, resized, cv2.TM_CCOEFF_NORMED)
                _min_value, max_value, _min_at, max_at = cv2.minMaxLoc(response)
                scored.append((float(max_value), max_at[0], max_at[1], width, height))
            scale += candidate.scale_step
        if not scored:
            raise SurfaceError(
                "target_absent",
                "The visual template exceeded the search region.",
                effect_absent=True,
            )
        scored.sort(reverse=True)
        best = scored[0]
        alternatives = [
            item
            for item in scored[1:]
            if abs(item[1] - best[1]) > best[3] // 2 or abs(item[2] - best[2]) > best[4] // 2
        ]
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
            round(anchor.x + spec.x * unit),
            round(anchor.y + spec.y * unit),
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
        x = min(region.x, viewport.width - 1)
        y = min(region.y, viewport.height - 1)
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
