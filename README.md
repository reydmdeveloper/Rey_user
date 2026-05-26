# REYDM – REY Datamind Platform

A multi-tool web platform built with **Flask + MySQL** featuring role-based access control with per-user tool assignment.

## Features

### Core Platform
- **Admin & User roles** – Admins have full access; users see only assigned tools
- **Tool-based permissions** – Admin assigns specific tools (Reminder, Night Shift) to each user via checkboxes
- **User management** – Register with OTP email verification, admin approval workflow
- **Sidebar navigation** – Only shows tools the logged-in user has access to
- **Unified dark theme** – Consistent blue-accent dark UI across all tools

### Tool: Reminder
- Create project reminders with date/time
- Live countdown timers
- Automatic email notifications via Gmail SMTP
- Background scheduler for due reminders

### Tool: Night Shift Attendance
- Monthly attendance grid with click-to-mark
- Employee management (add, edit, delete, import from Excel)
- Active/Resigned status tracking
- Dashboard with year heatmap, monthly bar chart, and per-employee breakdown
- CSV export
- All data stored in MySQL (not localStorage)

## Setup

### 1. Install dependencies
```bash
pip install -r requirements.txt
```

### 2. Configure environment
Copy `.env.example` to `.env` and update your MySQL and Gmail credentials.

### 3. Run the server
```bash
python app.py
```

The server starts at `http://localhost:5000`

### Default Admin Login
- Email: `admin@system.local`
- Password: `admin123`

## How Tool Permissions Work

1. **Admin** goes to **User Management**
2. Each user row has checkboxes for available tools (Reminder, Night Shift)
3. Checking/unchecking a tool immediately updates that user's access
4. When a **User** logs in, the sidebar only shows tools assigned to them
5. Route-level protection ensures users cannot access tools via URL either
6. **Admins** always have access to all tools

## Tech Stack
- **Backend:** Python Flask
- **Database:** MySQL (Aiven Cloud)
- **Frontend:** Jinja2 templates, vanilla JS, Font Awesome
- **Email:** Gmail SMTP with App Passwords
- **Fonts:** IBM Plex Sans + IBM Plex Mono
