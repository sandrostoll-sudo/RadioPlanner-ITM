@echo off
setlocal
cd /d "%~dp0"
where cl >nul 2>nul
if errorlevel 1 (
  echo FEHLER: MSVC cl.exe nicht gefunden.
  echo Bitte "x64 Native Tools Command Prompt for VS 2022" verwenden.
  exit /b 1
)
if /i not "%VSCMD_ARG_TGT_ARCH%"=="x64" (
  echo FEHLER: Bitte x64 Native Tools Command Prompt verwenden.
  exit /b 1
)
cl /nologo /std:c++17 /EHsc /O2 /LD /I ntia_src\include itm_bridge.cpp ntia_src\src\*.cpp /link /OUT:rp_itm.dll
if errorlevel 1 exit /b 1
if not exist rp_itm.dll exit /b 1
cd ..
py -3 verify_itm_engine.py native_itm\rp_itm.dll
if errorlevel 1 (
  echo FEHLER: DLL erstellt, aber ITM-Test fehlgeschlagen.
  exit /b 1
)
echo ERFOLG: native_itm\rp_itm.dll wurde erstellt und getestet.
exit /b 0
