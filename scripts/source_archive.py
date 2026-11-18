#!/usr/bin/env python3
"""Create deterministic source input from an explicit allowlist, including local work."""
import argparse
import ast
import gzip
from pathlib import Path
import tarfile

ROOT = Path(__file__).resolve().parents[1]


def version():
    module = ast.parse((ROOT / 'src/tidylinux/__init__.py').read_text())
    return next(ast.literal_eval(node.value) for node in module.body
                if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == '__version__' for t in node.targets))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('destination', type=Path)
    args = parser.parse_args()
    value = version()
    spec = (ROOT / 'packaging/fedora/tidylinux.spec').read_text()
    import re
    import xml.etree.ElementTree as ET
    if re.search(r'^Version:\s+(\S+)', spec, re.M)[1] != value:
        parser.error('RPM and authoritative Python version differ')
    if ET.parse(ROOT / 'data/io.github.tidylinux.TidyLinux.metainfo.xml').find('.//release').get('version') != value:
        parser.error('AppStream and authoritative Python version differ')
    files = []
    for entry in ('src', 'tests', 'scripts', 'data', 'docs', 'packaging', '.github',
                  'pyproject.toml', 'README.md', 'CONTRIBUTING.md', 'LICENSE', '.gitignore'):
        path = ROOT / entry
        for file in (path.rglob('*') if path.is_dir() else [path]):
            if file.is_symlink():
                parser.error(f'Symlink is not allowed in release sources: {file}')
            excluded = any(part in {'__pycache__', '.pytest_cache', '.venv', '.git', '.aws'}
                           or part.endswith(('.egg-info', '.dist-info')) for part in file.parts)
            if file.is_file() and not excluded and file.suffix not in ('.pyc', '.pyo'):
                files.append(file)
    args.destination.parent.mkdir(parents=True, exist_ok=True)
    with args.destination.open('xb') as raw, gzip.GzipFile(fileobj=raw, mode='wb', filename='', mtime=0) as compressed:
        with tarfile.open(fileobj=compressed, mode='w') as archive:
            for file in sorted(files):
                info = archive.gettarinfo(str(file), f'tidylinux-{value}/{file.relative_to(ROOT)}')
                info.uid = info.gid = info.mtime = 0
                info.uname = info.gname = ''
                info.mode = 0o755 if file.suffix == '.sh' else 0o644
                with file.open('rb') as stream:
                    archive.addfile(info, stream)
    print(args.destination)


if __name__ == '__main__':
    main()
