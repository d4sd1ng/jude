from __future__ import annotations

import io
import subprocess
import tempfile
from pathlib import Path

import pytesseract
from PIL import Image
from services.filesystem import AI_DATA_ROOT, resolve_path



class OCRService:
    def extract_path(self, path: str, language: str = "deu+eng") -> dict:
        source = resolve_path(path)
        if not source.is_file():
            raise FileNotFoundError(source)
        return self.extract(source.read_bytes(), source.name, language)

    def extract(self, content: bytes, filename: str, language: str = "deu+eng") -> dict:
        if language not in {"deu", "eng", "deu+eng", "eng+deu"}:
            raise ValueError("OCR-Sprache muss deu, eng oder deu+eng sein.")
        suffix = Path(filename).suffix.lower()
        if suffix == ".pdf":
            texts = self._pdf(content, language)
        elif suffix in {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}:
            image = Image.open(io.BytesIO(content))
            texts = [pytesseract.image_to_string(image, lang=language)]
        else:
            raise ValueError("Unterstützt werden PDF und gängige Bildformate.")
        return {"filename": filename, "language": language, "pages": len(texts), "text": "\n\n".join(texts).strip()}

    @staticmethod
    def _pdf(content: bytes, language: str) -> list[str]:
        temp_root = AI_DATA_ROOT / "Jude" / "tmp"
        temp_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="ocr-", dir=temp_root) as tmp:
            source = Path(tmp) / "input.pdf"
            source.write_bytes(content)
            prefix = Path(tmp) / "page"
            result = subprocess.run(["pdftoppm", "-png", "-r", "200", str(source), str(prefix)], capture_output=True, text=True, timeout=120)
            if result.returncode:
                raise RuntimeError(result.stderr.strip())
            return [pytesseract.image_to_string(Image.open(page), lang=language) for page in sorted(Path(tmp).glob("page-*.png"))]
