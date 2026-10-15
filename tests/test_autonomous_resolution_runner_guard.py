import importlib.util
from pathlib import Path

import pytest


@pytest.fixture
def runner():
    path=Path(__file__).resolve().parents[1]/'scripts/run_autonomous_resolution.py'
    spec=importlib.util.spec_from_file_location('archived_resolution_runner',path)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_live_run_refuses_before_discovery_or_output(runner,tmp_path,monkeypatch):
    calls=[]
    monkeypatch.setattr(runner,'resolve_executable',lambda: calls.append('discovery'))
    monkeypatch.setattr(runner.subprocess,'run',lambda *a,**k: calls.append('subprocess'))
    output=tmp_path/'fresh'
    with pytest.raises(RuntimeError,match='Archived runner: no fresh calls; guarded path only'):
        runner.run(output,[])
    assert calls==[] and not output.exists()


def test_cli_default_refuses_before_offline_preparation(runner,tmp_path,monkeypatch):
    calls=[]
    monkeypatch.setattr(runner,'prepare',lambda *a: calls.append('prepare'))
    monkeypatch.setattr(runner,'resolve_executable',lambda: calls.append('discovery'))
    monkeypatch.setattr(runner.subprocess,'run',lambda *a,**k: calls.append('subprocess'))
    output=tmp_path/'fresh'
    monkeypatch.setattr('sys.argv',['run_autonomous_resolution.py','--output',str(output)])
    with pytest.raises(RuntimeError,match='Archived runner: no fresh calls; guarded path only'):
        runner.main()
    assert calls==[] and not output.exists()


def test_prepare_only_remains_offline(runner,tmp_path,monkeypatch):
    calls=[]
    monkeypatch.setattr(runner,'prepare',lambda *a: calls.append('prepare') or [])
    monkeypatch.setattr(runner,'resolve_executable',lambda: calls.append('discovery'))
    monkeypatch.setattr(runner.subprocess,'run',lambda *a,**k: calls.append('subprocess'))
    monkeypatch.setattr('sys.argv',['run_autonomous_resolution.py','--output',str(tmp_path/'fresh'),'--prepare-only'])
    runner.main()
    assert calls==['prepare']
