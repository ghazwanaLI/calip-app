#!/usr/bin/env python3
"""
نظام إدارة القياس والمعايرة
"""
import json, os, hashlib, uuid, base64, io
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from datetime import datetime
#!/usr/bin/env python3
"""
نظام إدارة القياس والمعايرة
"""
import json, os, hashlib, uuid, base64, io
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from datetime import datetime

PORT = int(os.environ.get("PORT", 8081))
DATABASE_URL = os.environ.get("DATABASE_URL", "")
USE_DB = bool(DATABASE_URL)

# ── Local DB ──
DB_FILE = "calib_db.json"

def hash_pw(pw):
    return hashlib.sha256(pw.encode()).hexdigest()

# ── PostgreSQL ──
def get_conn():
    import pg8000
    import urllib.parse
    r = urllib.parse.urlparse(DATABASE_URL)
    return pg8000.connect(
        host=r.hostname, port=r.port or 5432,
        database=r.path.lstrip("/"),
        user=r.username, password=r.password,
        ssl_context=True
    )

def init_pg():
    conn = get_conn(); cur = conn.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS calib_store (key TEXT PRIMARY KEY, value TEXT)""")
    cur.execute("""CREATE TABLE IF NOT EXISTS calib_files (key TEXT PRIMARY KEY, name TEXT, data TEXT, mime TEXT)""")
    cur.execute("""CREATE TABLE IF NOT EXISTS calib_logs (
        id SERIAL PRIMARY KEY, user_name TEXT, user_fullname TEXT,
        action TEXT, details TEXT, ip TEXT, created_at TIMESTAMP DEFAULT NOW()
    )""")
    conn.commit()
    cur.execute("SELECT value FROM calib_store WHERE key='data'")
    row = cur.fetchone()
    if not row:
        cur.execute("INSERT INTO calib_store VALUES ('data', %s)", [json.dumps(default_db(), ensure_ascii=False)])
        conn.commit()
    cur.close(); conn.close()

def pg_load():
    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT value FROM calib_store WHERE key='data'")
    row = cur.fetchone(); cur.close(); conn.close()
    return json.loads(row[0])

def pg_save(db):
    conn = get_conn(); cur = conn.cursor()
    cur.execute("UPDATE calib_store SET value=%s WHERE key='data'", [json.dumps(db, ensure_ascii=False)])
    conn.commit(); cur.close(); conn.close()

def pg_save_file(key, name, data, mime):
    conn = get_conn(); cur = conn.cursor()
    cur.execute("""INSERT INTO calib_files (key,name,data,mime) VALUES(%s,%s,%s,%s)
        ON CONFLICT(key) DO UPDATE SET name=%s,data=%s,mime=%s""",
        [key,name,data,mime,name,data,mime])
    conn.commit(); cur.close(); conn.close()

def pg_load_file(key):
    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT name,data,mime FROM calib_files WHERE key=%s",[key])
    row = cur.fetchone(); cur.close(); conn.close()
    return {"name":row[0],"data":row[1],"mime":row[2]} if row else None

def pg_del_file(key):
    conn = get_conn(); cur = conn.cursor()
    cur.execute("DELETE FROM calib_files WHERE key=%s",[key])
    conn.commit(); cur.close(); conn.close()

def pg_add_log(user, action, details, ip=""):
    try:
        conn = get_conn(); cur = conn.cursor()
        cur.execute("INSERT INTO calib_logs(user_name,user_fullname,action,details,ip) VALUES(%s,%s,%s,%s,%s)",
            [user.get("username",""),user.get("fullname",""),action,details,ip])
        conn.commit(); cur.close(); conn.close()
    except: pass

def pg_get_logs(limit=100):
    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT id,user_name,user_fullname,action,details,ip,created_at FROM calib_logs ORDER BY created_at DESC LIMIT %s",[limit])
    rows = cur.fetchall(); cur.close(); conn.close()
    return [{"id":r[0],"username":r[1],"fullname":r[2],"action":r[3],"details":r[4],"ip":r[5],"time":str(r[6])} for r in rows]

# ── Local DB ──
def load_db():
    if USE_DB: return pg_load()
    if os.path.exists(DB_FILE):
        with open(DB_FILE,"r",encoding="utf-8") as f: return json.load(f)
    db = default_db(); save_db(db); return db

def save_db(db):
    if USE_DB: pg_save(db); return
    with open(DB_FILE,"w",encoding="utf-8") as f: json.dump(db,f,ensure_ascii=False,indent=2)

def save_file(key,name,data,mime):
    if USE_DB: pg_save_file(key,name,data,mime); return
    db = load_db(); db["files"][key]={"name":name,"data":data,"mime":mime}; save_db(db)

def load_file(key):
    if USE_DB: return pg_load_file(key)
    return load_db()["files"].get(key)

def del_file(key):
    if USE_DB: pg_del_file(key); return
    db = load_db(); db["files"].pop(key,None); save_db(db)

def add_log(user,action,details,ip=""):
    if USE_DB: pg_add_log(user,action,details,ip)

def get_logs(limit=100):
    if USE_DB: return pg_get_logs(limit)
    return []

def default_db():
    return {
        "devices": [],
        "stations": [],
        "pumps": [],
        "users": [{
            "id":1,"fullname":"مدير النظام","username":"admin",
            "password":hash_pw("admin123"),"role":"admin","active":True,
            "perms":{"view":True,"edit_devices":True,"del_devices":True,"edit_pumps":True,"del_pumps":True,"files_devices":True,"files_pumps":True,"export_devices":True,"export_pumps":True}
        }],
        "files":{},
        "next_device_id":1,
        "next_station_id":1,
        "next_pump_id":1,
        "tanks":[],
        "next_tank_id":1,
        "next_user_id":2
    }

sessions = {}

DEVICE_TYPES = {
    "balance":    {"label":"ميزان",     "icon":"⚖️",  "color":"#6366f1"},
    "pressure":   {"label":"ضغط",      "icon":"🔴",  "color":"#ef4444"},
    "temperature":{"label":"حرارة",    "icon":"🌡️", "color":"#f97316"},
    "flow":       {"label":"تدفق",     "icon":"🌊",  "color":"#3b82f6"},
    "volume":     {"label":"حجم",      "icon":"🧪",  "color":"#10b981"},
    "electrical": {"label":"كهرباء",   "icon":"⚡",  "color":"#f59e0b"},
    "other":      {"label":"أخرى",     "icon":"📏",  "color":"#8b5cf6"},
}

class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args): pass

    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type","application/json; charset=utf-8")
        self.send_header("Content-Length",len(body))
        self.send_header("Access-Control-Allow-Origin","*")
        self.send_header("Cache-Control","no-cache")
        self.end_headers(); self.wfile.write(body)

    def send_html(self, content):
        body = content.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type","text/html; charset=utf-8")
        self.send_header("Content-Length",len(body))
        self.send_header("Cache-Control","no-cache, no-store, must-revalidate")
        self.end_headers(); self.wfile.write(body)

    def read_body(self):
        length = int(self.headers.get("Content-Length",0))
        return json.loads(self.rfile.read(length)) if length else {}

    def get_token(self):
        return self.headers.get("Authorization","").replace("Bearer ","").strip()

    def get_user(self):
        uid = sessions.get(self.get_token())
        if not uid: return None
        return next((u for u in load_db()["users"] if u["id"]==uid),None)

    def require_auth(self):
        u = self.get_user()
        if not u: self.send_json({"error":"غير مصرح"},401)
        return u

    def can(self,user,perm):
        if user["role"]=="admin": return True
        return bool(user.get("perms",{}).get(perm))

    def ip(self):
        return self.headers.get("X-Forwarded-For",self.client_address[0])

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin","*")
        self.send_header("Access-Control-Allow-Methods","GET,POST,PUT,DELETE,OPTIONS")
        self.send_header("Access-Control-Allow-Headers","Content-Type,Authorization")
        self.end_headers()

    def do_GET(self):
        p = urlparse(self.path).path.rstrip("/")

        if p in ("","/"): 
            html_file = os.path.join(os.path.dirname(os.path.abspath(__file__)),"calib_index.html")
            with open(html_file,"r",encoding="utf-8") as f: self.send_html(f.read()); return

        u = self.require_auth()
        if not u: return

        if p=="/api/devices":
            self.send_json({"ok":True,"devices":load_db()["devices"]})

        elif p=="/api/stations-local":
            self.send_json({"ok":True,"stations":load_db().get("stations",[])})

        elif p=="/api/pumps":
            self.send_json({"ok":True,"pumps":load_db().get("pumps",[])})

        elif p=="/api/tanks":
            self.send_json({"ok":True,"tanks":load_db().get("tanks",[])})

        elif p=="/api/device-types":
            self.send_json({"ok":True,"types":DEVICE_TYPES})

        elif p=="/api/me":
            self.send_json({"ok":True,"user":{k:v for k,v in u.items() if k!="password"}})

        elif p=="/api/users":
            if u["role"]!="admin": self.send_json({"error":"غير مصرح"},403); return
            safe=[{k:v for k,v in x.items() if k!="password"} for x in load_db()["users"]]
            self.send_json({"ok":True,"users":safe})

        elif p=="/api/logs":
            if u["role"]!="admin": self.send_json({"error":"غير مصرح"},403); return
            qs = parse_qs(urlparse(self.path).query)
            limit = int(qs.get("limit",["100"])[0])
            self.send_json({"ok":True,"logs":get_logs(limit)})

        elif p=="/api/import-stations":
            if not self.can(u,"edit_devices") and not self.can(u,"edit"): self.send_json({"error":"لا صلاحية"},403); return
            body=self.read_body()
            excel_b64=body.get("data","")
            if not excel_b64: self.send_json({"error":"لا يوجد ملف"},400); return
            try:
                import openpyxl
                excel_bytes=base64.b64decode(excel_b64)
                wb=openpyxl.load_workbook(io.BytesIO(excel_bytes))
                ws=wb.active
                db=load_db()
                if "stations" not in db: db["stations"]=[]
                if "next_station_id" not in db: db["next_station_id"]=1
                added=0; skipped=0
                existing={s["name"].strip() for s in db["stations"]}
                for row in ws.iter_rows(min_row=2,values_only=True):
                    name=None
                    for cell in row:
                        if cell and isinstance(cell,str) and cell.strip():
                            name=cell.strip(); break
                    if not name: skipped+=1; continue
                    if name in existing: skipped+=1; continue
                    sid=db["next_station_id"]; db["next_station_id"]+=1
                    db["stations"].append({"id":sid,"name":name,"location":(str(row[1]) if len(row)>1 and row[1] else ""),"status":"active","added_by":u["fullname"],"added_at":datetime.now().strftime("%Y-%m-%d")})
                    existing.add(name); added+=1
                save_db(db)
                add_log(u,"استيراد محطات",f"استورد {added} محطة",self.ip())
                self.send_json({"ok":True,"added":added,"skipped":skipped})
            except Exception as e:
                self.send_json({"error":f"خطأ: {str(e)}"},400)

        elif p.startswith("/api/files/"):
            if not self.can(u,"files"): self.send_json({"error":"لا صلاحية"},403); return
            parts = p.split("/"); key="/".join(parts[3:])
            self.send_json({"ok":True,"file":load_file(key)})

        else:
            self.send_json({"error":"غير موجود"},404)

    def do_POST(self):
        p = urlparse(self.path).path.rstrip("/")

        if p=="/api/login":
            body = self.read_body(); db = load_db()
            user = next((u for u in db["users"]
                if u["username"]==body.get("username")
                and u["password"]==hash_pw(body.get("password",""))
                and u.get("active",True)),None)
            if not user: self.send_json({"error":"اسم المستخدم أو كلمة المرور غير صحيحة"},401); return
            token = str(uuid.uuid4()); sessions[token]=user["id"]
            add_log(user,"تسجيل دخول",f"دخل: {user['fullname']}",self.ip())
            self.send_json({"ok":True,"token":token,"user":{k:v for k,v in user.items() if k!="password"}}); return

        if p=="/api/logout":
            u2=self.get_user()
            if u2: add_log(u2,"تسجيل خروج",f"خرج: {u2['fullname']}",self.ip())
            sessions.pop(self.get_token(),None); self.send_json({"ok":True}); return

        u = self.require_auth()
        if not u: return

        if p=="/api/stations-local":
            if not self.can(u,"edit_devices") and not self.can(u,"edit"): self.send_json({"error":"لا صلاحية"},403); return
            body=self.read_body()
            if not body.get("name"): self.send_json({"error":"اسم المحطة مطلوب"},400); return
            db=load_db()
            if "stations" not in db: db["stations"]=[]
            if "next_station_id" not in db: db["next_station_id"]=1
            sid=db["next_station_id"]; db["next_station_id"]+=1
            now=datetime.now().strftime("%Y-%m-%d %H:%M")
            station={
                "id":sid,
                "name":body.get("name",""),
                "location":body.get("location",""),
                "notes":body.get("notes",""),
                "status":body.get("status","active"),
                "added_by":u["fullname"],
                "added_at":now,
            }
            db["stations"].append(station); save_db(db)
            add_log(u,"إضافة محطة",f"أضاف محطة: {station['name']}",self.ip())
            self.send_json({"ok":True,"station":station})

        elif p=="/api/pumps":
            if not self.can(u,"edit"): self.send_json({"error":"لا صلاحية"},403); return
            body=self.read_body()
            if not body.get("name"): self.send_json({"error":"اسم المضخة مطلوب"},400); return
            db=load_db()
            if "pumps" not in db: db["pumps"]=[]
            if "next_pump_id" not in db: db["next_pump_id"]=1
            pid=db["next_pump_id"]; db["next_pump_id"]+=1
            now=datetime.now().strftime("%Y-%m-%d %H:%M")
            pump={
                "id":pid,
                "pump_no":body.get("pump_no",""),
                "name":body.get("name",""),
                "product":body.get("product",""),
                "manufacturer":body.get("manufacturer",""),
                "location":body.get("location",""),
                "district":body.get("district",""),
                "last_calib":body.get("last_calib",""),
                "next_calib":body.get("next_calib",""),
                "notes":body.get("notes",""),
                "status":body.get("status","active"),
                "hose1":{
                    "reading_before":body.get("hose1_rb",""),
                    "reading_after":body.get("hose1_ra",""),
                    "deviation":body.get("hose1_dev",""),
                    "result":body.get("hose1_res",""),
                },
                "hose2":{
                    "reading_before":body.get("hose2_rb",""),
                    "reading_after":body.get("hose2_ra",""),
                    "deviation":body.get("hose2_dev",""),
                    "result":body.get("hose2_res",""),
                },
                "created_by":u["fullname"],
                "updated_by":u["fullname"],
                "updated_at":now,
            }
            db["pumps"].append(pump); save_db(db)
            add_log(u,"إضافة مضخة",f"أضاف مضخة: {pump['name']} ({pump['pump_no']})",self.ip())
            self.send_json({"ok":True,"pump":pump})

        elif p=="/api/devices":
            if not self.can(u,"edit"): self.send_json({"error":"لا صلاحية"},403); return
            body=self.read_body()
            if not body.get("name"): self.send_json({"error":"اسم الجهاز مطلوب"},400); return
            db=load_db(); sid=db["next_device_id"]; db["next_device_id"]+=1
            now=datetime.now().strftime("%Y-%m-%d %H:%M")
            device={
                "id":sid,
                "device_no":body.get("device_no",""),
                "name":body.get("name",""),
                "type":body.get("type","other"),
                "manufacturer":body.get("manufacturer",""),
                "location":body.get("location",""),
                "district":body.get("district",""),
                "last_calib":body.get("last_calib",""),
                "next_calib":body.get("next_calib",""),
                "result":body.get("result",""),
                "range":body.get("range",""),
                "unit":body.get("unit",""),
                "serial_no":body.get("serial_no",""),
                "notes":body.get("notes",""),
                "status":body.get("status","active"),
                "created_by":u["fullname"],
                "updated_by":u["fullname"],
                "updated_at":now,
            }
            db["devices"].append(device); save_db(db)
            add_log(u,"إضافة جهاز",f"أضاف: {device['name']} ({device['device_no']})",self.ip())
            self.send_json({"ok":True,"device":device})

        elif p=="/api/users":
            if u["role"]!="admin": self.send_json({"error":"غير مصرح"},403); return
            body=self.read_body(); db=load_db()
            if any(x["username"]==body.get("username") for x in db["users"]):
                self.send_json({"error":"اسم المستخدم مستخدم بالفعل"},400); return
            uid=db["next_user_id"]; db["next_user_id"]+=1
            role=body.get("role","viewer")
            new_user={"id":uid,"fullname":body.get("fullname",""),"username":body.get("username",""),
                "password":hash_pw(body.get("password","")),"role":role,"active":True,"location":body.get("location",""),
                "district":body.get("district",""),
                "perms":{"view":True,"edit_devices":True,"del_devices":True,"edit_pumps":True,"del_pumps":True,"files_devices":True,"files_pumps":True,"export_devices":True,"export_pumps":True}
                    if role=="admin" else body.get("perms",{"view":True,"edit_devices":False,"del_devices":False,"edit_pumps":False,"del_pumps":False,"files_devices":False,"files_pumps":False,"export_devices":False,"export_pumps":False})}
            db["users"].append(new_user); save_db(db)
            add_log(u,"إضافة مستخدم",f"أضاف مستخدم: {new_user['fullname']}",self.ip())
            self.send_json({"ok":True,"user":{k:v for k,v in new_user.items() if k!="password"}})

        elif p.startswith("/api/files/"):
            if not self.can(u,"files"): self.send_json({"error":"لا صلاحية"},403); return
            parts=p.split("/"); key="/".join(parts[3:])
            try:
                body=self.read_body()
                save_file(key,body.get("name",""),body.get("data",""),body.get("mime",""))
                add_log(u,"رفع ملف",f"رفع: {body.get('name','')}",self.ip())
                self.send_json({"ok":True})
            except Exception as e:
                self.send_json({"error":f"خطأ حفظ الملف: {str(e)}"},500)
        else:
            self.send_json({"error":"غير موجود"},404)

    def do_PUT(self):
        p = urlparse(self.path).path.rstrip("/")
        u = self.require_auth()
        if not u: return

        if p.startswith("/api/stations-local/"):
            if not self.can(u,"edit_devices") and not self.can(u,"edit"): self.send_json({"error":"لا صلاحية"},403); return
            sid=int(p.split("/")[-1]); body=self.read_body(); db=load_db()
            if "stations" not in db: db["stations"]=[]
            idx=next((i for i,s in enumerate(db["stations"]) if s["id"]==sid),None)
            if idx is None: self.send_json({"error":"غير موجود"},404); return
            for f in ["name","location","notes","status"]:
                if f in body: db["stations"][idx][f]=body[f]
            save_db(db)
            add_log(u,"تعديل محطة",f"عدّل محطة: {db['stations'][idx]['name']}",self.ip())
            self.send_json({"ok":True,"station":db["stations"][idx]})

        elif p.startswith("/api/pumps/"):
            if not self.can(u,"edit"): self.send_json({"error":"لا صلاحية"},403); return
            pid=int(p.split("/")[-1]); body=self.read_body(); db=load_db()
            if "pumps" not in db: db["pumps"]=[]
            idx=next((i for i,d in enumerate(db["pumps"]) if d["id"]==pid),None)
            if idx is None: self.send_json({"error":"غير موجود"},404); return
            for f in ["pump_no","name","product","manufacturer","location","district","last_calib","next_calib","notes","status"]:
                if f in body: db["pumps"][idx][f]=body[f]
            db["pumps"][idx]["hose1"]={
                "reading_before":body.get("hose1_rb",""),
                "reading_after":body.get("hose1_ra",""),
                "deviation":body.get("hose1_dev",""),
                "result":body.get("hose1_res",""),
            }
            db["pumps"][idx]["hose2"]={
                "reading_before":body.get("hose2_rb",""),
                "reading_after":body.get("hose2_ra",""),
                "deviation":body.get("hose2_dev",""),
                "result":body.get("hose2_res",""),
            }
            db["pumps"][idx]["updated_by"]=u["fullname"]
            db["pumps"][idx]["updated_at"]=datetime.now().strftime("%Y-%m-%d %H:%M")
            save_db(db)
            add_log(u,"تعديل مضخة",f"عدّل مضخة: {db['pumps'][idx]['name']}",self.ip())
            self.send_json({"ok":True,"pump":db["pumps"][idx]})

        elif p.startswith("/api/devices/"):
            if not self.can(u,"edit"): self.send_json({"error":"لا صلاحية"},403); return
            did=int(p.split("/")[-1]); body=self.read_body(); db=load_db()
            idx=next((i for i,d in enumerate(db["devices"]) if d["id"]==did),None)
            if idx is None: self.send_json({"error":"غير موجود"},404); return
            fields=["device_no","name","type","manufacturer","location","last_calib",
                    "next_calib","result","range","unit","serial_no","notes","status"]
            for f in fields:
                if f in body: db["devices"][idx][f]=body[f]
            db["devices"][idx]["updated_by"]=u["fullname"]
            db["devices"][idx]["updated_at"]=datetime.now().strftime("%Y-%m-%d %H:%M")
            save_db(db)
            add_log(u,"تعديل جهاز",f"عدّل: {db['devices'][idx]['name']}",self.ip())
            self.send_json({"ok":True,"device":db["devices"][idx]})

        elif p.startswith("/api/users/"):
            if u["role"]!="admin": self.send_json({"error":"غير مصرح"},403); return
            uid=int(p.split("/")[-1]); body=self.read_body(); db=load_db()
            idx=next((i for i,x in enumerate(db["users"]) if x["id"]==uid),None)
            if idx is None: self.send_json({"error":"غير موجود"},404); return
            if "password" in body and body["password"]:
                if "old_password" in body:
                    if db["users"][idx]["password"]!=hash_pw(body["old_password"]):
                        self.send_json({"error":"كلمة المرور الحالية غير صحيحة"},400); return
                db["users"][idx]["password"]=hash_pw(body["password"])
                add_log(u,"تغيير كلمة المرور",f"غيّر كلمة مرور id={uid}",self.ip())
            for f in ["fullname","username","role","active","perms","location"]:
                if f in body: db["users"][idx][f]=body[f]
            save_db(db)
            self.send_json({"ok":True,"user":{k:v for k,v in db["users"][idx].items() if k!="password"}})
        else:
            self.send_json({"error":"غير موجود"},404)

    def do_DELETE(self):
        p = urlparse(self.path).path.rstrip("/")
        u = self.require_auth()
        if not u: return

        if p.startswith("/api/stations-local/"):
            if not self.can(u,"edit_devices") and not self.can(u,"edit"): self.send_json({"error":"لا صلاحية"},403); return
            sid=int(p.split("/")[-1]); body=self.read_body(); db=load_db()
            if "stations" not in db: db["stations"]=[]
            idx=next((i for i,s in enumerate(db["stations"]) if s["id"]==sid),None)
            if idx is None: self.send_json({"error":"غير موجود"},404); return
            for f in ["name","location","notes","status"]:
                if f in body: db["stations"][idx][f]=body[f]
            save_db(db)
            add_log(u,"تعديل محطة",f"عدّل محطة: {db['stations'][idx]['name']}",self.ip())
            self.send_json({"ok":True,"station":db["stations"][idx]})

        elif p.startswith("/api/pumps/"):
            if not self.can(u,"del"): self.send_json({"error":"لا صلاحية حذف"},403); return
            pid=int(p.split("/")[-1]); db=load_db()
            if "pumps" not in db: db["pumps"]=[]
            deleted=next((d["name"] for d in db["pumps"] if d["id"]==pid),str(pid))
            db["pumps"]=[d for d in db["pumps"] if d["id"]!=pid]
            del_file(f"worklog_{pid}"); save_db(db)
            add_log(u,"حذف مضخة",f"حذف مضخة: {deleted}",self.ip())
            self.send_json({"ok":True})

        elif p.startswith("/api/devices/"):
            if not self.can(u,"del"): self.send_json({"error":"لا صلاحية حذف"},403); return
            did=int(p.split("/")[-1]); db=load_db()
            deleted=next((d["name"] for d in db["devices"] if d["id"]==did),str(did))
            db["devices"]=[d for d in db["devices"] if d["id"]!=did]
            del_file(f"cert_{did}"); del_file(f"report_{did}")
            save_db(db)
            add_log(u,"حذف جهاز",f"حذف: {deleted}",self.ip())
            self.send_json({"ok":True})

        elif p.startswith("/api/users/"):
            if u["role"]!="admin": self.send_json({"error":"غير مصرح"},403); return
            uid=int(p.split("/")[-1])
            if uid==u["id"]: self.send_json({"error":"لا يمكن حذف حسابك"},400); return
            db=load_db()
            deleted=next((x.get("fullname","") for x in db["users"] if x["id"]==uid),"")
            db["users"]=[x for x in db["users"] if x["id"]!=uid]
            save_db(db)
            add_log(u,"حذف مستخدم",f"حذف: {deleted}",self.ip())
            self.send_json({"ok":True})

        elif p.startswith("/api/files/"):
            if not self.can(u,"files"): self.send_json({"error":"لا صلاحية"},403); return
            parts=p.split("/"); key="/".join(parts[3:])
            del_file(key); self.send_json({"ok":True})
        else:
            self.send_json({"error":"غير موجود"},404)

if __name__=="__main__":
    if USE_DB:
        print("⏳ تهيئة قاعدة البيانات...")
        init_pg()
    server=HTTPServer(("0.0.0.0",PORT),Handler)
    print(f"\n  📐  نظام إدارة القياس والمعايرة")
    print(f"  ✅  السيرفر يعمل على المنفذ {PORT}")
    print(f"  🌐  http://localhost:{PORT}\n")
    try: server.serve_forever()
    except KeyboardInterrupt: server.shutdown()
PORT = int(os.environ.get("PORT", 8081))
DATABASE_URL = os.environ.get("DATABASE_URL", "")
USE_DB = bool(DATABASE_URL)

# ── Local DB ──
DB_FILE = "calib_db.json"

def hash_pw(pw):
    return hashlib.sha256(pw.encode()).hexdigest()

# ── PostgreSQL ──
def get_conn():
    import pg8000
    import urllib.parse
    r = urllib.parse.urlparse(DATABASE_URL)
    return pg8000.connect(
        host=r.hostname, port=r.port or 5432,
        database=r.path.lstrip("/"),
        user=r.username, password=r.password,
        ssl_context=True
    )

def init_pg():
    conn = get_conn(); cur = conn.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS calib_store (key TEXT PRIMARY KEY, value TEXT)""")
    cur.execute("""CREATE TABLE IF NOT EXISTS calib_files (key TEXT PRIMARY KEY, name TEXT, data TEXT, mime TEXT)""")
    cur.execute("""CREATE TABLE IF NOT EXISTS calib_logs (
        id SERIAL PRIMARY KEY, user_name TEXT, user_fullname TEXT,
        action TEXT, details TEXT, ip TEXT, created_at TIMESTAMP DEFAULT NOW()
    )""")
    conn.commit()
    cur.execute("SELECT value FROM calib_store WHERE key='data'")
    row = cur.fetchone()
    if not row:
        cur.execute("INSERT INTO calib_store VALUES ('data', %s)", [json.dumps(default_db(), ensure_ascii=False)])
        conn.commit()
    cur.close(); conn.close()

