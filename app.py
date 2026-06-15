"""
REYDM – REY Datamind Multi-Tool Platform
Flask + MySQL + Email Notifications
Tools: Reminder, Night Shift, Attendance, Petty Cash (CBE/DGL), Leave Manager,
       Char Palette, Cost Converter, Project Analysis, PDF Unlocker
"""

import os
import io
import json
import random
import string
import threading
import time
import re as re_module
from datetime import datetime, timedelta, date
from functools import wraps

from flask import (
    Flask, render_template, request, redirect, url_for,
    session, flash, jsonify
)
from werkzeug.security import generate_password_hash, check_password_hash
import mysql.connector
import pytz

# ─── Timezone (IST) ──────────────────────────────────────────────────
IST = pytz.timezone("Asia/Kolkata")


def now_ist():
    """Return current naive datetime in IST (no tzinfo, MySQL-friendly)."""
    return datetime.now(IST).replace(tzinfo=None)


def today_ist():
    """Return today's date in IST."""
    return now_ist().date()


# ─── App Configuration ───────────────────────────────────────────────
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-this-to-a-random-secret-key")
app.permanent_session_lifetime = timedelta(hours=8)
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB max upload

# ─── Database Configuration ──────────────────────────────────────────
DB_CONFIG = {
    "host": os.environ.get("DB_HOST", "mysql-21f1e29c-reydmdeveloper-2e13.i.aivencloud.com"),
    "port": int(os.environ.get("DB_PORT", 17090)),
    "user": os.environ.get("DB_USER", "avnadmin"),
    "password": os.environ.get("DB_PASSWORD", "AVNS_l-v67tdYKfQUCJZmrp9"),
    "database": os.environ.get("DB_NAME", "reydm_db"),
}

# ─── Email Configuration ─────────────────────────────────────────────
GMAIL_USER = os.environ.get("GMAIL_USER", "reydmdeveloper@gmail.com")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "txebwrbrwtvuqttc")
# SMTP transport: 'ssl' (port 465) by default — Render free tier blocks port 587 STARTTLS,
# but port 465 SSL works reliably. Set SMTP_MODE=starttls to force port 587.
SMTP_MODE = os.environ.get("SMTP_MODE", "ssl").lower()

# ─── Available Tools (Chat removed) ──────────────────────────────────
AVAILABLE_TOOLS = {
    "reminder": {
        "name": "Reminder",
        "icon": "fa-solid fa-bell",
        "description": "Project reminder with countdown & email alerts",
    },
    "nightshift": {
        "name": "Night Shift",
        "icon": "fa-solid fa-moon",
        "description": "Night shift attendance tracker with dashboard",
    },
    "charpalette": {
        "name": "Char Palette",
        "icon": "fa-solid fa-font",
        "description": "Unicode character palette with search & copy",
    },
    "costconverter": {
        "name": "Cost Converter",
        "icon": "fa-solid fa-money-bill-transfer",
        "description": "Currency exchange rate converter",
    },
    "projectanalysis": {
        "name": "Project Analysis",
        "icon": "fa-solid fa-file-pdf",
        "description": "PDF project analyzer with export",
    },
    "pdfunlocker": {
        "name": "PDF Unlocker",
        "icon": "fa-solid fa-lock-open",
        "description": "Remove restrictions from PDF files",
    },
    "attendance": {
        "name": "Attendance",
        "icon": "fa-solid fa-clock",
        "description": "Login/Logout time tracker with reports",
    },
    "pettycash_cbe": {
        "name": "Petty Cash (CBE)",
        "icon": "fa-solid fa-money-bill-wave",
        "description": "Coimbatore office petty cash tracker",
    },
    "pettycash_dgl": {
        "name": "Petty Cash (DGL)",
        "icon": "fa-solid fa-money-bills",
        "description": "Dindigul office petty cash tracker",
    },
    "leavemanager": {
        "name": "Leave Manager",
        "icon": "fa-solid fa-calendar-check",
        "description": "Employee leave tracker with monthly dashboard",
    },
    "macromanager": {
        "name": "Macro Manager",
        "icon": "fa-solid fa-file-code",
        "description": "Install or update Normal.dotm with old system macro, UI, and shortcuts",
    },
}


def parse_allowed_tools(tools_data):
    """Safely parse allowed_tools JSON column regardless of return type (bytes, str, list, etc.)."""
    if not tools_data:
        return []
    if isinstance(tools_data, (bytes, bytearray)):
        try:
            tools_data = tools_data.decode("utf-8")
        except Exception:
            pass
    if isinstance(tools_data, str):
        try:
            return json.loads(tools_data)
        except Exception:
            return []
    if isinstance(tools_data, list):
        return tools_data
    return []


# ═══════════════════════════════════════════════════════════════════════
# DATABASE HELPERS
# ═══════════════════════════════════════════════════════════════════════

def get_db():
    """Get a database connection with IST timezone set."""
    try:
        conn = mysql.connector.connect(
            host=DB_CONFIG["host"],
            port=DB_CONFIG["port"],
            user=DB_CONFIG["user"],
            password=DB_CONFIG["password"],
            database=DB_CONFIG["database"],
            connection_timeout=10,
        )
        cur = conn.cursor()
        cur.execute("SET time_zone = '+05:30'")
        cur.close()
        return conn
    except mysql.connector.Error as e:
        print(f"[ERROR] Database connection error: {e}")
        return None


