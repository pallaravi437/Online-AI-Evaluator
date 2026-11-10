-- =============================================================================
-- Migration: 002_add_assessment_deadlines_and_topics.sql
-- Adds unit_topic and deadline to assessments table (if not exists)
-- Supports MCQ Test & Slip Test dual workflow
-- =============================================================================

ALTER TABLE public.assessments 
ADD COLUMN IF NOT EXISTS unit_topic VARCHAR(255) DEFAULT 'General',
ADD COLUMN IF NOT EXISTS deadline TIMESTAMPTZ;

-- Ensure questions table has topic & learning_outcome
ALTER TABLE public.questions
ADD COLUMN IF NOT EXISTS unit_topic VARCHAR(255),
ADD COLUMN IF NOT EXISTS learning_outcome VARCHAR(255);

