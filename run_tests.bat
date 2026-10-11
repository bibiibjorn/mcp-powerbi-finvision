@echo off
REM Windows wrapper around run_tests.py — keeps the same args.
REM Usage: run_tests.bat [--fast|--verbose|--cov|--file NAME|--marker EXPR] [-- pytest-args...]
python "%~dp0run_tests.py" %*
exit /b %ERRORLEVEL%