def init_db():
    """Create the database and tables if they don't exist."""
    try:
        conn = mysql.connector.connect(
            host=DB_CONFIG["host"],
            port=DB_CONFIG["port"],
            user=DB_CONFIG["user"],
            password=DB_CONFIG["password"],
        )
        cur = conn.cursor()
        cur.execute(f"CREATE DATABASE IF NOT EXISTS `{DB_CONFIG['database']}`")
        cur.execute(f"USE `{DB_CONFIG['database']}`")
        cur.execute("SET time_zone = '+05:30'")

        # ─── USERS ───────────────────────────────────────────────────
        cur.execute("""
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
            )
        """)

        # ─── OTP ─────────────────────────────────────────────────────
        cur.execute("""
            CREATE TABLE IF NOT EXISTS otp_tokens (
                id INT AUTO_INCREMENT PRIMARY KEY,
                email VARCHAR(150) NOT NULL,
                otp_code VARCHAR(6) NOT NULL,
                purpose ENUM('register', 'reset_password') DEFAULT 'register',
                is_used TINYINT(1) DEFAULT 0,
                expires_at DATETIME NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # ─── REMINDERS ───────────────────────────────────────────────
        cur.execute("""
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
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS reminder_logs (
                id INT AUTO_INCREMENT PRIMARY KEY,
                reminder_id INT NOT NULL,
                sent_to VARCHAR(150) NOT NULL,
                status ENUM('sent', 'failed') DEFAULT 'sent',
                sent_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (reminder_id) REFERENCES reminders(id) ON DELETE CASCADE
            )
        """)

        # ─── NIGHT SHIFT ─────────────────────────────────────────────
        cur.execute("""
            CREATE TABLE IF NOT EXISTS ns_employees (
                id INT AUTO_INCREMENT PRIMARY KEY,
                emp_id VARCHAR(20) UNIQUE NOT NULL,
                name VARCHAR(100) NOT NULL,
                dept VARCHAR(60) DEFAULT '',
                status ENUM('active', 'resigned') DEFAULT 'active',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS ns_attendance (
                id INT AUTO_INCREMENT PRIMARY KEY,
                emp_id VARCHAR(20) NOT NULL,
                att_date DATE NOT NULL,
                present TINYINT(1) DEFAULT 1,
                UNIQUE KEY unique_emp_date (emp_id, att_date)
            )
        """)

        # ─── ATTENDANCE LOGS ─────────────────────────────────────────
        cur.execute("""
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
            )
        """)

        # Add unique index if missing (for older DBs)
        try:
            cur.execute("ALTER TABLE attendance_logs ADD UNIQUE KEY unique_user_date (user_id, login_date)")
        except mysql.connector.Error:
            pass

        cur.execute("""
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
            )
        """)

        # ─── PETTY CASH (CBE + DGL) ──────────────────────────────────
        cur.execute("""
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
            )
        """)

        # ─── LEAVE MANAGER ───────────────────────────────────────────
        cur.execute("""
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
            )
        """)

        cur.execute("""
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
            )
        """)

        # ─── MACRO FILES ─────────────────────────────────────────────
        cur.execute("""
            CREATE TABLE IF NOT EXISTS macro_files (
                id INT AUTO_INCREMENT PRIMARY KEY,
                filename VARCHAR(150) NOT NULL,
                file_data LONGBLOB NOT NULL,
                ui_data LONGBLOB DEFAULT NULL,
                uploaded_by INT,
                uploaded_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (uploaded_by) REFERENCES users(id) ON DELETE SET NULL
            )
        """)

        # Migration: Ensure ui_data column exists in older deployments
        try:
            cur.execute("ALTER TABLE macro_files ADD COLUMN ui_data LONGBLOB DEFAULT NULL")
        except mysql.connector.Error:
            pass

        # ─── ADMIN SETTINGS ──────────────────────────────────────────
        cur.execute("""
            CREATE TABLE IF NOT EXISTS admin_settings (
                id INT AUTO_INCREMENT PRIMARY KEY,
                setting_key VARCHAR(100) UNIQUE NOT NULL,
                setting_value TEXT DEFAULT NULL,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            )
        """)

        # ─── Default admin ───────────────────────────────────────────
        cur.execute("SELECT id FROM users WHERE role = 'admin' LIMIT 1")
        if not cur.fetchone():
            admin_hash = generate_password_hash("admin123")
            all_tools = json.dumps(list(AVAILABLE_TOOLS.keys()))
            cur.execute(
                """INSERT INTO users (full_name, email, password_hash, role, is_approved, allowed_tools)
                   VALUES (%s, %s, %s, 'admin', 1, %s)""",
                ("Administrator", "admin@system.local", admin_hash, all_tools),
            )

        # ─── Default night shift employees ───────────────────────────
        cur.execute("SELECT COUNT(*) FROM ns_employees")
        if cur.fetchone()[0] == 0:
            defaults = [
                ('E001', 'Ashwath', '', 'active'),
                ('E002', 'Bharathi', '', 'active'),
                ('E003', 'Dharani', '', 'active'),
                ('E004', 'Kanchana', '', 'active'),
                ('E005', 'Karthikeyan', '', 'active'),
                ('E006', 'Nethra', '', 'active'),
                ('E007', 'Sanjay', '', 'active'),
                ('E008', 'SRK', '', 'active'),
            ]
            cur.executemany(
                "INSERT INTO ns_employees (emp_id, name, dept, status) VALUES (%s, %s, %s, %s)",
                defaults,
            )

        # ─── Default leave manager employees ─────────────────────────
        cur.execute("SELECT COUNT(*) FROM lm_employees")
        if cur.fetchone()[0] == 0:
            lm_defaults = [
                (1, 'RDM1001', 'NANDHINI M', 'QC'),
                (2, 'RDM1002', 'MANOJ KUMAR P', 'Process'),
                (3, 'RDM1003', 'RAJALAKSHMI M', 'QC'),
                (4, 'RDM1004', 'DHARANI M', 'Process'),
                (5, 'RDM1005', 'SANJAY K', 'Process'),
                (6, 'RDM1006', 'VIJAY G', 'Process'),
                (7, 'RDM1007', 'MANOJ M', 'Process'),
                (8, 'RDM1008', 'SANJAY RAJAKUMARAN S', 'Process'),
                (9, 'RDM1009', 'PANDI SUBIKSHA G', 'Process'),
                (10, 'RDM1010', 'DIVYA K', 'QC'),
                (11, 'RDM1011', 'MUTHURAMAN S', 'Process'),
                (12, 'RDM1012', 'SAKTHI CHANDHANA S', 'Process'),
                (13, 'RDM1013', 'ASHWATH S', 'QC'),
                (14, 'RDM1014', 'BHARATHI PRIYADHARSHINI S', 'QC'),
                (15, 'RDM1015', 'KISHORE KUMAR K', 'Process'),
                (16, 'RDM1016', 'NANDHA KUMAR B', 'Process'),
                (17, 'RDM1017', 'THARANI D', 'QC'),
                (18, 'RDM1018', 'DIVYA M', 'QC'),
                (19, 'RDM1019', 'KANCHANA P', 'QC'),
                (20, 'RDM1020', 'ASWINI SHANU S', 'QC'),
                (21, 'RDM1021', 'KARTHIKEYAN N', 'QC'),
                (22, 'RDM1022', 'SURYA S', 'Process'),
                (23, 'RDM1023', 'SRIRAM S', 'Process'),
                (24, 'RDM1024', 'RAJALAKSHMI S', 'QC'),
                (25, 'RDM1025', 'LILASRI RAVIKUMAR', 'Process'),
            ]
            cur.executemany(
                "INSERT INTO lm_employees (sno, emp_id, name, dept, status) VALUES (%s, %s, %s, %s, 'Active')",
                lm_defaults,
            )

        conn.commit()
        cur.close()
        conn.close()
        print("[OK] Database initialized successfully.")
    except mysql.connector.Error as e:
        print(f"[ERROR] Database initialization error: {e}")


# ═══════════════════════════════════════════════════════════════════════
# EMAIL HELPERS  (Render-fix: uses SSL/port 465 by default)
# ═══════════════════════════════════════════════════════════════════════

def send_email(to_email, subject, body_html):
    """Send an email using Gmail SMTP. Uses SSL (port 465) by default — Render
    free tier blocks outbound port 587 STARTTLS, but 465 SSL works fine."""
    import smtplib
    import ssl
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart

    if not GMAIL_USER or not GMAIL_APP_PASSWORD:
        print("[WARN] Gmail credentials not configured.")
        return False

    try:
        msg = MIMEMultipart("alternative")
        msg["From"] = GMAIL_USER
        msg["To"] = to_email
        msg["Subject"] = subject
        msg.attach(MIMEText(body_html, "html"))

        if SMTP_MODE == "ssl":
            try:
                # Try secure SSL context first (best practice)
                ctx = ssl.create_default_context()
                server = smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ctx, timeout=30)
            except Exception as ssl_err:
                # Fallback to unverified SSL context on cloud platforms like Render
                print(f"[WARN] Secure SSL context failed ({ssl_err}). Falling back to unverified context...")
                ctx = ssl._create_unverified_context()
                server = smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ctx, timeout=30)
            
            with server:
                server.login(GMAIL_USER, GMAIL_APP_PASSWORD)
                server.sendmail(GMAIL_USER, to_email, msg.as_string())
        else:
            try:
                # Try secure STARTTLS context first
                ctx = ssl.create_default_context()
                server = smtplib.SMTP("smtp.gmail.com", 587, timeout=30)
                server.starttls(context=ctx)
            except Exception as ssl_err:
                # Fallback to unverified context on cloud platforms like Render
                print(f"[WARN] Secure STARTTLS context failed ({ssl_err}). Falling back to unverified context...")
                ctx = ssl._create_unverified_context()
                server = smtplib.SMTP("smtp.gmail.com", 587, timeout=30)
                server.starttls(context=ctx)

            with server:
                server.login(GMAIL_USER, GMAIL_APP_PASSWORD)
                server.sendmail(GMAIL_USER, to_email, msg.as_string())

        print(f"[MAIL] Email sent to {to_email}")
        return True
    except Exception as e:
        print(f"[ERROR] Email send failed to {to_email}: {e}")
        return False


def send_otp_email(to_email, otp_code):
    subject = "REYDM – Your Verification Code"
    body = f"""
    <div style="font-family:sans-serif;max-width:480px;margin:auto;padding:30px;
                border:1px solid #e0e0e0;border-radius:12px;">
        <h2 style="color:#1a1a2e;text-align:center;">Verification Code</h2>
        <p style="color:#555;text-align:center;">Use this code to complete your registration:</p>
        <div style="text-align:center;margin:24px 0;">
            <span style="font-size:32px;font-weight:700;letter-spacing:8px;
                         color:#e94560;background:#fef2f2;padding:12px 24px;
                         border-radius:8px;">{otp_code}</span>
        </div>
        <p style="color:#888;text-align:center;font-size:13px;">
            This code expires in <strong>10 minutes</strong>.
        </p>
    </div>
    """
    return send_email(to_email, subject, body)


def send_approval_notification(to_email, full_name):
    subject = "REYDM – New User Awaiting Approval"
    body = f"""
    <div style="font-family:sans-serif;max-width:480px;margin:auto;padding:30px;
                border:1px solid #e0e0e0;border-radius:12px;">
        <h2 style="color:#1a1a2e;">New Registration Request</h2>
        <p>A new user has registered and is waiting for admin approval:</p>
        <table style="width:100%;margin:16px 0;">
            <tr><td style="color:#888;">Name:</td><td><strong>{full_name}</strong></td></tr>
            <tr><td style="color:#888;">Email:</td><td><strong>{to_email}</strong></td></tr>
        </table>
    </div>
    """
    conn = get_db()
    if conn:
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT email FROM users WHERE role='admin' AND is_approved=1")
        admins = cur.fetchall()
        cur.close()
        conn.close()
        for admin in admins:
            send_email(admin["email"], subject, body)


def send_user_approved_email(to_email, full_name):
    subject = "REYDM – Account Approved!"
    body = f"""
    <div style="font-family:sans-serif;max-width:480px;margin:auto;padding:30px;
                border:1px solid #e0e0e0;border-radius:12px;">
        <h2 style="color:#16a34a;text-align:center;">Welcome, {full_name}!</h2>
        <p style="text-align:center;color:#555;">
            Your account has been approved. You can now log in to REYDM.
        </p>
    </div>
    """
    return send_email(to_email, subject, body)


def send_reminder_email(to_email, project_name, reminder_time):
    subject = f"⏰ Reminder: {project_name}"
    # Format IST-aware
    if hasattr(reminder_time, "strftime"):
        formatted_time = reminder_time.strftime("%B %d, %Y at %I:%M %p")
    else:
        formatted_time = str(reminder_time)
    body = f"""
    <div style="font-family:sans-serif;max-width:480px;margin:auto;padding:30px;
                border:1px solid #e0e0e0;border-radius:12px;">
        <h2 style="color:#e94560;text-align:center;">Project Reminder</h2>
        <div style="background:#fef2f2;padding:20px;border-radius:8px;margin:16px 0;">
            <h3 style="margin:0 0 8px;color:#1a1a2e;">{project_name}</h3>
            <p style="margin:0;color:#666;">Scheduled: {formatted_time} (IST)</p>
        </div>
    </div>
    """
    return send_email(to_email, subject, body)


# ═══════════════════════════════════════════════════════════════════════
# AUTH DECORATORS
# ═══════════════════════════════════════════════════════════════════════

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            flash("Please log in first.", "warning")
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            flash("Please log in first.", "warning")
            return redirect(url_for("login"))
        if session.get("role") != "admin":
            flash("Admin access required.", "danger")
            return redirect(url_for("dashboard"))
        return f(*args, **kwargs)
    return decorated


def tool_required(tool_key):
    """Decorator: ensures the user has access to the given tool."""
    def wrapper(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if "user_id" not in session:
                flash("Please log in first.", "warning")
                return redirect(url_for("login"))
            if session.get("role") == "admin":
                return f(*args, **kwargs)
            allowed = session.get("allowed_tools", [])
            if tool_key not in allowed:
                flash("You don't have access to this tool.", "danger")
                return redirect(url_for("dashboard"))
            return f(*args, **kwargs)
        return decorated
    return wrapper


def get_user_tools():
    if session.get("role") == "admin":
        return list(AVAILABLE_TOOLS.keys())
    return session.get("allowed_tools", [])


@app.context_processor
def inject_tools():
    user_tools = []
    if "user_id" in session:
        for key in get_user_tools():
            if key in AVAILABLE_TOOLS:
                user_tools.append({"key": key, **AVAILABLE_TOOLS[key]})
    return dict(
        user_tools=user_tools,
        all_tools=AVAILABLE_TOOLS,
        get_user_tools=get_user_tools,
    )


# ═══════════════════════════════════════════════════════════════════════
# AUTH ROUTES
# ═══════════════════════════════════════════════════════════════════════

@app.route("/")
def index():
    if "user_id" in session:
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if "user_id" in session:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")

        conn = get_db()
        if not conn:
            flash("Database connection error.", "danger")
            return render_template("login.html")

        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT * FROM users WHERE email = %s", (email,))
        user = cur.fetchone()
        cur.close()
        conn.close()

        if not user or not check_password_hash(user["password_hash"], password):
            flash("Invalid email or password.", "danger")
            return render_template("login.html")

        if not user["is_approved"]:
            flash("Your account is awaiting admin approval.", "warning")
            return render_template("login.html")

        if not user["is_active"]:
            flash("Your account has been deactivated.", "danger")
            return render_template("login.html")

        session.permanent = True
        session["user_id"] = user["id"]
        session["full_name"] = user["full_name"]
        session["email"] = user["email"]
        session["role"] = user["role"]

        # Parse allowed_tools robustly
        session["allowed_tools"] = parse_allowed_tools(user.get("allowed_tools"))

        # Update last_active
        conn2 = get_db()
        if conn2:
            c2 = conn2.cursor()
            c2.execute("UPDATE users SET last_active = %s WHERE id = %s", (now_ist(), user["id"]))
            conn2.commit()
            c2.close()
            conn2.close()

        flash(f"Welcome back, {user['full_name']}!", "success")
        return redirect(url_for("dashboard"))

    return render_template("login.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if "user_id" in session:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        errors = []
        if not full_name:
            errors.append("Full name is required.")
        if not email:
            errors.append("Email is required.")
        if len(password) < 6:
            errors.append("Password must be at least 6 characters.")
        if password != confirm_password:
            errors.append("Passwords do not match.")

        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template("register.html")

        conn = get_db()
        if not conn:
            flash("Database connection error.", "danger")
            return render_template("register.html")

        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT id FROM users WHERE email = %s", (email,))
        if cur.fetchone():
            flash("Email is already registered.", "danger")
            cur.close()
            conn.close()
            return render_template("register.html")

        otp_code = "".join(random.choices(string.digits, k=6))
        expires_at = now_ist() + timedelta(minutes=10)

        cur.execute(
            """INSERT INTO otp_tokens (email, otp_code, purpose, expires_at)
               VALUES (%s, %s, 'register', %s)""",
            (email, otp_code, expires_at),
        )
        conn.commit()
        cur.close()
        conn.close()

        threading.Thread(target=send_otp_email, args=(email, otp_code), daemon=True).start()

        session["reg_data"] = {
            "full_name": full_name,
            "email": email,
            "password": password,
        }

        flash("A verification code has been sent to your email.", "info")
        return redirect(url_for("verify_otp"))

    return render_template("register.html")


@app.route("/verify-otp", methods=["GET", "POST"])
def verify_otp():
    reg_data = session.get("reg_data")
    if not reg_data:
        flash("Please register first.", "warning")
        return redirect(url_for("register"))

    if request.method == "POST":
        otp_input = request.form.get("otp", "").strip()
        email = reg_data["email"]

        conn = get_db()
        cur = conn.cursor(dictionary=True)
        cur.execute(
            """SELECT * FROM otp_tokens
               WHERE email = %s AND otp_code = %s AND purpose = 'register'
                     AND is_used = 0 AND expires_at > NOW()
               ORDER BY id DESC LIMIT 1""",
            (email, otp_input),
        )
        token = cur.fetchone()

        if not token:
            flash("Invalid or expired OTP.", "danger")
            cur.close()
            conn.close()
            return render_template("verify_otp.html", email=email)

        cur.execute("UPDATE otp_tokens SET is_used = 1 WHERE id = %s", (token["id"],))

        pw_hash = generate_password_hash(reg_data["password"])
        try:
            cur.execute(
                """INSERT INTO users (full_name, email, password_hash, role, is_approved, allowed_tools)
                   VALUES (%s, %s, %s, 'user', 0, '[]')""",
                (reg_data["full_name"], email, pw_hash),
            )
            conn.commit()
        except mysql.connector.IntegrityError:
            flash("Email already registered.", "danger")
            cur.close()
            conn.close()
            return redirect(url_for("register"))

        cur.close()
        conn.close()

        threading.Thread(
            target=send_approval_notification,
            args=(email, reg_data["full_name"]),
            daemon=True,
        ).start()

        session.pop("reg_data", None)
        flash("Registration successful! Pending admin approval.", "success")
        return redirect(url_for("login"))

    return render_template("verify_otp.html", email=reg_data["email"])


@app.route("/resend-otp", methods=["POST"])
def resend_otp():
    reg_data = session.get("reg_data")
    if not reg_data:
        return jsonify({"success": False, "message": "Session expired."}), 400

    email = reg_data["email"]
    otp_code = "".join(random.choices(string.digits, k=6))
    expires_at = now_ist() + timedelta(minutes=10)

    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO otp_tokens (email, otp_code, purpose, expires_at)
           VALUES (%s, %s, 'register', %s)""",
        (email, otp_code, expires_at),
    )
    conn.commit()
    cur.close()
    conn.close()

    threading.Thread(target=send_otp_email, args=(email, otp_code), daemon=True).start()
    return jsonify({"success": True, "message": "A new OTP has been sent."})


@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("login"))


# ═══════════════════════════════════════════════════════════════════════
# DASHBOARD
# ═══════════════════════════════════════════════════════════════════════

