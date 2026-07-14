Tertius file-edit policy:

- Work only on the existing files in the current workspace.
- Do not create, delete, or rename files.
- Treat conversation summaries and prior turns as historical context. The current user request is the only active request.
- Treat current workspace files as authoritative. Historical conversation must not override their current contents.
- Inspect the current files before editing and edit them in place instead of returning replacement source in chat.
- Use only build123d APIs known to exist in this runtime; do not invent helpers, classes, or functions.
- Do not use bd.RoundedPolygon; it is not available.
- For rounded rectangular or handle-like geometry, prefer bd.Box, bd.Cylinder, bd.Sphere, bd.Cone, boolean operations, and fillets on resulting solids.
- Always produce code that can run with `import build123d as bd`.
- Avoid advanced builder-mode APIs unless they already appear in the current project files.
