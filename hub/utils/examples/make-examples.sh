#!/bin/bash
# Regenerates the binary type examples from the text examples in
# type_definitions.py, so every binary example describes the same data:
# the 98 bp sequence "seq", its two paired reads and its one variant.
#
# Needs samtools, bcftools, minimap2, ropebwt3, yak, velveth,
# bedToBigBed and bedGraphToBigWig on PATH. Run from anywhere; writes
# next to this script.
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
for type_id, name in [("FASTA", "ref.fa"), ("FASTQ", "reads.fq"), ("SAM", "reads.sam"), ("VCF", "calls.vcf")]:
    with open(f"{sys.argv[2]}/{name}", "w") as f:
        f.write(examples[type_id].rstrip("\n") + "\n")
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
yak count -o ref.yak ref.fa
velveth hash 21 -fmtAuto -reference ref.fa -short reads.fq
# The hashed reads in one tar, owned by nobody and dated 0, as BioChef's
# velvet operations write it
python3 - <<'PY'
import io, tarfile
with tarfile.open("ref.velveth", "w", format=tarfile.USTAR_FORMAT) as tar:
    for name in ("Sequences", "Roadmaps"):
        data = open(f"hash/{name}", "rb").read()
        info = tarfile.TarInfo(name)
        info.size, info.mode = len(data), 0o644
        tar.addfile(info, io.BytesIO(data))
PY
bedToBigBed peaks.bed sizes peaks.bb
bedGraphToBigWig cov.bedgraph sizes cov.bw

cp reads.bam "$out/example.bam"
cp reads.bam.bai "$out/example.bam.bai"
cp reads.cram "$out/example.cram"
cp calls.bcf "$out/example.bcf"
cp calls.bcf.csi "$out/example.bcf.csi"
cp ref.mmi "$out/example.mmi"
cp ref.fmd "$out/example.fmd"
cp ref.yak "$out/example.yak"
cp ref.velveth "$out/example.velveth"
cp peaks.bb "$out/example.bb"
cp cov.bw "$out/example.bw"
