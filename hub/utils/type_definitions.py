from pathlib import Path

# Binary examples cannot live in this file, so they are files in examples/,
# made from the text examples by examples/make-examples.sh.
EXAMPLES_DIR = Path(__file__).resolve().parent / "examples"

TYPE_DEFINITIONS = [
    {
        "id": "JSON",
        "input": True,
        "output": True,
        "example": '{"samples":[{"id":"sample_1","condition":"control","value":12.5},{"id":"sample_2","condition":"treated","value":18.2}],"metadata":{"organism":"example","assay":"measurement"}}',
    },
    {
        "id": "TEXT",
        "input": True,
        "output": True,
        "example": "Hello, World",
    },
    {
        "id": "FASTA",
        "input": True,
        "output": True,
        "example": ">seq\nTTGCACTGACCTGAAGTCTTGGAGTATGACCGCGGCTCGGCTCTATCGAACGCTCGATCTAGCGCTATAGGTGGTGCCGAAGGCGGTCTGTCGTCGTA",
    },
    {
        "id": "FASTQ",
        "input": True,
        "output": True,
        "example": "@read1\nGCTAAAGACAATTACATAACATACACGTCAGCACGAAACTTGTTGGCCCAGTGTGAATCGCTTAAGGGTTAAGTAAGTGT\n+\nIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIII\n@read2\nGCACGAAACTTGTTGGCCCAGTGTGAATCGCTTAAGGGTTAAGTAAGTGTGATGCATACGCCTTTACTTGCTGTGTCCAC\n+\nIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIII\n@read3\nCTTAAGGGTTAAGTAAGTGTGATGCATACGCCTTTACTTGCTGTGTCCACCCCATCGGACTGGCATTTTTATTACACTCA\n+\nIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIII\n@read4\nCCTTTACTTGCTGTGTCCACCCCATCGGACTGGCATTTTTATTACACTCAGAAACAGAACTCGGGTAATTTTGACAGGTC\n+\nIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIII\n@read5\nTGGCATTTTTATTACACTCAGAAACAGAACTCGGGTAATTTTGACAGGTCACGCAGAGGCGCGCCCTCCTGAAGTGCGTG\n+\nIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIII\n@read6\nTCGGGTAATTTTGACAGGTCACGCAGAGGCGCGCCCTCCTGAAGTGCGTGGACACTCGCTATGAATCTCTGATTTACCCA\n+\nIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIII",
    },
    {
        "id": "NUM",
        "input": True,
        "output": True,
        "example": "0.123\n3.432\n2.341\n1.323\n7.538\n4.122\n0.242\n0.654\n5.633",
    },
    {
        "id": "DNA",
        "input": True,
        "output": True,
        "example": "CGTACGTAGCTGACTGATCGTAGCTAGCTGACTGACTAGCTGATCGTAGCTGATCGTACGTAGCTAGCTAGCTGACTAGCTGATCGTACGTAGCTGAC",
    },
    {
        "id": "Multi-FASTA",
        "input": True,
        "output": True,
        # Four aligned taxa with informative shared substitutions for tree tools.
        "example": (
            '>seq1_human\n'
            'TTGATCCTTAATTGCGGCTTCGATTACTAAAAAACAGGGACAGCATCAACCAAACTGCTTACATTACTGTCGGCGCAACAGCGCGGAAACCGGTTGTTAA\n'
            '>seq2_cat\n'
            'TTGACCCTTAACTGCGGCTTCAATTACTAATAAACAGGGAAAGCATTACCCAAACTGCTTACATTACTGTCGGCGCAACAGCGCCGCAACCGATTGTTAA\n'
            '>seq3_mouse\n'
            'CTGACGTTTAAATGCGGCTGGAATTATTCAAAAACTGGTGAAGCATTTCCCAAGTTGCTTCCATTACAGTCTGCGGAACAGCGTCGAGAGCGATCGTAAA\n'
            '>seq4_dog\n'
            'CTGACGTCGAAATGCGGCTAGTATGATTAAAGAACTGGTAAAGCATTACCCAAATTGCTTCCATTACAGTCTGCGGAACAGCGTCGAAATCGATCGTAAA'
        ),
    },
    {
        "id": "EFA",
        "input": True,
        "output": True,
        "example": "<none.1\n>alpha\nACGTACGTACGTACGT\n>beta\nACGTACGTTCGTACGT\n>gamma\nACGTACGTACGTTCGT\n<abc.1\n>alpha\nACGTACGTACGTACGT\n>beta\nACGTACGTTCGTACGT\n>gamma\nACGTACGTACGTTCGT",
    },
    {
        "id": "BIN",
        "input": True,
        "output": True,
        "example": "0\n1\n0",
    },
    {
        "id": "RNA",
        "input": True,
        "output": True,
        "example": "CGUACGUAGCUGACUGAUCGAUGCUACGUAGCUGACGUAGCUAGCUAGCUAGCUAGCUAGCUAGCUAGCUAGCUAGCUAGCUAGCUAGCUAGCUAGCUA",
    },
    {
        "id": "AminoAcids",
        "input": True,
        "output": True,
        "example": "ACDEFGHIKLMNPQRSTVWY" * 5,
    },
    {
        "id": "PackagedFASTQ",
        "input": True,
        "output": True,
        # "example": "",
    },
    {
        "id": "POS",
        "input": True,
        "output": False,
    },
    {
        "id": "SVG",
        "input": False,
        "output": True,
        "example": "<svg width='100' height='100'><rect width='100' height='100' style='fill:blue'/></svg>",
    },
    {
        "id": "Group",
        "input": False,
        "output": True,
        # "example": ""
    },
    {
        "id": "VCF",
        "input": True,
        "output": True,
        "example": (
            "##fileformat=VCFv4.2\n"
            "##contig=<ID=seq,length=98>\n"
            "##FILTER=<ID=PASS,Description=\"All filters passed\">\n"
            "##FORMAT=<ID=GT,Number=1,Type=String,Description=\"Genotype\">\n"
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tsample\n"
            "seq\t10\t.\tC\tA\t60\tPASS\t.\tGT\t0/1"
        ),
    },
    {
        "id": "BCF",
        "input": True,
        "output": True,
        "kind": "binary",
        "example_file": "example.bcf",
    },
    {
        "id": "SAM",
        "input": True,
        "output": True,
        "example": (
            "@HD\tVN:1.6\tSO:queryname\n"
            "@SQ\tSN:seq\tLN:98\n"
            "read1\t99\tseq\t1\t60\t40M\t=\t59\t98\tTTGCACTGACCTGAAGTCTTGGAGTATGACCGCGGCTCGG\tIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIII\n"
            "read1\t147\tseq\t59\t60\t40M\t=\t1\t-98\tCTAGCGCTATAGGTGGTGCCGAAGGCGGTCTGTCGTCGTA\tIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIII"
        ),
    },
    {
        "id": "BAI",
        "input": True,
        "output": True,
        "kind": "binary",
        "example_file": "example.bam.bai",
    },
    {
        "id": "MMI",
        "input": True,
        "output": True,
        "kind": "binary",
        "example_file": "example.mmi",
    },
    {
        "id": "CSI",
        "input": True,
        "output": True,
        "kind": "binary",
        "example_file": "example.bcf.csi",
    },
    {
        "id": "FAI",
        "input": True,
        "output": True,
        "example": "seq\t98\t5\t98\t99",
    },
    {
        "id": "BAM",
        "input": True,
        "output": True,
        "kind": "binary",
        "example_file": "example.bam",
    },
    {
        "id": "CRAM",
        "input": True,
        "output": True,
        "kind": "binary",
        "example_file": "example.cram",
    },
    {
        "id": "BIGBED",
        "input": True,
        "output": True,
        "kind": "binary",
        "example_file": "example.bb",
    },
    {
        "id": "BIGWIG",
        "input": True,
        "output": True,
        "kind": "binary",
        "example_file": "example.bw",
    },
    {
        "id": "FMD",
        "input": True,
        "output": True,
        "kind": "binary",
        "example_file": "example.fmd",
    },
    {
        "id": "PSMC_CPG",
        "input": True,
        "output": True,
        "kind": "binary",
        "example_file": "example.cpg",
    },
    {
        "id": "BED",
        "input": True,
        "output": True,
        "example": "seq\t0\t10\nseq1\t0\t20",
    },
    {
        "id": "CLUSTAL",
        "input": True,
        "output": True,
        "example": "CLUSTAL format alignment by MAFFT (v7.526)\n\n\nseq1            ACGTACGTAC-GTACGT\nseq2            ACGTACGAACTGTACGT\nseq3            ACGTACGTAC-GTACGA\n                ******* ** ***** \n",
    },
    {
        "id": "SND",
        "input": True,
        "output": True,
        "example": "#sample\tgene1\tgene2\tgene3\ncellA\t0.95\t0\t1.2\ncellB\t0\t0.31\t0.8\ncellC\t1.1\t0.07\t0",
    },
    {
        "id": "GFF",
        "input": True,
        "output": True,
        "example": "seq\t.\tgene\t1\t30\t.\t+\t.\tID=gene1\nseq\t.\tmRNA\t1\t30\t.\t+\t.\tID=tx1;Parent=gene1\nseq\t.\texon\t1\t30\t.\t+\t.\tParent=tx1\nseq\t.\tCDS\t1\t30\t.\t+\t0\tParent=tx1",
    },
    {
        "id": "GTF",
        "input": True,
        "output": True,
        "example": 'seq\tStringTie\ttranscript\t1\t30\t1000\t+\t.\tgene_id "gene1"; transcript_id "tx1"; FPKM "12.34";\nseq\tStringTie\texon\t1\t30\t1000\t+\t.\tgene_id "gene1"; transcript_id "tx1"; exon_number "1";',
    },
    {
        "id": "PAF",
        "input": True,
        "output": True,
        "example": "queryA\t500\t10\t490\t+\ttargetA\t1000\t100\t580\t470\t480\t60\nqueryB\t300\t0\t300\t-\ttargetA\t1000\t650\t950\t290\t300\t60",
    },
    {
        "id": "GFA",
        "input": True,
        "output": True,
        "example": "H\tVN:Z:1.0\nS\ts1\tACGTACGTACGT\nS\ts2\tTTACGTACGTAC\nL\ts1\t+\ts2\t+\t4M\nP\tp1\ts1+,s2+\t*",
    },
    {
        "id": "LIST",
        "input": True,
        "output": True,
        "example": "seq\nseq1\nseq2",
    },
    {
        "id": "NEWICK",
        "input": True,
        "output": True,
        "example": (
            "((A:0.1,B:0.1)100:0.2,(C:0.1,D:0.1)100:0.2);\n"
            "((A:0.2,B:0.2)95:0.3,(C:0.2,D:0.2)95:0.3);\n"
            "((A:0.3,C:0.3)90:0.4,(B:0.3,D:0.3)90:0.4);"
        ),
    },
    {
        "id": "PSMC",
        "input": True,
        "output": True,
        "example": (
            'CC\n'
            'CC\tBrief Description of the file format:\n'
            'CC\t  CC  comments\n'
            'CC\t  MM  useful-messages\n'
            'CC\t  RD  round-of-iterations\n'
            'CC\t  LL  \\log[P(sequence)]\n'
            'CC\t  QD  Q-before-opt Q-after-opt\n'
            'CC\t  TR  \\theta_0 \\rho_0\n'
            'CC\t  RS  k t_k \\lambda_k \\pi_k \\sum_{l\\not=k}A_{kl} A_{kk}\n'
            'CC\t  DC  begin end best-k t_k+\\Delta_k max-prob\n'
            'CC\n'
            'MM\tVersion: 0.6.5-r74-dirty\n'
            'MM\tpattern:1+1+1+1, n:3, n_free_lambdas:4\n'
            'MM\tn_iterations:1, skip:1, max_t:15, theta/rho:4\n'
            'MM\tis_decoding:0\n'
            'MM\tn_seqs:1, sum_L:65, sum_n:3\n'
            'RD\t0\n'
            'LK\t0.000000\n'
            'QD\t0.000000 -> 0.000000\n'
            'RI\tinf\n'
            'TR\t0.047253\t0.011813\n'
            'MT\t15.000000\n'
            'MM\tC_pi: 1.000000, n_recomb: 0.763351\n'
            'RS\t0\t0.000000\t1.000000\t0.266770\t0.349472\t0.000000\n'
            'RS\t1\t0.432507\t1.000000\t0.446281\t0.584634\t0.000000\n'
            'RS\t2\t2.735641\t1.000000\t0.050300\t0.065894\t0.000000\n'
            'RS\t3\t15.000000\t1.000000\t0.000000\t0.000000\t0.000000\n'
            'PA\t1+1+1+1 0.047252885 0.011813221 15.000000000 1.000000000 1.000000000 1.000000000 1.000000000\n'
            '//\n'
            'IT\t50008\n'
            'RD\t1\n'
            'LK\t-12.748373\n'
            'QD\t-0.304372 -> -0.072240\n'
            'RI\t0.0011439472\n'
            'TR\t1.418623\t0.775098\n'
            'MT\t0.104141\n'
            'MM\tC_pi: 0.034110, n_recomb: 1.696070\n'
            'RS\t0\t0.000000\t0.440567\t0.099501\t0.058666\t0.068804\n'
            'RS\t1\t0.026856\t0.008736\t1.563930\t0.922091\t0.915310\n'
            'RS\t2\t0.060924\t0.000175\t0.032638\t0.019244\t0.015886\n'
            'RS\t3\t0.104141\t0.033600\t0.000000\t0.000000\t0.000000\n'
            'PA\t1+1+1+1 1.418623375 0.775097807 0.104141235 0.440567017 0.008735657 0.000175476 0.033599854\n'
            '//'
        ),
    },
    {
        "id": "PSMC_PARAMS",
        "input": True,
        "output": True,
        "example": "4+5*3+4 0.274400271 0.007093592 2.260467171 1685167.091007381 0.098469252 76061.673035966 3.400960683 0.033475147 0.001966313 0.001944450",
    },
]


