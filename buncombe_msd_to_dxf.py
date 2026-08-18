
import os
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from datetime import datetime

import requests
import ezdxf
from pyproj import Transformer

APP_NAME = "Buncombe MSD to DXF"
BASE = "https://gis.buncombecounty.org/arcgis/rest/services/permits/MapServer"
SRID = 2264

LAYERS = {
    "Gravity Mains": {"id": 25, "cad_layer": "S-SSWR-MAIN", "kind": "line"},
    "Laterals": {"id": 24, "cad_layer": "S-SSWR-LATL", "kind": "line"},
    "Manholes": {"id": 18, "cad_layer": "S-SSWR-MH", "kind": "point"},
    "Pressurized Mains": {"id": 23, "cad_layer": "S-SSWR-FM", "kind": "line"},
}

def geocode_address(address: str):
    url = "https://geocode-api.arcgis.com/arcgis/rest/services/World/GeocodeServer/findAddressCandidates"
    params = {
        "f": "json",
        "SingleLine": address,
        "outFields": "Match_addr,Addr_type",
        "maxLocations": 5,
    }
    r = requests.get(url, params=params, timeout=30)
    r.raise_for_status()
    data = r.json()
    candidates = data.get("candidates") or []
    if not candidates:
        raise RuntimeError(f"No geocoding match found for: {address}")
    best = candidates[0]
    loc = best["location"]
    return float(loc["x"]), float(loc["y"]), best.get("address", address)

def lonlat_to_nc2264(lon, lat):
    transformer = Transformer.from_crs(4326, SRID, always_xy=True)
    return transformer.transform(lon, lat)

def query_layer(layer_id, xmin, ymin, xmax, ymax):
    url = f"{BASE}/{layer_id}/query"
    params = {
        "f": "geojson",
        "where": "1=1",
        "geometry": f"{xmin},{ymin},{xmax},{ymax}",
        "geometryType": "esriGeometryEnvelope",
        "inSR": SRID,
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": "*",
        "returnGeometry": "true",
        "outSR": SRID,
    }
    r = requests.get(url, params=params, timeout=60)
    r.raise_for_status()
    data = r.json()
    if isinstance(data, dict) and "error" in data:
        raise RuntimeError(str(data["error"]))
    return data

def safe_str(v):
    return "" if v is None else str(v)

def xdata_for(props):
    keys = [
        "OBJECTID", "FACILITYID", "LEGACYID", "DIAMETER", "MATERIAL",
        "FROMMH", "TOMH", "UPLELEV", "DOWNLELEV", "SLOPE",
        "RIMELEV", "INVERTELEV", "DATASOURCE", "OWNEDBY", "MAINTBY", "LOCATION"
    ]
    out = []
    for key in keys:
        val = props.get(key)
        if val not in (None, ""):
            out.append((1000, f"{key}={safe_str(val)}"))
    return out[:50]

def add_features(doc, msp, config, geojson, label_manholes):
    layer_name = config["cad_layer"]
    if layer_name not in doc.layers:
        doc.layers.add(layer_name)
    if "MSDATTR" not in doc.appids:
        doc.appids.add("MSDATTR")

    count = 0
    for ft in geojson.get("features", []):
        geom = ft.get("geometry") or {}
        props = ft.get("properties") or {}
        gtype = geom.get("type")
        coords = geom.get("coordinates")
        xd = xdata_for(props)

        if config["kind"] == "line":
            if gtype == "LineString":
                parts = [coords]
            elif gtype == "MultiLineString":
                parts = coords
            else:
                parts = []
            for part in parts:
                if not part or len(part) < 2:
                    continue
                pts = [(float(p[0]), float(p[1])) for p in part]
                ent = msp.add_lwpolyline(pts, dxfattribs={"layer": layer_name})
                if xd:
                    ent.set_xdata("MSDATTR", xd)
                count += 1

        elif config["kind"] == "point":
            if gtype == "Point":
                points = [coords]
            elif gtype == "MultiPoint":
                points = coords
            else:
                points = []
            for p in points:
                if not p or len(p) < 2:
                    continue
                x, y = float(p[0]), float(p[1])
                ent = msp.add_circle((x, y), radius=1.0, dxfattribs={"layer": layer_name})
                if xd:
                    ent.set_xdata("MSDATTR", xd)
                if label_manholes:
                    label = props.get("LEGACYID") or props.get("FACILITYID")
                    if label:
                        msp.add_text(
                            safe_str(label),
                            dxfattribs={"layer": layer_name, "height": 2.5}
                        ).set_placement((x + 2.0, y + 2.0))
                count += 1
    return count

