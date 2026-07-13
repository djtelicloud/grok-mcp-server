import json, sys
items = json.loads(open(sys.argv[1]).read())
route_tool = {"name":"route","description":"Select the execution mode and route class for a user task.","parameters":{"mode":{"type":"string","description":"One of: fast, reasoning, thinking, research.","required":True},"route_class":{"type":"string","description":"One of: coding, planning, research, vision.","required":True}}}
with open("data/route_selection_ood.jsonl","w") as f:
    for it in items:
        f.write(json.dumps({"query": it["query"],
            "tools": json.dumps([route_tool], separators=(",",":")),
            "answers": json.dumps([{"name":"route","arguments":{"mode":it["mode"],"route_class":it["route_class"]}}], separators=(",",":"))})+"\n")
print("wrote", len(items), "OOD examples")
