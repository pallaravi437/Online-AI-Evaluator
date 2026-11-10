-- =============================================================================
-- BEA AI ASSESSMENT & ANSWER SCRIPT EVALUATION SYSTEM
-- Database Migration: 001_initial_bea_schema.sql
-- Target: Supabase PostgreSQL + Supabase Private Storage + Row Level Security
-- Subject: Basic Electronics and Amplifiers (BEA - C26_MEE-105)
-- Timezone Default: Asia/Kolkata
-- =============================================================================

-- Enable required extensions
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- Set session timezone to Asia/Kolkata for audit consistency
SET timezone = 'Asia/Kolkata';

-- =============================================================================
-- 1. UTILITY TRIGGER FOR updated_at TIMESTAMPS
-- =============================================================================
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- =============================================================================
-- 2. NORMALIZED TABLES (14 CORE TABLES)
-- =============================================================================

-- 2.1 teacher_profiles
CREATE TABLE IF NOT EXISTS public.teacher_profiles (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    auth_user_id UUID UNIQUE, -- Optional link to Supabase Auth user
    full_name VARCHAR(150) NOT NULL,
    email VARCHAR(255) UNIQUE NOT NULL,
    password_hash TEXT NOT NULL, -- Bcrypt/Argon2 hash for Flask server-side auth
    role VARCHAR(50) NOT NULL DEFAULT 'lecturer' CHECK (role IN ('lecturer', 'admin')),
    department VARCHAR(100) DEFAULT 'Electronics & Communication Engineering',
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    last_login_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 2.2 students
CREATE TABLE IF NOT EXISTS public.students (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    pin VARCHAR(50) NOT NULL,
    Pin_normalized VARCHAR(50) GENERATED ALWAYS AS (UPPER(TRIM(pin))) STORED,
    full_name VARCHAR(150) NOT NULL,
    name_normalized VARCHAR(150) GENERATED ALWAYS AS (UPPER(TRIM(full_name))) STORED,
    academic_year VARCHAR(20) NOT NULL, -- e.g., '2026-2027'
    semester VARCHAR(20) NOT NULL,      -- e.g., 'Semester 1' / 'III Sem'
    branch VARCHAR(50) NOT NULL DEFAULT 'ECE',
    section VARCHAR(20) NOT NULL DEFAULT 'A',
    contact_phone VARCHAR(30),
    contact_email VARCHAR(255),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    failed_login_attempts INT NOT NULL DEFAULT 0,
    locked_until TIMESTAMPTZ,
    last_login_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_students_pin UNIQUE (pin),
    CONSTRAINT uq_students_pin_normalized UNIQUE (Pin_normalized)
);

CREATE INDEX IF NOT EXISTS idx_students_pin_norm ON public.students(Pin_normalized);
CREATE INDEX IF NOT EXISTS idx_students_active_sem ON public.students(is_active, academic_year, semester, branch, section);

-- 2.3 topics (Syllabus & Concept Mapping for BEA)
CREATE TABLE IF NOT EXISTS public.topics (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    syllabus_code VARCHAR(30) UNIQUE NOT NULL, -- e.g., 'CO3-3.3', 'CO3-3.4', 'CO3-3.5'
    title VARCHAR(200) NOT NULL,               -- e.g., 'BJT Modes and Configurations (CE, CB, CC)'
    unit_number INT,
    description TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 2.4 assessments
CREATE TABLE IF NOT EXISTS public.assessments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title VARCHAR(200) NOT NULL,
    assessment_type VARCHAR(50) NOT NULL CHECK (assessment_type IN ('slip_test', 'assignment', 'unit_test', 'internal_assessment', 'mcq_test', 'quiz')),
    subject_code VARCHAR(50) NOT NULL DEFAULT 'BEA (MEE-105)',
    assessment_date DATE NOT NULL DEFAULT CURRENT_DATE,
    max_marks NUMERIC(6,2) NOT NULL CHECK (max_marks > 0),
    pass_percentage NUMERIC(5,2) NOT NULL DEFAULT 35.00 CHECK (pass_percentage >= 0 AND pass_percentage <= 100),
    instructions TEXT,
    status VARCHAR(30) NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'published', 'closed', 'archived')),
    allow_submissions BOOLEAN NOT NULL DEFAULT FALSE,
    results_published BOOLEAN NOT NULL DEFAULT FALSE,
    created_by UUID REFERENCES public.teacher_profiles(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_assessments_status ON public.assessments(status, assessment_date DESC);

-- 2.5 questions
CREATE TABLE IF NOT EXISTS public.questions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    assessment_id UUID NOT NULL REFERENCES public.assessments(id) ON DELETE CASCADE,
    question_number VARCHAR(20) NOT NULL, -- e.g., '1', '2a', '2b'
    display_order INT NOT NULL DEFAULT 1,
    question_type VARCHAR(20) NOT NULL DEFAULT 'descriptive' CHECK (question_type IN ('descriptive', 'mcq')),
    question_text TEXT NOT NULL,
    option_a TEXT,
    option_b TEXT,
    option_c TEXT,
    option_d TEXT,
    correct_option VARCHAR(10), -- 'A', 'B', 'C', 'D'
    max_marks NUMERIC(6,2) NOT NULL CHECK (max_marks > 0),
    model_answer TEXT NOT NULL,
    acceptable_alternatives TEXT,
    difficulty VARCHAR(20) NOT NULL DEFAULT 'medium' CHECK (difficulty IN ('easy', 'medium', 'hard')),
    requires_diagram BOOLEAN NOT NULL DEFAULT FALSE,
    requires_numerical BOOLEAN NOT NULL DEFAULT FALSE,
    question_image_url TEXT,
    question_image_path TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_assessment_question_num UNIQUE (assessment_id, question_number)
);

