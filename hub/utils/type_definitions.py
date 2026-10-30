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
        "id": "YAK",
        "input": True,
        "output": True,
        "kind": "binary",
        "example_file": "example.yak",
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
        "id": "SCORING_MATRIX",
        "input": True,
        "output": True,
        # NCBI BLOSUM62, as BLAST and SSW read it
        "example": (
            "   A  R  N  D  C  Q  E  G  H  I  L  K  M  F  P  S  T  W  Y  V  B  Z  X  *\n"
            "A  4 -1 -2 -2  0 -1 -1  0 -2 -1 -1 -1 -1 -2 -1  1  0 -3 -2  0 -2 -1  0 -4\n"
            "R -1  5  0 -2 -3  1  0 -2  0 -3 -2  2 -1 -3 -2 -1 -1 -3 -2 -3 -1  0 -1 -4\n"
            "N -2  0  6  1 -3  0  0  0  1 -3 -3  0 -2 -3 -2  1  0 -4 -2 -3  3  0 -1 -4\n"
            "D -2 -2  1  6 -3  0  2 -1 -1 -3 -4 -1 -3 -3 -1  0 -1 -4 -3 -3  4  1 -1 -4\n"
            "C  0 -3 -3 -3  9 -3 -4 -3 -3 -1 -1 -3 -1 -2 -3 -1 -1 -2 -2 -1 -3 -3 -2 -4\n"
            "Q -1  1  0  0 -3  5  2 -2  0 -3 -2  1  0 -3 -1  0 -1 -2 -1 -2  0  3 -1 -4\n"
            "E -1  0  0  2 -4  2  5 -2  0 -3 -3  1 -2 -3 -1  0 -1 -3 -2 -2  1  4 -1 -4\n"
            "G  0 -2  0 -1 -3 -2 -2  6 -2 -4 -4 -2 -3 -3 -2  0 -2 -2 -3 -3 -1 -2 -1 -4\n"
            "H -2  0  1 -1 -3  0  0 -2  8 -3 -3 -1 -2 -1 -2 -1 -2 -2  2 -3  0  0 -1 -4\n"
            "I -1 -3 -3 -3 -1 -3 -3 -4 -3  4  2 -3  1  0 -3 -2 -1 -3 -1  3 -3 -3 -1 -4\n"
            "L -1 -2 -3 -4 -1 -2 -3 -4 -3  2  4 -2  2  0 -3 -2 -1 -2 -1  1 -4 -3 -1 -4\n"
            "K -1  2  0 -1 -3  1  1 -2 -1 -3 -2  5 -1 -3 -1  0 -1 -3 -2 -2  0  1 -1 -4\n"
            "M -1 -1 -2 -3 -1  0 -2 -3 -2  1  2 -1  5  0 -2 -1 -1 -1 -1  1 -3 -1 -1 -4\n"
            "F -2 -3 -3 -3 -2 -3 -3 -3 -1  0  0 -3  0  6 -4 -2 -2  1  3 -1 -3 -3 -1 -4\n"
            "P -1 -2 -2 -1 -3 -1 -1 -2 -2 -3 -3 -1 -2 -4  7 -1 -1 -4 -3 -2 -2 -1 -2 -4\n"
            "S  1 -1  1  0 -1  0  0  0 -1 -2 -2  0 -1 -2 -1  4  1 -3 -2 -2  0  0  0 -4\n"
            "T  0 -1  0 -1 -1 -1 -1 -2 -2 -1 -1 -1 -1 -2 -1  1  5 -2 -2  0 -1 -1  0 -4\n"
            "W -3 -3 -4 -4 -2 -2 -3 -2 -2 -3 -2 -3 -1  1 -4 -3 -2 11  2 -3 -4 -3 -2 -4\n"
            "Y -2 -2 -2 -3 -2 -1 -2 -3  2 -1 -1 -2 -1  3 -3 -2 -2  2  7 -1 -3 -2 -1 -4\n"
            "V  0 -3 -3 -3 -1 -2 -2 -3 -3  3  1 -2  1 -1 -2 -2  0 -3 -1  4 -3 -2 -1 -4\n"
            "B -2 -1  3  4 -3  0  1 -1  0 -3 -4  0 -3 -3 -2  0 -1 -4 -3 -3  4  1 -1 -4\n"
            "Z -1  0  0  1 -3  3  4 -2  0 -3 -3  1 -1 -3 -1  0 -1 -3 -2 -2  1  4 -1 -4\n"
            "X  0 -1 -1 -1 -2 -1 -1 -1 -1 -1 -1 -1 -1 -1 -2  0  0 -2 -1 -1 -1 -1 -1 -4\n"
            "* -4 -4 -4 -4 -4 -4 -4 -4 -4 -4 -4 -4 -4 -4 -4 -4 -4 -4 -4 -4 -4 -4 -4  1"
        ),
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
        "id": "RGFA",
        "input": True,
        "output": True,
        "example": (
            "S\ts1\tACGTACGTAC\tSN:Z:seq\tSO:i:0\tSR:i:0\n"
            "S\ts2\tGGGGGCCCCC\tSN:Z:seq\tSO:i:10\tSR:i:0\n"
            "S\ts3\tTTTTTAAAAATT\tSN:Z:alt\tSO:i:0\tSR:i:1\n"
            "S\ts4\tCATGCATGCA\tSN:Z:seq\tSO:i:20\tSR:i:0\n"
            "L\ts1\t+\ts2\t+\t0M\tSR:i:0\n"
            "L\ts2\t+\ts4\t+\t0M\tSR:i:0\n"
            "L\ts1\t+\ts3\t+\t0M\tSR:i:1\n"
            "L\ts3\t+\ts4\t+\t0M\tSR:i:1"
        ),
    },
    {
        "id": "GAF",
        "input": True,
        "output": True,
        "example": "read1\t600\t153\t446\t+\t>s1>s2\t600\t153\t446\t293\t293\t60\nread1\t600\t10\t148\t+\t>s1\t300\t160\t298\t138\t138\t60",
    },
    {
        "id": "WIG",
        "input": True,
        "output": True,
        "example": "variableStep chrom=seq span=5\n1\t1.5\n11\t3",
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
