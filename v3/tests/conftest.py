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


# Boilerplate that saturates real construction documents, plus a distinctive
# subject per file. Used to build a corpus large enough that document-frequency
# weighting is actually exercised (round-4 reviewer N4-8: the 6-chunk fixture
# is below MIN_DOCS_FOR_DF, so NO test ever ran the instrument that runs in
# production, and disabling the DF plumbing entirely left the suite green).
_BOILER = ("The Contractor shall provide and install the works in accordance "
           "with the specification and the drawings. Refer to the relevant "
           "section for general requirements. Locations are shown on the "
           "drawings for the respective items. ")


@pytest.fixture()
def big_corpus(tmp_path: Path) -> Path:
    """A corpus above MIN_DOCS_FOR_DF, so the measured-DF path is live."""
    src = tmp_path / "bigsources" / "Dawson"
    src.mkdir(parents=True)
    # Each sheet carries the shared boilerplate plus TWO distinctive tokens, so
    # a real question can share two discriminating terms while the boilerplate
    # stays common. A corpus where every document uses the same vocabulary for
    # its subject has no discriminating terms at all — that is a property of
    # the corpus, not of the floor.
    subjects = ["samanea", "zoysia", "podocarpus", "ficus", "bougainvillea",
                "lagerstroemia", "plumeria", "tabebuia", "cassia", "mimusops"]
    zones = ["boulevard", "podium", "plaza", "carpark", "riverwalk",
             "courtyard", "rooftop"]
    for i in range(260):
        subject = subjects[i % len(subjects)]
        zone = zones[i % len(zones)]
        (src / f"spec_{i:03d}.txt").write_text(
            f"SPECIFICATION SHEET {i:03d}\n" + _BOILER * 3
            + f"Planting for the {zone} area uses {subject} at "
              f"{4 + (i % 5)}m centres, girth {100 + i}mm.\n",
            encoding="utf-8")
    return src


@pytest.fixture()
def big_ingested(tmp_path: Path, big_corpus: Path):
    """(cfg, manifest) over a corpus where doc-frequency weighting applies."""
    from alirag.ingest import Ingestor
    c = Config(workspace=str(tmp_path / "BIG_RAG"), source_roots=[str(big_corpus)],
               embed=EmbedConfig(provider="hash", dim=256))
    c.dense.backend = "memmap"
    guard = SafetyGuard(c.source_roots, c.workspace)
    mf = Manifest(c.manifest_db)
    scan(c, guard, mf, progress_every=0)
    ing = Ingestor(c, mf=mf)
    ing.run()
    yield c, mf
    ing.close()


@pytest.fixture()
def cfg(tmp_path: Path, corpus: Path) -> Config:
    c = Config(workspace=str(tmp_path / "ALI_RAG"), source_roots=[str(corpus)],
               embed=EmbedConfig(provider="hash", dim=256))
    c.dense.backend = "memmap"
    return c


# Five DISTINCT validated questions over the fixture corpus.
#
# The benchmark harness refuses fewer than MIN_DISTINCT_QUESTIONS and refuses
# duplicates outright (round-2 reviewer N5: 25 copies of one question satisfied
# every honesty gate, including "percentiles_meaningful"). Tests that need a
# successful benchmark run share this set rather than each inventing a
# too-small one.
BENCH_QUESTIONS = [
    {"q": "which document is the LAI-003 turf instruction?",
     "expect_file": "LAI-003 turf instruction.txt", "kind": "exact",
     "mode": "", "project": "Dawson", "reviewed": True},
    {"q": "what trunk diameter is required for the rain trees?",
     "expect_file": "Landscape Tender Spec R01.txt", "kind": "semantic",
     "mode": "", "project": "Dawson", "reviewed": True},
    {"q": "what does the Meridian spec require for Ficus microcarpa?",
     "expect_file": "Landscape Spec.txt", "kind": "semantic",
     "mode": "", "project": "Meridian", "reviewed": True},
    {"q": "which grass replaces cow grass in the Zone B turf areas?",
     "expect_file": "LAI-003 turf instruction.txt", "kind": "semantic",
     "mode": "", "project": "Dawson", "reviewed": True},
    {"q": "where is the root barrier detail specified for planter edges?",
     "expect_file": "Landscape Tender Spec R01.txt", "kind": "semantic",
     "mode": "", "project": "Dawson", "reviewed": True},
]


def write_questions(path, records=None):
    """Write a JSONL question file (defaults to the shared validated set)."""
    import json
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(r) for r in (records or BENCH_QUESTIONS)),
        encoding="utf-8")
    return path


@pytest.fixture()
def bench_questions(tmp_path: Path) -> Path:
    return write_questions(tmp_path / "questions.jsonl")


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
