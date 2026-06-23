@echo off
REM deFrost build script — produces a portable one-directory bundle
REM Run from the project root: E:\projects\deFrost\
REM Requires PyInstaller: pip install pyinstaller
REM
REM NOTE: ImDisk is NOT bundled. On first activation deFrost detects whether
REM the ImDisk driver is installed and installs it automatically (requires
REM the elevation that deFrost already runs with). Users do not need to
REM install ImDisk manually.

echo [deFrost] Building portable bundle...

pyinstaller ^
  --onedir ^
  --windowed ^
  --name deFrost ^
  --uac-admin ^
  --add-data "src\templates;templates" ^
  --add-data "src\static;static" ^
  --paths src ^
  src\tray.py

echo.
if %ERRORLEVEL% EQU 0 (
  echo [deFrost] Build complete.
  echo           Output: dist\deFrost\
  echo           Copy the dist\deFrost folder anywhere and run deFrost.exe
  echo           On first activation: UAC prompt will appear once.
  echo           ImDisk driver will be installed automatically if not present.
) else (
  echo [deFrost] Build FAILED. Check output above.
)
pause
