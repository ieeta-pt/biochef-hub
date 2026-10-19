#!/bin/bash
# Regenerates the binary type examples from the text examples in
# type_definitions.py, so every binary example describes the same data:
# the 98 bp sequence "seq", its two paired reads and its one variant. The
# kallisto index is of the sequence the FASTQ example's reads come from.
#
# Needs samtools, bcftools, minimap2, ropebwt3, kallisto, bedToBigBed and
# bedGraphToBigWig on PATH. Run from anywhere; writes next to this script.
# Stock kallisto seeds its Bloom filter at random, so its index differs from
# run to run; the build of ieeta-pt/biochef-recipes fixes the seed.
set -euo pipefail

out=$(cd "$(dirname "$0")" && pwd)
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

python3 - "$out/../type_definitions.py" "$tmp" <<'PY'
import importlib.util, sys
spec = importlib.util.spec_from_file_location("td", sys.argv[1])
td = importlib.util.module_from_spec(spec)
spec.loader.exec_module(td)
# Only the text examples: the binary ones are what this script makes
examples = {t["id"]: t["example"] for t in td.TYPE_DEFINITIONS if "example" in t}
for type_id, name in [("FASTA", "ref.fa"), ("SAM", "reads.sam"), ("VCF", "calls.vcf")]:
    with open(f"{sys.argv[2]}/{name}", "w") as f:
        f.write(examples[type_id].rstrip("\n") + "\n")
# The 230 bp sequence the FASTQ example's six reads tile, for the kallisto
# index, so quant and bus have reads to pseudoalign
reads = examples["FASTQ"].split("\n")[1::4]
source = reads[0]
for read in reads[1:]:
    overlap = next(n for n in range(len(read), 0, -1) if source.endswith(read[:n]))
    source += read[overlap:]
with open(f"{sys.argv[2]}/fastq_source.fa", "w") as f:
    f.write(">fastq_source\n" + source + "\n")
PY

cd "$tmp"
printf 'seq\t98\n' > sizes
printf 'seq\t0\t10\n' > peaks.bed
printf 'seq\t0\t10\t1.5\nseq\t10\t20\t3\n' > cov.bedgraph

samtools sort --no-PG -o reads.bam reads.sam
samtools index -b reads.bam reads.bam.bai
samtools view --no-PG -C -T ref.fa --output-fmt-option embed_ref=1 --output-fmt-option version=3.0 -o reads.cram reads.bam
bcftools view --no-version -Ob -o calls.bcf calls.vcf
bcftools index -c -o calls.bcf.csi calls.bcf
minimap2 -d ref.mmi ref.fa
ropebwt3 build -d -o ref.fmd ref.fa
kallisto index -i fastq_source.kallisto.idx fastq_source.fa
bedToBigBed peaks.bed sizes peaks.bb
bedGraphToBigWig cov.bedgraph sizes cov.bw

cp reads.bam "$out/example.bam"
cp reads.bam.bai "$out/example.bam.bai"
cp reads.cram "$out/example.cram"
cp calls.bcf "$out/example.bcf"
cp calls.bcf.csi "$out/example.bcf.csi"
cp ref.mmi "$out/example.mmi"
cp ref.fmd "$out/example.fmd"
cp fastq_source.kallisto.idx "$out/example.kallisto.idx"
cp peaks.bb "$out/example.bb"
cp cov.bw "$out/example.bw"
