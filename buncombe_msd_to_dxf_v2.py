
import os, math, threading, tkinter as tk
from tkinter import ttk, filedialog, messagebox
from datetime import datetime
from pathlib import Path

import requests
import ezdxf

APP_NAME = "Buncombe MSD to DXF v2"
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

def material(props):
    return val(props,"MATERIAL")

def diameter(props):
    v = val(props,"DIAMETER")
    if v is None: return None
    try:
        f=float(v)
        return f'{f:g}"'
    except: return str(v)

def address_candidates(address):
    # Buncombe County's own public locator; returns NC State Plane when outSR=2264.
    params = {
        "f":"json",
        "SingleLine": address,
        "outFields":"*",
        "outSR": SRID,
        "maxLocations": 10,
    }
    r=requests.get(GEOCODER, params=params, timeout=30)
    r.raise_for_status()
    d=r.json()
    if "error" in d: raise RuntimeError(str(d["error"]))
    return d.get("candidates", [])

def geocode(address):
    tries=[address]
    # Local locator often does better without city/state text.
    first=address.split(",")[0].strip()
    if first and first != address: tries.append(first)
    for q in tries:
        c=address_candidates(q)
        if c:
            best=max(c, key=lambda x:x.get("score",0))
            loc=best["location"]
            return float(loc["x"]),float(loc["y"]),best.get("address",q),best.get("score",0)
    raise RuntimeError(f"No Buncombe County address match found for: {address}")

def query_layer(layer_id, xmin,ymin,xmax,ymax):
    url=f"{BASE}/{layer_id}/query"
    params={
        "f":"geojson","where":"1=1",
        "geometry":f"{xmin},{ymin},{xmax},{ymax}",
        "geometryType":"esriGeometryEnvelope","inSR":SRID,
        "spatialRel":"esriSpatialRelIntersects","outFields":"*",
        "returnGeometry":"true","outSR":SRID
    }
    r=requests.get(url,params=params,timeout=60); r.raise_for_status()
    d=r.json()
    if "error" in d: raise RuntimeError(str(d["error"]))
    return d

def clip_segment(x1,y1,x2,y2,xmin,ymin,xmax,ymax):
    # Liang-Barsky rectangular clip
    dx=x2-x1; dy=y2-y1
    p=[-dx,dx,-dy,dy]; q=[x1-xmin,xmax-x1,y1-ymin,ymax-y1]
    u1,u2=0.0,1.0
    for pi,qi in zip(p,q):
        if pi==0:
            if qi<0:return None
        else:
            t=qi/pi
            if pi<0:
                if t>u2:return None
                if t>u1:u1=t
            else:
                if t<u1:return None
                if t<u2:u2=t
    return (x1+u1*dx,y1+u1*dy,x1+u2*dx,y1+u2*dy)

def clip_polyline(points,bbox):
    xmin,ymin,xmax,ymax=bbox
    parts=[]; current=[]
    for a,b in zip(points[:-1],points[1:]):
        c=clip_segment(a[0],a[1],b[0],b[1],xmin,ymin,xmax,ymax)
        if c is None:
            if len(current)>=2: parts.append(current)
            current=[]
            continue
        p1=(c[0],c[1]); p2=(c[2],c[3])
        if not current: current=[p1,p2]
        elif math.hypot(current[-1][0]-p1[0],current[-1][1]-p1[1])<0.01:
            current.append(p2)
        else:
            if len(current)>=2: parts.append(current)
            current=[p1,p2]
    if len(current)>=2: parts.append(current)
    return parts

def line_midpoint_angle(points):
    lengths=[]; total=0
    for a,b in zip(points[:-1],points[1:]):
        L=math.hypot(b[0]-a[0],b[1]-a[1]); lengths.append(L); total+=L
    target=total/2
    acc=0
    for (a,b),L in zip(zip(points[:-1],points[1:]),lengths):
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
    dia=diameter(p); mat=material(p)
    if name=="Gravity Mains":
        lines=[]
        main=" ".join(x for x in [dia,mat,"SANITARY SEWER"] if x)
        if main: lines.append(main)
        ids=" - ".join(x for x in [str(val(p,"FROMMH") or ""),str(val(p,"TOMH") or "")] if x)
        if ids: lines.append(f"MH {ids}")
        ups=fmt_num(val(p,"UPELEV")); dns=fmt_num(val(p,"DOWNELEV")); sl=fmt_num(val(p,"SLOPE"),2)
        elev="  ".join(x for x in [f"UP {ups}" if ups else "", f"DN {dns}" if dns else "", f"SLOPE {sl}" if sl else ""] if x)
        if elev: lines.append(elev)
        return "\n".join(lines)
    if name=="Laterals":
        return " ".join(x for x in [dia,mat,str(val(p,"SERVICETYPE") or ""),"SEWER LATERAL"] if x)
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