def create_dxf(cx, cy, buffer_ft, selected_names, output_path, label_manholes, logger):
    xmin = cx - buffer_ft
    ymin = cy - buffer_ft
    xmax = cx + buffer_ft
    ymax = cy + buffer_ft

    doc = ezdxf.new("R2018")
    doc.header["$INSUNITS"] = 2  # feet
    msp = doc.modelspace()

    total = 0
    for name in selected_names:
        cfg = LAYERS[name]
        logger(f"Pulling {name}...")
        gj = query_layer(cfg["id"], xmin, ymin, xmax, ymax)
        n = add_features(doc, msp, cfg, gj, label_manholes and name == "Manholes")
        logger(f"  {n} feature(s)")
        total += n

    note_layer = "MSD-GIS-NOTE"
    if note_layer not in doc.layers:
        doc.layers.add(note_layer)
    note = (
        "BUNCOMBE COUNTY / MSD GIS REFERENCE DATA - EPSG:2264 - "
        f"Retrieved {datetime.now().strftime('%Y-%m-%d %H:%M')} - "
        "FIELD VERIFY UTILITIES."
    )
    msp.add_text(note, dxfattribs={"layer": note_layer, "height": 2.5}).set_placement((xmin, ymin - 15.0))
    doc.saveas(output_path)
    return total

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_NAME)
        self.geometry("760x640")
        self.minsize(720, 600)

        self.mode = tk.StringVar(value="address")
        self.address = tk.StringVar(value="15 Walden Drive, Arden, NC")
        self.x = tk.StringVar()
        self.y = tk.StringVar()
        self.buffer = tk.StringVar(value="750")
        desktop = os.path.join(os.path.expanduser("~"), "Desktop")
        self.output = tk.StringVar(value=os.path.join(desktop, "Buncombe_MSD_Export.dxf"))
        self.label_mh = tk.BooleanVar(value=True)

        self.layer_vars = {
            "Gravity Mains": tk.BooleanVar(value=True),
            "Laterals": tk.BooleanVar(value=True),
            "Manholes": tk.BooleanVar(value=True),
            "Pressurized Mains": tk.BooleanVar(value=False),
        }
        self._build_ui()

    def _build_ui(self):
        pad = {"padx": 8, "pady": 6}

        ttk.Label(self, text="Buncombe MSD → DXF", font=("Segoe UI", 18, "bold")).pack(
            anchor="w", padx=16, pady=(16, 8)
        )

        loc = ttk.LabelFrame(self, text="Project Location")
        loc.pack(fill="x", padx=16, pady=6)

        ttk.Radiobutton(loc, text="Address", variable=self.mode, value="address").grid(row=0, column=0, sticky="w", **pad)
        ttk.Entry(loc, textvariable=self.address, width=62).grid(row=0, column=1, columnspan=4, sticky="ew", **pad)

        ttk.Radiobutton(loc, text="State Plane center", variable=self.mode, value="xy").grid(row=1, column=0, sticky="w", **pad)
        ttk.Label(loc, text="X").grid(row=1, column=1, sticky="e")
        ttk.Entry(loc, textvariable=self.x, width=16).grid(row=1, column=2, sticky="w", **pad)
        ttk.Label(loc, text="Y").grid(row=1, column=3, sticky="e")
        ttk.Entry(loc, textvariable=self.y, width=16).grid(row=1, column=4, sticky="w", **pad)

        ttk.Label(loc, text="Buffer (ft)").grid(row=2, column=0, sticky="w", **pad)
        ttk.Entry(loc, textvariable=self.buffer, width=12).grid(row=2, column=1, sticky="w", **pad)
        loc.columnconfigure(1, weight=1)

        lay = ttk.LabelFrame(self, text="MSD Data")
        lay.pack(fill="x", padx=16, pady=6)
        for i, (name, var) in enumerate(self.layer_vars.items()):
            ttk.Checkbutton(lay, text=name, variable=var).grid(row=0, column=i, sticky="w", **pad)
        ttk.Checkbutton(lay, text="Label manholes", variable=self.label_mh).grid(
            row=1, column=0, columnspan=2, sticky="w", **pad
        )

        out = ttk.LabelFrame(self, text="DXF Output")
        out.pack(fill="x", padx=16, pady=6)
        ttk.Entry(out, textvariable=self.output).grid(row=0, column=0, sticky="ew", **pad)
        ttk.Button(out, text="Browse", command=self.browse).grid(row=0, column=1, **pad)
        out.columnconfigure(0, weight=1)

        ttk.Button(self, text="CREATE DXF", command=self.start).pack(pady=10)

        self.log_box = tk.Text(self, height=15, wrap="word")
        self.log_box.pack(fill="both", expand=True, padx=16, pady=(0, 16))
        self.log("Ready.")

    def browse(self):
        path = filedialog.asksaveasfilename(
            title="Save DXF",
            defaultextension=".dxf",
            filetypes=[("DXF Files", "*.dxf")]
        )
        if path:
            self.output.set(path)

    def log(self, msg):
        self.log_box.insert("end", msg + "\n")
        self.log_box.see("end")
        self.update_idletasks()

    def start(self):
        selected = [n for n, v in self.layer_vars.items() if v.get()]
        if not selected:
            messagebox.showerror(APP_NAME, "Select at least one MSD layer.")
            return

        try:
            buffer_ft = float(self.buffer.get())
        except ValueError:
            messagebox.showerror(APP_NAME, "Buffer must be a number.")
            return

        out = self.output.get().strip()
        if not out.lower().endswith(".dxf"):
            out += ".dxf"
            self.output.set(out)

        def worker():
            try:
                if self.mode.get() == "address":
                    addr = self.address.get().strip()
                    self.log(f"Geocoding: {addr}")
                    lon, lat, match = geocode_address(addr)
                    self.log(f"Matched: {match}")
                    cx, cy = lonlat_to_nc2264(lon, lat)
                    self.log(f"Center: X={cx:.2f}, Y={cy:.2f}")
                else:
                    cx = float(self.x.get())
                    cy = float(self.y.get())

                self.log(f"Buffer: {buffer_ft:.0f} ft")
                total = create_dxf(
                    cx, cy, buffer_ft, selected, out, self.label_mh.get(), self.log
                )
                self.log(f"DONE — {total} feature(s)")
                self.log(out)
                messagebox.showinfo(APP_NAME, f"DXF created.\n\n{out}\n\n{total} feature(s)")
            except Exception as e:
                self.log(f"ERROR: {e}")
                messagebox.showerror(APP_NAME, str(e))

        threading.Thread(target=worker, daemon=True).start()

if __name__ == "__main__":
    App().mainloop()
