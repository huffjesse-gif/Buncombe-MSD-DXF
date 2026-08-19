
import os, math, threading, tkinter as tk
from tkinter import ttk, filedialog, messagebox
from datetime import datetime
from pathlib import Path

import requests
import ezdxf

APP_NAME = "NC Project GIS Pull v6"
BASE = "https://gis.buncombecounty.org/arcgis/rest/services/permits/MapServer"
GEOCODER = "https://services.gis.nc.gov/secure/rest/services/AddressNC/AddressNC_geocoder/GeocodeServer/findAddressCandidates"
SRID = 2264

LAYERS = {
    "Gravity Mains": {"id":25, "cad":"GIS-SSWR-MAIN", "label":"GIS-SSWR-MAIN-TEXT", "kind":"line"},
    "Laterals": {"id":24, "cad":"GIS-SSWR-LATL", "label":"GIS-SSWR-LATL-TEXT", "kind":"line"},
    "Manholes": {"id":18, "cad":"GIS-SSWR-MH", "label":"GIS-SSWR-MH-TEXT", "kind":"point"},
    "Pressurized Mains": {"id":23, "cad":"GIS-SSWR-FM", "label":"GIS-SSWR-FM-TEXT", "kind":"line"},
}


PARCEL_URL = "https://services.gis.nc.gov/secure/rest/services/NC1Map_Parcels/FeatureServer/1/query"
ROAD_BASE = "https://gis11.services.ncdot.gov/arcgis/rest/services/NCDOT_RoadNC/RoadNC_CenterlinesColor/MapServer"
ROAD_NAME_URL = "https://gis11.services.ncdot.gov/arcgis/rest/services/NCDOT_RoadNC/RoadNC_RoadNameLabels/MapServer/2/query"
HYDRO_BASE = "https://services.nconemap.gov/secure/rest/services/NC1Map_Hydrography/MapServer"
ASHEVILLE_STORM_LINES = "https://gis.ashevillenc.gov/server/rest/services/Stormwater/StormwaterLines/FeatureServer"
ASHEVILLE_STORM_STRUCT = "https://gis.ashevillenc.gov/server/rest/services/Stormwater/StormwaterStructure/FeatureServer"

# Asheville/Buncombe provider footprint; outside this area the local utility calls simply return no features.
ASHEVILLE_EXTENT = (908000, 627000, 972000, 713000)
BUNCOMBE_EXTENT = (800000, 500000, 1060000, 865000)

def _candidate_urls(url):
    urls=[url]
    if "services.gis.nc.gov" in url:
        urls.append(url.replace("services.gis.nc.gov","services.nconemap.gov"))
    elif "services.nconemap.gov" in url:
        urls.append(url.replace("services.nconemap.gov","services.gis.nc.gov"))
    return urls

def _get_json(url, params, timeout=60, tries=3):
    last=None
    for candidate in _candidate_urls(url):
        for attempt in range(tries):
            try:
                r=requests.get(candidate,params=params,timeout=timeout)
                r.raise_for_status()
                d=r.json()
                if isinstance(d,dict) and "error" in d:
                    raise RuntimeError(str(d["error"]))
                return d
            except Exception as e:
                last=e
    raise last if last else RuntimeError("GIS request failed.")

def query_url(url,xmin,ymin,xmax,ymax,out_fields="*",fmt="geojson"):
    params={"f":fmt,"where":"1=1","geometry":f"{xmin},{ymin},{xmax},{ymax}",
            "geometryType":"esriGeometryEnvelope","inSR":SRID,
            "spatialRel":"esriSpatialRelIntersects","outFields":out_fields,
            "returnGeometry":"true","outSR":SRID}
    return _get_json(url,params,timeout=60,tries=2)

