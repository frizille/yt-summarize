# YouTube Summarizer

Chapter-aware YouTube summaries, hosted on Vercel with Supabase.

## Stack

- **Frontend** — vanilla HTML/CSS/JS (`public/index.html`), served as a Vercel static asset
- **Backend** — FastAPI serverless function (`api/index.py`), Python 3.12, 300 s max duration
- **Database** — Supabase (PostgreSQL via psycopg2)
- **Summarization** — Claude API (`claude-sonnet-4`)
- **Transcript/metadata** — yt-dlp (Python API) + youtube-transcript-api

## Setup

### 1. Supabase

1. Create a project at [supabase.com](https://supabase.com).
2. Open the **SQL Editor** and run `supabase/schema.sql`.
3. Copy the **Transaction pooler** connection string from  
   *Project Settings → Database → Connection string → Transaction mode*  
   (use the pooler URL for serverless — port 6543).

### 2. Vercel

1. Import this repo in Vercel.
2. Add environment variables in *Project Settings → Environment Variables*:

   | Variable | Value |
   |---|---|
   | `DATABASE_URL` | Supabase Transaction pooler connection string |
   | `ANTHROPIC_API_KEY` | Your Anthropic API key |

3. Deploy. The root (`/`) serves the frontend; `/api/*` hits the serverless function.

## Local development

```bash
pip install -r requirements.txt
# add uvicorn for local dev: pip install uvicorn

export DATABASE_URL="postgresql://postgres:PASSWORD@HOST:6543/postgres"
export ANTHROPIC_API_KEY="sk-ant-..."

uvicorn api.index:app --reload --port 8000
# Open public/index.html in a browser (or proxy it to localhost:8000)
```

## How it works

1. Paste a YouTube URL → `POST /api/videos`
2. FastAPI adds the video to Supabase and kicks off a background task
3. yt-dlp fetches metadata + chapters (Python API, no subprocess)
4. youtube-transcript-api pulls the transcript; yt-dlp auto-subs as fallback
5. Each chapter's transcript slice → Claude summarizes it
6. Claude synthesizes an overall summary + key points
7. UI polls `/api/videos/{id}/status` every 3 s until done

## Status flow

```
queued → processing → done
                   ↘ error  (retry available)
```

## API

| Method | Path | Description |
|---|---|---|
| POST | /api/videos | Add URL to queue |
| GET | /api/videos | List all videos |
| GET | /api/videos/{id} | Video + chapters + summary |
| GET | /api/videos/{id}/status | Poll for status |
| POST | /api/videos/{id}/retry | Retry after error |
| DELETE | /api/videos/{id} | Remove from queue |

## Project structure

```
api/
  index.py          # FastAPI app (all routes, Vercel entry point)
lib/
  database.py       # Supabase / psycopg2
  youtube.py        # yt-dlp + youtube-transcript-api
  summarizer.py     # Anthropic Claude API
public/
  index.html        # Frontend SPA
supabase/
  schema.sql        # Run once in Supabase SQL editor
vercel.json         # Routing + function config
requirements.txt
```
