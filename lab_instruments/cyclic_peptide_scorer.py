"""
Production Macrocycle-Permeability Scorer  (DBTL Design engine)
==============================================================
Loads the pickled **HistGradientBoosting** model — fit on CycPeptMPDB PAMPA — and
scores candidate peptides from SMILES. This is the model that won the head-to-heads
on the honest **scaffold (OOD) split**:

    GBM-on-descriptors  >  2D-GCN  ≈  descriptor floor  >  multi-task MLP  ≈  FM (ChemBERTa) embeddings

Two pickle variants are supported (auto-detected via `has_3d` in the pickle):
  - v1  cycpep_perm_histgbm.pkl      RDKit 2D descriptors only  — scaffold R² ≈ 0.11
  - v2  cycpep_perm_histgbm_3d.pkl   2D + 3D branch (ETKDG conformer -> rdFreeSASA
        polar-SASA / 3D-PSA + Descriptors3D shape) — recovers the 3D chameleonic
        signal (target ≈ 0.29). v2 pays conformer-generation cost per candidate.

The featurizer is byte-identical to the training script
(research/autoimmune_corpus/cycpep_scorer_3d.py) and aligns by descriptor NAME via
feat_names stored in the pickle (robust to RDKit version drift — an absent/failed
descriptor falls back to the training median).

A rough triage prefilter, not a leaderboard model — the moat is the wet-lab flywheel
that lifts it, not this scorer. RDKit is imported lazily so this module (and the DBTL
sim demo) import fine without it.
"""
from __future__ import annotations
import pickle
from typing import Any, Callable, Dict, List

_UNPARSEABLE = -12.0   # very low logPapp -> DESIGN prefilter drops it

D3_NAMES = ["PSA3D", "SASA_total", "PSA3D_frac", "Asphericity", "Eccentricity",
            "RadiusOfGyration", "SpherocityIndex", "PMI1", "PMI2", "PMI3",
            "NPR1", "NPR2", "InertialShapeFactor"]


def _descriptor_list():
    from rdkit.Chem import Descriptors
    return [(n, f) for n, f in Descriptors._descList]


def _calc_3d(mol) -> Dict[str, float]:
    """3D features from ONE ETKDG macrocycle conformer. Returns {} on any failure.
    Byte-identical to cycpep_scorer_3d.calc_3d."""
    from rdkit import Chem
    from rdkit.Chem import AllChem, Descriptors3D, rdFreeSASA
    try:
        m = Chem.AddHs(mol)
        ps = AllChem.ETKDGv3(); ps.randomSeed = 0
        ps.useMacrocycleTorsions = True; ps.useRandomCoords = True
        if AllChem.EmbedMolecule(m, ps) != 0:
            if AllChem.EmbedMolecule(m, AllChem.ETKDGv2()) != 0:
                return {}
        try:
            AllChem.MMFFOptimizeMolecule(m, maxIters=200)
        except Exception:
            pass
        radii = rdFreeSASA.classifyAtoms(m)
        rdFreeSASA.CalcSASA(m, radii)
        def sasa(a): return float(a.GetProp("SASA")) if a.HasProp("SASA") else 0.0
        def polar(a):
            if a.GetSymbol() in ("N", "O"): return True
            return a.GetSymbol() == "H" and any(nb.GetSymbol() in ("N", "O") for nb in a.GetNeighbors())
        total = sum(sasa(a) for a in m.GetAtoms())
        psa3d = sum(sasa(a) for a in m.GetAtoms() if polar(a))
        return {"PSA3D": psa3d, "SASA_total": total,
                "PSA3D_frac": (psa3d / total) if total else float("nan"),
                "Asphericity": Descriptors3D.Asphericity(m),
                "Eccentricity": Descriptors3D.Eccentricity(m),
                "RadiusOfGyration": Descriptors3D.RadiusOfGyration(m),
                "SpherocityIndex": Descriptors3D.SpherocityIndex(m),
                "PMI1": Descriptors3D.PMI1(m), "PMI2": Descriptors3D.PMI2(m),
                "PMI3": Descriptors3D.PMI3(m), "NPR1": Descriptors3D.NPR1(m),
                "NPR2": Descriptors3D.NPR2(m),
                "InertialShapeFactor": Descriptors3D.InertialShapeFactor(m)}
    except Exception:
        return {}


def featurize_one(smiles: str, feat_names: List[str], medians: List[float],
                  want_3d: bool = False) -> List[float]:
    from rdkit import Chem
    m = Chem.MolFromSmiles(str(smiles))
    if m is None:                                  # unparseable -> caller drops it
        raise ValueError(f"unparseable SMILES: {smiles!r}")
    vals: Dict[str, float] = {}
    for name, f in _descriptor_list():
        try:
            vals[name] = float(f(m))
        except Exception:
            pass
    if want_3d:
        vals.update(_calc_3d(m))
    out: List[float] = []
    for i, name in enumerate(feat_names):
        v = vals.get(name)
        if v is None or v != v or v in (float("inf"), float("-inf")):
            v = float(medians[i])                  # per-feature fallback to training median
        out.append(v)
    return out


class PermeabilityScorer:
    """Callable: candidate dict (needs 'smiles') -> predicted logPapp (float)."""

    def __init__(self, pkl_path: str):
        with open(pkl_path, "rb") as fh:
            d = pickle.load(fh)
        self.model = d["model"]
        self.feat = d["feat_names"]
        self.med = d["medians"]
        self.has_3d = bool(d.get("has_3d", False))
        self.scaffold_r2 = d.get("scaffold_r2")
        self.random_r2 = d.get("random_r2")
        self.target = d.get("target")

    def __call__(self, candidate: Dict[str, Any]) -> float:
        smi = candidate.get("smiles")   # a structure, not the 'seq' identifier
        if not smi:
            return _UNPARSEABLE
        try:
            x = featurize_one(smi, self.feat, self.med, want_3d=self.has_3d)
            return float(self.model.predict([x])[0])
        except Exception:
            return _UNPARSEABLE


def load_perm_scorer(pkl_path: str) -> Callable[[Dict[str, Any]], float]:
    """Return a score_fn for make_controller / make_cyclic_peptide_design.

    pkl_path: local path to cycpep_perm_histgbm[_3d].pkl (sync from
    s3://knowledge-datasets-1759951976/cyclic_peptide/scorer/)."""
    return PermeabilityScorer(pkl_path)