def query_parcels(xmin,ymin,xmax,ymax,logger):
    # Two-stage query: get only IDs for the envelope, then retrieve geometry
    # in small batches. This avoids large one-shot parcel requests timing out.
    id_params={
        "f":"json","where":"1=1",
        "geometry":f"{xmin},{ymin},{xmax},{ymax}",
        "geometryType":"esriGeometryEnvelope","inSR":SRID,
        "spatialRel":"esriSpatialRelIntersects",
        "returnIdsOnly":"true","returnGeometry":"false"
    }
    ids_data=_get_json(PARCEL_URL,id_params,timeout=45,tries=3)
    ids=ids_data.get("objectIds") or []
    logger(f"  Parcel IDs found: {len(ids)}")
    features=[]
    for i in range(0,len(ids),100):
        chunk=ids[i:i+100]
        p={
            "f":"geojson",
            "objectIds":",".join(str(x) for x in chunk),
            "outFields":"*",
            "returnGeometry":"true",
            "outSR":SRID
        }
        d=_get_json(PARCEL_URL,p,timeout=60,tries=3)
        features.extend(d.get("features",[]))
    return {"type":"FeatureCollection","features":features}

def add_geojson_geometry(msp, geom, layer, point_radius=2.0, xdata=None):
    gt=geom.get("type"); c=geom.get("coordinates") or []; n=0
    if gt in ("LineString","MultiLineString"):
        parts=[c] if gt=="LineString" else c
        for part in parts:
            if len(part)<2: continue
            pts=[(float(p[0]),float(p[1])) for p in part]
            e=msp.add_lwpolyline(pts,dxfattribs={"layer":layer})
            if xdata: e.set_xdata("GISATTR",xdata)
            n+=1
    elif gt in ("Polygon","MultiPolygon"):
        polys=[c] if gt=="Polygon" else c
        for poly in polys:
            for ring in poly:
                if len(ring)<3: continue
                pts=[(float(p[0]),float(p[1])) for p in ring]
                e=msp.add_lwpolyline(pts,close=True,dxfattribs={"layer":layer})
                if xdata: e.set_xdata("GISATTR",xdata)
                n+=1
    elif gt in ("Point","MultiPoint"):
        pts=[c] if gt=="Point" else c
        for p in pts:
            if len(p)<2: continue
            e=msp.add_circle((float(p[0]),float(p[1])),point_radius,dxfattribs={"layer":layer})
            if xdata: e.set_xdata("GISATTR",xdata)
            n+=1
    return n

def feature_center(geom):
    pts=[]
    def walk(v):
        if isinstance(v,(list,tuple)) and len(v)>=2 and isinstance(v[0],(int,float)) and isinstance(v[1],(int,float)): pts.append((v[0],v[1]))
        elif isinstance(v,(list,tuple)):
            for x in v: walk(x)
    walk(geom.get("coordinates",[]))
    if not pts:return None
    return sum(x for x,y in pts)/len(pts),sum(y for x,y in pts)/len(pts)

def first_prop(p,*names):
    lower={str(k).lower():v for k,v in p.items()}
    for n in names:
        v=lower.get(n.lower())
        if v not in (None,""): return v
    return None