def get_type_definitions():
    return TYPE_DEFINITIONS


def get_allowed_input_types():
    return [type_def["id"] for type_def in TYPE_DEFINITIONS if type_def["input"]]


def get_allowed_output_types():
    return [type_def["id"] for type_def in TYPE_DEFINITIONS if type_def["output"]]


def get_example_inputs():
    """Maps each type with an example to it: str for text types, bytes for binary ones."""
    examples = {}
    for type_def in TYPE_DEFINITIONS:
        if "example" in type_def:
            examples[type_def["id"]] = type_def["example"]
        elif "example_file" in type_def:
            examples[type_def["id"]] = (EXAMPLES_DIR / type_def["example_file"]).read_bytes()
    return examples


def is_binary_type(type_id):
    type_def = next(
        (t for t in TYPE_DEFINITIONS if t["id"] == type_id),
        None
    )

    if type_def is None:
        return False

    return type_def.get("kind", "") == "binary"


def validate_type_examples():
    from utils.data_types import ALL_TYPES

    validators = {type_info["type"]: type_info["validator"]
                  for type_info in ALL_TYPES}
    failures = []

    for type_def in TYPE_DEFINITIONS:
        type_id = type_def["id"]

        if "example_file" in type_def:
            path = EXAMPLES_DIR / type_def["example_file"]
            if not path.is_file() or path.stat().st_size == 0:
                failures.append(f"{type_id}: example file {path.name} is missing or empty")
            continue

        if "example" not in type_def:
            continue

        validator = validators.get(type_id)
        if not validator:
            continue

        if not validator(type_def["example"]):
            failures.append(
                f"{type_id}: example does not validate as {type_id}")

    return failures