CREATE INDEX IF NOT EXISTS idx_questions_assessment ON public.questions(assessment_id, display_order);

-- 2.6 question_topics (Many-to-Many Join between questions and topics)
CREATE TABLE IF NOT EXISTS public.question_topics (
    question_id UUID NOT NULL REFERENCES public.questions(id) ON DELETE CASCADE,
    topic_id UUID NOT NULL REFERENCES public.topics(id) ON DELETE CASCADE,
    weight NUMERIC(4,2) NOT NULL DEFAULT 1.00 CHECK (weight > 0),
    PRIMARY KEY (question_id, topic_id)
);

CREATE INDEX IF NOT EXISTS idx_question_topics_topic ON public.question_topics(topic_id);

-- 2.7 marking_criteria (Rubric breakdown per question)
CREATE TABLE IF NOT EXISTS public.marking_criteria (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    question_id UUID NOT NULL REFERENCES public.questions(id) ON DELETE CASCADE,
    criterion_order INT NOT NULL DEFAULT 1,
    criterion_title VARCHAR(200) NOT NULL, -- e.g., 'Circuit Diagram & Terminal Labels', 'Formula & Substitution'
    max_marks NUMERIC(5,2) NOT NULL CHECK (max_marks > 0),
    description TEXT,
    keywords TEXT[],
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_marking_criteria_question ON public.marking_criteria(question_id, criterion_order);

-- 2.8 answer_submissions (Enforces 1 submission record per student per assessment)
CREATE TABLE IF NOT EXISTS public.answer_submissions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    submission_receipt_code VARCHAR(40) UNIQUE NOT NULL, -- e.g., 'BEA-20261031-A8F92B'
    assessment_id UUID NOT NULL REFERENCES public.assessments(id) ON DELETE CASCADE,
    student_id UUID NOT NULL REFERENCES public.students(id) ON DELETE RESTRICT,
    status VARCHAR(30) NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'submitted', 'reopened', 'evaluated', 'approved')),
    is_locked BOOLEAN NOT NULL DEFAULT FALSE,
    total_pages_uploaded INT NOT NULL DEFAULT 0,
    submitted_at TIMESTAMPTZ, -- Server-generated UTC timestamp (displayed in Asia/Kolkata)
    reopened_by UUID REFERENCES public.teacher_profiles(id) ON DELETE SET NULL,
    reopened_at TIMESTAMPTZ,
    reopen_reason TEXT,
    ai_total_marks NUMERIC(6,2),
    official_total_marks NUMERIC(6,2), -- Populated ONLY upon lecturer approval
    percentage NUMERIC(5,2),
    pass_status VARCHAR(20) CHECK (pass_status IN ('pass', 'fail', 'pending')),
    approved_by UUID REFERENCES public.teacher_profiles(id) ON DELETE SET NULL,
    approved_at TIMESTAMPTZ,
    lecturer_overall_comment TEXT,
    client_ip VARCHAR(64),
    user_agent TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_one_submission_per_student_assessment UNIQUE (assessment_id, student_id)
);