@app.route("/dashboard")
@login_required
def dashboard():
    conn = get_db()
    cur = conn.cursor(dictionary=True)

    is_admin = session.get("role") == "admin"
    tools = get_user_tools()

    # Admin-wide stats
    pending_count = 0
    total_users = 0
    active_users = 0
    today_logins = 0
    pending_attendance_requests = 0
    upcoming_reminders = 0
    total_reminders = 0
    db_storage = {}

    if is_admin:
        cur.execute("SELECT COUNT(*) AS cnt FROM users WHERE is_approved = 0")
        pending_count = cur.fetchone()["cnt"]

        cur.execute("SELECT COUNT(*) AS cnt FROM users")
        total_users = cur.fetchone()["cnt"]

        cur.execute("SELECT COUNT(*) AS cnt FROM users WHERE is_approved = 1 AND is_active = 1")
        active_users = cur.fetchone()["cnt"]

        cur.execute("SELECT COUNT(*) AS cnt FROM attendance_logs WHERE login_date = CURDATE()")
        today_logins = cur.fetchone()["cnt"]

        cur.execute("SELECT COUNT(*) AS cnt FROM attendance_requests WHERE status = 'pending'")
        pending_attendance_requests = cur.fetchone()["cnt"]

        cur.execute("SELECT COUNT(*) AS cnt FROM reminders WHERE is_sent = 0 AND reminder_datetime > NOW()")
        upcoming_reminders = cur.fetchone()["cnt"]

        cur.execute("SELECT COUNT(*) AS cnt FROM reminders")
        total_reminders = cur.fetchone()["cnt"]

        # Database storage info
        try:
            cur.execute(
                """SELECT
                        ROUND(SUM(data_length + index_length) / 1024 / 1024, 2) AS size_mb,
                        COUNT(*) AS table_count
                   FROM information_schema.tables
                   WHERE table_schema = %s""",
                (DB_CONFIG["database"],),
            )
            row = cur.fetchone()
            db_storage["size_mb"] = float(row["size_mb"] or 0)
            db_storage["table_count"] = row["table_count"] or 0
        except Exception:
            db_storage = {"size_mb": 0, "table_count": 0}

    # ─── User-specific dashboard data ────────────────────────────────
    # 1) Attendance — today's session + this month's totals
    attendance_data = None
    if "attendance" in tools or is_admin:
        cur.execute(
            """SELECT * FROM attendance_logs
               WHERE user_id = %s AND login_date = CURDATE()
               ORDER BY login_time DESC LIMIT 1""",
            (session["user_id"],),
        )
        today_log = cur.fetchone()

        cur.execute(
            """SELECT COUNT(*) AS days, COALESCE(SUM(hours_spent), 0) AS hrs
               FROM attendance_logs
               WHERE user_id = %s
                 AND YEAR(login_date) = YEAR(CURDATE())
                 AND MONTH(login_date) = MONTH(CURDATE())""",
            (session["user_id"],),
        )
        month_stats = cur.fetchone()

        attendance_data = {
            "today_log": today_log,
            "month_days": month_stats["days"] if month_stats else 0,
            "month_hours": float(month_stats["hrs"] or 0) if month_stats else 0,
        }

    # 2) User login info
    cur.execute("SELECT full_name, email, role, created_at, last_active FROM users WHERE id = %s",
                (session["user_id"],))
    me = cur.fetchone()

    # 3) Leave details (only if user has leave manager OR they're an admin)
    leave_summary = None
    if "leavemanager" in tools or is_admin:
        # Look up the leave manager employee by matching name (best-effort)
        # For self-view: find employee whose name matches the user's full name.
        cur.execute(
            "SELECT * FROM lm_employees WHERE LOWER(name) = LOWER(%s) LIMIT 1",
            (me["full_name"] if me else "",),
        )
        my_lm_emp = cur.fetchone()
        if my_lm_emp:
            yr = today_ist().year
            cur.execute(
                """SELECT lv_type, COUNT(*) AS cnt FROM lm_leaves
                   WHERE emp_id = %s AND yr = %s GROUP BY lv_type""",
                (my_lm_emp["emp_id"], yr),
            )
            rows = cur.fetchall()
            c_taken = s_taken = l_taken = 0
            for r in rows:
                t = (r["lv_type"] or "").upper()
                cnt = r["cnt"]
                if t == "C":
                    c_taken += cnt
                elif t == "S":
                    s_taken += cnt
                elif t == "L":
                    l_taken += cnt
                elif t in ("HC", "CH"):
                    c_taken += 0.5 * cnt
                elif t in ("HS", "SH"):
                    s_taken += 0.5 * cnt
            leave_summary = {
                "emp": my_lm_emp,
                "year": yr,
                "casual_taken": c_taken,
                "sick_taken": s_taken,
                "lop_taken": l_taken,
            }

    # 4) Reminders if user has it
    reminders = []
    if "reminder" in tools or is_admin:
        cur.execute("""
            SELECT r.*, u.full_name AS creator_name
            FROM reminders r JOIN users u ON r.created_by = u.id
            ORDER BY r.reminder_datetime DESC
            LIMIT 10
        """)
        reminders = cur.fetchall()

    # 5) Admin-only: all users today + recent activity
    all_users_today = []
    if is_admin:
        cur.execute("""
            SELECT u.id, u.full_name, u.email, u.last_active,
                   al.login_time, al.logout_time, al.hours_spent
            FROM users u
            LEFT JOIN attendance_logs al
              ON al.user_id = u.id AND al.login_date = CURDATE()
            WHERE u.is_approved = 1 AND u.is_active = 1
            ORDER BY u.full_name ASC
        """)
        all_users_today = cur.fetchall()

    cur.close()
    conn.close()

    return render_template(
        "dashboard.html",
        is_admin=is_admin,
        me=me,
        attendance_data=attendance_data,
        leave_summary=leave_summary,
        reminders=reminders,
        upcoming_count=upcoming_reminders,
        total_reminders=total_reminders,
        pending_count=pending_count,
        total_users=total_users,
        active_users=active_users,
        today_logins=today_logins,
        pending_attendance_requests=pending_attendance_requests,
        db_storage=db_storage,
        all_users_today=all_users_today,
    )


# ═══════════════════════════════════════════════════════════════════════
# REMINDERS
# ═══════════════════════════════════════════════════════════════════════

@app.route("/reminders/add", methods=["GET", "POST"])
@login_required
@tool_required("reminder")
def add_reminder():
    if request.method == "POST":
        project_name = request.form.get("project_name", "").strip()
        reminder_date = request.form.get("reminder_date", "")
        reminder_time = request.form.get("reminder_time", "")

        if not project_name or not reminder_date or not reminder_time:
            flash("All fields are required.", "danger")
            return render_template("add_reminder.html")

        try:
            reminder_dt = datetime.strptime(f"{reminder_date} {reminder_time}", "%Y-%m-%d %H:%M")
        except ValueError:
            flash("Invalid date/time format.", "danger")
            return render_template("add_reminder.html")

        if reminder_dt <= now_ist():
            flash("Reminder must be in the future.", "danger")
            return render_template("add_reminder.html")

        conn = get_db()
        cur = conn.cursor(dictionary=True)
        cur.execute(
            "SELECT id FROM reminders WHERE project_name = %s AND reminder_datetime = %s",
            (project_name, reminder_dt),
        )
        if cur.fetchone():
            flash("Duplicate reminder exists.", "warning")
            cur.close()
            conn.close()
            return render_template("add_reminder.html")

        cur.execute(
            """INSERT INTO reminders (project_name, reminder_datetime, created_by)
               VALUES (%s, %s, %s)""",
            (project_name, reminder_dt, session["user_id"]),
        )
        conn.commit()
        cur.close()
        conn.close()

        flash("Reminder created successfully!", "success")
        return redirect(url_for("dashboard"))

    return render_template("add_reminder.html")


@app.route("/reminders/delete/<int:reminder_id>", methods=["POST"])
@login_required
@tool_required("reminder")
def delete_reminder(reminder_id):
    conn = get_db()
    cur = conn.cursor(dictionary=True)
    cur.execute("SELECT * FROM reminders WHERE id = %s", (reminder_id,))
    reminder = cur.fetchone()
    if not reminder:
        flash("Reminder not found.", "danger")
    elif session["role"] != "admin" and reminder["created_by"] != session["user_id"]:
        flash("You can only delete your own reminders.", "danger")
    else:
        cur.execute("DELETE FROM reminders WHERE id = %s", (reminder_id,))
        conn.commit()
        flash("Reminder deleted.", "success")
    cur.close()
    conn.close()
    return redirect(url_for("dashboard"))


@app.route("/reminders/edit/<int:reminder_id>", methods=["GET", "POST"])
@login_required
@tool_required("reminder")
def edit_reminder(reminder_id):
    conn = get_db()
    cur = conn.cursor(dictionary=True)
    cur.execute("SELECT * FROM reminders WHERE id = %s", (reminder_id,))
    reminder = cur.fetchone()
    if not reminder:
        flash("Reminder not found.", "danger")
        cur.close()
        conn.close()
        return redirect(url_for("dashboard"))
    if session["role"] != "admin" and reminder["created_by"] != session["user_id"]:
        flash("You can only edit your own reminders.", "danger")
        cur.close()
        conn.close()
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        project_name = request.form.get("project_name", "").strip()
        reminder_date = request.form.get("reminder_date", "")
        reminder_time = request.form.get("reminder_time", "")
        try:
            reminder_dt = datetime.strptime(f"{reminder_date} {reminder_time}", "%Y-%m-%d %H:%M")
        except ValueError:
            flash("Invalid date/time format.", "danger")
            return render_template("edit_reminder.html", reminder=reminder)
        cur.execute(
            "SELECT id FROM reminders WHERE project_name = %s AND reminder_datetime = %s AND id != %s",
            (project_name, reminder_dt, reminder_id),
        )
        if cur.fetchone():
            flash("Duplicate reminder exists.", "warning")
            return render_template("edit_reminder.html", reminder=reminder)
        cur.execute(
            "UPDATE reminders SET project_name = %s, reminder_datetime = %s WHERE id = %s",
            (project_name, reminder_dt, reminder_id),
        )
        conn.commit()
        flash("Reminder updated.", "success")
        cur.close()
        conn.close()
        return redirect(url_for("dashboard"))

    cur.close()
    conn.close()
    return render_template("edit_reminder.html", reminder=reminder)


@app.route("/reminders/trigger/<int:reminder_id>", methods=["POST"])
@login_required
def trigger_reminder(reminder_id):
    conn = get_db()
    if not conn:
        return jsonify({"success": False, "message": "Database error."}), 500
    cur = conn.cursor(dictionary=True)
    cur.execute("SELECT * FROM reminders WHERE id = %s", (reminder_id,))
    reminder = cur.fetchone()
    if not reminder:
        cur.close()
        conn.close()
        return jsonify({"success": False, "message": "Not found."}), 404
    if reminder["is_sent"]:
        cur.close()
        conn.close()
        return jsonify({"success": True, "message": "Already sent."})

    cur.execute("SELECT email FROM users WHERE is_approved = 1 AND is_active = 1 AND mail_enabled = 1")
    users = cur.fetchall()
    sent_count = 0
    for user in users:
        success = send_reminder_email(user["email"], reminder["project_name"], reminder["reminder_datetime"])
        cur.execute(
            "INSERT INTO reminder_logs (reminder_id, sent_to, status) VALUES (%s, %s, %s)",
            (reminder["id"], user["email"], "sent" if success else "failed"),
        )
        if success:
            sent_count += 1

    cur.execute("UPDATE reminders SET is_sent = 1 WHERE id = %s", (reminder_id,))
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({
        "success": True,
        "message": f"Sent to {sent_count} user(s).",
        "sent_count": sent_count,
        "total_users": len(users),
    })


@app.route("/api/reminders")
@login_required
def api_reminders():
    conn = get_db()
    cur = conn.cursor(dictionary=True)
    cur.execute("""
        SELECT r.*, u.full_name AS creator_name
        FROM reminders r JOIN users u ON r.created_by = u.id
        ORDER BY r.reminder_datetime ASC
    """)
    reminders = cur.fetchall()
    cur.close()
    conn.close()
    return jsonify([{
        "id": r["id"], "project_name": r["project_name"],
        "reminder_datetime": r["reminder_datetime"].strftime("%Y-%m-%d %H:%M"),
        "creator_name": r["creator_name"], "is_sent": r["is_sent"],
        "created_by": r["created_by"],
    } for r in reminders])


# ═══════════════════════════════════════════════════════════════════════
# NIGHT SHIFT ATTENDANCE
# ═══════════════════════════════════════════════════════════════════════

@app.route("/nightshift")
@login_required
@tool_required("nightshift")
def nightshift():
    return render_template("nightshift.html")


@app.route("/api/ns/employees")
@login_required
@tool_required("nightshift")
def api_ns_employees():
    conn = get_db()
    cur = conn.cursor(dictionary=True)
    cur.execute("SELECT * FROM ns_employees ORDER BY emp_id ASC")
    emps = cur.fetchall()
    cur.close()
    conn.close()
    return jsonify(emps)


@app.route("/api/ns/employees", methods=["POST"])
@login_required
@tool_required("nightshift")
def api_ns_add_employee():
    data = request.get_json() or {}
    emp_id = str(data.get("emp_id", "")).strip()
    name = str(data.get("name", "")).strip()
    dept = str(data.get("dept", "")).strip()
    status = data.get("status", "active")
    if status not in ("active", "resigned"):
        status = "active"
    if not emp_id or not name:
        return jsonify({"success": False, "message": "ID and Name required."}), 400

    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO ns_employees (emp_id, name, dept, status) VALUES (%s, %s, %s, %s)",
            (emp_id, name, dept, status),
        )
        conn.commit()
    except mysql.connector.IntegrityError:
        cur.close()
        conn.close()
        return jsonify({"success": False, "message": "Employee ID already exists."}), 400
    cur.close()
    conn.close()
    return jsonify({"success": True})


@app.route("/api/ns/employees/<emp_id>", methods=["PUT"])
@login_required
@tool_required("nightshift")
def api_ns_update_employee(emp_id):
    data = request.get_json() or {}
    new_id = str(data.get("emp_id", "")).strip()
    name = str(data.get("name", "")).strip()
    dept = str(data.get("dept", "")).strip()
    status = data.get("status", "active")
    if status not in ("active", "resigned"):
        status = "active"
    if not new_id or not name:
        return jsonify({"success": False, "message": "ID and Name required."}), 400

    conn = get_db()
    cur = conn.cursor()
    try:
        if new_id != emp_id:
            cur.execute("UPDATE ns_attendance SET emp_id = %s WHERE emp_id = %s", (new_id, emp_id))
        cur.execute(
            "UPDATE ns_employees SET emp_id = %s, name = %s, dept = %s, status = %s WHERE emp_id = %s",
            (new_id, name, dept, status, emp_id),
        )
        conn.commit()
    except mysql.connector.IntegrityError:
        cur.close()
        conn.close()
        return jsonify({"success": False, "message": "Employee ID already exists."}), 400
    cur.close()
    conn.close()
    return jsonify({"success": True})


