import os
import json
import zipfile
import datetime
import shutil
import math
import subprocess
import tempfile
from flask import Flask, render_template, request, redirect, url_for, flash
import mysql.connector
from mysql.connector import errorcode
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = 'your_secret_key'  # Replace with a strong secret key

CONFIG_FILE = 'config.json'


def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r') as f:
            try:
                config = json.load(f)
            except json.JSONDecodeError:
                config = {}
    else:
        config = {}
    # Set defaults if not present
    config.setdefault('mysql_username', '')
    config.setdefault('mysql_password', '')
    config.setdefault('migration_folder', os.path.join(os.getcwd(), 'migrations'))
    return config


def save_config(config):
    with open(CONFIG_FILE, 'w') as f:
        json.dump(config, f, indent=4)


# Global configuration loaded on startup
config = load_config()
os.makedirs(config['migration_folder'], exist_ok=True)


def get_mysql_connection():
    try:
        conn = mysql.connector.connect(
            host='localhost',
            user=config['mysql_username'],
            password=config['mysql_password']
        )
        return conn
    except mysql.connector.Error as err:
        flash(f"MySQL connection error: {err}", "danger")
        return None


def drop_all_schemas():
    conn = get_mysql_connection()
    if not conn:
        return False
    try:
        cursor = conn.cursor()
        cursor.execute("SHOW DATABASES")
        databases = cursor.fetchall()
        for (db,) in databases:
            # Skip system databases
            if db in ('information_schema', 'mysql', 'performance_schema', 'sys'):
                continue
            drop_stmt = f"DROP DATABASE `{db}`"
            cursor.execute(drop_stmt)
        conn.commit()
        cursor.close()
        conn.close()
        return True
    except mysql.connector.Error as err:
        flash(f"Error dropping schemas: {err}", "danger")
        return False


def apply_migration_version(version_name):
    migration_path = os.path.join(config['migration_folder'], version_name)
    if not os.path.isdir(migration_path):
        flash("Migration version not found.", "danger")
        return False
    # First, drop all schemas
    if not drop_all_schemas():
        return False
    conn = get_mysql_connection()
    if not conn:
        return False
    try:
        cursor = conn.cursor()
        # Walk through the migration directory and execute all .sql files
        for root, dirs, files in os.walk(migration_path):
            files.sort()  # Execute in a consistent order
            for file in files:
                if file.endswith('.sql'):
                    file_path = os.path.join(root, file)
                    with open(file_path, 'r', encoding='utf-8') as f:
                        sql_commands = f.read()
                        # Simple split by semicolon; for complex scripts consider a proper parser
                        for command in sql_commands.split(';'):
                            command = command.strip()
                            if command:
                                cursor.execute(command)
        conn.commit()
        cursor.close()
        conn.close()
        flash("Migration applied successfully.", "success")
        return True
    except mysql.connector.Error as err:
        flash(f"Error applying migration: {err}", "danger")
        return False


def list_migration_versions():
    migration_versions = []
    folder = config['migration_folder']
    if os.path.isdir(folder):
        for item in os.listdir(folder):
            path = os.path.join(folder, item)
            if os.path.isdir(path):
                creation_time = os.path.getctime(path)
                migration_versions.append({
                    'name': item,
                    'created': datetime.datetime.fromtimestamp(creation_time)
                })
    # Sort by creation date descending (newest first)
    migration_versions.sort(key=lambda x: x['created'], reverse=True)
    return migration_versions


@app.route('/', methods=['GET'])
def dashboard():
    migration_versions = list_migration_versions()
    # Pagination parameters: 5 migration versions per page
    page = request.args.get('page', 1, type=int)
    per_page = 5
    total = len(migration_versions)
    total_pages = math.ceil(total / per_page)
    start = (page - 1) * per_page
    end = start + per_page
    paginated_migrations = migration_versions[start:end]
    return render_template('dashboard.html',
                           config=config,
                           migrations=paginated_migrations,
                           page=page,
                           total_pages=total_pages)


@app.route('/update_config', methods=['POST'])
def update_config():
    global config
    mysql_username = request.form.get('mysql_username', '').strip()
    mysql_password = request.form.get('mysql_password', '').strip()
    migration_folder = request.form.get('migration_folder', '').strip()
    if not migration_folder:
        migration_folder = os.path.join(os.getcwd(), 'migrations')
    config['mysql_username'] = mysql_username
    config['mysql_password'] = mysql_password
    config['migration_folder'] = migration_folder
    os.makedirs(migration_folder, exist_ok=True)
    save_config(config)
    flash("Configuration updated.", "success")
    return redirect(url_for('dashboard'))


@app.route('/drop_schemas', methods=['POST'])
def drop_schemas():
    if drop_all_schemas():
        flash("All schemas dropped successfully.", "success")
    else:
        flash("Failed to drop schemas.", "danger")
    return redirect(url_for('dashboard'))


@app.route('/apply_migration', methods=['POST'])
def apply_migration():
    version = request.form.get('version')
    if version:
        if apply_migration_version(version):
            flash(f"Migration version '{version}' applied.", "success")
        else:
            flash("Failed to apply migration.", "danger")
    else:
        flash("No migration version selected.", "warning")
    return redirect(url_for('dashboard'))


