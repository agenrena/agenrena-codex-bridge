import assert from "node:assert/strict";
import { chmodSync, existsSync, mkdtempSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { AgentBridgeClient, BridgeService, CodexRunner, StateStore, configure, loadConfig, processStatus, publicConfig, startDaemon, stopDaemon } from "./lib.mjs";

const TEST_PNG_BASE64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=";

function tempEnv() {
  const root = mkdtempSync(join(tmpdir(), "agenrena-codex-plugin-"));
  return { root, env: { ...process.env, AGENRENA_CODEX_BRIDGE_CONFIG_FILE: join(root, "config.json"), AGENRENA_CODEX_BRIDGE_STATE_DIR: join(root, "state") } };
}

test("configure stores only plugin-owned workspace configuration", () => {
  const { root, env } = tempEnv();
  const workspace = join(root, "workspace");
  mkdirSync(workspace);
  const result = configure({ workspace }, env);
  assert.equal(result.workspace, workspace);
  assert.deepEqual(loadConfig(env), { version: 2, workspace });
  assert.equal(publicConfig(env).configured, true);
});

test("state uses opaque routes and persists pending replies", () => {
  const { root } = tempEnv();
  const path = join(root, "state.json");
  const first = new StateStore(path);
  first.load();
  first.record({ inboundMessageID: "m1", route: "opaque.route", threadID: "t1", turnID: "turn1", text: "answer", clientMessageID: "codex-m1" });
  const second = new StateStore(path);
  second.load();
  assert.equal(second.threadID("opaque.route"), "t1");
  assert.equal(second.pending("m1").text, "answer");
  second.markSent("m1");
  assert.equal(second.completed("m1"), true);
});

test("state preserves delivery data but resets threads when the tool surface changes", () => {
  const { root } = tempEnv();
  const path = join(root, "state.json");
  writeFileSync(path, JSON.stringify({
    version: 2,
    sessions: { "opaque.route": "old-thread" },
    pendingReplies: { m1: { inboundMessageID: "m1", text: "pending" } },
    completedMessageIDs: ["m0"],
  }));
  const store = new StateStore(path);
  store.load();
  assert.equal(store.threadID("opaque.route"), "");
  assert.equal(store.pending("m1").text, "pending");
  assert.equal(store.completed("m0"), true);
});

test("state stages generated images durably and removes them after delivery", () => {
  const { root } = tempEnv();
  const path = join(root, "state.json");
  const first = new StateStore(path);
  first.load();
  const reply = first.record({
    inboundMessageID: "m-image", route: "opaque.route", threadID: "t1", turnID: "turn1",
    text: "image reply", clientMessageID: "codex-m-image", media: [{ data: TEST_PNG_BASE64 }],
  });
  assert.equal(reply.media.length, 1);
  assert.equal(readFileSync(reply.media[0].path).toString("base64"), TEST_PNG_BASE64);
  const second = new StateStore(path);
  second.load();
  assert.equal(second.pending("m-image").media[0].path, reply.media[0].path);
  second.markSent("m-image");
  assert.equal(existsSync(reply.media[0].path), false);
});

test("bridge serializes turns by route and sends stable replies", async () => {
  const { root } = tempEnv();
  const bridge = new (class {
    listeners = new Map();
    sent = [];
    on(name, fn) { this.listeners.set(name, fn); }
    async initialize() { return { state: "connected" }; }
    async sendReply(value) { this.sent.push(value); }
  })();
  let active = 0;
  let maxActive = 0;
  const codex = { async runTurn(message) { active += 1; maxActive = Math.max(maxActive, active); await new Promise(resolvePromise => setTimeout(resolvePromise, 10)); active -= 1; return { threadID: `thread-${message.route}`, turnID: `turn-${message.id}`, text: `reply-${message.id}` }; } };
  const service = new BridgeService({ bridge, codex, store: new StateStore(join(root, "state.json")) });
  await service.start();
  service.accept({ id: "m1", route: "same", text: "one", media: [] });
  service.accept({ id: "m2", route: "same", text: "two", media: [] });
  await new Promise(resolvePromise => setTimeout(resolvePromise, 50));
  assert.equal(maxActive, 1);
  assert.deepEqual(bridge.sent.map(value => value.clientMessageID), ["codex-m1", "codex-m2"]);
});

test("bridge completes a handed-off message without sending or queuing a reply", async () => {
  const { root } = tempEnv();
  const handedOffRoutes = [];
  const bridge = new (class {
    listeners = new Map();
    sent = [];
    on(name, fn) { this.listeners.set(name, fn); }
    async initialize() { return { state: "connected" }; }
    async handoff(route) { handedOffRoutes.push(route); return { responder: "human" }; }
    async sendReply(value) { this.sent.push(value); }
  })();
  const codex = {
    async runTurn(message, threadID, handoff) {
      await handoff();
      return { threadID: "thread-handoff", turnID: "turn-handoff", text: "this must be discarded", media: [], handedOff: true };
    },
  };
  const store = new StateStore(join(root, "state.json"));
  const service = new BridgeService({ bridge, codex, store });
  await service.start();
  service.accept({ id: "m-handoff", route: "opaque.handoff", text: "human please", media: [] });
  await new Promise(resolvePromise => setTimeout(resolvePromise, 25));
  assert.deepEqual(handedOffRoutes, ["opaque.handoff"]);
  assert.deepEqual(bridge.sent, []);
  assert.equal(store.pending("m-handoff"), null);
  assert.equal(store.completed("m-handoff"), true);
  assert.equal(store.threadID("opaque.handoff"), "thread-handoff");
});

test("status is stopped when no daemon exists", () => {
  const { env } = tempEnv();
  assert.equal(processStatus(env).running, false);
});

test("version 1 configuration and state are intentionally not migrated", () => {
  const { root, env } = tempEnv();
  writeFileSync(env.AGENRENA_CODEX_BRIDGE_CONFIG_FILE, JSON.stringify({ version: 1, workspace: root }));
  assert.equal(publicConfig(env).configured, false);
  const path = join(root, "old-state.json");
  writeFileSync(path, JSON.stringify({ version: 1, sessions: { old: "thread-old" }, pending_replies: {}, completed_message_ids: [] }));
  const store = new StateStore(path);
  store.load();
  assert.equal(store.threadID("old"), "");
});

test("agent bridge handoff sends the current opaque route", async () => {
  const { root } = tempEnv();
  const fake = join(root, "fake-handoff-bridge");
  writeFileSync(fake, `#!${process.execPath}\n
    import readline from "node:readline";
    const lines = readline.createInterface({ input: process.stdin });
    lines.on("line", raw => {
      const request = JSON.parse(raw);
      if (request.method === "initialize") process.stdout.write(JSON.stringify({ jsonrpc: "2.0", id: request.id, result: { state: "connected" } }) + "\\n");
      if (request.method === "conversations/handoff") process.stdout.write(JSON.stringify({ jsonrpc: "2.0", id: request.id, result: { route: request.params.route, responder: "human" } }) + "\\n");
      if (request.method === "shutdown") process.stdout.write(JSON.stringify({ jsonrpc: "2.0", id: request.id, result: { state: "stopped" } }) + "\\n", () => process.exit(0));
    });
  `);
  chmodSync(fake, 0o755);
  const bridge = new AgentBridgeClient({ agenrenaBin: fake }).start();
  await bridge.initialize();
  assert.deepEqual(await bridge.handoff("opaque.handoff"), { route: "opaque.handoff", responder: "human" });
  await bridge.shutdown();
});

test("Codex runner speaks app-server JSONL and returns the final answer", async () => {
  const { root } = tempEnv();
  const fake = join(root, "fake-app-server.mjs");
  writeFileSync(fake, `
    import readline from "node:readline";
    const lines = readline.createInterface({ input: process.stdin });
    lines.on("line", raw => {
      const request = JSON.parse(raw);
      if (request.method === "initialize") process.stdout.write(JSON.stringify({ id: request.id, result: {} }) + "\\n");
      if (request.method === "thread/start") process.stdout.write(JSON.stringify({ id: request.id, result: { thread: { id: "thread-1" } } }) + "\\n");
      if (request.method === "turn/start") {
        process.stdout.write(JSON.stringify({ id: request.id, result: { turn: { id: "turn-1" } } }) + "\\n");
        process.stdout.write(JSON.stringify({ method: "item/completed", params: { turnId: "turn-1", item: { id: "item-1", type: "agentMessage", phase: "final_answer", text: "final reply" } } }) + "\\n");
        process.stdout.write(JSON.stringify({ method: "turn/completed", params: { turn: { id: "turn-1", status: "completed" } } }) + "\\n");
      }
    });
  `);
  const runner = new CodexRunner({
    codexCommand: [process.execPath, fake], workspace: root, model: "",
    sandboxMode: "read-only", approvalPolicy: "never", timeoutMs: 2000,
  });
  const result = await runner.runTurn({ id: "m1", route: "opaque", sender: { id: "u1" }, text: "hello", media: [], context: [] });
  assert.deepEqual(result, { threadID: "thread-1", turnID: "turn-1", text: "final reply", media: [], handedOff: false });
});

test("Codex runner refreshes trusted sender metadata for every turn in one thread", async () => {
  const { root } = tempEnv();
  const fake = join(root, "fake-auth-app-server.mjs");
  const capture = join(root, "requests.jsonl");
  writeFileSync(fake, `
    import { appendFileSync } from "node:fs";
    import readline from "node:readline";
    const capture = ${JSON.stringify(capture)};
    const lines = readline.createInterface({ input: process.stdin });
    lines.on("line", raw => {
      const request = JSON.parse(raw);
      if (request.method === "initialize") process.stdout.write(JSON.stringify({ id: request.id, result: {} }) + "\\n");
      if (request.method === "thread/start" || request.method === "thread/resume") {
        appendFileSync(capture, JSON.stringify({ method: request.method, params: request.params }) + "\\n");
        process.stdout.write(JSON.stringify({ id: request.id, result: { thread: { id: "shared-thread" } } }) + "\\n");
      }
      if (request.method === "turn/start") {
        appendFileSync(capture, JSON.stringify({ method: request.method, params: request.params }) + "\\n");
        process.stdout.write(JSON.stringify({ id: request.id, result: { turn: { id: "turn-auth" } } }) + "\\n");
        process.stdout.write(JSON.stringify({ method: "item/completed", params: { turnId: "turn-auth", item: { id: "item-auth", type: "agentMessage", phase: "final_answer", text: "ok" } } }) + "\\n");
        process.stdout.write(JSON.stringify({ method: "turn/completed", params: { turn: { id: "turn-auth", status: "completed" } } }) + "\\n");
      }
    });
  `);
  const runner = new CodexRunner({
    codexCommand: [process.execPath, fake], workspace: root, model: "",
    sandboxMode: "read-only", approvalPolicy: "never", timeoutMs: 2000,
  });

  const first = await runner.runTurn({
    id: "m-owner", route: "same-route", sender: { id: "owner-id" }, text: "first", media: [], context: [],
  });
  await runner.runTurn(
    { id: "m-guest", route: "same-route", sender: { id: "guest-id" }, text: "second", media: [], context: [] },
    first.threadID,
  );

  const requests = readFileSync(capture, "utf8").trim().split("\n").map(value => JSON.parse(value));
  assert.equal(requests[0].method, "thread/start");
  assert.match(requests[0].params.developerInstructions, /<agenrena_transport_metadata>{"auth_sender_id":"owner-id"}<\/agenrena_transport_metadata>/);
  assert.deepEqual(requests[1].params.input, [{ type: "text", text: "first", text_elements: [] }]);
  assert.equal(requests[2].method, "thread/resume");
  assert.equal(requests[2].params.threadId, "shared-thread");
  assert.match(requests[2].params.developerInstructions, /<agenrena_transport_metadata>{"auth_sender_id":"guest-id"}<\/agenrena_transport_metadata>/);
  assert.deepEqual(requests[3].params.input, [{ type: "text", text: "second", text_elements: [] }]);
});

test("Codex runner exposes handoff and reports a successful tool call", async () => {
  const { root } = tempEnv();
  const fake = join(root, "fake-handoff-app-server.mjs");
  writeFileSync(fake, `
    import readline from "node:readline";
    const lines = readline.createInterface({ input: process.stdin });
    lines.on("line", raw => {
      const request = JSON.parse(raw);
      if (request.method === "initialize") {
        if (request.params.capabilities.experimentalApi !== true) return process.stdout.write(JSON.stringify({ id: request.id, error: { message: "experimental API disabled" } }) + "\\n");
        process.stdout.write(JSON.stringify({ id: request.id, result: {} }) + "\\n");
      }
      if (request.method === "thread/start") {
        if (request.params.dynamicTools?.[0]?.name !== "handoff_to_human") return process.stdout.write(JSON.stringify({ id: request.id, error: { message: "handoff tool missing" } }) + "\\n");
        process.stdout.write(JSON.stringify({ id: request.id, result: { thread: { id: "thread-handoff" } } }) + "\\n");
      }
      if (request.method === "turn/start") {
        process.stdout.write(JSON.stringify({ id: request.id, result: { turn: { id: "turn-handoff" } } }) + "\\n");
        process.stdout.write(JSON.stringify({ id: 99, method: "item/tool/call", params: { threadId: "thread-handoff", turnId: "turn-handoff", callId: "call-handoff", namespace: "dynamic", tool: "handoff_to_human", arguments: {} } }) + "\\n");
      }
      if (request.id === 99 && !request.method) {
        if (request.result?.success !== true) throw new Error("handoff response was not successful");
        process.stdout.write(JSON.stringify({ method: "item/completed", params: { turnId: "turn-handoff", item: { id: "item-final", type: "agentMessage", phase: "final_answer", text: "handoff complete" } } }) + "\\n");
        process.stdout.write(JSON.stringify({ method: "turn/completed", params: { turn: { id: "turn-handoff", status: "completed" } } }) + "\\n");
      }
    });
  `);
  const runner = new CodexRunner({
    codexCommand: [process.execPath, fake], workspace: root, model: "",
    sandboxMode: "read-only", approvalPolicy: "never", timeoutMs: 2000,
  });
  let calls = 0;
  const result = await runner.runTurn(
    { id: "m-handoff", route: "opaque.handoff", text: "human please", media: [], context: [] },
    "",
    async () => { calls += 1; return { responder: "human" }; },
  );
  assert.equal(calls, 1);
  assert.deepEqual(result, {
    threadID: "thread-handoff", turnID: "turn-handoff", text: "handoff complete", media: [], handedOff: true,
  });
});

test("Codex runner returns completed image generation output", async () => {
  const { root } = tempEnv();
  const fake = join(root, "fake-image-app-server.mjs");
  writeFileSync(fake, `
    import readline from "node:readline";
    const lines = readline.createInterface({ input: process.stdin });
    lines.on("line", raw => {
      const request = JSON.parse(raw);
      if (request.method === "initialize") process.stdout.write(JSON.stringify({ id: request.id, result: {} }) + "\\n");
      if (request.method === "thread/start") process.stdout.write(JSON.stringify({ id: request.id, result: { thread: { id: "thread-image" } } }) + "\\n");
      if (request.method === "turn/start") {
        process.stdout.write(JSON.stringify({ id: request.id, result: { turn: { id: "turn-image" } } }) + "\\n");
        process.stdout.write(JSON.stringify({ method: "item/completed", params: { turnId: "turn-image", item: { id: "image-1", type: "imageGeneration", status: "completed", revisedPrompt: null, result: "${TEST_PNG_BASE64}" } } }) + "\\n");
        process.stdout.write(JSON.stringify({ method: "turn/completed", params: { turn: { id: "turn-image", status: "completed" } } }) + "\\n");
      }
    });
  `);
  const runner = new CodexRunner({
    codexCommand: [process.execPath, fake], workspace: root, model: "",
    sandboxMode: "read-only", approvalPolicy: "never", timeoutMs: 2000,
  });
  const result = await runner.runTurn({ id: "m-image", route: "opaque", text: "make an image", media: [], context: [] });
  assert.deepEqual(result, {
    threadID: "thread-image", turnID: "turn-image", text: "", media: [{ data: TEST_PNG_BASE64 }], handedOff: false,
  });
});

test("Codex runner collects and deduplicates images from tool call output", async () => {
  const { root } = tempEnv();
  const fake = join(root, "fake-tool-image-app-server.mjs");
  writeFileSync(fake, `
    import readline from "node:readline";
    const imageURL = "data:image/png;base64,${TEST_PNG_BASE64}";
    const lines = readline.createInterface({ input: process.stdin });
    lines.on("line", raw => {
      const request = JSON.parse(raw);
      if (request.method === "initialize") process.stdout.write(JSON.stringify({ id: request.id, result: {} }) + "\\n");
      if (request.method === "thread/start") process.stdout.write(JSON.stringify({ id: request.id, result: { thread: { id: "thread-tool-image" } } }) + "\\n");
      if (request.method === "turn/start") {
        process.stdout.write(JSON.stringify({ id: request.id, result: { turn: { id: "turn-tool-image" } } }) + "\\n");
        process.stdout.write(JSON.stringify({ method: "rawResponseItem/completed", params: { threadId: "thread-tool-image", turnId: "turn-tool-image", item: { type: "function_call_output", call_id: "exec-1", output: [{ type: "input_image", image_url: imageURL }] } } }) + "\\n");
        process.stdout.write(JSON.stringify({ method: "rawResponseItem/completed", params: { threadId: "thread-tool-image", turnId: "turn-tool-image", item: { type: "custom_tool_call_output", call_id: "wait-1", output: [{ type: "input_image", image_url: imageURL }] } } }) + "\\n");
        process.stdout.write(JSON.stringify({ method: "item/completed", params: { turnId: "turn-tool-image", item: { id: "item-1", type: "agentMessage", phase: "final_answer", text: "tool image reply" } } }) + "\\n");
        process.stdout.write(JSON.stringify({ method: "turn/completed", params: { turn: { id: "turn-tool-image", status: "completed" } } }) + "\\n");
      }
    });
  `);
  const runner = new CodexRunner({
    codexCommand: [process.execPath, fake], workspace: root, model: "",
    sandboxMode: "read-only", approvalPolicy: "never", timeoutMs: 2000,
  });
  const result = await runner.runTurn({ id: "m-tool-image", route: "opaque", text: "make an image", media: [], context: [] });
  assert.deepEqual(result, {
    threadID: "thread-tool-image", turnID: "turn-tool-image", text: "tool image reply",
    media: [{ data: `data:image/png;base64,${TEST_PNG_BASE64}` }], handedOff: false,
  });
});

test("supervisor runs the complete generic-bridge to Codex reply path", async () => {
  const { root, env } = tempEnv();
  const workspace = join(root, "workspace");
  mkdirSync(workspace);
  configure({ workspace }, env);
  const fakeBridge = join(root, "fake-agenrena");
  const replyFile = join(root, "reply.json");
  writeFileSync(fakeBridge, `#!${process.execPath}\n
    import { readFileSync, writeFileSync } from "node:fs";
    import readline from "node:readline";
    const lines = readline.createInterface({ input: process.stdin });
    lines.on("line", raw => {
      const request = JSON.parse(raw);
      if (request.method === "initialize") {
        process.stdout.write(JSON.stringify({ jsonrpc: "2.0", id: request.id, result: { protocolVersion: 1, state: "connected", serverInfo: { name: "agenrena-agent-bridge", version: "test" }, capabilities: { inboundMedia: true, outboundMedia: true, messageTypes: ["text"] } } }) + "\\n");
        setTimeout(() => process.stdout.write(JSON.stringify({ jsonrpc: "2.0", method: "messages/received", params: { id: "message-1", route: "opaque-route", messageType: "text", sender: { id: "user-1" }, text: "hello", media: [], context: [] } }) + "\\n"), 10);
      }
      if (request.method === "messages/send") {
        const mediaData = (request.params.media || []).map(value => readFileSync(value.path).toString("base64"));
        writeFileSync(process.env.FAKE_REPLY_FILE, JSON.stringify({ params: request.params, mediaData }));
        process.stdout.write(JSON.stringify({ jsonrpc: "2.0", id: request.id, result: { messageId: "reply-1", clientMessageId: request.params.clientMessageId } }) + "\\n");
      }
      if (request.method === "shutdown") process.stdout.write(JSON.stringify({ jsonrpc: "2.0", id: request.id, result: { state: "stopped" } }) + "\\n", () => process.exit(0));
    });
  `);
  chmodSync(fakeBridge, 0o755);
  const fakeCodex = join(root, "fake-codex");
  writeFileSync(fakeCodex, `#!${process.execPath}\n
    import readline from "node:readline";
    const lines = readline.createInterface({ input: process.stdin });
    lines.on("line", raw => {
      const request = JSON.parse(raw);
      if (request.method === "initialize") process.stdout.write(JSON.stringify({ id: request.id, result: {} }) + "\\n");
      if (request.method === "thread/start") process.stdout.write(JSON.stringify({ id: request.id, result: { thread: { id: "thread-1" } } }) + "\\n");
      if (request.method === "turn/start") {
        process.stdout.write(JSON.stringify({ id: request.id, result: { turn: { id: "turn-1" } } }) + "\\n");
        process.stdout.write(JSON.stringify({ method: "rawResponseItem/completed", params: { threadId: "thread-1", turnId: "turn-1", item: { type: "function_call_output", call_id: "exec-image", output: [{ type: "input_image", image_url: "data:image/png;base64," + process.env.FAKE_IMAGE_RESULT }] } } }) + "\\n");
        process.stdout.write(JSON.stringify({ method: "item/completed", params: { turnId: "turn-1", item: { id: "item-1", type: "agentMessage", phase: "final_answer", text: "end-to-end reply" } } }) + "\\n");
        process.stdout.write(JSON.stringify({ method: "turn/completed", params: { turn: { id: "turn-1", status: "completed" } } }) + "\\n");
      }
    });
  `);
  chmodSync(fakeCodex, 0o755);
  const runtimeEnv = { ...env, AGENRENA_BIN: fakeBridge, CODEX_BIN: fakeCodex, FAKE_REPLY_FILE: replyFile, FAKE_IMAGE_RESULT: TEST_PNG_BASE64 };
  const daemon = join(import.meta.dirname, "daemon.mjs");
  const started = startDaemon(daemon, runtimeEnv);
  assert.equal(started.running, true);
  assert.equal(started.state, "connected");
  for (let index = 0; index < 40 && !existsSync(replyFile); index += 1) await new Promise(resolvePromise => setTimeout(resolvePromise, 25));
  const delivered = JSON.parse(readFileSync(replyFile, "utf8"));
  assert.deepEqual(delivered, {
    params: {
      route: "opaque-route", replyTo: "message-1", clientMessageId: "codex-message-1",
      text: "end-to-end reply", format: "markdown", media: [{ path: delivered.params.media[0].path }],
    },
    mediaData: [TEST_PNG_BASE64],
  });
  const stopped = stopDaemon(runtimeEnv);
  assert.equal(stopped.running, false);
});
