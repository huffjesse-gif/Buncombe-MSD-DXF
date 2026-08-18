@echo off
setlocal
echo Building Buncombe_MSD_DXF.exe...
py -m pip install --upgrade pip
py -m pip install -r requirements.txt
pyinstaller --noconfirm --clean --onefile --windowed --name Buncombe_MSD_DXF buncombe_msd_to_dxf.py
echo.
echo DONE
echo EXE: dist\Buncombe_MSD_DXF.exe
pause