def add_statewide(msp,xmin,ymin,xmax,ymax,opts,labels,logger):
    total=0
    if opts.get("parcels"):
        logger("Pulling NC OneMap parcels...")
        try:
            gj=query_parcels(xmin,ymin,xmax,ymax,logger)
        except Exception as e:
            logger(f"  WARNING: Parcel service unavailable: {e}")
            gj={"features":[]}
        for ft in gj.get("features",[]):
            p=ft.get("properties") or {}; g=ft.get("geometry") or {}
            total+=add_geojson_geometry(msp,g,"GIS-PARCELS",xdata=all_xdata(p))
            if labels:
                c=feature_center(g)
                if c:
                    pin=first_prop(p,"parno","parcelid","parcel_id","pin","pid")
                    addr=first_prop(p,"situsaddr","siteaddr","address","full_address")
                    area=first_prop(p,"gisacres","acreage","acres")
                    if pin:add_mtext(msp,str(pin),c,"GIS-PARCEL-NUM",4.0)
                    if addr:add_mtext(msp,str(addr),(c[0],c[1]-6),"GIS-PARCEL-ADDRESS",3.5)
                    if area:
                        try: txt=f"{float(area):.2f} AC"
                        except: txt=str(area)
                        add_mtext(msp,txt,(c[0],c[1]-12),"GIS-PARCEL-AREA",3.5)
        logger(f"  {len(gj.get('features',[]))} parcel feature(s)")
    if opts.get("roads"):
        logger("Pulling NCDOT statewide roads...")
        for lid in range(5):
            gj=query_url(f"{ROAD_BASE}/{lid}/query",xmin,ymin,xmax,ymax)
            for ft in gj.get("features",[]): total+=add_geojson_geometry(msp,ft.get("geometry") or {},"GIS-CL",xdata=all_xdata(ft.get("properties") or {}))
        if labels:
            gj=query_url(ROAD_NAME_URL,xmin,ymin,xmax,ymax)
            for ft in gj.get("features",[]):
                p=ft.get("properties") or {}; g=ft.get("geometry") or {}; name=first_prop(p,"FullName","RouteName")
                if name:
                    c=feature_center(g)
                    if c:add_mtext(msp,str(name),c,"GIS-CL-TEXT",4.0)
    if opts.get("hydro"):
        logger("Pulling NC OneMap hydrography...")
        for lid,layer in [(1,"GIS-STREAM"),(2,"GIS-WATER")]:
            gj=query_url(f"{HYDRO_BASE}/{lid}/query",xmin,ymin,xmax,ymax)
            for ft in gj.get("features",[]): total+=add_geojson_geometry(msp,ft.get("geometry") or {},layer,xdata=all_xdata(ft.get("properties") or {}))
    return total

def intersects_extent(cx,cy,b,ext):
    return not (cx+b<ext[0] or cx-b>ext[2] or cy+b<ext[1] or cy-b>ext[3])

def add_asheville_storm(msp,xmin,ymin,xmax,ymax,logger):
    total=0
    # StormwaterLines service: discover layers, then query every feature layer returned.
    for base,layer in [(ASHEVILLE_STORM_LINES,"GIS-STM-MAIN"),(ASHEVILLE_STORM_STRUCT,"GIS-STM-STR")]:
        try:
            meta=requests.get(base,params={"f":"json"},timeout=30).json()
            ids=[x["id"] for x in meta.get("layers",[]) if isinstance(x.get("id"),int)]
            logger(f"Pulling Asheville {'storm lines' if layer.endswith('MAIN') else 'storm structures'}...")
            for lid in ids:
                try:
                    gj=query_url(f"{base}/{lid}/query",xmin,ymin,xmax,ymax)
                    for ft in gj.get("features",[]): total+=add_geojson_geometry(msp,ft.get("geometry") or {},layer,xdata=all_xdata(ft.get("properties") or {}))
                except Exception: pass
        except Exception as e: logger(f"  Asheville storm source unavailable: {e}")
    return total

def val(props, key):
    v = props.get(key)
    return None if v in (None, "", "Null", "null") else v

def fmt_num(v, decimals=2):
    if v in (None, ""): return None
    try: return f"{float(v):.{decimals}f}"
    except: return str(v)

def diameter(props):
    v = val(props, "DIAMETER")
    if v is None: return None
    try: return f'{float(v):g}"'
    except: return str(v)

def geocode(address):
    params={
        "f":"json",
        "SingleLine":address,
        "outFields":"*",
        "outSR":SRID,
        "maxLocations":10
    }
    d=_get_json(GEOCODER,params,timeout=45,tries=3)
    cands=d.get("candidates",[])
    if not cands:
        raise RuntimeError(f"No statewide NC address match found for: {address}")
    best=max(cands,key=lambda x:x.get("score",0))
    score=float(best.get("score",0) or 0)
    if score < 90:
        raise RuntimeError(
            f"Address match is not reliable enough (score {score:.1f}): "
            f"{best.get('address','unknown match')}. Use State Plane center instead."
        )
    loc=best["location"]
    return float(loc["x"]),float(loc["y"]),best.get("address",address),score

