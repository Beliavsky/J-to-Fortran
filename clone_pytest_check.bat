@echo off
setlocal

rem Clone this repository into a fresh temporary directory and run pytest there.
rem Usage:
rem   clone_pytest_check.bat
rem   clone_pytest_check.bat C:\temp\xj2f-clean-check

set "SRC=%~dp0"
set "SRC=%SRC:~0,-1%"

if "%~1"=="" (
    set "DEST=%TEMP%\xj2f_clean_clone"
) else (
    set "DEST=%~1"
)

echo Source: "%SRC%"
echo Clone:  "%DEST%"

where git >nul 2>nul
if errorlevel 1 (
    echo ERROR: git was not found on PATH.
    exit /b 1
)

where pytest >nul 2>nul
if errorlevel 1 (
    echo ERROR: pytest was not found on PATH.
    exit /b 1
)

if exist "%DEST%" (
    echo Removing existing clone directory...
    rmdir /s /q "%DEST%"
    if exist "%DEST%" (
        echo ERROR: could not remove "%DEST%".
        exit /b 1
    )
)

echo Cloning repository...
git clone "%SRC%" "%DEST%"
if errorlevel 1 exit /b 1

cd /d "%DEST%"
if errorlevel 1 exit /b 1

echo Running pytest in clean clone...
pytest -q
set "STATUS=%ERRORLEVEL%"

echo.
if "%STATUS%"=="0" (
    echo Clean clone pytest check passed.
) else (
    echo Clean clone pytest check failed with exit code %STATUS%.
)

exit /b %STATUS%
