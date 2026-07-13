import os
import json
import hashlib
import tempfile
import shutil
from enum import Enum
from typing import Dict, Any, Optional

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
        # Safe cache location outside of tracked source
        self.cache_dir = os.path.expanduser("~/.gemini/antigravity/cache/campaigns/gemma-needle-2000-v1")
        os.makedirs(self.cache_dir, exist_ok=True)
        
    def _compute_cache_key(self, schema_version: str, template_digest: str, settings: dict, request: str) -> str:
        key_data = {
            "provider": self.provider,
            "model": self.model,
            "plane": self.plane,
            "role": self.role,
            "schema_version": schema_version,
            "template_digest": template_digest,
            "settings": settings,
            "request": request.strip().lower()
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

    def validate_response(self, response: dict) -> bool:
        """Fail-closed response-schema validation, artifact-presence, promise-only rejection."""
        if not response or not isinstance(response, dict):
            return False
            
        content = response.get("content", "")
        # Reject empty or promise-only without artifact
        if not content.strip() or ("I'll" in content and "{" not in content):
            return False
            
        return True

    def execute(self, request: str, schema_version: str, template_digest: str, settings: dict) -> dict:
        key = self._compute_cache_key(schema_version, template_digest, settings, request)
        
        if self.mode in (RunMode.MOCK, RunMode.REPLAY):
            cached = self._read_cache(key)
            if cached:
                return cached
            if self.mode == RunMode.REPLAY:
                raise ValueError("Replay mode: Cache miss for deterministic execution.")
        
        if self.mode == RunMode.MOCK:
            response = {"content": "{ 'mocked_artifact': true }"}
            if self.validate_response(response):
                self._atomic_write_cache(key, response)
            return response
            
        # LIVE mode implementation goes here (e.g. bounded retries)
        raise NotImplementedError("Live mode not implemented for Stage 0.")
