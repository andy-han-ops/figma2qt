cmake_minimum_required(VERSION 3.16)
project(qt_preview LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

find_package(Qt5 5.15 REQUIRED COMPONENTS Widgets UiTools Gui)

# The source is stored as main.cpp.txt so that it stays readable plain text in
# git on machines with transparent source-code encryption. It is materialised
# as main.cpp inside the build tree.
configure_file(${CMAKE_CURRENT_SOURCE_DIR}/main.cpp.txt
               ${CMAKE_CURRENT_BINARY_DIR}/main.cpp COPYONLY)
set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS main.cpp.txt)

add_executable(qt_preview ${CMAKE_CURRENT_BINARY_DIR}/main.cpp)
target_link_libraries(qt_preview PRIVATE Qt5::Widgets Qt5::UiTools Qt5::Gui)

if(MSVC)
  target_compile_options(qt_preview PRIVATE /utf-8)
endif()
