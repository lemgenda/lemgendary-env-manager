"""
LemGendary Environment Manager — masked, write-only secrets vault tests.

Every test runs against a temporary workspace: ``_secrets_vault`` in the
server is patched so no real ``.secrets.yaml`` or credential file is touched.
"""

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml
from fastapi.testclient import TestClient

from env_manager import server
from env_manager.secrets_vault import (
    SecretsVault,
    seed_from_legacy_files,
    value_hint,
)

KAGGLE_TOKEN = "KGAT_abcdefgh12345678"
GITHUB_TOKEN = "ghp_secretsecret9876"


def _kaggle(value=None, username="alice", item_id="kaggle-alice", is_default=True):
    item = {
        "id": item_id,
        "service": "kaggle",
        "label": "Kaggle",
        "username": username,
        "is_default": is_default,
    }
    if value is not None:
        item["secret_value"] = value
    return item


class SecretsVaultApiTests(unittest.TestCase):
    """HTTP-level checks of GET/POST /api/secrets on a temporary workspace."""

    def setUp(self) -> None:
        self._tmp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self._tmp_dir)
        self.root = Path(self._tmp_dir) / "workspace"
        self.home_kaggle = Path(self._tmp_dir) / "home" / ".kaggle"
        for repo in ("lemgendary-datasets", "lemgendary-training-suite"):
            (self.root / repo).mkdir(parents=True)
        vault = SecretsVault(self.root, self.home_kaggle)
        patcher = mock.patch.object(server, "_secrets_vault", return_value=vault)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = TestClient(server.app)
        self.secrets_file = self.root / ".secrets.yaml"

    def _stored(self) -> dict:
        data = yaml.safe_load(self.secrets_file.read_text(encoding="utf-8"))
        return {s["id"]: s for s in data["secrets"]}

    def _post(self, items):
        return self.client.post("/api/secrets", json={"secrets": items})

    def test_get_masks_values_and_has_no_side_effect(self) -> None:
        """GET masks values and writes nothing."""
        users = self.root / "lemgendary-training-suite" / ".kaggle_users"
        users.write_text(f"KAGGLE_USERNAME=alice, KAGGLE_API_TOKEN={KAGGLE_TOKEN};\n",
                         encoding="utf-8")
        before = sorted(p.relative_to(self.root) for p in self.root.rglob("*"))

        res = self.client.get("/api/secrets")

        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertFalse(body["initialized"])
        self.assertNotIn(KAGGLE_TOKEN, res.text)
        item = body["secrets"][0]
        self.assertNotIn("secret_value", item)
        self.assertTrue(item["has_value"])
        self.assertEqual(item["value_hint"], KAGGLE_TOKEN[-4:])
        self.assertTrue(item["is_default"])
        self.assertFalse(self.secrets_file.exists())
        after = sorted(p.relative_to(self.root) for p in self.root.rglob("*"))
        self.assertEqual(before, after)

    def test_get_masks_stored_vault(self) -> None:
        """GET masks the values of a stored vault."""
        self.assertEqual(self._post([_kaggle(KAGGLE_TOKEN)]).status_code, 200)
        res = self.client.get("/api/secrets")
        self.assertTrue(res.json()["initialized"])
        self.assertNotIn(KAGGLE_TOKEN, res.text)
        self.assertEqual(res.json()["secrets"][0]["value_hint"], "5678")

    def test_post_keeps_stored_value_when_empty_or_omitted(self) -> None:
        """POST keeps the stored value when the new one is empty or missing."""
        github = {"id": "github-primary", "service": "github", "label": "GitHub",
                  "secret_value": GITHUB_TOKEN}
        self.assertEqual(self._post([_kaggle(KAGGLE_TOKEN), github]).status_code, 200)

        res = self._post([_kaggle(), {**github, "secret_value": ""}])

        self.assertEqual(res.status_code, 200)
        stored = self._stored()
        self.assertEqual(stored["kaggle-alice"]["secret_value"], KAGGLE_TOKEN)
        self.assertEqual(stored["github-primary"]["secret_value"], GITHUB_TOKEN)

    def test_post_replaces_value_when_non_empty(self) -> None:
        """POST replaces the value when a new one is given."""
        self._post([_kaggle(KAGGLE_TOKEN)])
        res = self._post([_kaggle("KGAT_replacement_0000")])
        self.assertEqual(res.status_code, 200)
        self.assertEqual(self._stored()["kaggle-alice"]["secret_value"], "KGAT_replacement_0000")
        self.assertTrue((self.secrets_file.parent / ".secrets.yaml.bak").exists())

    def test_post_removes_items_left_out(self) -> None:
        """POST removes items left out of the payload."""
        github = {"id": "github-primary", "service": "github", "label": "GitHub",
                  "secret_value": GITHUB_TOKEN}
        self._post([_kaggle(KAGGLE_TOKEN), github])
        self._post([_kaggle()])
        self.assertNotIn("github-primary", self._stored())

    def test_kaggle_mandatory_accepts_kept_value(self) -> None:
        """The mandatory Kaggle secret is satisfied by a kept stored value."""
        self._post([_kaggle(KAGGLE_TOKEN)])
        self.assertEqual(self._post([_kaggle("")]).status_code, 200)

    def test_kaggle_mandatory_accepts_kept_legacy_seed(self) -> None:
        """The mandatory Kaggle secret is satisfied by a kept legacy seed."""
        users = self.root / "lemgendary-training-suite" / ".kaggle_users"
        users.write_text(f"KAGGLE_USERNAME=alice, KAGGLE_API_TOKEN={KAGGLE_TOKEN};\n",
                         encoding="utf-8")
        res = self._post([_kaggle()])
        self.assertEqual(res.status_code, 200)
        self.assertEqual(self._stored()["kaggle-alice"]["secret_value"], KAGGLE_TOKEN)

    def test_kaggle_mandatory_rejects_missing_value(self) -> None:
        """Saving without any Kaggle value is rejected."""
        res = self._post([_kaggle()])
        self.assertEqual(res.status_code, 400)
        self.assertIn("Kaggle", res.json()["detail"])
        self.assertFalse(self.secrets_file.exists())

    def test_post_response_never_echoes_values(self) -> None:
        """The POST response never contains secret values."""
        github = {"id": "github-primary", "service": "github", "label": "GitHub",
                  "secret_value": GITHUB_TOKEN}
        res = self._post([_kaggle(KAGGLE_TOKEN), github])
        self.assertEqual(res.status_code, 200)
        self.assertNotIn(KAGGLE_TOKEN, res.text)
        self.assertNotIn(GITHUB_TOKEN, res.text)
        self.assertEqual(res.json()["count"], 2)

    def test_propagation_writes_legacy_files_without_inventing_usernames(self) -> None:
        """Propagation writes legacy files and never invents a username."""
        second = _kaggle("KGAT_second_token_11", username=None, item_id="kaggle-2",
                         is_default=False)
        res = self._post([_kaggle(KAGGLE_TOKEN), second])

        report = res.json()["propagation"]
        self.assertEqual(report["failed"], [])
        for repo in ("lemgendary-datasets", "lemgendary-training-suite"):
            token_file = self.root / repo / ".kaggle_token"
            self.assertEqual(token_file.read_text(encoding="utf-8"), KAGGLE_TOKEN)
        self.assertFalse((self.root / "lemgendary-env-manager" / ".kaggle_token").exists())
        users = (self.root / "lemgendary-training-suite" / ".kaggle_users").read_text(
            encoding="utf-8")
        self.assertEqual(users, f"KAGGLE_USERNAME=alice, KAGGLE_API_TOKEN={KAGGLE_TOKEN};\n")
        self.assertTrue(any("kaggle-2" in s["path"] for s in report["skipped"]))
        kaggle_json = json.loads((self.home_kaggle / "kaggle.json").read_text(encoding="utf-8"))
        self.assertEqual(kaggle_json, {"username": "alice", "key": KAGGLE_TOKEN})

    def test_propagation_skips_kaggle_json_without_username(self) -> None:
        """``kaggle.json`` is skipped when no username is known."""
        res = self._post([_kaggle(KAGGLE_TOKEN, username=None)])
        report = res.json()["propagation"]
        self.assertIn("~/.kaggle/kaggle.json", [s["path"] for s in report["skipped"]])
        self.assertFalse((self.home_kaggle / "kaggle.json").exists())

    def test_propagation_reports_write_failures(self) -> None:
        """Failed legacy writes are listed in the report."""
        # A directory in place of the token file makes the write fail.
        (self.root / "lemgendary-datasets" / ".kaggle_token").mkdir()
        res = self._post([_kaggle(KAGGLE_TOKEN)])
        self.assertEqual(res.status_code, 200)
        failed = [f["path"] for f in res.json()["propagation"]["failed"]]
        self.assertEqual(failed, ["lemgendary-datasets/.kaggle_token"])
        self.assertIn("could not be written", res.json()["message"])

    def test_get_reports_unreadable_vault(self) -> None:
        """GET on an unreadable vault returns status ``error`` and no secrets."""
        self.secrets_file.write_text("secrets: [unclosed\n", encoding="utf-8")
        res = self.client.get("/api/secrets")
        self.assertEqual(res.json()["status"], "error")
        self.assertEqual(res.json()["secrets"], [])


class SecretsVaultUnitTests(unittest.TestCase):
    """Pure helpers."""

    def test_value_hint(self) -> None:
        """The hint is the last four characters, and empty for values under eight."""
        self.assertEqual(value_hint("1234567"), "")
        self.assertEqual(value_hint("12345678"), "5678")
        self.assertEqual(value_hint(""), "")

    def test_seed_does_not_invent_usernames(self) -> None:
        """Seeding from legacy files never invents a username."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "lemgendary-training-suite").mkdir()
            (root / "lemgendary-training-suite" / ".GITHUB_PAT").write_text(
                GITHUB_TOKEN, encoding="utf-8")
            (root / "lemgendary-datasets").mkdir()
            (root / "lemgendary-datasets" / ".mt5_credentials").write_text(
                "User: 123\nPass: pw\n", encoding="utf-8")
            seeds = {s["id"]: s for s in seed_from_legacy_files(root)}
        self.assertIsNone(seeds["github-primary"]["username"])
        self.assertIsNone(seeds["mt5-123"]["server"])
        self.assertEqual(seeds["mt5-123"]["secret_value"], "pw")


if __name__ == "__main__":
    unittest.main()
