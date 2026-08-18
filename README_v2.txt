BUNCOMBE MSD TO DXF v2

Changes:
- Address search now uses Buncombe County's public AddressSearch2 GeocodeServer.
- Save location uses a real Save As dialog; no assumed Desktop path.
- Query output is clipped to the requested State Plane buffer rectangle.
- Separate CAD layers for gravity mains, laterals, manholes, pressure mains and labels.
- Full survey-useful labels.

Gravity main label:
  diameter + material + SANITARY SEWER
  FROMMH - TOMH
  upstream elevation + downstream elevation + slope

Manhole label:
  Manhole ID
  Rim elevation
  Invert Out
  Invert In 1
  Invert In 2
  Invert In 3
  Invert In 4
  Drop invert

Lateral label:
  diameter + material + service type + SEWER LATERAL

Pressure-main label:
  diameter + material + PRESSURIZED SEWER MAIN

All non-empty source GIS attributes remain attached to the DXF entity as MSDATTR XDATA.

Build:
Upload/replace the supplied files in the existing GitHub repo. Preserve
.github/workflows/build-windows-exe.yml. Commit. Actions will build
Buncombe_MSD_DXF_v2.exe.
