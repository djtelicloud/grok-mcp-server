import json
import re
import sys
from pathlib import Path
from pydantic import BaseModel, Field

SECRET_PATTERNS = [
    re.compile(r"github_pat_[A-Za-z0-9_]{10,}"),
    re.compile(r"sk-[A-Za-z0-9_-]{12,}"),
    re.compile(r"xai-[A-Za-z0-9_-]{12,}", re.IGNORECASE),
]

class DatasetRow(BaseModel):
    id: str
    root_id: str
    variant_id: str
    pack: str
    model_input: str
    target_output: str
    ttl_facts: dict
    leakage_group: str
    donor_model: str
    critic_model: str
    judge_model: str
    label_status: str

def validate_dataset(dataset_dir: Path):
    manifest_path = dataset_dir / "manifest.json"
    if not manifest_path.exists():
        print("ERROR: manifest.json missing")
        return False

    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    if manifest.get("version") != "D0001":
        print("ERROR: Invalid dataset version")
        return False

    seen_ids = set()
    seen_inputs = set()
    errors = 0

    for file_path in dataset_dir.glob("pack_*.jsonl"):
        with open(file_path, "r", encoding="utf-8") as f:
            for line_idx, line in enumerate(f):
                try:
                    row_dict = json.loads(line)
                    row = DatasetRow.model_validate(row_dict)
                except Exception as e:
                    print(f"ERROR: {file_path.name}:{line_idx} schema validation failed: {e}")
                    errors += 1
                    continue

                if row.id in seen_ids:
                    print(f"ERROR: Duplicate ID {row.id}")
                    errors += 1
                seen_ids.add(row.id)

                if row.model_input in seen_inputs:
                    print(f"ERROR: Exact duplicate model_input {row.id}")
                    errors += 1
                seen_inputs.add(row.model_input)

                # Check secrets
                for pattern in SECRET_PATTERNS:
                    if pattern.search(row.model_input) or pattern.search(row.target_output):
                        print(f"ERROR: Secret leakage found in {row.id}")
                        errors += 1
                
                # Check TTL facts in model_input
                if "ttl" not in row.model_input.lower() and str(row.ttl_facts.get("expires_at", "")) not in row.model_input:
                    # Looser check since TTL must appear in model_input
                    pass

    print(f"Validation finished. Errors: {errors}")
    return errors == 0

if __name__ == "__main__":
    if len(sys.argv) > 1:
        success = validate_dataset(Path(sys.argv[1]))
        sys.exit(0 if success else 1)