def pg_load():
    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT value FROM calib_store WHERE key='data'")
    row = cur.fetchone(); cur.close(); conn.close()
    return json.loads(row[0])

def pg_save(db):
    conn = get_conn(); cur = conn.cursor()
    cur.execute("UPDATE calib_store SET value=%s WHERE key='data'", [json.dumps(db, ensure_ascii=False)])
    conn.commit(); cur.close(); conn.close()

def pg_save_file(key, name, data, mime):
    conn = get_conn(); cur = conn.cursor()
    cur.execute("""INSERT INTO calib_files (key,name,data,mime) VALUES(%s,%s,%s,%s)
        ON CONFLICT(key) DO UPDATE SET name=%s,data=%s,mime=%s""",
        [key,name,data,mime,name,data,mime])
    conn.commit(); cur.close(); conn.close()

def pg_load_file(key):
    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT name,data,mime FROM calib_files WHERE key=%s",[key])
    row = cur.fetchone(); cur.close(); conn.close()
    return {"name":row[0],"data":row[1],"mime":row[2]} if row else None

def pg_del_file(key):
    conn = get_conn(); cur = conn.cursor()
    cur.execute("DELETE FROM calib_files WHERE key=%s",[key])
    conn.commit(); cur.close(); conn.close()

def pg_add_log(user, action, details, ip=""):
    try:
        conn = get_conn(); cur = conn.cursor()
        cur.execute("INSERT INTO calib_logs(user_name,user_fullname,action,details,ip) VALUES(%s,%s,%s,%s,%s)",
            [user.get("username",""),user.get("fullname",""),action,details,ip])
        conn.commit(); cur.close(); conn.close()
    except: pass

