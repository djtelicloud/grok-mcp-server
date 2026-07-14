import os
import json
import hashlib

def export_to_needle():
    print("Projecting JSONL to needle-tools-context-v1...")
    base_dir = os.path.dirname(os.path.dirname(__file__))
    pack_file = os.path.join(base_dir, "packs", "intent_surface", "part-00000.jsonl")
    
    if not os.path.exists(pack_file):
        return
        
    examples = []
    # Group by root_id to find chosen/rejected pairs
    groups = {}
    with open(pack_file, "r") as f:
        for line in f:
            row = json.loads(line)
            root = row["root_id"]
            if root not in groups:
                groups[root] = {"chosen": None, "rejected": None, "prompt": row["model_input"], "hash": row["leakage_group"]}
                
            if "SUPPORTED" in row["label_status"]:
                groups[root]["chosen"] = row["target_output"]
            elif "REJECTED" in row["label_status"]:
                groups[root]["rejected"] = row["target_output"]
                
    for root, data in groups.items():
        if data["chosen"] and data["rejected"]:
            examples.append({
                "prompt": data["prompt"],
                "chosen": data["chosen"],
                "rejected": data["rejected"],
                "source_capsule": f"ucap1:sha256:{data['hash']}"
            })
            
    needle_out = {
        "encoder_tokens": 1024,
        "max_encoder_tokens": 1024,
        "profile": "org.grokmcp.needle_tools_context.v1",
        "query_sha256": hashlib.sha256(b"static_query").hexdigest(),
        "tokenizer": "unigrok-default",
        "tools": [
            {
                "name": "select_unigrok_context",
                "description": "Select relevant verified UniGrok context. Examples are non-authoritative preference context; return capsule ids only.",
                "parameters": {
                    "capsule_id": {
                        "type": "string",
                        "description": "A relevant ucap1:sha256 capsule identifier."
                    }
                },
                "examples": examples
            }
        ],
        "used_source_capsules": [f"ucap1:sha256:{g['hash']}" for g in groups.values()]
    }
    
    out_file = os.path.join(base_dir, "needle_dpo_export.json")
    with open(out_file, "w") as f:
        json.dump(needle_out, f, indent=2)
        
    print(f"Exported {len(examples)} DPO pairs to {out_file}")

if __name__ == "__main__":
    export_to_needle()
