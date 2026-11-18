#!/usr/bin/env python3
"""Check repository-relative Markdown links and images without network access."""
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
errors = []
for path in [ROOT / 'README.md', ROOT / 'CONTRIBUTING.md', *sorted((ROOT / 'docs').rglob('*.md')), *sorted((ROOT / 'packaging').rglob('*.md'))]:
    for match in re.finditer(r'!?\[[^\]]*\]\(([^\s)]+)(?:\s+"[^"]*")?\)', path.read_text()):
        link = match[1].strip('<>')
        parts = urlsplit(link)
        if parts.scheme or (not parts.path and not parts.fragment):
            continue
        target = (path.parent / unquote(parts.path)).resolve() if parts.path else path.resolve()
        if not target.is_relative_to(ROOT) or not target.exists():
            errors.append(f'{path.relative_to(ROOT)}: missing/escaping link {link}')
        elif parts.fragment and target.suffix == '.md':
            anchors = set()
            counts = {}
            for heading in re.findall(r'^#{1,6}\s+(.+?)\s*#*$', target.read_text(), re.M):
                slug = re.sub(r'[^\w\- ]', '', heading.lower()).replace(' ', '-')
                count = counts.get(slug, 0)
                counts[slug] = count + 1
                anchors.add(slug + (f'-{count}' if count else ''))
            anchors.update(re.findall(r'<a\s+(?:id|name)=[\"\']([^\"\']+)', target.read_text()))
            if unquote(parts.fragment) not in anchors:
                errors.append(f'{path.relative_to(ROOT)}: missing anchor {link}')
if errors:
    raise SystemExit('\n'.join(errors))
print('Relative Markdown link/image targets exist.')
