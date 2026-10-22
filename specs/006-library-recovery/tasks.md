# Recover deployed library without re-embedding

- [x] Confirm live registry is empty while 3,332 retained vectors remain.
- [x] Export all retained vector metadata read-only, preserving original IDs/namespaces.
- [x] Match all eight original source files to their verified manifest hashes.
- [x] Reconstruct and verify legacy parent table indexes/sections/kinds.
- [x] Build a compressed, checksum-bound recovery bundle (no API keys or vector values).
- [x] Add empty-registry-only startup import, remote count/provenance checks and atomic publication.
- [x] Skip existing registries; preserve completed duplicate cleanup as source aliases.
- [x] Configure Replit startup automatically and choose the populated provider on initial frontend load.
- [x] Rehearse all nine original source downloads and both Tesla scope questions using the actual recovered library.
- [x] Fix Q1 derivation from an explicitly selected Q2 source when Q1 is also in the catalog.
- [ ] Redeploy this code and recovery bundle to Replit; verify its live state after startup.

No embeddings were generated and no Pinecone records were written/deleted in this increment. The rehearsal used an isolated local runtime; the deployed runtime is not directly accessible from this session.

Validation: 90-test full Python suite and desktop/mobile browser checks passed, including populated-provider fallback and explicit provider selection. An additional Replit auto-detection regression verifies recovery with an older custom Python launch command. The actual recovered library also passed the 307-vector duplicate-merge preview without applying it.
