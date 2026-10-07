# kitcher
a simple app to recommend me what to buy at the grocery store based on what i have in my fridge/pantry and recipes that i like.

## Local development (Windows / VS Code)

The repository is ready for Python development. App code and runtime dependencies
have not been added yet. Use Python 3.12 and the project-local `.venv` environment.

To recreate the environment from a fresh clone, run in PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
code .
```

VS Code is configured to use `.venv` and format Python files with Ruff on save.
The recommended extensions provide Python support, debugging, and Ruff.
If VS Code asks for an interpreter, select `.venv\Scripts\python.exe`.

Run these commands from the project folder; activation is optional:

```powershell
# Lint
.\.venv\Scripts\python.exe -m ruff check .

# Format
.\.venv\Scripts\python.exe -m ruff format .

# Run tests after adding test_*.py files
.\.venv\Scripts\python.exe -m pytest
```

These commands are also available under **Terminal > Run Task**. To debug a Python
file, open it and press **F5**, then choose **Python: Current File**.

Optional environment activation in PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
```

There are no tests or app entry point yet. Until tests are added, pytest reports
"no tests ran" and exits with code 5.
