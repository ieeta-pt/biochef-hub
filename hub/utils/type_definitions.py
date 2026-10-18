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
        "kind": "binary"
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
        "kind": "binary"
    },
    {
        "id": "MMI",
        "input": True,
        "output": True,
        "kind": "binary"
    },
    {
        "id": "CSI",
        "input": True,
        "output": True,
        "kind": "binary"
    },
    {
        "id": "FAI",
        "input": True,
        "output": True,
    },
    {
        "id": "BAM",
        "input": True,
        "output": True,
        "kind": "binary"
    },
    {
        "id": "CRAM",
        "input": True,
        "output": True,
        "kind": "binary"
    },
    {
        "id": "BIGBED",
        "input": True,
        "output": True,
        "kind": "binary"
    },
    {
        "id": "BIGWIG",
        "input": True,
        "output": True,
        "kind": "binary"
    },
    {
        "id": "FMD",
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
        "id": "CLUSTAL",
        "input": True,
        "output": True,
        "example": "CLUSTAL format alignment by MAFFT (v7.526)\n\n\nseq1            ACGTACGTAC-GTACGT\nseq2            ACGTACGAACTGTACGT\nseq3            ACGTACGTAC-GTACGA\n                ******* ** ***** \n",
    },
    {
        "id": "SND",
        "input": True,
        "output": True,
        # t-SNE needs more than three times the perplexity samples: 151 for
        # bhtsne's default of 50
        "example": (
            "#cell\tSNAP25\tRBFOX3\tGFAP\tAQP4\tCX3CR1\tP2RY12\n"
            "neuron_01\t7.9\t7.0\t0.2\t0.8\t0.5\t0.0\n"
            "neuron_02\t8.0\t8.0\t0.3\t0.1\t0.7\t0.4\n"
            "neuron_03\t8.0\t8.6\t0.3\t0.1\t0.3\t0.8\n"
            "neuron_04\t8.5\t8.4\t0.5\t0.2\t0.3\t0.7\n"
            "neuron_05\t5.4\t7.5\t0.7\t0.5\t0.7\t0.1\n"
            "neuron_06\t6.1\t7.0\t0.1\t0.5\t0.5\t0.2\n"
            "neuron_07\t8.7\t7.8\t0.2\t0.8\t0.1\t0.1\n"
            "neuron_08\t7.2\t6.4\t0.2\t1.2\t0.5\t0.1\n"
            "neuron_09\t8.2\t6.7\t0.2\t0.6\t0.5\t0.2\n"
            "neuron_10\t7.8\t6.9\t0.2\t0.0\t0.3\t0.3\n"
            "neuron_11\t8.1\t9.5\t0.4\t0.4\t0.1\t0.2\n"
            "neuron_12\t6.3\t6.8\t0.0\t0.2\t0.2\t0.0\n"
            "neuron_13\t8.4\t7.3\t0.2\t0.4\t0.7\t0.5\n"
            "neuron_14\t6.6\t6.9\t0.1\t0.3\t0.6\t0.5\n"
            "neuron_15\t7.4\t7.7\t0.1\t0.1\t0.4\t0.3\n"
            "neuron_16\t7.1\t5.1\t0.1\t0.0\t0.1\t0.4\n"
            "neuron_17\t7.5\t6.5\t0.3\t0.6\t0.3\t0.3\n"
            "neuron_18\t7.8\t7.9\t0.1\t0.2\t0.8\t0.1\n"
            "neuron_19\t8.4\t5.6\t0.6\t0.2\t0.3\t0.5\n"
            "neuron_20\t6.4\t6.6\t0.1\t0.4\t0.5\t0.2\n"
            "neuron_21\t7.5\t7.2\t0.3\t0.5\t0.3\t0.6\n"
            "neuron_22\t10.8\t8.3\t0.3\t0.6\t0.6\t0.0\n"
            "neuron_23\t7.8\t6.9\t0.4\t0.5\t0.3\t0.4\n"
            "neuron_24\t6.9\t8.3\t0.2\t0.2\t0.2\t0.4\n"
            "neuron_25\t7.4\t8.7\t0.1\t0.3\t0.1\t0.5\n"
            "neuron_26\t7.3\t7.3\t0.5\t0.1\t0.5\t0.3\n"
            "neuron_27\t6.2\t6.5\t0.2\t1.1\t0.3\t0.3\n"
            "neuron_28\t6.7\t6.9\t0.3\t0.6\t0.6\t0.8\n"
            "neuron_29\t6.0\t8.7\t0.2\t0.5\t0.6\t0.6\n"
            "neuron_30\t8.0\t7.3\t0.5\t0.3\t0.5\t0.2\n"
            "neuron_31\t8.4\t7.7\t0.8\t0.6\t0.8\t0.7\n"
            "neuron_32\t9.1\t7.0\t0.3\t0.3\t0.6\t0.1\n"
            "neuron_33\t7.3\t7.6\t0.5\t0.4\t0.1\t0.2\n"
            "neuron_34\t7.7\t4.1\t0.2\t0.1\t0.3\t0.7\n"
            "neuron_35\t7.8\t7.0\t0.7\t0.0\t0.3\t0.5\n"
            "neuron_36\t6.8\t5.3\t0.1\t0.2\t0.3\t0.8\n"
            "neuron_37\t9.6\t8.9\t0.0\t0.4\t1.1\t0.1\n"
            "neuron_38\t7.6\t6.8\t0.0\t0.3\t0.5\t0.1\n"
            "neuron_39\t6.2\t8.0\t0.3\t0.4\t0.1\t0.2\n"
            "neuron_40\t7.6\t7.4\t0.0\t0.1\t0.3\t0.2\n"
            "neuron_41\t9.1\t7.2\t0.1\t0.2\t0.3\t0.1\n"
            "neuron_42\t7.4\t7.5\t0.4\t0.8\t0.7\t0.2\n"
            "neuron_43\t8.5\t5.1\t0.3\t0.3\t0.1\t0.5\n"
            "neuron_44\t7.9\t9.9\t0.2\t0.1\t0.1\t0.1\n"
            "neuron_45\t9.1\t7.1\t0.5\t0.2\t0.3\t0.1\n"
            "neuron_46\t6.9\t7.7\t0.0\t0.4\t0.1\t0.3\n"
            "neuron_47\t8.5\t7.8\t0.5\t0.5\t0.9\t0.7\n"
            "neuron_48\t7.4\t8.7\t0.5\t0.1\t0.1\t0.2\n"
            "neuron_49\t7.5\t9.4\t0.4\t0.5\t0.2\t0.1\n"
            "neuron_50\t7.3\t7.5\t0.1\t0.3\t0.2\t0.4\n"
            "neuron_51\t9.5\t7.6\t0.1\t0.1\t0.6\t0.6\n"
            "astrocyte_01\t0.6\t0.4\t6.8\t8.8\t0.7\t1.0\n"
            "astrocyte_02\t0.4\t0.2\t6.8\t9.5\t0.3\t0.4\n"
            "astrocyte_03\t0.3\t0.0\t6.5\t7.1\t0.3\t0.0\n"
            "astrocyte_04\t0.2\t0.4\t7.3\t6.0\t0.4\t0.4\n"
            "astrocyte_05\t0.5\t0.0\t7.9\t8.0\t0.3\t0.0\n"
            "astrocyte_06\t0.3\t0.2\t7.3\t7.7\t0.6\t0.1\n"
            "astrocyte_07\t0.5\t0.2\t8.1\t8.4\t0.5\t0.2\n"
            "astrocyte_08\t0.1\t0.1\t7.6\t6.8\t0.9\t0.1\n"
            "astrocyte_09\t0.3\t0.3\t6.6\t7.7\t0.1\t0.2\n"
            "astrocyte_10\t0.6\t0.3\t6.4\t7.5\t0.0\t0.7\n"
            "astrocyte_11\t0.6\t0.1\t5.7\t8.0\t0.6\t0.1\n"
            "astrocyte_12\t0.5\t0.5\t5.7\t6.2\t0.5\t0.4\n"
            "astrocyte_13\t0.0\t0.1\t7.6\t8.0\t0.1\t0.4\n"
            "astrocyte_14\t0.2\t0.8\t7.3\t8.1\t0.6\t0.1\n"
            "astrocyte_15\t0.8\t0.4\t7.6\t5.7\t0.2\t0.6\n"
            "astrocyte_16\t0.3\t0.2\t6.4\t6.4\t0.6\t0.1\n"
            "astrocyte_17\t0.1\t0.0\t10.0\t6.8\t0.7\t0.2\n"
            "astrocyte_18\t0.3\t0.0\t8.4\t7.8\t0.5\t0.1\n"
            "astrocyte_19\t0.5\t0.1\t8.9\t6.9\t0.4\t0.2\n"
            "astrocyte_20\t0.5\t0.4\t8.4\t9.0\t0.1\t0.2\n"
            "astrocyte_21\t0.2\t0.6\t8.0\t7.0\t0.3\t0.1\n"
            "astrocyte_22\t1.1\t0.5\t7.6\t7.4\t0.7\t0.4\n"
            "astrocyte_23\t0.0\t0.0\t8.8\t8.3\t0.2\t0.0\n"
            "astrocyte_24\t0.7\t0.3\t7.1\t8.6\t0.5\t0.1\n"
            "astrocyte_25\t0.4\t0.2\t6.7\t9.0\t0.2\t0.5\n"
            "astrocyte_26\t0.7\t0.3\t7.4\t7.0\t0.1\t0.3\n"
            "astrocyte_27\t0.9\t0.3\t6.2\t6.5\t0.3\t0.2\n"
            "astrocyte_28\t0.5\t0.5\t7.3\t6.9\t0.6\t0.9\n"
            "astrocyte_29\t0.2\t0.2\t7.1\t8.2\t0.1\t0.9\n"
            "astrocyte_30\t0.3\t0.1\t7.8\t6.7\t0.2\t0.5\n"
            "astrocyte_31\t0.5\t0.0\t8.8\t7.5\t0.3\t0.7\n"
            "astrocyte_32\t0.1\t0.5\t6.2\t7.8\t0.8\t0.3\n"
            "astrocyte_33\t0.2\t0.1\t5.8\t6.7\t0.5\t0.6\n"
            "astrocyte_34\t0.1\t0.5\t9.0\t6.9\t0.5\t0.0\n"
            "astrocyte_35\t0.7\t0.0\t7.7\t6.4\t0.5\t0.5\n"
            "astrocyte_36\t0.2\t0.2\t7.2\t8.8\t0.2\t0.1\n"
            "astrocyte_37\t0.6\t0.1\t8.4\t8.1\t0.2\t0.0\n"
            "astrocyte_38\t0.6\t0.1\t7.5\t9.2\t0.1\t0.2\n"
            "astrocyte_39\t0.1\t0.1\t7.1\t8.3\t0.3\t0.6\n"
            "astrocyte_40\t0.2\t0.1\t6.8\t8.3\t0.2\t0.1\n"
            "astrocyte_41\t0.2\t0.4\t9.0\t8.3\t0.0\t0.8\n"
            "astrocyte_42\t0.4\t0.7\t9.5\t10.5\t0.2\t0.2\n"
            "astrocyte_43\t0.3\t0.5\t8.2\t7.7\t0.2\t0.5\n"
            "astrocyte_44\t0.0\t1.2\t8.9\t5.8\t0.0\t0.2\n"
            "astrocyte_45\t0.2\t0.7\t7.6\t7.2\t0.4\t0.0\n"
            "astrocyte_46\t0.8\t0.6\t6.6\t7.6\t0.1\t1.4\n"
            "astrocyte_47\t0.1\t0.5\t7.3\t7.8\t0.4\t0.2\n"
            "astrocyte_48\t0.1\t0.9\t8.5\t7.3\t0.1\t0.5\n"
            "astrocyte_49\t0.3\t0.3\t7.9\t6.9\t0.2\t0.2\n"
            "astrocyte_50\t0.2\t0.1\t6.1\t8.3\t0.7\t0.3\n"
            "astrocyte_51\t0.3\t0.6\t8.2\t8.4\t0.2\t0.2\n"
            "microglia_01\t0.5\t0.1\t0.7\t0.2\t6.3\t6.5\n"
            "microglia_02\t0.4\t0.3\t0.1\t0.1\t6.6\t8.4\n"
            "microglia_03\t0.6\t0.3\t0.1\t0.1\t5.0\t6.9\n"
            "microglia_04\t0.3\t0.6\t0.5\t0.1\t6.0\t6.7\n"
            "microglia_05\t0.2\t0.3\t0.4\t0.1\t8.0\t7.9\n"
            "microglia_06\t0.0\t0.1\t0.2\t0.1\t6.7\t8.8\n"
            "microglia_07\t1.2\t0.3\t0.5\t0.5\t8.0\t7.6\n"
            "microglia_08\t0.3\t0.4\t0.1\t0.4\t7.3\t6.5\n"
            "microglia_09\t0.3\t0.0\t0.5\t0.4\t8.6\t6.6\n"
            "microglia_10\t0.2\t0.5\t0.5\t0.2\t7.5\t9.3\n"
            "microglia_11\t0.2\t0.0\t0.4\t0.2\t7.6\t7.2\n"
            "microglia_12\t0.3\t0.1\t0.7\t0.6\t7.2\t7.4\n"
            "microglia_13\t0.1\t0.0\t0.1\t0.3\t9.5\t7.7\n"
            "microglia_14\t0.0\t0.0\t0.0\t0.3\t6.6\t8.1\n"
            "microglia_15\t0.4\t0.8\t0.2\t0.3\t7.8\t6.0\n"
            "microglia_16\t0.5\t0.3\t0.4\t0.1\t7.8\t6.2\n"
            "microglia_17\t0.4\t0.0\t0.1\t0.8\t8.4\t7.0\n"
            "microglia_18\t0.6\t0.3\t0.0\t0.1\t8.1\t9.2\n"
            "microglia_19\t0.2\t0.1\t0.0\t0.1\t7.8\t8.2\n"
            "microglia_20\t0.2\t0.4\t0.7\t0.0\t6.6\t7.9\n"
            "microglia_21\t0.4\t0.0\t0.9\t0.4\t7.8\t8.0\n"
            "microglia_22\t0.3\t0.1\t0.7\t0.2\t7.3\t7.2\n"
            "microglia_23\t0.1\t0.3\t0.2\t1.0\t7.7\t7.1\n"
            "microglia_24\t0.2\t0.7\t0.4\t0.5\t5.3\t7.0\n"
            "microglia_25\t0.3\t0.4\t0.1\t0.1\t7.8\t6.9\n"
            "microglia_26\t0.2\t0.3\t0.1\t0.3\t5.7\t7.3\n"
            "microglia_27\t0.3\t0.2\t0.6\t0.4\t9.0\t7.5\n"
            "microglia_28\t0.7\t0.2\t0.3\t0.1\t9.6\t7.6\n"
            "microglia_29\t0.4\t0.6\t0.1\t0.2\t7.0\t8.6\n"
            "microglia_30\t0.2\t0.9\t0.3\t0.4\t7.9\t7.7\n"
            "microglia_31\t0.5\t0.1\t0.4\t0.2\t5.8\t6.5\n"
            "microglia_32\t0.3\t0.7\t0.1\t0.2\t5.6\t10.3\n"
            "microglia_33\t1.2\t0.8\t0.2\t0.8\t8.6\t6.8\n"
            "microglia_34\t1.1\t0.2\t0.2\t0.6\t6.9\t8.9\n"
            "microglia_35\t0.6\t0.3\t0.2\t0.7\t7.0\t7.1\n"
            "microglia_36\t0.2\t0.2\t0.5\t0.1\t7.3\t7.1\n"
            "microglia_37\t0.0\t0.1\t0.3\t0.1\t7.5\t7.9\n"
            "microglia_38\t0.1\t0.4\t0.2\t0.1\t7.9\t6.6\n"
            "microglia_39\t0.2\t0.4\t0.1\t0.3\t7.0\t7.1\n"
            "microglia_40\t0.0\t1.0\t0.1\t0.2\t6.6\t7.8\n"
            "microglia_41\t0.4\t0.7\t0.5\t0.3\t8.1\t8.9\n"
            "microglia_42\t0.0\t0.1\t0.6\t0.6\t8.0\t7.7\n"
            "microglia_43\t0.6\t0.1\t0.1\t0.4\t6.7\t7.2\n"
            "microglia_44\t0.4\t0.3\t0.3\t0.3\t6.2\t8.1\n"
            "microglia_45\t0.2\t0.1\t0.5\t0.3\t8.0\t7.7\n"
            "microglia_46\t0.1\t0.5\t0.4\t0.1\t8.9\t6.7\n"
            "microglia_47\t0.4\t0.2\t0.1\t0.2\t7.6\t7.3\n"
            "microglia_48\t0.0\t0.3\t0.3\t0.8\t8.5\t8.1\n"
            "microglia_49\t0.2\t0.2\t0.1\t0.2\t8.6\t5.7\n"
            "microglia_50\t0.8\t0.2\t0.3\t0.5\t8.3\t8.9\n"
            "microglia_51\t0.7\t0.3\t0.2\t0.4\t6.7\t7.5"
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

        if "example" not in type_def:
            continue

        validator = validators.get(type_id)
        if not validator:
            continue

        if not validator(type_def["example"]):
            failures.append(
                f"{type_id}: example does not validate as {type_id}")

    return failures