def all_xdata(p):
    out=[]
    for k,v in p.items():
        if v not in (None,""):
            s=f"{k}={v}"
            out.append((1000,s[:250]))
    return out[:100]

def add_mtext(msp,text,xy,layer,height=2.5,rotation=0):
    if not text:return
    e=msp.add_mtext(text.replace("\n","\\P"),dxfattribs={"layer":layer,"char_height":height,"rotation":rotation})
    e.set_location(xy)

def make_layers(doc):
    defs={
        "S-SSWR-MAIN":1, "S-SSWR-MAIN-TEXT":1,
        "S-SSWR-LATL":8, "S-SSWR-LATL-TEXT":8,
        "S-SSWR-MH":1, "S-SSWR-MH-TEXT":1,
        "S-SSWR-FM":6, "S-SSWR-FM-TEXT":6,
        "MSD-GIS-NOTE":8,
    }
    for n,c in defs.items():
        if n not in doc.layers: doc.layers.add(n,color=c)

def create_dxf(cx,cy,buffer_ft,selected,out,label_all,logger):
    bbox=(cx-buffer_ft,cy-buffer_ft,cx+buffer_ft,cy+buffer_ft)
    doc=ezdxf.new("R2018"); doc.header["$INSUNITS"]=2
    if "MSDATTR" not in doc.appids: doc.appids.add("MSDATTR")
    make_layers(doc); msp=doc.modelspace(); total=0

    for name in selected:
        cfg=LAYERS[name]; logger(f"Pulling {name}...")
        gj=query_layer(cfg["id"],*bbox); n=0
        for ft in gj.get("features",[]):
            g=ft.get("geometry") or {}; p=ft.get("properties") or {}; xd=all_xdata(p)
            if cfg["kind"]=="line":
                raw=[g.get("coordinates")] if g.get("type")=="LineString" else (g.get("coordinates") or [] if g.get("type")=="MultiLineString" else [])
                for pts0 in raw:
                    pts=[(float(q[0]),float(q[1])) for q in pts0]
                    for part in clip_polyline(pts,bbox):
                        e=msp.add_lwpolyline(part,dxfattribs={"layer":cfg["cad"]})
                        if xd:e.set_xdata("MSDATTR",xd)
                        if label_all:
                            x,y,a=line_midpoint_angle(part)
                            add_mtext(msp,line_label(name,p),(x,y+2.0),cfg["label"],2.5,a)
                        n+=1
            else:
                pts=[g.get("coordinates")] if g.get("type")=="Point" else []
                for q in pts:
                    x,y=float(q[0]),float(q[1])
                    if not (bbox[0]<=x<=bbox[2] and bbox[1]<=y<=bbox[3]): continue
                    e=msp.add_circle((x,y),1.5,dxfattribs={"layer":cfg["cad"]})
                    if xd:e.set_xdata("MSDATTR",xd)
                    if label_all:add_mtext(msp,mh_label(p),(x+3,y+3),cfg["label"],2.5,0)
                    n+=1
        logger(f"  {n} feature(s)"); total+=n

    note=f"BUNCOMBE COUNTY / MSD GIS REFERENCE DATA - EPSG:2264 - Retrieved {datetime.now():%Y-%m-%d %H:%M} - FIELD VERIFY UTILITIES."
    add_mtext(msp,note,(bbox[0],bbox[1]-15),"MSD-GIS-NOTE",2.5)
    Path(out).parent.mkdir(parents=True,exist_ok=True)
    doc.saveas(out)
    return total

