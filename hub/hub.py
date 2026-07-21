import argparse
import json
import os
import sys
from sbom.check import check_registry
from sbom.generate import generate_sboms
from catalog.generate import generate_sign_and_publish_catalog
from signing.provenance import generate_provenance_predicates
from signing.sign import sign_and_attest_published_artifacts
from signing.verify import verify_published_artifacts, write_verification_report

BUILD_FILE = ".build" # file containing the validation results
BUILD_DIR = "build" # directory where the builders should output the results
REGISTRY_DIR = "registry"

def get_valid_recipes():
    if os.path.exists(BUILD_FILE):
        with open(BUILD_FILE) as f:
            build_data = json.load(f)
            return build_data["paths"]
    else:
        print("No validated paths found. Run validation first.")
        return None

def validate_cmd(args):
    paths = args.paths
    if not paths:
        raise argparse.ArgumentError(None, "Path provided does not exist")
        
    from validate.validate import validate_recipe
    from utils.type_definitions import validate_type_examples
    import yaml

    type_example_failures = validate_type_examples()
    if type_example_failures:
        print("Type definition example validation failed:")
        for failure in type_example_failures:
            print(f"  - {failure}")
        raise ValueError("Type definition example validation failed")
    else:
        print("Type definition example validation successful")

    print(f"Validating files: {paths}")
    valid_paths = []
    for path in paths:
        with open(path) as f:
            if validate_recipe(yaml.safe_load(f)):
                print(f"Recipe validation successful: {path}")
                valid_paths.append(path)
            else:
                raise ValueError(f"Recipe validation failed: {path}")
            

    build_data = {
        "paths": valid_paths
    }
    with open(BUILD_FILE, "w") as f:
        json.dump(build_data, f)

def build_cmd(args):
    from builders.builder import build_plugins

    recipes = get_valid_recipes()
    if not recipes: return
    build_plugins(recipes, BUILD_DIR, REGISTRY_DIR)

def test_cmd(args):
    from tests.test import test_tools
    from utils.type_definitions import validate_type_examples

    type_example_failures = validate_type_examples()
    if type_example_failures:
        print("Type definition example validation failed:")
        for failure in type_example_failures:
            print(f"  - {failure}")
        raise RuntimeError(f"The following tests failed: {type_example_failures}")
    else:
        print("Type definition example validation successful")

    failed_tests = test_tools(REGISTRY_DIR)
    
    if len(failed_tests) > 0:
        print(f"The following tools failed the tests: {failed_tests}")
    else:
        print("All tests passed")
        
    pass

def sbom_cmd(args):

    summary = generate_sboms(
        registry_dir=args.registry_dir,
        recipes_dir=args.recipes_dir,
    )
    print(f"SBOM generation complete: scanned={summary.scanned}, written={summary.written}")
    for output in summary.outputs:
        print(f"  - {output}")

def sbom_check_cmd(args):
    summary = check_registry(
        registry_dir=args.registry_dir,
    )
    print(
        f"SBOM check complete: scanned={summary.scanned}, "
        f"failures={len(summary.failures)}, warnings={len(summary.warnings)}"
    )
    for issue in summary.failures[:args.max_issues]:
        print(f"  [FAIL] {issue.bundle} {issue.code}: {issue.message}")
    remaining = len(summary.failures) - args.max_issues
    if remaining > 0:
        print(f"  ... {remaining} additional failures not shown")
    for issue in summary.warnings[:args.max_issues]:
        print(f"  [WARN] {issue.bundle} {issue.code}: {issue.message}")
    remaining = len(summary.warnings) - args.max_issues
    if remaining > 0:
        print(f"  ... {remaining} additional warnings not shown")
    if summary.failed:
        sys.exit(1)

def publish_cmd(args):
    from publish.publish import publish_plugins

    registry_url = args.registry
    results = publish_plugins(registry_url, args.registry_dir, package_prefix=args.package_prefix)
    print(f"Published {len(results['artifacts'])} bundle(s)")
    for artifact in results["artifacts"]:
        print(f"  - {artifact['digest_reference']}")

