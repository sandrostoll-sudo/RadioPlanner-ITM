# Original NTIA-ITM integration

Source: https://github.com/NTIA/itm, user-supplied `itm-master.zip` (archive commit 183ad95bd813a8be11009df396e1c631356864b2, dated 2024-09-24).
The original NTIA C++ algorithms are included under `ntia_src/` (Linux include-path syntax adjusted); see `NTIA_LICENSE.md`.
`itm_bridge.cpp` is a RadioPlanner adapter, added 2026-11-02.

## Windows build (MSVC x64 Developer Command Prompt)

From this folder:

```
cl /nologo /std:c++17 /EHsc /O2 /LD /I ntia_src\include itm_bridge.cpp ntia_src\src\*.cpp /Fe:rp_itm.dll
```

Place the resulting `rp_itm.dll` alongside the application and test with
`python verify_itm_engine.py native_itm/rp_itm.dll`.
This package includes a Linux `.so` for integration testing, **not** a Windows DLL.

Note: ITM model's point-to-point variability (`mdvar=1`) and TLS settings must be checked against Radio Mobile before treating maps as equivalent.

## Linux verification

The upstream NTIA `.cpp` files have been modified **only** to convert Windows-style
`..\\include\\` include paths to POSIX `../include/` paths for Linux compilation.
The propagation algorithms are unchanged. Windows can compile the adjusted include paths.

```
g++ -std=c++17 -O2 -fPIC -shared '-D__declspec(x)=' -I ntia_src/include itm_bridge.cpp ntia_src/src/*.cpp -o librp_itm.so
cd ..
python verify_itm_engine.py native_itm/librp_itm.so
```

Verified Linux outputs (80 MHz, default Radio Mobile settings):
flat_1km 95.5485 dB, flat_10km 134.4783 dB, ridge_10km 139.5127 dB.
These are smoke tests, not independent NTIA reference validation.


## Automatischer Windows-Build (Entwicklungsstand 9)

Die Datei `.github/workflows/build-itm-windows.yml` kompiliert die DLL auf
GitHub Actions Windows 2022 mit MSVC x64, testet die drei echten ITM-Strecken
und stellt nur bei Erfolg das Artefakt `RadioPlanner-ITM-Windows-x64` bereit.
Dazu muss der Quellcode in einem GitHub-Repository liegen und der Workflow
manuell unter Actions gestartet werden. Alternativ auf Windows die Datei
`native_itm/build_windows.cmd` in einer **x64 Native Tools Command Prompt**
ausführen (Visual Studio 2022 C++ Build Tools und Python 3.12 erforderlich).
Die DLL gehört nach `native_itm/rp_itm.dll` und wird dann automatisch erkannt.

Wichtig: Der Windows-Build wurde in dieser Linux-Umgebung nicht ausgeführt.
Die Linux-Engine ist funktionsgeprüft, Windows muss noch separat verifiziert werden.
