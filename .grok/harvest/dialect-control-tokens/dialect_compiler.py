import json

class DialectCompiler:
    """V13 Functional Singularity: Translates DAG intents into Native Latent IPC."""
    
    def __init__(self, matrix_path: str = ".agent/brain/dialect_matrix.json"):
        with open(matrix_path, "r", encoding="utf-8") as f:
            self.matrix = json.load(f).get("dialects", {})

    def compile(self, model_family: str, mission: str, payload: dict = None, force_tool: bool = False, force_reasoning: bool = False) -> str:
        """Crushes intent into the target's native Token Control Language."""
        # O(1) fallback to generic gemini if family is unrecognized
        dialect = self.matrix.get(model_family.lower(), self.matrix["gemini"])
        
        # Phase 1: Structural Lock & Safety Bypass
        prompt = f"{dialect.get('system_lock', '')}{dialect.get('safety_bypass', '')}{mission}\n"
        
        # Phase 2: Payload Injection
        if payload:
            prompt += f"{dialect.get('user_wrap_open', '')}{json.dumps(payload, separators=(',', ':'))}\n"
        
        # Phase 3: Format Coercion (The Pre-fill Trick)
        prompt += dialect.get('assistant_force', '')
        
        if force_tool:
            prompt += dialect.get('tool_coercion_open', '')
        elif force_reasoning:
            prompt += dialect.get('reasoning_start', '')
            
        return prompt

if __name__ == "__main__":
    # Internal Unit Test
    compiler = DialectCompiler()
    test_out = compiler.compile("llama3", "Analyze the target.", {"target": "x"}, force_reasoning=True)
    assert "<|begin_of_text|>" in test_out
    print("[EXIT 0] DialectCompiler validated.")
