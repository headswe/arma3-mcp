cmake_minimum_required(VERSION 3.13)

# Output: arma3_mcp_x64.dll on Windows, arma3_mcp_x64.so on Linux.
project(arma3_mcp CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

set(A3MCP_VERSION "0.1.0" CACHE STRING "Version reported by the extension")

if(CMAKE_SIZEOF_VOID_P EQUAL 8)
    set(A3MCP_TARGET arma3_mcp_x64)
else()
    set(A3MCP_TARGET arma3_mcp)
endif()

add_library(${A3MCP_TARGET} SHARED src/extension.cpp src/bridge.cpp)
target_compile_definitions(${A3MCP_TARGET} PRIVATE A3MCP_VERSION="${A3MCP_VERSION}")
set_target_properties(${A3MCP_TARGET} PROPERTIES PREFIX "" CXX_VISIBILITY_PRESET hidden)
set_target_properties(${A3MCP_TARGET} PROPERTIES
    RUNTIME_OUTPUT_DIRECTORY "${PROJECT_SOURCE_DIR}/build/$<CONFIG>"
    LIBRARY_OUTPUT_DIRECTORY "${PROJECT_SOURCE_DIR}/build/$<CONFIG>")

if(WIN32)
    target_link_libraries(${A3MCP_TARGET} PRIVATE ws2_32)
endif()

if(MSVC)
    # Static CRT so the DLL needs no Visual C++ redistributable.
    set_property(TARGET ${A3MCP_TARGET} PROPERTY MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>")
    target_compile_options(${A3MCP_TARGET} PRIVATE /W4 /EHsc)
else()
    target_compile_options(${A3MCP_TARGET} PRIVATE -Wall -Wextra -O2)
    target_link_options(${A3MCP_TARGET} PRIVATE -static-libgcc -static-libstdc++)
    find_package(Threads REQUIRED)
    target_link_libraries(${A3MCP_TARGET} PRIVATE Threads::Threads ${CMAKE_DL_LIBS})
endif()
