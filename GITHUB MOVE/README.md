# BEA AI Assessment & Answer Script Evaluation System

A full-stack, AI-powered examination and handwritten script evaluation platform tailored for **Basic Electronics and Amplifiers (BEA - Subject Code: MEE-105)** for Diploma students.

Designed for **one lecturer and approximately 60–70 Diploma students**, providing transparent multimodal AI grading of handwritten answers, circuit schematics, derivations, and calculations.

---

## 🏛️ System Architecture

| Component | Technology | Role & Specification |
|---|---|---|
| **Frontend** | HTML5, Modern CSS3, JavaScript (ES6+) | Mobile camera capture, interactive canvas crop/rotate, Chart.js analytics |
| **Backend** | Python Flask (3.1+) | Server-side sessions, Bcrypt auth, rate limiting, REST APIs |
| **Database** | Supabase PostgreSQL | 14 normalized tables, Row Level Security (RLS) policies, foreign keys |
| **File Storage** | Supabase Private Storage | `bea-answer-scripts` private bucket for original & compressed student scripts |
| **Primary AI Engine** | Google Gemini Multimodal API | Multimodal evaluation of handwritten text, circuit topologies, and calculations |
| **Optional AI Integration** | Model Context Protocol (MCP) | Non-blocking external tool / server integration bridge |
| **Analytics Engine** | Chart.js & Python Analytics | Class averages, medians, rankings, topic mastery, and remedial recommendations |
| **Production Hosting** | Render (Procfile / render.yaml) | Gunicorn WSGI server (`web: gunicorn app:app`) |
| **CI / Automation** | GitHub Actions | Automated linting, test suite execution on every push/PR |

---

## 🚀 Quick Start Guide (Local Development)

### 1. Prerequisites
- Python 3.10+ (Tested on Python 3.14)
- Git

### 2. Setup Virtual Environment
```bash
# Clone the repository
git clone <your-repository-url>
cd "Online Evaluator"

# Create and activate virtual environment
python -m venv .venv

# On Windows:
.venv\Scripts\activate
# On Linux/macOS:
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 3. Configure Environment Variables
Copy `.env.example` to `.env`:
```bash
cp .env.example .env
```
Fill in your credentials:
```env
FLASK_SECRET_KEY=your-secure-random-key
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_KEY=your-anon-publishable-key
SUPABASE_SERVICE_ROLE_KEY=your-service-role-secret-key
GEMINI_API_KEY=AIzaSy...your-gemini-key
GEMINI_MODEL=gemini-2.5-flash
APP_TIMEZONE=Asia/Kolkata
```

### 4. Database Setup (Supabase)
Run the SQL migration in your Supabase SQL Editor or use the Admin Settings Wizard:
```sql
-- File location: database/migrations/001_initial_bea_schema.sql
```
This provisions:
- 14 normalized tables
- Row Level Security (RLS) policies
- `update_updated_at_column` triggers
- `bea-answer-scripts` private storage bucket

### 5. Run the Application
```bash
python app.py
```
Open your browser at **`http://127.0.0.1:5000`**.

---

## 🔐 Authentication & Security

### 🎓 Student PIN Login
- **Authentication:** Registered Student Full Name + Unique Diploma PIN (e.g., `26054-CPS-001`, `26054-ES-041`).
- **Zero Passwords/OTP:** Students never have to remember passwords.
- **Privacy:** Public frontend never exposes the list of registered PINs.
- **Brute-Force Protection:** 5 consecutive failed attempts trigger an automated **15-minute security lockout**.
- **Data Isolation:** Students can only view their own submissions, receipts, evaluations, and objection records.

### 👨‍🏫 Lecturer Administration
- Email & Bcrypt password hash verification.
- Initial setup account: `lecturer@bea.edu` / `admin123`.

---

## 📸 Student Answer Upload & Canvas Processing
- Supports **mobile camera capture**, desktop uploads, and PDFs.
- **Interactive Canvas Tools:**
  - 90° clockwise and counter-clockwise rotation.
  - Interactive crop and auto-straighten edge-detection helper.
  - Aspect-ratio preserving compression ensuring circuit diagram and subscript legibility.
  - Original vs. Optimized KB display (typically reduces 4 MB photo to ~300 KB).
- **Single Submission Enforcement:**
  - Database unique constraint (`uq_one_submission_per_student_assessment`).
  - Upon submission, editing is locked, and a cryptographic receipt (`BEA-YYYYMMDD-XXXXXX`) with **Asia/Kolkata date & time** is issued.
  - Submissions can only be reopened by a teacher with a recorded audit reason.

---

## 🤖 Gemini AI Evaluation Workflow
1. Evaluation is triggered **only when the teacher clicks EVALUATE**.
2. Evaluates handwritten scripts question-by-question against specific rubric criteria.
3. **Structured Outputs:**
   - Marks awarded & max marks
   - Criterion-wise score breakdown
   - Correct concepts recognized
   - Missing concepts / technical mistakes
   - Deduction reasons
   - Model answer reference & constructive feedback
   - Confidence level (`high`, `medium`, `low`)
   - **Lecturer Review Warning:** Flags smudged equations or illegible handwriting without guessing.
4. **Teacher Moderation:** AI marks remain suggestions until the lecturer clicks **Approve / Modify**, recording all changes in `marks_audit_log`.

---

## 📊 Result Analyser & Topic Analytics
- **Rankings:** Merit ledger from highest to lowest score with pass/fail badges.
- **Class Metrics:** Average, median, highest, lowest, pass percentage.
- **Visuals:** Chart.js marks distribution histogram and syllabus topic mastery radar/bar chart.
- **Remedial Teaching Advice:** AI pedagogical recommendations for classroom revision clearly distinguished from computed results.

---

## 🚢 Deployment to Render
This project includes pre-configured deployment artifacts:
- **`Procfile`**: `web: gunicorn app:app --workers 2 --threads 4 --timeout 120`
- **`render.yaml`**: Infrastructure blueprint for Singapore/Asia region.
- **Health Check Route**: `/health` for zero-downtime health probes.

---

## 🧪 Testing
Run the automated test suite:
```bash
python -m pytest tests/ -v -o pythonpath=.
```
All tests verify health probes, student PIN auth, rate limiting, Bcrypt passwords, image optimization, receipt generation, and assessment seeding.
