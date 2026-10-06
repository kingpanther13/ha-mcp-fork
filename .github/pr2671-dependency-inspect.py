import importlib.metadata as metadata
import importlib.util
from pathlib import Path

import homeassistant

print('HA', metadata.version('homeassistant'))
for name in ('quickjs', 'quickjs-ng'):
    try:
        print('INSTALLED', name, metadata.version(name))
    except metadata.PackageNotFoundError:
        print('ABSENT', name)
print('IMPORTABLE', importlib.util.find_spec('quickjs') is not None)
for dist in metadata.distributions():
    for requirement in dist.requires or []:
        if 'quickjs' in requirement.lower():
            print('CONSUMER', dist.metadata['Name'], requirement)
root = Path(homeassistant.__file__).parent
path = root / 'package_constraints.txt'
if path.is_file():
    print('CONSTRAINTS', [line for line in path.read_text().splitlines() if 'quickjs' in line.lower()])
for path in (root / 'components').glob('*/manifest.json'):
    if 'quickjs' in path.read_text().lower():
        print('CORE_INTEGRATION', path.parent.name, path.read_text())
