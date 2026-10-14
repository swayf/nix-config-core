import { Type } from "@earendil-works/pi-ai";
import { buildSessionContext } from "@earendil-works/pi-coding-agent";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { registerContextHandoff } from "./core.mjs";

const text = (maxLength: number) => Type.String({ minLength: 1, maxLength });

export default function (pi: ExtensionAPI) {
  registerContextHandoff(pi, {
    buildSessionContext,
    parameters: Type.Object({
      task: text(120),
      goal: text(1200),
      completed: text(1600),
      next: text(1200),
      constraints: Type.Array(text(500), { minItems: 1, maxItems: 12 }),
      checks: Type.Array(text(500), { minItems: 1, maxItems: 12 }),
      readFirst: Type.Array(text(300), { minItems: 1, maxItems: 8 }),
      stateFiles: Type.Array(text(300), { minItems: 1, maxItems: 8 }),
      statePersisted: Type.Literal(true),
      nextAuthorized: Type.Literal(true),
    }, { additionalProperties: false }),
  });
}
