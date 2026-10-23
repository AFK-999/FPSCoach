cmake_minimum_required(VERSION 3.23)
project(FPSCoach VERSION 0.1.0 LANGUAGES CXX)

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

    pybind11_add_module(fpscoach_native
        native/src/bindings.cpp
        native/src/capture.cpp
    )
    target_include_directories(fpscoach_native PRIVATE native/include)
    # WindowsApp: Windows Graphics Capture (C++/WinRT) for window-only capture.
    target_link_libraries(fpscoach_native PRIVATE fpscoach_analysis d3d11 dxgi dxguid WindowsApp)
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