def query_layer(layer_id, xmin,ymin,xmax,ymax):
    url=f"{BASE}/{layer_id}/query"
    params={
        "f":"geojson","where":"1=1",
        "geometry":f"{xmin},{ymin},{xmax},{ymax}",
        "geometryType":"esriGeometryEnvelope",
        "inSR":SRID,
        "spatialRel":"esriSpatialRelIntersects",
        "outFields":"*",
        "returnGeometry":"true",
        "outSR":SRID
    }
    r=requests.get(url,params=params,timeout=60); r.raise_for_status()
    d=r.json()
    if "error" in d: raise RuntimeError(str(d["error"]))
    return d

def all_xdata(p):
    out=[]
    for k,v in p.items():
        if v not in (None,""):
            out.append((1000,f"{k}={v}"[:250]))
    return out[:100]

def line_midpoint_angle(points):
    segs=[]; total=0
    for a,b in zip(points[:-1],points[1:]):
        L=math.hypot(b[0]-a[0],b[1]-a[1]); segs.append((a,b,L)); total+=L
    if total<=0:return points[0][0],points[0][1],0
    target=total/2; acc=0
    for a,b,L in segs:
        if acc+L>=target and L:
            t=(target-acc)/L
            x=a[0]+t*(b[0]-a[0]); y=a[1]+t*(b[1]-a[1])
            ang=math.degrees(math.atan2(b[1]-a[1],b[0]-a[0]))
            if ang>90: ang-=180
            if ang<-90: ang+=180
            return x,y,ang
        acc+=L
    return points[0][0],points[0][1],0

def line_label(name,p):
    dia=diameter(p); mat=val(p,"MATERIAL")
    if name=="Gravity Mains":
        lines=[]
        main=" ".join(x for x in [dia,mat,"SANITARY SEWER"] if x)
        if main: lines.append(main)
        frm=val(p,"FROMMH"); to=val(p,"TOMH")
        if frm or to: lines.append("MH "+" - ".join(str(x) for x in [frm,to] if x))
        up=fmt_num(val(p,"UPLELEV") or val(p,"UPELEV"))
        dn=fmt_num(val(p,"DOWNLELEV") or val(p,"DOWNELEV"))
        sl=fmt_num(val(p,"SLOPE"),2)
        elev=[]
        if up:elev.append(f"UP {up}")
        if dn:elev.append(f"DN {dn}")
        if sl:elev.append(f"SLOPE {sl}")
        if elev:lines.append("  ".join(elev))
        return "\n".join(lines)
    if name=="Laterals":
        return " ".join(x for x in [dia,mat,val(p,"SERVICETYPE"),"SEWER LATERAL"] if x)
    if name=="Pressurized Mains":
        return " ".join(x for x in [dia,mat,"PRESSURIZED SEWER MAIN"] if x)
    return ""

def mh_label(p):
    lines=[]
    ident=val(p,"LEGACYID") or val(p,"FACILITYID")
    if ident: lines.append(f"MH {ident}")
    rim=fmt_num(val(p,"RIMELEV"))
    if rim: lines.append(f"RIM {rim}")
    invout=fmt_num(val(p,"INVERTELEV"))
    if invout: lines.append(f"INV OUT {invout}")
    for key,lab in [("INVERT","INV IN 1"),("INVERTTWO","INV IN 2"),("INVERTTHREE","INV IN 3"),("INVERTFOUR","INV IN 4"),("DROPINV","DROP INV")]:
        v=fmt_num(val(p,key))
        if v: lines.append(f"{lab} {v}")
    return "\n".join(lines)

def add_mtext(msp,text,xy,layer,height=5.0,rotation=0):
    if not text:return
    e=msp.add_mtext(text.replace("\n","\\P"),dxfattribs={"layer":layer,"char_height":height,"rotation":rotation})
    e.set_location(xy)

