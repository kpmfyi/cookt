# Sending recipes to cookt from iPhone / iPad

## Add to Home Screen first

Safari → your cookt HTTPS address (see the README) → Share → **Add to Home Screen**. The app then opens full screen and works
offline for reading and cooking.

## Share sheet

Android/desktop Chrome installs the PWA as a share target (`/share`, declared in `manifest.webmanifest`).
**iOS Safari does not support Web Share Target**, so on iPhone/iPad use this Shortcut (one-time setup, ~1 minute):

1. Shortcuts app → **+** → name it **Send to cookt**.
2. Shortcut details (ⓘ) → turn on **Show in Share Sheet**; input types: **URLs, Text, Images, Safari web pages**.
3. Add action **Get Contents of URL**:
   - URL: `https://<your-cookt-host>/share`
   - Method: **POST**, Request Body: **Form**
   - Field `url` (Text) = *Shortcut Input* → *URL*
   - Field `text` (Text) = *Shortcut Input* (for captions / pasted text)
   - Field `images` (File) = *Shortcut Input* (only when sharing photos or screenshots)
4. Optional: add **Open URLs** `https://<your-cookt-host>/inbox` to jump to the inbox.

What happens: the server creates an inbox item (link → fetch + parse; Instagram/TikTok/YouTube → caption via
`yt-dlp`, optional local Whisper transcript; images → vision model) and redirects to `/inbox`, where the item shows
*fetching → extracting → ready*. One tap saves it; enrichment (tags, nutrition, embedding) runs afterwards.

The phone must be able to reach the server (for a tailnet install, Tailscale running).