def provenance_cmd(args):
    summary = generate_provenance_predicates(
        registry_dir=args.registry_dir,
        publish_results_path=args.publish_results,
        hub_repository=args.hub_repository,
        hub_ref=args.hub_ref,
        workflow_path=args.workflow_path,
    )
    print(f"SLSA provenance generation complete: scanned={summary.scanned}, written={summary.written}")
    for output in summary.outputs:
        print(f"  - {output}")

def verify_attestations_cmd(args):
    summary = verify_published_artifacts(
        registry_dir=args.registry_dir,
        publish_results_path=args.publish_results,
        policy_path=args.policy,
        cosign_bin=args.cosign,
        operation_id=args.operation_id,
        version=args.version,
    )
    print(f"Signing verification complete: scanned={summary.scanned}, failures={len(summary.failures)}")
    for issue in summary.failures[:args.max_issues]:
        print(f"  [FAIL] {issue.artifact} {issue.code}: {issue.message}")
    remaining = len(summary.failures) - args.max_issues
    if remaining > 0:
        print(f"  ... {remaining} additional issues not shown")
    if summary.failed:
        sys.exit(1)
    if args.report:
        write_verification_report(args.report, args.registry_dir, args.publish_results, args.policy, summary)
        print(f"Signing verification report written: {args.report}")

def sign_attest_cmd(args):
    summary = sign_and_attest_published_artifacts(
        registry_dir=args.registry_dir,
        publish_results_path=args.publish_results,
        policy_path=args.policy,
        cosign_bin=args.cosign,
        max_attempts=args.max_attempts,
        retry_delay_seconds=args.retry_delay_seconds,
    )
    print(f"Signing and attestation complete: scanned={summary.scanned}, signed={summary.signed}, failures={len(summary.failures)}")
    for issue in summary.failures[:args.max_issues]:
        print(f"  [FAIL] {issue.artifact} {issue.code}: {issue.message}")
    remaining = len(summary.failures) - args.max_issues
    if remaining > 0:
        print(f"  ... {remaining} additional issues not shown")
    if summary.failed:
        sys.exit(1)

def publish_catalog_cmd(args):
    summary = generate_sign_and_publish_catalog(
        registry_dir=args.registry_dir,
        publish_results_path=args.publish_results,
        verification_report_path=args.verification_report,
        signing_policy_path=args.policy,
        registry_url=args.registry,
        package_prefix=args.package_prefix,
        catalog_version=args.catalog_version,
        channel=args.channel,
        sequence=args.sequence,
        expires_days=args.expires_days,
        private_jwk_path=args.private_jwk,
    )
    print(f"Verified catalog published: entries={summary.entries}")
    print(f"  catalog: {summary.catalog_path}")
    print(f"  signature: {summary.signature_path}")
    print(f"  digest: {summary.digest_reference}")
    print(f"  {args.channel}: {summary.latest_digest_reference}")

def index_cmd(args):
    #TODO
    pass

