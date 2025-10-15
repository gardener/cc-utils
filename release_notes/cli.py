#! /usr/bin/env python3
import sys
import argparse

from release_notes.model import validate_release_notes


def parse_args() -> argparse.Namespace:
    """ Parses CLI for the release notes validation """
    parser = argparse.ArgumentParser(
        description='Validate release notes in content'
    )
    parser.add_argument(
        'content',
        nargs='?',
        help='Content to validate (if not provided, reads from stdin)'
    )
    parser.add_argument(
        '--file', '-f',
        help='Read content from file instead of argument or stdin'
    )

    return parser.parse_args()


def validate_release_notes_cli():
    """CLI wrapper for release notes validation"""

    args = parse_args()
    if args.file:
        with open(args.file, 'r') as f:
            content = f.read()
    elif args.content:
        content = args.content
    else:
        content = sys.stdin.read()

    try:
        validate_release_notes(source="cli", content=content)
        print("✓ Release notes validation passed")
        sys.exit(0)
    except ValueError as e:
        print(f"✗ Release notes validation failed: {e}", file=sys.stderr)
        sys.exit(1)
