import io
import os
import re
import json
import logging
from typing import List, Dict, Any, Tuple, Optional
from config import Config
from services.ai_service import AIService

logger = logging.getLogger("bea.doc_parser")

class DocumentQuestionParser:
    """
    Universal Parser for Question Papers uploaded as:
    - Images: JPG, JPEG, PNG, WEBP
    - Documents: PDF (.pdf), Word (.docx, .doc)

    Extracts genuine assessment questions, options for MCQs, marks, and model answers.
    Strips out exam metadata, college headers, instructions, student info, and signatures.
    Supports AI-assisted extraction and offline heuristic parsing.
    """

    ALLOWED_EXTENSIONS = {
        "jpg", "jpeg", "png", "webp", "pdf", "docx", "doc"
    }

    # Common exam noise lines to filter out in heuristic parsing
    NOISE_PATTERNS = [
        re.compile(r'^\s*(?:university|college|institute|polytechnic|school|academy|department)\b', re.I),
        re.compile(r'^\s*(?:mid[- ]?term|semester|final|annual|unit\s*test|slip\s*test|examination|internal\s*assessment)\b', re.I),
        re.compile(r'^\s*(?:course|subject|branch|regulation|code)\s*[:\-]', re.I),
        re.compile(r'^\s*(?:roll\s*no|pin\s*no|reg(?:istration)?\s*no|student\s*name)\s*[:\-]', re.I),
        re.compile(r'^\s*(?:date|time|duration|max(?:imum)?\s*marks|total\s*marks)\s*[:\-]', re.I),
        re.compile(r'^\s*(?:instructions?|general\s*instructions?|note)\s*[:\-]', re.I),
        re.compile(r'^\s*(?:answer\s*all|answer\s*any|all\s*questions\s*carry|draw\s*neat\s*diagrams?)\b', re.I),
        re.compile(r'^\s*(?:page\s*\d+\s*(?:of\s*\d+)?|turn\s*over|p\.?t\.?o\.?|all\s*the\s*best)\b', re.I),
        re.compile(r'^\s*(?:signature\s*of\s*(?:the\s*)?(?:invigilator|examiner|head))\b', re.I),
        re.compile(r'^\s*(?:[-=_*~]{3,})\s*$'),  # Horizontal dividers
    ]

    @classmethod
    def is_allowed_file(cls, filename: str) -> bool:
        if not filename or "." not in filename:
            return False
        ext = filename.rsplit(".", 1)[1].lower()
        return ext in cls.ALLOWED_EXTENSIONS

    @classmethod
    def parse_uploaded_file(
        cls,
        file_bytes: bytes,
        filename: str,
        default_marks: float = 5.0,
        subject: str = ""
    ) -> Tuple[bool, List[Dict[str, Any]], str]:
        """
        Parses an uploaded file (Image, PDF, or Word) into structured question objects.
        Returns: (success, questions_list, message)
        """
        if not file_bytes:
            return False, [], "The uploaded file is empty."

        ext = filename.rsplit(".", 1)[1].lower() if "." in filename else ""
        if ext not in cls.ALLOWED_EXTENSIONS:
            return False, [], f"Unsupported file type (.{ext}). Allowed: JPG, PNG, PDF, DOCX."

        try:
            if ext in ("jpg", "jpeg", "png", "webp"):
                return cls._parse_image(file_bytes, ext, default_marks, subject)
            elif ext == "pdf":
                return cls._parse_pdf(file_bytes, default_marks, subject)
            elif ext in ("docx", "doc"):
                return cls._parse_docx(file_bytes, default_marks, subject)
            else:
                return False, [], f"Unsupported extension: {ext}"
        except Exception as e:
            logger.error(f"Error parsing document {filename}: {e}", exc_info=True)
            return False, [], f"Failed to parse document: {str(e)}"

    # -------------------------------------------------------------------------
    # IMAGE PARSING (Multimodal AI)
    # -------------------------------------------------------------------------
    @classmethod
    def _parse_image(
        cls,
        image_bytes: bytes,
        ext: str,
        default_marks: float = 5.0,
        subject: str = ""
    ) -> Tuple[bool, List[Dict[str, Any]], str]:
        """Parses a single exam paper image using Multimodal AI."""
        return cls._parse_multiple_images([image_bytes], default_marks, subject, ext=ext)

    @classmethod
    def _parse_multiple_images(
        cls,
        images_list: List[bytes],
        default_marks: float = 5.0,
        subject: str = "",
        ext: str = "jpeg"
    ) -> Tuple[bool, List[Dict[str, Any]], str]:
        """Parses one or multiple exam paper images using Multimodal AI."""
        import base64
        from PIL import Image

        # Check AI availability first
        try:
            key = Config.GEMINI_API_KEY.strip()
            if Config.is_placeholder(key):
                return False, [], (
                    "AI image analysis requires a Gemini API key. "
                    "Please go to Settings → Gemini API Key to enable it, "
                    "or upload a PDF with selectable text / a Word (.docx) document instead."
                )
        except Exception:
            pass

        valid_images = []
        for raw in images_list[:8]:  # Process up to 8 pages/images per upload
            try:
                img = Image.open(io.BytesIO(raw))
                img.verify()
                valid_images.append(raw)
            except Exception as e:
                logger.warning(f"Skipping corrupt image part: {e}")

        if not valid_images:
            return False, [], "No clear or valid images could be processed from the file."

        prompt_system = (
            "You are an expert academic exam paper digitizer and parser.\n"
            "Your task is to analyze the uploaded question paper page(s) and extract ALL genuine assessment questions.\n\n"
            "CRITICAL CLEANING RULES:\n"
            "1. STRICTLY REMOVE ALL non-question noise:\n"
            "   - University / College / School names, headers, logos, and department labels\n"
            "   - Exam metadata: 'MID TERM EXAMINATION', 'Date', 'Time', 'Branch', 'Subject Code', 'Semester'\n"
            "   - General guidelines: 'Answer any 5 questions', 'All questions carry equal marks'\n"
            "   - Student fields: 'Name:', 'Roll No / PIN:', 'Signature of Invigilator'\n"
            "   - Footers: 'Page 1 of 2', 'PTO', 'All the best'\n"
            "2. Identify each question's type: 'mcq' (if 4 options A,B,C,D are present) or 'descriptive'.\n"
            "3. Extract or assign realistic marks (e.g. from [5M] or (10 marks); default 1 for MCQ, 5 for descriptive).\n"
            "4. For MCQs: accurately parse option_a, option_b, option_c, option_d and indicate correct_option ('A'/'B'/'C'/'D').\n"
            "5. For descriptive: provide complete technical question text, official model answer, and marking criteria.\n"
            "Return strictly a valid JSON array of question objects without markdown wrapping or chat."
        )

        user_content = [
            prompt_system,
            f"Subject context: {subject or 'Academic Examination'}. Default marks: {default_marks}."
        ]

        for p_idx, img_bytes in enumerate(valid_images, 1):
            mime = f"image/{'png' if ext.lower() == 'png' else 'jpeg'}"
            b64_data = base64.b64encode(img_bytes).decode("utf-8")
            if len(valid_images) > 1:
                user_content.append(f"Question Paper Page {p_idx}:")
            user_content.append({"mime_type": mime, "data": b64_data})

        try:
            raw_text, used_prov, used_model, _ = AIService._generate_with_ai(
                contents=user_content,
                json_output=True,
                temperature=0.1
            )
            parsed_questions = cls._clean_and_parse_json_response(raw_text, default_marks)
            if parsed_questions:
                prov_name = AIService.PROVIDER_DISPLAY_NAMES.get(used_prov, used_prov)
                return True, parsed_questions, f"Extracted {len(parsed_questions)} questions via {prov_name} ({used_model})."
            else:
                return False, [], (
                    "The AI analyzed the document image but could not find recognizable numbered questions. "
                    "Please ensure the image is clear and contains numbered questions, or try uploading a PDF/Word document."
                )
        except Exception as e:
            logger.warning(f"AI image parsing failed: {e}")
            err_str = str(e)
            if any(kw in err_str.lower() for kw in ("api_key", "credential", "invalid_api", "api key")):
                return False, [], (
                    "AI API key is not configured or is invalid. "
                    "Please go to Settings → Gemini API Key to enable AI-based image extraction. "
                    "For PDF/Word files, the rule-based parser works without an API key."
                )
            return False, [], f"AI image analysis failed: {err_str[:200]}. Try uploading a PDF or Word document instead."

    # -------------------------------------------------------------------------
    # PDF PARSING
    # -------------------------------------------------------------------------
    @classmethod
    def _parse_pdf(
        cls,
        pdf_bytes: bytes,
        default_marks: float = 5.0,
        subject: str = ""
    ) -> Tuple[bool, List[Dict[str, Any]], str]:
        """Extracts text from PDF and parses questions using AI or heuristics."""
        import pypdf

        extracted_text = ""
        extracted_images = []
        num_pages = 0
        try:
            reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
            num_pages = len(reader.pages)
            for page_idx, page in enumerate(reader.pages):
                page_text = page.extract_text() or ""
                extracted_text += f"\n--- Page {page_idx + 1} ---\n" + page_text
                # Also collect any embedded page images
                try:
                    for img in page.images:
                        extracted_images.append(img.data)
                except Exception:
                    pass
        except Exception as e:
            logger.error(f"pypdf extraction error: {e}")
            return False, [], f"Could not read PDF: {str(e)}"

        clean_text = extracted_text.strip()
        logger.info(f"PDF extracted {len(clean_text)} chars from {num_pages} pages, {len(extracted_images)} embedded images")

        # If selectable text is present, attempt text parsing first
        if len(clean_text) >= 30:
            ok, questions, msg = cls._parse_extracted_text(clean_text, default_marks, subject, source_format="PDF")
            if ok and questions:
                return ok, questions, msg
            # Text parsing failed — try embedded images as fallback
            if extracted_images:
                logger.info("Text-based extraction found no questions; trying embedded PDF images via AI.")
                ok2, qs2, msg2 = cls._parse_multiple_images(extracted_images, default_marks, subject)
                if ok2 and qs2:
                    return ok2, qs2, msg2
            # Return partial extraction with the raw text so lecturer can review
            logger.warning("All parsing strategies exhausted for PDF with text content.")
            return False, [], (
                f"The PDF text was extracted ({len(clean_text)} characters, {num_pages} pages) but no "
                "numbered questions were detected automatically. This may happen if:\n"
                "• Questions are numbered in a non-standard format (e.g. roman numerals, letters, or inline)\n"
                "• The AI API key is not configured (Settings → Gemini API Key)\n"
                "• The PDF uses complex multi-column layouts\n\n"
                "Tip: Try uploading the Word (.docx) version, or add questions manually using '+ Add Question'."
            )

        # If text was sparse or yielded 0 questions, check for scanned/embedded page images
        if extracted_images:
            logger.info("PDF has little/no selectable text — trying embedded images.")
            return cls._parse_multiple_images(extracted_images, default_marks, subject)

        return False, [], (
            f"The uploaded PDF ({num_pages} page{'s' if num_pages != 1 else ''}) appears to be a scanned image "
            "document with no readable text layer and no extractable embedded images.\n"
            "Please try:\n"
            "• A PDF with selectable/digital text\n"
            "• The original Word (.docx) document\n"
            "• Taking a photo of the question paper and uploading as JPG/PNG"
        )

    # -------------------------------------------------------------------------
    # WORD (.DOCX) PARSING
    # -------------------------------------------------------------------------
    @classmethod
    def _parse_docx(
        cls,
        docx_bytes: bytes,
        default_marks: float = 5.0,
        subject: str = ""
    ) -> Tuple[bool, List[Dict[str, Any]], str]:
        """Extracts text and tables from Word document and parses questions."""
        import docx

        extracted_text = ""
        try:
            doc = docx.Document(io.BytesIO(docx_bytes))
            for p in doc.paragraphs:
                text = p.text.strip()
                if text:
                    extracted_text += text + "\n"

            for table in doc.tables:
                for row in table.rows:
                    row_texts = [cell.text.strip() for cell in row.cells if cell.text.strip()]
                    if row_texts:
                        extracted_text += " | ".join(row_texts) + "\n"

        except Exception as e:
            logger.error(f"python-docx extraction error: {e}")
            return False, [], f"Could not read Word document: {e}"

        clean_text = extracted_text.strip()
        if len(clean_text) < 30:
            return False, [], "The uploaded Word document contains no readable text."

        return cls._parse_extracted_text(clean_text, default_marks, subject, source_format="Word (.docx)")

    # -------------------------------------------------------------------------
    # SHARED TEXT QUESTION EXTRACTION (AI + HEURISTIC FALLBACK)
    # -------------------------------------------------------------------------
    @classmethod
    def _parse_extracted_text(
        cls,
        text: str,
        default_marks: float = 5.0,
        subject: str = "",
        source_format: str = "Document"
    ) -> Tuple[bool, List[Dict[str, Any]], str]:
        """
        Parses text extracted from PDF or Word into structured question objects.
        First tries AI extraction with header noise removal; falls back to heuristic parsing.
        """
        # Try AI extraction first if key configured
        ai_available = False
        try:
            key = Config.GEMINI_API_KEY.strip()
            ai_available = key and not Config.is_placeholder(key)
        except Exception:
            pass

        if ai_available:
            try:
                ai_questions = cls._extract_with_ai(text, default_marks, subject)
                if ai_questions:
                    return True, ai_questions, f"Successfully detected and extracted {len(ai_questions)} questions from {source_format} via AI."
                logger.info("AI returned empty questions list; falling back to heuristics.")
            except Exception as ai_err:
                logger.warning(f"AI extraction failed: {ai_err}; falling back to heuristics.")
        else:
            logger.info("AI API key not configured; using heuristic parser directly.")

        # Offline / Fallback Heuristic Parser
        logger.info("Using smart heuristic parser for document questions.")
        heuristic_questions = cls._extract_with_heuristics(text, default_marks)
        if heuristic_questions:
            note = "" if ai_available else " (Configure Gemini API key in Settings for AI-enhanced extraction)"
            return True, heuristic_questions, f"Extracted {len(heuristic_questions)} questions from {source_format} (Rule-based parser){note}."

        return False, [], f"No questions could be detected in the uploaded {source_format}."

    @classmethod
    def _extract_with_ai(
        cls,
        raw_text: str,
        default_marks: float = 5.0,
        subject: str = ""
    ) -> List[Dict[str, Any]]:
        """Sends extracted exam text to AI with strict noise filtering."""
        # Truncate text if excessively long to avoid token explosion
        truncated_text = raw_text[:20000]

        system_instruction = (
            "You are an academic examination digitizer.\n"
            "Read this raw exam paper text, filter out all noise, and extract ONLY the assessment questions.\n\n"
            "STRICT FILTERING RULES:\n"
            "1. REMOVE institution names, college headers, university logos, department names.\n"
            "2. REMOVE exam metadata: 'Semester', 'Date', 'Time Allowed', 'Course Code', 'Max Marks'.\n"
            "3. REMOVE student blanks: 'Name', 'Roll Number', 'Registration No', 'PIN', 'Invigilator Signature'.\n"
            "4. REMOVE instructions: 'Answer all questions', 'Each question carries 5 marks', 'Part A / Section B'.\n"
            "5. REMOVE footers: 'Page 1 of 3', 'Turn Over', 'End of Paper'.\n\n"
            "For each genuine question, extract:\n"
            "- question_number: string (e.g. '1', '2a', 'Q3')\n"
            "- question_type: 'mcq' if 4 choices are given, else 'descriptive'\n"
            "- question_text: clean, unambiguous question statement\n"
            "- max_marks: float (extract from paper if given like [5M], (10), else use default)\n"
            "- For MCQ: option_a, option_b, option_c, option_d, correct_option ('A'/'B'/'C'/'D'), model_answer\n"
            "- For Descriptive: model_answer, criteria: list of [{criterion_title, max_marks, description}]\n"
            "Return strictly a valid JSON array of question objects."
        )

        user_prompt = f"Subject context: {subject or 'Academic Curriculum'}\nDefault marks per question: {default_marks}\n\nExam Paper Text:\n{truncated_text}"

        try:
            raw_json, _, _, _ = AIService._generate_with_ai(
                contents=[system_instruction, user_prompt],
                json_output=True,
                temperature=0.1
            )
            return cls._clean_and_parse_json_response(raw_json, default_marks)
        except Exception as e:
            logger.warning(f"AI text extraction error: {e}")
            return []

    # -------------------------------------------------------------------------
    # SMART OFFLINE HEURISTIC PARSER
    # -------------------------------------------------------------------------
    @classmethod
    def _extract_with_heuristics(cls, raw_text: str, default_marks: float = 5.0) -> List[Dict[str, Any]]:
        """
        Regex-based question identifier that skips headers, instructions, and metadata.
        Handles diverse formats: "1.", "Q1.", "Q.1", "i.", "ii.", "a)", "(1)", "Question 1:".
        """
        lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
        
        # Step 1: Filter out noise lines
        clean_lines: List[str] = []
        for line in lines:
            is_noise = any(pattern.search(line) for pattern in cls.NOISE_PATTERNS)
            if not is_noise:
                clean_lines.append(line)

        # Step 2: Identify question boundaries with broad pattern coverage
        # Matches: "1.", "2)", "(3)", "Q1", "Q.1:", "Q1.", "Question 1:", "1a.", roman "i.", "ii."
        q_start_pattern = re.compile(
            r'^(?:'
            r'Q(?:uestion)?\s*\.?\s*(\d+[a-z]?)\s*[.:\)]'   # Q1. / Q.1 / Question 1:
            r'|(\d+[a-z]?)\s*[.)\s]\s+'                       # 1. / 2) / 1a. with space after
            r'|\((\d+[a-z]?)\)\s+'                             # (1) / (2a)
            r'|(vi{0,3}|i{1,3}v?|iv|ix|xl|x{1,3})\s*[.)]\s+' # Roman i. ii. iii. iv.
            r')',
            re.I
        )
        
        # Marks pattern: "[5M]", "(5 Marks)", "[10]", "(10M)"
        marks_pattern = re.compile(r'\[\s*(\d+(?:\.\d+)?)\s*(?:marks?|m)?\s*\]|\(\s*(\d+(?:\.\d+)?)\s*(?:marks?|m)?\s*\)', re.I)
        
        # Option pattern: "(A)", "A.", "A)", "a)"
        option_pattern = re.compile(r'^(?:\(?([A-Da-d])\)|\b([A-Da-d])[\.\:])\s*(.*)')

        questions: List[Dict[str, Any]] = []
        current_q: Optional[Dict[str, Any]] = None

        for line in clean_lines:
            q_match = q_start_pattern.match(line)
            if q_match:
                # Save previous question
                if current_q and current_q.get("question_text"):
                    questions.append(cls._finalize_heuristic_question(current_q, default_marks))

                # Extract question number from whichever group matched
                matched_groups = [g for g in q_match.groups() if g]
                q_num = matched_groups[0].strip().rstrip('.):') if matched_groups else str(len(questions) + 1)
                # Remainder is everything after the matched prefix
                remainder = line[q_match.end():].strip()

                # Check for marks in statement
                m_match = marks_pattern.search(remainder)
                q_marks = default_marks
                if m_match:
                    try:
                        q_marks = float(m_match.group(1) or m_match.group(2))
                    except (ValueError, TypeError):
                        q_marks = default_marks
                    remainder = marks_pattern.sub("", remainder).strip()

                current_q = {
                    "question_number": str(q_num),
                    "question_text": remainder,
                    "max_marks": q_marks,
                    "question_type": "descriptive",
                    "options": {},
                    "model_answer": "",
                    "criteria": []
                }
                continue

            if current_q:
                # Check for MCQ option: A), B), C), D)
                opt_match = option_pattern.match(line)
                if opt_match:
                    letter = (opt_match.group(1) or opt_match.group(2) or "").upper()
                    opt_text = opt_match.group(3) or ""
                    if letter in ("A", "B", "C", "D"):
                        current_q["question_type"] = "mcq"
                        current_q["options"][letter] = opt_text
                        continue

                # Check if line contains answer key
                ans_match = re.search(r'(?:correct\s*option|answer|key)\s*[:\-]\s*\(?([A-Da-d])\)?', line, re.I)
                if ans_match and current_q.get("question_type") == "mcq":
                    current_q["correct_option"] = ans_match.group(1).upper()
                    continue

                # Append to current question text
                if not current_q["options"]:
                    current_q["question_text"] += " " + line
                elif current_q["question_type"] == "mcq":
                    # Append to last added option
                    last_opt = list(current_q["options"].keys())[-1] if current_q["options"] else None
                    if last_opt:
                        current_q["options"][last_opt] += " " + line

        if current_q and current_q.get("question_text"):
            questions.append(cls._finalize_heuristic_question(current_q, default_marks))

        return questions

    @classmethod
    def _finalize_heuristic_question(cls, q: Dict[str, Any], default_marks: float) -> Dict[str, Any]:
        """Formats and standardizes a single heuristically parsed question."""
        is_mcq = q.get("question_type") == "mcq" and len(q.get("options", {})) >= 2
        q_type = "mcq" if is_mcq else "descriptive"
        marks = float(q.get("max_marks", 1.0 if is_mcq else default_marks))

        final_q: Dict[str, Any] = {
            "question_number": str(q.get("question_number", "1")),
            "question_type": q_type,
            "question_text": q.get("question_text", "").strip(),
            "max_marks": marks,
            "difficulty": "medium",
            "requires_diagram": False,
            "requires_numerical": bool(re.search(r'\b(?:calculate|determine|derive|find|compute|formula)\b', q.get("question_text", ""), re.I)),
        }

        if is_mcq:
            opts = q.get("options", {})
            final_q["option_a"] = opts.get("A", "")
            final_q["option_b"] = opts.get("B", "")
            final_q["option_c"] = opts.get("C", "")
            final_q["option_d"] = opts.get("D", "")
            final_q["correct_option"] = q.get("correct_option", "A")
            final_q["model_answer"] = f"Option {final_q['correct_option']}: {opts.get(final_q['correct_option'], '')}"
            final_q["criteria"] = [
                {"criterion_title": "Correct Option Selection", "max_marks": marks, "description": "Accurate option choice"}
            ]
        else:
            final_q["model_answer"] = f"Comprehensive solution covering key principles, formula substitution, and explanation for {final_q['question_text'][:50]}..."
            part_m = round(marks / 2.0, 1) if marks >= 2 else marks
            final_q["criteria"] = [
                {"criterion_title": "Core Concept & Principle", "max_marks": part_m, "description": "Accurate technical definition and governing principles."},
                {"criterion_title": "Detailed Working / Analysis", "max_marks": round(marks - part_m, 1), "description": "Complete steps, derivations, and conclusions."}
            ]

        return final_q

    # -------------------------------------------------------------------------
    # JSON RESPONSE CLEANER & VALIDATOR
    # -------------------------------------------------------------------------
    @classmethod
    def _clean_and_parse_json_response(cls, raw_json: str, default_marks: float = 5.0) -> List[Dict[str, Any]]:
        """Parses and sanitizes AI JSON output into standardized question objects."""
        clean = raw_json.strip()
        if clean.startswith("```"):
            clean = re.sub(r'^```(?:json)?\s*', '', clean)
            clean = re.sub(r'\s*```$', '', clean)

        try:
            parsed = json.loads(clean.strip())
        except Exception:
            # Try finding array inside text
            m = re.search(r'\[.*\]', clean, re.DOTALL)
            if m:
                try:
                    parsed = json.loads(m.group(0))
                except Exception:
                    return []
            else:
                return []

        if not isinstance(parsed, list):
            if isinstance(parsed, dict) and "questions" in parsed:
                parsed = parsed["questions"]
            else:
                return []

        validated: List[Dict[str, Any]] = []
        for idx, item in enumerate(parsed, start=1):
            if not isinstance(item, dict):
                continue
            q_text = str(item.get("question_text") or item.get("title") or "").strip()
            if not q_text:
                continue

            q_type = str(item.get("question_type") or "descriptive").lower().strip()
            if q_type not in ("mcq", "descriptive"):
                q_type = "mcq" if (item.get("option_a") or item.get("options")) else "descriptive"

            try:
                m_val = float(item.get("max_marks") or item.get("marks") or (1.0 if q_type == "mcq" else default_marks))
            except (ValueError, TypeError):
                m_val = 1.0 if q_type == "mcq" else default_marks

            q_obj: Dict[str, Any] = {
                "question_number": str(item.get("question_number") or item.get("n") or idx),
                "question_type": q_type,
                "question_text": q_text,
                "max_marks": m_val,
                "difficulty": str(item.get("difficulty") or "medium").lower(),
                "requires_diagram": bool(item.get("requires_diagram")),
                "requires_numerical": bool(item.get("requires_numerical")),
                "model_answer": str(item.get("model_answer") or item.get("answer") or "").strip(),
            }

            if q_type == "mcq":
                opts = item.get("options", {})
                if isinstance(opts, dict):
                    q_obj["option_a"] = str(opts.get("A") or opts.get("a") or item.get("option_a") or "")
                    q_obj["option_b"] = str(opts.get("B") or opts.get("b") or item.get("option_b") or "")
                    q_obj["option_c"] = str(opts.get("C") or opts.get("c") or item.get("option_c") or "")
                    q_obj["option_d"] = str(opts.get("D") or opts.get("d") or item.get("option_d") or "")
                elif isinstance(opts, list) and len(opts) >= 2:
                    q_obj["option_a"] = str(opts[0])
                    q_obj["option_b"] = str(opts[1])
                    q_obj["option_c"] = str(opts[2]) if len(opts) > 2 else ""
                    q_obj["option_d"] = str(opts[3]) if len(opts) > 3 else ""
                else:
                    q_obj["option_a"] = str(item.get("option_a") or "")
                    q_obj["option_b"] = str(item.get("option_b") or "")
                    q_obj["option_c"] = str(item.get("option_c") or "")
                    q_obj["option_d"] = str(item.get("option_d") or "")

                correct = str(item.get("correct_option") or item.get("correct") or "A").strip().upper()
                if correct not in ("A", "B", "C", "D"):
                    correct = "A"
                q_obj["correct_option"] = correct
                if not q_obj["model_answer"]:
                    q_obj["model_answer"] = f"Option {correct}: {q_obj.get(f'option_{correct.lower()}', '')}"

                q_obj["criteria"] = [
                    {"criterion_title": "Correct Option Selection", "max_marks": m_val, "description": "Accurate option choice"}
                ]
            else:
                crit_list = item.get("criteria") or item.get("rubric") or []
                norm_crit: List[Dict[str, Any]] = []
                if isinstance(crit_list, list):
                    for c in crit_list:
                        if isinstance(c, dict) and "criterion_title" in c:
                            try:
                                c_m = float(c.get("max_marks", 1.0))
                            except (ValueError, TypeError):
                                c_m = 1.0
                            norm_crit.append({
                                "criterion_title": str(c.get("criterion_title", "Concept")).strip(),
                                "max_marks": c_m,
                                "description": str(c.get("description", ""))
                            })
                        elif isinstance(c, (list, tuple)) and len(c) >= 2:
                            try:
                                c_m = float(c[1])
                            except (ValueError, TypeError):
                                c_m = 1.0
                            norm_crit.append({
                                "criterion_title": str(c[0]).strip(),
                                "max_marks": c_m,
                                "description": "Criterion breakdown"
                            })

                if not norm_crit:
                    part_m = round(m_val / 2.0, 1) if m_val >= 2 else m_val
                    norm_crit = [
                        {"criterion_title": "Core Concepts & Principles", "max_marks": part_m, "description": "Accurate definitions, formulas, and concepts."},
                        {"criterion_title": "Working Steps & Solutions", "max_marks": round(m_val - part_m, 1), "description": "Clear step-by-step reasoning or derivations."}
                    ]
                q_obj["criteria"] = norm_crit

            validated.append(q_obj)

        return validated
