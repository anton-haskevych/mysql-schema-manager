import os
import json
import zipfile
import datetime
import shutil
import math
import subprocess
import tempfile
import re
from flask import Flask, render_template, request, redirect, url_for, flash
import mysql.connector
from werkzeug.utils import secure_filename
from markupsafe import Markup
import os
import subprocess
import concurrent.futures

from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm

app = Flask(__name__)
app.secret_key = "your_secret_key"  # Replace with a strong secret key

CONFIG_FILE = "config.json"


def is_config_valid():
    # A simple validity check: all required config values are non-empty.
    return bool(
        config.get("mysql_username")
        and config.get("mysql_password")
        and config.get("migration_folder")
    )


def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r") as f:
            try:
                config = json.load(f)
            except json.JSONDecodeError:
                config = {}
    else:
        config = {}
    # Set defaults if not present
    config.setdefault("mysql_username", "")
    config.setdefault("mysql_password", "")
    config.setdefault("host", "localhost")
    config.setdefault("port", "3306")
    config.setdefault("migration_folder", os.path.join(os.getcwd(), "migrations"))
    config.setdefault("backup_folder", os.path.join(os.getcwd(), "backups"))
    return config


def save_config(config):
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=4)


# Global configuration loaded on startup
config = load_config()
os.makedirs(config["migration_folder"], exist_ok=True)
os.makedirs(config["backup_folder"], exist_ok=True)


def get_mysql_connection():
    try:
        conn = mysql.connector.connect(
            host=config["host"],
            port=config["port"],
            user=config["mysql_username"],
            password=config["mysql_password"],
        )
        return conn
    except mysql.connector.Error as err:
        flash(f"MySQL connection error: {err}", "danger")
        return None


def drop_all_schemas():
    """
    WARNING: This function drops every non-system database.
    Use with extreme caution!
    """
    conn = get_mysql_connection()
    if not conn:
        return False
    try:
        cursor = conn.cursor()
        cursor.execute("SHOW DATABASES")
        databases = cursor.fetchall()
        for (db,) in databases:
            if db in ("information_schema", "mysql", "performance_schema", "sys"):
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


def list_sql_files(directory):
    """List all .sql files in a directory."""
    return [
        os.path.join(directory, f)
        for f in os.listdir(directory)
        if os.path.isfile(os.path.join(directory, f)) and f.endswith(".sql")
    ]


def check_and_remove_line(file_path, line_to_remove):
    """Remove a specific line from a file, if present."""
    with open(file_path, "rb") as file:
        lines = file.readlines()
    with open(file_path, "wb") as file:
        for line in lines:
            if line.strip() != line_to_remove.encode("utf-8"):
                file.write(line)


def set_global_time_zone(time_zone):
    """Set MySQL global time zone using your credentials."""
    env = os.environ.copy()
    env["MYSQL_PWD"] = config["mysql_password"]
    cmd = [
        "mysql",
        "-u",
        config["mysql_username"],
        "-e",
        f"SET GLOBAL time_zone = '{time_zone}';",
    ]
    subprocess.check_call(cmd, env=env)


def apply_migration_version(version_name: str) -> bool:
    migration_path = os.path.join(config["migration_folder"], version_name)
    if not os.path.isdir(migration_path):
        flash("Migration version not found.", "danger")
        return False

    # Drop all non-system schemas first (warning: destructive!)
    if not drop_all_schemas():
        return False

    env = os.environ.copy()
    env["MYSQL_PWD"] = config["mysql_password"]

    # Gather migration tasks from all .sql files in the directory tree.
    migration_tasks = []
    for root, _, files in os.walk(migration_path):
        for file in sorted(files):
            if not file.endswith(".sql"):
                print(f"Not ends with .sql: {file}")
                continue
            db_name = os.path.splitext(file)[0]
            file_path = os.path.join(root, file)
            migration_tasks.append((db_name, file_path))

    def apply_migration_file(db_name: str, file_path: str) -> bool:
        create_db_command = f"mysql -u {config['mysql_username']} -e 'CREATE DATABASE IF NOT EXISTS {db_name}'"
        apply_command = f"mysql -u {config['mysql_username']} {db_name} < {file_path}"
        try:
            subprocess.check_call(create_db_command, shell=True, env=env)
            subprocess.check_call(apply_command, shell=True, env=env)
            return True
        except subprocess.CalledProcessError as e:
            flash(f"Error applying snapshot to {db_name}: {e}", "danger")
            return False

    # Run migrations concurrently.
    with concurrent.futures.ThreadPoolExecutor() as executor:
        futures = [
            executor.submit(apply_migration_file, db_name, file_path)
            for db_name, file_path in migration_tasks
        ]

        for future in concurrent.futures.as_completed(futures):
            if not future.result():
                return False

    flash("Migration applied successfully.", "success")
    return True