CREATE INDEX IF NOT EXISTS idx_submissions_assessment_status ON public.answer_submissions(assessment_id, status);
CREATE INDEX IF NOT EXISTS idx_submissions_student ON public.answer_submissions(student_id);

-- 2.9 answer_pages (Question-wise uploaded script pages in Private Supabase Storage)
CREATE TABLE IF NOT EXISTS public.answer_pages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    submission_id UUID NOT NULL REFERENCES public.answer_submissions(id) ON DELETE CASCADE,
    question_id UUID NOT NULL REFERENCES public.questions(id) ON DELETE CASCADE,
    page_number INT NOT NULL CHECK (page_number >= 1),
    storage_bucket VARCHAR(100) NOT NULL DEFAULT 'bea-answer-scripts',
    storage_path TEXT NOT NULL, -- Path inside private Supabase Storage bucket
    original_filename VARCHAR(255),
    mime_type VARCHAR(100) NOT NULL CHECK (mime_type IN ('image/jpeg', 'image/png', 'image/webp', 'application/pdf')),
    source_type VARCHAR(30) NOT NULL DEFAULT 'camera' CHECK (source_type IN ('camera', 'scanner', 'pdf', 'gallery')),
    original_size_bytes BIGINT NOT NULL CHECK (original_size_bytes >= 0),
    optimized_size_bytes BIGINT NOT NULL CHECK (optimized_size_bytes >= 0),
    image_width INT,
    image_height INT,
    processing_metadata JSONB DEFAULT '{}'::JSONB, -- stores rotation, crop box, edge-detection flags
    sha256_checksum VARCHAR(64),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_submission_question_page UNIQUE (submission_id, question_id, page_number)
);

CREATE INDEX IF NOT EXISTS idx_answer_pages_submission_question ON public.answer_pages(submission_id, question_id, page_number);

-- 2.10 evaluation_runs (Tracks individual or batch AI evaluation jobs & token usage)
CREATE TABLE IF NOT EXISTS public.evaluation_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    assessment_id UUID NOT NULL REFERENCES public.assessments(id) ON DELETE CASCADE,
    submission_id UUID REFERENCES public.answer_submissions(id) ON DELETE CASCADE, -- NULL if batch run across multiple submissions
    triggered_by UUID REFERENCES public.teacher_profiles(id) ON DELETE SET NULL,
    run_mode VARCHAR(30) NOT NULL DEFAULT 'single_submission' CHECK (run_mode IN ('single_submission', 'batch_assessment', 'selected_questions')),
    ai_provider VARCHAR(50) NOT NULL DEFAULT 'gemini',
    model_name VARCHAR(100) NOT NULL DEFAULT 'gemini-2.5-flash',
    mcp_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    status VARCHAR(30) NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'in_progress', 'paused', 'completed', 'failed', 'partial')),
    total_questions_targeted INT NOT NULL DEFAULT 0,
    questions_evaluated_count INT NOT NULL DEFAULT 0,
    questions_failed_count INT NOT NULL DEFAULT 0,
    prompt_tokens_used INT NOT NULL DEFAULT 0,
    completion_tokens_used INT NOT NULL DEFAULT 0,
    total_tokens_used INT NOT NULL DEFAULT 0,
    error_log JSONB DEFAULT '[]'::JSONB,
    checkpoint_state JSONB DEFAULT '{}'::JSONB, -- Enables safe resumption of interrupted jobs
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_eval_runs_assessment ON public.evaluation_runs(assessment_id, status);

