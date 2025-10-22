# BioChef Hub

Build-and-publish system for BioChef. Validates recipes from `biochef-recipes`, compiles to **WASM** (biowasm/emscripten) and/or **native tarball** or **WES**, runs tiny tests, generates **SBOM** and **SLSA**, signs with **cosign**, and publishes signed bundles to the **BioChef Registry**.


## How it works

1. A PR in `biochef-recipes` adds a `biochef.yaml` and tiny tests.
2. The recipes repo **uses** this repo’s reusable GitHub Actions workflow.
3. Hub validates, builds, tests, creates SBOM/SLSA, signs, and **publishes** to the Registry.
4. Hub updates the signed `index.json`.

> Reusable workflows run in the **caller** workspace. This workflow checks out the Hub source to build the CLI before use.


## Repository layout

```
biochef-hub/
├─ cmd/hub/                      # CLI: build | test | sbom | attest | publish | index
├─ internal/
│  ├─ builders/                  # biowasm, biowasm-fork, emscripten, native
│  ├─ packagers/                 # wasm, oci, native-tar, wes
│  ├─ sign/                      # cosign + SRI
│  ├─ publish/                   # upload + index signer
│  ├─ provenance/                # SLSA attestation
│  └─ spec/                      # biochef.yaml validation
├─ docker/                       # Builder images
│  ├─ builder.Dockerfile
│  └─ emscripten.Dockerfile
├─ .github/workflows/
│  └─ build-recipe.yml           # reusable workflow
├─ templates/
│  ├─ recipe/biochef.yaml        # sample recipe
│  └─ ci/recipes.workflow.yml    # sample caller workflow
└─ README.md
```


## Reusable workflow (called by `biochef-recipes`)

```yaml
# biochef-hub/.github/workflows/build-recipe.yml
name: Build BioChef Recipe
on:
  workflow_call:
    inputs:
      registry_url:
        required: true
        type: string
    secrets:
      COSIGN_KEY: { required: true }                       # if using key-based signing
      REGISTRY_S3_ACCESS_KEY_ID: { required: true }
      REGISTRY_S3_SECRET_ACCESS_KEY: { required: true }

jobs:
  build:
    runs-on: ubuntu-latest
    permissions:
      id-token: write   # needed if you switch to keyless OIDC signing
      contents: read
    steps:
      # 1) Checkout caller (biochef-recipes)
      - uses: actions/checkout@v4

      # 2) Checkout Hub source into .hub/
      - uses: actions/checkout@v4
        with:
          repository: biochef-org/biochef-hub
          path: .hub

      # 3) Build Hub CLI
      - uses: actions/setup-go@v5
        with: { go-version: '1.22.x' }
      - run: go build -o hub ./.hub/cmd/hub

      # (Optional) Node validator if kept separate from CLI
      # - uses: actions/setup-node@v4
      # - run: npx biochef-validate ./**/biochef.yaml

      # 4) Validate, build, test
      - run: ./hub validate ./**/biochef.yaml
      - run: ./hub build
      - run: ./hub test --golden ./tests

      # 5) SBOM + SLSA
      - run: ./hub sbom && ./hub attest

      # 6) Install cosign (required for signing)
      - uses: sigstore/cosign-installer@v3

      # 7) Sign bundle (key-based; for keyless, omit --key and ensure id-token: write)
      - env: { COSIGN_PASSWORD: "" }
        run: ./hub sign --key "${{ secrets.COSIGN_KEY }}"

      # 8) Publish to Registry and update signed index
      - env:
          REGISTRY_URL: ${{ inputs.registry_url }}
          AWS_ACCESS_KEY_ID: ${{ secrets.REGISTRY_S3_ACCESS_KEY_ID }}
          AWS_SECRET_ACCESS_KEY: ${{ secrets.REGISTRY_S3_SECRET_ACCESS_KEY }}
        run: ./hub publish --registry "$REGISTRY_URL"
      - run: ./hub index --sign
```

