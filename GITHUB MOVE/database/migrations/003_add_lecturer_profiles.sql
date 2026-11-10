-- Migration: Add lecturer profiles table
-- Run this migration after initial setup
CREATE TABLE IF NOT EXISTS lecturers (
    id TEXT PRIMARY KEY DEFAULT (hex(randomblob(16))),
    full_name TEXT NOT NULL,
    username TEXT UNIQUE NOT NULL,
    email TEXT UNIQUE NOT NULL,
    mobile TEXT,
    subject TEXT,
    specialization TEXT,
    password_hash TEXT NOT NULL,
    is_active INTEGER DEFAULT 0,
    is_approved INTEGER DEFAULT 0,
    verification_token TEXT,
    verification_expires TIMESTAMP
);

