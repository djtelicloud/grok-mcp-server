import os
import json
import hashlib

def canonical_hash(output_str):
    try:
        obj = json.loads(output_str)
        canonical = json.dumps(obj, sort_keys=True, separators=(',', ':'))
        return hashlib.sha256(canonical.encode()).hexdigest()
    except:
        # Fallback to string hash if not json
        return hashlib.sha256(output_str.encode()).hexdigest()

def dedup_file(filepath):
    seen = {}
    deduped = []
    
    with open(filepath, "r") as f:
        for line in f:
            row = json.loads(line)
            h = canonical_hash(row["target_output"])
            key = f"{row['root_id']}_{h}"
            
            if key in seen:
                # Merge provenance
                seen_row = seen[key]
                if row["donor_model"] not in seen_row["donor_model"]:
                    if isinstance(seen_row["donor_model"], list):
                        seen_row["donor_model"].append(row["donor_model"])
                    else:
                        seen_row["donor_model"] = [seen_row["donor_model"], row["donor_model"]]
            else:
                seen[key] = row
                deduped.append(row)
                
    with open(filepath, "w") as f:
        for row in deduped:
            f.write(json.dumps(row) + "\n")

if __name__ == "__main__":
    print("Running canonical JSON deduplication...")
    base_dir = os.path.dirname(os.path.dirname(__file__))
    for pack in ["intent_surface", "funnel_routing"]:
        p = os.path.join(base_dir, "packs", pack, "part-00000.jsonl")
        if os.path.exists(p):
            dedup_file(p)
    print("Deduplication complete.")
