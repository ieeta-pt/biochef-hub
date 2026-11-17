# BioChef Hub

The build and publish system for BioChef. A Python command line tool and two reusable GitHub Actions workflows take recipes from [biochef-recipes](https://github.com/ieeta-pt/biochef-recipes), build and test them, and publish the resulting tool bundles to an OCI registry together with a signed catalog.


## Role in BioChef

| Repository | Role |
| --- | --- |
| [biochef-app](https://github.com/ieeta-pt/biochef-app) | Web application. Reads the signed catalog and loads tools from the registry. |
| biochef-hub (this repository) | Validates, builds, tests, signs and publishes recipes. |
| [biochef-recipes](https://github.com/ieeta-pt/biochef-recipes) | The recipes, one `biochef.yaml` per tool. Its workflows call the workflows in this repository. |
| [biochef-agent](https://github.com/ieeta-pt/biochef-agent) | Execution service that runs BioChef workflows outside the browser, using tools from the registry. |


## How it works

The workflows in `.github/workflows` are called by biochef-recipes and run `hub/hub.py`.

`validate-recipes.yml` runs on recipe pull requests:

1. `validate` checks each recipe.
2. `build` builds it.
3. `test` runs the built tools.
4. `sbom` and `sbom-check` generate and check a CycloneDX SBOM for each bundle.

`publish-recipes.yml` runs on recipe changes on master:

1. `validate`, `build`, `sbom` and `sbom-check`, as above, without `test`.
2. `publish` pushes each bundle to the registry and records its digest.
3. `sign-attest` signs each bundle by digest with Cosign and attaches its SBOM as an attestation. `verify-attestations` then checks the signatures and attestations against a signing policy.
4. A SLSA provenance job generates provenance for the published digests, which is verified with `slsa-verifier` and attached to each bundle.
5. When `publish_catalog` is true, `publish-catalog` writes a catalog of the verified bundles, signs it with an ES256 key, and pushes it to the registry. The catalog is then signed with Cosign.


## Repository layout

```
hub/
  hub.py              CLI entry point
  requirements.txt    Python dependencies
  example.env         Template for registry credentials
  builders/           Build recipes: biowasm, emscripten and native
  catalog/            Generate, sign and publish the catalog
  publish/            Push bundles to the registry
  sbom/               Generate and check CycloneDX SBOMs
  signing/            Sign and verify with Cosign
  tests/              Run built tools and check their outputs
  utils/              Data type definitions, validators and example files
  validate/           Recipe schema validation
docs/
  adding-data-types.md
.github/workflows/
  publish-recipes.yml
  validate-recipes.yml
```

`build.wasm.strategy` in a recipe selects the WebAssembly builder. `biowasm` builds inside a Docker image defined by `hub/builders/biowasm.Dockerfile` and `biowasm-builder.lock.json`, `emscripten` runs the recipe's build script with the Emscripten SDK, and `auto` tries biowasm and falls back to emscripten. `build.native` builds a native binary.

To add a data type, see [docs/adding-data-types.md](docs/adding-data-types.md).


## Local development

Requirements:

- Python 3.12. `hub/builders/builder.py` does not parse on 3.11.
- Docker, for the biowasm strategy.
- The Emscripten SDK, for the emscripten strategy. The `EMSDK` environment variable points to it and defaults to `/opt/emsdk`.
- Node.js, for `test` on WebAssembly tools.
- Cosign, for `sign-attest` and `verify-attestations`.

Set up a virtual environment:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r hub/requirements.txt
```

Run the CLI:

```bash
python hub/hub.py --help
python hub/hub.py validate ../biochef-recipes/recipes/edlib/biochef.yaml
python hub/hub.py build
python hub/hub.py test
python hub/hub.py sbom
python hub/hub.py sbom-check
```

Commands work in the current directory. `validate` writes the list of valid recipes to `.build`, `build` reads it, and bundles go to `registry/`. `build` deletes any existing `registry/` first.

To build and publish a recipe to a local registry with only Docker installed, use `scripts/publish-local-recipe.sh` in biochef-app. It runs the hub in the Docker image defined by `Dockerfile.hub.dev`. See the [biochef-app README](https://github.com/ieeta-pt/biochef-app#run-with-a-local-registry).


## CLI

Run `python hub/hub.py <command> --help` for all options.

| Command | Purpose |
| --- | --- |
| `validate PATH [PATH ...]` | Validate recipes and the data type examples. |
| `build` | Build the recipes recorded by `validate`. |
| `test` | Run the tools in `registry/` and check their outputs. |
| `sbom` | Generate an SBOM for each bundle. |
| `sbom-check` | Check the SBOM evidence of each bundle. |
| `publish --registry REGISTRY` | Push bundles and write `publish-results.json`. |
| `sign-attest --policy FILE --expected-hub-commit SHA` | Sign and attest published bundles with Cosign. |
| `verify-attestations --policy FILE --expected-hub-commit SHA` | Verify published signatures and attestations. |
| `publish-catalog --registry REGISTRY --policy FILE --slsa-verification-report FILE` | Generate, sign and publish the catalog. |
| `index` | Does nothing. |

`publish` needs no credentials for `localhost` or `127.0.0.1`. For any other registry it reads these from the environment, or from a `.env` file such as a copy of `hub/example.env` saved as `hub/.env`:

| Variable | Purpose |
| --- | --- |
| `REGISTRY_USERNAME` | Registry user. Required. |
| `REGISTRY_PASSWORD` | Registry password or token. Required. |
| `ORAS_AUTH_BACKEND` | ORAS auth backend, for example `token`. Required. |
| `ORAS_INSECURE` | Set to `true` for an insecure connection. Optional. |

`BIOCHEF_BIOWASM_BUILDER_IMAGE` selects a prebuilt biowasm image by digest. Without it the image is built locally if it is not already present.


## Reusable workflows

`validate-recipes.yml` inputs:

| Input | Required | Default | Purpose |
| --- | --- | --- | --- |
| `recipes` | yes | | Space-separated recipe paths. |
| `hub_ref` | no | `master` | Hub ref to check out. |

`publish-recipes.yml` inputs:

| Input | Required | Default | Purpose |
| --- | --- | --- | --- |
| `registry_url` | yes | | Registry hostname, without a scheme or path. |
| `recipes` | yes | | Space-separated recipe paths. |
| `oras_auth` | yes | | ORAS auth backend. |
| `signing_policy` | yes | | Path to the signing policy JSON in the caller repository. |
| `package_prefix` | no | `biochef-plugins-` | OCI package name prefix. |
| `publish` | no | `true` | Run the publish, signing and provenance jobs. |
| `publish_catalog` | no | `false` | Publish the catalog. |
| `biowasm_builder_image` | no | empty | Prebuilt biowasm image, by digest. |

`publish-recipes.yml` secrets:

| Secret | Required | Purpose |
| --- | --- | --- |
| `REGISTRY_USERNAME` | yes | Registry user. |
| `REGISTRY_PASSWORD` | yes | Registry password or token. |
| `BIOCHEF_CATALOG_SIGNING_PRIVATE_JWK` | no | EC P-256 private JWK that signs the catalog. |

Production publishing, to `registry.biochef.app` with the default prefix, must use `publish-recipes.yml` from master; the workflow fails otherwise.


## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

Apache License 2.0; see [LICENSE](LICENSE) and [NOTICE](NOTICE). Code written before the change was released under the MIT License; its notice is kept in [LICENSE-MIT](LICENSE-MIT).