def pg_get_logs(limit=100):
    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT id,user_name,user_fullname,action,details,ip,created_at FROM calib_logs ORDER BY created_at DESC LIMIT %s",[limit])
    rows = cur.fetchall(); cur.close(); conn.close()
    return [{"id":r[0],"username":r[1],"fullname":r[2],"action":r[3],"details":r[4],"ip":r[5],"time":str(r[6])} for r in rows]

# ── Local DB ──
def load_db():
    if USE_DB: return pg_load()
    if os.path.exists(DB_FILE):
        with open(DB_FILE,"r",encoding="utf-8") as f: return json.load(f)
    db = default_db(); save_db(db); return db

def save_db(db):
    if USE_DB: pg_save(db); return
    with open(DB_FILE,"w",encoding="utf-8") as f: json.dump(db,f,ensure_ascii=False,indent=2)

def save_file(key,name,data,mime):
    if USE_DB: pg_save_file(key,name,data,mime); return
    db = load_db(); db["files"][key]={"name":name,"data":data,"mime":mime}; save_db(db)

def load_file(key):
    if USE_DB: return pg_load_file(key)
    return load_db()["files"].get(key)

def del_file(key):
    if USE_DB: pg_del_file(key); return
    db = load_db(); db["files"].pop(key,None); save_db(db)

