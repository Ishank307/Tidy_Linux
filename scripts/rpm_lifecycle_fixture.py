#!/usr/bin/env python3
"""Disposable-container fixture only; never operate on a real home."""
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tarfile

root = Path('/home/tidytest/fixture')
if os.environ.get('TIDYLINUX_CONTAINER_TEST') != '1' or os.getuid() == 0 or Path.home() != root.parent:
    raise SystemExit('Run only as tidytest inside the disposable RPM container.')
os.environ['XDG_DATA_HOME'] = str(root / 'data')


def snapshot():
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob('*')) if p.is_file()}


if sys.argv[1] == 'seed':
    from tidylinux.installer import Installer
    from tidylinux.archive import inspect_archive
    root.mkdir()
    installer = Installer()
    for version in ('1', '2'):
        source = root / f'fixture-{version}.tar.gz'
        content = b'#!/bin/sh\nexit 0\n'
        with tarfile.open(source, 'w:gz') as archive:
            entry = tarfile.TarInfo('Fixture/run')
            entry.size, entry.mode = len(content), 0o755
            archive.addfile(entry, io.BytesIO(content))
        inspection = inspect_archive(source)
        if version == '1':
            record = installer.install(inspection, 'Fixture/run', 'RPM lifecycle fixture', version)
        else:
            record = installer.update(record, inspection, 'Fixture/run', version)
    from tidylinux.sources import SourceStore
    SourceStore(installer.paths).configure(record, 'https://github.com/Publisher/Fixture', confirmed=True)
    (root / 'external-settings').write_text('preserve me')
    Path('/home/tidytest/before.json').write_text(json.dumps(snapshot(), sort_keys=True))
else:
    assert snapshot() == json.loads(Path('/home/tidytest/before.json').read_text()), 'Managed fixture data changed'
    print('Managed application, rollback, source metadata, shortcut, archives and external settings preserved.')
