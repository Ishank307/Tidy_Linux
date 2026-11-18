"""Release source reproducibility and packaging safety contracts."""
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class PackagingTests(unittest.TestCase):
    def test_clean_source_is_reproducible_and_contains_local_features(self):
        with tempfile.TemporaryDirectory() as temporary:
            outputs = [Path(temporary) / name for name in ('a.tar.gz', 'b.tar.gz')]
            for path in outputs:
                subprocess.run(['python3', str(ROOT / 'scripts/source_archive.py'), str(path)], check=True, capture_output=True)
            self.assertEqual(outputs[0].read_bytes(), outputs[1].read_bytes())
            with tarfile.open(outputs[0]) as archive:
                names = [p.name for p in archive.getmembers()]
                self.assertTrue(any(n.endswith('/tidylinux/cleanup.py') for n in names))
                self.assertTrue(any(n.endswith('/tidylinux/updates.py') for n in names))
                for name in names:
                    self.assertFalse(any(p.endswith(('.egg-info', '.dist-info')) for p in Path(name).parts))
                    self.assertFalse(set(Path(name).parts) & {'.git', '.venv', '__pycache__', '.pytest_cache', 'dist', '.aws'})

    def test_rpm_has_no_user_data_payload_or_application_scriptlets(self):
        spec = (ROOT / 'packaging/fedora/tidylinux.spec').read_text()
        for section in ('%pre\n', '%post\n', '%preun\n', '%postun\n', '%trigger'):
            self.assertNotIn(section, spec)
        for forbidden in ('/home/', '/root/', 'XDG_DATA_HOME', 'sudo ', 'rm -rf'):
            self.assertNotIn(forbidden, spec)
        self.assertIn('BuildArch:      noarch', spec)
        self.assertIn('%pyproject_install', spec)
