import collections

_tot = collections.Counter()
_n = collections.Counter()
_slow = collections.Counter()


def pytest_runtest_logreport(report):
    _tot[report.when] += report.duration
    _n[report.when] += 1
    if report.when in ("setup", "teardown"):
        _slow[(report.when, report.nodeid.split("::")[0])] += report.duration


def pytest_terminal_summary(terminalreporter):
    for w in ("setup", "call", "teardown"):
        terminalreporter.write_line(f"PHASE {w}: {_tot[w]:.1f}s over {_n[w]} reports")
    for (w, f), d in _slow.most_common(15):
        terminalreporter.write_line(f"SLOWFILE {w} {d:.1f}s {f}")
