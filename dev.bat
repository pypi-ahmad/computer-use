@echo off
setlocal
uv run --frozen python "%~dp0dev.py" %*
endlocal
