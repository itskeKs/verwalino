"use strict";
// Contracts of scripts/acceptance/handoff-export-archive.js. The page script is run exactly as it is
// pasted (vm) against a minimal fake of IndexedDB, URL, anchor, document and DataTransfer.
// node:test only: no browser, no database, no dependency.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "acceptance", "handoff-export-archive.js"), "utf8");
const BYTES = Buffer.from("not really a tar.gz, but enough bytes to count");
const TYPE = "application/gzip";
const NAME = "verwalino-organisation.tar.gz";

class FakeIndexedDB { // databases: name -> { version, stores: Map(store -> Map(key -> value)) }
  constructor() { this.databases = new Map(); this.connections = 0; }
  open(name, version) {
    const request = { result: null, error: null };
    queueMicrotask(() => {
      let record = this.databases.get(name);
      if (record && version !== undefined && version < record.version) {
        request.error = new Error("VersionError");
        return request.onerror();
      }
      const upgrading = !record || (version !== undefined && version > record.version);
      if (!record) { record = { version: version ?? 1, stores: new Map() }; this.databases.set(name, record); }
      else if (upgrading) record.version = version;
      this.connections += 1;
      request.result = {
        version: record.version,
        objectStoreNames: { contains: (store) => record.stores.has(store) },
        createObjectStore: (store) => record.stores.set(store, new Map()),
        close: () => { this.connections -= 1; },
        transaction: (store, mode) => this.transaction(record, store, mode),
      };
      if (upgrading) request.onupgradeneeded();
      request.onsuccess();
    });
    return request;
  }
  transaction(record, name, mode) {
    const store = record.stores.get(name);
    if (!store) throw new Error("NotFoundError");
    const tx = {
      error: null,
      objectStore: () => ({
        get: (key) => ({ result: store.get(key) }),
        delete: (key) => ({ result: store.delete(key) }),
        clear: () => ({ result: store.clear() }),
        put: (value, key) => {
          assert.equal(mode, "readwrite");
          store.set(key, value);
          return { result: key };
        },
      }),
    };
    queueMicrotask(() => tx.oncomplete());
    return tx;
  }
  files() { return this.databases.get("acceptance-handoff")?.stores.get("files"); }
}

class FakeAnchor {
  constructor() { this.attributes = new Map(); this.href = ""; this.clicked = 0; }
  set download(value) { this.attributes.set("download", value); }
  hasAttribute(name) { return this.attributes.has(name); }
  getAttribute(name) { return this.attributes.get(name) ?? null; }
  click() { this.clicked += 1; } // the original click: the browser would save the file
}

class FakeFileInput {
  constructor() { this.files = null; this.events = []; }
  dispatchEvent(event) { this.events.push(event); }
}

class FakeDataTransfer {
  constructor() { this.list = []; this.items = { add: (file) => this.list.push(file) }; }
  get files() { return this.list; }
}

// One browser page. Pages that share an `indexedDB` are pages of one origin, whose memory is separate.
function page({ indexedDB = new FakeIndexedDB(), inputs = [] } = {}) {
  const errors = [];
  const urls = { created: 0 };
  const context = {
    indexedDB, Blob, File, Event, inputs, errors, urls,
    console: { error: (...args) => errors.push(args) },
    URL: { createObjectURL() { urls.created += 1; return `blob:fake/${urls.created}`; }, revokeObjectURL() {} },
    HTMLAnchorElement: class extends FakeAnchor {},
    DataTransfer: FakeDataTransfer,
    document: { querySelectorAll: (selector) => (selector === 'input[type="file"]' ? inputs : []) },
  };
  vm.createContext(context);
  vm.runInContext(source, context);
  return context;
}

// What OrganizationDataCard does on "Daten exportieren": blob URL, anchor with `download`, click, revoke.
function exportArchive(context, { bytes = BYTES, type = TYPE, name = NAME } = {}) {
  const url = context.URL.createObjectURL(new Blob([bytes], { type }));
  const anchor = new context.HTMLAnchorElement();
  anchor.href = url;
  anchor.download = name;
  anchor.click();
  context.URL.revokeObjectURL(url);
  return anchor;
}

const plain = (value) => JSON.parse(JSON.stringify(value)); // results come from the vm realm, deepEqual compares prototypes
const settle = () => new Promise((resolve) => setTimeout(resolve, 0)); // the fake settles in microtasks

function seed(indexedDB, entries, version = 1) {
  indexedDB.databases.set("acceptance-handoff", { version, stores: new Map([["files", new Map(Object.entries(entries))]]) });
}

