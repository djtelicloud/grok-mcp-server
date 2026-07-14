# Mock Needle packet (committed test fixture)

Tiny synthetic packet so `needle-training-campaign.js` mock mode and the
gate-validator tests run from `main` without any external bundle. All
rows are fabricated; nothing here is or derives from sealed evaluation
data, and this packet must never be used as a live training packet.

Split policy: _per_tool_split seed 42 (pinned before corpus assembly).
Base checkpoint sha256 prefix: 40a32e91d1d4197b
Env: python 3.11, jax 0.4, flax 0.8