@app.route("/api/ns/employees/<emp_id>", methods=["DELETE"])
@login_required
@tool_required("nightshift")
def api_ns_delete_employee(emp_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM ns_attendance WHERE emp_id = %s", (emp_id,))
    cur.execute("DELETE FROM ns_employees WHERE emp_id = %s", (emp_id,))
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({"success": True})


@app.route("/api/ns/employees/bulk", methods=["POST"])
@login_required
@tool_required("nightshift")
def api_ns_bulk_add():
    data = request.get_json() or {}
    employees = data.get("employees", [])
    added = skipped = 0
    conn = get_db()
    cur = conn.cursor()
    for emp in employees:
        try:
            cur.execute(
                "INSERT INTO ns_employees (emp_id, name, dept, status) VALUES (%s, %s, %s, %s)",
                (emp.get("emp_id"), emp.get("name"), emp.get("dept", ""), emp.get("status", "active")),
            )
            added += 1
        except mysql.connector.IntegrityError:
            skipped += 1
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({"success": True, "added": added, "skipped": skipped})


@app.route("/api/ns/attendance/<int:year>/<int:month>")
@login_required
@tool_required("nightshift")
def api_ns_attendance(year, month):
    conn = get_db()
    cur = conn.cursor(dictionary=True)
    cur.execute(
        """SELECT emp_id, DAY(att_date) AS day_num
           FROM ns_attendance
           WHERE YEAR(att_date) = %s AND MONTH(att_date) = %s AND present = 1""",
        (year, month),
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    result = {}
    for r in rows:
        result.setdefault(r["emp_id"], []).append(r["day_num"])
    return jsonify(result)


@app.route("/api/ns/attendance/toggle", methods=["POST"])
@login_required
@tool_required("nightshift")
def api_ns_toggle_attendance():
    data = request.get_json() or {}
    emp_id = data["emp_id"]
    year = int(data["year"])
    month = int(data["month"])
    day = int(data["day"])
    att_date = f"{year}-{month:02d}-{day:02d}"

    conn = get_db()
    cur = conn.cursor(dictionary=True)
    cur.execute("SELECT id FROM ns_attendance WHERE emp_id = %s AND att_date = %s", (emp_id, att_date))
    existing = cur.fetchone()
    if existing:
        cur.execute("DELETE FROM ns_attendance WHERE id = %s", (existing["id"],))
        present = False
    else:
        cur.execute("INSERT INTO ns_attendance (emp_id, att_date, present) VALUES (%s, %s, 1)", (emp_id, att_date))
        present = True
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({"success": True, "present": present})


@app.route("/api/ns/attendance/year/<int:year>")
@login_required
@tool_required("nightshift")
def api_ns_year_attendance(year):
    conn = get_db()
    cur = conn.cursor(dictionary=True)
    cur.execute(
        """SELECT emp_id, MONTH(att_date) AS month_num, COUNT(*) AS total
           FROM ns_attendance
           WHERE YEAR(att_date) = %s AND present = 1
           GROUP BY emp_id, MONTH(att_date)""",
        (year,),
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    result = {}
    for r in rows:
        result.setdefault(r["emp_id"], {})[r["month_num"]] = r["total"]
    return jsonify(result)


# ═══════════════════════════════════════════════════════════════════════
# SIMPLE TOOL PAGES
# ═══════════════════════════════════════════════════════════════════════

@app.route("/charpalette")
@login_required
@tool_required("charpalette")
def charpalette():
    return render_template("charpalette.html")


@app.route("/costconverter")
@login_required
@tool_required("costconverter")
def costconverter():
    return render_template("costconverter.html")


@app.route("/projectanalysis")
@login_required
@tool_required("projectanalysis")
def projectanalysis():
    return render_template("projectanalysis.html")


@app.route("/pdfunlocker")
@login_required
@tool_required("pdfunlocker")
def pdfunlocker():
    return render_template("pdfunlocker.html")


# ═══════════════════════════════════════════════════════════════════════
# MACRO MANAGER ROUTES
# ═══════════════════════════════════════════════════════════════════════

def get_word_templates_dir():
    """Dynamically determine the active Microsoft Word templates directory.
    Queries Windows Registry keys for Word options and common template path redirections.
    Falls back to %APPDATA%/Microsoft/Templates if registry scanning fails or returns empty."""
    import platform
    if platform.system() != 'Windows':
        return None
        
    try:
        from flask import session
        custom_dir = session.get('custom_templates_dir')
        if custom_dir:
            return custom_dir
    except RuntimeError:
        pass
    except Exception:
        pass
        
    appdata_dir = os.environ.get('APPDATA')
    if appdata_dir:
        default_templates_dir = os.path.join(appdata_dir, 'Microsoft', 'Templates')
    else:
        system_name = os.environ.get('USERNAME')
        if not system_name:
            try:
                import getpass
                system_name = getpass.getuser()
            except Exception:
                system_name = "Default"
        sys_drive = os.environ.get('SystemDrive', 'C:')
        default_templates_dir = os.path.join(sys_drive + os.sep, 'Users', system_name, 'AppData', 'Roaming', 'Microsoft', 'Templates')
    
    try:
        import winreg
    except ImportError:
        return default_templates_dir

    # 1. Determine Office versions installed
    office_versions = []
    for root in [winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE]:
        try:
            with winreg.OpenKey(root, "Software\\Microsoft\\Office") as office_key:
                num_keys = winreg.QueryInfoKey(office_key)[0]
                for i in range(num_keys):
                    subkey_name = winreg.EnumKey(office_key, i)
                    if subkey_name.replace('.', '').isdigit():
                        if subkey_name not in office_versions:
                            office_versions.append(subkey_name)
        except Exception:
            pass
            
    # Sort version keys in descending order (e.g. 16.0, 15.0)
    try:
        office_versions = sorted(office_versions, key=lambda x: [float(i) for i in x.split('.') if i.replace('.', '').isdigit()], reverse=True)
    except Exception:
        pass
        
    if not office_versions:
        office_versions = ["16.0", "15.0", "14.0"]

    # 2. Check all common registry locations
    roots = [winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE]
    key_templates = [
        r"Software\Microsoft\Office\{version}\Word\Options",
        r"Software\Microsoft\Office\{version}\Common\General",
        r"Software\Policies\Microsoft\Office\{version}\Word\Options",
        r"Software\Policies\Microsoft\Office\{version}\Common\General",
    ]
    value_names = ["DOT-PATH", "UserTemplates"]

    for version in office_versions:
        for root in roots:
            for template in key_templates:
                key_path = template.format(version=version)
                try:
                    with winreg.OpenKey(root, key_path, 0, winreg.KEY_READ) as key:
                        for val_name in value_names:
                            try:
                                path_val, _ = winreg.QueryValueEx(key, val_name)
                                if path_val:
                                    path_val = os.path.expandvars(path_val)
                                    path_val = os.path.abspath(path_val)
                                    # Accept the path if it already exists or if its parent exists
                                    if os.path.isdir(path_val) or os.path.exists(os.path.dirname(path_val)):
                                        return path_val
                            except Exception:
                                pass
                except Exception:
                    pass

    return default_templates_dir


@app.route("/macromanager")
@login_required
@tool_required("macromanager")
def macromanager():
    import platform
    is_windows = (platform.system() == 'Windows')
    
    local_path = None
    local_exists = False
    local_backup_exists = False
    local_files = []
    system_name = None
    
    if is_windows:
        system_name = os.environ.get('USERNAME')
        if not system_name:
            try:
                import getpass
                system_name = getpass.getuser()
            except Exception:
                system_name = "Default"
        templates_dir = get_word_templates_dir()
        if templates_dir:
            local_path = os.path.join(templates_dir, 'Normal.dotm')
            if os.path.exists(local_path):
                local_exists = True
            # Check if backups exist and list files
            if os.path.exists(templates_dir):
                for file in os.listdir(templates_dir):
                    if file.startswith("Normal.dotm.bak"):
                        local_backup_exists = True
                    
                    lower_file = file.lower()
                    # Filter for template-related files and OneDrive/conflict copies
                    if 'normal' in lower_file or lower_file.endswith('.dotm') or lower_file.endswith('.dotx'):
                        file_path = os.path.join(templates_dir, file)
                        if os.path.isfile(file_path):
                            try:
                                stat_info = os.stat(file_path)
                                size_bytes = stat_info.st_size
                                modified_time = datetime.fromtimestamp(stat_info.st_mtime)
                                local_files.append({
                                    'name': file,
                                    'size': f"{size_bytes / 1024:.1f} KB" if size_bytes > 0 else "0 KB",
                                    'size_bytes': size_bytes,
                                    'modified': modified_time.strftime("%b %d, %Y %I:%M %p")
                                })
                            except Exception:
                                pass
                local_files.sort(key=lambda x: x['name'].lower())

    conn = get_db()
    cur = conn.cursor(dictionary=True)
    cur.execute("""
        SELECT mf.id, mf.filename, mf.uploaded_at, u.full_name as uploader_name, mf.uploaded_by,
               (mf.ui_data IS NOT NULL AND LENGTH(mf.ui_data) > 0) AS has_ui
        FROM macro_files mf
        LEFT JOIN users u ON mf.uploaded_by = u.id
        ORDER BY mf.uploaded_at DESC
    """)
    files = cur.fetchall()
    cur.close()
    conn.close()

    word_running_id = session.get("macro_word_running_id")

    return render_template(
        "macromanager.html",
        is_windows=is_windows,
        system_name=system_name,
        local_path=local_path,
        local_exists=local_exists,
        local_backup_exists=local_backup_exists,
        local_files=local_files,
        files=files,
        word_running_id=word_running_id
    )

@app.route("/macromanager/select_path", methods=["POST"])
@login_required
@tool_required("macromanager")
def macromanager_select_path():
    import platform
    if platform.system() != 'Windows':
        flash("Manual path selection is only supported on Windows.", "danger")
        return redirect(url_for("macromanager"))
        
    try:
        import tkinter as tk
        from tkinter import filedialog
        
        # Initialize and hide tkinter root
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        
        # Open folder picker dialog
        selected_dir = filedialog.askdirectory(title="Select Microsoft Word Templates Directory")
        root.destroy()
        
        if selected_dir:
            selected_dir = os.path.abspath(selected_dir.replace('/', os.sep))
            session['custom_templates_dir'] = selected_dir
            flash(f"Successfully connected and updated templates directory to '{selected_dir}'.", "success")
        else:
            flash("No directory selected. Using default/registry path.", "info")
            
    except Exception as e:
        flash(f"Failed to open directory browser: {str(e)}", "danger")
        
    return redirect(url_for("macromanager"))

@app.route("/macromanager/reset_path", methods=["POST"])
@login_required
@tool_required("macromanager")
def macromanager_reset_path():
    session.pop('custom_templates_dir', None)
    flash("Templates directory path reset to system default / registry path.", "success")
    return redirect(url_for("macromanager"))

@app.route("/macromanager/export", methods=["POST"])
@login_required
@tool_required("macromanager")
def macromanager_export():
    if session.get('role') != 'admin':
        flash("Only administrators are allowed to export macro templates.", "danger")
        return redirect(url_for("macromanager"))
        
    import platform
    is_windows = (platform.system() == 'Windows')
    
    if not is_windows:
        flash("Exporting local Word settings is only supported when running the desktop app locally on Windows.", "danger")
        return redirect(url_for("macromanager"))
        
    templates_dir = get_word_templates_dir()
    if not templates_dir:
        flash("Could not determine Microsoft Word templates directory.", "danger")
        return redirect(url_for("macromanager"))
        
    local_path = os.path.join(templates_dir, 'Normal.dotm')
    if not os.path.exists(local_path):
        flash("No local MS Word Normal.dotm template file was found. Please ensure you have customized your Word settings or created macros first.", "danger")
        return redirect(url_for("macromanager"))
        
    # Attempt to read Ribbon/Taskbar UI customizations
    ui_data = None
    localappdata = os.environ.get('LOCALAPPDATA')
    ui_path = None
    if localappdata:
        ui_path = os.path.join(localappdata, 'Microsoft', 'Office', 'Word.officeUI')
        if os.path.exists(ui_path):
            try:
                with open(ui_path, 'rb') as uf:
                    ui_data = uf.read()
            except Exception as e:
                print(f"Warning: could not read Word.officeUI: {e}")
                
    try:
        import zipfile
        import io
        
        # Build the zip archive in-memory
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
            for root, dirs, files in os.walk(templates_dir):
                # Exclude Microsoft's cache folders like LiveContent to keep size small
                if 'LiveContent' in root:
                    continue
                for file in files:
                    # Exclude temp files and existing backups
                    if file.startswith('~$') or '.bak' in file:
                        continue
                    file_path = os.path.join(root, file)
                    if os.path.exists(file_path):
                        rel_path = os.path.relpath(file_path, templates_dir).replace('\\', '/')
                        zip_file.write(file_path, rel_path)
            
            # Package the Word.officeUI file into the zip under a special folder
            if ui_path and os.path.exists(ui_path) and ui_data:
                zip_file.writestr('__OfficeUI__/Word.officeUI', ui_data)
                
        file_data = zip_buffer.getvalue()
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"Templates_Exported_{timestamp}.zip"
        
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO macro_files (filename, file_data, ui_data, uploaded_by) VALUES (%s, %s, %s, %s)",
            (filename, file_data, ui_data, session.get('user_id'))
        )
        conn.commit()
        cur.close()
        conn.close()
        
        ui_msg = " and Ribbon customizations (Word.officeUI)" if ui_data else " (no custom ribbon UI file found)"
        flash(f"Successfully compiled, zipped and exported your active Templates folder{ui_msg} as '{filename}' and added it to the library. Users can now import & replace this on their computers.", "success")
    except Exception as e:
        flash(f"Error exporting Templates folder: {str(e)}", "danger")
        
    return redirect(url_for("macromanager"))


@app.route("/macromanager/upload", methods=["POST"])
@login_required
@tool_required("macromanager")
def macromanager_upload():
    if session.get('role') != 'admin':
        flash("Only administrators are allowed to upload macro templates.", "danger")
        return redirect(url_for("macromanager"))
        
    if 'file' not in request.files:
        flash("No file part.", "danger")
        return redirect(url_for("macromanager"))
    
    file = request.files['file']
    if file.filename == '':
        flash("No selected file.", "danger")
        return redirect(url_for("macromanager"))
    
    filename_lower = file.filename.lower()
    if not (filename_lower.endswith('.dotm') or filename_lower.endswith('.zip')):
        flash("Invalid file type. Only MS Word Template files (.dotm) or zipped templates (.zip) are allowed.", "danger")
        return redirect(url_for("macromanager"))
    
    # Check file size limit (maximum size is 15MB)
    file_data = file.read()
    if len(file_data) > 15 * 1024 * 1024:
        flash("File too large. Maximum size is 15MB.", "danger")
        return redirect(url_for("macromanager"))
        
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO macro_files (filename, file_data, uploaded_by) VALUES (%s, %s, %s)",
        (file.filename, file_data, session.get('user_id'))
    )
    conn.commit()
    cur.close()
    conn.close()
    
    flash(f"Macro file '{file.filename}' uploaded successfully.", "success")
    return redirect(url_for("macromanager"))

@app.route("/macromanager/download/<int:file_id>")
@login_required
@tool_required("macromanager")
def macromanager_download(file_id):
    conn = get_db()
    cur = conn.cursor(dictionary=True)
    cur.execute("SELECT filename, file_data FROM macro_files WHERE id = %s", (file_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    
    if not row:
        flash("File not found.", "danger")
        return redirect(url_for("macromanager"))
        
    import io
    from flask import send_file
    mimetype = 'application/zip' if row['filename'].lower().endswith('.zip') else 'application/vnd.ms-word.template.macroEnabled.12'
    return send_file(
        io.BytesIO(row['file_data']),
        as_attachment=True,
        download_name=row['filename'],
        mimetype=mimetype
    )

@app.route("/macromanager/download_ui/<int:file_id>")
@login_required
@tool_required("macromanager")
def macromanager_download_ui(file_id):
    conn = get_db()
    cur = conn.cursor(dictionary=True)
    cur.execute("SELECT filename, ui_data FROM macro_files WHERE id = %s", (file_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    
    if not row or not row['ui_data']:
        flash("Ribbon customization UI file was not found for this template.", "danger")
        return redirect(url_for("macromanager"))
        
    import io
    from flask import send_file
    ui_filename = row['filename'].replace('.dotm', '.officeUI')
    if not ui_filename.endswith('.officeUI'):
        ui_filename = "Word.officeUI"
        
    return send_file(
        io.BytesIO(row['ui_data']),
        as_attachment=True,
        download_name=ui_filename,
        mimetype='text/xml'
    )

def clear_templates_directory(templates_dir):
    import shutil
    import stat
    if not os.path.exists(templates_dir):
        return
    for item in os.listdir(templates_dir):
        item_path = os.path.join(templates_dir, item)
        # Skip LiveContent
        if item.lower() == 'livecontent':
            continue
        # Skip backups
        if 'templates_backup_' in item.lower() or 'normal.dotm.bak_' in item.lower():
            continue
        try:
            if os.path.isdir(item_path):
                # Recursively clear read-only flag on Windows to allow clean deletion
                def remove_readonly(func, path, excinfo):
                    try:
                        os.chmod(path, stat.S_IWRITE)
                    except Exception:
                        pass
                    func(path)
                shutil.rmtree(item_path, onerror=remove_readonly)
            else:
                try:
                    os.chmod(item_path, stat.S_IWRITE)
                except Exception:
                    pass
                os.remove(item_path)
        except Exception as e:
            print(f"Warning: failed to delete {item_path}: {e}")

@app.route("/macromanager/apply/<int:file_id>", methods=["POST"])
@login_required
@tool_required("macromanager")
def macromanager_apply(file_id):
    import platform
    is_windows = (platform.system() == 'Windows')
    
    if not is_windows:
        flash("Automatic installation is only supported when running the desktop app locally on Windows.", "danger")
        return redirect(url_for("macromanager"))
        
    templates_dir = get_word_templates_dir()
    if not templates_dir:
        flash("Could not determine Microsoft Word templates directory.", "danger")
        return redirect(url_for("macromanager"))

    # Check if Microsoft Word is running (winword.exe)
    import subprocess
    try:
        tasklist_output = subprocess.check_output('tasklist /FI "IMAGENAME eq winword.exe" /NH', shell=True)
        word_is_running = b"winword.exe" in tasklist_output.lower()
    except Exception:
        word_is_running = False

    if word_is_running:
        force = request.form.get("force", "false").lower() in ("true", "1", "yes")
        if force:
            try:
                subprocess.check_call('taskkill /f /im winword.exe', shell=True)
                time.sleep(0.5)
                word_is_running = False
                flash("Microsoft Word was successfully closed.", "info")
            except Exception as e:
                flash(f"Failed to force close Word: {str(e)}", "danger")
                return redirect(url_for("macromanager"))
        else:
            session["macro_word_running_id"] = file_id
            flash("Error: Microsoft Word is currently running! Please save your work and close all MS Word windows completely before importing & replacing the template. If Word is open, it locks the file or will overwrite your changes when it closes.", "danger")
            return redirect(url_for("macromanager"))
        
    local_path = os.path.join(templates_dir, 'Normal.dotm')
    
    # Retrieve file data and UI data from database
    conn = get_db()
    cur = conn.cursor(dictionary=True)
    cur.execute("SELECT filename, file_data, ui_data FROM macro_files WHERE id = %s", (file_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    
    if not row:
        flash("Macro template file not found in database.", "danger")
        return redirect(url_for("macromanager"))
        
    try:
        # Create templates directory if it does not exist
        if not os.path.exists(templates_dir):
            os.makedirs(templates_dir)
            
        is_zip = row['filename'].lower().endswith('.zip')
        
        if is_zip:
            # 1. Back up existing templates directory
            import zipfile
            import shutil
            import stat
            
            backup_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_path = os.path.join(os.path.dirname(templates_dir), f"Templates_Backup_{backup_timestamp}.zip")
            
            try:
                with zipfile.ZipFile(backup_path, 'w', zipfile.ZIP_DEFLATED) as backup_zip:
                    for root, dirs, files in os.walk(templates_dir):
                        if 'LiveContent' in root:
                            continue
                        for file in files:
                            file_path = os.path.join(root, file)
                            if os.path.exists(file_path):
                                rel_path = os.path.relpath(file_path, templates_dir).replace('\\', '/')
                                backup_zip.write(file_path, rel_path)
                backup_msg = f"A backup of your old templates folder was saved as 'Templates_Backup_{backup_timestamp}.zip'."
            except Exception as be:
                backup_msg = f" (Warning: templates folder backup failed: {str(be)})"
                
            # 2. Extract zip package
            zip_buffer = io.BytesIO(row['file_data'])
            ui_restore_msg = ""
            
            # Clear old templates first (excluding LiveContent and backups)
            clear_templates_directory(templates_dir)
            
            ui_backed_up = False
            
            with zipfile.ZipFile(zip_buffer, 'r') as zip_file:
                # First, find if there is a common top-level directory prefix for non-UI template files
                members = zip_file.infolist()
                non_ui_files = []
                for member in members:
                    norm_name = member.filename.replace('\\', '/')
                    if norm_name.endswith('/') or member.is_dir():
                        continue
                    
                    # Clean leading slashes and dot-segments
                    clean_name = norm_name
                    while clean_name.startswith('/') or clean_name.startswith('../') or clean_name.startswith('./'):
                        if clean_name.startswith('/'):
                            clean_name = clean_name[1:]
                        elif clean_name.startswith('../'):
                            clean_name = clean_name[3:]
                        elif clean_name.startswith('./'):
                            clean_name = clean_name[2:]
                            
                    if not clean_name.lower().startswith('__officeui__/'):
                        non_ui_files.append(clean_name)
                
                common_prefix = ""
                if non_ui_files:
                    parts = non_ui_files[0].split('/')
                    if len(parts) > 1:
                        first_seg = parts[0]
                        standard_folders = {'document themes', 'smartart graphics', 'livecontent', '1033'}
                        is_locale_folder = first_seg.isdigit() and len(first_seg) == 4
                        if first_seg.lower() not in standard_folders and not is_locale_folder:
                            all_share = True
                            for f in non_ui_files:
                                if not f.startswith(first_seg + '/'):
                                    all_share = False
                                    break
                            if all_share:
                                common_prefix = first_seg + '/'
                
                for member in members:
                    norm_name = member.filename.replace('\\', '/')
                    
                    # Skip directory entries
                    if norm_name.endswith('/') or member.is_dir():
                        continue
                    
                    # Clean leading slashes and dot-segments
                    clean_name = norm_name
                    while clean_name.startswith('/') or clean_name.startswith('../') or clean_name.startswith('./'):
                        if clean_name.startswith('/'):
                            clean_name = clean_name[1:]
                        elif clean_name.startswith('../'):
                            clean_name = clean_name[3:]
                        elif clean_name.startswith('./'):
                            clean_name = clean_name[2:]
                        
                    # Check if this is the Word.officeUI file (case-insensitive)
                    if clean_name.lower().startswith('__officeui__/'):
                        ui_filename = clean_name[len('__officeui__/'):]
                        if not ui_filename:
                            continue
                        localappdata = os.environ.get('LOCALAPPDATA')
                        if localappdata:
                            ui_dir = os.path.join(localappdata, 'Microsoft', 'Office')
                            ui_path = os.path.join(ui_dir, ui_filename)
                            try:
                                if not os.path.exists(ui_dir):
                                    os.makedirs(ui_dir)
                                if os.path.exists(ui_path) and not ui_backed_up:
                                    try:
                                        os.chmod(ui_path, stat.S_IWRITE)
                                    except Exception:
                                        pass
                                    ui_backup = f"{ui_path}.bak_{backup_timestamp}"
                                    shutil.copy2(ui_path, ui_backup)
                                    ui_restore_msg = f" Additionally, your old ribbon/taskbar UI customization was backed up as '{os.path.basename(ui_path)}.bak_{backup_timestamp}'."
                                    ui_backed_up = True
                                
                                if os.path.exists(ui_path):
                                    try:
                                        os.chmod(ui_path, stat.S_IWRITE)
                                    except Exception:
                                        pass
                                with open(ui_path, 'wb') as uf:
                                    uf.write(zip_file.read(member.filename))
                                if "successfully restored" not in ui_restore_msg:
                                    ui_restore_msg += f" Ribbon and taskbar UI customizations ({ui_filename}) successfully restored."
                            except Exception as ue:
                                ui_restore_msg = f" (Warning: failed to restore ribbon UI customizations: {str(ue)})"
                    else:
                        # Extract normal template folder files
                        rel_filename = clean_name
                        if common_prefix and clean_name.startswith(common_prefix):
                            rel_filename = clean_name[len(common_prefix):]
                        
                        target_path = os.path.join(templates_dir, rel_filename.replace('/', os.sep))
                        target_dir = os.path.dirname(target_path)
                        if not os.path.exists(target_dir):
                            os.makedirs(target_dir)
                            
                        # Clear read-only attribute if file exists, to prevent PermissionError
                        if os.path.exists(target_path):
                            try:
                                os.chmod(target_path, stat.S_IWRITE)
                            except Exception:
                                pass
                        with open(target_path, 'wb') as f:
                            f.write(zip_file.read(member.filename))
                            
            session.pop("macro_word_running_id", None)
            flash(f"Success! Imported and replaced the active Word templates folder using '{row['filename']}'. {backup_msg}{ui_restore_msg} Please restart Microsoft Word.", "success")
            
        else:
            # Standard individual file replacement logic (.dotm)
            import stat
            if os.path.exists(local_path):
                try:
                    os.chmod(local_path, stat.S_IWRITE)
                except Exception:
                    pass
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                backup_path = f"{local_path}.bak_{timestamp}"
                import shutil
                shutil.copy2(local_path, backup_path)
                backup_msg = f"A backup of your old template was saved as 'Normal.dotm.bak_{timestamp}'."
            else:
                backup_msg = "No existing Normal.dotm template was found, a new one was created."
                
            if os.path.exists(local_path):
                try:
                    os.chmod(local_path, stat.S_IWRITE)
                except Exception:
                    pass
            # Write new file data
            with open(local_path, 'wb') as f:
                f.write(row['file_data'])
                
            # Restore Ribbon / Taskbar UI customization if available
            ui_restore_msg = ""
            if 'ui_data' in row and row['ui_data']:
                localappdata = os.environ.get('LOCALAPPDATA')
                if localappdata:
                    ui_dir = os.path.join(localappdata, 'Microsoft', 'Office')
                    ui_path = os.path.join(ui_dir, 'Word.officeUI')
                    try:
                        if not os.path.exists(ui_dir):
                            os.makedirs(ui_dir)
                        if os.path.exists(ui_path):
                            try:
                                os.chmod(ui_path, stat.S_IWRITE)
                            except Exception:
                                pass
                            ui_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                            ui_backup_path = f"{ui_path}.bak_{ui_timestamp}"
                            import shutil
                            shutil.copy2(ui_path, ui_backup_path)
                            ui_restore_msg = f" Additionally, your old ribbon/taskbar UI customization was backed up as 'Word.officeUI.bak_{ui_timestamp}'."
                        
                        if os.path.exists(ui_path):
                            try:
                                os.chmod(ui_path, stat.S_IWRITE)
                            except Exception:
                                pass
                        with open(ui_path, 'wb') as uf:
                            uf.write(row['ui_data'])
                        ui_restore_msg += " Taskbar UI and custom Ribbon tabs successfully restored."
                    except Exception as ue:
                        ui_restore_msg = f" (Warning: failed to restore ribbon UI customizations: {str(ue)})"
                
            session.pop("macro_word_running_id", None)
            flash(f"Success! Replaced local Normal.dotm with '{row['filename']}'. {backup_msg}{ui_restore_msg} Please restart Microsoft Word to apply the macros, custom UI, and shortcuts.", "success")
    except Exception as e:
        flash(f"Error applying macro: {str(e)}", "danger")
        
    return redirect(url_for("macromanager"))

@app.route("/macromanager/delete/<int:file_id>", methods=["POST"])
@login_required
@tool_required("macromanager")
def macromanager_delete(file_id):
    if session.get('role') != 'admin':
        flash("Only administrators are allowed to delete macro templates.", "danger")
        return redirect(url_for("macromanager"))
        
    conn = get_db()
    cur = conn.cursor(dictionary=True)
    cur.execute("SELECT id, filename FROM macro_files WHERE id = %s", (file_id,))
    row = cur.fetchone()
    
    if not row:
        cur.close()
        conn.close()
        flash("File not found.", "danger")
        return redirect(url_for("macromanager"))
        
    cur.execute("DELETE FROM macro_files WHERE id = %s", (file_id,))
    conn.commit()
    cur.close()
    conn.close()
    
    flash(f"Macro file '{row['filename']}' deleted successfully.", "success")
    return redirect(url_for("macromanager"))


# ═══════════════════════════════════════════════════════════════════════
# ATTENDANCE (Login/Logout Tracker) — IST + idempotent upsert logic
# ═══════════════════════════════════════════════════════════════════════

@app.route("/attendance")
@login_required
@tool_required("attendance")
def attendance():
    conn = get_db()
    cur = conn.cursor(dictionary=True)

    # Active session today (no logout yet)
    cur.execute(
        """SELECT * FROM attendance_logs
           WHERE user_id = %s AND login_date = CURDATE() AND logout_time IS NULL
           ORDER BY login_time DESC LIMIT 1""",
        (session["user_id"],),
    )
    active_session = cur.fetchone()

    # Today's completed log (if any)
    cur.execute(
        """SELECT * FROM attendance_logs
           WHERE user_id = %s AND login_date = CURDATE()
           ORDER BY login_time DESC LIMIT 1""",
        (session["user_id"],),
    )
    today_log = cur.fetchone()

    cur.execute(
        """SELECT * FROM attendance_logs
           WHERE user_id = %s AND login_date >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)
           ORDER BY login_date DESC, login_time DESC""",
        (session["user_id"],),
    )
    recent_logs = cur.fetchall()

    cur.execute(
        "SELECT COUNT(*) AS cnt FROM attendance_requests WHERE user_id = %s AND status = 'pending'",
        (session["user_id"],),
    )
    pending_requests = cur.fetchone()["cnt"]

    cur.execute(
        """SELECT * FROM attendance_requests
           WHERE user_id = %s ORDER BY created_at DESC LIMIT 20""",
        (session["user_id"],),
    )
    my_requests = cur.fetchall()

    cur.close()
    conn.close()

    return render_template(
        "attendance.html",
        active_session=active_session,
        today_log=today_log,
        recent_logs=recent_logs,
        pending_requests=pending_requests,
        my_requests=my_requests,
    )


@app.route("/attendance/login", methods=["POST"])
@login_required
@tool_required("attendance")
def attendance_login():
    """
    Idempotent login:
      - If a row for (user, today) already exists with login_time → reject (use upsert logic).
      - Otherwise insert a new row with login_time.
    """
    conn = get_db()
    cur = conn.cursor(dictionary=True)
    today = today_ist()
    now = now_ist()

    cur.execute(
        "SELECT * FROM attendance_logs WHERE user_id = %s AND login_date = %s",
        (session["user_id"], today),
    )
    existing = cur.fetchone()

    if existing:
        if existing["logout_time"] is None:
            flash("You are already logged in for today. Please logout first.", "warning")
        else:
            flash("You have already completed today's attendance. New login not allowed.", "warning")
        cur.close()
        conn.close()
        return redirect(url_for("attendance"))

    cur.execute(
        """INSERT INTO attendance_logs (user_id, login_date, login_time)
           VALUES (%s, %s, %s)""",
        (session["user_id"], today, now),
    )
    conn.commit()
    cur.close()
    conn.close()
    flash(f"Logged in at {now.strftime('%I:%M %p')} (IST)", "success")
    return redirect(url_for("attendance"))


@app.route("/attendance/logout", methods=["POST"])
@login_required
@tool_required("attendance")
def attendance_logout():
    conn = get_db()
    cur = conn.cursor(dictionary=True)
    cur.execute(
        """SELECT * FROM attendance_logs
           WHERE user_id = %s AND login_date = CURDATE() AND logout_time IS NULL
           ORDER BY login_time DESC LIMIT 1""",
        (session["user_id"],),
    )
    active = cur.fetchone()
    if not active:
        flash("No active login session found for today.", "warning")
        cur.close()
        conn.close()
        return redirect(url_for("attendance"))

    now = now_ist()
    login_time = active["login_time"]
    diff = (now - login_time).total_seconds() / 3600.0
    hours_spent = round(diff, 2)

    cur.execute(
        "UPDATE attendance_logs SET logout_time = %s, hours_spent = %s WHERE id = %s",
        (now, hours_spent, active["id"]),
    )
    conn.commit()
    cur.close()
    conn.close()
    flash(f"Logged out at {now.strftime('%I:%M %p')} (IST). Hours: {hours_spent} hrs", "success")
    return redirect(url_for("attendance"))


@app.route("/attendance/request", methods=["POST"])
@login_required
@tool_required("attendance")
def attendance_request():
    """User submits a request to manually add/change attendance.
    Smart logic:
      - If user requests a date that already has a login but no logout,
        treat the request as a logout-only update (no new row creation).
      - If date has full row (login + logout), reject as duplicate.
      - If no row exists, request creates a new entry on approval.
    """
    req_date_str = request.form.get("request_date", "")
    req_login = request.form.get("request_login", "")
    req_logout = request.form.get("request_logout", "")
    reason = request.form.get("reason", "").strip()

    if not req_date_str or not req_logout:
        flash("Date and logout time are required.", "danger")
        return redirect(url_for("attendance"))

    try:
        req_date = datetime.strptime(req_date_str, "%Y-%m-%d").date()
    except ValueError:
        flash("Invalid date format.", "danger")
        return redirect(url_for("attendance"))

    conn = get_db()
    cur = conn.cursor(dictionary=True)

    # Check existing attendance for that date
    cur.execute(
        "SELECT * FROM attendance_logs WHERE user_id = %s AND login_date = %s",
        (session["user_id"], req_date),
    )
    existing = cur.fetchone()

    try:
        if existing:
            # Login already exists. Don't create a new entry — request a logout update.
            if existing["logout_time"] is not None:
                flash("This date already has full login/logout records. Request not needed.", "warning")
                cur.close()
                conn.close()
                return redirect(url_for("attendance"))

            # Existing login but no logout → user must supply only the logout time
            login_dt = existing["login_time"]
            logout_dt = datetime.strptime(f"{req_date_str} {req_logout}", "%Y-%m-%d %H:%M")
            if logout_dt <= login_dt:
                logout_dt += timedelta(days=1)

            cur.execute(
                """INSERT INTO attendance_requests
                   (user_id, request_date, requested_login, requested_logout, reason)
                   VALUES (%s, %s, %s, %s, %s)""",
                (session["user_id"], req_date, login_dt, logout_dt,
                 f"[Logout only] {reason}"),
            )
            flash("Logout-time request submitted (existing login will be reused).", "success")
        else:
            # No record — need both login and logout
            if not req_login:
                flash("This date has no login record. Please provide login time too.", "danger")
                cur.close()
                conn.close()
                return redirect(url_for("attendance"))
            login_dt = datetime.strptime(f"{req_date_str} {req_login}", "%Y-%m-%d %H:%M")
            logout_dt = datetime.strptime(f"{req_date_str} {req_logout}", "%Y-%m-%d %H:%M")
            if logout_dt <= login_dt:
                logout_dt += timedelta(days=1)

            cur.execute(
                """INSERT INTO attendance_requests
                   (user_id, request_date, requested_login, requested_logout, reason)
                   VALUES (%s, %s, %s, %s, %s)""",
                (session["user_id"], req_date, login_dt, logout_dt, reason),
            )
            flash("Attendance request submitted for admin approval.", "success")
        conn.commit()
    except ValueError:
        flash("Invalid time format.", "danger")
    finally:
        cur.close()
        conn.close()

    return redirect(url_for("attendance"))


@app.route("/admin/attendance-requests")
@admin_required
def admin_attendance_requests():
    conn = get_db()
    cur = conn.cursor(dictionary=True)
    cur.execute(
        """SELECT ar.*, u.full_name, u.email
           FROM attendance_requests ar
           JOIN users u ON ar.user_id = u.id
           ORDER BY
             CASE ar.status WHEN 'pending' THEN 0 ELSE 1 END,
             ar.created_at DESC
           LIMIT 100"""
    )
    requests_list = cur.fetchall()
    cur.close()
    conn.close()
    return render_template("admin_attendance_requests.html", requests=requests_list)


@app.route("/admin/attendance-requests/<int:req_id>/<action>", methods=["POST"])
@admin_required
def handle_attendance_request(req_id, action):
    if action not in ("approve", "decline"):
        flash("Invalid action.", "danger")
        return redirect(url_for("admin_attendance_requests"))

    conn = get_db()
    cur = conn.cursor(dictionary=True)
    cur.execute("SELECT * FROM attendance_requests WHERE id = %s", (req_id,))
    req = cur.fetchone()
    if not req:
        flash("Request not found.", "danger")
        cur.close()
        conn.close()
        return redirect(url_for("admin_attendance_requests"))

    if action == "approve":
        login_dt = req["requested_login"]
        logout_dt = req["requested_logout"]
        diff = (logout_dt - login_dt).total_seconds() / 3600.0
        hours_spent = round(diff, 2)

        # Upsert: don't create duplicate; reuse if user already has a row for that date
        cur.execute(
            "SELECT * FROM attendance_logs WHERE user_id = %s AND login_date = %s",
            (req["user_id"], req["request_date"]),
        )
        existing = cur.fetchone()
        if existing:
            # Update only logout if login already present, otherwise overwrite
            if existing["logout_time"] is None:
                # Existing has login only — just update logout
                lt = existing["login_time"]
                new_hrs = round((logout_dt - lt).total_seconds() / 3600.0, 2)
                cur.execute(
                    "UPDATE attendance_logs SET logout_time = %s, hours_spent = %s WHERE id = %s",
                    (logout_dt, new_hrs, existing["id"]),
                )
            else:
                cur.execute(
                    """UPDATE attendance_logs
                       SET login_time = %s, logout_time = %s, hours_spent = %s
                       WHERE id = %s""",
                    (login_dt, logout_dt, hours_spent, existing["id"]),
                )
        else:
            cur.execute(
                """INSERT INTO attendance_logs (user_id, login_date, login_time, logout_time, hours_spent)
                   VALUES (%s, %s, %s, %s, %s)""",
                (req["user_id"], req["request_date"], login_dt, logout_dt, hours_spent),
            )

        cur.execute(
            "UPDATE attendance_requests SET status = 'approved', reviewed_by = %s WHERE id = %s",
            (session["user_id"], req_id),
        )
        flash("Request approved and attendance logged.", "success")
    else:
        cur.execute(
            "UPDATE attendance_requests SET status = 'declined', reviewed_by = %s WHERE id = %s",
            (session["user_id"], req_id),
        )
        flash("Request declined.", "info")

    conn.commit()
    cur.close()
    conn.close()
    return redirect(url_for("admin_attendance_requests"))


@app.route("/api/attendance/chart")
@login_required
def api_attendance_chart():
    conn = get_db()
    cur = conn.cursor(dictionary=True)
    cur.execute(
        """SELECT login_date, SUM(hours_spent) AS total_hours
           FROM attendance_logs
           WHERE user_id = %s AND login_date >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)
             AND hours_spent IS NOT NULL
           GROUP BY login_date ORDER BY login_date ASC""",
        (session["user_id"],),
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return jsonify([
        {"date": r["login_date"].strftime("%Y-%m-%d"), "hours": float(r["total_hours"])}
        for r in rows
    ])


# ═══════════════════════════════════════════════════════════════════════
# ADMIN: USER MANAGEMENT
# ═══════════════════════════════════════════════════════════════════════

@app.route("/admin/users")
@admin_required
def admin_users():
    conn = get_db()
    cur = conn.cursor(dictionary=True)
    cur.execute("SELECT * FROM users ORDER BY created_at DESC")
    users = cur.fetchall()
    cur.close()
    conn.close()
    for u in users:
        u["allowed_tools"] = parse_allowed_tools(u.get("allowed_tools"))
    pending_users = [u for u in users if not u.get("is_approved")]
    return render_template("admin_users.html", users=users, pending_users=pending_users)


@app.route("/admin/users/approve/<int:user_id>", methods=["POST"])
@admin_required
def approve_user(user_id):
    conn = get_db()
    cur = conn.cursor(dictionary=True)
    cur.execute("SELECT * FROM users WHERE id = %s", (user_id,))
    user = cur.fetchone()
    if user:
        cur.execute("UPDATE users SET is_approved = 1 WHERE id = %s", (user_id,))
        conn.commit()
        threading.Thread(
            target=send_user_approved_email,
            args=(user["email"], user["full_name"]),
            daemon=True,
        ).start()
        flash(f"User {user['full_name']} approved.", "success")
    cur.close()
    conn.close()
    return redirect(url_for("admin_users"))


@app.route("/admin/users/reject/<int:user_id>", methods=["POST"])
@admin_required
def reject_user(user_id):
    conn = get_db()
    cur = conn.cursor(dictionary=True)
    cur.execute("SELECT * FROM users WHERE id = %s", (user_id,))
    user = cur.fetchone()
    if user and user["role"] != "admin":
        cur.execute("DELETE FROM users WHERE id = %s", (user_id,))
        conn.commit()
        flash(f"User {user['full_name']} rejected.", "info")
    cur.close()
    conn.close()
    return redirect(url_for("admin_users"))


@app.route("/admin/users/toggle-active/<int:user_id>", methods=["POST"])
@admin_required
def toggle_user_active(user_id):
    conn = get_db()
    cur = conn.cursor(dictionary=True)
    cur.execute("SELECT * FROM users WHERE id = %s", (user_id,))
    user = cur.fetchone()
    if user and user["id"] != session["user_id"]:
        new_status = 0 if user["is_active"] else 1
        cur.execute("UPDATE users SET is_active = %s WHERE id = %s", (new_status, user_id))
        conn.commit()
        flash(f"User {user['full_name']} {'activated' if new_status else 'deactivated'}.", "success")
    cur.close()
    conn.close()
    return redirect(url_for("admin_users"))


@app.route("/admin/users/toggle-mail/<int:user_id>", methods=["POST"])
@admin_required
def toggle_mail(user_id):
    conn = get_db()
    cur = conn.cursor(dictionary=True)
    cur.execute("SELECT * FROM users WHERE id = %s", (user_id,))
    user = cur.fetchone()
    if user:
        new_status = 0 if user["mail_enabled"] else 1
        cur.execute("UPDATE users SET mail_enabled = %s WHERE id = %s", (new_status, user_id))
        conn.commit()
        flash(f"Email {'enabled' if new_status else 'disabled'} for {user['full_name']}.", "success")
    cur.close()
    conn.close()
    return redirect(url_for("admin_users"))


@app.route("/admin/users/change-role/<int:user_id>", methods=["POST"])
@admin_required
def change_role(user_id):
    new_role = request.form.get("role", "user")
    if new_role not in ("admin", "user"):
        flash("Invalid role.", "danger")
        return redirect(url_for("admin_users"))
    conn = get_db()
    cur = conn.cursor(dictionary=True)
    cur.execute("SELECT * FROM users WHERE id = %s", (user_id,))
    user = cur.fetchone()
    if user and user["id"] != session["user_id"]:
        cur.execute("UPDATE users SET role = %s WHERE id = %s", (new_role, user_id))
        conn.commit()
        flash(f"Role for {user['full_name']} changed to {new_role}.", "success")
    cur.close()
    conn.close()
    return redirect(url_for("admin_users"))


@app.route("/admin/users/reset-password/<int:user_id>", methods=["POST"])
@admin_required
def reset_password(user_id):
    new_password = request.form.get("new_password", "")
    if len(new_password) < 6:
        flash("Password must be at least 6 characters.", "danger")
        return redirect(url_for("admin_users"))
    conn = get_db()
    cur = conn.cursor()
    pw_hash = generate_password_hash(new_password)
    cur.execute("UPDATE users SET password_hash = %s WHERE id = %s", (pw_hash, user_id))
    conn.commit()
    cur.close()
    conn.close()
    flash("Password reset successfully.", "success")
    return redirect(url_for("admin_users"))


@app.route("/admin/users/toggle-tool/<int:user_id>/<tool_key>", methods=["POST"])
@admin_required
def toggle_tool(user_id, tool_key):
    if tool_key not in AVAILABLE_TOOLS:
        flash("Invalid tool.", "danger")
        return redirect(url_for("admin_users"))
    conn = get_db()
    cur = conn.cursor(dictionary=True)
    cur.execute("SELECT allowed_tools FROM users WHERE id = %s", (user_id,))
    row = cur.fetchone()
    if not row:
        cur.close()
        conn.close()
        flash("User not found.", "danger")
        return redirect(url_for("admin_users"))
    tools = parse_allowed_tools(row.get("allowed_tools"))

    if tool_key in tools:
        tools.remove(tool_key)
        action = "disabled"
    else:
        tools.append(tool_key)
        action = "enabled"

    cur.execute("UPDATE users SET allowed_tools = %s WHERE id = %s", (json.dumps(tools), user_id))
    conn.commit()
    cur.close()
    conn.close()
    flash(f"{AVAILABLE_TOOLS[tool_key]['name']} {action} for user.", "success")
    return redirect(url_for("admin_users"))


# ═══════════════════════════════════════════════════════════════════════
# USER PROFILE
# ═══════════════════════════════════════════════════════════════════════

@app.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    if request.method == "POST":
        current_password = request.form.get("current_password", "")
        new_password = request.form.get("new_password", "")
        confirm_password = request.form.get("confirm_password", "")
        conn = get_db()
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT * FROM users WHERE id = %s", (session["user_id"],))
        user = cur.fetchone()
        if not check_password_hash(user["password_hash"], current_password):
            flash("Current password is incorrect.", "danger")
        elif len(new_password) < 6:
            flash("New password must be at least 6 characters.", "danger")
        elif new_password != confirm_password:
            flash("New passwords do not match.", "danger")
        else:
            pw_hash = generate_password_hash(new_password)
            cur.execute("UPDATE users SET password_hash = %s WHERE id = %s", (pw_hash, session["user_id"]))
            conn.commit()
            flash("Password updated successfully.", "success")
        cur.close()
        conn.close()

    conn = get_db()
    cur = conn.cursor(dictionary=True)
    cur.execute("SELECT * FROM users WHERE id = %s", (session["user_id"],))
    user = cur.fetchone()
    cur.close()
    conn.close()
    return render_template("profile.html", user=user)


# ═══════════════════════════════════════════════════════════════════════
# REMINDER SCHEDULER (Background Thread)
# ═══════════════════════════════════════════════════════════════════════

def reminder_scheduler():
    while True:
        try:
            conn = get_db()
            if conn:
                cur = conn.cursor(dictionary=True)
                cur.execute("""
                    SELECT * FROM reminders
                    WHERE is_sent = 0
                      AND reminder_datetime <= DATE_ADD(NOW(), INTERVAL 60 SECOND)
                      AND reminder_datetime >= DATE_SUB(NOW(), INTERVAL 5 MINUTE)
                """)
                due = cur.fetchall()
                for reminder in due:
                    cur.execute(
                        "SELECT email FROM users WHERE is_approved = 1 AND is_active = 1 AND mail_enabled = 1"
                    )
                    users = cur.fetchall()
                    for user in users:
                        success = send_reminder_email(
                            user["email"], reminder["project_name"], reminder["reminder_datetime"],
                        )
                        cur.execute(
                            "INSERT INTO reminder_logs (reminder_id, sent_to, status) VALUES (%s, %s, %s)",
                            (reminder["id"], user["email"], "sent" if success else "failed"),
                        )
                    cur.execute("UPDATE reminders SET is_sent = 1 WHERE id = %s", (reminder["id"],))
                    conn.commit()
                cur.close()
                conn.close()
        except Exception as e:
            print(f"Scheduler error: {e}")
        time.sleep(30)


# ═══════════════════════════════════════════════════════════════════════
# PETTY CASH (CBE + DGL) — DB-backed
# ═══════════════════════════════════════════════════════════════════════

@app.route("/petty-cash/coimbatore")
@login_required
@tool_required("pettycash_cbe")
def pettycash_cbe():
    return render_template("petty_cash_coimbatore.html")


@app.route("/petty-cash/dindigul")
@login_required
@tool_required("pettycash_dgl")
def pettycash_dgl():
    return render_template("petty_cash_dindigul.html")


def _pc_office_check(office_key):
    """Map tool key → office shortcode."""
    return {"pettycash_cbe": "cbe", "pettycash_dgl": "dgl"}.get(office_key)


def _pc_required(office_key):
    """Return office shortcode if user has access, else None."""
    if session.get("role") == "admin":
        return _pc_office_check(office_key)
    if office_key in session.get("allowed_tools", []):
        return _pc_office_check(office_key)
    return None


@app.route("/api/pettycash/<office_key>")
@login_required
def api_pc_list(office_key):
    """Get all entries for an office. office_key = pettycash_cbe or pettycash_dgl."""
    office = _pc_required(office_key)
    if not office:
        return jsonify({"success": False, "message": "Access denied"}), 403
    conn = get_db()
    cur = conn.cursor(dictionary=True)
    cur.execute(
        """SELECT id, entry_date, particular, amount, entry_type, category
           FROM petty_cash WHERE office = %s ORDER BY entry_date ASC, id ASC""",
        (office,),
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return jsonify([{
        "id": r["id"],
        "date": r["entry_date"].strftime("%Y-%m-%d"),
        "particular": r["particular"],
        "amount": float(r["amount"]),
        "type": r["entry_type"],
        "category": r["category"] or "",
    } for r in rows])


@app.route("/api/pettycash/<office_key>", methods=["POST"])
@login_required
def api_pc_add(office_key):
    office = _pc_required(office_key)
    if not office:
        return jsonify({"success": False, "message": "Access denied"}), 403
    data = request.get_json() or {}
    try:
        d = datetime.strptime(data.get("date", ""), "%Y-%m-%d").date()
        amount = float(data.get("amount", 0))
        if amount <= 0:
            return jsonify({"success": False, "message": "Amount must be positive"}), 400
    except (ValueError, TypeError):
        return jsonify({"success": False, "message": "Invalid date or amount"}), 400

    particular = str(data.get("particular", "")).strip()[:500]
    entry_type = data.get("type", "debit")
    category = str(data.get("category", "")).strip()[:80]
    if entry_type not in ("credit", "debit"):
        entry_type = "debit"
    if not particular:
        return jsonify({"success": False, "message": "Particular required"}), 400

    conn = get_db()
    cur = conn.cursor(dictionary=True)
    cur.execute(
        """INSERT INTO petty_cash (office, entry_date, particular, amount, entry_type, category, created_by)
           VALUES (%s, %s, %s, %s, %s, %s, %s)""",
        (office, d, particular, amount, entry_type, category, session["user_id"]),
    )
    new_id = cur.lastrowid
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({"success": True, "id": new_id})


@app.route("/api/pettycash/<office_key>/<int:entry_id>", methods=["DELETE"])
@login_required
def api_pc_delete(office_key, entry_id):
    office = _pc_required(office_key)
    if not office:
        return jsonify({"success": False, "message": "Access denied"}), 403
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM petty_cash WHERE id = %s AND office = %s", (entry_id, office))
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({"success": True})


@app.route("/api/pettycash/<office_key>/clear", methods=["POST"])
@login_required
def api_pc_clear(office_key):
    office = _pc_required(office_key)
    if not office:
        return jsonify({"success": False, "message": "Access denied"}), 403
    # Only admins can clear all
    if session.get("role") != "admin":
        return jsonify({"success": False, "message": "Admin only"}), 403
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM petty_cash WHERE office = %s", (office,))
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({"success": True})


# ═══════════════════════════════════════════════════════════════════════
# LEAVE MANAGER — DB-backed
# ═══════════════════════════════════════════════════════════════════════

@app.route("/leave-manager")
@login_required
@tool_required("leavemanager")
def leavemanager():
    return render_template("RDM_Leave_Manager.html")


@app.route("/api/lm/employees")
@login_required
@tool_required("leavemanager")
def api_lm_employees():
    conn = get_db()
    cur = conn.cursor(dictionary=True)
    cur.execute("SELECT * FROM lm_employees ORDER BY sno ASC, id ASC")
    emps = cur.fetchall()
    cur.close()
    conn.close()
    # Normalize keys to JS-friendly format
    return jsonify([{
        "sno": e["sno"],
        "empId": e["emp_id"],
        "name": e["name"],
        "dept": e["dept"] or "",
        "status": e["status"] or "Active",
        "joinDate": e["join_date"].strftime("%Y-%m-%d") if e["join_date"] else "",
        "extraCL": float(e["extra_cl"] or 0),
        "extraSL": float(e["extra_sl"] or 0),
        "extraNote": e["extra_note"] or "",
    } for e in emps])


@app.route("/api/lm/employees", methods=["POST"])
@login_required
@tool_required("leavemanager")
def api_lm_add_employee():
    data = request.get_json() or {}
    emp_id = str(data.get("empId", "")).strip().upper()
    name = str(data.get("name", "")).strip().upper()
    dept = str(data.get("dept", "")).strip()
    status = data.get("status", "Active")
    if status not in ("Active", "Inactive"):
        status = "Active"
    join_date = data.get("joinDate") or None
    try:
        join_date = datetime.strptime(join_date, "%Y-%m-%d").date() if join_date else None
    except Exception:
        join_date = None
    extra_cl = float(data.get("extraCL", 0) or 0)
    extra_sl = float(data.get("extraSL", 0) or 0)
    extra_note = str(data.get("extraNote", "")).strip()[:255]

    if not emp_id or not name or not dept:
        return jsonify({"success": False, "message": "Name, ID, Department required"}), 400

    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT IFNULL(MAX(sno), 0) FROM lm_employees")
    next_sno = (cur.fetchone()[0] or 0) + 1
    try:
        cur.execute(
            """INSERT INTO lm_employees (sno, emp_id, name, dept, status, join_date, extra_cl, extra_sl, extra_note)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (next_sno, emp_id, name, dept, status, join_date, extra_cl, extra_sl, extra_note),
        )
        conn.commit()
    except mysql.connector.IntegrityError:
        cur.close()
        conn.close()
        return jsonify({"success": False, "message": "Employee ID already exists"}), 400
    cur.close()
    conn.close()
    return jsonify({"success": True})


@app.route("/api/lm/employees/<emp_id>", methods=["PUT"])
@login_required
@tool_required("leavemanager")
def api_lm_update_employee(emp_id):
    data = request.get_json() or {}
    new_id = str(data.get("empId", "")).strip().upper()
    name = str(data.get("name", "")).strip().upper()
    dept = str(data.get("dept", "")).strip()
    status = data.get("status", "Active")
    if status not in ("Active", "Inactive"):
        status = "Active"
    join_date = data.get("joinDate") or None
    try:
        join_date = datetime.strptime(join_date, "%Y-%m-%d").date() if join_date else None
    except Exception:
        join_date = None
    extra_cl = float(data.get("extraCL", 0) or 0)
    extra_sl = float(data.get("extraSL", 0) or 0)
    extra_note = str(data.get("extraNote", "")).strip()[:255]

    if not new_id or not name or not dept:
        return jsonify({"success": False, "message": "Name, ID, Department required"}), 400

    conn = get_db()
    cur = conn.cursor()
    if new_id != emp_id:
        cur.execute("UPDATE lm_leaves SET emp_id = %s WHERE emp_id = %s", (new_id, emp_id))
    try:
        cur.execute(
            """UPDATE lm_employees
               SET emp_id = %s, name = %s, dept = %s, status = %s,
                   join_date = %s, extra_cl = %s, extra_sl = %s, extra_note = %s
               WHERE emp_id = %s""",
            (new_id, name, dept, status, join_date, extra_cl, extra_sl, extra_note, emp_id),
        )
        conn.commit()
    except mysql.connector.IntegrityError:
        cur.close()
        conn.close()
        return jsonify({"success": False, "message": "Employee ID conflict"}), 400
    cur.close()
    conn.close()
    return jsonify({"success": True})


@app.route("/api/lm/employees/<emp_id>", methods=["DELETE"])
@login_required
@tool_required("leavemanager")
def api_lm_delete_employee(emp_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM lm_leaves WHERE emp_id = %s", (emp_id,))
    cur.execute("DELETE FROM lm_employees WHERE emp_id = %s", (emp_id,))
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({"success": True})


@app.route("/api/lm/leaves/<int:year>")
@login_required
@tool_required("leavemanager")
def api_lm_leaves(year):
    conn = get_db()
    cur = conn.cursor(dictionary=True)
    cur.execute("SELECT emp_id, mon, dy, lv_type FROM lm_leaves WHERE yr = %s", (year,))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    # Format: { empId: { month: { day: type } } }
    result = {}
    for r in rows:
        result.setdefault(r["emp_id"], {}).setdefault(r["mon"], {})[str(r["dy"])] = r["lv_type"]
    return jsonify(result)


@app.route("/api/lm/leaves", methods=["POST"])
@login_required
@tool_required("leavemanager")
def api_lm_set_leave():
    data = request.get_json() or {}
    emp_id = str(data.get("empId", "")).strip().upper()
    yr = int(data.get("year", 0))
    mon = str(data.get("month", "")).strip()
    dy = int(data.get("day", 0))
    lv_type = str(data.get("type", "")).strip().upper()

    valid_months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
    valid_types = ['C', 'S', 'L', 'HC', 'HS', 'CH', 'SH']
    if not emp_id or yr < 2000 or mon not in valid_months or dy < 1 or dy > 31:
        return jsonify({"success": False, "message": "Invalid data"}), 400

    conn = get_db()
    cur = conn.cursor()
    if lv_type == "":
        cur.execute(
            "DELETE FROM lm_leaves WHERE emp_id = %s AND yr = %s AND mon = %s AND dy = %s",
            (emp_id, yr, mon, dy),
        )
    elif lv_type in valid_types:
        cur.execute(
            """INSERT INTO lm_leaves (emp_id, yr, mon, dy, lv_type) VALUES (%s, %s, %s, %s, %s)
               ON DUPLICATE KEY UPDATE lv_type = VALUES(lv_type)""",
            (emp_id, yr, mon, dy, lv_type),
        )
    else:
        cur.close()
        conn.close()
        return jsonify({"success": False, "message": "Invalid leave type"}), 400
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({"success": True})


@app.route("/api/lm/leaves/bulk", methods=["POST"])
@login_required
@tool_required("leavemanager")
def api_lm_bulk_leaves():
    """Bulk import leaves. Body: { leaves: [{empId, year, month, day, type}, ...] }"""
    data = request.get_json() or {}
    leaves = data.get("leaves", [])
    valid_months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
    valid_types = ['C', 'S', 'L', 'HC', 'HS', 'CH', 'SH']
    added = skipped = 0
    conn = get_db()
    cur = conn.cursor()
    for lv in leaves:
        try:
            emp_id = str(lv.get("empId", "")).strip().upper()
            yr = int(lv.get("year", 0))
            mon = str(lv.get("month", "")).strip()
            dy = int(lv.get("day", 0))
            lv_type = str(lv.get("type", "")).strip().upper()
            if not emp_id or mon not in valid_months or dy < 1 or dy > 31 or lv_type not in valid_types:
                skipped += 1
                continue
            cur.execute(
                """INSERT INTO lm_leaves (emp_id, yr, mon, dy, lv_type) VALUES (%s, %s, %s, %s, %s)
                   ON DUPLICATE KEY UPDATE lv_type = VALUES(lv_type)""",
                (emp_id, yr, mon, dy, lv_type),
            )
            added += 1
        except Exception:
            skipped += 1
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({"success": True, "added": added, "skipped": skipped})


@app.route("/api/lm/employees/bulk", methods=["POST"])
@login_required
@tool_required("leavemanager")
def api_lm_bulk_employees():
    data = request.get_json() or {}
    employees = data.get("employees", [])
    added = skipped = 0
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT IFNULL(MAX(sno), 0) FROM lm_employees")
    next_sno = (cur.fetchone()[0] or 0) + 1
    for emp in employees:
        emp_id = str(emp.get("empId", "")).strip().upper()
        name = str(emp.get("name", "")).strip().upper()
        dept = str(emp.get("dept", "QC")).strip()
        if not emp_id or not name:
            skipped += 1
            continue
        try:
            cur.execute(
                """INSERT INTO lm_employees (sno, emp_id, name, dept, status)
                   VALUES (%s, %s, %s, %s, 'Active')""",
                (next_sno, emp_id, name, dept),
            )
            next_sno += 1
            added += 1
        except mysql.connector.IntegrityError:
            skipped += 1
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({"success": True, "added": added, "skipped": skipped})


@app.route("/api/lm/clear-year/<int:year>", methods=["POST"])
@login_required
@tool_required("leavemanager")
def api_lm_clear_year(year):
    if session.get("role") != "admin":
        return jsonify({"success": False, "message": "Admin only"}), 403
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM lm_leaves WHERE yr = %s", (year,))
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({"success": True})


# ═══════════════════════════════════════════════════════════════════════
# ADMIN: SETTINGS, DB STATS, CACHE CLEAR
# ═══════════════════════════════════════════════════════════════════════

@app.route("/admin/settings")
@admin_required
def admin_settings():
    conn = get_db()
    cur = conn.cursor(dictionary=True)
    # Get DB storage details for display
    try:
        cur.execute(
            """SELECT table_name AS tn,
                      ROUND((data_length + index_length)/1024/1024, 3) AS size_mb,
                      table_rows AS rows
               FROM information_schema.tables
               WHERE table_schema = %s
               ORDER BY (data_length + index_length) DESC""",
            (DB_CONFIG["database"],),
        )
        tables = cur.fetchall()
        total_size = sum(float(t["size_mb"] or 0) for t in tables)
    except Exception:
        tables = []
        total_size = 0
    cur.close()
    conn.close()
    return render_template(
        "admin_settings.html",
        tables=tables,
        total_size=round(total_size, 2),
        db_name=DB_CONFIG["database"],
        smtp_user=GMAIL_USER,
        smtp_mode=SMTP_MODE,
    )


@app.route("/admin/cache/clear-otp", methods=["POST"])
@admin_required
def admin_clear_otp_cache():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM otp_tokens WHERE is_used = 1 OR expires_at < NOW()")
    deleted = cur.rowcount
    conn.commit()
    cur.close()
    conn.close()
    flash(f"Cleared {deleted} expired/used OTP tokens.", "success")
    return redirect(url_for("admin_settings"))


@app.route("/admin/cache/clear-reminder-logs", methods=["POST"])
@admin_required
def admin_clear_reminder_logs():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM reminder_logs WHERE sent_at < DATE_SUB(NOW(), INTERVAL 90 DAY)")
    deleted = cur.rowcount
    conn.commit()
    cur.close()
    conn.close()
    flash(f"Cleared {deleted} reminder log entries older than 90 days.", "success")
    return redirect(url_for("admin_settings"))


@app.route("/admin/cache/clear-old-attendance", methods=["POST"])
@admin_required
def admin_clear_old_attendance():
    """Clear attendance logs older than 1 year (keeps recent year)."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM attendance_logs WHERE login_date < DATE_SUB(CURDATE(), INTERVAL 1 YEAR)")
    deleted = cur.rowcount
    conn.commit()
    cur.close()
    conn.close()
    flash(f"Cleared {deleted} attendance entries older than 1 year.", "success")
    return redirect(url_for("admin_settings"))


@app.route("/admin/cache/optimize-tables", methods=["POST"])
@admin_required
def admin_optimize_tables():
    conn = get_db()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = %s",
            (DB_CONFIG["database"],),
        )
        tables = [r["table_name"] for r in cur.fetchall()]
        for t in tables:
            try:
                cur.execute(f"OPTIMIZE TABLE `{t}`")
                cur.fetchall()
            except Exception:
                pass
        flash(f"Optimised {len(tables)} tables.", "success")
    except Exception as e:
        flash(f"Optimise failed: {e}", "danger")
    cur.close()
    conn.close()
    return redirect(url_for("admin_settings"))


@app.route("/api/admin/db-stats")
@admin_required
def api_admin_db_stats():
    conn = get_db()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute(
            """SELECT table_name AS tn,
                      ROUND((data_length + index_length)/1024/1024, 3) AS size_mb,
                      table_rows AS rows
               FROM information_schema.tables
               WHERE table_schema = %s
               ORDER BY (data_length + index_length) DESC""",
            (DB_CONFIG["database"],),
        )
        tables = cur.fetchall()
    except Exception:
        tables = []
    cur.close()
    conn.close()
    total = sum(float(t["size_mb"] or 0) for t in tables)
    return jsonify({
        "tables": [{"name": t["tn"], "size_mb": float(t["size_mb"] or 0), "rows": t["rows"] or 0} for t in tables],
        "total_mb": round(total, 2),
        "db_name": DB_CONFIG["database"],
    })


# ═══════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    init_db()

    scheduler_thread = threading.Thread(target=reminder_scheduler, daemon=True)
    scheduler_thread.start()

    run_host = os.environ.get("APP_HOST", "0.0.0.0")
    run_port = int(os.environ.get("PORT", os.environ.get("APP_PORT", 5000)))
    run_debug = os.environ.get("APP_DEBUG", "false").lower() in ("true", "1", "yes")

    print("")
    print("+--------------------------------------------------+")
    print("|           REYDM Server                           |")
    print("+--------------------------------------------------+")
    print(f"|  Local:   http://127.0.0.1:{run_port}")
    print(f"|  Debug:   {run_debug}")
    print(f"|  SMTP:    {SMTP_MODE} ({'port 465' if SMTP_MODE=='ssl' else 'port 587'})")
    print("+--------------------------------------------------+")
    print("")

    app.run(debug=run_debug, host=run_host, port=run_port)
else:
    # Production / gunicorn — initialise DB and scheduler on import
    init_db()
    scheduler_thread = threading.Thread(target=reminder_scheduler, daemon=True)
    scheduler_thread.start()