class App(tk.Tk):
    def __init__(self):
        super().__init__(); self.title(APP_NAME); self.geometry("820x690"); self.minsize(760,620)
        self.mode=tk.StringVar(value="address")
        self.address=tk.StringVar(value="15 Walden Drive, Arden, NC")
        self.x=tk.StringVar(); self.y=tk.StringVar(); self.buffer=tk.StringVar(value="750")
        self.output=tk.StringVar(value="")
        self.labels=tk.BooleanVar(value=True)
        self.vars={n:tk.BooleanVar(value=(n!="Pressurized Mains")) for n in LAYERS}
        self.build()

    def build(self):
        p={"padx":8,"pady":6}
        ttk.Label(self,text="Buncombe MSD → DXF v2",font=("Segoe UI",18,"bold")).pack(anchor="w",padx=16,pady=(16,8))
        f=ttk.LabelFrame(self,text="Project Location"); f.pack(fill="x",padx=16,pady=6)
        ttk.Radiobutton(f,text="Address",variable=self.mode,value="address").grid(row=0,column=0,sticky="w",**p)
        ttk.Entry(f,textvariable=self.address,width=65).grid(row=0,column=1,columnspan=4,sticky="ew",**p)
        ttk.Radiobutton(f,text="State Plane center",variable=self.mode,value="xy").grid(row=1,column=0,sticky="w",**p)
        ttk.Label(f,text="X").grid(row=1,column=1); ttk.Entry(f,textvariable=self.x,width=16).grid(row=1,column=2,**p)
        ttk.Label(f,text="Y").grid(row=1,column=3); ttk.Entry(f,textvariable=self.y,width=16).grid(row=1,column=4,**p)
        ttk.Label(f,text="Buffer (ft)").grid(row=2,column=0,sticky="w",**p); ttk.Entry(f,textvariable=self.buffer,width=12).grid(row=2,column=1,sticky="w",**p)

        l=ttk.LabelFrame(self,text="MSD Data"); l.pack(fill="x",padx=16,pady=6)
        for i,(n,v) in enumerate(self.vars.items()): ttk.Checkbutton(l,text=n,variable=v).grid(row=0,column=i,sticky="w",**p)
        ttk.Checkbutton(l,text="Create full GIS labels",variable=self.labels).grid(row=1,column=0,columnspan=3,sticky="w",**p)

        o=ttk.LabelFrame(self,text="DXF Output"); o.pack(fill="x",padx=16,pady=6)
        ttk.Entry(o,textvariable=self.output).grid(row=0,column=0,sticky="ew",**p)
        ttk.Button(o,text="Browse / Save As",command=self.browse).grid(row=0,column=1,**p); o.columnconfigure(0,weight=1)
        ttk.Button(self,text="CREATE DXF",command=self.start).pack(pady=10)
        self.logbox=tk.Text(self,height=18,wrap="word"); self.logbox.pack(fill="both",expand=True,padx=16,pady=(0,16)); self.log("Ready.")

    def browse(self):
        p=filedialog.asksaveasfilename(title="Save MSD DXF",defaultextension=".dxf",initialfile="Buncombe_MSD_Export.dxf",filetypes=[("DXF","*.dxf")])
        if p:self.output.set(p)

    def log(self,s): self.logbox.insert("end",s+"\n"); self.logbox.see("end"); self.update_idletasks()

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
                    q=self.address.get().strip(); self.log(f"Searching Buncombe address locator: {q}")
                    cx,cy,match,score=geocode(q); self.log(f"Matched: {match} (score {score})"); self.log(f"Center: X={cx:.2f}, Y={cy:.2f}")
                else: cx,cy=float(self.x.get()),float(self.y.get())
                self.log(f"Buffer: {b:.0f} ft")
                n=create_dxf(cx,cy,b,sel,out,self.labels.get(),self.log)
                self.log(f"DONE — {n} feature(s)\n{out}")
                messagebox.showinfo(APP_NAME,f"DXF created.\n\n{out}\n\n{n} feature(s)")
            except Exception as e:
                self.log(f"ERROR: {e}"); messagebox.showerror(APP_NAME,str(e))
        threading.Thread(target=work,daemon=True).start()

if __name__=="__main__": App().mainloop()
