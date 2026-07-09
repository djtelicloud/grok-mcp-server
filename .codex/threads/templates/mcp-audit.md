# MCP Contract Audit Thread Prompt

You are a Codex background thread auditing UniGrok MCP contracts.

Focus on Codex-observable behavior only:

- MCP server tool registration
- OpenAI-compatible HTTP gateway contracts
- tool schemas and annotations
- thread-safe execution paths visible through tests
- Codex connector compatibility risks

Return:

- source files inspected
- contract drift risks
- compatibility risks for Codex, MCP_DOCKER, or Grok MCP clients
- targeted tests to add or run
- whether a follow-up implementation thread is recommended

