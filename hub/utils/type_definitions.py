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
        "example": "@seq\nGCTAGCTGATCGTACGTAGCGTATCGTAGCTGATCGTACGATCGTAGCTAGCTGATCGTAGCTAGCTAGCTGATCGTAGCTAGCTGATCGTACGTAGC\n+\nIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIII",
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
        "example": ">seq1_human\nGTTCCAGTAGCGGCGTATCGTAGGTGACGTAGCAGTCGATCGCTAGCGAAGCGCTGACTAGCTCGATAGCGGCTACTCGTACGTAGTACGTAGCATACG\n>seq2_cat\nAGCTGCTGATCGTGATCGAGCTCGATGCATCGATCGCTAGCGTACGTAGCTGACGTAGCGTGACTGATCGTAGCTGATCGTGACGTAGCTGACGTAGCTG",
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
        #"example": ""
    },
    {
        "id": "VCF",
        "input": True,
        "output": True,
    },
    {
        "id": "BCF",
        "input": True,
        "output": True,
        "king": "binary"
    },
    {
        "id": "SAM",
        "input": True,
        "output": True,
    },
    {
        "id": "BAM",
        "input": True,
        "output": True,
        "king": "binary"
    },
    {
        "id": "CRAM",
        "input": True,
        "output": True,
        "kind": "binary"
    },
    {
        "id": "BED",
        "input": True,
        "output": True,
        "example": "seq\t0\t10\nseq1\t0\t20",
    },
    {
        "id": "GFF",
        "input": True,
        "output": True,
        "example": "seq\t.\tgene\t1\t30\t.\t+\t.\tID=gene1\nseq\t.\tmRNA\t1\t30\t.\t+\t.\tID=tx1;Parent=gene1\nseq\t.\texon\t1\t30\t.\t+\t.\tParent=tx1\nseq\t.\tCDS\t1\t30\t.\t+\t0\tParent=tx1",
    },
    {
        "id": "LIST",
        "input": True,
        "output": True,
        "example": "seq\nseq1\nseq2",
    },
]


def get_type_definitions():
    return TYPE_DEFINITIONS


def get_allowed_input_types():
    return [type_def["id"] for type_def in TYPE_DEFINITIONS if type_def["input"]]


def get_allowed_output_types():
    return [type_def["id"] for type_def in TYPE_DEFINITIONS if type_def["output"]]


def get_example_inputs():
    return {
        type_def["id"]: type_def["example"]
        for type_def in TYPE_DEFINITIONS
        if "example" in type_def
    }

def is_binary_type(type_id):
    type_def = next(
        (t for t in TYPE_DEFINITIONS if t["id"] == type_id),
        None
    )

    if type_def is None: return False

    return type_def.get("kind", "") == "binary"