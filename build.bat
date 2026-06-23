@echo off
REM deFrost build script — produces a portable one-directory bundle
REM Run from the project root: E:\projects\deFrost\
REM Requires PyInstaller: pip install pyinstaller

echo [deFrost] Building portable bundle...

pyinstaller ^
  --onedir ^
  --windowed ^
  --name deFrost ^
  --uac-admin ^
  --add-data "src\templates;templates" ^
  --add-data "src\static;static" ^
  --add-binary "assets\imdisk\imdisk.exe;assets\imdisk" ^
  --add-binary "assets\imdisk\imdisk.sys;assets\imdisk" ^
  --paths src ^
  src\tray.py

echo.
if %ERRORLEVEL% EQU 0 (
  echo [deFrost] Build complete.
  echo           Output: dist\deFrost\
  echo           Copy the dist\deFrost folder anywhere and run deFrost.exe
  echo           On first run on a new machine: UAC prompt will appear once.
  echo           ImDisk driver will be auto-installed if not already present.
) else (
  echo [deFrost] Build FAILED. Check output above.
)
pause
