import { Type } from "@earendil-works/pi-ai";
import { buildSessionContext, formatSkillsForPrompt, getAgentDir, loadSkillsFromDir } from "@earendil-works/pi-coding-agent";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { registerLazyCapabilities } from "./core.mjs";

export default function (pi: ExtensionAPI) {
  registerLazyCapabilities(pi, {
    parameters: Type.Object({}, { additionalProperties: false }),
    buildSessionContext,
    formatSkillsForPrompt,
    loadSkillsFromDir,
    agentDir: getAgentDir(),
  });
}
