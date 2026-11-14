"""Build extraction cache for demo sources.

Run live once per label set and write results to demo_cache/.
Usage:
    python scripts/build_cache.py
"""
import json
import os
import sys

# Add project root to sys.path
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import parsers, extract


def main():
    data_dir = os.path.join(ROOT, "data")
    cache_dir = os.path.join(ROOT, "demo_cache")
    os.makedirs(cache_dir, exist_ok=True)

    with open(os.path.join(data_dir, "roles.json"), encoding="utf-8") as f:
        roles_data = json.load(f)

    # Load all lines from source files
    all_sources = parsers.load_all(data_dir)
    source_labels = {s["id"]: s["labels"] for s in roles_data["sources"]}

    # Group lines by label set
    groups: dict[str, list[dict]] = {}
    for sid, lines in all_sources.items():
        labels = source_labels[sid]
        label_key = ",".join(sorted(labels))
        groups.setdefault(label_key, []).extend(lines)

    print("=" * 60)
    print("BATON: Building live cache for demo sources")
    print(f"Destination: {cache_dir}")
    print(f"Provider:    {os.environ.get('LLM_PROVIDER', 'gemini')}")
    print(f"Model:       {os.environ.get('LLM_MODEL', '<from env>')}")
    print("=" * 60)

    for label_key, lines in groups.items():
        cache_key = extract.compute_cache_key(label_key, lines)
        cache_file = os.path.join(cache_dir, f"{cache_key}.json")
        print(f"\nProcessing label set: '{label_key}' ({len(lines)} lines)")
        print(f"Cache key: {cache_key}")

        result = extract.extract_for_label_set(
            label_set=label_key,
            lines=lines,
            mode="live_only",
            cache_dir=cache_dir,
        )

        claims_count = len(result.get("claims", []))
        gaps_count = len(result.get("gaps", []))
        print(f"  -> Extracted {claims_count} claims, {gaps_count} gaps")
        print(f"  -> Written to {cache_file}")

    print("\n" + "=" * 60)
    print("Cache generation complete. Commit demo_cache/ to the repository.")
    print("=" * 60)


if __name__ == "__main__":
    main()