def make_layers(doc):
    for n,c in {
        "GIS-SSWR-MAIN":1,"GIS-SSWR-MAIN-TEXT":1,
        "GIS-SSWR-LATL":8,"GIS-SSWR-LATL-TEXT":8,
        "GIS-SSWR-MH":1,"GIS-SSWR-MH-TEXT":1,
        "GIS-SSWR-FM":6,"GIS-SSWR-FM-TEXT":6,
        "GIS-PARCELS":163,"GIS-PARCEL-NUM":1,"GIS-PARCEL-ADDRESS":1,"GIS-PARCEL-AREA":1,
        "GIS-CL":143,"GIS-CL-TEXT":143,"GIS-STREAM":143,"GIS-WATER":143,
        "GIS-STM-MAIN":4,"GIS-STM-STR":4,"GIS-NOTE":8
    }.items():
        if n not in doc.layers: doc.layers.add(n,color=c)

def add_v1_geometry_line(msp,cfg,geom,props,do_labels,name):
    """Literal v1 geometry handling: source coordinates are written directly."""
    coords=geom.get("coordinates") or []
    gtype=geom.get("type")
    if gtype=="LineString":
        parts=[coords]
    elif gtype=="MultiLineString":
        parts=coords
    else:
        return 0

    xd=all_xdata(props)
    count=0
    for part in parts:
        if not part or len(part)<2: continue
        # EXACT SOURCE VERTICES, NO EDITING OF ANY KIND
        pts=[(float(x),float(y)) for x,y,*rest in part]
        ent=msp.add_lwpolyline(pts,dxfattribs={"layer":cfg["cad"]})
        if xd: ent.set_xdata("MSDATTR",xd)
        if do_labels:
            x,y,a=line_midpoint_angle(pts)
            add_mtext(msp,line_label(name,props),(x,y+5),cfg["label"],5.0,a)
        count+=1
    return count

def add_v1_geometry_point(msp,cfg,geom,props,do_labels):
    gtype=geom.get("type"); coords=geom.get("coordinates")
    if gtype=="Point": pts=[coords]
    elif gtype=="MultiPoint": pts=coords or []
    else: return 0
    xd=all_xdata(props); count=0
    for p in pts:
        if not p or len(p)<2: continue
        x,y=float(p[0]),float(p[1])
        ent=msp.add_circle((x,y),2.0,dxfattribs={"layer":cfg["cad"]})
        if xd: ent.set_xdata("MSDATTR",xd)
        if do_labels: add_mtext(msp,mh_label(props),(x+6,y+6),cfg["label"],5.0)
        count+=1
    return count

def create_dxf(cx,cy,buffer_ft,selected,out,labels,logger,state_opts=None,storm=False):
    xmin,ymin,xmax,ymax=cx-buffer_ft,cy-buffer_ft,cx+buffer_ft,cy+buffer_ft
    logger(f"QUERY ENVELOPE: {xmin:.2f},{ymin:.2f},{xmax:.2f},{ymax:.2f}")

    doc=ezdxf.new("R2018"); doc.header["$INSUNITS"]=2
    if "MSDATTR" not in doc.appids: doc.appids.add("MSDATTR")
    if "GISATTR" not in doc.appids: doc.appids.add("GISATTR")
    make_layers(doc); msp=doc.modelspace()
    total=0
    state_opts=state_opts or {}
    try:
        total += add_statewide(msp,xmin,ymin,xmax,ymax,state_opts,labels,logger)
    except Exception as e:
        logger(f"WARNING: A statewide GIS source failed; continuing with remaining configured data: {e}")
    if storm and intersects_extent(cx,cy,buffer_ft,ASHEVILLE_EXTENT):
        total += add_asheville_storm(msp,xmin,ymin,xmax,ymax,logger)

    for name in selected:
        cfg=LAYERS[name]; logger(f"Pulling {name}...")
        gj=query_layer(cfg["id"],xmin,ymin,xmax,ymax)
        raw_count=len(gj.get("features",[]))
        logger(f"  Source features returned: {raw_count}")
        n=0
        for ft in gj.get("features",[]):
            g=ft.get("geometry") or {}; p=ft.get("properties") or {}
            if cfg["kind"]=="line":
                n+=add_v1_geometry_line(msp,cfg,g,p,labels,name)
            else:
                n+=add_v1_geometry_point(msp,cfg,g,p,labels)
        logger(f"  DXF entities written: {n}")
        total+=n

    note=f"GIS REFERENCE DATA - EPSG:2264 - Retrieved {datetime.now():%Y-%m-%d %H:%M} - FIELD VERIFY UTILITIES AND BOUNDARIES."
    add_mtext(msp,note,(xmin,ymin-20),"GIS-NOTE",5.0)
    Path(out).parent.mkdir(parents=True,exist_ok=True)
    doc.saveas(out)
    return total