test("deliver refuses without a stored archive and leaves the input alone", async () => {
  const input = new FakeFileInput();
  const indexedDB = new FakeIndexedDB();
  assert.equal(indexedDB.files(), undefined);
  await assert.rejects(page({ indexedDB, inputs: [input] }).deliver("archive-1"), /nothing stored under "archive-1"/);

  seed(indexedDB, { "archive-2": new Blob([BYTES]), "archive-2-name": NAME, "archive-1": new Blob([BYTES]) });
  await assert.rejects(page({ indexedDB, inputs: [input] }).deliver("archive-1"), /nothing stored under "archive-1"/);
  assert.equal(input.files, null);
  assert.deepEqual(input.events, []);
  assert.equal(indexedDB.connections, 0);
});

test("deliver refuses without a file input, keeps the archive and works when called again", async () => {
  const indexedDB = new FakeIndexedDB();
  seed(indexedDB, { "archive-1": new Blob([BYTES], { type: TYPE }), "archive-1-name": NAME });
  const context = page({ indexedDB });
  await assert.rejects(context.deliver("archive-1"), /no file input on this page yet/);
  assert.deepEqual([...indexedDB.files().keys()], ["archive-1", "archive-1-name"]);

  const input = new FakeFileInput();
  context.inputs.push(input); // the card with the import block has loaded
  assert.deepEqual(plain(await context.deliver("archive-1")), { key: "archive-1", name: NAME, type: TYPE, size: BYTES.length });
  assert.equal(input.events.length, 1);
});

test("deliver refuses a page with several file inputs", async () => {
  const indexedDB = new FakeIndexedDB();
  seed(indexedDB, { "archive-1": new Blob([BYTES]), "archive-1-name": NAME });
  const inputs = [new FakeFileInput(), new FakeFileInput()];
  await assert.rejects(page({ indexedDB, inputs }).deliver("archive-1"), /2 file inputs on this page/);
  assert.deepEqual(inputs.map((input) => [input.files, input.events]), [[null, []], [null, []]]);
});

test("every function refuses a missing or unusable key", async () => {
  const context = page({ inputs: [new FakeFileInput()] });
  for (const key of [undefined, "", 7, "archive-1-name"]) {
    for (const call of ["capture", "stored", "deliver"]) {
      await assert.rejects(context[call](key), /pass a key such as "archive-852"/, `${call}(${String(key)})`);
    }
  }
  assert.equal(context.URL.createObjectURL.handoff, undefined);
});

test("capture then deliver in a fresh page hands over name, type, size and bytes", async () => {
  const indexedDB = new FakeIndexedDB();
  const exporting = page({ indexedDB });
  assert.deepEqual(plain(await exporting.capture("archive-852")), { key: "archive-852", capturing: true, alreadyActive: false });

  const anchor = exportArchive(exporting);
  assert.equal(anchor.clicked, 0, "the download click is swallowed");
  assert.equal(exporting.urls.created, 1);
  await settle();
  const facts = { key: "archive-852", name: NAME, type: TYPE, size: BYTES.length };
  assert.deepEqual(plain(await exporting.stored("archive-852")), facts);

  // another session: sign-out, sign-in, a fresh page of the same origin that never called capture()
  const input = new FakeFileInput();
  const importing = page({ indexedDB, inputs: [input] });
  assert.equal(importing.URL.createObjectURL.handoff, undefined);
  assert.deepEqual(plain(await importing.deliver("archive-852")), facts);
  const [file] = input.files;
  assert.ok(file instanceof File);
  assert.deepEqual([file.name, file.type, file.size], [NAME, TYPE, BYTES.length]);
  assert.deepEqual(Buffer.from(await file.arrayBuffer()), BYTES);
  assert.equal(input.events.length, 1);
  assert.equal(input.events[0].type, "change");
  assert.equal(input.events[0].bubbles, true);
  assert.deepEqual(exporting.errors.concat(importing.errors), []);
  assert.equal(indexedDB.connections, 0, "every connection is closed");
});

test("a second capture does not wrap twice and only switches the key", async () => {
  const indexedDB = new FakeIndexedDB();
  const context = page({ indexedDB });
  await context.capture("first");
  const [createObjectURL, click] = [context.URL.createObjectURL, context.HTMLAnchorElement.prototype.click];

  assert.deepEqual(plain(await context.capture("second")), { key: "second", capturing: true, alreadyActive: true });
  assert.equal(context.URL.createObjectURL, createObjectURL);
  assert.equal(context.HTMLAnchorElement.prototype.click, click);

  const anchor = exportArchive(context);
  await settle();
  assert.equal(context.urls.created, 1, "the original createObjectURL runs once per call");
  assert.equal(anchor.clicked, 0);
  assert.deepEqual([...indexedDB.files().keys()], ["second", "second-name"]);
});

