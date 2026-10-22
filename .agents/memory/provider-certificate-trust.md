---
name: Provider certificate trust
description: Why provider HTTPS clients must retain system certificate trust on Replit
---

Use verified operating-system certificate trust for provider HTTPS connections; never disable TLS verification to make publishing succeed.

**Why:** On Replit, the default Python HTTPX certificate chain failed for both Pinecone and unrelated HTTPS sites while system trust succeeded. Library recovery contacts Pinecone before startup completes, so a certificate failure can cause a publishing crash loop even when an existing workspace library starts normally.

**How to apply:** Preserve system trust when adding or replacing provider HTTP clients. Verify recovery into an isolated empty runtime as well as startup against existing data; do not overwrite the live library or rewrite vectors to test recovery.
