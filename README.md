# Opus

A chatbot website with a looping blue video background, powered by the Gemini API.

## Run it on your computer

1. Open this folder in VS Code and open a terminal
2. Install the packages
   `pip install -r requirements.txt`
3. Copy `.env.example` to a new file named `.env` and put your key in it
   `GEMINI_API_KEY=your_real_key`
4. Start the server
   `python app.py`
5. Open http://127.0.0.1:5000

## How your API key stays safe

- The key lives only in the `.env` file on the server
- `.env` is listed in `.gitignore` so it is never uploaded to git
- The key is never put into any page, script, or response so the browser cannot see it
- It is sent to Google in a request header and not in the URL so it cannot show up in logs
- Error messages and logs have the key scrubbed out
- Requests from other websites are blocked and each visitor is rate limited
- Before sharing this folder or the zip always delete your own `.env` file first

## Deploy (Heroku style hosts)

The `Procfile` is included. Do not upload the `.env` file. Add `GEMINI_API_KEY` in the
host's environment variables settings instead. If the host uses a proxy also add `TRUST_PROXY=1`.

## Settings in .env

- `GEMINI_MODEL` the main model (default `gemini-3.5-flash`)
- `GEMINI_FALLBACK_MODELS` models tried in order if the main one is unavailable for your key
- `RATE_LIMIT_PER_MINUTE` messages per visitor per minute (default 20)

## Answer style

Opus is told to answer in plain words with no punctuation and the server also strips any
punctuation that slips through. Code blocks are kept as they are.
