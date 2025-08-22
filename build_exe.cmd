echo on
setlocal EnableDelayedExpansion

set compile_file_name=%1
for /f "tokens=*" %%i in ('where upx') do set "UPX_DIR=%%~dpi"
echo Path to upx: %UPX_DIR%
set upx_path=%UPX_DIR%
rem set upx_path=upx


pip install -r requirements.txt

pyinstaller --onefile --console --clean --upx-dir %upx_path%  "%compile_file_name%.py"
rem pyinstaller --onefile --console --clean  "%compile_file_name%.py"

rem move /y dist\%compile_file_name%.exe %compile_file_name%.exe

pause