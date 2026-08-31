from __future__ import annotations

from pathlib import Path
from typing import Any

from research_harness.adapters.base import RuntimeAdapter
from research_harness.contract.models import RuntimeLoaderConfig
from research_harness.contract.template import default_contract
from research_harness.runtime.loader import (
    RuntimeLoadRequest,
    import_runtime_factory,
    list_registered_runtimes,
    parse_entrypoint_target,
    resolve_runtime_load_request,
)
from research_harness.supervisor.runtime_factory import create_runtime


class StubRuntime(RuntimeAdapter):
    def __init__(self, *, project_id: str, state_dir: Path, marker: str = "ok") -> None:
        self.project_id = project_id
        self.state_dir = state_dir
        self.marker = marker

    def inspect(self):
        raise NotImplementedError

    def restart_worker(self) -> None:
        return None

    def relaunch(self, action: str) -> None:
        del action


    def stop(self) -> None:
        return None


def create_stub_runtime(*, project_id: str, state_dir: Path, **options: Any) -> RuntimeAdapter:
    return StubRuntime(
        project_id=project_id,
        state_dir=state_dir,
        marker=str(options.get("marker", "ok")),
    )


def test_parse_entrypoint_target() -> None:
    module, attribute = parse_entrypoint_target("policy_eval.harness:create_runtime")
    assert module == "policy_eval.harness"
    assert attribute == "create_runtime"


def test_import_runtime_factory_from_test_module() -> None:
    factory = import_runtime_factory(f"{__name__}:create_stub_runtime")
    runtime = factory(project_id="demo", state_dir=Path("/tmp/demo"))
    assert isinstance(runtime, StubRuntime)


def test_create_runtime_from_explicit_entrypoint(tmp_path: Path) -> None:
    request = RuntimeLoadRequest(
        label="entrypoint",
        entrypoint=f"{__name__}:create_stub_runtime",
        options={"marker": "loaded"},
    )
    runtime = create_runtime(
        request=request,
        project_id="demo",
        state_dir=tmp_path,
    )
    assert isinstance(runtime, StubRuntime)
    assert runtime.marker == "loaded"


def test_resolve_runtime_from_contract_loader(tmp_path: Path) -> None:
    contract = default_contract(project_id="demo", objective="obj")
    contract.runtime_loader = RuntimeLoaderConfig(
        entrypoint=f"{__name__}:create_stub_runtime",
        options={"marker": "contract"},
    )
    request = resolve_runtime_load_request(
        runtime=None,
        entrypoint=None,
        contract_runtime_loader=contract.runtime_loader,
        state_dir=tmp_path,
    )
    runtime = create_runtime(request=request, project_id="demo", state_dir=tmp_path)
    assert isinstance(runtime, StubRuntime)
    assert runtime.marker == "contract"


def test_list_registered_runtimes_includes_failing_worker() -> None:
    names = {runtime.name for runtime in list_registered_runtimes()}
    assert "failing-worker" in names


def test_cli_runtimes_list() -> None:
    from typer.testing import CliRunner

    from research_harness.cli import app

    result = CliRunner().invoke(app, ["runtimes", "list"])
    assert result.exit_code == 0, result.output
    assert "failing-worker" in result.output
