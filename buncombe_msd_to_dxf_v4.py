
import os, math, threading, tkinter as tk
from tkinter import ttk, filedialog, messagebox
from datetime import datetime
from pathlib import Path

import requests
import ezdxf

APP_NAME = "Buncombe MSD to DXF v4"
BASE = "https://gis.buncombecounty.org/arcgis/rest/services/permits/MapServer"
GEOCODER = "https://gis.buncombecounty.org/arcgis/rest/services/AddressSearch2/GeocodeServer/findAddressCandidates"
SRID = 2264

LAYERS = {
    "Gravity Mains": {"id":25, "cad":"S-SSWR-MAIN", "label":"S-SSWR-MAIN-TEXT", "kind":"line"},
    "Laterals": {"id":24, "cad":"S-SSWR-LATL", "label":"S-SSWR-LATL-TEXT", "kind":"line"},
    "Manholes": {"id":18, "cad":"S-SSWR-MH", "label":"S-SSWR-MH-TEXT", "kind":"point"},
    "Pressurized Mains": {"id":23, "cad":"S-SSWR-FM", "label":"S-SSWR-FM-TEXT", "kind":"line"},
}

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
    params = {
        "f":"json","SingleLine":address,"outFields":"*",
        "outSR":SRID,"maxLocations":10
    }
    r=requests.get(GEOCODER,params=params,timeout=30); r.raise_for_status()
    d=r.json()
    if "error" in d: raise RuntimeError(str(d["error"]))
    cands=d.get("candidates",[])
    if not cands:
        first=address.split(",")[0].strip()
        if first and first!=address:
            params["SingleLine"]=first
            r=requests.get(GEOCODER,params=params,timeout=30); r.raise_for_status()
            d=r.json(); cands=d.get("candidates",[])
    if not cands: raise RuntimeError(f"No Buncombe County address match found for: {address}")
    best=max(cands,key=lambda x:x.get("score",0))
    loc=best["location"]
    return float(loc["x"]),float(loc["y"]),best.get("address",address),best.get("score",0)

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
        "S-SSWR-MAIN":1,"S-SSWR-MAIN-TEXT":1,
        "S-SSWR-LATL":8,"S-SSWR-LATL-TEXT":8,
        "S-SSWR-MH":1,"S-SSWR-MH-TEXT":1,
        "S-SSWR-FM":6,"S-SSWR-FM-TEXT":6,
        "MSD-GIS-NOTE":8
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

def create_dxf(cx,cy,buffer_ft,selected,out,labels,logger):
    xmin,ymin,xmax,ymax=cx-buffer_ft,cy-buffer_ft,cx+buffer_ft,cy+buffer_ft
    logger(f"QUERY ENVELOPE: {xmin:.2f},{ymin:.2f},{xmax:.2f},{ymax:.2f}")

    doc=ezdxf.new("R2018"); doc.header["$INSUNITS"]=2
    if "MSDATTR" not in doc.appids: doc.appids.add("MSDATTR")
    make_layers(doc); msp=doc.modelspace()
    total=0

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

    note=f"BUNCOMBE COUNTY / MSD GIS REFERENCE DATA - EPSG:2264 - Retrieved {datetime.now():%Y-%m-%d %H:%M} - FIELD VERIFY UTILITIES."
    add_mtext(msp,note,(xmin,ymin-20),"MSD-GIS-NOTE",5.0)
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
        self.vars={n:tk.BooleanVar(value=(n!="Pressurized Mains")) for n in LAYERS}
        self.build()

    def build(self):
        p={"padx":8,"pady":6}
        ttk.Label(self,text="Buncombe MSD → DXF v4",font=("Segoe UI",18,"bold")).pack(anchor="w",padx=16,pady=(16,8))
        f=ttk.LabelFrame(self,text="Project Location"); f.pack(fill="x",padx=16,pady=6)
        ttk.Radiobutton(f,text="Address",variable=self.mode,value="address").grid(row=0,column=0,sticky="w",**p)
        ttk.Entry(f,textvariable=self.address,width=65).grid(row=0,column=1,columnspan=4,sticky="ew",**p)
        ttk.Radiobutton(f,text="State Plane center",variable=self.mode,value="xy").grid(row=1,column=0,sticky="w",**p)
        ttk.Label(f,text="X").grid(row=1,column=1); ttk.Entry(f,textvariable=self.x,width=16).grid(row=1,column=2,**p)
        ttk.Label(f,text="Y").grid(row=1,column=3); ttk.Entry(f,textvariable=self.y,width=16).grid(row=1,column=4,**p)
        ttk.Label(f,text="Buffer (ft)").grid(row=2,column=0,sticky="w",**p); ttk.Entry(f,textvariable=self.buffer,width=12).grid(row=2,column=1,sticky="w",**p)

        l=ttk.LabelFrame(self,text="MSD Data"); l.pack(fill="x",padx=16,pady=6)
        for i,(n,v) in enumerate(self.vars.items()):
            ttk.Checkbutton(l,text=n,variable=v).grid(row=0,column=i,sticky="w",**p)
        ttk.Checkbutton(l,text="Create full GIS labels",variable=self.labels).grid(row=1,column=0,columnspan=3,sticky="w",**p)

        o=ttk.LabelFrame(self,text="DXF Output"); o.pack(fill="x",padx=16,pady=6)
        ttk.Entry(o,textvariable=self.output).grid(row=0,column=0,sticky="ew",**p)
        ttk.Button(o,text="Browse / Save As",command=self.browse).grid(row=0,column=1,**p); o.columnconfigure(0,weight=1)

        ttk.Button(self,text="CREATE DXF",command=self.start).pack(pady=10)
        self.logbox=tk.Text(self,height=22,wrap="word"); self.logbox.pack(fill="both",expand=True,padx=16,pady=(0,16))
        self.log("Ready.")
        self.log("v4 defaults to known-good Walden State Plane center for geometry verification.")

    def browse(self):
        p=filedialog.asksaveasfilename(title="Save MSD DXF",defaultextension=".dxf",initialfile="Buncombe_MSD_Export_v4.dxf",filetypes=[("DXF","*.dxf")])
        if p:self.output.set(p)

    def log(self,s):
        self.logbox.insert("end",s+"\n"); self.logbox.see("end"); self.update_idletasks()

    def start(self):
        sel=[n for n,v in self.vars.items() if v.get()]
        if not sel:return messagebox.showerror(APP_NAME,"Select at least one layer.")
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
                    self.log(f"Searching Buncombe address locator: {q}")
                    cx,cy,match,score=geocode(q)
                    self.x.set(f"{cx:.2f}"); self.y.set(f"{cy:.2f}")
                    self.log(f"Matched: {match} (score {score})")
                    self.log(f"RESOLVED CENTER: X={cx:.2f}, Y={cy:.2f}")
                else:
                    cx,cy=float(self.x.get()),float(self.y.get())
                    self.log(f"MANUAL CENTER: X={cx:.2f}, Y={cy:.2f}")

                self.log(f"Buffer: {b:.0f} ft")
                n=create_dxf(cx,cy,b,sel,out,self.labels.get(),self.log)
                self.log(f"DONE — {n} entities")
                self.log(out)
                messagebox.showinfo(APP_NAME,f"DXF created.\n\n{out}\n\n{n} entities")
            except Exception as e:
                self.log(f"ERROR: {e}"); messagebox.showerror(APP_NAME,str(e))

        threading.Thread(target=work,daemon=True).start()

if __name__=="__main__": App().mainloop()
