from __future__ import annotations

from pathlib import Path
from typing import Any

from forthatday_agent.application.payment_verification import OcrDocument, OcrLine


class RapidOcrProvider:
    def __init__(self, engine: Any | None = None) -> None:
        if engine is None:
            try:
                from rapidocr import RapidOCR
            except ImportError as error:
                raise RuntimeError(
                    "RapidOCR could not load; install requirements and native OpenCV libraries"
                ) from error
            engine = RapidOCR()
        self.engine = engine

    def extract(self, image_path: Path) -> OcrDocument:
        if not image_path.is_file():
            raise ValueError("OCR image does not exist")
        result = self.engine(str(image_path))
        texts = tuple(getattr(result, "txts", ()) or ())
        scores = tuple(getattr(result, "scores", ()) or ())
        if len(texts) != len(scores):
            raise ValueError("OCR returned inconsistent text and confidence arrays")
        return OcrDocument(
            tuple(
                OcrLine(str(text).strip(), float(score))
                for text, score in zip(texts, scores, strict=True)
                if str(text).strip()
            )
        )