**Caller example in `biochef-recipes`**

```yaml
# biochef-recipes/.github/workflows/recipes.yml
name: Recipes CI
on: [pull_request, push]
jobs:
  build_recipe:
    uses: biochef-org/biochef-hub/.github/workflows/build-recipe.yml@main
    with:
      registry_url: s3://registry.biochef.org/
    secrets: inherit
```

> Alternative: use a container image `ghcr.io/biochef-org/hub:latest` with the CLI preinstalled to simplify the job.


## Build strategies

* **WASM**: `strategy: auto` tries **biowasm** first; fallback **emscripten** is defined in the recipe.
* **Local**: either `native/<os-arch>.tar.zst` or OCI image by digest.
* **Remote**: WES/TES via `wdl|cwl|nf`.
* **Federated**: `runtime/federated/*` for trainers/adapters.


## Secrets

* `COSIGN_KEY` for signing (omit for keyless OIDC).
* Registry credentials (S3/GCS).
* Optional GHCR creds if pushing images.


## Security

* **Signing**: Sigstore **cosign** for bundles and `index.json`.
* **Provenance**: **SLSA** attestations.
* **Licensing**: **SPDX** checks.
* **Integrity**: **SRI** hashes verified by clients.

**References**

* Cosign docs: [https://docs.sigstore.dev/cosign/](https://docs.sigstore.dev/cosign/) ([Sigstore][1])
* Cosign quickstart (sign/verify): [https://docs.sigstore.dev/quickstart/quickstart-cosign/](https://docs.sigstore.dev/quickstart/quickstart-cosign/) ([Sigstore][2])
* Cosign installer GitHub Action: [https://github.com/sigstore/cosign-installer](https://github.com/sigstore/cosign-installer) ([GitHub][3])
* OIDC in Fulcio (GitHub Actions id-token, issuer values): [https://docs.sigstore.dev/certificate_authority/oidc-in-fulcio/](https://docs.sigstore.dev/certificate_authority/oidc-in-fulcio/) ([Sigstore][4])
* GitHub reusable workflows: [https://docs.github.com/en/actions/how-tos/reuse-automations/reuse-workflows](https://docs.github.com/en/actions/how-tos/reuse-automations/reuse-workflows) ([GitHub Docs][5])
* SLSA spec: [https://slsa.dev/](https://slsa.dev/) and [https://slsa.dev/spec/v1.0/about](https://slsa.dev/spec/v1.0/about) ([SLSA][6])
* SPDX: [https://spdx.dev/](https://spdx.dev/) and License list [https://spdx.org/licenses/](https://spdx.org/licenses/) ([spdx.dev][7])
* MDN SRI: [https://developer.mozilla.org/en-US/docs/Web/Security/Subresource_Integrity](https://developer.mozilla.org/en-US/docs/Web/Security/Subresource_Integrity) ([developer.mozilla.org][8])
* GA4GH WES: [https://github.com/ga4gh/workflow-execution-service-schemas](https://github.com/ga4gh/workflow-execution-service-schemas) and docs [https://ga4gh.github.io/workflow-execution-service-schemas/docs/](https://ga4gh.github.io/workflow-execution-service-schemas/docs/) ([GitHub][9])
* GA4GH TES: [https://github.com/ga4gh/task-execution-schemas](https://github.com/ga4gh/task-execution-schemas) and docs [https://ga4gh.github.io/task-execution-schemas/docs/](https://ga4gh.github.io/task-execution-schemas/docs/) ([GitHub][10])


## Local development

```bash
# build CLI
go build -o bin/hub ./cmd/hub
# run without installing
go run ./cmd/hub --help

# typical tasks
bin/hub validate ./**/biochef.yaml
bin/hub build
bin/hub test --golden ./tests
bin/hub sbom && bin/hub attest
```


## License

MIT.