def add_log(user,action,details,ip=""):
    if USE_DB: pg_add_log(user,action,details,ip)

def get_logs(limit=100):
    if USE_DB: return pg_get_logs(limit)
    return []

def default_db():
    return {
        "devices": [],
        "pumps": [],
        "users": [{
            "id":1,"fullname":"مدير النظام","username":"admin",
            "password":hash_pw("admin123"),"role":"admin","active":True,
            "perms":{"view":True,"edit_devices":True,"del_devices":True,"edit_pumps":True,"del_pumps":True,"files_devices":True,"files_pumps":True,"export_devices":True,"export_pumps":True}
        }],
        "files":{},
        "next_device_id":1,
        "next_pump_id":1,
        "next_user_id":2
    }

sessions = {}

DEVICE_TYPES = {
    "balance":    {"label":"ميزان",     "icon":"⚖️",  "color":"#6366f1"},
    "pressure":   {"label":"ضغط",      "icon":"🔴",  "color":"#ef4444"},
    "temperature":{"label":"حرارة",    "icon":"🌡️", "color":"#f97316"},
    "flow":       {"label":"تدفق",     "icon":"🌊",  "color":"#3b82f6"},
    "volume":     {"label":"حجم",      "icon":"🧪",  "color":"#10b981"},
    "electrical": {"label":"كهرباء",   "icon":"⚡",  "color":"#f59e0b"},
    "other":      {"label":"أخرى",     "icon":"📏",  "color":"#8b5cf6"},
}

