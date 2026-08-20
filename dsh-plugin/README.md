# Mount Cadfree tools in a DeepSeek Harness profile

Cadfree does not fork `deepseek-ai/deepseek-harness`. The harness is a plugin
runtime; this package *is* a plugin.

1. Clone or `npx @deepseek-ai/dsh web` as usual.
2. Point a patch file at this folder (or copy `dsh-plugin/` into your harness workspace).
3. Export `CADFREE_ROOT` (this repo) and `CADFREE_PROJECT_ID`.
4. The plugin shells `python -m cadfree.cli tool …`, which is the same tool
   surface the Python agent loop uses.

The Python app at `http://127.0.0.1:8181` is the product UI (Carrot). Use dsh
when you want Cadfree tools inside an existing harness session.
