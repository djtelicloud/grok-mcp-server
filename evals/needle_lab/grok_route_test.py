# Transform a raw grok-4.5 route-generation batch (JSON list of
# {query, mode, route_class}) into needle-format JSONL.
#
# Provenance: the raw inputs were one-shot grok-4.5 generations via the
# UniGrok MCP and are only partially committed — data/route_selection_ood.jsonl
# (48 items; raw input not kept) and data/route_sealed.jsonl (40 items;
# raw input committed as data/sealed_raw.json, transform verified byte-exact).
#
# Usage: python grok_route_test.py <raw_items.json> [out.jsonl]
import json, sys
items = json.loads(open(sys.argv[1]).read())
out_path = sys.argv[2] if len(sys.argv) > 2 else "data/route_selection_ood.jsonl"
route_tool = {"name":"route","description":"Select the execution mode and route class for a user task.","parameters":{"mode":{"type":"string","description":"One of: fast, reasoning, thinking, research.","required":True},"route_class":{"type":"string","description":"One of: coding, planning, research, vision.","required":True}}}
with open(out_path,"w") as f:
    for it in items:
        f.write(json.dumps({"query": it["query"],
            "tools": json.dumps([route_tool], separators=(",",":")),
            "answers": json.dumps([{"name":"route","arguments":{"mode":it["mode"],"route_class":it["route_class"]}}], separators=(",",":"))})+"\n")
print("wrote", len(items), "examples to", out_path)
