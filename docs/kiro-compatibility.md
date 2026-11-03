# Kiro 2.37.1 / V3 compatibility

The single CI-owned manifest is `src/kiro_headless_service/kiro-release.json`. It is packaged
with the wheel and copied into the image build. It selects **2.37.1**, **v3**, and exact official
archive URLs, sizes and SHA-256 for Linux amd64/arm64. Runtime settings cannot change version
or engine. Existing jobs preserve their expected release; incompatible jobs fail rather than
being interpreted using another engine. Update the manifest and rebuild/test to upgrade.

Official documentation reviewed on 2026-11-03:

- [Headless](https://kiro.dev/docs/cli/headless/): API-key auth, stdin, stream-json, explicit V3,
  workspace trust, noninteractive permissions and required MCP startup.
- [Native agents](https://kiro.dev/docs/custom-agents/configuration-reference/): JSON/Markdown,
  prompt file references, tool selectors, permissions, resources and MCP configuration.
- [CLI](https://kiro.dev/docs/reference/cli-commands/): KIRO_HOME and KIRO_RUNTIME_DIR (2.37+).
- [License](https://kiro.dev/license/) and
  [AWS IP terms](https://aws.amazon.com/legal/aws-ip-license-terms/): no public redistribution
  rights are assumed for the proprietary CLI. Service code is MIT independently.

The actual official amd64 archive was downloaded and checksum-verified. The extracted CLI
reports 2.37.1. Its help confirms every argument used by the adapter:

```text
kiro-cli chat --agent-engine v3 --trust-workspace --no-interactive
  --output-format stream-json --agent <selected-native-id>
  [--model <enabled-id>] [--effort <level>] [--require-mcp-startup]
```

No trust-all or legacy allowedTools flag is used. Native permissions govern preapproval.
Only generated controlled workspaces are trusted; consumer content cannot install agents,
MCP servers or executable hooks. Profile built-in tool ceilings and deny rules remain enforced.

The actual `settings` command confirmed `.kiro/settings/cli.json` beneath isolated KIRO_HOME.
The adapter disables automatic knowledge/code intelligence and default resource inheritance
there, sets includeMcpJson/includePowers false, and loads only captured resources. KIRO_RUNTIME_DIR
points to a writable directory inside the disposable run. Host accounts/settings are absent.

The decoder handles the supported runStarted/sessionUpdate/metadata/runFinished/runError
envelope subset and rejects malformed, incomplete, oversized, unknown or truncated output.
Successful completion requires exit zero and a successful nontruncated end_turn record.
Business JSON Schema validation is a separate service step. Conversation continuity reconstructs
service history; there is no ACP/native session recovery claim.

No valid KIRO_API_KEY was available. Native agent `validate` on the real CLI required login
and was not completed; it is not counted as provider validation. Version/help/settings checks
used no inference. Successful V3 inference and actual provider MCP invocation remain opt-in
checks. Subprocess doubles explicitly simulate the protocol and never certify the provider.

amd64 version/help executed locally. arm64 archives are pinned and CI includes native arm64
CLI checks, but that hosted job was not run from this environment. Rootless Podman image checks
are prepared separately. See verification.md for what actually ran.
