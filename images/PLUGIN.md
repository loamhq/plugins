---
id: images
name: Images
version: 1.1.0
author: Loam contributors
api: 0
kind: [extension]
icon: assets/icon.svg
keys:
  - provider: openai
    why: Makes and changes images with gpt-image when you have an OpenAI key
    optional: true
  - provider: google
    why: Makes and changes images with Gemini when you have a Google key
    optional: true
touches:
  network:
    - host: api.openai.com
      why: Sends the prompt and any source images to OpenAI's image models
    - host: generativelanguage.googleapis.com
      why: Sends the prompt and any source images to Google's Gemini image models
  record: none
  memory: none
  server: false
contributes:
  agent.tool:
    - name: images
      run: python3 tools/images_mcp.py
      lanes: [persona, engineer]
  agent.prompt:
    - lanes: [persona]
      text: "You can make and change images with generate_image (the Images plugin): from words, or from pictures you pass as sources. It saves each into the vault as an ordinary file. Read the file to see what you made before you show it. To show an image in your reply, put ![a few words](its path) on a line of its own; up to three show in your message."
  settings.entry:
    - key: folder
      label: Save new images in
      note: A folder in your vault. An agent can still save one somewhere else when you ask.
      default: Images
    - key: backend
      label: Made with
      choices: [auto, openai, google]
      labels: {auto: "Whichever key you have", openai: OpenAI, google: Google Gemini}
      note: With both keys in Accounts, OpenAI makes them unless you choose Google.
      default: auto
    - key: openai_model
      label: OpenAI model
      advanced: true
      default: gpt-image-2
    - key: gemini_model
      label: Google model
      advanced: true
      default: gemini-3-pro-image-preview
  vault.folder:
    - setting: folder
---

# Images

Your agents make and change pictures: a cover, a diagram's illustration, a
mock-up, a photo with the sky changed. Each is saved in your vault as an
ordinary PNG, JPEG or WebP file, in the **Images** folder unless the agent
names another place, so it is yours like any other file.

**It needs a key.** Add an OpenAI key or a Google (Gemini) key in
Settings › Accounts. With both, OpenAI makes your images unless you choose
Google under **Made with** in this plugin's settings. The images are billed to you by the
service that makes them; a high-quality OpenAI image costs roughly 35 times
a low-quality one, and each call's cost is in the agent's answer.

**In a conversation**, an agent shows an image by writing
`![a few words](path)` on a line of its own: up to three appear in its
message, and a tap opens one full size.

Every engine that speaks MCP gets the same tool, `generate_image`. An agent
whose engine has image making of its own may use either.
