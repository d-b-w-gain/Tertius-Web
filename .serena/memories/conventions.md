# Conventions

- Keep deployment work as additive files rather than reshaping app internals unless the spec requires it.
- Kubernetes chart should not install cluster-wide operators; CloudNativePG and Keycloak operators are prerequisites.
- Local k3s defaults should avoid pulling local images: use local tags and `IfNotPresent`/`Never` semantics as configured in values.
- Secrets for Cloudflare, database passwords, Valkey auth, Keycloak admin/client credentials must be references or generated placeholders, not real committed values.