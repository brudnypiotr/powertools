// Today, the right side: one of the brief, an item, Ask, or the step for the token.
import { $ } from "../core.js";
import { bodyAsk } from "./ask.js";
import { bodyBrief } from "./brief.js";
import { bodyConnect } from "./connect.js";
import { bodyItem } from "./item.js";
import { find, renderToday } from "./list.js";
import { V, select } from "./store.js";
import { saveDraft } from "./draft.js";

const BODIES = { brief: bodyBrief, item: bodyItem, ask: bodyAsk, connect: bodyConnect };
function renderBody(force) {
  if (V.mode === "item" && !find(V.sel)) V.mode = "brief";
  return (BODIES[V.mode] || bodyBrief)(force);
}
async function showItem(id) {
  await saveDraft().catch(() => {});
  select(id);
  renderToday();
  $("#today-body").scrollTop = 0;
  renderBody(true);
}

export { renderBody, showItem };