def list_migration_versions():
    migration_versions = []
    folder = config["migration_folder"]
    if os.path.isdir(folder):
        for item in os.listdir(folder):
            path = os.path.join(folder, item)
            if os.path.isdir(path):
                creation_time = os.path.getctime(path)
                migration_versions.append(
                    {
                        "name": item,
                        "created": datetime.datetime.fromtimestamp(creation_time),
                    }
                )
    migration_versions.sort(key=lambda x: x["created"], reverse=True)
    return migration_versions


# -----------------------------
# Flask Routes
# -----------------------------


@app.route("/", methods=["GET"])
def dashboard():
    migration_versions = list_migration_versions()
    page = request.args.get("page", 1, type=int)
    per_page = 5
    total = len(migration_versions)
    total_pages = math.ceil(total / per_page)
    start = (page - 1) * per_page
    end = start + per_page
    paginated_migrations = migration_versions[start:end]

    valid_config = is_config_valid()

    return render_template(
        "dashboard.html",
        config=config,
        migrations=paginated_migrations,
        page=page,
        total_pages=total_pages,
        valid_config=valid_config,
    )


@app.route("/update_config", methods=["POST"])
def update_config():
    global config
    mysql_username = request.form.get("mysql_username", "").strip()
    mysql_password = request.form.get("mysql_password", "").strip()
    migration_folder = request.form.get("migration_folder", "").strip()
    if not migration_folder:
        migration_folder = os.path.join(os.getcwd(), "migrations")

    # Attempt to connect to the MySQL database using the provided credentials.
    try:
        conn = mysql.connector.connect(
            host="localhost", user=mysql_username, password=mysql_password
        )
        conn.close()
    except mysql.connector.Error as err:
        flash(f"Error connecting to MySQL with provided credentials: {err}", "danger")
        return redirect(url_for("dashboard"))

    # If connection succeeds, update configuration.
    config["mysql_username"] = mysql_username
    config["mysql_password"] = mysql_password
    config["migration_folder"] = migration_folder
    os.makedirs(migration_folder, exist_ok=True)
    save_config(config)
    flash("Configuration updated.", "success")
    return redirect(url_for("dashboard"))


@app.route("/drop_schemas", methods=["POST"])
def drop_schemas():
    if drop_all_schemas():
        flash("All schemas dropped successfully.", "success")
    else:
        flash("Failed to drop schemas.", "danger")
    return redirect(url_for("dashboard"))


@app.route("/apply_migration", methods=["POST"])
def apply_migration():
    if not is_config_valid():
        flash(
            "Configuration is missing or invalid. Please update your configuration first.",
            "warning",
        )
        return redirect(url_for("dashboard"))
    version = request.form.get("version")
    if version:
        if apply_migration_version(version):
            flash(f"Migration version '{version}' applied.", "success")
        else:
            flash("Failed to apply migration.", "danger")
    else:
        flash("No migration version selected.", "warning")
    return redirect(url_for("dashboard"))


@app.route("/upload_migration", methods=["POST"])
def upload_migration():
    upload_folder = request.form.get("upload_folder", "").strip()
    if not upload_folder:
        flash("Please enter a valid migration folder path.", "warning")
        return redirect(url_for("dashboard"))
    os.makedirs(upload_folder, exist_ok=True)
    version_name = "migration_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    version_path = os.path.join(upload_folder, version_name)
    os.makedirs(version_path, exist_ok=True)
    if "migration_files" not in request.files:
        flash("No file part in the request.", "warning")
        return redirect(url_for("dashboard"))
    uploaded_file = request.files["migration_files"]
    filename = secure_filename(uploaded_file.filename)
    if filename == "":
        flash("No selected file.", "warning")
        return redirect(url_for("dashboard"))
    if filename.endswith(".zip"):
        zip_path = os.path.join(version_path, filename)
        uploaded_file.save(zip_path)
        with zipfile.ZipFile(zip_path, "r") as zip_ref:
            zip_ref.extractall(version_path)
        os.remove(zip_path)
        flash("Zip migration uploaded and extracted.", "success")
    else:
        file_path = os.path.join(version_path, filename)
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        uploaded_file.save(file_path)
        flash("Migration file uploaded.", "success")
    return redirect(url_for("dashboard"))


