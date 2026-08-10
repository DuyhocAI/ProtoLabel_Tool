import os, sys, tempfile, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
TEST_ROOT=tempfile.TemporaryDirectory(); ROOT=Path(TEST_ROOT.name)
(ROOT/"project").mkdir(); (ROOT/"project"/"image.jpg").touch(); (ROOT/"models").mkdir()
os.environ.update(PROTOLABEL_DATA_DIR=str(ROOT/"data"),PROTOLABEL_WORKSPACE_ROOT=str(ROOT),PROTOLABEL_ROOT=str(ROOT),PROTOLABEL_MODEL_DIR=str(ROOT/"models"))
from fastapi.testclient import TestClient
from app import main
from app.auth import init_auth_schema

class AccountTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls): cls.ctx=TestClient(main.app); cls.client=cls.ctx.__enter__()
 @classmethod
 def tearDownClass(cls): cls.ctx.__exit__(None,None,None); TEST_ROOT.cleanup()
 def setUp(self):
  c=main.db()
  for table in ("user_events","image_activity","sessions","users","boxes","images","projects"): c.execute(f"DELETE FROM {table}")
  c.execute("UPDATE app_settings SET value='1' WHERE key='registration_enabled'"); c.commit(); c.close(); init_auth_schema(); self.client.cookies.clear(); main.jobs.clear()
 def login_admin(self):
  r=self.client.post("/api/auth/login",json={"username":"admin","password":"admin"}); self.assertEqual(r.status_code,200,r.text); self.assertTrue(r.json()["user"]["must_change_password"]); self.assertEqual(self.client.get("/api/projects").status_code,403)
  r=self.client.put("/api/auth/change-password",json={"current_password":"admin","new_password":"admin-secure-123"}); self.assertEqual(r.status_code,200,r.text)
 def seed(self,boxes=False):
  c=main.db(); c.execute("INSERT INTO projects(id,name,root,classes) VALUES(?,?,?,?)",("p","p",str(ROOT/"project"),'["person"]')); c.execute("INSERT INTO images VALUES(?,?,?,?,?,?)",("i","p","image.jpg",100,100,"unlabeled"))
  if boxes:
   c.execute("INSERT INTO boxes VALUES(?,?,?,?,?,?,?,?,?,?)",("manual","i","person",.1,.1,.2,.2,None,"manual","{}")); c.execute("INSERT INTO boxes VALUES(?,?,?,?,?,?,?,?,?,?)",("old-ai","i","person",.2,.2,.3,.3,.8,"custom.pt","{}"))
  c.commit(); c.close()
 def test_login_register_and_cookie_auth(self):
  self.assertEqual(self.client.get("/api/health").status_code,200); self.assertEqual(self.client.get("/api/projects").status_code,401); self.login_admin(); self.assertEqual(self.client.get("/api/projects").status_code,200); self.client.delete("/api/auth/logout")
  r=self.client.post("/api/auth/register",json={"username":"worker1","display_name":"Worker One","password":"worker-pass-123"}); self.assertEqual(r.status_code,200,r.text); self.assertEqual(r.json()["user"]["role"],"annotator")
  self.assertNotIn("password_hash",r.text); self.assertNotIn("password_salt",r.text); self.assertIn("httponly",r.headers["set-cookie"].lower()); self.assertIn("samesite=strict",r.headers["set-cookie"].lower())
 def test_admin_account_management(self):
  self.login_admin(); admin_cookie=self.client.cookies.get("protolabel_session")
  with TestClient(main.app) as worker:
   r=worker.post("/api/auth/register",json={"username":"worker2","display_name":"Worker Two","password":"worker-pass-123"}); self.assertEqual(r.status_code,200,r.text); worker_id=r.json()["user"]["id"]
  self.client.cookies.set("protolabel_session",admin_cookie)
  r=self.client.put(f"/api/admin/users/{worker_id}",json={"new_password":"temporary-pass-123"}); self.assertEqual(r.status_code,200,r.text)
  managed=next(x for x in self.client.get("/api/admin/users").json()["users"] if x["id"]==worker_id); self.assertTrue(managed["must_change_password"])
  r=self.client.put("/api/admin/settings",json={"registration_enabled":False}); self.assertEqual(r.status_code,200,r.text)
  with TestClient(main.app) as outsider:
   self.assertEqual(outsider.post("/api/auth/register",json={"username":"blocked","password":"blocked-pass-123"}).status_code,403)
   r=outsider.post("/api/auth/login",json={"username":"worker2","password":"temporary-pass-123"}); self.assertEqual(r.status_code,200,r.text); self.assertTrue(r.json()["user"]["must_change_password"])
 def test_admin_performance(self):
  self.login_admin(); self.seed(); self.client.get("/api/projects/p/images/i")
  one={"boxes":[{"id":"b","cls_name":"person","bbox":[.1,.1,.2,.2],"source":"manual"}],"status":"labeled"}
  for _ in range(2):
   r=self.client.put("/api/projects/p/images/i/boxes",json=one); self.assertEqual(r.status_code,200,r.text)
  two={"boxes":[*one["boxes"],{"id":"b2","cls_name":"person","bbox":[.4,.4,.2,.2],"source":"manual"}],"status":"labeled"}
  self.client.put("/api/projects/p/images/i/boxes",json=two)
  row=self.client.get("/api/admin/users?project_id=p").json()["users"][0]; self.assertEqual(row["images_saved"],1); self.assertEqual(row["boxes_saved"],2)
  future=self.client.get("/api/admin/users?date_from=9999999999").json()["users"][0]; self.assertEqual(future["images_saved"],0)
 def test_top_left_xywh_bbox_contract(self):
  self.login_admin(); self.seed(); bbox=[.1,.2,.3,.4]
  r=self.client.put("/api/projects/p/images/i/boxes",json={"boxes":[{"id":"b","cls_name":"person","bbox":bbox,"source":"manual"}],"status":"labeled"}); self.assertEqual(r.status_code,200,r.text); self.assertEqual(r.json()["boxes"][0]["bbox"],bbox)
  bad=self.client.put("/api/projects/p/images/i/boxes",json={"boxes":[{"id":"bad","cls_name":"person","bbox":[.8,.2,.3,.4],"source":"manual"}]}); self.assertEqual(bad.status_code,400,bad.text)

 def test_dynamic_models(self):
  self.login_admin(); (ROOT/"models"/"custom-detector.pt").write_bytes(b"weights"); (ROOT/"models"/"ignored.txt").write_text("no"); r=self.client.get("/api/models"); self.assertEqual([x["id"] for x in r.json()["models"]],["custom-detector.pt"])
 def test_invalid_conf(self):
  self.login_admin(); self.seed(); (ROOT/"models"/"custom.pt").write_bytes(b"weights"); r=self.client.post("/api/projects/p/prelabel",json={"image_ids":["i"],"model_id":"custom.pt","conf":"abc"}); self.assertEqual(r.status_code,422)
 def test_manual_boxes_survive_prelabel(self):
  self.seed(True); old=main.predict; main.jobs["job"]={"id":"job","status":"queued"}; main.predict=lambda *a:[{"id":"new-ai","cls_name":"person","bbox":[.3,.3,.4,.4],"confidence":.9,"source":"custom.pt","attributes":{}}]
  try: main.prelabel_job("job","p",["i"],"custom.pt",.25,.7,True,None)
  finally: main.predict=old
  c=main.db(); rows=c.execute("SELECT id,source FROM boxes WHERE image_id=? ORDER BY id",("i",)).fetchall(); c.close(); self.assertEqual([tuple(x) for x in rows],[("manual","manual"),("new-ai","custom.pt")])

if __name__=="__main__": unittest.main()
