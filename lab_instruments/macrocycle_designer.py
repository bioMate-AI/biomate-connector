"""
Macrocycle Designer  (DBTL Design engine — the propose_fn)
=========================================================
Enumerates head-to-tail cyclic peptide SMILES over a small amino-acid alphabet with
controllable N-methylation, so BioMate's Design engine can PROPOSE candidates that the
permeability scorer (cyclic_peptide_scorer.PermeabilityScorer) then ranks. This is the
generative half of the loop the sim demo faked.

Cost note: N-methylation and cyclization are the expensive parts of synthesis
(~$495/residue US for N-Me). `propose(...)` exposes `max_nmethyl` so round-1 batches
can be biased toward cheap-to-synthesize candidates.

RDKit imported lazily; validity is checked with MolFromSmiles (invalid enumerations
are dropped).
"""
from __future__ import annotations
import random
from typing import Dict, List, Optional

# side-chain SMILES fragment for the residue's alpha carbon (None => glycine, CH2)
RESIDUES: Dict[str, Optional[str]] = {
    "Gly": None, "Ala": "C", "Val": "C(C)C", "Leu": "CC(C)C", "Ile": "C(C)CC",
    "Pro": "__PRO__", "Phe": "Cc1ccccc1", "Tyr": "Cc1ccc(O)cc1", "Trp": "Cc1c[nH]c2ccccc12",
    "Ser": "CO", "Thr": "C(C)O", "Lys": "CCCCN", "Arg": "CCCNC(=N)N", "Asp": "CC(=O)O",
    "Glu": "CCC(=O)O", "Asn": "CC(N)=O", "Gln": "CCC(N)=O", "His": "Cc1cnc[nH]1",
    "Met": "CCSC", "Cys": "CS",
}
# a lipophilic-leaning default alphabet (macrocycle permeability favors low polar SA)
DEFAULT_ALPHABET = ["Ala", "Val", "Leu", "Ile", "Phe", "Pro", "Gly", "Thr", "Tyr", "Trp"]


def _residue_unit(name: str, first: bool, last: bool, n_methyl: bool) -> str:
    """One backbone unit -[N(H/Me)-CHR-C(=O)]-. The macrocycle ring bond uses the
    two-digit label %99 so it never collides with side-chain ring digits (Phe/Trp/His
    reuse 1,2,3)."""
    sc = RESIDUES[name]
    if sc == "__PRO__":                       # proline surrogate (keeps SMILES valid, cheap)
        sc = "C"
    if first:
        n = "N%99(C)" if n_methyl else "N%99"  # ring-open on residue-1 backbone N
    else:
        n = "N(C)" if n_methyl else "N"
    co = "C%99(=O)" if last else "C(=O)"       # ring-close on final carbonyl carbon
    if sc is None:                             # glycine (CH2, no stereocenter)
        return f"{n}C{co}"
    return f"{n}[C@@H]({sc}){co}"


def build_cyclic_smiles(seq: List[str], nmethyl_idx: Optional[List[int]] = None) -> str:
    """Head-to-tail cyclic peptide SMILES; %99 joins residue-1 N to the final carbonyl."""
    nmethyl_idx = set(nmethyl_idx or [])
    n = len(seq)
    return "".join(_residue_unit(res, i == 0, i == n - 1, i in nmethyl_idx)
                   for i, res in enumerate(seq))


def propose(params: Dict) -> List[Dict]:
    """Generate candidate cyclic peptides. params:
      n (int)            how many to attempt (default 200)
      length (int|tuple) residues per macrocycle (default (6,8))
      max_nmethyl (int)  cap N-methylations per peptide for cost (default 2)
      alphabet (list)    residue names to sample
      seed (int)         RNG seed (varies the batch each round)
    Returns list of {seq, residues, n_methyl, smiles}."""
    from rdkit import Chem
    rng = random.Random(params.get("seed", 0))
    n = params.get("n", 200)
    length = params.get("length", (6, 8))
    max_nm = params.get("max_nmethyl", 2)
    alpha = params.get("alphabet", DEFAULT_ALPHABET)
    out, seen = [], set()
    for _ in range(n * 4):
        if len(out) >= n:
            break
        L = rng.randint(*length) if isinstance(length, (tuple, list)) else int(length)
        seq = [rng.choice(alpha) for _ in range(L)]
        k = rng.randint(0, max_nm)
        nm = sorted(rng.sample(range(L), k)) if k else []
        smi = build_cyclic_smiles(seq, nm)
        m = Chem.MolFromSmiles(smi)
        if m is None:
            continue
        can = Chem.MolToSmiles(m)
        if can in seen:
            continue
        seen.add(can)
        out.append({"seq": "-".join(seq), "residues": seq, "n_methyl": k,
                    "nmethyl_idx": nm, "smiles": can})
    return out
