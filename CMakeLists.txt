cmake_minimum_required(VERSION 3.23)

# No LANGUAGES here on purpose: enabling CXX at project() time makes CMake look
# for a compiler during configure, which would defeat the compiler-free install
# below. CXX is enabled in the branch that actually builds something.
project(FPSCoach VERSION 0.1.0 LANGUAGES NONE)

# Optional pure-Python install: with FPSCOACH_SKIP_NATIVE=1 the C++ core is not
# built at all, so `pip install -e ".[recorder]"` needs no compiler, SDK, or
# CMake toolchain beyond the CMake wheel pip already provides. The app then runs
# on the portable Python fallback analyzer (fps_coach.backend imports
# fpscoach_native defensively) and the Capture page says the native core is
# unavailable.
#
#   PowerShell:  $env:FPSCOACH_SKIP_NATIVE = "1"; pip install -e ".[dev,recorder]"
#   cmd.exe:     set FPSCOACH_SKIP_NATIVE=1 && pip install -e ".[dev,recorder]"
#
# "0" and "" leave the native build on, so the variable can be defined globally
# (e.g. in a CI matrix) and still overridden. The guard is a plain if() rather
# than a top-level return(), which CMake only allows from 3.29; the floor here
# stays at 3.23 to match pyproject.toml.
set(FPSCOACH_SKIP_NATIVE OFF)
if(DEFINED ENV{FPSCOACH_SKIP_NATIVE}
   AND NOT "$ENV{FPSCOACH_SKIP_NATIVE}" STREQUAL "0"
   AND NOT "$ENV{FPSCOACH_SKIP_NATIVE}" STREQUAL "")
    set(FPSCOACH_SKIP_NATIVE ON)
endif()

if(FPSCOACH_SKIP_NATIVE)
    message(STATUS "FPS Coach: FPSCOACH_SKIP_NATIVE is set - skipping the native core "
                   "(pure-Python install; the app uses its fallback analyzer).")
else()
    enable_language(CXX)

    set(CMAKE_CXX_STANDARD 20)
    set(CMAKE_CXX_STANDARD_REQUIRED ON)
    set(CMAKE_CXX_EXTENSIONS OFF)

    add_library(fpscoach_analysis STATIC
        native/src/movement_analyzer.cpp
    )
    target_include_directories(fpscoach_analysis PUBLIC native/include)
    set_target_properties(fpscoach_analysis PROPERTIES POSITION_INDEPENDENT_CODE ON)

    if(MSVC)
        target_compile_options(fpscoach_analysis PRIVATE /W4 /permissive-)
    else()
        target_compile_options(fpscoach_analysis PRIVATE -Wall -Wextra -Wpedantic)
    endif()

    if(WIN32)
        find_package(Python REQUIRED COMPONENTS Interpreter Development.Module)
        find_package(pybind11 CONFIG REQUIRED)

        # LNK2001 "__imp__Py..." for the whole CPython API means this path was
        # empty and MSVC linked against headers alone. Print it, and say so
        # before the link step rather than after 149 unresolved externals.
        message(STATUS "FPS Coach: CPython ${Python_VERSION} import library: ${Python_LIBRARIES}")
        if(NOT Python_LIBRARIES)
            message(WARNING
                "FPS Coach: no CPython import library resolved for ${Python_EXECUTABLE}. "
                "The native core cannot link (LNK2001 __imp__Py...). Reinstall that Python "
                "with its development files, or set FPSCOACH_SKIP_NATIVE=1 to install "
                "without the native core.")
        endif()

        pybind11_add_module(fpscoach_native
            native/src/bindings.cpp
            native/src/capture.cpp
        )
        target_include_directories(fpscoach_native PRIVATE native/include)
        # WindowsApp: Windows Graphics Capture (C++/WinRT) for window-only capture.
        # Python::Module is listed explicitly: pybind11 normally links it through
        # pybind11::module, but if that does not reach the link line MSVC fails with
        # LNK2001 "unresolved external symbol __imp__Py..." for the whole CPython API.
        target_link_libraries(fpscoach_native PRIVATE
            fpscoach_analysis Python::Module d3d11 dxgi dxguid WindowsApp)
        target_compile_definitions(fpscoach_native PRIVATE NOMINMAX WIN32_LEAN_AND_MEAN)
        if(MSVC)
            target_compile_options(fpscoach_native PRIVATE /W4 /permissive- /EHsc)
        endif()

        install(TARGETS fpscoach_native
            LIBRARY DESTINATION .
            RUNTIME DESTINATION .
        )
    else()
        message(STATUS "Desktop Duplication extension is Windows-only; building analysis core only.")
    endif()

    include(CTest)
    if(BUILD_TESTING)
        add_executable(fpscoach_analysis_tests native/tests/test_movement_analyzer.cpp)
        target_link_libraries(fpscoach_analysis_tests PRIVATE fpscoach_analysis)
        add_test(NAME movement_analyzer COMMAND fpscoach_analysis_tests)
    endif()
endif()
