# Repository Guidelines

## Project Structure & Module Organization
- `app.py` centralizes Flask routes, MySQL utilities, and background workers; keep new logic modular and avoid inflating view functions.
- `templates/` (notably `templates/components/`) holds Jinja2 fragments; prefer reusing snippets instead of duplicating markup across pages.
- `static/` contains shared assets such as `in-app-screenshot.png`; store new CSS or JavaScript here and reference via `url_for("static", ...)`.
- `migrations/` and `backups/` are runtime artifacts created per environment; keep schema folders date-stamped to match the existing naming convention.

## Build, Test, and Development Commands
- `python3 -m venv .venv && source .venv/bin/activate` creates an isolated environment for dependencies.
- `pip install -r requirements.txt` installs Flask, mysql-connector-python, tqdm, and other runtime needs.
- `python app.py` launches the development server at `http://localhost:5000`; use this for manual UI verification.
- `MYSQL_PWD=$MYSQL_PASSWORD mysql -u $MYSQL_USER -e "SHOW DATABASES;"` is a quick connectivity sanity check before running migrations.

## Coding Style & Naming Conventions
- Follow PEP 8: 4-space indentation, snake_case for functions and variables, UPPERCASE constants such as `CONFIG_FILE`.
- Add targeted comments for non-obvious MySQL operations; prefer docstrings for helper functions.
- Keep templates readable with lowercase, hyphenated filenames and consistent block naming (`{% block content %}`).

## Testing Guidelines
- The project currently lacks automated tests; add `pytest`-based suites under a new `tests/` directory.
- Use Flask’s test client to exercise routes without touching a live database, and mock `mysql.connector` for connection scenarios.
- When migrations require integration coverage, spin up a disposable MySQL instance (e.g., Docker) and clean it via teardown fixtures.
- Run `pytest -q` locally before opening a pull request; aim for coverage on critical paths such as backup execution and config validation.

## Commit & Pull Request Guidelines
- Craft commits as short, imperative sentences (e.g., `Add migration pruning helper`); group unrelated changes into separate commits.
- Reference related issues in the body (`Refs #123`) and describe the user-visible impact plus any database side effects.
- Pull requests should include configuration assumptions, testing evidence (command output or screenshots), and rollback considerations for destructive actions.

## Configuration & Security Tips
- Never commit `config.json`; rely on `.gitignore` and document any local overrides in PR descriptions.
- Store credentials via environment variables or secret managers instead of hardcoding defaults.
- Verify migration folders and backup destinations exist to avoid permission errors in production deployments.