-- 2.11 question_evaluations (Immutable original AI evaluation + Lecturer approved state)
CREATE TABLE IF NOT EXISTS public.question_evaluations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    submission_id UUID NOT NULL REFERENCES public.answer_submissions(id) ON DELETE CASCADE,
    question_id UUID NOT NULL REFERENCES public.questions(id) ON DELETE CASCADE,
    evaluation_run_id UUID REFERENCES public.evaluation_runs(id) ON DELETE SET NULL,
    
    -- Immutable Original AI Evaluation Output (Never overwritten on teacher edit)
    ai_marks_awarded NUMERIC(5,2) NOT NULL CHECK (ai_marks_awarded >= 0),
    max_marks NUMERIC(5,2) NOT NULL CHECK (max_marks > 0),
    ai_criterion_breakdown JSONB NOT NULL DEFAULT '[]'::JSONB,
    ai_correct_concepts JSONB NOT NULL DEFAULT '[]'::JSONB,
    ai_missing_concepts JSONB NOT NULL DEFAULT '[]'::JSONB,
    ai_technical_mistakes JSONB NOT NULL DEFAULT '[]'::JSONB,
    ai_marks_deducted_reasons JSONB NOT NULL DEFAULT '[]'::JSONB,
    ai_correct_explanation TEXT,
    ai_model_answer_reference TEXT,
    ai_student_feedback TEXT,
    ai_confidence_level VARCHAR(20) NOT NULL DEFAULT 'high' CHECK (ai_confidence_level IN ('high', 'medium', 'low')),
    ai_lecturer_review_warning BOOLEAN NOT NULL DEFAULT FALSE,
    ai_warning_reason TEXT, -- e.g., 'Unclear handwriting in equation step 2'
    ai_raw_response JSONB,
    ai_evaluated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Lecturer Official Review & Approval State
    approval_status VARCHAR(30) NOT NULL DEFAULT 'pending_review' CHECK (approval_status IN ('pending_review', 'approved_as_is', 'modified_and_approved', 're_evaluation_requested')),
    approved_marks NUMERIC(5,2) CHECK (approved_marks >= 0),
    lecturer_comments TEXT,
    reviewed_by UUID REFERENCES public.teacher_profiles(id) ON DELETE SET NULL,
    reviewed_at TIMESTAMPTZ,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_submission_question_eval UNIQUE (submission_id, question_id)
);

CREATE INDEX IF NOT EXISTS idx_question_evals_submission ON public.question_evaluations(submission_id, approval_status);

-- 2.12 recheck_requests (Student objections & Lecturer resolution)
CREATE TABLE IF NOT EXISTS public.recheck_requests (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    submission_id UUID NOT NULL REFERENCES public.answer_submissions(id) ON DELETE CASCADE,
    question_id UUID NOT NULL REFERENCES public.questions(id) ON DELETE CASCADE,
    question_evaluation_id UUID NOT NULL REFERENCES public.question_evaluations(id) ON DELETE CASCADE,
    student_id UUID NOT NULL REFERENCES public.students(id) ON DELETE CASCADE,
    student_objection_reason TEXT NOT NULL,
    marks_before_recheck NUMERIC(5,2) NOT NULL,
    marks_after_recheck NUMERIC(5,2),
    status VARCHAR(30) NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'accepted', 'rejected')),
    lecturer_resolution_comment TEXT,
    resolved_by UUID REFERENCES public.teacher_profiles(id) ON DELETE SET NULL,
    resolved_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_student_question_recheck UNIQUE (submission_id, question_id)
);

