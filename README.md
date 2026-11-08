# SpiceBot

A spice and herb guide with a chat assistant. Online, questions go to Google's Gemini AI through a small server, along with the matching library notes and book passages. Offline, or if the AI is unavailable, the built-in brain answers from the same library.

## Live server (Render)

The repo deploys to Render as one web service using `render.yaml`:

1. On Render, choose **New → Blueprint** and pick this repo (or **New → Web Service** with start command `python backend/server.py`).
2. In the service's **Environment**, add `GEMINI_API_KEY` with your key from Google AI Studio. Never put the key in code or commit it.
3. Deploy. The site is served at `https://spicebot.onrender.com/`, and `/api/health` reports whether the AI is ready.

On the free plan the server sleeps after a period without visits, so the first question after a break can take up to a minute while it wakes; the page shows "Waking up SpiceBot's AI…" meanwhile.

If Render gives the service a different address, update `API_BASE` in the web page and `SITE_HOST` in `MainActivity.java`, then rebuild.

## Running locally

```
cp backend/.env.example backend/.env   # then paste your key into backend/.env
python backend/server.py               # http://127.0.0.1:8000
```

The server needs only Python's standard library.

## How the app works

The Android app (`web/SpiceBot.apk`, version 4) opens the live site, so changes to the website reach the app without a reinstall. If the site can't be reached within 12 seconds, or there's no internet, it opens the copy built into the app, where the built-in brain answers.

## Files

- `web/`: the website (single page, service worker for offline use, icons, the APK)
- `backend/server.py`: serves `web/` and the `/api/chat` endpoint that calls Gemini (model fallback, rate limit of 15 requests a minute per visitor)
- `render.yaml`: Render blueprint
- `android/`: the Android app source

## Updating

Change the files in `web/`, commit and push; Render redeploys automatically and the app picks up the change on its next launch.
