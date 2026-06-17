# Top Workflows in the BioMate Catalog

This is a shortlist for fast retrieval — pick a workflow_id from here directly instead of calling `search_workflow` when the user's intent clearly matches one of these. For the full 2,455-workflow catalog use `search_workflow`.

## Drug discovery (61 workflows)

| workflow_id | Use it when the user wants… |
|---|---|
| `admet_screen` | ADMET screening of a SMILES list (hERG, CYP3A4, hepatotoxicity, BBB, solubility, etc.) |
| `admet_screen_with_autoloop` | Same as above but with auto-loop remediation: failed compounds trigger lead-optimization suggestions |
| `vina_docking` | AutoDock Vina docking against a single receptor PDB |
| `diffdock_blind` | DiffDock blind docking — no pocket specified |
| `pharmacophore_filter` | Pharmacophore-based virtual screening |
| `pbpk_model_human` | Whole-body PBPK in human; outputs Cmax, AUC, half-life |
| `pbpk_model_rat_to_human` | Allometric scaling rat → human |
| `boin_dose_escalation` | BOIN clinical dose-escalation simulation |
| `mtpi_dose_escalation` | mTPI-2 dose-escalation simulation |
| `andv_inhibitor_stack` | Full inhibitor design stack: pharmacophore → ADMET → docking → MD |

## Transcriptomics (865 workflows)

| workflow_id | Use when… |
|---|---|
| `nfcore_rnaseq` | RNA-seq pipeline — bulk RNA-seq from FASTQ to gene counts + DE |
| `nfcore_scrnaseq` | bioinformatics scrnaseq — single-cell RNA-seq (10x, Smart-seq) |
| `deseq2_de` | DESeq2 differential expression on a counts matrix |
| `edger_de` | edgeR differential expression alternative |
| `limma_voom_de` | limma-voom for low-count or microarray-like data |
| `clusterprofiler_enrich` | clusterProfiler / enrichGO over a gene list |
| `seurat_standard` | Seurat standard scRNA pipeline (QC, normalize, cluster, DE) |
| `gsea_msigdb` | GSEA against MSigDB hallmark / curated collections |

## Genomics (369 workflows)

| workflow_id | Use when… |
|---|---|
| `nfcore_sarek` | WGS variant-calling pipeline — germline / somatic variant calling from FASTQ |
| `gatk_haplotypecaller` | GATK HaplotypeCaller germline only |
| `mutect2_somatic` | Mutect2 somatic variant calling |
| `manta_sv` | Manta structural variant calling |
| `nfcore_methylseq` | bioinformatics methylseq — WGBS / RRBS methylation |
| `bcftools_norm_filter` | VCF normalization + filtering |
| `vep_annotate` | Ensembl VEP annotation of a VCF |

## Structural biology & cryo-EM (10+ workflows)

| workflow_id | Use when… |
|---|---|
| `cryosparc_standard_spa` | Standard single-particle reconstruction in CryoSPARC |
| `relion_standard_spa` | RELION single-particle alternative |
| `alphafold2_monomer` | AlphaFold2 monomer prediction |
| `alphafold_multimer` | AlphaFold-Multimer complex prediction |
| `colabfold_msa_then_af2` | ColabFold MSA → AlphaFold2 |

## Proteomics (113 workflows)

| workflow_id | Use when… |
|---|---|
| `maxquant_lfq` | MaxQuant label-free quantification |
| `fragpipe_tmt` | FragPipe TMT quantification |
| `msstats_de` | MSstats differential abundance |
| `proteinprophet_validate` | ProteinProphet validation |

## Epigenomics & ChIP/ATAC (133 workflows)

| workflow_id | Use when… |
|---|---|
| `nfcore_chipseq` | bioinformatics chipseq |
| `nfcore_atacseq` | bioinformatics atacseq |
| `macs3_peakcall` | MACS3 peak calling |
| `homer_motif` | HOMER motif discovery |

## Regulatory / CRO / IND

| workflow_id | Use when… |
|---|---|
| `ind_module_261_narrative` | Generate IND §2.6.1 pharmacology written summary (DOCX) |
| `cro_compliance_package` | Bundle ADMET + PBPK + tox results into a CRO submission ZIP |
| `health_canada_sbd_extract` | Scrape and structure Health Canada SBD docs |

## When in doubt

Call `search_workflow(query=<user's verbatim phrase>, limit=5)` — the catalog has 2,455 workflows across 34 domains and the routing model is well-tuned. The list above is **fast-path memorization**, not exhaustive.
