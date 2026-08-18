BUNCOMBE MSD TO DXF v3

Key correction from v2:
- Restores v1 linework behavior.
- MSD source line geometry is written exactly as returned by the GIS service.
- No clipping, reconstruction, simplification, resampling, or segment rewriting.
- The buffer controls QUERY SELECTION ONLY.

Retained from v2:
- Buncombe County public address lookup.
- Real Save As dialog.
- Separate CAD layers.
- Full labels.
- All non-empty GIS attributes attached as MSDATTR XDATA.

Label contents:
Gravity mains:
  diameter + material + SANITARY SEWER
  FROMMH - TOMH
  upstream elevation + downstream elevation + slope

Manholes:
  MH ID
  RIM
  INV OUT
  INV IN 1
  INV IN 2
  INV IN 3
  INV IN 4
  DROP INV

Laterals:
  diameter + material + service type + SEWER LATERAL

Pressurized mains:
  diameter + material + PRESSURIZED SEWER MAIN

Build:
Upload buncombe_msd_to_dxf_v3.py and requirements.txt.
Replace .github/workflows/build-windows-exe.yml with the included v3 workflow.
Commit, then run the GitHub Action.
