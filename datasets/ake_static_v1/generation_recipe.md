# AKE Static Dataset (V1) Generation Recipe

## Operational Objective
Produce a static, provider-diverse candidate corpus mapping deterministic OKF markdown cells to synthetic FAQ intents and agentic trajectories.

## Schema / Data Model
The canonical row schema for this dataset:
- `id`
- `root_id`
- `variant_id`
- `pack`
- `model_input`
- `target_output`
- `ttl_facts`
- `leakage_group`
- `donor_model`
- `critic_model`
- `judge_model`
- `label_status`
- `corruption_recipe`

## Model Pipeline
1. **Donor (Synthesis)**: Vertex Gemma and Gemini Pro.
2. **Critic**: Grok 4.5 via UniGrok CLI Plane.
3. **Judge**: Gemini Pro (Blind).

## Pack Families
intent_surface, funnel_routing, observation_typing, memory_selection, resource_selection, tool_selection, plan_state_transition, recovery_selection, authorization_ttl_decision, verification_request
