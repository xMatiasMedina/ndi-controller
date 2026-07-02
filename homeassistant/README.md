# LoreaTec NDI Controller — Home Assistant integration

A custom integration that exposes the NDI Controller's **default playlist** as a
combo box (a `select` entity) under a single "NDI Controller" device. Picking a
playlist sets it as the auto-resuming default; the controller applies it
immediately (starts it if idle, or swaps if the previous default was showing).

## Install

1. Copy the `custom_components/loreatec_ndi` folder into your Home Assistant
   config directory, so you end up with:

   ```
   <config>/custom_components/loreatec_ndi/
   ```

   (On HAOS/Supervised, `<config>` is `/config`. With the Samba/SSH add-on, drop
   it into the `config/custom_components/` share.)

2. Restart Home Assistant.

3. **Settings → Devices & Services → Add Integration**, search for
   **"LoreaTec NDI Controller"**.

4. In the popup, enter the controller's **IP address** and **Port**
   (default `8000`). HTTPS-vs-HTTP and the self-signed certificate are handled
   automatically.

5. A device named **NDI Controller** appears with a **Default playlist** select
   entity (`select.ndi_controller_default_playlist`).

## What you get

- **`select` entity** listing every playlist, plus a `— None —` option to clear
  the default. Its current value reflects the controller's current default.
- The list refreshes every 30 seconds, so playlists added/removed on the
  controller show up automatically.

## Using it in automations

```yaml
service: select.select_option
target:
  entity_id: select.ndi_controller_default_playlist
data:
  option: "Lobby Loop"   # must match a playlist name exactly
```

## Notes

- Setting the default **auto-applies** — no separate "play" call is needed
  (the API's `POST /api/default-playlist` runs `refresh_default` server-side).
- Playlist names are assumed unique (the dropdown matches by name).
- The integration is local-polling and needs no cloud account.
