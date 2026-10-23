Linux check for the native capture worker (test-only, not part of the Windows build).

native/src/capture.cpp needs DXGI and D3D11, which exist only on Windows. This folder
holds stand-in headers and a fake desktop (fake_win.cpp) so the real worker, the real
pybind11 bindings and the Python preview can run on Linux. The fake desktop updates at
60 Hz. Pixel (x, y) of desktop frame N is BGRA (x & 255, y & 255, N & 255, 255).

Build (from the repo root; needs pybind11 and the Python headers):
  mkdir -p build_fake
  g++ -std=c++17 -O2 -shared -fPIC -Inative/tests/fake_dxgi -Inative/include \
      -I$(python3 -c "import pybind11;print(pybind11.get_include())") \
      -I$(python3 -c "import sysconfig;print(sysconfig.get_paths()['include'])") \
      native/src/bindings.cpp native/src/capture.cpp native/src/movement_analyzer.cpp \
      native/tests/fake_dxgi/fake_win.cpp \
      -o build_fake/fpscoach_native.so
Run:
  PYTHONPATH=build_fake python3 native/tests/fake_dxgi/smoke_capture.py
  PYTHONPATH=build_fake python3 native/tests/fake_dxgi/stress_capture.py

What this proves: the threading, buffer reuse, shared-frame publishing, sequence numbers and
row-pitch handling are internally consistent. What it does NOT prove: anything about real
Desktop Duplication timing, GPU behaviour or Windows drivers. Those need a Windows build.
