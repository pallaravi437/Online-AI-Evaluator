import re
import urllib.request
import urllib.error
import json
import logging
from typing import List, Dict, Any, Optional, Tuple
from bs4 import BeautifulSoup

logger = logging.getLogger("bea.html_parser")

class HtmlQuestionParser:
    """
    Parses HTML files and Google Forms links containing Multiple Choice Questions (MCQs)
    and Descriptive Questions. Supports tables, quiz containers, radio input forms,
    Google Forms public view / prefill schemas, and linear text blocks.
    Extracts Question Text, 4 Options (A, B, C, D), Correct Answer, Explanation,
    Model Answers, Rubric Criteria, and Marks.
    """

    @classmethod
    def parse_google_forms_url(cls, url: str) -> Tuple[bool, List[Dict[str, Any]], str]:
        """
        Attempts to retrieve and extract questions from a public Google Forms URL.
        Never executes scripts. Extracts questions, multiple choice options, and answers if accessible.
        If access is restricted or login-gated, explains clearly and instructs the user.
        """
        clean_url = url.strip()
        if not ("docs.google.com/forms" in clean_url or "forms.gle" in clean_url):
            return False, [], "The provided link does not appear to be a valid Google Forms URL."

        # Fetch form page with legitimate browser User-Agent
        req = urllib.request.Request(
            clean_url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
            }
        )

        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                final_url = resp.geturl()
                # Check for Google Account login redirect
                if "accounts.google.com" in final_url or "ServiceLogin" in final_url:
                    return False, [], "Access is restricted: This Google Form requires internal Google Workspace or institutional login. To import these questions, please open the form in your browser, use 'Save Page As' (HTML) or copy-paste the questions, and use the HTML / Text Upload tab."

                html_content = resp.read().decode("utf-8", errors="ignore")
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                return False, [], f"Access restricted (HTTP {e.code}): This Google Form is private or requires organizational authentication. Please export/save the form as an HTML file or copy-paste questions into the HTML / Text Import tab."
            return False, [], f"Could not reach Google Form: HTTP Error {e.code} ({e.reason})."
        except Exception as e:
            logger.error(f"Error fetching Google Form link: {e}")
            return False, [], f"Network connection error while reaching Google Forms: {str(e)}"

        # Attempt to extract FB_PUBLIC_LOAD_DATA_ JSON blob which Google Forms embeds
        match = re.search(r'FB_PUBLIC_LOAD_DATA_\s*=\s*(.*?);', html_content, re.DOTALL)
        if match:
            try:
                data = json.loads(match.group(1))
                # data[1][1] contains the list of form items
                items = data[1][1] if len(data) > 1 and len(data[1]) > 1 else []
                extracted_questions = []
                question_counter = 0

                for raw_idx, item in enumerate(items, start=1):
                    if not isinstance(item, list) or len(item) < 2:
                        continue
                    q_title = item[1] if len(item) > 1 else ""
                    if not q_title:
                        continue

                    # Filter out student administrative / metadata inputs (e.g. Student Name, PIN Number, Roll No)
                    if cls._is_metadata_field(q_title):
                        continue

                    question_counter += 1

                    # Extract explicit marks (e.g. "[10 marks]", "(5M)", "5 Marks") from title or description
                    item_desc = item[2] if len(item) > 2 and isinstance(item[2], str) else ""
                    combined_text = f"{q_title} {item_desc}"
                    marks_found = cls._extract_marks_from_text(combined_text)

                    # Extract question number if present in title (e.g. "Q1. ", "1) ")
                    clean_title = q_title.strip()
                    q_num = str(question_counter)
                    num_match = re.match(r'^(?:Q(?:uestion)?\s*)?(\d+[a-z]?)\s*[\.\:\-\)]\s*(.*)', clean_title, re.IGNORECASE)
                    if num_match:
                        q_num = num_match.group(1)
                        clean_title = num_match.group(2).strip()

                    # Strip trailing marks notation like "[5 marks]" from clean_title
                    clean_title = re.sub(r'\s*[\[\(]\s*\d+(?:\.\d+)?\s*(?:marks?|m|pts?|points?)\s*[\]\)]\s*$', '', clean_title, flags=re.IGNORECASE).strip()
                    if not clean_title:
                        clean_title = q_title.strip()

                    # item[4] contains the answer metadata for this item
                    options = []
                    correct_answer = None
                    if len(item) > 4 and isinstance(item[4], list) and len(item[4]) > 0:
                        entry = item[4][0]
                        if isinstance(entry, list) and len(entry) > 1 and isinstance(entry[1], list):
                            for opt in entry[1]:
                                if isinstance(opt, list) and len(opt) > 0:
                                    options.append(opt[0])
                            # Correct answer index
                            if len(entry) > 4 and isinstance(entry[4], list):
                                for ans_set in entry[4]:
                                    if isinstance(ans_set, list) and len(ans_set) > 1 and isinstance(ans_set[1], list):
                                        for corr in ans_set[1]:
                                            if isinstance(corr, list) and len(corr) > 0:
                                                correct_answer = corr[0]

                    if len(options) >= 4:
                        # MCQ
                        ans_letter = "A"
                        if correct_answer:
                            for i, opt in enumerate(options[:4]):
                                if opt == correct_answer:
                                    ans_letter = chr(65 + i)
                                    break
                        mcq_marks = marks_found if marks_found is not None else 1.0
                        extracted_questions.append({
                            "question_number": q_num,
                            "question_type": "mcq",
                            "question_text": clean_title,
                            "option_a": options[0] if len(options) > 0 else "",
                            "option_b": options[1] if len(options) > 1 else "",
                            "option_c": options[2] if len(options) > 2 else "",
                            "option_d": options[3] if len(options) > 3 else "",
                            "correct_option": ans_letter,
                            "max_marks": mcq_marks,
                            "model_answer": f"Option {ans_letter}: {correct_answer or options[0]}",
                            "acceptable_alternatives": "",
                            "difficulty": "medium",
                            "requires_diagram": False,
                            "requires_numerical": False,
                            "criteria": [
                                {"criterion_title": f"Correct Option Selection ({ans_letter})", "max_marks": mcq_marks, "description": "Correct MCQ choice"}
                            ]
                        })
                    else:
                        # Descriptive (text or paragraph type in Google Forms)
                        desc_marks = marks_found if marks_found is not None else 5.0
                        req_diagram = bool(re.search(r'(?:circuit|diagram|draw|sketch|schematic)', combined_text, re.IGNORECASE))
                        req_numerical = bool(re.search(r'(?:calculate|numerical|compute|find the value|derive)', combined_text, re.IGNORECASE))
                        difficulty = cls._detect_difficulty(combined_text)

                        # Check if description provides model answer or rubric notes
                        model_answer = item_desc if item_desc else ""
                        rubric_criteria = cls._parse_rubric_text(item_desc) if item_desc else []
                        if not rubric_criteria:
                            rubric_criteria = [
                                {"criterion_title": "Complete and correct answer", "max_marks": desc_marks, "description": "Full solution with key concepts"}
                            ]

                        extracted_questions.append({
                            "question_number": q_num,
                            "question_type": "descriptive",
                            "question_text": clean_title,
                            "option_a": "", "option_b": "", "option_c": "", "option_d": "",
                            "correct_option": None,
                            "max_marks": desc_marks,
                            "model_answer": model_answer,
                            "acceptable_alternatives": "",
                            "difficulty": difficulty,
                            "requires_diagram": req_diagram,
                            "requires_numerical": req_numerical,
                            "criteria": rubric_criteria
                        })

                if extracted_questions:
                    return True, extracted_questions, f"Successfully parsed {len(extracted_questions)} questions from Google Form! Correct answers and explanations are pre-set for verification."
            except Exception as jerr:
                logger.warning(f"Note parsing FB_PUBLIC_LOAD_DATA_: {jerr}")

        # Fallback to HTML DOM parsing
        parsed = cls.parse_html(html_content)
        if parsed:
            return True, parsed, f"Successfully extracted {len(parsed)} questions from Google Form HTML structure!"

        return False, [], "Could not extract questions from this Google Form: The form may be empty, require interactive JavaScript to display items, or have strict privacy settings. Please save the form as an HTML file in your browser or paste the questions into the HTML / Text box."


    @classmethod
    def parse_html(cls, html_content: str) -> List[Dict[str, Any]]:
        """
        Parses HTML string and returns a normalized list of question dicts.
        Handles:
        0. Embedded JSON/JavaScript question datasets (e.g. const data = [...]).
        1. Rendered article/div/section structured containers (.question, article, etc.).
        2. HTML Tables (only if they contain actual question statements and options, skipping blueprints).
        3. Linear paragraph / list fallback.
        """
        # 0. First check for embedded JavaScript data arrays (frequently used in interactive quiz/test HTML exports)
        js_questions = cls._parse_embedded_js(html_content)
        if js_questions:
            return js_questions

        soup = BeautifulSoup(html_content, "html.parser")
        questions = []

        # 1. Try parsing structured container blocks (.question, article, .mcq, .quiz-item, fieldset)
        block_questions = cls._parse_containers(soup)
        if block_questions:
            return block_questions

        # 2. Try parsing HTML Table format (carefully discriminating real question tables from blueprint tables)
        table_questions = cls._parse_tables(soup)
        if table_questions:
            return table_questions

        # 3. Fallback: Parse linear paragraphs / list items
        linear_questions = cls._parse_linear_text(soup)
        if linear_questions:
            return linear_questions

        return questions

    @classmethod
    def _parse_embedded_js(cls, html_content: str) -> List[Dict[str, Any]]:
        """
        Detects and parses embedded JavaScript arrays of questions like:
        const data = [ { n: 1, title: "...", marks: 5, answer: `...`, rubric: [...] }, ... ];
        """
        match = re.search(r'(?:const|let|var)\s+(?:data|questions|qData|testData)\s*=\s*(\[\s*\{.*?\}\s*\]);', html_content, re.DOTALL)
        if not match:
            match = re.search(r'(\[\s*\{\s*n\s*:\s*\d+.*?\}\s*\])', html_content, re.DOTALL)

        if not match:
            return []

        raw_array = match.group(1)

        # Split into individual item strings by matching objects
        # We find each block starting with {n: ... and ending before the next {n: or end of array
        item_blocks = re.findall(r'(\{\s*n\s*:\s*\d+.*?\}(?=\s*,\s*\{\s*n\s*:\s*\d+|\s*\]))', raw_array, re.DOTALL)
        if not item_blocks:
            # Fallback regex pattern
            item_blocks = re.findall(r'(\{\s*n\s*:\s*\d+.*?\n\})', raw_array, re.DOTALL)

        extracted = []
        for block in item_blocks:
            n_match = re.search(r'n\s*:\s*(\d+)', block)
            title_match = re.search(r'title\s*:\s*["\'](.+?)["\']', block, re.DOTALL)
            marks_match = re.search(r'marks\s*:\s*(\d+(?:\.\d+)?)', block)
            ans_match = re.search(r'answer\s*:\s*(?:`(?P<tick>.*?)`|["\'](?P<str>.*?)["\'])', block, re.DOTALL)
            rubric_match = re.search(r'rubric\s*:\s*\[(.*?)\]\s*\}', block, re.DOTALL)

            if not n_match or not title_match:
                continue

            n = n_match.group(1)
            title = title_match.group(1).strip()
            marks = float(marks_match.group(1)) if marks_match else 5.0
            raw_ans = ""
            if ans_match:
                raw_ans = (ans_match.group("tick") or ans_match.group("str") or "").strip()

            # Clean HTML tags from model answer for clean readability while preserving line breaks
            clean_ans = re.sub(r'<br\s*/?>', '\n', raw_ans)
            clean_ans = re.sub(r'</?(?:p|div|tr|li|h[1-6])>', '\n', clean_ans)
            clean_ans = re.sub(r'<[^>]+>', '', clean_ans).strip()
            clean_ans = re.sub(r'\n{3,}', '\n\n', clean_ans)

            # Parse rubric criteria
            criteria = []
            if rubric_match:
                rubric_str = rubric_match.group(1)
                r_items = re.findall(r'\[\s*["\'](.*?)["\']\s*,\s*(\d+(?:\.\d+)?)\s*\]', rubric_str)
                for r_title, r_marks in r_items:
                    criteria.append({
                        "criterion_title": r_title.strip(),
                        "max_marks": float(r_marks),
                        "description": ""
                    })

            # Check difficulty, diagrams, numerical
            diff = "medium"
            if "Easy" in block: diff = "easy"
            elif "Hard" in block: diff = "hard"

            req_diagram = bool(re.search(r'diagram\s*:\s*["\']Yes', block, re.IGNORECASE))
            req_numerical = bool(re.search(r'numerical\s*:\s*["\']Yes', block, re.IGNORECASE))

            extracted.append({
                "question_number": str(n),
                "question_type": "descriptive",
                "question_text": title,
                "option_a": "",
                "option_b": "",
                "option_c": "",
                "option_d": "",
                "correct_option": None,
                "max_marks": marks,
                "model_answer": clean_ans or title,
                "acceptable_alternatives": "",
                "difficulty": diff,
                "requires_diagram": req_diagram,
                "requires_numerical": req_numerical,
                "criteria": criteria
            })

        return extracted



    @classmethod
    def _parse_tables(cls, soup: BeautifulSoup) -> List[Dict[str, Any]]:
        """Parses HTML tables with columns like Question, Option A, Option B, etc. or descriptive question tables."""
        tables = soup.find_all("table")
        questions = []

        for table in tables:
            rows = table.find_all("tr")
            if len(rows) < 2:
                continue

            # Check header row
            header_cells = [c.get_text().strip().upper() for c in (rows[0].find_all("th") or rows[0].find_all("td"))]
            # Accept tables with QUESTION or TOPIC or MARKS headers (not just MCQ tables)
            if not any(kw in h for h in header_cells for kw in ("OPTION", "QUESTION", "ANS", "TOPIC", "MARKS", "STATEMENT", "TEXT", "MODEL")):
                continue

            col_map = {}
            for idx, h in enumerate(header_cells):
                clean_h = re.sub(r'[^A-Z0-9]', '_', h)
                if "OPTION_A" in clean_h or clean_h in ("A", "OPT_A"):
                    col_map["opt_a"] = idx
                elif "OPTION_B" in clean_h or clean_h in ("B", "OPT_B"):
                    col_map["opt_b"] = idx
                elif "OPTION_C" in clean_h or clean_h in ("C", "OPT_C"):
                    col_map["opt_c"] = idx
                elif "OPTION_D" in clean_h or clean_h in ("D", "OPT_D"):
                    col_map["opt_d"] = idx
                elif "ANS" in clean_h or "CORRECT" in clean_h:
                    col_map["answer"] = idx
                elif "MODEL" in clean_h or "EXPECTED" in clean_h or "SOLUTION" in clean_h:
                    col_map["model_answer"] = idx
                elif "MARK" in clean_h:
                    col_map["marks"] = idx
                elif "NO" in clean_h or "SL" in clean_h:
                    col_map["num"] = idx
                elif "TOPIC" in clean_h or "DESCRIPTION" in clean_h or "STATEMENT" in clean_h or "TITLE" in clean_h:
                    col_map["question"] = idx
                elif "QUESTION" in clean_h or clean_h == "Q":
                    # If this column only has "1", "2" (numbers), and another column is topic/statement, assign appropriately
                    col_map["question"] = idx
                elif "RUBRIC" in clean_h or "CRITERIA" in clean_h:
                    col_map["rubric"] = idx
                elif "DIFFICULT" in clean_h:
                    col_map["difficulty"] = idx

            # If question column has "QUESTION" and another has "TOPIC", make sure question points to the descriptive content
            for idx, h in enumerate(header_cells):
                if "TOPIC" in h or "STATEMENT" in h or "TEXT" in h or "DESCRIPTION" in h:
                    col_map["question"] = idx
                    break

            is_mcq_table = "opt_a" in col_map or "opt_b" in col_map

            for r_idx, row in enumerate(rows[1:], start=1):
                cells = [c.get_text().strip() for c in row.find_all("td")]
                if len(cells) < 2:
                    continue

                q_text = cells[col_map.get("question", 1)] if col_map.get("question", 1) < len(cells) else ""
                if not q_text:
                    continue

                q_num = str(cells[col_map["num"]]) if "num" in col_map and col_map["num"] < len(cells) else str(r_idx)
                marks = 5.0  # Default for descriptive
                if "marks" in col_map and col_map["marks"] < len(cells):
                    try:
                        marks = float(cells[col_map["marks"]])
                    except ValueError:
                        marks = 5.0

                if is_mcq_table:
                    opt_a = cells[col_map["opt_a"]] if "opt_a" in col_map and col_map["opt_a"] < len(cells) else ""
                    opt_b = cells[col_map["opt_b"]] if "opt_b" in col_map and col_map["opt_b"] < len(cells) else ""
                    opt_c = cells[col_map["opt_c"]] if "opt_c" in col_map and col_map["opt_c"] < len(cells) else ""
                    opt_d = cells[col_map["opt_d"]] if "opt_d" in col_map and col_map["opt_d"] < len(cells) else ""
                    ans = cells[col_map["answer"]] if "answer" in col_map and col_map["answer"] < len(cells) else "A"

                    if not marks or marks == 5.0:
                        marks = 1.0  # MCQ default

                    clean_ans = cls._normalize_answer_letter(ans, opt_a, opt_b, opt_c, opt_d)
                    questions.append({
                        "question_number": q_num,
                        "question_type": "mcq",
                        "question_text": q_text,
                        "option_a": opt_a, "option_b": opt_b, "option_c": opt_c, "option_d": opt_d,
                        "correct_option": clean_ans,
                        "max_marks": marks,
                        "model_answer": f"Option {clean_ans}: {cls._get_opt_val(clean_ans, opt_a, opt_b, opt_c, opt_d)}",
                        "acceptable_alternatives": "",
                        "difficulty": "medium",
                        "requires_diagram": False,
                        "requires_numerical": False,
                        "criteria": [
                            {"criterion_title": f"Correct Option Selection ({clean_ans})", "max_marks": marks, "description": "Correct MCQ choice"}
                        ]
                    })
                else:
                    # Descriptive table row
                    model_answer = cells[col_map["model_answer"]] if "model_answer" in col_map and col_map["model_answer"] < len(cells) else ""
                    rubric_text = cells[col_map["rubric"]] if "rubric" in col_map and col_map["rubric"] < len(cells) else ""
                    difficulty = "medium"
                    if "difficulty" in col_map and col_map["difficulty"] < len(cells):
                        d = cells[col_map["difficulty"]].strip().lower()
                        if d in ("easy", "medium", "hard"):
                            difficulty = d

                    # Parse rubric criteria from text (e.g. "Diagram: 2, Formula: 1, Derivation: 2")
                    criteria = cls._parse_rubric_text(rubric_text) if rubric_text else []

                    req_diagram = bool(re.search(r'(?:circuit|diagram|draw|sketch|schematic)', q_text, re.IGNORECASE))
                    req_numerical = bool(re.search(r'(?:calculate|numerical|compute|find the value|derive)', q_text, re.IGNORECASE))

                    questions.append({
                        "question_number": q_num,
                        "question_type": "descriptive",
                        "question_text": q_text,
                        "option_a": "", "option_b": "", "option_c": "", "option_d": "",
                        "correct_option": None,
                        "max_marks": marks,
                        "model_answer": model_answer,
                        "acceptable_alternatives": "",
                        "difficulty": difficulty,
                        "requires_diagram": req_diagram,
                        "requires_numerical": req_numerical,
                        "criteria": criteria if criteria else [
                            {"criterion_title": "Complete and correct answer", "max_marks": marks, "description": "Full solution with key concepts"}
                        ]
                    })

        return questions

    @classmethod
    def _parse_containers(cls, soup: BeautifulSoup) -> List[Dict[str, Any]]:
        """Parses quiz block containers (.question, .mcq, .card, fieldset, article)."""
        containers = soup.find_all(lambda tag: tag.name in ("div", "fieldset", "li", "section", "article") and any(
            c in " ".join(tag.get("class", [])).lower() or c in tag.get("id", "").lower()
            for c in ("question", "mcq", "quiz-item", "item-box", "q-card", "descriptive", "slip")
        ))
        if not containers:
            return []

        questions = []
        for idx, c in enumerate(containers, start=1):
            text_content = c.get_text()

            # Find question text (often in p, h3, h4, or strong)
            q_elem = c.find(["h3", "h4", "h5", "p", "strong", "legend"])
            q_text = q_elem.get_text().strip() if q_elem else ""
            if not q_text:
                continue

            # Skip identity / student metadata fields (e.g. Student Name, PIN Number)
            if cls._is_metadata_field(q_text):
                continue

            # Strip leading "1. " or "Q1. "
            q_num_match = re.match(r'^(?:Q(?:uestion)?\s*)?(\d+[a-z]?)\s*[\.\:\-\)]\s*(.*)', q_text, re.IGNORECASE)
            if q_num_match:
                q_num = q_num_match.group(1)
                q_text = q_num_match.group(2)
            else:
                q_num = str(idx)

            # Extract marks from text or data attribute
            marks = cls._extract_marks_from_text(text_content, c)

            # Find options
            opt_a, opt_b, opt_c, opt_d = "", "", "", ""
            list_items = c.find_all("li")
            if len(list_items) >= 4:
                opt_a = cls._clean_option_prefix(list_items[0].get_text().strip())
                opt_b = cls._clean_option_prefix(list_items[1].get_text().strip())
                opt_c = cls._clean_option_prefix(list_items[2].get_text().strip())
                opt_d = cls._clean_option_prefix(list_items[3].get_text().strip())
            else:
                # Look for labels / radio inputs
                labels = c.find_all(["label", "div"])
                label_texts = [l.get_text().strip() for l in labels if len(l.get_text().strip()) > 0]
                opts_found = cls._extract_options_from_texts(label_texts or text_content.splitlines())
                opt_a = opts_found.get("A", "")
                opt_b = opts_found.get("B", "")
                opt_c = opts_found.get("C", "")
                opt_d = opts_found.get("D", "")

            is_mcq = bool(opt_a or opt_b)

            if is_mcq:
                # MCQ question
                ans_match = re.search(r'(?:Answer|Correct|Key|Ans)\s*[\:\-\=]?\s*\(?([A-Da-d])\)?', text_content, re.IGNORECASE)
                ans = ans_match.group(1).upper() if ans_match else "A"
                clean_ans = cls._normalize_answer_letter(ans, opt_a, opt_b, opt_c, opt_d)
                questions.append({
                    "question_number": q_num,
                    "question_type": "mcq",
                    "question_text": q_text,
                    "option_a": opt_a, "option_b": opt_b, "option_c": opt_c, "option_d": opt_d,
                    "correct_option": clean_ans,
                    "max_marks": marks if marks else 1.0,
                    "model_answer": f"Option {clean_ans}: {cls._get_opt_val(clean_ans, opt_a, opt_b, opt_c, opt_d)}",
                    "acceptable_alternatives": "",
                    "difficulty": "medium",
                    "requires_diagram": False,
                    "requires_numerical": False,
                    "criteria": [
                        {"criterion_title": f"Correct Option Selection ({clean_ans})", "max_marks": marks if marks else 1.0, "description": "Correct MCQ choice"}
                    ]
                })
            else:
                # Descriptive question
                model_answer = cls._extract_model_answer(c, text_content)
                criteria = cls._extract_rubric_criteria(c, text_content)
                req_diagram = bool(re.search(r'(?:circuit|diagram|draw|sketch|schematic)', text_content, re.IGNORECASE))
                req_numerical = bool(re.search(r'(?:calculate|numerical|compute|find the value|derive)', text_content, re.IGNORECASE))
                difficulty = cls._detect_difficulty(text_content)

                if not marks:
                    marks = sum(cr["max_marks"] for cr in criteria) if criteria else 5.0

                questions.append({
                    "question_number": q_num,
                    "question_type": "descriptive",
                    "question_text": q_text,
                    "option_a": "", "option_b": "", "option_c": "", "option_d": "",
                    "correct_option": None,
                    "max_marks": marks,
                    "model_answer": model_answer,
                    "acceptable_alternatives": "",
                    "difficulty": difficulty,
                    "requires_diagram": req_diagram,
                    "requires_numerical": req_numerical,
                    "criteria": criteria if criteria else [
                        {"criterion_title": "Complete and correct answer", "max_marks": marks, "description": "Full solution with key concepts"}
                    ]
                })

        return questions

    @classmethod
    def _parse_linear_text(cls, soup: BeautifulSoup) -> List[Dict[str, Any]]:
        """Parses HTML where questions and options are simple consecutive <p> or <br> separated lines.
        Now also handles descriptive questions with model answers, marks, and rubric criteria."""
        text = soup.get_text(separator="\n")
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        questions = []
        curr_q = None

        for line in lines:
            # Check if start of a new question
            q_start = re.match(r'^(?:Q(?:uestion)?\s*)?(\d+[a-z]?)\s*[\.\:\-\)]\s*(.*)', line, re.IGNORECASE)
            if q_start:
                candidate_text = q_start.group(2) or line
                # Ignore metadata fields like "1. Student Name" or "Name"
                if cls._is_metadata_field(candidate_text):
                    continue

                if curr_q:
                    cls._finalize_linear_q(curr_q, questions)
                curr_q = {
                    "num": q_start.group(1),
                    "text": candidate_text,
                    "opt_a": "",
                    "opt_b": "",
                    "opt_c": "",
                    "opt_d": "",
                    "ans": "A",
                    "marks": None,
                    "model_answer": "",
                    "rubric_text": "",
                    "extra_text_lines": []
                }

                # Check for inline marks like "[5 marks]" or "(10 marks)"
                marks_inline = re.search(r'[\[\(]\s*(\d+(?:\.\d+)?)\s*(?:marks?|m)\s*[\]\)]', line, re.IGNORECASE)
                if marks_inline:
                    curr_q["marks"] = float(marks_inline.group(1))
                    # Clean marks notation from question text
                    curr_q["text"] = re.sub(r'\s*[\[\(]\s*\d+(?:\.\d+)?\s*(?:marks?|m)\s*[\]\)]', '', curr_q["text"]).strip()

                continue

            if not curr_q:
                continue

            # Check Option A
            opt_a_match = re.match(r'^(?:[\(\[]?[Aa][\)\]\.]\s*|A[\.\:\-\)]\s*)(.*)', line)
            if opt_a_match:
                curr_q["opt_a"] = opt_a_match.group(1) or line
                continue

            # Check Option B
            opt_b_match = re.match(r'^(?:[\(\[]?[Bb][\)\]\.]\s*|B[\.\:\-\)]\s*)(.*)', line)
            if opt_b_match:
                curr_q["opt_b"] = opt_b_match.group(1) or line
                continue

            # Check Option C
            opt_c_match = re.match(r'^(?:[\(\[]?[Cc][\)\]\.]\s*|C[\.\:\-\)]\s*)(.*)', line)
            if opt_c_match:
                curr_q["opt_c"] = opt_c_match.group(1) or line
                continue

            # Check Option D
            opt_d_match = re.match(r'^(?:[\(\[]?[Dd][\)\]\.]\s*|D[\.\:\-\)]\s*)(.*)', line)
            if opt_d_match:
                curr_q["opt_d"] = opt_d_match.group(1) or line
                continue

            # Check for model answer line FIRST (before MCQ answer check to avoid false matches)
            model_match = re.match(r'^(?:Model\s*Answer|Expected\s*Answer|Solution)\s*[\:\-\=]\s*(.*)', line, re.IGNORECASE)
            if model_match:
                curr_q["model_answer"] = model_match.group(1).strip()
                continue

            # Check for rubric/criteria line
            rubric_match = re.match(r'^(?:Rubric|Criteria|Marking\s*Scheme|Mark\s*Breakdown)\s*[\:\-\=]\s*(.*)', line, re.IGNORECASE)
            if rubric_match:
                curr_q["rubric_text"] = rubric_match.group(1).strip()
                continue

            # Check for marks line (e.g. "Marks: 5" or "[5 marks]")
            marks_match = re.match(r'^(?:Marks?|Max\s*Marks?|Points?)\s*[\:\-\=]?\s*(\d+(?:\.\d+)?)', line, re.IGNORECASE)
            if marks_match:
                curr_q["marks"] = float(marks_match.group(1))
                continue
            marks_bracket = re.match(r'^\s*[\[\(]\s*(\d+(?:\.\d+)?)\s*(?:marks?|m|points?)\s*[\]\)]', line, re.IGNORECASE)
            if marks_bracket:
                curr_q["marks"] = float(marks_bracket.group(1))
                continue

            # Check Answer: MCQ single letter only (e.g. "Answer: C" but NOT "Answer: Alpha is...")
            ans_match = re.match(r'^(?:Answer|Correct|Ans|Key)\s*[\:\-\=]?\s*\(?([A-Da-d])\)?\s*$', line, re.IGNORECASE)
            if ans_match:
                curr_q["ans"] = ans_match.group(1).upper()
                continue

            # Check for generic "Answer: <long text>" as model answer (descriptive)
            generic_ans = re.match(r'^(?:Answer)\s*[\:\-\=]\s*(.{10,})', line, re.IGNORECASE)
            if generic_ans and not curr_q["model_answer"]:
                curr_q["model_answer"] = generic_ans.group(1).strip()
                continue

            # If we're collecting model answer continuation lines (after "Model Answer:")
            if curr_q["model_answer"]:
                # Could be continuation of model answer
                curr_q["model_answer"] += "\n" + line

        if curr_q:
            cls._finalize_linear_q(curr_q, questions)

        return questions

    @classmethod
    def _finalize_linear_q(cls, curr_q: Dict[str, Any], questions: List[Dict[str, Any]]):
        is_mcq = bool(curr_q["opt_a"] or curr_q["opt_b"])

        if is_mcq:
            clean_ans = cls._normalize_answer_letter(curr_q["ans"], curr_q["opt_a"], curr_q["opt_b"], curr_q["opt_c"], curr_q["opt_d"])
            marks = curr_q["marks"] or 1.0
            questions.append({
                "question_number": curr_q["num"],
                "question_type": "mcq",
                "question_text": curr_q["text"],
                "option_a": curr_q["opt_a"],
                "option_b": curr_q["opt_b"],
                "option_c": curr_q["opt_c"],
                "option_d": curr_q["opt_d"],
                "correct_option": clean_ans,
                "max_marks": marks,
                "model_answer": f"Option {clean_ans}: {cls._get_opt_val(clean_ans, curr_q['opt_a'], curr_q['opt_b'], curr_q['opt_c'], curr_q['opt_d'])}",
                "acceptable_alternatives": "",
                "difficulty": "medium",
                "requires_diagram": False,
                "requires_numerical": False,
                "criteria": [
                    {"criterion_title": f"Correct Option Selection ({clean_ans})", "max_marks": marks, "description": "Correct MCQ choice"}
                ]
            })
        else:
            # Descriptive question
            marks = curr_q["marks"] or 5.0
            model_answer = curr_q.get("model_answer", "").strip()
            rubric_text = curr_q.get("rubric_text", "")
            criteria = cls._parse_rubric_text(rubric_text) if rubric_text else []

            req_diagram = bool(re.search(r'(?:circuit|diagram|draw|sketch|schematic)', curr_q["text"], re.IGNORECASE))
            req_numerical = bool(re.search(r'(?:calculate|numerical|compute|find the value|derive)', curr_q["text"], re.IGNORECASE))

            questions.append({
                "question_number": curr_q["num"],
                "question_type": "descriptive",
                "question_text": curr_q["text"],
                "option_a": "", "option_b": "", "option_c": "", "option_d": "",
                "correct_option": None,
                "max_marks": marks,
                "model_answer": model_answer,
                "acceptable_alternatives": "",
                "difficulty": cls._detect_difficulty(curr_q["text"]),
                "requires_diagram": req_diagram,
                "requires_numerical": req_numerical,
                "criteria": criteria if criteria else [
                    {"criterion_title": "Complete and correct answer", "max_marks": marks, "description": "Full solution with key concepts"}
                ]
            })

    # ── Helper methods ─────────────────────────────────────────────────────

    @staticmethod
    def _clean_option_prefix(text: str) -> str:
        return re.sub(r'^(?:[\(\[]?[A-Da-d][\)\]\.]\s*|\b[A-Da-d]\b[\.\:\-\)]\s*)', '', text).strip()

    @staticmethod
    def _normalize_answer_letter(ans: str, a: str, b: str, c: str, d: str) -> str:
        clean = ans.strip().upper()
        if clean in ("A", "B", "C", "D"):
            return clean
        if a and clean in a.upper():
            return "A"
        if b and clean in b.upper():
            return "B"
        if c and clean in c.upper():
            return "C"
        if d and clean in d.upper():
            return "D"
        return "A"

    @staticmethod
    def _get_opt_val(ans_letter: str, a: str, b: str, c: str, d: str) -> str:
        letter = ans_letter.upper()
        if letter == "A": return a or "Option A"
        if letter == "B": return b or "Option B"
        if letter == "C": return c or "Option C"
        if letter == "D": return d or "Option D"
        return a

    @classmethod
    def _extract_options_from_texts(cls, lines: List[str]) -> Dict[str, str]:
        opts = {}
        for line in lines:
            for ltr in ("A", "B", "C", "D"):
                if ltr not in opts:
                    m = re.match(rf'^(?:[\(\[]?{ltr}[\)\]\.]\s*|\b{ltr}\b[\.\:\-\)]\s*)(.*)', line, re.IGNORECASE)
                    if m:
                        opts[ltr] = m.group(1).strip()
        return opts

    @staticmethod
    def _extract_marks_from_text(text_content: str, element=None) -> Optional[float]:
        """Extracts marks from text patterns like [5 marks], (10M), data-marks='5', Marks: 5."""
        # Check data attributes first
        if element:
            for attr in ("data-marks", "data-max-marks", "marks"):
                val = element.get(attr)
                if val:
                    try:
                        return float(val)
                    except (ValueError, TypeError):
                        pass

        # Check text patterns
        marks_match = re.search(r'[\[\(]\s*(\d+(?:\.\d+)?)\s*(?:marks?|m|points?)\s*[\]\)]', text_content, re.IGNORECASE)
        if marks_match:
            return float(marks_match.group(1))
        marks_match2 = re.search(r'(?:Marks?|Max\s*Marks?|Points?)\s*[\:\-\=]?\s*(\d+(?:\.\d+)?)', text_content, re.IGNORECASE)
        if marks_match2:
            return float(marks_match2.group(1))
        return None

    @staticmethod
    def _extract_model_answer(element, text_content: str) -> str:
        """Extracts model answer from container element."""
        # Look for element with class/id containing 'answer' or 'solution'
        ans_elem = element.find(lambda tag: tag.name in ("div", "p", "span", "pre", "blockquote") and any(
            kw in " ".join(tag.get("class", [])).lower() or kw in tag.get("id", "").lower()
            for kw in ("answer", "solution", "model", "expected")
        ))
        if ans_elem:
            return ans_elem.get_text().strip()

        # Look for text after "Answer:", "Model Answer:", "Solution:" in the content
        ans_match = re.search(
            r'(?:Model\s*Answer|Expected\s*Answer|Answer|Solution)\s*[\:\-\=]\s*(.+?)(?=\n(?:Q(?:uestion)?\s*\d|\Z))',
            text_content, re.IGNORECASE | re.DOTALL
        )
        if ans_match:
            return ans_match.group(1).strip()

        return ""

    @staticmethod
    def _extract_rubric_criteria(element, text_content: str) -> List[Dict[str, Any]]:
        """Extracts rubric criteria from container element."""
        criteria = []

        # Look for a rubric/criteria sub-element
        rubric_elem = element.find(lambda tag: tag.name in ("div", "ul", "ol", "table") and any(
            kw in " ".join(tag.get("class", [])).lower() or kw in tag.get("id", "").lower()
            for kw in ("rubric", "criteria", "marking", "scheme")
        ))

        if rubric_elem:
            items = rubric_elem.find_all("li")
            if items:
                for item in items:
                    item_text = item.get_text().strip()
                    # Try to extract marks from item text like "Diagram: 2 marks"
                    m = re.match(r'(.+?)[\:\-]\s*(\d+(?:\.\d+)?)\s*(?:marks?|m|points?)?', item_text, re.IGNORECASE)
                    if m:
                        criteria.append({
                            "criterion_title": m.group(1).strip(),
                            "max_marks": float(m.group(2)),
                            "description": ""
                        })
                    else:
                        criteria.append({
                            "criterion_title": item_text,
                            "max_marks": 1.0,
                            "description": ""
                        })

        if not criteria:
            # Try parsing from text
            rubric_match = re.search(
                r'(?:Rubric|Criteria|Marking\s*Scheme|Mark\s*Breakdown)\s*[\:\-\=]\s*(.+?)(?=\n(?:Q(?:uestion)?\s*\d|\Z))',
                text_content, re.IGNORECASE | re.DOTALL
            )
            if rubric_match:
                criteria = HtmlQuestionParser._parse_rubric_text(rubric_match.group(1).strip())

        return criteria

    # Common metadata header fields in student forms (e.g. Student Name, PIN Number)
    METADATA_PATTERNS = [
        r'^(?:student\s+)?name(?:\s+of\s+(?:the\s+)?student)?$',
        r'^(?:full\s+)?name$',
        r'^(?:candidate\s+)?name$',
        r'^(?:enter\s+(?:your\s+)?)?(?:student\s+)?name$',
        r'^(?:student\s+)?pin(?:\s+no|\s+number)?$',
        r'^(?:enter\s+(?:your\s+)?)?pin(?:\s+no|\s+number)?$',
        r'^(?:diploma\s+)?pin(?:\s+no|\s+number)?$',
        r'^(?:roll|reg(?:istration)?|hall\s*ticket|admission|matriculation)\s*(?:no|num|number)?$',
        r'^(?:enter\s+(?:your\s+)?)?(?:roll|reg(?:istration)?)\s*(?:no|number)?$',
        r'^(?:email|e-mail)(?:\s+id|\s+address)?$',
        r'^(?:phone|mobile|contact)(?:\s+no|\s+number)?$',
        r'^(?:branch|department|section|class|semester|year)$',
    ]

    @classmethod
    def _is_metadata_field(cls, text: str) -> bool:
        """Returns True if text is a student identity / administrative field, not an exam question."""
        if not text:
            return False
        clean = re.sub(r'[^a-zA-Z0-9\s]', ' ', text).strip().lower()
        clean = re.sub(r'\s+', ' ', clean)
        for pat in cls.METADATA_PATTERNS:
            if re.match(pat, clean):
                return True
        return False

    @classmethod
    def _parse_rubric_text(cls, rubric_text: str) -> List[Dict[str, Any]]:
        """Parses rubric criteria from text like 'Diagram: 2, Formula: 1, Derivation: 2'
        or 'Diagram - 2 marks, Formula - 1 mark'."""
        criteria = []
        if not rubric_text:
            return criteria

        # Try comma/semicolon separated items
        parts = re.split(r'[;,]', rubric_text)
        for part in parts:
            part = part.strip()
            if not part:
                continue
            m = re.match(r'(.+?)[\:\-\=]\s*(\d+(?:\.\d+)?)\s*(?:marks?|m|points?)?', part, re.IGNORECASE)
            if m:
                criteria.append({
                    "criterion_title": m.group(1).strip(),
                    "max_marks": float(m.group(2)),
                    "description": ""
                })

        # If no criteria parsed, try line-by-line
        if not criteria:
            for line in rubric_text.splitlines():
                line = line.strip()
                if not line:
                    continue
                m = re.match(r'(.+?)[\:\-\=]\s*(\d+(?:\.\d+)?)\s*(?:marks?|m|points?)?', line, re.IGNORECASE)
                if m:
                    criteria.append({
                        "criterion_title": m.group(1).strip(),
                        "max_marks": float(m.group(2)),
                        "description": ""
                    })

        return criteria

    @staticmethod
    def _detect_difficulty(text: str) -> str:
        """Detects difficulty from text keywords."""
        text_lower = text.lower()
        if any(kw in text_lower for kw in ("derive", "prove", "calculate", "numerical", "complex")):
            return "hard"
        elif any(kw in text_lower for kw in ("define", "state", "list", "name", "what is")):
            return "easy"
        return "medium"
