-- ═══════════════════════════════════════════════════════════════
-- REYDM – Database Setup Script (Updated: no Chat; petty cash, leave manager in DB)
-- app.py init_db() handles all this automatically.
-- This SQL file is provided for reference or manual setup.
-- ═══════════════════════════════════════════════════════════════

CREATE DATABASE IF NOT EXISTS `reydm_db`;
USE `reydm_db`;
SET time_zone = '+05:30';

-- ─── USERS ───────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS users (
    id INT AUTO_INCREMENT PRIMARY KEY,
    full_name VARCHAR(100) NOT NULL,
    email VARCHAR(150) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    role ENUM('admin', 'user') DEFAULT 'user',
    is_approved TINYINT(1) DEFAULT 0,
    is_active TINYINT(1) DEFAULT 1,
    mail_enabled TINYINT(1) DEFAULT 1,
    allowed_tools JSON DEFAULT NULL,
    last_active DATETIME DEFAULT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

-- ─── OTP TOKENS ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS otp_tokens (
    id INT AUTO_INCREMENT PRIMARY KEY,
    email VARCHAR(150) NOT NULL,
    otp_code VARCHAR(6) NOT NULL,
    purpose ENUM('register', 'reset_password') DEFAULT 'register',
    is_used TINYINT(1) DEFAULT 0,
    expires_at DATETIME NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- ─── REMINDERS ───────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS reminders (
    id INT AUTO_INCREMENT PRIMARY KEY,
    project_name VARCHAR(255) NOT NULL,
    reminder_datetime DATETIME NOT NULL,
    created_by INT NOT NULL,
    is_sent TINYINT(1) DEFAULT 0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE CASCADE,
    UNIQUE KEY unique_project_time (project_name, reminder_datetime)
);

CREATE TABLE IF NOT EXISTS reminder_logs (
    id INT AUTO_INCREMENT PRIMARY KEY,
    reminder_id INT NOT NULL,
    sent_to VARCHAR(150) NOT NULL,
    status ENUM('sent', 'failed') DEFAULT 'sent',
    sent_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (reminder_id) REFERENCES reminders(id) ON DELETE CASCADE
);

-- ─── NIGHT SHIFT ─────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS ns_employees (
    id INT AUTO_INCREMENT PRIMARY KEY,
    emp_id VARCHAR(20) UNIQUE NOT NULL,
    name VARCHAR(100) NOT NULL,
    dept VARCHAR(60) DEFAULT '',
    status ENUM('active', 'resigned') DEFAULT 'active',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS ns_attendance (
    id INT AUTO_INCREMENT PRIMARY KEY,
    emp_id VARCHAR(20) NOT NULL,
    att_date DATE NOT NULL,
    present TINYINT(1) DEFAULT 1,
    UNIQUE KEY unique_emp_date (emp_id, att_date)
);

-- ─── ATTENDANCE LOGS ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS attendance_logs (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    login_date DATE NOT NULL,
    login_time DATETIME NOT NULL,
    logout_time DATETIME DEFAULT NULL,
    hours_spent DECIMAL(5,2) DEFAULT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    UNIQUE KEY unique_user_date (user_id, login_date)
);

CREATE TABLE IF NOT EXISTS attendance_requests (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    request_date DATE NOT NULL,
    requested_login DATETIME NOT NULL,
    requested_logout DATETIME NOT NULL,
    reason VARCHAR(500) DEFAULT '',
    status ENUM('pending', 'approved', 'declined') DEFAULT 'pending',
    admin_note VARCHAR(255) DEFAULT '',
    reviewed_by INT DEFAULT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

-- ─── PETTY CASH (CBE + DGL) ──────────────────────────────────────
CREATE TABLE IF NOT EXISTS petty_cash (
    id INT AUTO_INCREMENT PRIMARY KEY,
    office ENUM('cbe', 'dgl') NOT NULL,
    entry_date DATE NOT NULL,
    particular VARCHAR(500) NOT NULL,
    amount DECIMAL(12,2) NOT NULL,
    entry_type ENUM('credit', 'debit') NOT NULL,
    category VARCHAR(80) DEFAULT '',
    created_by INT DEFAULT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_office_date (office, entry_date)
);

-- ─── LEAVE MANAGER ───────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS lm_employees (
    id INT AUTO_INCREMENT PRIMARY KEY,
    sno INT DEFAULT 0,
    emp_id VARCHAR(30) UNIQUE NOT NULL,
    name VARCHAR(150) NOT NULL,
    dept VARCHAR(60) DEFAULT '',
    status ENUM('Active', 'Inactive') DEFAULT 'Active',
    join_date DATE DEFAULT NULL,
    extra_cl DECIMAL(5,2) DEFAULT 0,
    extra_sl DECIMAL(5,2) DEFAULT 0,
    extra_note VARCHAR(255) DEFAULT '',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS lm_leaves (
    id INT AUTO_INCREMENT PRIMARY KEY,
    emp_id VARCHAR(30) NOT NULL,
    yr INT NOT NULL,
    mon VARCHAR(5) NOT NULL,
    dy INT NOT NULL,
    lv_type VARCHAR(4) NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY unique_emp_date (emp_id, yr, mon, dy),
    INDEX idx_year (yr)
);

-- ─── ADMIN SETTINGS ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS admin_settings (
    id INT AUTO_INCREMENT PRIMARY KEY,
    setting_key VARCHAR(100) UNIQUE NOT NULL,
    setting_value TEXT DEFAULT NULL,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);