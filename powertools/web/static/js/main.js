// Starts the window: every module registers what it shows; the first tab is Today.
import "./tabs.js";
import "./state.js";
import "./dictation.js";
import "./side.js";
import "./settings.js";
import "./diagnostics.js";
import "./graph.js";
import "./ranges.js";
import "./teams.js";
import "./mail.js";
import "./jira.js";
import "./org.js";
import "./jobs.js";
import "./recorded.js";
import "./writing.js";
import "./ai.js";
import "./look.js";
import "./idea.js";
import "./about.js";
import "./install.js";
import "./recap/store.js";
import "./recap/list.js";
import "./recap/body.js";
import "./recap/connect.js";
import "./recap/clicks.js";
import "./recap/matters.js";
import "./recap/meetings.js";
import "./recap/setup.js";
import "./chat.js";
import { $ } from "./core.js";
import { loadRecap } from "./recap/store.js";
import { poll } from "./state.js";

$(".main").classList.add("full");
poll();
loadRecap().catch(() => {});       // asked again by itself