test("other keys survive, in an existing store and in a database without one", async () => {
  const indexedDB = new FakeIndexedDB();
  const earlier = { "archive-851": new Blob(["earlier run"]), "archive-851-name": "earlier.tar.gz", unrelated: "kept" };
  seed(indexedDB, earlier, 3);
  const context = page({ indexedDB });
  await context.capture("archive-853");
  exportArchive(context);
  await settle();
  assert.deepEqual([...indexedDB.files().keys()].sort(), ["archive-851", "archive-851-name", "archive-853", "archive-853-name", "unrelated"]);
  for (const [key, value] of Object.entries(earlier)) assert.equal(indexedDB.files().get(key), value);
  assert.equal(indexedDB.databases.get("acceptance-handoff").version, 3, "an existing store is not upgraded");

  const bare = new FakeIndexedDB();
  bare.databases.set("acceptance-handoff", { version: 4, stores: new Map() });
  const other = page({ indexedDB: bare });
  await other.capture("archive-854");
  exportArchive(other);
  await settle();
  assert.equal(bare.databases.get("acceptance-handoff").version, 5, "the missing store comes with a version upgrade");
  assert.deepEqual([...bare.files().keys()], ["archive-854", "archive-854-name"]);
  assert.deepEqual(context.errors.concat(other.errors), []);
  assert.equal(indexedDB.connections + bare.connections, 0);
});

test("clicks that are not the download of a captured blob are left alone", async () => {
  const indexedDB = new FakeIndexedDB();
  const context = page({ indexedDB });
  await context.capture("archive-1");
  const url = context.URL.createObjectURL(new Blob(["preview"]));

  const noDownload = new context.HTMLAnchorElement();
  noDownload.href = url;
  noDownload.click();
  const plainLink = new context.HTMLAnchorElement(); // for example the CSV template link
  plainLink.href = "/api/properties/import/template.csv";
  plainLink.download = "master-data-template.csv";
  plainLink.click();
  await settle();
  assert.deepEqual([noDownload.clicked, plainLink.clicked], [1, 1]);
  assert.equal(indexedDB.files().size, 0);
});

test("pasting the script again is harmless", async () => {
  const context = page();
  await context.capture("archive-1");
  vm.runInContext(source, context);
  assert.deepEqual(plain(await context.capture("archive-2")), { key: "archive-2", capturing: true, alreadyActive: true });
});

test("the script stays a hand-off: no network, no cookies, no other storage, no clicks, one DOM query", () => {
  assert.doesNotMatch(source, /\b(fetch|XMLHttpRequest|WebSocket|EventSource|sendBeacon|localStorage|sessionStorage|eval|importScripts)\b/);
  assert.doesNotMatch(source, /\bcookie\b|\.click\(|\.submit\(|\bimport\s*\(/);
  assert.doesNotMatch(source, /^(const|let|var|class|import|export) /m, "only function declarations, so a second paste cannot fail");
  const documentUses = source.match(/\bdocument\b[^\n]*/g) ?? [];
  assert.deepEqual(documentUses.map((use) => use.replace(/\s+/g, " ")).filter((use) => !use.startsWith("document.querySelectorAll('input[type=\"file\"]')")), []);
  assert.ok(source.split("\n").length <= 120, "short enough for a harness that refuses page scripts doing more than the hand-off");
});

test("the contracts run in make test and in CI, and the script is documented where sessions look", () => {
  const read = (...parts) => fs.readFileSync(path.join(__dirname, "..", ...parts), "utf8");
  const makefile = read("Makefile");
  const testLine = makefile.split("\n").find((line) => line.startsWith("test:"));
  assert.ok(testLine.split("##")[0].split(/\s+/).includes("test-acceptance-handoff"));
  assert.match(makefile, /^test-acceptance-handoff:.*\n\tnode --test scripts\/test-acceptance-handoff\.cjs$/m);
  assert.match(read(".github", "workflows", "tests-on-pr.yaml"), /run: make test-acceptance-handoff/);
  for (const page of ["AGENTS.md", "CONTRIBUTING.md"]) assert.match(read(page), /scripts\/acceptance\/handoff-export-archive\.js/, page);
  assert.match(read("docs", "acceptance", "README.md"), /handoff-export-archive\.js/);
});
