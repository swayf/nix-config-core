import assert from "node:assert/strict";
import test from "node:test";
import extension from "../extensions/clear.ts";

function register() {
  const commands = {};
  extension({ registerCommand(name, definition) { commands[name] = definition; } });
  return commands.clear;
}

test("/clear starts a new session and awaits completion", async () => {
  const command = register();
  let calls = 0;
  let complete;
  const pending = command.handler("", {
    newSession(...args) {
      calls++;
      assert.deepEqual(args, []);
      return new Promise(resolve => { complete = resolve; });
    },
  });
  assert.equal(calls, 1);
  complete({ cancelled: false });
  await pending;
});

test("/clear respects session cancellation", async () => {
  await register().handler("", {
    async newSession() { return { cancelled: true }; },
  });
});

test("/clear propagates session errors", async () => {
  await assert.rejects(register().handler("", {
    async newSession() { throw new Error("session failed"); },
  }), /session failed/);
});
