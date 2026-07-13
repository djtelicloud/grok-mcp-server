import os
import json
import hashlib
import tempfile
import shutil
from enum import Enum
from .schemas import BaseRootEnvelope

class RunMode(str, Enum):
    MOCK = "mock"
    REPLAY = "replay"
    LIVE = "live"

class ProviderAdapter:
    def __init__(self, provider: str, model: str, plane: str, role: str, mode: RunMode):
        self.provider = provider
        self.model = model
        self.plane = plane
        self.role = role
        self.mode = mode
        
        # Safe cache location outside of tracked source. Segregated by mode.
        self.cache_dir = os.path.expanduser(f"~/.gemini/antigravity/cache/campaigns/gemma-needle-2000-v1/{mode.value}")
        os.makedirs(self.cache_dir, exist_ok=True)
        
    def _compute_cache_key(self, schema_version: str, template_digest: str, settings: dict, request: str) -> str:
        # Do not lowercase request, case matters for JSON artifacts
        key_data = {
            "provider": self.provider,
            "model": self.model,
            "plane": self.plane,
            "role": self.role,
            "schema_version": schema_version,
            "template_digest": template_digest,
            "settings": settings,
            "request": request
        }
        key_str = json.dumps(key_data, sort_keys=True)
        return hashlib.sha256(key_str.encode()).hexdigest()
        
    def _atomic_write_cache(self, key: str, response: dict):
        path = os.path.join(self.cache_dir, f"{key}.json")
        fd, temp_path = tempfile.mkstemp(dir=self.cache_dir)
        with os.fdopen(fd, 'w') as f:
            json.dump(response, f)
        shutil.move(temp_path, path)

    def _read_cache(self, key: str) -> Optional[dict]:
        path = os.path.join(self.cache_dir, f"{key}.json")
        if os.path.exists(path):
            with open(path, 'r') as f:
                return json.load(f)
        return None

    def extract_json_artifact(self, content: str) -> Optional[dict]:
        """Strict JSON parsing from content, handling promise preface."""
        try:
            start_idx = content.find('{')
            end_idx = content.rfind('}')
            if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
                json_str = content[start_idx:end_idx+1]
                return json.loads(json_str)
            return None
        except Exception:
            return None

    def validate_response(self, response: dict) -> bool:
        """Fail-closed response-schema validation, artifact-presence, promise-only rejection."""
        if not response or not isinstance(response, dict):
            return False
            
        content = response.get("content", "")
        if not content.strip():
            return False
            
        # Reject promise-only without artifact
        artifact = self.extract_json_artifact(content)
        if not artifact:
            return False
            
        # Ensure it actually decodes to a known schema or dict
        if not isinstance(artifact, dict):
            return False
            
        return True

    def execute(self, request: str, schema_version: str, template_digest: str, settings: dict) -> dict:
        key = self._compute_cache_key(schema_version, template_digest, settings, request)
        
        # Never allow mock replay in live, isolated namespaces guarantee this.
        if self.mode in (RunMode.MOCK, RunMode.REPLAY):
            cached = self._read_cache(key)
            if cached and self.validate_response(cached):
                return cached
            if self.mode == RunMode.REPLAY:
                raise ValueError("Replay mode: Cache miss or invalid cache for deterministic execution.")
        
        if self.mode == RunMode.MOCK:
            # Generate a valid mock response
            response = {"content": "I'll do that right away.\n{\"mocked_artifact\": true}"}
            if self.validate_response(response):
                self._atomic_write_cache(key, response)
            return response
            
        raise NotImplementedError("Live mode not implemented for Stage 0.")