@app.route("/delete_migration", methods=["POST"])
def delete_migration():
    version = request.form.get("version")
    if version:
        version_path = os.path.join(config["migration_folder"], version)
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
    return redirect(url_for("dashboard"))


@app.route("/dump_database", methods=["GET", "POST"])
def dump_database():
    if request.method == "POST":
        host = request.form.get("host", "localhost").strip()
        dump_username = request.form.get("dump_username", "").strip()
        dump_password = request.form.get("dump_password", "").strip()
        backup_folder = request.form.get("backup_folder", "").strip()
        if not backup_folder:
            backup_folder = config["backup_folder"]
        os.makedirs(backup_folder, exist_ok=True)
        timestamp_folder = datetime.datetime.now().strftime("%m_%d_%Y_%I_%M_%p")
        full_backup_path = os.path.join(backup_folder, timestamp_folder)
        os.makedirs(full_backup_path, exist_ok=True)
        config_contents = "[client]\n"
        config_contents += f"user={dump_username}\n"
        config_contents += f"password={dump_password}\n"
        config_contents += f"host={host}\n"
        with tempfile.NamedTemporaryFile(mode="w", delete=False) as tf:
            tf.write(config_contents)
            mysql_config_file = tf.name
        try:
            conn = mysql.connector.connect(
                host=host, user=dump_username, password=dump_password
            )
            cursor = conn.cursor()
            cursor.execute("SHOW DATABASES")
            databases = [db[0] for db in cursor.fetchall()]
            cursor.close()
            conn.close()
        except mysql.connector.Error as err:
            flash(f"Error connecting to MySQL: {err}", "danger")
            os.remove(mysql_config_file)
            return redirect(url_for("dump_database"))
        exclude = ["information_schema", "mysql", "sys", "performance_schema"]
        databases = [db for db in databases if db not in exclude]
        dumped_files = []
        env = os.environ.copy()
        env["MYSQL_PWD"] = dump_password
        for db in databases:
            backup_file = os.path.join(full_backup_path, f"{db}.sql")
            cmd = [
                "mysqldump",
                f"--defaults-extra-file={mysql_config_file}",
                "--single-transaction",
                "--set-gtid-purged=OFF",
                db,
            ]
            with open(backup_file, "w") as f:
                result = subprocess.run(cmd, stdout=f, stderr=subprocess.PIPE, env=env)
            if result.returncode == 0:
                dumped_files.append(backup_file)
            else:
                flash(
                    f"Error dumping database {db}: {result.stderr.decode()}", "danger"
                )
        os.remove(mysql_config_file)
        if dumped_files:
            flash(
                f"Dump completed. # of backed up databases: {len(dumped_files)}. Everything saved in folder: {full_backup_path}",
                "success",
            )
        else:
            flash("No databases were backed up.", "warning")
        return redirect(url_for("dashboard"))
    return render_template("dump.html", cwd=os.getcwd(), config=config)


from flask import jsonify


@app.route("/confirm_apply_migration/<version>", methods=["GET", "POST"])
def confirm_apply_migration(version):
    migration_path = os.path.join(config["migration_folder"], version)
    if not os.path.isdir(migration_path):
        flash("Migration version not found.", "danger")
        return redirect(url_for("dashboard"))

    if request.method == "GET":
        # List all SQL files in the migration folder.
        sql_files = []
        for root, dirs, files in os.walk(migration_path):
            for file in sorted(files):
                if file.endswith(".sql"):
                    sql_files.append(file)
        return render_template(
            "confirm_apply_migration.html", version=version, sql_files=sql_files
        )

    # POST: apply the migration.
    success = apply_migration_version(version)
    if success:
        # Return JSON response for AJAX.
        return jsonify({"success": True})
    else:
        return jsonify({"success": False, "message": "Error applying migration."}), 500


@app.route("/readme")
def readme():
    try:
        with open("README.md", "r", encoding="utf-8") as f:
            content = f.read()
    except Exception as e:
        content = f"Error reading README.md: {e}"
    # Render the content inside a preformatted block
    return render_template("readme.html", content=Markup(f"<pre>{content}</pre>"))


if __name__ == "__main__":
    app.run(debug=True)
