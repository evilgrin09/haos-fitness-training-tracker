"""CSV download of the training data of the requesting user."""

from __future__ import annotations

from aiohttp import web
from homeassistant.components.http import KEY_HASS_USER, HomeAssistantView
from homeassistant.util import dt as dt_util

from .const import DOMAIN, EXPORT_URL
from .store import GymStore


class ExportView(HomeAssistantView):
    """Serves the CSV export; the panel opens it through a path signed for its user."""

    url = EXPORT_URL
    name = f"api:{DOMAIN}:export"

    async def get(self, request: web.Request) -> web.Response:
        """Return the data of the signed-in user as CSV."""
        store: GymStore | None = request.app["hass"].data.get(DOMAIN)
        if store is None or store.db is None:
            return web.Response(status=503)
        user = request.get(KEY_HASS_USER)
        if user is None or not store.allowed(user.id):
            return web.Response(status=403)
        text = await store.async_read(lambda db: db.export_csv(user.id))
        filename = f"gym-tracker-{dt_util.now().date().isoformat()}.csv"
        return web.Response(
            body=text.encode("utf-8"),
            content_type="text/csv",
            charset="utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
