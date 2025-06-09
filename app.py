from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
import sqlite3
import os
from datetime import datetime, timedelta
from functools import wraps # Added for decorator

app = Flask(__name__)
app.secret_key = 'your_super_secret_key_here' # **IMPORTANT: Change this to a strong, random key in production!**

# Define the database path
# Keeping the absolute path from our previous discussion for stability.
# If you want to revert to a path relative to your project, change this to:
# DB_FOLDER = 'database'
# DB_PATH = os.path.join(DB_FOLDER, 'attendance.db')
DB_PATH = r'C:\\temp_flask_db\\attendance.db' 

# Ensure the database folder exists
# For absolute path, check if its directory exists
if not os.path.exists(os.path.dirname(DB_PATH)):
    os.makedirs(os.path.dirname(DB_PATH))

# Helper function to get a database connection with row_factory set to sqlite3.Row
def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row  # This allows accessing columns by name (e.g., row['id'] or row.id)
    return conn

# Create tables if not exists
def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()

    # Create employees table with employee_id_text for unique employee IDs
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS employees (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            employee_id_text TEXT UNIQUE NOT NULL, -- New unique ID for employees
            name TEXT NOT NULL,
            department TEXT,
            job_title TEXT
        );
    """)

    # Create attendance table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS attendance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            employee_id INTEGER NOT NULL,
            date TEXT NOT NULL,
            time_in TEXT NOT NULL,
            time_out TEXT,
            location TEXT, -- Added location column
            FOREIGN KEY (employee_id) REFERENCES employees(id)
        );
    """)

    conn.commit()
    conn.close()

# Initialize the database when the app starts
init_db()

# Decorator to restrict access to admin
def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'admin' not in session:
            flash("Please log in as admin to access this page.", "warning")
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# --- Before Request Hook for Authentication ---
@app.before_request
def require_login():
    # List of routes that do NOT require admin login (index, mark_in, mark_out, get_attendance_status are for employees)
    allowed_routes = ['login', 'static', 'mark_in', 'mark_out', 'index', 'get_attendance_status']
    # Check if the requested endpoint is in the allowed routes or if admin is logged in
    if request.endpoint not in allowed_routes and not session.get('admin'):
        # If not logged in as admin and trying to access an admin-only route, redirect to login
        if request.endpoint != 'login': # Avoid infinite redirect
            return redirect(url_for('login'))

 
# New route to get attendance status for selected employee (for JS button control)
@app.route('/get_attendance_status/<int:employee_id>')
def get_attendance_status(employee_id):
    conn = get_db_connection()
    current_date = datetime.now().strftime('%Y-%m-%d')
 
    cursor = conn.cursor()
 
    # Check for an open IN record for today
    cursor.execute(
        "SELECT id FROM attendance WHERE employee_id = ? AND date = ? AND time_out IS NULL",
        (employee_id, current_date)
    )
    open_in_record = cursor.fetchone()
 
    if open_in_record:
        conn.close()
        return jsonify({'status': 'IN'})
 
    # Check for a completed IN/OUT record for today
    cursor.execute(
        "SELECT id FROM attendance WHERE employee_id = ? AND date = ? AND time_out IS NOT NULL",
        (employee_id, current_date)
    )
    completed_record = cursor.fetchone()
 
    if completed_record:
        conn.close()
        return jsonify({'status': 'OUT'})
   
    conn.close()
    return jsonify({'status': 'NONE'}) # No record for today or not yet IN
 
@app.route('/')
def index():
    conn = get_db_connection()
    employees = conn.execute('SELECT id, name FROM employees ORDER BY name ASC').fetchall()

    # Fetch today's marked-in employees and their status
    today_date = datetime.now().strftime('%Y-%m-%d')
    
    # Get employees who marked in today and have NOT marked out
    marked_in_today = conn.execute(f"""
        SELECT E.id, E.name
        FROM employees E
        JOIN attendance A ON E.id = A.employee_id
        WHERE A.date = '{today_date}' AND (A.time_out IS NULL OR A.time_out = '')
    """).fetchall()
    
    marked_in_ids = {emp['id'] for emp in marked_in_today}

    conn.close()
    return render_template('index.html', employees=employees, marked_in_ids=marked_in_ids)


