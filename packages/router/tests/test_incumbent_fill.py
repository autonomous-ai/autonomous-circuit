"""Compiler copper must not be mixed into a partial route by default."""
from types import SimpleNamespace
import pytest
from circuitpy import router_bridge, toolchain
from routerlib import portfolio
from routerlib.model import RoutingSolution, Trace, Point, TOP
from test_specctra import _problem

@pytest.mark.parametrize('opt_in', [False, True])
def test_incumbent_fill_requires_explicit_opt_in(tmp_path, monkeypatch, opt_in):
    if opt_in:
        monkeypatch.setenv(router_bridge.FILL_ENV, '1')
    else:
        monkeypatch.delenv(router_bridge.FILL_ENV, raising=False)
    empty = RoutingSolution(router='empty')
    incumbent = RoutingSolution(router='compiler', traces=(
        Trace(id='sig', net='n_sig', layer=TOP, points=(Point(-5, 0), Point(5, 0)), width_mm=.2),
        Trace(id='gnd', net='n_gnd', layer=TOP, points=(Point(-5, 5), Point(5, 5)), width_mm=.5),
    ))
    def run(dsn, ses, **kwargs):
        ses.write_text('(session tiny (routes (resolution um 10) (network_out)))')
        return SimpleNamespace(output='')
    monkeypatch.setattr(toolchain, 'run_freerouting', run)
    monkeypatch.setattr(router_bridge, '_registry', lambda: {})
    monkeypatch.setattr(portfolio, 'route', lambda *a, **kw: SimpleNamespace(solution=empty, stages=[]))
    report = {}
    result = router_bridge._freerouting_solution(_problem(), tmp_path, report, incumbent)
    assert result.traces == (incumbent.traces if opt_in else ())
    assert result.complete == opt_in
    assert ('filledFromIncumbent' in report) == opt_in
