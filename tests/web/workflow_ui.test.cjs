const { test } = require("node:test");
const assert = require("node:assert/strict");
const vm = require("node:vm");
const fs = require("node:fs");
const path = require("node:path");
const root = path.join(__dirname, "../../app/web");
function setup(settings = false) {
  const elements = {};
  const element = (id) =>
    (elements[id] ||= {
      style: {},
      dataset: {},
      textContent: "",
      hidden: false,
      disabled: false,
      getBoundingClientRect: () => ({ height: 40 }),
    });
  const frames = [],
    callbacks = [];
  const context = {
    document: {
      getElementById: element,
      documentElement: { clientWidth: 590 },
      body: { classList: { contains: () => settings }, scrollHeight: 2000 },
    },
    BX24: { resizeWindow: (...args) => frames.push(args) },
    requestAnimationFrame: (fn) => callbacks.push(fn),
    addEventListener: () => {},
  };
  context.window = context;
  vm.createContext(context);
  vm.runInContext(fs.readFileSync(path.join(root, "ui.js"), "utf8"), context);
  return {
    context,
    element,
    frames,
    flush: () => {
      while (callbacks.length) callbacks.shift()();
    },
  };
}
test("frame uses content height rather than old viewport and skips duplicate resize", () => {
  const { context, frames, flush, element } = setup();
  context.WorkflowUI.resize();
  context.WorkflowUI.resize();
  flush();
  assert.deepEqual(frames, [[590, 52]]);
  context.WorkflowUI.resize();
  flush();
  assert.equal(frames.length, 1);
  element("widget").getBoundingClientRect = () => ({ height: 112 });
  context.WorkflowUI.resize();
  flush();
  assert.deepEqual(frames[1], [590, 124]);
});
test("paint preserves chosen size and selects contrasting text for bright and dark backgrounds", () => {
  const { context, element } = setup();
  const button = element("launch");
  context.WorkflowUI.paint(button, {
    label: "Проверить госномер",
    color: "#44e900",
    width: 240,
    height: 40,
  });
  assert.equal(button.textContent, "Проверить госномер");
  assert.equal(button.style.color, "#172b3a");
  assert.equal(button.style.width, "240px");
  assert.equal(button.style.height, "40px");
  context.WorkflowUI.paint(button, {
    label: "Проверить",
    color: "#123456",
    width: 240,
    height: 40,
  });
  assert.equal(button.style.color, "#ffffff");
});
test("field keeps request key after connection loss and shows successful launch", async () => {
  const { context, element, flush } = setup();
  let init;
  const requests = [];
  Object.assign(context.BX24, {
    init: (fn) => (init = fn),
    getAuth: () => ({ member_id: "portal", access_token: "test" }),
    placement: {
      info: () => ({
        options: {
          MODE: "view",
          ENTITY_ID: "CRM_DEAL",
          ENTITY_VALUE_ID: "42",
          FIELD_NAME: "UF_CRM_BUTTON",
        },
      }),
    },
  });
  Object.assign(context, {
    crypto: { randomUUID: () => "same-key" },
    location: {},
    confirm: () => true,
    fetch: async (url, options) => {
      if (url === "config")
        return {
          ok: true,
          json: async () => ({
            configured: true,
            admin: true,
            can_launch: true,
            settings: {
              label: "Проверить",
              color: "#2563eb",
              width: 240,
              height: 40,
              confirm: false,
            },
          }),
        };
      requests.push(JSON.parse(options.body));
      if (requests.length === 1) throw new Error("connection lost");
      return {
        ok: true,
        json: async () => ({ status: "started", workflow_id: "workflow-id" }),
      };
    },
  });
  vm.runInContext(
    fs.readFileSync(path.join(root, "field.js"), "utf8"),
    context,
  );
  await init();
  flush();
  assert.equal(element("settings").hidden, false);
  await element("launch").onclick();
  flush();
  assert.equal(element("launch").disabled, false);
  await element("launch").onclick();
  flush();
  assert.equal(requests[0].request_id, requests[1].request_id);
  assert.equal(requests[1].deal_id, 42);
  assert.equal(element("message").dataset.tone, "success");
  assert.equal(element("launch").disabled, true);
});
