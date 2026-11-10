-- 005_universal_academic_storage.sql
-- Universal Academic Data Storage Schema
-- Supports Multi-Server routing across Supabase, MySQL, Firebase, and Local Storage

-- 1. Storage Servers Registry
CREATE TABLE IF NOT EXISTS public.storage_servers_registry (
    server_id VARCHAR(100) PRIMARY KEY,
    server_name VARCHAR(255) NOT NULL,
    provider VARCHAR(50) NOT NULL, -- 'supabase', 'mysql', 'firebase', 'local'
    endpoint_url TEXT,
    config_metadata JSONB DEFAULT '{}'::jsonb,
    is_active BOOLEAN DEFAULT TRUE,
    is_default BOOLEAN DEFAULT FALSE,
    health_status VARCHAR(50) DEFAULT 'untested', -- 'healthy', 'degraded', 'error', 'untested'
    last_tested_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now()),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now())
);

-- 2. Storage Routing & Assignment Matrix
CREATE TABLE IF NOT EXISTS public.server_storage_assignments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    category VARCHAR(50) NOT NULL, -- 'academic_materials', 'syllabus', 'students', 'examinations', 'ai_evaluations', 'application_data'
    department VARCHAR(100),       -- Optional filter: e.g. 'ECE', 'CSE', 'EEE', 'Mechanical'
    semester VARCHAR(20),          -- Optional filter: e.g. 'Semester 1', 'Semester 5'
    academic_year VARCHAR(50),     -- Optional filter: e.g. '2026-2027'
    subject_code VARCHAR(100),     -- Optional filter: e.g. 'BEA (MEE-105)'
    test_id VARCHAR(100),          -- Optional filter for specific test
    material_category VARCHAR(50), -- Optional filter: 'textbooks', 'lecture_notes', etc.
    server_id VARCHAR(100) NOT NULL REFERENCES public.storage_servers_registry(server_id) ON DELETE CASCADE,
    priority INT DEFAULT 10,        -- Higher number takes precedence (Test=50, Subject=40, Dept=30, Category=10)
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now()),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now())
);

CREATE INDEX IF NOT EXISTS idx_storage_assignments_lookup 
ON public.server_storage_assignments(category, department, subject_code, test_id);

-- 3. Comprehensive Syllabus & Curriculum Management
CREATE TABLE IF NOT EXISTS public.syllabus_curriculum (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title VARCHAR(255) NOT NULL,
    course_code VARCHAR(100) NOT NULL,
    subject_name VARCHAR(255) NOT NULL,
    department VARCHAR(100) NOT NULL,
    semester VARCHAR(50) NOT NULL,
    academic_year VARCHAR(50) NOT NULL,
    version VARCHAR(50) NOT NULL DEFAULT '1.0',
    is_active BOOLEAN DEFAULT TRUE,
    previous_version_id UUID REFERENCES public.syllabus_curriculum(id) ON DELETE SET NULL,
    teaching_hours INT DEFAULT 60,
    credits INT DEFAULT 4,
    course_outcomes JSONB DEFAULT '[]'::jsonb,      -- [{co_id, description, blooms_level}]
    learning_outcomes JSONB DEFAULT '[]'::jsonb,    -- [{lo_id, unit, description}]
    copo_mapping JSONB DEFAULT '{}'::jsonb,         -- {co1: {po1: 3, po2: 2}, ...}
    units_breakdown JSONB DEFAULT '[]'::jsonb,     -- [{unit_number, unit_title, topics, hours}]
    assessment_scheme JSONB DEFAULT '{}'::jsonb,    -- {internal_weightage: 40, external_weightage: 60}
    practical_experiments JSONB DEFAULT '[]'::jsonb,-- [{exp_no, exp_title, objective}]
    storage_server_id VARCHAR(100) NOT NULL DEFAULT 'supabase_main',
    file_path TEXT,
    file_name VARCHAR(255),
    raw_text TEXT,
    created_by VARCHAR(100),
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now()),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now())
);

