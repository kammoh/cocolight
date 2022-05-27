from pathlib import Path
import pytest
import xeda

SCRIPT_DIR = Path(__file__).parent

runner = xeda.flow_runner.FlowRunner()

designs = list(Path(SCRIPT_DIR.parent / "examples").glob("**/*.toml"))
print("designs:", "\n".join(f"  {d}" for d in designs), sep="\n")

@pytest.mark.parametrize(
        'toml', designs
    )
def test_all_examples(toml):
    design = xeda.Design.from_toml(toml)
    completed_flow = runner.run_flow(xeda.flows.GhdlSim, design)
    assert completed_flow.succeeded


if __name__ == "__main__":
    for toml in designs:
        test_all_examples(toml)