def main():
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate_parser = subparsers.add_parser("validate")
    validate_parser.add_argument("paths", nargs="+", default="biochef.yaml", help="Path to the files to validate")
    validate_parser.set_defaults(func=validate_cmd)

    build_parser = subparsers.add_parser("build")
    build_parser.set_defaults(func=build_cmd)

    test_parser = subparsers.add_parser("test")
    test_parser.set_defaults(func=test_cmd)

    sbom_parser = subparsers.add_parser("sbom")
    sbom_parser.add_argument("--registry-dir", default=REGISTRY_DIR, help="Registry bundle directory to scan")
    sbom_parser.add_argument("--recipes-dir", required=True, help="Directory containing BioCHEF recipe biochef.yaml files")
    sbom_parser.set_defaults(func=sbom_cmd)

    sbom_check_parser = subparsers.add_parser("sbom-check")
    sbom_check_parser.add_argument("--registry-dir", default=REGISTRY_DIR, help="Registry bundle directory to scan")
    sbom_check_parser.add_argument("--max-issues", type=int, default=20, help="Maximum failures and warnings to print")
    sbom_check_parser.set_defaults(func=sbom_check_cmd)

    publish_parser = subparsers.add_parser("publish")
    publish_parser.add_argument('--registry', required=True, help="URL of the registry to publish to")
    publish_parser.add_argument("--registry-dir", default=REGISTRY_DIR, help="Registry bundle directory to publish")
    publish_parser.add_argument("--package-prefix", default="biochef-plugins-", help="OCI package name prefix. Use a test-only prefix for sandbox registries.")
    publish_parser.set_defaults(func=publish_cmd)

    provenance_parser = subparsers.add_parser("provenance")
    provenance_parser.add_argument("--registry-dir", default=REGISTRY_DIR, help="Registry bundle directory to scan")
    provenance_parser.add_argument("--publish-results", help="Path to publish-results.json. Defaults to <registry-dir>/publish-results.json")
    provenance_parser.add_argument("--hub-repository", help="GitHub repository used for the hub checkout")
    provenance_parser.add_argument("--hub-ref", help="Hub branch, tag, or commit requested by the workflow")
    provenance_parser.add_argument("--workflow-path", help="GitHub workflow path used to derive local builder identity")
    provenance_parser.set_defaults(func=provenance_cmd)

    verify_parser = subparsers.add_parser("verify-attestations")
    verify_parser.add_argument("--registry-dir", default=REGISTRY_DIR, help="Registry bundle directory to scan")
    verify_parser.add_argument("--publish-results", help="Path to publish-results.json. Defaults to <registry-dir>/publish-results.json")
    verify_parser.add_argument("--policy", required=True, help="Signing verification policy JSON")
    verify_parser.add_argument("--cosign", default="cosign", help="Cosign executable to use")
    verify_parser.add_argument("--operation-id", help="Verify only one published operation id")
    verify_parser.add_argument("--version", help="Verify only one published operation version")
    verify_parser.add_argument("--report", help="Write a machine-readable verification report JSON")
    verify_parser.add_argument("--max-issues", type=int, default=20, help="Maximum failures to print")
    verify_parser.set_defaults(func=verify_attestations_cmd)

    sign_parser = subparsers.add_parser("sign-attest")
    sign_parser.add_argument("--registry-dir", default=REGISTRY_DIR, help="Registry bundle directory to scan")
    sign_parser.add_argument("--publish-results", help="Path to publish-results.json. Defaults to <registry-dir>/publish-results.json")
    sign_parser.add_argument("--policy", required=True, help="Signing verification policy JSON")
    sign_parser.add_argument("--cosign", default="cosign", help="Cosign executable to use")
    sign_parser.add_argument("--max-attempts", type=int, default=2, help="Maximum attempts for each Cosign write and verification operation")
    sign_parser.add_argument("--retry-delay-seconds", type=int, default=5, help="Base retry delay between attempts")
    sign_parser.add_argument("--max-issues", type=int, default=20, help="Maximum failures to print")
    sign_parser.set_defaults(func=sign_attest_cmd)

    catalog_parser = subparsers.add_parser("publish-catalog")
    catalog_parser.add_argument("--registry", required=True, help="URL of the registry to publish the verified catalog to")
    catalog_parser.add_argument("--registry-dir", default=REGISTRY_DIR, help="Registry bundle directory to scan")
    catalog_parser.add_argument("--publish-results", help="Path to publish-results.json. Defaults to <registry-dir>/publish-results.json")
    catalog_parser.add_argument("--verification-report", help="Path to signing-verification-report.json. Defaults to <registry-dir>/signing-verification-report.json")
    catalog_parser.add_argument("--policy", required=True, help="Signing verification policy JSON")
    catalog_parser.add_argument("--package-prefix", default="biochef-plugins-", help="OCI package name prefix")
    catalog_parser.add_argument("--catalog-version", help="Immutable catalog tag. Defaults to catalog-<run id>-<sha> in GitHub Actions")
    catalog_parser.add_argument("--channel", default="latest", help="Mutable catalog channel tag")
    catalog_parser.add_argument("--sequence", type=int, help="Monotonic catalog sequence. Defaults to current Unix time")
    catalog_parser.add_argument("--expires-days", type=int, default=30, help="Catalog validity period in days")
    catalog_parser.add_argument("--private-jwk", help="Path to EC P-256 private JWK. Defaults to BIOCHEF_CATALOG_SIGNING_PRIVATE_JWK")
    catalog_parser.set_defaults(func=publish_catalog_cmd)

    index_parser = subparsers.add_parser("index")
    index_parser.set_defaults(func=index_cmd)

    args = parser.parse_args()
    args.func(args)

if __name__ == "__main__":
    main()
