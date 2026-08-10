import os
import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

TEST_ROOT = tempfile.TemporaryDirectory()
ROOT = Path(TEST_ROOT.name)
(ROOT / "project").mkdir()
(ROOT / "project" / "image.jpg").touch()
os.environ.update(
    PROTOLABEL_DATA_DIR=str(ROOT / "data"),
    PROTOLABEL_WORKSPACE_ROOT=str(ROOT),
    PROTOLABEL_ROOT=str(ROOT),
    PROTOLABEL_MODEL_DIR=str(ROOT / "models"),
    PROTOLABEL_AUTH_USERNAME="qa",
    PROTOLABEL_AUTH_PASSWORD="qa-secret",
)

from fastapi.testclient import TestClient
from app import main


class ApiRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client_context = TestClient(main.app)
        cls.client = cls.client_context.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client_context.__exit__(None, None, None)
        TEST_ROOT.cleanup()

    def setUp(self):
        connection = main.db()
        connection.execute("DELETE FROM boxes")
        connection.execute("DELETE FROM images")
        connection.execute("DELETE FROM projects")
        connection.commit()
        connection.close()
        main.jobs.clear()

    def auth(self):
        return ("qa", "qa-secret")

    def test_health_is_public_but_api_and_docs_are_protected(self):
        self.assertEqual(self.client.get("/api/health").status_code, 200)
        self.assertEqual(self.client.get("/api/projects").status_code, 401)
        self.assertEqual(self.client.get("/docs").status_code, 401)
        self.assertEqual(
            self.client.get("/api/projects", auth=self.auth()).status_code,
            200,
        )

    def test_invalid_prelabel_conf_returns_422(self):
        self._seed_image()
        response = self.client.post(
            "/api/projects/p/prelabel",
            json={"image_ids": ["i"], "conf": "abc"},
            auth=self.auth(),
        )
        self.assertEqual(response.status_code, 422)

    def test_prelabel_replace_preserves_manual_boxes(self):
        self._seed_image(with_boxes=True)
        original_predict = main.predict
        main.jobs["job"] = {"id": "job", "status": "queued"}
        main.predict = lambda *args: [{
            "id": "new-ai", "cls_name": "person",
            "bbox": [.3, .3, .4, .4], "confidence": .9,
            "source": "yolo26n", "attributes": {},
        }]
        try:
            main.prelabel_job("job", "p", ["i"], "yolo26n", .25, .7, True, None)
        finally:
            main.predict = original_predict
        connection = main.db()
        rows = connection.execute(
            "SELECT id,source FROM boxes WHERE image_id=? ORDER BY id", ("i",)
        ).fetchall()
        connection.close()
        self.assertEqual(
            [tuple(row) for row in rows],
            [("manual", "manual"), ("new-ai", "yolo26n")],
        )

    def test_interrupted_job_is_recovered_after_restart(self):
        job = main.create_job("scan", processed=1, total=10)
        main.update_job(job["id"], status="running")
        main.jobs.clear()
        main.job_last_persisted.clear()
        main.load_jobs()
        recovered = main.jobs[job["id"]]
        self.assertEqual(recovered["status"], "error")
        self.assertEqual(recovered["error"], "Job interrupted by backend restart")

    def _seed_image(self, with_boxes=False):
        connection = main.db()
        connection.execute(
            "INSERT INTO projects(id,name,root,classes) VALUES(?,?,?,?)",
            ("p", "p", str(ROOT / "project"), '["person"]'),
        )
        connection.execute(
            "INSERT INTO images VALUES(?,?,?,?,?,?)",
            ("i", "p", "image.jpg", 100, 100, "unlabeled"),
        )
        if with_boxes:
            connection.execute(
                "INSERT INTO boxes VALUES(?,?,?,?,?,?,?,?,?,?)",
                ("manual", "i", "person", .1, .1, .2, .2, None, "manual", "{}"),
            )
            connection.execute(
                "INSERT INTO boxes VALUES(?,?,?,?,?,?,?,?,?,?)",
                ("old-ai", "i", "person", .2, .2, .3, .3, .8, "yolo26n", "{}"),
            )
        connection.commit()
        connection.close()


if __name__ == "__main__":
    unittest.main()
