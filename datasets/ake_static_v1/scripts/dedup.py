import os
import json
import hashlib
import unicodedata

def normalize_string(s: str) -> str:
    """Canonicalize Unicode, line endings, and trailing whitespace."""
    if not isinstance(s, str):
        return str(s)
    s = unicodedata.normalize('NFC', s)
    s = s.replace('\r\n', '\n').replace('\r', '\n')
    return "\n".join([line.rstrip() for line in s.split('\n')]).strip()

def strict_digest(pack: str, input_str: str, output_str: str) -> str:
    norm_in = normalize_string(input_str)
    norm_out = normalize_string(output_str)
    payload = f"{pack}|||{norm_in}|||{norm_out}".encode('utf-8')
    return hashlib.sha256(payload).hexdigest()

def merge_provenance(existing, new_val):
    if not existing:
        return [new_val] if new_val else []
    if isinstance(existing, list):
        if new_val not in existing:
            existing.append(new_val)
        return existing
    return [existing, new_val] if existing != new_val else [existing]

def dedup_pack(pack_dir: str, pack_name: str, queue_file: str):
    # Dictionaries to hold state
    # Key: hash(pack + input + output) -> canonical row (for exact matches)
    exact_matches = {}
    
    # Key: hash(pack + input) -> list of target hashes (to find target conflicts)
    input_to_targets = {}
    
    # Gather all JSONL files in the pack directory
    files = [f for f in os.listdir(pack_dir) if f.endswith(".jsonl")]
    
    for file in files:
        filepath = os.path.join(pack_dir, file)
        with open(filepath, "r") as f:
            for line in f:
                row = json.loads(line)
                
                # Check for things we never collapse
                # If they differ in TTL state, auth, or corruption recipe, we must isolate them.
                # For this implementation, we incorporate these critical fields into the hash
                # to strictly prevent collapsing them.
                ttl_str = json.dumps(row.get("ttl_facts", []))
                recipe = row.get("corruption_recipe", "none")
                
                input_str = row["model_input"]
                output_str = row["target_output"]
                
                # Input digest (for conflict detection)
                input_key = strict_digest(pack_name, input_str + ttl_str + recipe, "")
                
                # Full digest (for exact merging)
                full_key = strict_digest(pack_name, input_str + ttl_str + recipe, output_str)
                
                if input_key not in input_to_targets:
                    input_to_targets[input_key] = set()
                input_to_targets[input_key].add(full_key)
                
                if full_key in exact_matches:
                    # Same input and same target -> Merge Provenance
                    existing = exact_matches[full_key]
                    existing["donor_model"] = merge_provenance(existing.get("donor_model"), row.get("donor_model"))
                    existing["critic_model"] = merge_provenance(existing.get("critic_model"), row.get("critic_model"))
                    if "generation_wave" in row:
                        existing["generation_wave"] = merge_provenance(existing.get("generation_wave", "D0001"), row["generation_wave"])
                else:
                    # Initialize provenance as lists for uniform structure
                    row["donor_model"] = [row.get("donor_model")] if row.get("donor_model") else []
                    row["critic_model"] = [row.get("critic_model")] if row.get("critic_model") else []
                    row["generation_wave"] = [row.get("generation_wave", "D0001")]
                    exact_matches[full_key] = row

    # Adjudication Logic
    canonical_rows = []
    adjudication_rows = []
    
    for input_key, targets in input_to_targets.items():
        if len(targets) > 1:
            # Conflict! Same input, different targets.
            for t in targets:
                row = exact_matches[t]
                row["label_status"] = "DISPUTED"
                adjudication_rows.append(row)
        else:
            canonical_rows.append(exact_matches[list(targets)[0]])
            
    # Write canonical deduplicated rows
    with open(os.path.join(pack_dir, "canonical_dataset.jsonl"), "w") as f:
        for row in canonical_rows:
            f.write(json.dumps(row) + "\n")
            
    # Write adjudication queue
    if adjudication_rows:
        with open(queue_file, "a") as f:
            for row in adjudication_rows:
                f.write(json.dumps(row) + "\n")
                
    return len(canonical_rows), len(adjudication_rows)

if __name__ == "__main__":
    print("Running Strict Exact-Digest Deduplication Engine...")
    base_dir = os.path.dirname(os.path.dirname(__file__))
    packs_dir = os.path.join(base_dir, "packs")
    queue_file = os.path.join(base_dir, "adjudication_queue.jsonl")
    
    # Clear old queue
    if os.path.exists(queue_file):
        os.remove(queue_file)
        
    total_canonical = 0
    total_disputes = 0
    
    for pack in os.listdir(packs_dir):
        pack_dir = os.path.join(packs_dir, pack)
        if os.path.isdir(pack_dir):
            c, d = dedup_pack(pack_dir, pack, queue_file)
            total_canonical += c
            total_disputes += d
            
    print(f"Deduplication complete. Canonical Rows: {total_canonical}, Disputed Rows sent to adjudication: {total_disputes}")
