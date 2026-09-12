# JARVIS application plugins

This directory defines the platform's future extension boundary, not a Codex plugin. The Rust `ProposalPlugin` trait accepts bounded observation data and returns bounded typed proposals. It does not provide a runtime loader, native handles, or an OS sandbox. Untrusted plugin code must run in an isolated worker with separately approved resources, network policy and capabilities.

`manifest.example.json` is a non-executable manifest design example. The signed artifact hash, signer, platform compatibility and requested permissions must be verified before installation. A declared request is not a capability grant; neither installation nor updates may silently inherit daemon authority. The target database separates immutable Plugin/Tool versions from mutable per-device installation state.
