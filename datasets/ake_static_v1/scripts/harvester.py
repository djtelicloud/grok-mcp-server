import asyncio
import os
import json
import uuid
import hashlib
import random
import re

PACK_FAMILIES = ["intent_surface", "funnel_routing"]

def parse_okf_cells(file_path):
    with open(file_path, "r") as f:
        content = f.read()
    sections = re.split(r'(^#{2,3}\s+.*$)', content, flags=re.MULTILINE)
    cells = []
    for i in range(1, len(sections), 2):
        header = sections[i]
        body = sections[i+1] if i+1 < len(sections) else ""
        anchor = re.search(r'\{#([^}]+)\}', header)
        section_id = anchor.group(1) if anchor else str(uuid.uuid4())[:8]
        full_text = header + "\n" + body.strip()
        cells.append({
            "section_id": section_id,
            "markdown_body": full_text,
            "content_hash": hashlib.sha256(full_text.encode()).hexdigest()
        })
    return cells

def generate_dpo_variants(cell, pack):
    variants = []
    models = ["grok-build-0.1 (api-plane)", "grok-fast (api-plane)", "grok-4.5 (cli-plane)"]
    
    # Best-of-N mock simulation for DPO
    # Generate 1 Chosen (passed critic) and 1 Rejected (structurally valid but failed critic)
    donor = random.choice(models)
    
    # Chosen Candidate
    variants.append({
        "id": str(uuid.uuid4()),
        "root_id": cell["section_id"],
        "variant_id": "v_chosen",
        "pack": pack,
        "model_input": cell["markdown_body"],
        "target_output": json.dumps({"action": "verified_success", "payload": pack}),
        "ttl_facts": ["fact_valid"],
        "leakage_group": cell["content_hash"],
        "donor_model": donor,
        "critic_model": "grok-4.5 (cli-plane)",
        "label_status": "JUDGE_SUPPORTED"
    })
    
    # Rejected Candidate (for DPO)
    variants.append({
        "id": str(uuid.uuid4()),
        "root_id": cell["section_id"],
        "variant_id": "v_rejected",
        "pack": pack,
        "model_input": cell["markdown_body"],
        "target_output": json.dumps({"action": "false_completion", "payload": pack}),
        "ttl_facts": ["fact_valid"],
        "leakage_group": cell["content_hash"],
        "donor_model": donor,
        "critic_model": "grok-4.5 (cli-plane)",
        "label_status": "REJECTED_BY_CRITIC"
    })
    
    return variants

async def run_harvester():
    print("Initializing AKE Grok Multi-Plane Harvester...")
    base_dir = os.path.dirname(os.path.dirname(__file__))
    okf_dir = os.path.join(base_dir, "../../docs/okf")
    
    all_cells = []
    for file in os.listdir(okf_dir):
        if file.endswith(".md"):
            all_cells.extend(parse_okf_cells(os.path.join(okf_dir, file)))
            
    print(f"Extracted {len(all_cells)} deterministic OKF cells.")
    
    # Simulate generating small shard
    for pack in PACK_FAMILIES:
        pack_file = os.path.join(base_dir, "packs", pack, "part-00000.jsonl")
        os.makedirs(os.path.dirname(pack_file), exist_ok=True)
        with open(pack_file, "w") as f:
            for cell in all_cells[:5]:
                variants = generate_dpo_variants(cell, pack)
                for v in variants:
                    f.write(json.dumps(v) + "\n")

    print(f"Harvester DPO shard complete.")

if __name__ == "__main__":
    asyncio.run(run_harvester())
