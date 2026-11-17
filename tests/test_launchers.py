"""Launcher recommendations never need to execute the downloaded application."""
from pathlib import Path
from unittest import TestCase

from tidylinux.launchers import helper_reason, recommend_launcher
from tidylinux.models import Candidate, Inspection


def inspection(paths, root="Demo-linux-x64"):
    return Inspection(Path("fixture.tar.gz"), 1, "0" * 64, 1, len(paths),
                      tuple(Candidate(p, "Linux executable") for p in paths), (), root, root)


class LauncherTests(TestCase):
    def test_recommendations(self):
        cases = [
            (["run"], "Demo", "run"),
            (["Demo/AppRun", "Demo/bin/runtime"], "Demo", "Demo/AppRun"),
            (["VSCode-linux-x64/code", "VSCode-linux-x64/chrome-sandbox",
              "VSCode-linux-x64/chrome_crashpad_handler"], "VSCode-linux-x64", "VSCode-linux-x64/code"),
            (["Demo-linux-x64/demo", "Demo-linux-x64/bin/tool"], "Demo-linux-x64", "Demo-linux-x64/demo"),
            (["bundle/start", "bundle/lib/runtime"], "bundle", "bundle/start"),
            (["bundle/a", "bundle/b"], "bundle", None),
            (["bundle/chrome-sandbox"], "bundle", None),
            (["a/AppRun", "b/AppRun"], "bundle", None),
            (["app/apple", "app/tool"], "application", None),
            ([], "bundle", None),
        ]
        for paths, root, expected in cases:
            with self.subTest(paths=paths):
                selected, explanation = recommend_launcher(inspection(paths, root))
                self.assertEqual(selected, expected)
                self.assertTrue(explanation)

    def test_helpers(self):
        for path in ("chrome-sandbox", "app/CHROME_CRASHPAD_HANDLER", "QtWebEngineProcess",
                     "app/thing-helper", "app/thing_helper", "app/node_modules/bin/tool",
                     "app/extensions/tool", "app/helpers/tool"):
            with self.subTest(path=path):
                self.assertIsNotNone(helper_reason(path))
        for path in ("app/code", "app/AppRun", "app/helper-tools", "app/node", "app/run"):
            with self.subTest(path=path):
                self.assertIsNone(helper_reason(path))
