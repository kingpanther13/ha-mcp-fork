"""Profile the guard against the real file where whole-tree validation stalled."""
import cProfile
import faulthandler
import json
import os
from pathlib import Path
import pstats
import runpy
import shutil
import signal
import subprocess
import sys
import time

root = Path('/tmp/slf001-profile')
root.mkdir()
revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
assert revision == os.environ['PROBE_CANDIDATE']
source = Path(os.environ['PROBE_FILE'])
assert source.is_relative_to('tests/src/unit') and source.suffix == '.py'
target = root / 'repo' / source
target.parent.mkdir(parents=True)
shutil.copyfile(source, target)
(root / 'source.json').write_text(json.dumps({'revision': revision, 'file': str(source)}))
sys.path.insert(0, str(Path('scripts').resolve()))
sys.argv = ['scripts/private_test_access.py', '--repo', str(root / 'repo')]
profiler = cProfile.Profile()

def timeout(signum, frame):
    raise TimeoutError('Guard hotspot exceeded 120 seconds')

signal.signal(signal.SIGALRM, timeout)
signal.alarm(120)
faulthandler.dump_traceback_later(30, repeat=True)
start = time.monotonic()
try:
    profiler.enable()
    runpy.run_path('scripts/private_test_access.py', run_name='__main__')
finally:
    profiler.disable()
    signal.alarm(0)
    faulthandler.cancel_dump_traceback_later()
    (root / 'elapsed.txt').write_text(str(time.monotonic() - start))
    profiler.dump_stats(str(root / 'profile.pstats'))
    with (root / 'profile.txt').open('w') as stream:
        stats = pstats.Stats(profiler, stream=stream)
        stats.sort_stats('cumulative').print_stats(80)
        stats.sort_stats('tottime').print_stats(50)
    print((root / 'profile.txt').read_text())