class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args): pass

    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type","application/json; charset=utf-8")
        self.send_header("Content-Length",len(body))
        self.send_header("Access-Control-Allow-Origin","*")
        self.send_header("Cache-Control","no-cache")
        self.end_headers(); self.wfile.write(body)

    def send_html(self, content):
        body = content.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type","text/html; charset=utf-8")
        self.send_header("Content-Length",len(body))
        self.send_header("Cache-Control","no-cache, no-store, must-revalidate")
        self.end_headers(); self.wfile.write(body)

    def read_body(self):
        length = int(self.headers.get("Content-Length",0))
        return json.loads(self.rfile.read(length)) if length else {}

    def get_token(self):
        return self.headers.get("Authorization","").replace("Bearer ","").strip()

    def get_user(self):
        uid = sessions.get(self.get_token())
        if not uid: return None
        return next((u for u in load_db()["users"] if u["id"]==uid),None)

    def require_auth(self):
        u = self.get_user()
        if not u: self.send_json({"error":"غير مصرح"},401)
        return u

    def can(self,user,perm):
        if user["role"]=="admin": return True
        return bool(user.get("perms",{}).get(perm))

    def ip(self):
        return self.headers.get("X-Forwarded-For",self.client_address[0])

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin","*")
        self.send_header("Access-Control-Allow-Methods","GET,POST,PUT,DELETE,OPTIONS")
        self.send_header("Access-Control-Allow-Headers","Content-Type,Authorization")
        self.end_headers()

    def do_GET(self):
        p = urlparse(self.path).path.rstrip("/")

        if p in ("","/"): 
            html_file = os.path.join(os.path.dirname(os.path.abspath(__file__)),"calib_index.html")
            with open(html_file,"r",encoding="utf-8") as f: self.send_html(f.read()); return

        u = self.require_auth()
        if not u: return

        if p=="/api/devices":
            self.send_json({"ok":True,"devices":load_db()["devices"]})

        elif p=="/api/pumps":
            self.send_json({"ok":True,"pumps":load_db().get("pumps",[])})

        elif p=="/api/device-types":
            self.send_json({"ok":True,"types":DEVICE_TYPES})

        elif p=="/api/me":
            self.send_json({"ok":True,"user":{k:v for k,v in u.items() if k!="password"}})

        elif p=="/api/users":
            if u["role"]!="admin": self.send_json({"error":"غير مصرح"},403); return
            safe=[{k:v for k,v in x.items() if k!="password"} for x in load_db()["users"]]
            self.send_json({"ok":True,"users":safe})

        elif p=="/api/logs":
            if u["role"]!="admin": self.send_json({"error":"غير مصرح"},403); return
            qs = parse_qs(urlparse(self.path).query)
            limit = int(qs.get("limit",["100"])[0])
            self.send_json({"ok":True,"logs":get_logs(limit)})

        elif p.startswith("/api/files/"):
            if not self.can(u,"files"): self.send_json({"error":"لا صلاحية"},403); return
            parts = p.split("/"); key="/".join(parts[3:])
            self.send_json({"ok":True,"file":load_file(key)})

        else:
            self.send_json({"error":"غير موجود"},404)

    def do_POST(self):
        p = urlparse(self.path).path.rstrip("/")

        if p=="/api/login":
            body = self.read_body(); db = load_db()
            user = next((u for u in db["users"]
                if u["username"]==body.get("username")
                and u["password"]==hash_pw(body.get("password",""))
                and u.get("active",True)),None)
            if not user: self.send_json({"error":"اسم المستخدم أو كلمة المرور غير صحيحة"},401); return
            token = str(uuid.uuid4()); sessions[token]=user["id"]
            add_log(user,"تسجيل دخول",f"دخل: {user['fullname']}",self.ip())
            self.send_json({"ok":True,"token":token,"user":{k:v for k,v in user.items() if k!="password"}}); return

        if p=="/api/logout":
            u2=self.get_user()
            if u2: add_log(u2,"تسجيل خروج",f"خرج: {u2['fullname']}",self.ip())
            sessions.pop(self.get_token(),None); self.send_json({"ok":True}); return

        u = self.require_auth()
        if not u: return

        if p=="/api/pumps":
            if not self.can(u,"edit"): self.send_json({"error":"لا صلاحية"},403); return
            body=self.read_body()
            if not body.get("name"): self.send_json({"error":"اسم المضخة مطلوب"},400); return
            db=load_db()
            if "pumps" not in db: db["pumps"]=[]
            if "next_pump_id" not in db: db["next_pump_id"]=1
            pid=db["next_pump_id"]; db["next_pump_id"]+=1
            now=datetime.now().strftime("%Y-%m-%d %H:%M")
            pump={
                "id":pid,
                "pump_no":body.get("pump_no",""),
                "name":body.get("name",""),
                "product":body.get("product",""),
                "manufacturer":body.get("manufacturer",""),
                "location":body.get("location",""),
                "district":body.get("district",""),
                "last_calib":body.get("last_calib",""),
                "next_calib":body.get("next_calib",""),
                "notes":body.get("notes",""),
                "status":body.get("status","active"),
                "hose1":{
                    "reading_before":body.get("hose1_rb",""),
                    "reading_after":body.get("hose1_ra",""),
                    "deviation":body.get("hose1_dev",""),
                    "result":body.get("hose1_res",""),
                },
                "hose2":{
                    "reading_before":body.get("hose2_rb",""),
                    "reading_after":body.get("hose2_ra",""),
                    "deviation":body.get("hose2_dev",""),
                    "result":body.get("hose2_res",""),
                },
                "created_by":u["fullname"],
                "updated_by":u["fullname"],
                "updated_at":now,
            }
            db["pumps"].append(pump); save_db(db)
            add_log(u,"إضافة مضخة",f"أضاف مضخة: {pump['name']} ({pump['pump_no']})",self.ip())
            self.send_json({"ok":True,"pump":pump})

        elif p=="/api/devices":
            if not self.can(u,"edit"): self.send_json({"error":"لا صلاحية"},403); return
            body=self.read_body()
            if not body.get("name"): self.send_json({"error":"اسم الجهاز مطلوب"},400); return
            db=load_db(); sid=db["next_device_id"]; db["next_device_id"]+=1
            now=datetime.now().strftime("%Y-%m-%d %H:%M")
            device={
                "id":sid,
                "device_no":body.get("device_no",""),
                "name":body.get("name",""),
                "type":body.get("type","other"),
                "manufacturer":body.get("manufacturer",""),
                "location":body.get("location",""),
                "district":body.get("district",""),
                "last_calib":body.get("last_calib",""),
                "next_calib":body.get("next_calib",""),
                "result":body.get("result",""),
                "range":body.get("range",""),
                "unit":body.get("unit",""),
                "serial_no":body.get("serial_no",""),
                "notes":body.get("notes",""),
                "status":body.get("status","active"),
                "created_by":u["fullname"],
                "updated_by":u["fullname"],
                "updated_at":now,
            }
            db["devices"].append(device); save_db(db)
            add_log(u,"إضافة جهاز",f"أضاف: {device['name']} ({device['device_no']})",self.ip())
            self.send_json({"ok":True,"device":device})

        elif p=="/api/users":
            if u["role"]!="admin": self.send_json({"error":"غير مصرح"},403); return
            body=self.read_body(); db=load_db()
            if any(x["username"]==body.get("username") for x in db["users"]):
                self.send_json({"error":"اسم المستخدم مستخدم بالفعل"},400); return
            uid=db["next_user_id"]; db["next_user_id"]+=1
            role=body.get("role","viewer")
            new_user={"id":uid,"fullname":body.get("fullname",""),"username":body.get("username",""),
                "password":hash_pw(body.get("password","")),"role":role,"active":True,"location":body.get("location",""),
                "district":body.get("district",""),
                "perms":{"view":True,"edit_devices":True,"del_devices":True,"edit_pumps":True,"del_pumps":True,"files_devices":True,"files_pumps":True,"export_devices":True,"export_pumps":True}
                    if role=="admin" else body.get("perms",{"view":True,"edit_devices":False,"del_devices":False,"edit_pumps":False,"del_pumps":False,"files_devices":False,"files_pumps":False,"export_devices":False,"export_pumps":False})}
            db["users"].append(new_user); save_db(db)
            add_log(u,"إضافة مستخدم",f"أضاف مستخدم: {new_user['fullname']}",self.ip())
            self.send_json({"ok":True,"user":{k:v for k,v in new_user.items() if k!="password"}})

        elif p.startswith("/api/files/"):
            if not self.can(u,"files"): self.send_json({"error":"لا صلاحية"},403); return
            parts=p.split("/"); key="/".join(parts[3:])
            try:
                body=self.read_body()
                save_file(key,body.get("name",""),body.get("data",""),body.get("mime",""))
                add_log(u,"رفع ملف",f"رفع: {body.get('name','')}",self.ip())
                self.send_json({"ok":True})
            except Exception as e:
                self.send_json({"error":f"خطأ حفظ الملف: {str(e)}"},500)
        else:
            self.send_json({"error":"غير موجود"},404)

    def do_PUT(self):
        p = urlparse(self.path).path.rstrip("/")
        u = self.require_auth()
        if not u: return

        if p.startswith("/api/pumps/"):
            if not self.can(u,"edit"): self.send_json({"error":"لا صلاحية"},403); return
            pid=int(p.split("/")[-1]); body=self.read_body(); db=load_db()
            if "pumps" not in db: db["pumps"]=[]
            idx=next((i for i,d in enumerate(db["pumps"]) if d["id"]==pid),None)
            if idx is None: self.send_json({"error":"غير موجود"},404); return
            for f in ["pump_no","name","product","manufacturer","location","district","last_calib","next_calib","notes","status"]:
                if f in body: db["pumps"][idx][f]=body[f]
            db["pumps"][idx]["hose1"]={
                "reading_before":body.get("hose1_rb",""),
                "reading_after":body.get("hose1_ra",""),
                "deviation":body.get("hose1_dev",""),
                "result":body.get("hose1_res",""),
            }
            db["pumps"][idx]["hose2"]={
                "reading_before":body.get("hose2_rb",""),
                "reading_after":body.get("hose2_ra",""),
                "deviation":body.get("hose2_dev",""),
                "result":body.get("hose2_res",""),
            }
            db["pumps"][idx]["updated_by"]=u["fullname"]
            db["pumps"][idx]["updated_at"]=datetime.now().strftime("%Y-%m-%d %H:%M")
            save_db(db)
            add_log(u,"تعديل مضخة",f"عدّل مضخة: {db['pumps'][idx]['name']}",self.ip())
            self.send_json({"ok":True,"pump":db["pumps"][idx]})

        elif p.startswith("/api/devices/"):
            if not self.can(u,"edit"): self.send_json({"error":"لا صلاحية"},403); return
            did=int(p.split("/")[-1]); body=self.read_body(); db=load_db()
            idx=next((i for i,d in enumerate(db["devices"]) if d["id"]==did),None)
            if idx is None: self.send_json({"error":"غير موجود"},404); return
            fields=["device_no","name","type","manufacturer","location","last_calib",
                    "next_calib","result","range","unit","serial_no","notes","status"]
            for f in fields:
                if f in body: db["devices"][idx][f]=body[f]
            db["devices"][idx]["updated_by"]=u["fullname"]
            db["devices"][idx]["updated_at"]=datetime.now().strftime("%Y-%m-%d %H:%M")
            save_db(db)
            add_log(u,"تعديل جهاز",f"عدّل: {db['devices'][idx]['name']}",self.ip())
            self.send_json({"ok":True,"device":db["devices"][idx]})

        elif p.startswith("/api/users/"):
            if u["role"]!="admin": self.send_json({"error":"غير مصرح"},403); return
            uid=int(p.split("/")[-1]); body=self.read_body(); db=load_db()
            idx=next((i for i,x in enumerate(db["users"]) if x["id"]==uid),None)
            if idx is None: self.send_json({"error":"غير موجود"},404); return
            if "password" in body and body["password"]:
                if "old_password" in body:
                    if db["users"][idx]["password"]!=hash_pw(body["old_password"]):
                        self.send_json({"error":"كلمة المرور الحالية غير صحيحة"},400); return
                db["users"][idx]["password"]=hash_pw(body["password"])
                add_log(u,"تغيير كلمة المرور",f"غيّر كلمة مرور id={uid}",self.ip())
            for f in ["fullname","username","role","active","perms","location"]:
                if f in body: db["users"][idx][f]=body[f]
            save_db(db)
            self.send_json({"ok":True,"user":{k:v for k,v in db["users"][idx].items() if k!="password"}})
        else:
            self.send_json({"error":"غير موجود"},404)

    def do_DELETE(self):
        p = urlparse(self.path).path.rstrip("/")
        u = self.require_auth()
        if not u: return

        if p.startswith("/api/pumps/"):
            if not self.can(u,"del"): self.send_json({"error":"لا صلاحية حذف"},403); return
            pid=int(p.split("/")[-1]); db=load_db()
            if "pumps" not in db: db["pumps"]=[]
            deleted=next((d["name"] for d in db["pumps"] if d["id"]==pid),str(pid))
            db["pumps"]=[d for d in db["pumps"] if d["id"]!=pid]
            del_file(f"worklog_{pid}"); save_db(db)
            add_log(u,"حذف مضخة",f"حذف مضخة: {deleted}",self.ip())
            self.send_json({"ok":True})

        elif p.startswith("/api/devices/"):
            if not self.can(u,"del"): self.send_json({"error":"لا صلاحية حذف"},403); return
            did=int(p.split("/")[-1]); db=load_db()
            deleted=next((d["name"] for d in db["devices"] if d["id"]==did),str(did))
            db["devices"]=[d for d in db["devices"] if d["id"]!=did]
            del_file(f"cert_{did}"); del_file(f"report_{did}")
            save_db(db)
            add_log(u,"حذف جهاز",f"حذف: {deleted}",self.ip())
            self.send_json({"ok":True})

        elif p.startswith("/api/users/"):
            if u["role"]!="admin": self.send_json({"error":"غير مصرح"},403); return
            uid=int(p.split("/")[-1])
            if uid==u["id"]: self.send_json({"error":"لا يمكن حذف حسابك"},400); return
            db=load_db()
            deleted=next((x.get("fullname","") for x in db["users"] if x["id"]==uid),"")
            db["users"]=[x for x in db["users"] if x["id"]!=uid]
            save_db(db)
            add_log(u,"حذف مستخدم",f"حذف: {deleted}",self.ip())
            self.send_json({"ok":True})

        elif p.startswith("/api/files/"):
            if not self.can(u,"files"): self.send_json({"error":"لا صلاحية"},403); return
            parts=p.split("/"); key="/".join(parts[3:])
            del_file(key); self.send_json({"ok":True})
        else:
            self.send_json({"error":"غير موجود"},404)

if __name__=="__main__":
    if USE_DB:
        print("⏳ تهيئة قاعدة البيانات...")
        init_pg()
    server=HTTPServer(("0.0.0.0",PORT),Handler)
    print(f"\n  📐  نظام إدارة القياس والمعايرة")
    print(f"  ✅  السيرفر يعمل على المنفذ {PORT}")
    print(f"  🌐  http://localhost:{PORT}\n")
    try: server.serve_forever()
    except KeyboardInterrupt: server.shutdown()
