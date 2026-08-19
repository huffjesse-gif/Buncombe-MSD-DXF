NC PROJECT GIS PULL v6

Fixes from v5:
- Replaces Buncombe-only fuzzy address lookup with the NC statewide AddressNC locator.
- Rejects weak/fuzzy address matches instead of silently using the wrong coordinates.
- NC OneMap parcels are queried in two stages: IDs first, then geometry in batches of 100.
- Retries NC GIS requests and automatically switches between the services.gis.nc.gov
  and services.nconemap.gov hosts when available.
- Parcel-service failure logs a warning instead of killing the entire DXF export.
- Preserves the v1/v4 direct source-geometry behavior for utility linework.

For the Walden verification test, State Plane:
X = 952383.09
Y = 642534.51
