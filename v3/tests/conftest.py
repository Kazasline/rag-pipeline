"""Shared fixtures: a synthetic multi-project corpus + fully wired engine
using the deterministic HashEmbedder (no model server needed).

The corpus intentionally contains the spec's trap cases: two projects with
similar documents (wrong-project trap), an exact duplicate, and a revision
family (R00 -> R01).
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from alirag.config import Config, EmbedConfig  # noqa: E402
from alirag.inventory import scan  # noqa: E402
from alirag.manifest import Manifest  # noqa: E402
from alirag.safety import SafetyGuard  # noqa: E402

DAWSON_TENDER = """LANDSCAPE TENDER SPECIFICATION — DAWSON PROJECT
Section 4.2 Softscape Works
The Contractor shall supply and plant Samanea saman (Rain Tree) of minimum
trunk diameter 150mm at locations shown on drawing L-201 R01 Zone B.
Root barrier 50mm TD shall be installed along all planter edges per detail
D-7. Refer to LAI-003 for the instruction on revised turf areas.
Final claim amount RM50,569.30 as certified.
"""

DAWSON_TENDER_R0 = DAWSON_TENDER.replace("L-201 R01", "L-201 R00").replace(
    "RM50,569.30", "RM48,000.00")

MERIDIAN_SPEC = """LANDSCAPE SPECIFICATION — MERIDIAN TOWERS
Section 4.2 Softscape Works
The Contractor shall supply Ficus microcarpa of minimum trunk diameter 100mm
per drawing MT-L-05. Final claim amount RM99,111.22 as certified.
"""

LAI_003 = """LANDSCAPE ARCHITECT INSTRUCTION LAI-003
Project: Dawson. Date: 12 Aug 2026.
Instruction: replace cow grass with Zoysia matrella in Zone B turf areas.
This instruction supersedes the turf specification in the tender clause 4.2.
"""


@pytest.fixture()
def corpus(tmp_path: Path) -> Path:
    src = tmp_path / "sources"
    (src / "Dawson" / "Tender").mkdir(parents=True)
    (src / "Dawson" / "Instructions").mkdir(parents=True)
    (src / "Meridian" / "Specs").mkdir(parents=True)
    (src / "Dawson" / "Tender" / "Landscape Tender Spec R01.txt").write_text(
        DAWSON_TENDER, encoding="utf-8")
    (src / "Dawson" / "Tender" / "Landscape Tender Spec R00.txt").write_text(
        DAWSON_TENDER_R0, encoding="utf-8")
    (src / "Dawson" / "Instructions" / "LAI-003 turf instruction.txt").write_text(
        LAI_003, encoding="utf-8")
    # exact duplicate of the LAI in a second location
    (src / "Dawson" / "LAI-003 turf instruction.txt").write_text(
        LAI_003, encoding="utf-8")
    (src / "Meridian" / "Specs" / "Landscape Spec.txt").write_text(
        MERIDIAN_SPEC, encoding="utf-8")
    # garbage that must not be indexed as knowledge
    (src / "model.gguf").write_bytes(b"\x00" * 64)
    return src


@pytest.fixture()
def cfg(tmp_path: Path, corpus: Path) -> Config:
    c = Config(workspace=str(tmp_path / "ALI_RAG"), source_roots=[str(corpus)],
               embed=EmbedConfig(provider="hash", dim=256))
    c.dense.backend = "memmap"
    return c


@pytest.fixture()
def ingested(cfg: Config):
    """Inventory + ingest the synthetic corpus; yields (cfg, manifest)."""
    from alirag.ingest import Ingestor
    guard = SafetyGuard(cfg.source_roots, cfg.workspace)
    mf = Manifest(cfg.manifest_db)
    counts = scan(cfg, guard, mf, progress_every=0)
    assert counts["new"] >= 5
    ing = Ingestor(cfg, mf=mf)
    ing.run()
    yield cfg, mf
    ing.close()