@app.route('/mark_in', methods=['POST'])
def mark_in():
    employee_id = request.form['employee_id']
    location = request.form.get('location', 'Onsite') # Default to Onsite if not provided
    date = datetime.now().strftime('%Y-%m-%d')
    time_in = datetime.now().strftime('%H:%M:%S')

    conn = get_db_connection()
    cursor = conn.cursor()

    # Check if employee has already marked in today
    cursor.execute(f"""
        SELECT * FROM attendance
        WHERE employee_id = ? AND date = ? AND (time_out IS NULL OR time_out = '')
    """, (employee_id, date))
    existing_record = cursor.fetchone()

    if existing_record:
        flash("Employee has already marked in today and not yet marked out.", "error")
    else:
        conn.execute("INSERT INTO attendance (employee_id, date, time_in, location) VALUES (?, ?, ?, ?)",
                     (employee_id, date, time_in, location))
        conn.commit()
        flash("Employee marked in successfully!", "success")
    
    conn.close()
    return redirect(url_for('index'))

@app.route('/mark_out', methods=['POST'])
def mark_out():
    employee_id = request.form['employee_id']
    date = datetime.now().strftime('%Y-%m-%d')
    time_out = datetime.now().strftime('%H:%M:%S')

    conn = get_db_connection()
    cursor = conn.cursor()

    # Find the latest 'in' record for today that hasn't been marked out yet
    cursor.execute(f"""
        SELECT id, time_in FROM attendance
        WHERE employee_id = ? AND date = ? AND (time_out IS NULL OR time_out = '')
        ORDER BY time_in DESC
        LIMIT 1
    """, (employee_id, date))
    record = cursor.fetchone()

    if record:
        conn.execute("UPDATE attendance SET time_out = ? WHERE id = ?", (time_out, record['id']))
        conn.commit()
        flash("Employee marked out successfully!", "success")
    else:
        flash("No active 'mark in' record found for this employee today.", "error")
    
    conn.close()
    return redirect(url_for('index'))

@app.route('/records')
@admin_required
def records():
    conn = get_db_connection()
    attendance_records = []
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    employee_id_filter = request.args.get('employee_id_filter') # New filter

    print("--- Debugging /records route ---")
    print(f"DEBUG: Filters received: start_date={start_date}, end_date={end_date}, employee_id_filter={employee_id_filter}")

    query = """
        SELECT
            a.id,
            e.name,
            e.employee_id_text,
            a.date,
            a.time_in,
            a.time_out,
            a.location
        FROM
            attendance a
        JOIN
            employees e ON a.employee_id = e.id
        WHERE 1=1
    """
    params = []

    if start_date:
        query += " AND a.date >= ?"
        params.append(start_date)
    if end_date:
        query += " AND a.date <= ?"
        params.append(end_date)
    # Add filter for employee_id_filter (the internal employee ID)
    if employee_id_filter:
        try:
            employee_id_filter_int = int(employee_id_filter)
            query += " AND a.employee_id = ?"
            params.append(employee_id_filter_int)
        except ValueError:
            flash("Invalid Employee ID filter.", "danger")
            # You might want to handle this more gracefully, e.g., clear the filter
            employee_id_filter = None

    query += " ORDER BY a.date DESC, a.time_in DESC"

    print(f"DEBUG: SQL Query: {query}")
    print(f"DEBUG: Query Parameters: {params}")

    try:
        cursor = conn.execute(query, params)
        records = cursor.fetchall()

        print(f"DEBUG: Number of records fetched from DB: {len(records)}")
        if records:
            print(f"DEBUG: First record (if any): {dict(records[0])}") # Convert Row to dict for readable print
        else:
            print("DEBUG: No records fetched from DB.")

        for record in records:
            attendance_records.append({
                'id': record['id'],
                'name': record['name'],
                'employee_id_text': record['employee_id_text'],
                'date': record['date'],
                'time_in': record['time_in'],
                'time_out': record['time_out'],
                'location': record['location']
            })
    except sqlite3.Error as e:
        print(f"Database error in /records: {e}")
        flash(f"Database error: {e}", "danger")
    finally:
        conn.close()

    # Get all employees for the filter dropdown
    conn = get_db_connection()
    employees = conn.execute('SELECT id, name, employee_id_text FROM employees ORDER BY name').fetchall()
    conn.close()

    return render_template('records.html',
                           attendance_records=attendance_records,
                           start_date=start_date,
                           end_date=end_date,
                           employees=employees, # Pass employees to the template
                           selected_employee_id=employee_id_filter) # Pass the selected employee ID for persistence