CREATE INDEX IF NOT EXISTS idx_recheck_status ON public.recheck_requests(status, created_at DESC);

-- 2.13 marks_audit_log (Immutable audit trail of all mark events)
CREATE TABLE IF NOT EXISTS public.marks_audit_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    submission_id UUID NOT NULL REFERENCES public.answer_submissions(id) ON DELETE CASCADE,
    question_id UUID REFERENCES public.questions(id) ON DELETE SET NULL,
    question_evaluation_id UUID REFERENCES public.question_evaluations(id) ON DELETE SET NULL,
    action_type VARCHAR(50) NOT NULL CHECK (action_type IN (
        'ai_initial_evaluation',
        'ai_re_evaluation',
        'lecturer_approved',
        'lecturer_modified',
        'recheck_accepted',
        'recheck_rejected',
        'submission_reopened'
    )),
    original_ai_marks NUMERIC(5,2),
    previous_official_marks NUMERIC(5,2),
    new_official_marks NUMERIC(5,2),
    reason TEXT NOT NULL,
    actor_lecturer_id UUID REFERENCES public.teacher_profiles(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_marks_audit_submission ON public.marks_audit_log(submission_id, created_at DESC);

-- 2.14 application_settings (Non-secret configuration only; raw API keys stored in env/vault)
CREATE TABLE IF NOT EXISTS public.application_settings (
    setting_key VARCHAR(100) PRIMARY KEY,
    setting_value JSONB NOT NULL DEFAULT '{}'::JSONB,
    description TEXT,
    updated_by UUID REFERENCES public.teacher_profiles(id) ON DELETE SET NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Seed safe default non-secret application settings
INSERT INTO public.application_settings (setting_key, setting_value, description)
VALUES
    ('gemini_config', '{"model": "gemini-2.5-flash", "retry_limit": 3, "batch_size": 5, "temperature": 0.1, "evaluation_instructions": "Evaluate Diploma Basic Electronics and Amplifiers (BEA) handwritten scripts strictly against marking criteria. Accept valid alternative circuit explanations. Never guess illegible handwriting; flag unclear regions for lecturer review."}'::JSONB, 'Non-secret Gemini model and batch evaluation settings'),
    ('mcp_config', '{"enabled": false, "server_name": "", "transport": "sse", "endpoint_url": "", "last_tested_at": null, "status": "disabled"}'::JSONB, 'Optional MCP server non-secret connection metadata'),
    ('storage_config', '{"bucket_name": "bea-answer-scripts", "max_page_size_mb": 5, "target_compressed_kb": 350, "allowed_mime_types": ["image/jpeg", "image/png", "image/webp", "application/pdf"]}'::JSONB, 'Private storage bucket parameters')
ON CONFLICT (setting_key) DO NOTHING;

-- =============================================================================
-- 3. UPDATED_AT TRIGGERS
-- =============================================================================
DO $$
DECLARE
    tbl TEXT;
BEGIN
    FOREACH tbl IN ARRAY ARRAY[
        'teacher_profiles', 'students', 'topics', 'assessments',
        'questions', 'marking_criteria', 'answer_submissions',
        'answer_pages', 'evaluation_runs', 'question_evaluations',
        'recheck_requests', 'application_settings'
    ]
    LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS trg_%I_updated_at ON public.%I;', tbl, tbl);
        EXECUTE format('CREATE TRIGGER trg_%I_updated_at BEFORE UPDATE ON public.%I FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();', tbl, tbl);
    END LOOP;
END $$;

-- =============================================================================
-- 4. ROW LEVEL SECURITY (RLS) POLICIES
-- =============================================================================
-- Enable RLS across all 14 tables (Never disable RLS to bypass permissions)
ALTER TABLE public.teacher_profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.students ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.topics ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.assessments ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.questions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.question_topics ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.marking_criteria ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.answer_submissions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.answer_pages ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.evaluation_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.question_evaluations ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.recheck_requests ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.marks_audit_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.application_settings ENABLE ROW LEVEL SECURITY;

-- Helper function to read custom JWT/request claims set by Flask backend per-request
-- Flask backend sets `request.jwt.claim.role` and `request.jwt.claim.student_id` / `teacher_id`
CREATE OR REPLACE FUNCTION public.current_app_role() RETURNS TEXT AS $$
    SELECT COALESCE(
        current_setting('request.jwt.claim.app_role', true),
        current_setting('app.current_role', true),
        'anonymous'
    );
$$ LANGUAGE sql STABLE;

CREATE OR REPLACE FUNCTION public.current_student_id() RETURNS UUID AS $$
    SELECT NULLIF(
        COALESCE(
            current_setting('request.jwt.claim.student_id', true),
            current_setting('app.current_student_id', true),
            ''
        ), ''
    )::UUID;
$$ LANGUAGE sql STABLE;

-- Lecturer/Admin full access policies
CREATE POLICY lecturer_all_teacher_profiles ON public.teacher_profiles
    FOR ALL USING (public.current_app_role() IN ('lecturer', 'admin'));

CREATE POLICY lecturer_all_students ON public.students
    FOR ALL USING (public.current_app_role() IN ('lecturer', 'admin'));

-- Student can only read their own row (never the full PIN list)
CREATE POLICY student_read_own_profile ON public.students
    FOR SELECT USING (public.current_app_role() = 'student' AND id = public.current_student_id());

CREATE POLICY lecturer_all_topics ON public.topics
    FOR ALL USING (public.current_app_role() IN ('lecturer', 'admin'));

CREATE POLICY student_read_topics ON public.topics
    FOR SELECT USING (public.current_app_role() = 'student');

CREATE POLICY lecturer_all_assessments ON public.assessments
    FOR ALL USING (public.current_app_role() IN ('lecturer', 'admin'));

CREATE POLICY student_read_published_assessments ON public.assessments
    FOR SELECT USING (public.current_app_role() = 'student' AND status IN ('published', 'closed'));

CREATE POLICY lecturer_all_questions ON public.questions
    FOR ALL USING (public.current_app_role() IN ('lecturer', 'admin'));

CREATE POLICY student_read_questions ON public.questions
    FOR SELECT USING (
        public.current_app_role() = 'student' AND
        EXISTS (
            SELECT 1 FROM public.assessments a
            WHERE a.id = questions.assessment_id AND a.status IN ('published', 'closed')
        )
    );

CREATE POLICY lecturer_all_question_topics ON public.question_topics
    FOR ALL USING (public.current_app_role() IN ('lecturer', 'admin'));

CREATE POLICY student_read_question_topics ON public.question_topics
    FOR SELECT USING (public.current_app_role() = 'student');

CREATE POLICY lecturer_all_marking_criteria ON public.marking_criteria
    FOR ALL USING (public.current_app_role() IN ('lecturer', 'admin'));

-- Students can view marking criteria only after results are published
CREATE POLICY student_read_marking_criteria ON public.marking_criteria
    FOR SELECT USING (
        public.current_app_role() = 'student' AND
        EXISTS (
            SELECT 1 FROM public.questions q
            JOIN public.assessments a ON a.id = q.assessment_id
            WHERE q.id = marking_criteria.question_id AND a.results_published = TRUE
        )
    );

-- Submissions: Lecturer full access; Student only their own submissions
CREATE POLICY lecturer_all_submissions ON public.answer_submissions
    FOR ALL USING (public.current_app_role() IN ('lecturer', 'admin'));

CREATE POLICY student_select_own_submissions ON public.answer_submissions
    FOR SELECT USING (public.current_app_role() = 'student' AND student_id = public.current_student_id());

CREATE POLICY student_insert_own_submissions ON public.answer_submissions
    FOR INSERT WITH CHECK (
        public.current_app_role() = 'student' AND
        student_id = public.current_student_id() AND
        is_locked = FALSE
    );

CREATE POLICY student_update_own_unlocked_submissions ON public.answer_submissions
    FOR UPDATE USING (
        public.current_app_role() = 'student' AND
        student_id = public.current_student_id() AND
        is_locked = FALSE
    );

-- Answer Pages: Lecturer full access; Student only their own pages while unlocked
CREATE POLICY lecturer_all_answer_pages ON public.answer_pages
    FOR ALL USING (public.current_app_role() IN ('lecturer', 'admin'));

CREATE POLICY student_select_own_pages ON public.answer_pages
    FOR SELECT USING (
        public.current_app_role() = 'student' AND
        EXISTS (
            SELECT 1 FROM public.answer_submissions s
            WHERE s.id = answer_pages.submission_id AND s.student_id = public.current_student_id()
        )
    );

CREATE POLICY student_modify_own_unlocked_pages ON public.answer_pages
    FOR ALL USING (
        public.current_app_role() = 'student' AND
        EXISTS (
            SELECT 1 FROM public.answer_submissions s
            WHERE s.id = answer_pages.submission_id
              AND s.student_id = public.current_student_id()
              AND s.is_locked = FALSE
        )
    );

-- Evaluation Runs: Lecturer only
CREATE POLICY lecturer_all_evaluation_runs ON public.evaluation_runs
    FOR ALL USING (public.current_app_role() IN ('lecturer', 'admin'));

-- Question Evaluations: Lecturer full access; Student read-only on own approved evaluations
CREATE POLICY lecturer_all_question_evaluations ON public.question_evaluations
    FOR ALL USING (public.current_app_role() IN ('lecturer', 'admin'));

CREATE POLICY student_read_own_approved_evaluations ON public.question_evaluations
    FOR SELECT USING (
        public.current_app_role() = 'student' AND
        approval_status IN ('approved_as_is', 'modified_and_approved') AND
        EXISTS (
            SELECT 1 FROM public.answer_submissions s
            WHERE s.id = question_evaluations.submission_id
              AND s.student_id = public.current_student_id()
        )
    );

-- Recheck Requests: Lecturer full access; Student can view/create own requests
CREATE POLICY lecturer_all_recheck_requests ON public.recheck_requests
    FOR ALL USING (public.current_app_role() IN ('lecturer', 'admin'));

CREATE POLICY student_select_own_rechecks ON public.recheck_requests
    FOR SELECT USING (public.current_app_role() = 'student' AND student_id = public.current_student_id());

CREATE POLICY student_insert_own_rechecks ON public.recheck_requests
    FOR INSERT WITH CHECK (public.current_app_role() = 'student' AND student_id = public.current_student_id());

-- Marks Audit Log & Application Settings: Lecturer/Admin only
CREATE POLICY lecturer_all_marks_audit_log ON public.marks_audit_log
    FOR ALL USING (public.current_app_role() IN ('lecturer', 'admin'));

CREATE POLICY lecturer_all_application_settings ON public.application_settings
    FOR ALL USING (public.current_app_role() IN ('lecturer', 'admin'));

-- =============================================================================
-- 5. SUPABASE PRIVATE STORAGE BUCKET SETUP
-- =============================================================================
INSERT INTO storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
VALUES (
    'bea-answer-scripts',
    'bea-answer-scripts',
    FALSE, -- Strictly PRIVATE bucket
    10485760, -- 10 MB per file limit
    ARRAY['image/jpeg', 'image/png', 'image/webp', 'application/pdf']
)
ON CONFLICT (id) DO UPDATE SET
    public = FALSE,
    file_size_limit = 10485760,
    allowed_mime_types = ARRAY['image/jpeg', 'image/png', 'image/webp', 'application/pdf'];