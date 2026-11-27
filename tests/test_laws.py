"""The bundled Bend laws are part of the project's test gate."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BEND = ROOT / "bend" / "bin" / "bend"


@pytest.mark.skipif(not BEND.exists(), reason="bundled Bend compiler is missing")
def test_bend_laws_and_proofs_check():
    assert (ROOT / "LAWS.bend").exists()
    assert (ROOT / "PROOF.bend").exists()

    result = subprocess.run(
        [str(BEND), "PROOF.bend"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    output = result.stdout + result.stderr

    assert result.returncode == 0, output
    assert "ALL PROOFS CHECK" in output, output


@pytest.mark.skipif(not BEND.exists(), reason="bundled Bend compiler is missing")
def test_bend_laws_gate_rejects_a_false_law(tmp_path):
    """The gate must not pass a deliberately falsified law."""

    laws = (ROOT / "LAWS.bend").read_text()
    laws = laws.replace(
        "{visible(False{}, models) == Nil{} : List<Nat>}",
        "{visible(False{}, models) == models : List<Nat>}",
    )
    (tmp_path / "LAWS.bend").write_text(laws)
    (tmp_path / "PROOF.bend").write_text((ROOT / "PROOF.bend").read_text())

    result = subprocess.run(
        [str(BEND), "PROOF.bend"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    output = result.stdout + result.stderr

    assert "SOME PROOFS FAIL" in output, output
