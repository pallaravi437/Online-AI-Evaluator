import os
import io
import hashlib
import logging
from typing import Tuple, Dict, Any, Optional
from PIL import Image, ImageOps, ExifTags

logger = logging.getLogger("bea.image_service")

# Allowed MIME types and image extensions
ALLOWED_IMAGE_MIMES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/svg+xml": ".svg",
    "application/pdf": ".pdf"
}

class ImageService:
    """
    Handles image validation, EXIF auto-rotation, aspect-ratio preserving
    compression for handwritten scripts and electronic circuits,
    and size comparison reporting.
    """

    @staticmethod
    def calculate_sha256(data: bytes) -> str:
        """Returns the SHA256 hash of the binary file content."""
        return hashlib.sha256(data).hexdigest()

    @staticmethod
    def fix_orientation(image: Image.Image) -> Image.Image:
        """Auto-rotates image based on mobile camera EXIF orientation tag."""
        try:
            return ImageOps.exif_transpose(image)
        except Exception:
            return image

    @classmethod
    def optimize_image(
        cls, 
        image_bytes: bytes, 
        max_dimension: int = 2048, 
        quality: int = 82
    ) -> Tuple[bytes, Dict[str, Any]]:
        """
        Compresses and optimizes handwritten answer images:
        - Auto-rotates orientation based on camera EXIF.
        - Scales down if larger than max_dimension while preserving high legibility for circuit lines.
        - Converts RGBA to RGB for JPEG optimization.
        - Returns (optimized_bytes, metadata).
        """
        original_size = len(image_bytes)
        img = Image.open(io.BytesIO(image_bytes))
        orig_width, orig_height = img.size
        orig_format = img.format or "JPEG"

        # Correct camera rotation
        img = cls.fix_orientation(img)
        
        # Convert RGBA / Palette mode to RGB
        if img.mode in ("RGBA", "P", "LA"):
            background = Image.new("RGB", img.size, (255, 255, 255))
            if img.mode == "RGBA":
                background.paste(img, mask=img.split()[3])
            else:
                background.paste(img)
            img = background
        elif img.mode != "RGB":
            img = img.convert("RGB")

        # Resize if dimensions exceed max_dimension
        current_w, current_h = img.size
        if max(current_w, current_h) > max_dimension:
            ratio = max_dimension / float(max(current_w, current_h))
            new_w = int(current_w * ratio)
            new_h = int(current_h * ratio)
            img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)

        # Save to buffer with progressive JPEG compression
        out_buf = io.BytesIO()
        img.save(
            out_buf,
            format="JPEG",
            quality=quality,
            optimize=True,
            progressive=True
        )
        optimized_bytes = out_buf.getvalue()
        optimized_size = len(optimized_bytes)

        # If compression actually made it larger (rare), fallback to original if valid
        if optimized_size > original_size and original_size > 0:
            final_bytes = image_bytes
            final_size = original_size
        else:
            final_bytes = optimized_bytes
            final_size = optimized_size

        metadata = {
            "original_size_bytes": original_size,
            "optimized_size_bytes": final_size,
            "original_size_kb": round(original_size / 1024, 1),
            "optimized_size_kb": round(final_size / 1024, 1),
            "compression_ratio_pct": round((1 - (final_size / max(original_size, 1))) * 100, 1),
            "width": img.width,
            "height": img.height,
            "mime_type": "image/jpeg",
            "sha256": cls.calculate_sha256(final_bytes)
        }

        return final_bytes, metadata

    @classmethod
    def validate_and_process_upload(
        cls, 
        file_storage, 
        crop_data: Optional[Dict[str, Any]] = None
    ) -> Tuple[bool, Optional[bytes], Optional[Dict[str, Any]], str]:
        """
        Validates file stream, applies optional crop coordinates,
        and runs optimization pipeline.
        """
        try:
            file_bytes = file_storage.read()
            if not file_bytes:
                return False, None, None, "Uploaded file is empty."

            # Check if PDF
            if file_bytes.startswith(b"%PDF"):
                metadata = {
                    "original_size_bytes": len(file_bytes),
                    "optimized_size_bytes": len(file_bytes),
                    "original_size_kb": round(len(file_bytes) / 1024, 1),
                    "optimized_size_kb": round(len(file_bytes) / 1024, 1),
                    "compression_ratio_pct": 0.0,
                    "width": None,
                    "height": None,
                    "mime_type": "application/pdf",
                    "sha256": cls.calculate_sha256(file_bytes)
                }
                return True, file_bytes, metadata, "PDF accepted."

            # Check if SVG vector circuit / image
            cleaned_prefix = file_bytes.strip()[:100].lower()
            if b"<svg" in cleaned_prefix or b"<?xml" in cleaned_prefix and b"<svg" in file_bytes[:500].lower():
                metadata = {
                    "original_size_bytes": len(file_bytes),
                    "optimized_size_bytes": len(file_bytes),
                    "original_size_kb": round(len(file_bytes) / 1024, 1),
                    "optimized_size_kb": round(len(file_bytes) / 1024, 1),
                    "compression_ratio_pct": 0.0,
                    "width": None,
                    "height": None,
                    "mime_type": "image/svg+xml",
                    "sha256": cls.calculate_sha256(file_bytes)
                }
                return True, file_bytes, metadata, "SVG diagram accepted."

            # Verify image
            try:
                img = Image.open(io.BytesIO(file_bytes))
                img.verify()
            except Exception as e:
                return False, None, None, f"Invalid or corrupted image format: {e}"

            # If crop coordinates provided from interactive canvas
            if crop_data and all(k in crop_data for k in ("x", "y", "width", "height")):
                raw_img = Image.open(io.BytesIO(file_bytes))
                raw_img = cls.fix_orientation(raw_img)
                box = (
                    int(crop_data["x"]),
                    int(crop_data["y"]),
                    int(crop_data["x"] + crop_data["width"]),
                    int(crop_data["y"] + crop_data["height"])
                )
                cropped_img = raw_img.crop(box)
                buf = io.BytesIO()
                cropped_img.save(buf, format="JPEG", quality=90)
                file_bytes = buf.getvalue()

            optimized_bytes, metadata = cls.optimize_image(file_bytes)
            return True, optimized_bytes, metadata, "Image processed successfully."

        except Exception as e:
            logger.error(f"Error processing upload: {e}")
            return False, None, None, f"Upload processing failed: {str(e)}"