@app.route('/admin_dashboard') # This route is for the employee list, not the summary
@admin_required
def admin_dashboard():
    conn = get_db_connection()
    employees = conn.execute("SELECT id, employee_id_text, name, department, job_title FROM employees ORDER BY name ASC").fetchall()
    conn.close()
    return render_template('admin_dashboard.html', employees=employees)


@app.route('/dashboard')
@admin_required
def dashboard():
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
 
        # Get current date
        today_date = datetime.now().strftime('%Y-%m-%d')
 
        # Total Employees
        cursor.execute("SELECT COUNT(*) FROM employees")
        total_employees = cursor.fetchone()[0] or 0
 
        # Employees IN Today (marked in today, not marked out yet)
        cursor.execute("""
            SELECT COUNT(DISTINCT E.id)
            FROM employees E
            JOIN attendance A ON E.id = A.employee_id
            WHERE A.date = ? AND (A.time_out IS NULL OR A.time_out = '')
        """, (today_date,))
        employees_in_today = cursor.fetchone()[0] or 0
 
        # Employees OUT Today (marked in AND out today)
        cursor.execute("""
            SELECT COUNT(DISTINCT E.id)
            FROM employees E
            JOIN attendance A ON E.id = A.employee_id
            WHERE A.date = ? AND A.time_out IS NOT NULL AND A.time_out != ''
        """, (today_date,))
        employees_out_today = cursor.fetchone()[0] or 0
 
        # Employees not marked today
        employees_not_marked_today = total_employees - (employees_in_today + employees_out_today)
        if employees_not_marked_today < 0:
            employees_not_marked_today = 0
 
        # Total attendance records (all time)
        cursor.execute("SELECT COUNT(*) FROM attendance")
        total_attendance_records = cursor.fetchone()[0] or 0
 
        # Onsite records count (for today)
        cursor.execute("""
            SELECT COUNT(*)
            FROM attendance
            WHERE date = ? AND location = 'Onsite'
        """, (today_date,))
        onsite_count = cursor.fetchone()[0] or 0
 
        # Remote records count (for today)
        cursor.execute("""
            SELECT COUNT(*)
            FROM attendance
            WHERE date = ? AND location = 'Remote'
        """, (today_date,))
        Remote_count = cursor.fetchone()[0] or 0
 
        # Recent Attendance Activities
        cursor.execute("""
            SELECT E.name, A.date, A.time_in, A.time_out, A.location
            FROM attendance A
            JOIN employees E ON A.employee_id = E.id
            ORDER BY A.date DESC, A.time_in DESC
            LIMIT 10
        """)
        recent_activities = cursor.fetchall()
 
        return render_template('dashboard.html',
                            current_date=today_date,
                            total_employees=total_employees,
                            employees_in_today=employees_in_today,
                            employees_out_today=employees_out_today,
                            employees_not_marked_today=employees_not_marked_today,
                            total_attendance_records=total_attendance_records,
                            onsite_count=onsite_count,
                            Remote_count=Remote_count,
                            recent_activities=recent_activities)
 
    except sqlite3.Error as e:
        print(f"SQLite error: {e}")
        flash(f"Database error occurred: {e}", "error")
        return redirect(url_for('admin_dashboard'))
    except Exception as e:
        print(f"General error: {e}")
        flash(f"An error occurred: {e}", "error")
        return redirect(url_for('admin_dashboard'))
    finally:
        if 'conn' in locals():
            conn.close()

