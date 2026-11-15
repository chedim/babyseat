# babyseat add-on

The babyseat seat (`cliffvm serve`: an AI seat whose memory is a graph the model programs) for Home Assistant OS and Supervised installs.
The add-on runs the seat and tells the **babyseat (membrane)** integration where it is and which token to use, so the integration sets itself up without typing an address or a token.

- The chat model: Claude with a Claude API key (the default), any OpenAI-compatible endpoint, a local Ollama server, or (advanced) a claude CLI you supply.
- The seat's data lives in the add-on's own folder (included in backups); the old 0.7 seat's chat history in Home Assistant's config folder is imported once, read-only.
- The page is on port 4096 (every route but the page needs a token).

See the Documentation tab for the options and the setup.
