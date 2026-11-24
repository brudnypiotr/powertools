"""Did the user answer in the meantime? Looks only at the open questions and tasks: one request for up to 20 chats and one
for the sent mail, no model. Only conversations the user wrote in (or reacted in) are then read and sorted again."""

from __future__ import annotations

import logging
from datetime import datetime
from urllib.parse import quote

from .. import export
from ..graph import teams

log = logging.getLogger("powertools.assistant")
BATCH = 20


def find(g, me_id: str, items: list[dict]) -> set[str]:
    """Keys of the conversations with something newer from the user."""
    return _chats(g, me_id, [i for i in items if i["source"] == "teams"]) | _mail(g, [i for i in items if i["source"] == "mail"])


def newer(when: str, than: str) -> bool:
    return bool(when) and export.parse_time(when) > export.parse_time(than)


def _chats(g, me_id: str, items: list[dict]) -> set[str]:
    out = set()
    for n in range(0, len(items), BATCH):
        part = items[n:n + BATCH]
        bodies = g.batch([f"/chats/{quote(i['key'][6:], safe=':@')}/messages?$top=20" for i in part])
        for i, body in zip(part, bodies, strict=True):
            if any(newer(m.get("createdDateTime", ""), i["last"]) and teams.sender_id(m) == me_id or newer(teams.reaction(m, me_id)[0], i["last"])
                   for m in body.get("value", [])):
                out.add(i["key"])
    return out


def _mail(g, items: list[dict]) -> set[str]:
    if not items:
        return set()
    last = {i["key"][5:]: i["last"] for i in items}
    oldest: datetime = min(export.parse_time(t) for t in last.values())
    sent = g.pages("/me/mailFolders/sentitems/messages", {"$filter": f"sentDateTime ge {teams.iso(oldest)}", "$top": "100",
                                                          "$select": "conversationId,sentDateTime,receivedDateTime"}, None, 500)
    return {"mail:" + m["conversationId"] for m in sent
            if m.get("conversationId") in last and newer(m.get("sentDateTime") or m.get("receivedDateTime") or "", last[m["conversationId"]])}
