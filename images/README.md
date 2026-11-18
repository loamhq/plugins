# Images, a Loam plugin

Your agents make and change pictures with OpenAI's or Google's image
models, and each is saved in your vault as an ordinary file. What it does,
what it reaches and what it costs is in [PLUGIN.md](PLUGIN.md), which is
also its manifest.

- **Needs** an OpenAI or a Google (Gemini) key, entered once in Loam's
  Settings › Accounts. It declares both (`keys:`), so Loam hands it those
  two keys and no other.
- **No dependencies**: Python 3.11's standard library. `tools/images_mcp.py`
  is the agents' tool (MCP over stdio); `tools/images_core.py` does the work.
- **Tests**: `python3 tests/test_images.py` runs it against a fake OpenAI and
  a fake Google, with no network and no key.

Install it from Settings › Plugins with this folder's link at a tag.
