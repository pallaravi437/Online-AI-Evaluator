-- 004_add_course_materials.sql
-- Table for structured storage of syllabus, textbook notes, and reference materials (PDF, Word, Images)

CREATE TABLE IF NOT EXISTS public.course_materials (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title VARCHAR(255) NOT NULL,
    material_type VARCHAR(50) NOT NULL DEFAULT 'material', -- 'syllabus', 'material', 'notes', 'formula_sheet', 'textbook'
    subject_code VARCHAR(100) NOT NULL,
    unit_topic VARCHAR(255),
    assessment_id UUID REFERENCES public.assessments(id) ON DELETE SET NULL,
    file_path TEXT NOT NULL,
    file_name VARCHAR(255) NOT NULL,
    file_type VARCHAR(50) NOT NULL, -- 'pdf', 'docx', 'doc', 'image'
    file_size_kb INT DEFAULT 0,
    extracted_text TEXT,
    structured_content JSONB DEFAULT '{}'::jsonb,
    created_by_id UUID REFERENCES public.teacher_profiles(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now()),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now())
);

-- Indexes for lightning-fast retrieval during question generation and answer script evaluation
CREATE INDEX IF NOT EXISTS idx_course_materials_subject ON public.course_materials(subject_code);
CREATE INDEX IF NOT EXISTS idx_course_materials_unit ON public.course_materials(unit_topic);
CREATE INDEX IF NOT EXISTS idx_course_materials_type ON public.course_materials(material_type);
CREATE INDEX IF NOT EXISTS idx_course_materials_assessment ON public.course_materials(assessment_id);