@app.route('/add_employee', methods=['GET', 'POST'])
@admin_required
def add_employee():
    conn = get_db_connection()
    if request.method == 'POST':
        employee_id_text = request.form['employee_id_text'].strip() # Get unique ID
        name = request.form['name'].strip()
        department = request.form.get('department', '').strip()
        job_title = request.form.get('job_title', '').strip()

        if not employee_id_text or not name:
            flash("Employee ID and Name are required fields.", "error")
            conn.close()
            return redirect(url_for('add_employee'))

        try:
            conn.execute("INSERT INTO employees (employee_id_text, name, department, job_title) VALUES (?, ?, ?, ?)",
                         (employee_id_text, name, department, job_title))
            conn.commit()
            flash(f"Employee '{name}' (ID: {employee_id_text}) added successfully!", "success")
            return redirect(url_for('add_employee'))
        except sqlite3.IntegrityError:
            flash(f"Error: Employee ID '{employee_id_text}' already exists. Please use a unique ID.", "error")
            conn.rollback()
        except Exception as e:
            flash(f"An error occurred: {e}", "error")
            conn.rollback()
    
    # Fetch employees to display, including their unique ID
    employees = conn.execute("SELECT id, employee_id_text, name, department, job_title FROM employees ORDER BY name ASC").fetchall()
    conn.close()
    return render_template('add_employee.html', employees=employees)


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        user = request.form['username']
        pw = request.form['password']
        if user == 'admin' and pw == 'admin123':  # **IMPORTANT: Change these credentials!**
            session['admin'] = True
            flash("Logged in as Admin!", "success")
            return redirect(url_for('dashboard')) # <--- CORRECTED THIS LINE to redirect to the summary dashboard
        else:
            flash("Invalid credentials", "error")
            return render_template('login.html'), 401
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.pop('admin', None)
    flash("Logged out successfully.", "info")
    return redirect(url_for('index'))

@app.route('/export_csv')
@admin_required
def export_csv():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT E.employee_id_text, E.name, A.date, A.time_in, A.time_out, A.location
        FROM attendance A
        JOIN employees E ON A.employee_id = E.id
        ORDER BY A.date DESC, A.time_in DESC
    """)
    records = cursor.fetchall()
    conn.close()

    csv_data = "Employee ID,Employee Name,Date,Time In,Time Out,Location\n"
    for record in records:
        time_out = record['time_out'] if record['time_out'] else 'N/A'
        csv_data += f"{record['employee_id_text']},{record['name']},{record['date']},{record['time_in']},{time_out},{record['location']}\n"
    
    response = app.make_response(csv_data)
    response.headers["Content-Disposition"] = "attachment; filename=attendance_records.csv"
    response.headers["Content-type"] = "text/csv"
    return response

@app.route('/employee_report/<int:employee_id>')
@admin_required
def employee_report(employee_id):
    conn = get_db_connection()
    
    employee = conn.execute("SELECT id, employee_id_text, name, department, job_title FROM employees WHERE id = ?", (employee_id,)).fetchone()
    if not employee:
        flash("Employee not found.", "error")
        conn.close()
        return redirect(url_for('dashboard')) # Redirect to dashboard if employee not found

    attendance_records = []
    
    # Fetch all attendance records for the specific employee
    # Calculate duration in minutes for each record
    cursor = conn.cursor()
    cursor.execute(f"""
        SELECT date, time_in, time_out, location
        FROM attendance
        WHERE employee_id = ?
        ORDER BY date DESC, time_in DESC
    """, (employee_id,))
    
    raw_records = cursor.fetchall()

    for record in raw_records:
        duration_minutes = None
        if record['time_in'] and record['time_out']:
            try:
                # Convert time strings to datetime objects to calculate duration
                time_in_dt = datetime.strptime(record['time_in'], '%H:%M:%S')
                time_out_dt = datetime.strptime(record['time_out'], '%H:%M:%S')
                
                # Handle cases where time_out might be on the next day (e.g., worked past midnight)
                # For simplicity here, we assume within same day. If cross-day, need to adjust date.
                if time_out_dt < time_in_dt:
                    # This implies time_out is on the next day, add 24 hours to time_out_dt
                    time_out_dt += timedelta(days=1)
                
                duration = time_out_dt - time_in_dt
                duration_minutes = round(duration.total_seconds() / 60) # Convert to minutes
            except ValueError:
                # Handle cases where time format might be incorrect
                duration_minutes = None 
        
        # Append record with calculated duration
        attendance_records.append({
            'date': record['date'],
            'time_in': record['time_in'],
            'time_out': record['time_out'],
            'location': record['location'],
            'duration_minutes': duration_minutes
        })

    conn.close()

    return render_template('employee_report.html', employee=employee, attendance_records=attendance_records)


if __name__ == '__main__':
    app.run(debug=True)