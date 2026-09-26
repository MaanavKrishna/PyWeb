"""PyWeb CLI (Track C additive flag: --security-scan only)."""
import argparse


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser; Track C adds only ``--security-scan``."""
    parser = argparse.ArgumentParser(prog="pyweb", description="PyWeb CLI")
    parser.add_argument("--security-scan", action="store_true",
                        help="run security.scan over the app and report")
    return parser


def main(argv=None) -> int:
    """CLI entry point."""
    args = build_parser().parse_args(argv)
    if args.security_scan:
        from . import security
        print("security scan: pass an app descriptor to security.scan(app)")
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