@app.route('/upload_migration', methods=['POST'])
def upload_migration():
    # Use the folder provided by the user in this upload form
    upload_folder = request.form.get('upload_folder', '').strip()
    if not upload_folder:
        flash("Please enter a valid migration folder path.", "warning")
        return redirect(url_for('dashboard'))
    # Ensure the folder exists
    os.makedirs(upload_folder, exist_ok=True)

    # Auto-generate a new migration version name using the current timestamp
    version_name = "migration_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    version_path = os.path.join(upload_folder, version_name)
    os.makedirs(version_path, exist_ok=True)

    if 'migration_files' not in request.files:
        flash("No file part in the request.", "warning")
        return redirect(url_for('dashboard'))

    uploaded_file = request.files['migration_files']
    filename = secure_filename(uploaded_file.filename)
    if filename == '':
        flash("No selected file.", "warning")
        return redirect(url_for('dashboard'))

    if filename.endswith('.zip'):
        zip_path = os.path.join(version_path, filename)
        uploaded_file.save(zip_path)
        # Extract zip contents into version_path
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(version_path)
        os.remove(zip_path)
        flash("Zip migration uploaded and extracted.", "success")
    else:
        # Handle folder upload (when using webkitdirectory, browsers include relative paths)
        file_path = os.path.join(version_path, filename)
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        uploaded_file.save(file_path)
        flash("Migration file uploaded.", "success")

    return redirect(url_for('dashboard'))


@app.route('/delete_migration', methods=['POST'])
def delete_migration():
    version = request.form.get('version')
    if version:
        version_path = os.path.join(config['migration_folder'], version)
        if os.path.isdir(version_path):
            try:
                shutil.rmtree(version_path)
                flash(f"Migration version '{version}' deleted.", "success")
            except Exception as e:
                flash(f"Error deleting migration version: {e}", "danger")
        else:
            flash("Migration version not found.", "warning")
    else:
        flash("No migration version specified.", "warning")
    return redirect(url_for('dashboard'))


# New Dump Database route and handler
@app.route('/dump_database', methods=['GET', 'POST'])
def dump_database():
    if request.method == 'POST':
        # Retrieve dump parameters from form
        host = request.form.get('host', 'localhost').strip()
        dump_username = request.form.get('dump_username', '').strip()
        dump_password = request.form.get('dump_password', '').strip()
        backup_folder = request.form.get('backup_folder', '').strip()
        if not backup_folder:
            backup_folder = os.path.join(os.getcwd(), 'migrations')
        os.makedirs(backup_folder, exist_ok=True)

        # Create a subfolder with the timestamp in the format mm_dd_yyyy_HH_mm_AM/PM
        timestamp_folder = datetime.datetime.now().strftime("%m_%d_%Y_%I_%M_%p")
        full_backup_path = os.path.join(backup_folder, timestamp_folder)
        os.makedirs(full_backup_path, exist_ok=True)

        # Create a temporary MySQL config file for mysqldump
        config_contents = "[client]\n"
        config_contents += f"user={dump_username}\n"
        config_contents += f"password={dump_password}\n"
        config_contents += f"host={host}\n"
        with tempfile.NamedTemporaryFile(mode='w', delete=False) as tf:
            tf.write(config_contents)
            mysql_config_file = tf.name

        # Connect to MySQL to list available databases
        try:
            conn = mysql.connector.connect(
                host=host,
                user=dump_username,
                password=dump_password
            )
            cursor = conn.cursor()
            cursor.execute("SHOW DATABASES")
            databases = [db[0] for db in cursor.fetchall()]
            cursor.close()
            conn.close()
        except mysql.connector.Error as err:
            flash(f"Error connecting to MySQL: {err}", "danger")
            os.remove(mysql_config_file)
            return redirect(url_for('dump_database'))

        # Exclude system databases
        exclude = ['information_schema', 'mysql', 'sys', 'performance_schema']
        databases = [db for db in databases if db not in exclude]

        dumped_files = []
        # Dump each database using mysqldump with specified parameters
        for db in databases:
            file_timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_file = os.path.join(full_backup_path, f"{db}_{file_timestamp}.sql")
            cmd = [
                "mysqldump",
                f"--defaults-extra-file={mysql_config_file}",
                "--single-transaction",
                "--set-gtid-purged=OFF",
                db
            ]
            with open(backup_file, 'w') as f:
                result = subprocess.run(cmd, stdout=f, stderr=subprocess.PIPE)
            if result.returncode == 0:
                dumped_files.append(backup_file)
            else:
                flash(f"Error dumping database {db}: {result.stderr.decode()}", "danger")
        os.remove(mysql_config_file)
        if dumped_files:
            flash(f"Dump completed. # of backed up databases: {len(dumped_files)}. Everything saved in folder: {full_backup_path}", "success")
        else:
            flash("No databases were backed up.", "warning")
        return redirect(url_for('dashboard'))
    return render_template('dump.html', cwd=os.getcwd(), config=config)


if __name__ == '__main__':
    app.run(debug=True)
