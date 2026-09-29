"""The documentation validator must ignore installed tools, not project contracts."""

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class PackageValidationTests(unittest.TestCase):
    def test_dependency_json_is_ignored_but_project_json_is_checked(self):
        with tempfile.TemporaryDirectory() as temporary:
            package = Path(temporary)
            for name in (
                "README.md",
                "AGENTS.md",
                "CODEX_START_PROMPT.md",
                ".env.example",
                "docs",
                "evidence",
                "contracts",
                "config",
                "fixtures",
                "sources",
                "scripts",
            ):
                source = ROOT / name
                if source.is_dir():
                    shutil.copytree(source, package / name)
                else:
                    shutil.copy2(source, package / name)
            for directory in ("node_modules/dependency", ".venv/site-packages/tool"):
                target = package / directory
                target.mkdir(parents=True)
                (target / "example.json").write_text("not JSON", encoding="utf-8")
            command = [sys.executable, "scripts/validate_package.py"]
            result = subprocess.run(command, cwd=package, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            (package / "contracts/broken.json").write_text("not JSON", encoding="utf-8")
            result = subprocess.run(command, cwd=package, capture_output=True, text=True)
            self.assertEqual(result.returncode, 1)
            self.assertIn("json:contracts/broken.json", result.stdout)


if __name__ == "__main__":
    unittest.main()
