# CodeMind OS setup for Windows PowerShell. Creates a venv and installs Python dependencies.
# It does NOT download models, run repository code, or overwrite existing files.
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")
$py = if ($env:PYTHON) { $env:PYTHON } else { "python" }
& $py -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)"
if ($LASTEXITCODE -ne 0) { Write-Error "Python 3.11+ is required (install from https://www.python.org/downloads/)."; exit 1 }
if (-not (Test-Path .venv)) { & $py -m venv .venv }
& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\pip.exe install -r requirements-dev.txt
if (-not (Test-Path .env)) { Copy-Item .env.example .env; Write-Host "Created .env from .env.example" }
& .\.venv\Scripts\python.exe scripts\check_environment.py
Write-Host @"

Next steps (nothing large is downloaded automatically):
  1. Install Ollama from https://ollama.com, then (your choice) run:  ollama pull qwen2.5-coder:7b
  2. Optional offline embeddings:  .\.venv\Scripts\python.exe scripts\check_environment.py --download-embedding-model
  3. Start CodeMind:   .\.venv\Scripts\python.exe main.py serve
  4. Open the web UI:  http://127.0.0.1:8000/
"@