class App(tk.Tk):
    def __init__(self):
        super().__init__(); self.title(APP_NAME); self.geometry("850x760"); self.minsize(800,680)
        self.mode=tk.StringVar(value="xy")
        self.address=tk.StringVar(value="15 Walden Drive, Arden, NC")
        self.x=tk.StringVar(value="952383.09")
        self.y=tk.StringVar(value="642534.51")
        self.buffer=tk.StringVar(value="750")
        self.output=tk.StringVar(value="")
        self.labels=tk.BooleanVar(value=True)
        self.parcels=tk.BooleanVar(value=True); self.roads=tk.BooleanVar(value=True); self.hydro=tk.BooleanVar(value=True); self.storm=tk.BooleanVar(value=True)
        self.vicinity=tk.BooleanVar(value=False); self.vic_buffer=tk.StringVar(value="6000")
        self.vars={n:tk.BooleanVar(value=(n!="Pressurized Mains")) for n in LAYERS}
        self.build()

    def build(self):
        p={"padx":8,"pady":6}
        ttk.Label(self,text="NC Project GIS Pull v6",font=("Segoe UI",18,"bold")).pack(anchor="w",padx=16,pady=(16,8))
        f=ttk.LabelFrame(self,text="Project Location"); f.pack(fill="x",padx=16,pady=6)
        ttk.Radiobutton(f,text="Address",variable=self.mode,value="address").grid(row=0,column=0,sticky="w",**p)
        ttk.Entry(f,textvariable=self.address,width=65).grid(row=0,column=1,columnspan=4,sticky="ew",**p)
        ttk.Radiobutton(f,text="State Plane center",variable=self.mode,value="xy").grid(row=1,column=0,sticky="w",**p)
        ttk.Label(f,text="X").grid(row=1,column=1); ttk.Entry(f,textvariable=self.x,width=16).grid(row=1,column=2,**p)
        ttk.Label(f,text="Y").grid(row=1,column=3); ttk.Entry(f,textvariable=self.y,width=16).grid(row=1,column=4,**p)
        ttk.Label(f,text="Buffer (ft)").grid(row=2,column=0,sticky="w",**p); ttk.Entry(f,textvariable=self.buffer,width=12).grid(row=2,column=1,sticky="w",**p)

        sfrm=ttk.LabelFrame(self,text="Statewide / Local GIS Data"); sfrm.pack(fill="x",padx=16,pady=6)
        ttk.Checkbutton(sfrm,text="NC OneMap Parcels",variable=self.parcels).grid(row=0,column=0,sticky="w",**p)
        ttk.Checkbutton(sfrm,text="NCDOT Roads + Names",variable=self.roads).grid(row=0,column=1,sticky="w",**p)
        ttk.Checkbutton(sfrm,text="Streams / Water",variable=self.hydro).grid(row=0,column=2,sticky="w",**p)
        ttk.Checkbutton(sfrm,text="Local Storm (where configured)",variable=self.storm).grid(row=0,column=3,sticky="w",**p)
        ttk.Checkbutton(sfrm,text="Vicinity Map preset",variable=self.vicinity).grid(row=1,column=0,sticky="w",**p)
        ttk.Label(sfrm,text="Vicinity buffer (ft)").grid(row=1,column=1,sticky="e",**p); ttk.Entry(sfrm,textvariable=self.vic_buffer,width=10).grid(row=1,column=2,sticky="w",**p)

        l=ttk.LabelFrame(self,text="Buncombe MSD Sanitary Sewer (auto-use only where available)"); l.pack(fill="x",padx=16,pady=6)
        for i,(n,v) in enumerate(self.vars.items()):
            ttk.Checkbutton(l,text=n,variable=v).grid(row=0,column=i,sticky="w",**p)
        ttk.Checkbutton(l,text="Create full GIS labels",variable=self.labels).grid(row=1,column=0,columnspan=3,sticky="w",**p)

        o=ttk.LabelFrame(self,text="DXF Output"); o.pack(fill="x",padx=16,pady=6)
        ttk.Entry(o,textvariable=self.output).grid(row=0,column=0,sticky="ew",**p)
        ttk.Button(o,text="Browse / Save As",command=self.browse).grid(row=0,column=1,**p); o.columnconfigure(0,weight=1)

        ttk.Button(self,text="CREATE DXF",command=self.start).pack(pady=10)
        self.logbox=tk.Text(self,height=22,wrap="word"); self.logbox.pack(fill="both",expand=True,padx=16,pady=(0,16))
        self.log("Ready.")
        self.log("v6: resilient statewide parcel queries + statewide NC address locator.")

    def browse(self):
        p=filedialog.asksaveasfilename(title="Save MSD DXF",defaultextension=".dxf",initialfile="NC_Project_GIS_Export_v6.dxf",filetypes=[("DXF","*.dxf")])
        if p:self.output.set(p)

    def log(self,s):
        self.logbox.insert("end",s+"\n"); self.logbox.see("end"); self.update_idletasks()

    def start(self):
        sel=[n for n,v in self.vars.items() if v.get()]
        if not sel and not any([self.parcels.get(),self.roads.get(),self.hydro.get(),self.storm.get()]): return messagebox.showerror(APP_NAME,"Select at least one data layer.")
        try:b=float(self.buffer.get())
        except:return messagebox.showerror(APP_NAME,"Buffer must be numeric.")
        out=self.output.get().strip()
        if not out:
            self.browse(); out=self.output.get().strip()
            if not out:return

        def work():
            try:
                if self.mode.get()=="address":
                    q=self.address.get().strip()
                    self.log(f"Searching address locator: {q}")
                    cx,cy,match,score=geocode(q)
                    self.x.set(f"{cx:.2f}"); self.y.set(f"{cy:.2f}")
                    self.log(f"Matched: {match} (score {score})")
                    self.log(f"RESOLVED CENTER: X={cx:.2f}, Y={cy:.2f}")
                else:
                    cx,cy=float(self.x.get()),float(self.y.get())
                    self.log(f"MANUAL CENTER: X={cx:.2f}, Y={cy:.2f}")

                if self.vicinity.get():
                    b=float(self.vic_buffer.get()); self.log(f"Vicinity Map preset: {b:.0f} ft buffer")
                else: self.log(f"Buffer: {b:.0f} ft")
                # Only call Buncombe MSD when project envelope intersects Buncombe provider footprint.
                use_msd = sel if intersects_extent(cx,cy,b,BUNCOMBE_EXTENT) else []
                if sel and not use_msd: self.log("Buncombe MSD skipped — project is outside configured provider area.")
                state_opts={"parcels":self.parcels.get(),"roads":self.roads.get(),"hydro":self.hydro.get()}
                n=create_dxf(cx,cy,b,use_msd,out,self.labels.get(),self.log,state_opts,self.storm.get())
                self.log(f"DONE — {n} entities")
                self.log(out)
                messagebox.showinfo(APP_NAME,f"DXF created.\n\n{out}\n\n{n} entities")
            except Exception as e:
                self.log(f"ERROR: {e}"); messagebox.showerror(APP_NAME,str(e))

        threading.Thread(target=work,daemon=True).start()

if __name__=="__main__": App().mainloop()
