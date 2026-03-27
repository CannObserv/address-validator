"""Agent-assisted address labeling for usaddress training data.

Generates draft labels from both usaddress (current model) and Claude,
produces a diff showing disagreements, and outputs labeled XML.

Usage:
    python scripts/model/label.py input.csv output.xml [--test-output test.xml]
    python scripts/model/label.py input.csv output.xml --model-only
    python scripts/model/label.py input.csv output.xml --claude-only
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from xml.dom import minidom

import usaddress

VALID_TAGS = set(usaddress.LABELS)


def _label_with_model(address: str) -> list[tuple[str, str]]:
    """Label using current usaddress model. Returns (token, label) pairs."""
    try:
        return usaddress.parse(address)
    except usaddress.RepeatedLabelError as exc:
        return list(exc.parsed_string)


def _label_with_claude(address: str) -> list[tuple[str, str]]:
    """Label using Claude API. Returns (token, label) pairs."""
    try:
        import anthropic  # noqa: PLC0415
    except ImportError:
        print(
            "Error: anthropic package required. Install with: uv add --dev anthropic",
            file=sys.stderr,
        )
        sys.exit(1)

    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        print("Error: ANTHROPIC_API_KEY not set", file=sys.stderr)
        sys.exit(1)

    client = anthropic.Anthropic(api_key=api_key)
    labels_str = ", ".join(sorted(VALID_TAGS))

    prompt = f"""Label each token in this US address with the correct USPS address component tag.

Available tags: {labels_str}

Address: {address}

Return ONLY a JSON array of [token, label] pairs. Example:
[["123", "AddressNumber"], ["Main", "StreetName"], ["St", "StreetNamePostType"]]

Important:
- Split the address into the same tokens that a simple whitespace tokenizer would produce
- Each token gets exactly one label
- Use the most specific applicable tag
- For secondary unit designators (APT, STE, BLDG, ROOM, etc.), use OccupancyType or SubaddressType
- Keep punctuation attached to tokens as they appear"""

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )

    text = response.content[0].text.strip()
    if "```" in text:
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()

    pairs = json.loads(text)
    return [(token, label) for token, label in pairs]


def _format_diff(
    address: str,
    model_labels: list[tuple[str, str]],
    claude_labels: list[tuple[str, str]],
) -> str:
    """Format a side-by-side diff of model vs Claude labels."""
    lines = [f"\n{'=' * 60}", f"Address: {address}", f"{'=' * 60}"]
    lines.append(f"{'Token':<20} {'Model':<30} {'Claude':<30} {'Match'}")
    lines.append("-" * 85)

    max_len = max(len(model_labels), len(claude_labels))
    for i in range(max_len):
        m_tok, m_lab = model_labels[i] if i < len(model_labels) else ("--", "--")
        c_tok, c_lab = claude_labels[i] if i < len(claude_labels) else ("--", "--")
        token = m_tok if m_tok != "--" else c_tok
        match = "OK" if m_lab == c_lab else "DIFF"
        lines.append(f"{token:<20} {m_lab:<30} {c_lab:<30} {match}")

    return "\n".join(lines)


def _write_xml(addresses: list[list[tuple[str, str]]], outfile: str) -> None:
    """Write labeled addresses to XML file in usaddress training format."""
    root = ET.Element("AddressCollection")
    for labels in addresses:
        addr_elem = ET.SubElement(root, "AddressString")
        for token, label in labels:
            child = ET.SubElement(addr_elem, label)
            child.text = token

    xml_str = minidom.parseString(  # noqa: S318
        ET.tostring(root, encoding="unicode")
    ).toprettyxml(indent="  ")
    lines = xml_str.split("\n")
    if lines[0].startswith("<?xml"):
        lines = lines[1:]
    with Path(outfile).open("w") as f:
        f.write("\n".join(lines))
    print(f"Wrote {len(addresses)} labeled addresses to {outfile}")


def _read_addresses(input_csv: str) -> list[str]:
    """Read addresses from CSV. Supports 'raw_address' column or single-column."""
    addresses: list[str] = []
    with Path(input_csv).open() as f:
        reader = csv.DictReader(f)
        if reader.fieldnames and "raw_address" in reader.fieldnames:
            for row in reader:
                addr = row["raw_address"].strip()
                if addr:
                    addresses.append(addr)
        else:
            f.seek(0)
            next(f, None)  # skip header
            for line in f:
                addr = line.strip().strip('"')
                if addr:
                    addresses.append(addr)
    return addresses


def main() -> None:
    parser = argparse.ArgumentParser(description="Agent-assisted address labeling")
    parser.add_argument(
        "input_csv",
        help="CSV with addresses (column 'raw_address' or single-column)",
    )
    parser.add_argument("output_xml", help="Output XML for training data")
    parser.add_argument("--test-output", help="Output XML for test data (20%% split)")
    parser.add_argument(
        "--model-only",
        action="store_true",
        help="Use model labels only (non-interactive)",
    )
    parser.add_argument(
        "--claude-only",
        action="store_true",
        help="Use Claude labels only (non-interactive)",
    )
    args = parser.parse_args()

    addresses = _read_addresses(args.input_csv)
    if not addresses:
        print("No addresses found in input file.", file=sys.stderr)
        sys.exit(1)

    print(f"Loaded {len(addresses)} addresses from {args.input_csv}\n")

    final_labels: list[list[tuple[str, str]]] = []
    for addr in addresses:
        model_labels = _label_with_model(addr)

        if args.model_only:
            final_labels.append(model_labels)
            continue

        if args.claude_only:
            claude_labels = _label_with_claude(addr)
            final_labels.append(claude_labels)
            continue

        claude_labels = _label_with_claude(addr)
        diff = _format_diff(addr, model_labels, claude_labels)
        print(diff)

        print("\nUse [m]odel labels, [c]laude labels, or [s]kip? ", end="")
        choice = input().strip().lower()
        if choice == "m":
            final_labels.append(model_labels)
        elif choice == "c":
            final_labels.append(claude_labels)
        elif choice == "s":
            print("Skipped.")
        else:
            print(f"Unknown choice '{choice}', using Claude labels.")
            final_labels.append(claude_labels)

    if not final_labels:
        print("No addresses labeled.", file=sys.stderr)
        sys.exit(1)

    # Handle test split
    if args.test_output and len(final_labels) > 1:
        split = max(1, len(final_labels) // 5)
        test_labels = final_labels[-split:]
        train_labels = final_labels[:-split]
        _write_xml(train_labels, args.output_xml)
        _write_xml(test_labels, args.test_output)
    else:
        _write_xml(final_labels, args.output_xml)


if __name__ == "__main__":
    main()