CREATE INDEX IF NOT EXISTS idx_syllabus_lookup 
ON public.syllabus_curriculum(department, academic_year, semester, course_code, version);

-- 4. Universal Academic Materials (Textbooks, Notes, PPTs, Reference Papers, Lab Manuals)
CREATE TABLE IF NOT EXISTS public.academic_materials (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title VARCHAR(255) NOT NULL,
    material_category VARCHAR(50) NOT NULL DEFAULT 'study_material',
    -- Categories: 'textbook', 'reference_book', 'lecture_notes', 'presentation_slides',
    --             'previous_paper', 'reference_answers', 'lab_manual', 'formula_sheet', 'handout'
    subject_code VARCHAR(100) NOT NULL,
    department VARCHAR(100),
    semester VARCHAR(50),
    academic_year VARCHAR(50),
    unit_topic VARCHAR(255),
    version VARCHAR(50) NOT NULL DEFAULT '1.0',
    previous_version_id UUID REFERENCES public.academic_materials(id) ON DELETE SET NULL,
    file_type VARCHAR(50) NOT NULL, -- 'pdf', 'docx', 'doc', 'pptx', 'ppt', 'jpg', 'png', 'webp'
    file_size_kb INT DEFAULT 0,
    storage_server_id VARCHAR(100) NOT NULL DEFAULT 'supabase_main',
    storage_path TEXT NOT NULL,
    file_url TEXT,
    extracted_text TEXT,
    structured_content JSONB DEFAULT '{}'::jsonb,
    uploaded_by VARCHAR(100),
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now()),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now())
);

CREATE INDEX IF NOT EXISTS idx_academic_materials_lookup 
ON public.academic_materials(department, semester, subject_code, material_category, version);

-- 5. Master Student Records & Enrollment
CREATE TABLE IF NOT EXISTS public.students_master (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    student_pin VARCHAR(100) NOT NULL UNIQUE,
    full_name VARCHAR(255) NOT NULL,
    department VARCHAR(100) NOT NULL,
    semester VARCHAR(50) NOT NULL,
    academic_year VARCHAR(50) NOT NULL,
    enrollment_details JSONB DEFAULT '{}'::jsonb,
    status VARCHAR(50) NOT NULL DEFAULT 'active', -- 'active', 'alumni', 'suspended', 'on_leave'
    subject_registrations JSONB DEFAULT '[]'::jsonb, -- ['MEE-105', 'MATH-101', ...]
    examination_history JSONB DEFAULT '[]'::jsonb,   -- [{test_id, score, percentage, date}]
    storage_server_id VARCHAR(100) NOT NULL DEFAULT 'mysql_primary',
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now()),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now())
);

CREATE INDEX IF NOT EXISTS idx_students_master_pin ON public.students_master(student_pin);
CREATE INDEX IF NOT EXISTS idx_students_master_dept_sem ON public.students_master(department, semester);

-- 6. Universal Audit Logs
CREATE TABLE IF NOT EXISTS public.audit_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    actor_id VARCHAR(100) NOT NULL,
    actor_role VARCHAR(50) NOT NULL, -- 'admin', 'lecturer', 'student', 'system'
    action VARCHAR(100) NOT NULL,    -- 'create', 'update', 'delete', 'evaluate', 'approve', 'import'
    target_category VARCHAR(50) NOT NULL,
    target_id VARCHAR(100) NOT NULL,
    details JSONB DEFAULT '{}'::jsonb,
    storage_server_id VARCHAR(100),
    ip_address VARCHAR(100),
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now())
);

CREATE INDEX IF NOT EXISTS idx_audit_logs_actor ON public.audit_logs(actor_id, action);
CREATE INDEX IF NOT EXISTS idx_audit_logs_target ON public.audit_logs(target_category, target_id);

