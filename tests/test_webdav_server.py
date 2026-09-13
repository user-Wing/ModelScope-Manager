import base64
import http.client
import io
import socket
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from modelscope_manager.folder_index import FolderSizeIndex
from modelscope_manager.service import RemoteEntry, Repository
from modelscope_manager.webdav_server import ModelScopeWebDAV


class FakeService:
    def __init__(self):
        self.token = "test-token"
        self.uploads = []
        self.entry_calls = 0

    def list_repositories(self):
        return [
            Repository("alice/demo", "dataset", "public"),
            Repository("alice/model", "model", "public"),
        ]

    def list_entries(self, repo):
        self.entry_calls += 1
        return [
            RemoteEntry("folder", is_dir=True),
            RemoteEntry("folder/a.txt", 3, "abc"),
        ]

    def upload_file_as(self, repo, path, remote_path):
        self.uploads.append((repo, remote_path, path.read_bytes()))


class WebDAVTests(unittest.TestCase):
    def setUp(self):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            self.port = probe.getsockname()[1]
        self.service = FakeService()
        self.temporary = tempfile.TemporaryDirectory()
        self.folder_index = FolderSizeIndex(Path(self.temporary.name) / "sizes.sqlite3")
        self.public_repo = Repository("moonshotai/PerceptionBench", "dataset", "public")
        self.gateway = ModelScopeWebDAV(
            lambda: self.service,
            "127.0.0.1",
            self.port,
            "user",
            "pass",
            lambda: [self.public_repo],
            self.folder_index,
        )
        self.gateway.start()
        self.authorization = "Basic " + base64.b64encode(b"user:pass").decode()

    def tearDown(self):
        self.gateway.stop()
        self.temporary.cleanup()

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
        merged = {"Authorization": self.authorization, **(headers or {})}
        connection.request(method, path, body=body, headers=merged)
        response = connection.getresponse()
        payload = response.read()
        connection.close()
        return response.status, payload

    def test_propfind_lists_repository_tree(self):
        status, body = self.request("PROPFIND", "/datasets/alice/demo/", headers={"Depth": "1"})
        self.assertEqual(status, 207)
        self.assertIn(b"folder", body)
        self.assertIn(b"<d:getcontentlength>3</d:getcontentlength>", body)
        self.assertEqual(self.folder_index.folder_size(Repository("alice/demo", "dataset"), "folder"), 3)
        second_status, _ = self.request("PROPFIND", "/datasets/alice/demo/", headers={"Depth": "1"})
        self.assertEqual(second_status, 207)
        self.assertEqual(self.service.entry_calls, 1)

    def test_root_always_contains_models_datasets_and_public(self):
        status, body = self.request("PROPFIND", "/", headers={"Depth": "1"})
        self.assertEqual(status, 207)
        self.assertIn(b"models", body)
        self.assertIn(b"datasets", body)
        self.assertIn(b"public", body)

    def test_unauthorized_propfind_closes_connection(self):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
        try:
            connection.request(
                "PROPFIND",
                "/",
                body=b'<?xml version="1.0"?><propfind xmlns="DAV:"/>',
                headers={"Content-Type": "application/xml"},
            )
            response = connection.getresponse()
            self.assertEqual(response.status, 401)
            self.assertEqual(response.getheader("Connection"), "close")
            response.read()
        finally:
            connection.close()

    def test_dav_endpoint_preserves_prefix_for_subdirectory_mount(self):
        status, body = self.request("PROPFIND", "/dav/public", headers={"Depth": "1"})

        self.assertEqual(status, 207)
        root = ET.fromstring(body)
        hrefs = [element.text for element in root.findall(".//{DAV:}href")]
        self.assertEqual(
            hrefs,
            ["/dav/public/", "/dav/public/moonshotai%40PerceptionBench%20%5Bdataset%5D/"],
        )

    def test_public_root_lists_each_saved_resource_as_a_mount(self):
        status, body = self.request("PROPFIND", "/public/", headers={"Depth": "1"})
        self.assertEqual(status, 207)
        self.assertIn(b"moonshotai%40PerceptionBench%20%5Bdataset%5D", body)

    def test_public_pool_works_without_private_token(self):
        public_repo = Repository("moonshotai/PerceptionBench", "dataset", "public")
        gateway = ModelScopeWebDAV(
            lambda: None,
            "127.0.0.1",
            self.port,
            "user",
            "pass",
            lambda: [public_repo],
        )
        root_names = [node.name for node in gateway.children(gateway.resolve("/"))]
        private_models = gateway.children(gateway.resolve("/models"))
        public_children = gateway.children(gateway.resolve("/public"))
        public_repo_node = gateway.resolve("/public/moonshotai@PerceptionBench [dataset]")
        self.assertEqual(root_names, ["models", "datasets", "public"])
        self.assertEqual(private_models, [])
        self.assertEqual([node.name for node in public_children], ["moonshotai@PerceptionBench [dataset]"])
        self.assertEqual(public_repo_node.repo, public_repo)
        self.assertTrue(public_repo_node.public)

    def test_server_starts_and_serves_root_without_token(self):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        gateway = ModelScopeWebDAV(lambda: None, "127.0.0.1", port, "user", "pass")
        gateway.start()
        try:
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
            connection.request("PROPFIND", "/", headers={
                "Authorization": self.authorization,
                "Depth": "1",
            })
            response = connection.getresponse()
            body = response.read()
            connection.close()
        finally:
            gateway.stop()
        self.assertEqual(response.status, 207)
        self.assertIn(b"public", body)

    def test_put_routes_to_sdk_upload(self):
        status, _ = self.request("PUT", "/datasets/alice/demo/new.txt", body=b"new")
        self.assertEqual(status, 201)
        self.assertEqual(self.service.uploads[0][1:], ("new.txt", b"new"))

    def test_mkcol_is_visible_until_content_is_uploaded(self):
        status, _ = self.request("MKCOL", "/datasets/alice/demo/new-folder")
        self.assertEqual(status, 201)
        status, body = self.request("PROPFIND", "/datasets/alice/demo/", headers={"Depth": "1"})
        self.assertEqual(status, 207)
        self.assertIn(b"new-folder", body)

    def test_delete_and_move_are_rejected(self):
        delete_status, _ = self.request("DELETE", "/datasets/alice/demo/folder/a.txt")
        move_status, _ = self.request("MOVE", "/datasets/alice/demo/folder/a.txt")
        self.assertEqual((delete_status, move_status), (405, 405))

    def test_model_upload_over_50gb_reaches_stream_handling(self):
        gateway = ModelScopeWebDAV(
            lambda: self.service,
            "127.0.0.1",
            0,
            "user",
            "pass",
        )
        with self.assertRaises(ConnectionError):
            gateway.upload(
                "/models/alice/model/huge.bin",
                io.BytesIO(b""),
                50 * 1024**3 + 1,
            )


if __name__ == "__main__":
    unittest.main()
