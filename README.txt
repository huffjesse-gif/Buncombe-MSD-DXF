
BUNCOMBE MSD TO DXF — OFFICE BUILD

PURPOSE
-------
Standalone Windows utility that queries Buncombe County / MSD sewer GIS
and creates a Carlson/AutoCAD-ready DXF in NC State Plane coordinates.

DATA
----
Gravity Mains       Buncombe layer 25 -> S-SSWR-MAIN
Laterals            Buncombe layer 24 -> S-SSWR-LATL
Manholes            Buncombe layer 18 -> S-SSWR-MH
Pressurized Mains   Buncombe layer 23 -> S-SSWR-FM

Coordinate system: EPSG:2264 / NAD83 North Carolina State Plane, US survey feet.

USE
---
1. Run Buncombe_MSD_DXF.exe.
2. Enter an address OR State Plane X/Y center.
3. Enter buffer distance in feet.
4. Select MSD data layers.
5. Click CREATE DXF.
6. Open, INSERT, or XREF the DXF in Carlson/AutoCAD.

NO PYTHON IS REQUIRED ON END-USER COMPUTERS ONCE THE EXE IS BUILT.

BUILD THE EXE WITHOUT INSTALLING PYTHON LOCALLY
-----------------------------------------------
1. Create a GitHub repository.
2. Upload this folder exactly as supplied, including the .github folder.
3. Push/commit to the main branch.
4. Open the repository's Actions tab.
5. Open "Build Windows EXE".
6. Run the workflow if it did not start automatically.
7. Download the artifact named "Buncombe_MSD_DXF_Windows".
8. Distribute Buncombe_MSD_DXF.exe to office users.

The GitHub Windows runner installs Python only during the build. The resulting
EXE is standalone.

LOCAL WINDOWS BUILD
-------------------
If a development computer already has Python, BUILD_WINDOWS_EXE.bat creates
the same executable in dist\Buncombe_MSD_DXF.exe.

SURVEY NOTE
-----------
The exported features are GIS reference data. Utility positions must be field
verified where survey-grade location is required.
