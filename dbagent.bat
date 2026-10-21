@echo off
setlocal EnableExtensions
chcp 65001 >nul 2>nul
title DBAgent

rem ===================================================================
rem  DBAgent launcher - Windows
rem  location: dbagent\agent.bat     entry: dbagent\package\main.py
rem
rem  Behaves the same as run.sh, which is the reference version:
rem    same argument set and entry point
rem    locate interpreter then print its version
rem    force UTF-8 environment
rem    print exit code when non-zero
rem    wait for Enter when launched with no arguments
rem
rem  usage:
rem    agent.bat                  -> package\main.py  menu / chat
rem    agent.bat --setup          -> configure API Key
rem    agent.bat plan  "req"      -> plan only, no code change
rem    agent.bat apply "req"      -> plan + generate edits, apply after confirm
rem    agent.bat chat             -> AI chat dev mode
rem    agent.bat project-path     -> analyze structure, report + mindmap
rem         extras: --no-mind  --no-open  --out dir
rem
rem  API Key: env var AGENT_API_KEY, or run agent.bat --setup first
rem ===================================================================

set "ROOT=%~dp0"
cd /d "%ROOT%"

set "ENTRY=package\main.py"
set "FORWARD=%*"

rem ---- locate python: prefer py -3, then python/python3 ----
set "PYCMD="
for %%C in (py python python3) do (
    if not defined PYCMD (
        where %%C >nul 2>nul
        if not errorlevel 1 set "PYCMD=%%C"
    )
)
if /I "%PYCMD%"=="py" set "PYCMD=py -3"
if not defined PYCMD (
    if exist "%LOCALAPPDATA%\Programs\Python\Python310\python.exe" (
        set PYCMD=%LOCALAPPDATA%\Programs\Python\Python310\python.exe
    )
)

if not defined PYCMD (
    echo.
    echo [ERROR] Python not found.
    echo         Install Python 3.10+ with "Add to PATH",
    echo         or set PYCMD in this script to the full python.exe path.
    echo.
    pause
    exit /b 9009
)

rem ---- force UTF-8 so Chinese output is not garbled ----
set "PYTHONUTF8=1"
if not defined PYTHONIOENCODING set "PYTHONIOENCODING=utf-8"

rem ---- interpreter version, mirrors PYCMD -V in run.sh ----
set "PYVER="
for /f "delims=" %%V in ('%PYCMD% -V 2^>^&1') do set "PYVER=%%V"

echo.
echo ============================================================
echo   DBAgent
echo   entry : %ENTRY%
echo   python: %PYCMD% ^(%PYVER%^)
echo   cwd   : %CD%
echo ============================================================
echo.

%PYCMD% "%ENTRY%" %FORWARD%
set "CODE=%ERRORLEVEL%"

if not "%CODE%"=="0" (
    echo.
    echo [exit code %CODE%]
)

rem ---- wait for Enter when launched with no arguments ----
if "%~1"=="" (
    echo.
    pause
)

exit /b %CODE%
