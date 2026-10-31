import tempfile
import unittest
import json
from datetime import timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select

from gaussiancircuit.api.app import create_app
from gaussiancircuit.api.config import Settings
from gaussiancircuit.api.tables import CaptureSession, utcnow


JPEG = b"\xff\xd8\xff\xe0" + b"phase-two-test-image"


class CaptureApiTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        settings = Settings(
            database_url=f"sqlite:///{(root / 'test.db').as_posix()}",
            capture_root=root / "captures",
            reconstruction_root=root / "reconstructions",
            public_web_url="http://testserver",
            session_minutes=30,
            min_capture_images=2,
            max_capture_images=5,
        )
        self.app = create_app(settings)
        self.client_context = TestClient(self.app)
        self.client = self.client_context.__enter__()

    def tearDown(self):
        self.client_context.__exit__(None, None, None)
        self.temporary_directory.cleanup()

    def create_and_join(self):
        created = self.client.post("/api/capture-sessions").json()
        joined_response = self.client.post(
            "/api/capture-sessions/join", json={"code": created["code"].lower().replace("-", "")}
        )
        self.assertEqual(joined_response.status_code, 200)
        return created, joined_response.json()

    def test_desktop_creates_code_and_phone_joins(self):
        created, joined = self.create_and_join()
        self.assertRegex(created["code"], r"^[A-HJ-NP-Z2-9]{3}-[A-HJ-NP-Z2-9]{3}$")
        self.assertNotEqual(created["desktop_token"], joined["phone_token"])
        response = self.client.get(
            f'/api/capture-sessions/{created["id"]}',
            headers={"Authorization": f'Bearer {created["desktop_token"]}'},
        )
        self.assertEqual(response.json()["status"], "capturing")

    def test_phone_token_cannot_read_desktop_state(self):
        created, joined = self.create_and_join()
        response = self.client.get(
            f'/api/capture-sessions/{created["id"]}',
            headers={"Authorization": f'Bearer {joined["phone_token"]}'},
        )
        self.assertEqual(response.status_code, 403)

    def test_upload_deduplicates_and_finish_writes_manifest(self):
        created, joined = self.create_and_join()
        endpoint = f'/api/capture-sessions/{created["id"]}/photos'
        headers = {"Authorization": f'Bearer {joined["phone_token"]}'}
        first = self.client.post(
            endpoint,
            headers=headers,
            files=[("files", ("first.jpg", JPEG, "image/jpeg"))],
        )
        self.assertEqual(first.status_code, 200)
        duplicate = self.client.post(
            endpoint,
            headers=headers,
            files=[("files", ("same.jpg", JPEG, "image/jpeg"))],
        )
        self.assertEqual(duplicate.json()["duplicate_count"], 1)
        second_image = JPEG + b"-different"
        second = self.client.post(
            endpoint,
            headers=headers,
            files=[("files", ("second.jpg", second_image, "image/jpeg"))],
        )
        self.assertEqual(second.json()["snapshot"]["photo_count"], 2)
        finished = self.client.post(
            f'/api/capture-sessions/{created["id"]}/finish', headers=headers
        )
        self.assertEqual(finished.status_code, 200)
        self.assertEqual(finished.json()["status"], "ready_for_reconstruction")
        manifest = self.app.state.settings.capture_root / created["id"] / "manifest.json"
        self.assertTrue(manifest.exists())

    def test_rejects_spoofed_and_oversized_images(self):
        created, joined = self.create_and_join()
        endpoint = f'/api/capture-sessions/{created["id"]}/photos'
        headers = {"Authorization": f'Bearer {joined["phone_token"]}'}
        spoofed = self.client.post(
            endpoint,
            headers=headers,
            files=[("files", ("fake.jpg", b"not-a-jpeg", "image/jpeg"))],
        )
        self.assertEqual(spoofed.status_code, 422)

    def test_expired_code_cannot_join(self):
        created = self.client.post("/api/capture-sessions").json()
        database = self.app.state.database
        with database.sessions() as db:
            capture = db.scalar(select(CaptureSession).where(CaptureSession.id == created["id"]))
            capture.expires_at = utcnow() - timedelta(seconds=1)
            db.commit()
        response = self.client.post("/api/capture-sessions/join", json={"code": created["code"]})
        self.assertEqual(response.status_code, 410)

    def test_completed_mesh_requires_desktop_token_and_is_downloadable(self):
        created, joined = self.create_and_join()
        workspace = self.app.state.settings.reconstruction_root / created["id"]
        mesh = workspace / "openmvs" / "scene_mesh.ply"
        mesh.parent.mkdir(parents=True)
        mesh.write_bytes(b"ply\nformat ascii 1.0\nend_header\n")
        (workspace / "status.json").write_text(
            json.dumps(
                {
                    "status": "complete",
                    "mesh_path": "openmvs/scene_mesh.ply",
                }
            ),
            encoding="utf-8",
        )
        endpoint = f'/api/capture-sessions/{created["id"]}/reconstruction/mesh'
        forbidden = self.client.get(
            endpoint,
            headers={"Authorization": f'Bearer {joined["phone_token"]}'},
        )
        self.assertEqual(forbidden.status_code, 403)
        downloaded = self.client.get(
            endpoint,
            headers={"Authorization": f'Bearer {created["desktop_token"]}'},
        )
        self.assertEqual(downloaded.status_code, 200)
        self.assertTrue(downloaded.content.startswith(b"ply"))

    def test_navigation_map_requires_desktop_token_and_is_downloadable(self):
        created, joined = self.create_and_join()
        workspace = self.app.state.settings.reconstruction_root / created["id"]
        navigation = workspace / "openmvs" / "navigation.json"
        navigation.parent.mkdir(parents=True)
        navigation.write_text(json.dumps({"schema_version": "1.0", "spawn": [0, 0]}), encoding="utf-8")
        (workspace / "status.json").write_text(
            json.dumps({"status": "complete", "navigation_path": "openmvs/navigation.json"}),
            encoding="utf-8",
        )
        endpoint = f'/api/capture-sessions/{created["id"]}/reconstruction/navigation'
        forbidden = self.client.get(endpoint, headers={"Authorization": f'Bearer {joined["phone_token"]}'})
        self.assertEqual(forbidden.status_code, 403)
        downloaded = self.client.get(endpoint, headers={"Authorization": f'Bearer {created["desktop_token"]}'})
        self.assertEqual(downloaded.status_code, 200)
        self.assertEqual(downloaded.json()["spawn"], [0, 0])

    def test_textured_model_requires_desktop_token(self):
        created, joined = self.create_and_join()
        workspace = self.app.state.settings.reconstruction_root / created["id"]
        model = workspace / "openmvs" / "scene_textured.glb"
        model.parent.mkdir(parents=True)
        model.write_bytes(b"glTF-test")
        (workspace / "status.json").write_text(
            json.dumps({"status": "complete", "model_path": "openmvs/scene_textured.glb"}),
            encoding="utf-8",
        )
        endpoint = f'/api/capture-sessions/{created["id"]}/reconstruction/model'
        forbidden = self.client.get(endpoint, headers={"Authorization": f'Bearer {joined["phone_token"]}'})
        self.assertEqual(forbidden.status_code, 403)
        downloaded = self.client.get(endpoint, headers={"Authorization": f'Bearer {created["desktop_token"]}'})
        self.assertEqual(downloaded.status_code, 200)
        self.assertEqual(downloaded.content, b"glTF-test")


if __name__ == "__main__":
    unittest.main()
