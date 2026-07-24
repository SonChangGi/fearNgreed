import { readFile } from "node:fs/promises";
import { pathToFileURL } from "node:url";
import path from "node:path";

import { Window } from "happy-dom";

const ROOT = path.resolve(import.meta.dirname, "../..");
const DATA_FILES = new Set(["summary.json", "dashboard.json", "history.json", "strategy-comparison.json", "live-signal.json"]);

function installGlobal(name, value) {
  Object.defineProperty(globalThis, name, { configurable: true, writable: true, value });
}

export async function waitFor(predicate, message, timeoutMs = 90_000) {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    if (predicate()) return;
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
  throw new Error(`Timed out: ${message}`);
}

export async function bootDashboard({ url = "http://fearngreed.test/", storage = {}, dataOverrides = {}, setupWindow = null } = {}) {
  const window = new Window({ url });
  const html = (await readFile(path.join(ROOT, "index.html"), "utf8")).replace(/<script\b[\s\S]*?<\/script>/gi, "");
  window.document.write(html);
  window.document.close();
  Object.entries(storage).forEach(([key, value]) => window.localStorage.setItem(key, value));

  const media = (query) => ({
    matches: false,
    media: query,
    onchange: null,
    addEventListener() {},
    removeEventListener() {},
    addListener() {},
    removeListener() {},
    dispatchEvent() { return false; }
  });
  window.matchMedia = media;
  delete window.IntersectionObserver;
  window.requestAnimationFrame = (callback) => setTimeout(() => callback(Date.now()), 0);
  window.cancelAnimationFrame = (id) => clearTimeout(id);
  [window.HTMLCollection, window.NodeList, window.HTMLTableRowsCollection].filter(Boolean).forEach((Collection) => {
    if (!Collection.prototype[Symbol.iterator]) Collection.prototype[Symbol.iterator] = Array.prototype[Symbol.iterator];
  });
  if (window.HTMLTableSectionElement && !Object.getOwnPropertyDescriptor(window.HTMLTableSectionElement.prototype, "rows")) {
    Object.defineProperty(window.HTMLTableSectionElement.prototype, "rows", {
      configurable: true,
      get() { return this.querySelectorAll(":scope > tr"); }
    });
  }
  if (!window.HTMLElement.prototype.scrollTo) window.HTMLElement.prototype.scrollTo = function scrollTo({ left = 0, top = 0 } = {}) { this.scrollLeft = left; this.scrollTop = top; };
  Object.defineProperty(window.navigator, "clipboard", {
    configurable: true,
    value: { writeText: async (value) => { window.__copiedText = value; } }
  });

  const localFetch = async (input) => {
    const target = new URL(typeof input === "string" ? input : input.url, window.location.href);
    const filename = path.basename(target.pathname);
    if (!DATA_FILES.has(filename)) return new Response("not found", { status: 404 });
    if (Object.hasOwn(dataOverrides, filename)) {
      const override = dataOverrides[filename];
      if (override == null) return new Response("not found", { status: 404 });
      return new Response(typeof override === "string" ? override : JSON.stringify(override), { status: 200, headers: { "content-type": "application/json" } });
    }
    if (filename === "live-signal.json") {
      try {
        const body = await readFile(path.join(ROOT, "data", filename), "utf8");
        return new Response(body, { status: 200, headers: { "content-type": "application/json" } });
      } catch (_) {
        return new Response("not found", { status: 404 });
      }
    }
    const body = await readFile(path.join(ROOT, "data", filename), "utf8");
    return new Response(body, { status: 200, headers: { "content-type": "application/json" } });
  };
  window.fetch = localFetch;
  if (typeof setupWindow === "function") setupWindow(window);

  installGlobal("window", window);
  installGlobal("document", window.document);
  installGlobal("location", window.location);
  installGlobal("history", window.history);
  installGlobal("localStorage", window.localStorage);
  installGlobal("navigator", window.navigator);
  installGlobal("matchMedia", media);
  installGlobal("requestAnimationFrame", window.requestAnimationFrame);
  installGlobal("cancelAnimationFrame", window.cancelAnimationFrame);
  installGlobal("fetch", localFetch);
  installGlobal("ResizeObserver", window.ResizeObserver);
  installGlobal("IntersectionObserver", undefined);
  installGlobal("Event", window.Event);
  installGlobal("PointerEvent", window.PointerEvent);
  installGlobal("HTMLElement", window.HTMLElement);
  installGlobal("Element", window.Element);
  installGlobal("Node", window.Node);

  const moduleUrl = `${pathToFileURL(path.join(ROOT, "assets", "app.js")).href}?dom-test=${Date.now()}-${Math.random()}`;
  await import(moduleUrl);
  await waitFor(
    () => (window.document.querySelector("#signal-settings-status")?.dataset.state === "ok" && window.document.querySelector(".analysis-config")?.getAttribute("aria-busy") === "false") || window.document.querySelector("#status-badge")?.textContent === "unavailable",
    "dashboard initial render"
  );
  if (window.document.querySelector("#status-badge")?.textContent === "unavailable") {
    throw new Error(`Dashboard failed to boot: ${window.document.querySelector("#status-note")?.textContent || "unknown error"}`);
  }
  return window;
}

export function fireInput(window, selector, value) {
  const input = window.document.querySelector(selector);
  input.value = String(value);
  input.dispatchEvent(new window.Event("input", { bubbles: true }));
  return input;
}

export function submit(window, selector) {
  const form = window.document.querySelector(selector);
  form.dispatchEvent(new window.Event("submit", { bubbles: true, cancelable: true }));
  return form;
}

export function click(window, selector) {
  const button = window.document.querySelector(selector);
  button.click();
  return button;
}

export function signature(document, selectors) {
  return selectors.map((selector) => {
    const node = document.querySelector(selector);
    return `${selector}:${node?.getAttribute("aria-label") || ""}:${node?.innerHTML || node?.textContent || ""}`;
  }).join("\n");
}
