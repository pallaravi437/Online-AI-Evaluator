import os
import io
import re
import json
import uuid
import base64
import logging
from datetime import datetime, timezone
from typing import List, Dict, Any, Tuple, Optional
from config import Config
from database.db_manager import db_manager
from services.ai_service import AIService

logger = logging.getLogger("bea.material_service")

# Local in-memory store for dev / offline mode
_local_materials: Dict[str, Dict[str, Any]] = {}
_manifest_path = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    "static", "uploads", "materials", "materials_manifest.json"
)

def _load_local_manifest():
    """Loads locally cached materials manifest from disk if it exists."""
    global _local_materials
    if os.path.exists(_manifest_path):
        try:
            with open(_manifest_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
                if isinstance(data, dict):
                    _local_materials = data
                elif isinstance(data, list):
                    _local_materials = {m["id"]: m for m in data if "id" in m}
        except Exception as e:
            logger.warning(f"Could not load materials manifest: {e}")

def _save_local_manifest():
    """Persists locally cached materials to disk."""
    try:
        os.makedirs(os.path.dirname(_manifest_path), exist_ok=True)
        with open(_manifest_path, "w", encoding="utf-8") as fh:
            json.dump(_local_materials, fh, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.warning(f"Could not save materials manifest: {e}")

_load_local_manifest()


class MaterialService:
    """
    Service for managing Course Syllabus, Lecture Notes, Reference Textbooks,
    and Formula Sheets uploaded as PDF, Word (.docx/.doc), or Images.
    Extracts text, structures key concepts (units, topics, formulas, definitions),
    and indexes them for instant retrieval during AI Question Generation and Evaluation.
    """

    ALLOWED_EXTENSIONS = {"pdf", "docx", "doc", "jpg", "jpeg", "png", "webp", "txt", "pptx", "ppt"}

    @classmethod
    def is_allowed_file(cls, filename: str) -> bool:
        if not filename or "." not in filename:
            return False
        ext = filename.rsplit(".", 1)[1].lower()
        return ext in cls.ALLOWED_EXTENSIONS

    @classmethod
    def save_and_process_material(
        cls,
        file_bytes: bytes = None,
        filename: str = None,
        title: str = "",
        subject_code: str = "",
        unit_topic: str = "",
        material_type: str = "notes",
        assessment_id: Optional[str] = None,
        created_by_id: Optional[str] = None,
        *,
        file=None,
        created_by: Optional[str] = None,
    ) -> Tuple[bool, Optional[Dict[str, Any]], str]:
        """
        Saves uploaded file to storage, extracts its technical text,
        structures key concepts (units, formulas, definitions), and stores it.

        Accepts either raw bytes (file_bytes + filename) or a file-like object
        with a .filename attribute (file=).
        """
        # Normalise `created_by` alias
        if created_by and not created_by_id:
            created_by_id = created_by

        # Support file=<FileStorage|BytesIO> kwarg as an alternative
        if file is not None and file_bytes is None:
            if hasattr(file, "read"):
                file_bytes = file.read()
            if hasattr(file, "filename") and not filename:
                filename = file.filename
            if not filename:
                filename = "upload.bin"

        if not file_bytes:
            return False, None, "Uploaded file is empty."

        if not cls.is_allowed_file(filename):
            return False, None, f"Unsupported file type. Allowed: PDF, Word (.docx, .doc), Images (JPG, PNG, WEBP)."

        ext = filename.rsplit(".", 1)[1].lower()
        file_id = str(uuid.uuid4())
        safe_base = re.sub(r'[^a-zA-Z0-9_\-]', '_', filename.rsplit(".", 1)[0])[:40]
        stored_filename = f"mat_{file_id[:8]}_{safe_base}.{ext}"

        upload_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static", "uploads", "materials")
        os.makedirs(upload_dir, exist_ok=True)
        disk_path = os.path.join(upload_dir, stored_filename)
        rel_path = f"/static/uploads/materials/{stored_filename}"

        try:
            with open(disk_path, "wb") as fh:
                fh.write(file_bytes)
        except Exception as e:
            logger.error(f"Failed to write material file to disk: {e}")
            return False, None, f"Storage write error: {e}"

        file_size_kb = int(len(file_bytes) / 1024)

        # Step 1: Extract Text from Document or Image
        extracted_text, extract_msg = cls._extract_text(file_bytes, ext, filename, title, subject_code)
        if not extracted_text:
            extracted_text = f"Title: {title}\nSubject: {subject_code}\nUnit/Topic: {unit_topic}\n(File contains visual diagrams or notes - text could not be directly extracted)."

        # Step 2: Automatically structure the content into units, topics, formulas, and definitions
        structured_content = cls._structure_material_content(
            extracted_text=extracted_text,
            title=title,
            subject_code=subject_code,
            unit_topic=unit_topic,
            material_type=material_type
        )

        clean_type = material_type.lower().strip()
        if clean_type not in ("syllabus", "notes", "material", "formula_sheet", "textbook", "reference"):
            clean_type = "notes"

        material_record = {
            "id": file_id,
            "title": title.strip() or filename,
            "material_type": clean_type,
            "subject_code": subject_code.strip() or "General Academic",
            "unit_topic": unit_topic.strip() or "",
            "assessment_id": assessment_id if assessment_id else None,
            "file_path": rel_path,
            "file_name": filename,
            "file_type": ext,
            "file_size_kb": file_size_kb,
            "extracted_text": extracted_text,
            "structured_content": structured_content,
            "created_by_id": created_by_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat()
        }

        # Step 3: Commit to Supabase or local store
        supabase = db_manager.get_supabase(use_service_role=True)
        if supabase:
            try:
                res = supabase.table("course_materials").insert(material_record).execute()
                if res.data:
                    return True, res.data[0], f"Material '{title}' uploaded and indexed successfully!"
            except Exception as e:
                logger.warning(f"Supabase course_materials insert failed, falling back to local store: {e}")

        # Local fallback
        _local_materials[file_id] = material_record
        _save_local_manifest()
        return True, material_record, f"Material '{title}' uploaded and indexed successfully!"

    # -------------------------------------------------------------------------
    # TEXT EXTRACTION HELPERS
    # -------------------------------------------------------------------------
    @classmethod
    def _extract_text(
        cls,
        file_bytes: bytes,
        ext: str,
        filename: str,
        title: str,
        subject: str
    ) -> Tuple[str, str]:
        """Extracts readable text from PDF, Word, Image, or plain text files."""
        if ext == "pdf":
            return cls._extract_from_pdf(file_bytes, subject)
        elif ext in ("docx", "doc"):
            return cls._extract_from_docx(file_bytes)
        elif ext in ("jpg", "jpeg", "png", "webp"):
            return cls._extract_from_image(file_bytes, ext, subject)
        elif ext in ("txt", "text"):
            try:
                return file_bytes.decode("utf-8", errors="replace"), "Extracted from text file."
            except Exception:
                return file_bytes.decode("latin-1", errors="replace"), "Extracted from text file (latin-1)."
        elif ext in ("pptx", "ppt"):
            return cls._extract_from_pptx(file_bytes)
        return "", "Unsupported file type"

    @classmethod
    def _extract_from_pdf(cls, pdf_bytes: bytes, subject: str) -> Tuple[str, str]:
        """Extracts text from PDF; runs AI OCR on embedded images if text is sparse."""
        import pypdf
        extracted = ""
        embedded_images = []
        try:
            reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
            for page_idx, page in enumerate(reader.pages):
                txt = page.extract_text() or ""
                if txt.strip():
                    extracted += f"\n--- Page {page_idx + 1} ---\n" + txt
                try:
                    for img in page.images:
                        embedded_images.append(img.data)
                except Exception:
                    pass
        except Exception as e:
            logger.error(f"pypdf read error: {e}")
            return "", str(e)

        clean = extracted.strip()
        # If digital text exists, return it
        if len(clean) >= 50:
            return clean, f"Extracted {len(clean)} characters of text from PDF."

        # If scanned PDF with images, transcribe up to 4 pages with AI
        if embedded_images:
            logger.info("PDF has sparse selectable text; transcribing embedded page images with Multimodal AI.")
            image_transcriptions = []
            for p_num, img_b in enumerate(embedded_images[:4], 1):
                img_txt, _ = cls._extract_from_image(img_b, "jpeg", subject)
                if img_txt:
                    image_transcriptions.append(f"--- Scanned Page {p_num} ---\n" + img_txt)
            if image_transcriptions:
                return "\n\n".join(image_transcriptions), f"Transcribed {len(image_transcriptions)} scanned pages via AI."

        return clean, "Extracted text from PDF."

    @classmethod
    def _extract_from_docx(cls, docx_bytes: bytes) -> Tuple[str, str]:
        """Extracts text and tables from Word document."""
        import docx
        lines = []
        try:
            doc = docx.Document(io.BytesIO(docx_bytes))
            for p in doc.paragraphs:
                txt = p.text.strip()
                if txt:
                    lines.append(txt)
            for table in doc.tables:
                for row in table.rows:
                    row_txt = " | ".join(c.text.strip() for c in row.cells if c.text.strip())
                    if row_txt:
                        lines.append(row_txt)
            text = "\n".join(lines).strip()
            return text, f"Extracted {len(text)} characters from Word document."
        except Exception as e:
            logger.error(f"Word docx extract error: {e}")
            return "", str(e)

    @classmethod
    def _extract_from_image(cls, image_bytes: bytes, ext: str, subject: str) -> Tuple[str, str]:
        """Transcribes notes or syllabus image using Multimodal AI."""
        from PIL import Image
        try:
            img = Image.open(io.BytesIO(image_bytes))
            img.verify()
        except Exception as e:
            return "", f"Invalid image file: {e}"

        prompt = (
            "You are an academic curriculum and study notes transcriber.\n"
            f"Subject: {subject or 'Engineering & Technology'}\n"
            "Task: Transcribe all readable text, section headings, unit names, definitions, "
            "formulas, circuit equations, and key technical concepts shown in this study material image.\n"
            "Format the output as clean Markdown."
        )

        mime = f"image/{'png' if ext.lower() == 'png' else 'jpeg'}"
        b64_data = base64.b64encode(image_bytes).decode("utf-8")
        contents = [prompt, {"mime_type": mime, "data": b64_data}]

        try:
            raw_text, used_prov, used_model, _ = AIService._generate_with_ai(
                contents=contents,
                temperature=0.1
            )
            return raw_text.strip(), f"Transcribed image via {used_prov} ({used_model})."
        except Exception as e:
            logger.warning(f"Image transcription AI error: {e}")
            return "", str(e)

    # -------------------------------------------------------------------------
    # STRUCTURED CONTENT ANALYZER (AI + PEDAGOGY RULE FALLBACK)
    # -------------------------------------------------------------------------
    @classmethod
    def _structure_material_content(
        cls,
        extracted_text: str,
        title: str = "",
        subject_code: str = "",
        unit_topic: str = "",
        material_type: str = "syllabus"
    ) -> Dict[str, Any]:
        """
        Structures raw syllabus or study material text into indexed JSON:
        units, topics, key_definitions, key_formulas, learning_outcomes, and summary.
        """
        if not extracted_text or len(extracted_text.strip()) < 20:
            return {
                "summary": f"{title} for {subject_code}.",
                "units": [unit_topic] if unit_topic else [],
                "topics": [],
                "key_definitions": [],
                "key_formulas": [],
                "learning_outcomes": []
            }

        # 1. Try AI Structured Analyzer
        key = Config.GEMINI_API_KEY.strip()
        if not Config.is_placeholder(key):
            try:
                structured = cls._structure_with_ai(extracted_text, title, subject_code, unit_topic, material_type)
                if structured and (structured.get("topics") or structured.get("key_formulas") or structured.get("summary")):
                    return structured
            except Exception as e:
                logger.warning(f"AI content structuring failed: {e}")

        # 2. Rule-based Heuristic Analyzer (Zero-API / Offline fallback)
        return cls._structure_with_heuristics(extracted_text, title, subject_code, unit_topic)

    @classmethod
    def _structure_with_ai(
        cls,
        text: str,
        title: str,
        subject: str,
        unit_topic: str,
        material_type: str
    ) -> Dict[str, Any]:
        """Uses AI to parse syllabus/notes into structured JSON."""
        sample_text = text[:15000]

        prompt_system = (
            "You are an academic curriculum and knowledge structuring engine.\n"
            "Analyze the provided academic study material / syllabus text and extract a structured knowledge blueprint.\n\n"
            "Return strictly a JSON object with this exact schema:\n"
            "{\n"
            '  "summary": "2-3 sentence overview of this material and what students learn",\n'
            '  "units": ["List of distinct units, e.g. Unit 1: DC Circuits", "Unit 2: AC Analysis"],\n'
            '  "topics": ["List of specific subtopics and core concepts"],\n'
            '  "key_definitions": [\n'
            '     {"term": "Term Name", "definition": "Clear concise technical definition"}\n'
            '  ],\n'
            '  "key_formulas": [\n'
            '     {"name": "Formula Name", "formula": "Mathematical equation, e.g. V = I * R", "context": "When to apply"}\n'
            '  ],\n'
            '  "learning_outcomes": ["List of competencies / Course Outcomes, e.g. CO1: Calculate voltage drop"]\n'
            "}\n"
            "CRITICAL: Keep formulas, definitions, and topics technically accurate. No conversational wrapper."
        )

        user_content = (
            f"Title: {title}\nSubject: {subject}\nSpecified Unit/Topic: {unit_topic}\n"
            f"Material Type: {material_type}\n\nDocument Text:\n{sample_text}"
        )

        raw_json, _, _, _ = AIService._generate_with_ai(
            contents=[prompt_system, user_content],
            json_output=True,
            temperature=0.1
        )

        clean = raw_json.strip()
        if clean.startswith("```json"):
            clean = clean[7:]
        elif clean.startswith("```"):
            clean = clean[3:]
        if clean.endswith("```"):
            clean = clean[:-3]

        parsed = json.loads(clean.strip())
        if isinstance(parsed, dict):
            # Ensure all required keys exist
            return {
                "summary": str(parsed.get("summary") or f"{title} for {subject}."),
                "units": list(parsed.get("units") or ([unit_topic] if unit_topic else [])),
                "topics": list(parsed.get("topics") or []),
                "key_definitions": list(parsed.get("key_definitions") or []),
                "key_formulas": list(parsed.get("key_formulas") or []),
                "learning_outcomes": list(parsed.get("learning_outcomes") or [])
            }
        return {}

    @classmethod
    def _structure_with_heuristics(
        cls,
        text: str,
        title: str,
        subject: str,
        unit_topic: str
    ) -> Dict[str, Any]:
        """Regex and pattern-based knowledge extraction for offline reliability."""
        lines = [l.strip() for l in text.splitlines() if l.strip()]

        units: List[str] = []
        topics: List[str] = []
        formulas: List[Dict[str, str]] = []
        definitions: List[Dict[str, str]] = []
        learning_outcomes: List[str] = []

        if unit_topic:
            units.append(unit_topic)

        # Regex patterns
        unit_pattern = re.compile(r'^(?:unit|chapter|module|section)\s*[-:]?\s*(\d+|[IVXLCDM]+)[\s:\-–—]+(.*)', re.I)
        formula_pattern = re.compile(r'([A-Za-z0-9_α-ωΑ-Ω\s\(\)]+)\s*=\s*([A-Za-z0-9_α-ωΑ-Ω\s\+\-\*\/\^\(\)\.\√]+)')
        definition_pattern = re.compile(r'^([A-Z][a-zA-Z0-9\s]{2,40})\s*(?:is defined as|refers to|is the|means)\s+(.+)', re.I)
        co_pattern = re.compile(r'^(?:CO|LO|outcome)\s*[-:]?\s*(\d+)?[\s:\-–—]+(.*)', re.I)

        for line in lines:
            # Check unit headers
            u_match = unit_pattern.match(line)
            if u_match:
                u_text = f"Unit {u_match.group(1)}: {u_match.group(2).strip()}"
                if u_text not in units:
                    units.append(u_text)
                continue

            # Check Course Outcomes
            co_match = co_pattern.match(line)
            if co_match:
                learning_outcomes.append(line)
                continue

            # Check definitions
            def_match = definition_pattern.match(line)
            if def_match and len(line) < 300:
                definitions.append({
                    "term": def_match.group(1).strip(),
                    "definition": def_match.group(2).strip()
                })
                continue

            # Check formulas (lines with equals and math symbols)
            if "=" in line and any(sym in line for sym in ("+", "-", "*", "/", "^", "cos", "sin", "sqrt", "ohm", "pi", "alpha", "beta")):
                f_match = formula_pattern.search(line)
                if f_match and len(line) < 120:
                    formulas.append({
                        "name": f_match.group(1).strip(),
                        "formula": line,
                        "context": "Standard mathematical/engineering relation"
                    })
                    continue

            # Topics: bullet points or short title-like lines
            if line.startswith(("-", "•", "*", "1.", "2.", "3.", "4.", "5.", "6.", "7.", "8.", "9.", "10.")):
                clean_top = re.sub(r'^[\-\•\*\d\.]+\s*', '', line).strip()
                if 4 < len(clean_top) < 100:
                    topics.append(clean_top)
            elif 5 < len(line) < 60 and not line.endswith((".", "?", ":")):
                if len(topics) < 25:
                    topics.append(line)

        summary = (
            f"Curriculum material for {subject} ({title}). Covers "
            f"{len(units) or 1} unit(s), {len(topics)} core topics, and "
            f"{len(formulas)} key formulas/relations."
        )

        return {
            "summary": summary,
            "units": units or ([unit_topic] if unit_topic else ["General Unit"]),
            "topics": topics[:30],
            "key_definitions": definitions[:15],
            "key_formulas": formulas[:20],
            "learning_outcomes": learning_outcomes[:10]
        }

    # -------------------------------------------------------------------------
    # RETRIEVAL & QUERYING METHODS
    # -------------------------------------------------------------------------
    @classmethod
    def get_all_materials(
        cls,
        subject_code: Optional[str] = None,
        unit_topic: Optional[str] = None,
        material_type: Optional[str] = None,
        assessment_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Retrieves materials with optional filters, sorted by creation date."""
        supabase = db_manager.get_supabase(use_service_role=True)
        if supabase:
            try:
                query = supabase.table("course_materials").select("*").order("created_at", desc=True)
                if subject_code:
                    query = query.ilike("subject_code", f"%{subject_code.strip()}%")
                if unit_topic:
                    query = query.ilike("unit_topic", f"%{unit_topic.strip()}%")
                if material_type:
                    query = query.eq("material_type", material_type.strip().lower())
                if assessment_id:
                    query = query.eq("assessment_id", assessment_id)
                res = query.execute()
                if res.data is not None:
                    return res.data
            except Exception as e:
                logger.warning(f"Supabase get_all_materials query error: {e}")

        # Local mode filter
        materials = list(_local_materials.values())
        if subject_code:
            s_low = subject_code.strip().lower()
            materials = [m for m in materials if s_low in m.get("subject_code", "").lower()]
        if unit_topic:
            u_low = unit_topic.strip().lower()
            materials = [m for m in materials if u_low in m.get("unit_topic", "").lower()]
        if material_type:
            t_low = material_type.strip().lower()
            materials = [m for m in materials if m.get("material_type", "").lower() == t_low]
        if assessment_id:
            materials = [m for m in materials if m.get("assessment_id") == assessment_id]

        # Sort newest first
        materials.sort(key=lambda m: m.get("created_at", ""), reverse=True)
        return materials

    @classmethod
    def get_material_by_id(cls, material_id: str) -> Optional[Dict[str, Any]]:
        """Retrieves a single material record by ID."""
        if not material_id:
            return None

        supabase = db_manager.get_supabase(use_service_role=True)
        if supabase:
            try:
                res = supabase.table("course_materials").select("*").eq("id", material_id).execute()
                if res.data and len(res.data) > 0:
                    return res.data[0]
            except Exception as e:
                logger.warning(f"Supabase get_material_by_id error: {e}")

        return _local_materials.get(material_id)

    @classmethod
    def delete_material(cls, material_id: str) -> Tuple[bool, str]:
        """Deletes a material record and removes its physical file from disk."""
        mat = cls.get_material_by_id(material_id)
        if not mat:
            return False, "Material not found."

        # Remove file from disk
        rel_path = mat.get("file_path", "")
        if rel_path:
            disk_path = os.path.join(
                os.path.dirname(os.path.dirname(__file__)),
                rel_path.lstrip("/").replace("/", os.sep)
            )
            if os.path.exists(disk_path):
                try:
                    os.remove(disk_path)
                except Exception as e:
                    logger.warning(f"Could not remove physical file {disk_path}: {e}")

        # Remove from Supabase
        supabase = db_manager.get_supabase(use_service_role=True)
        if supabase:
            try:
                supabase.table("course_materials").delete().eq("id", material_id).execute()
            except Exception as e:
                logger.warning(f"Supabase delete_material error: {e}")

        # Remove from local memory & manifest
        if material_id in _local_materials:
            del _local_materials[material_id]
            _save_local_manifest()

        return True, "Material deleted successfully."

    @classmethod
    def attach_to_assessment(cls, material_id: str, assessment_id: str) -> Tuple[bool, str]:
        """Links a material to a specific assessment."""
        supabase = db_manager.get_supabase(use_service_role=True)
        if supabase:
            try:
                supabase.table("course_materials").update({"assessment_id": assessment_id}).eq("id", material_id).execute()
            except Exception as e:
                logger.warning(f"Supabase attach_to_assessment error: {e}")

        if material_id in _local_materials:
            _local_materials[material_id]["assessment_id"] = assessment_id
            _save_local_manifest()
            return True, "Material attached to assessment."

        return False, "Material not found."

    # -------------------------------------------------------------------------
    # STRUCTURED CONTEXT RETRIEVAL (FOR AI QUESTION GENERATION & EVALUATION)
    # -------------------------------------------------------------------------
    @classmethod
    def get_structured_context_for_assessment(
        cls,
        assessment_id: Optional[str] = None,
        subject_code: Optional[str] = None,
        unit_topic: Optional[str] = None,
        max_chars: int = 5000
    ) -> str:
        """
        Synthesizes a token-optimized reference context string from matching
        uploaded syllabus, textbook notes, and formula sheets.
        Used by AIService to strictly ground Question Generation and Student Evaluation.
        """
        materials = []

        # 1. Prefer materials explicitly attached to this assessment
        if assessment_id:
            materials.extend(cls.get_all_materials(assessment_id=assessment_id))

        # 2. Also retrieve materials matching this subject_code or unit_topic
        if len(materials) < 3 and (subject_code or unit_topic):
            matches = cls.get_all_materials(subject_code=subject_code, unit_topic=unit_topic)
            for m in matches:
                if not any(existing["id"] == m["id"] for existing in materials):
                    materials.append(m)

        if not materials:
            return ""

        context_parts = []
        context_parts.append("=== OFFICIAL COURSE SYLLABUS & STUDY MATERIAL REFERENCE ===")

        for idx, m in enumerate(materials[:3], 1):
            struct = m.get("structured_content") or {}
            title = m.get("title", "Course Material")
            m_type = (m.get("material_type") or "Material").upper()
            sub = m.get("subject_code", "")
            unit = m.get("unit_topic", "")

            block = [f"\n--- [{idx}] {title} ({m_type}) | Subject: {sub} | Unit: {unit} ---"]

            summary = struct.get("summary")
            if summary:
                block.append(f"Summary: {summary}")

            units = struct.get("units", [])
            if units:
                block.append(f"Syllabus Units: {', '.join(units[:5])}")

            topics = struct.get("topics", [])
            if topics:
                block.append(f"Covered Topics: {', '.join(topics[:12])}")

            formulas = struct.get("key_formulas", [])
            if formulas:
                f_list = [f"{f.get('name', 'Formula')}: {f.get('formula', '')}" for f in formulas[:8]]
                block.append("Key Equations/Formulas:\n" + "\n".join(f"  • {item}" for item in f_list))

            definitions = struct.get("key_definitions", [])
            if definitions:
                d_list = [f"{d.get('term', '')} - {d.get('definition', '')}" for d in definitions[:6]]
                block.append("Core Definitions:\n" + "\n".join(f"  • {item}" for item in d_list))

            context_parts.append("\n".join(block))

        full_context = "\n".join(context_parts)
        if len(full_context) > max_chars:
            full_context = full_context[:max_chars] + "\n... [truncated for context efficiency]"

        return full_context

