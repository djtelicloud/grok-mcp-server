import os
import json
import hashlib

def build_manifest():
    print("Building D0002 Manifest...")
    base_dir = os.path.dirname(os.path.dirname(__file__))
    
    # Calculate parent digest
    parent_manifest_path = os.path.join(base_dir, "manifest.json")
    if os.path.exists(parent_manifest_path):
        with open(parent_manifest_path, "r") as f:
            parent_manifest_digest = hashlib.sha256(f.read().encode()).hexdigest()
    else:
        parent_manifest_digest = "unknown"
        
    # Gather stats
    packs_dir = os.path.join(base_dir, "packs")
    total_raw = 0
    total_canonical = 0
    total_disputes = 0
    pack_counts = {}
    provider_counts = {"donors": {}, "critics": {}}
    
    # Read adjudication queue
    queue_file = os.path.join(base_dir, "adjudication_queue.jsonl")
    if os.path.exists(queue_file):
        with open(queue_file, "r") as f:
            total_disputes = sum(1 for line in f)

    for pack in os.listdir(packs_dir):
        pack_dir = os.path.join(packs_dir, pack)
        if not os.path.isdir(pack_dir): continue
        
        pack_canonical = 0
        canonical_file = os.path.join(pack_dir, "canonical_dataset.jsonl")
        if os.path.exists(canonical_file):
            with open(canonical_file, "r") as f:
                for line in f:
                    pack_canonical += 1
                    total_canonical += 1
                    row = json.loads(line)
                    # Aggregate providers
                    for donor in row.get("donor_model", []):
                        provider_counts["donors"][donor] = provider_counts["donors"].get(donor, 0) + 1
                    for critic in row.get("critic_model", []):
                        provider_counts["critics"][critic] = provider_counts["critics"].get(critic, 0) + 1
                        
        pack_counts[pack] = pack_canonical
        
        # Raw count (all .jsonl except canonical and adjudication)
        for file in os.listdir(pack_dir):
            if file.endswith(".jsonl") and file != "canonical_dataset.jsonl":
                with open(os.path.join(pack_dir, file), "r") as f:
                    total_raw += sum(1 for line in f)

    manifest = {
        "version": "D0002",
        "name": "AKE Static Dataset V1 (OpenAI/Anthropic Expansion)",
        "source_bundle": "docs/okf/okf-manifest.json",
        "parent_manifest_digest": parent_manifest_digest,
        "generation_waves": ["D0001_initial", "openai-anthropic-expansion-v1"],
        "statistics": {
            "total_raw_candidate_atoms": total_raw,
            "total_canonical_atoms": total_canonical,
            "target_conflicts_isolated": total_disputes,
            "pack_distribution": pack_counts,
            "provider_counts": provider_counts
        },
        "files": [
            "stats.json",
            "generation_recipe.md",
            "adjudication_queue.jsonl",
            "packs/*/canonical_dataset.jsonl"
        ]
    }
    
    with open(os.path.join(base_dir, "manifest_v2.json"), "w") as f:
        json.dump(manifest, f, indent=2)
        
    # Replace original manifest for simplicity of the PR diff (or we can keep both)
    # The instructions say "publish a new manifest version"
    os.rename(os.path.join(base_dir, "manifest_v2.json"), os.path.join(base_dir, "manifest.json"))
    print("D0002 Manifest Generated.")

if __name__ == "__main__":
    build_manifest()
