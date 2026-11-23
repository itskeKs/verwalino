// Hands an organization export archive to the import form in a blind browser run: the test browser
// can neither save a download nor fill a file field. Paste this whole file into the browser tool with
// one call appended in the same evaluation (functions declared next to a top-level await do not
// survive into the next evaluation; docs/acceptance/README.md):
//   await capture("archive-852")   before "Daten exportieren"; stays active until the page reloads
//   await stored("archive-852")    after the export: name, type and size of what arrived
//   await deliver("archive-852")   on the users page of the target; then click "Daten importieren"
// It reads no page content and makes no network call; the only storage it uses is the IndexedDB
// store acceptance-handoff/files, which all runs of the origin share: use one key per run.

function handoffKey(key) {
  if (typeof key !== "string" || !key || key.endsWith("-name")) {
    throw new Error('handoff: pass a key such as "archive-852" (a string, not empty, not ending in "-name")');
  }
}

function handoffOpen() { // the store; created by a version upgrade when the database exists without it
  return new Promise((resolve, reject) => {
    const open = (version) => {
      const request = indexedDB.open("acceptance-handoff", version);
      request.onupgradeneeded = () => {
        if (!request.result.objectStoreNames.contains("files")) request.result.createObjectStore("files");
      };
      request.onblocked = () => reject(new Error("handoff: another tab of this origin blocks the IndexedDB upgrade; close it"));
      request.onerror = () => reject(request.error);
      request.onsuccess = () => {
        const db = request.result;
        if (db.objectStoreNames.contains("files")) return resolve(db);
        const next = db.version + 1;
        db.close();
        open(next);
      };
    };
    open();
  });
}

function handoffRun(mode, work) { // work(store) returns a request; resolves with its result once the transaction is done
  return handoffOpen().then((db) => new Promise((resolve, reject) => {
    const tx = db.transaction("files", mode);
    const request = work(tx.objectStore("files"));
    tx.oncomplete = () => { db.close(); resolve(request.result); };
    tx.onabort = () => { db.close(); reject(tx.error || new Error("handoff: IndexedDB transaction aborted")); };
  }));
}

async function handoffRead(key) {
  handoffKey(key);
  const blob = await handoffRun("readonly", (store) => store.get(key));
  const name = await handoffRun("readonly", (store) => store.get(key + "-name"));
  if (!blob || !name) {
    throw new Error(`handoff: nothing stored under "${key}"; call capture("${key}") before "Daten exportieren" in this browser profile and origin, then check stored("${key}")`);
  }
  return { blob, name };
}

async function capture(key) {
  handoffKey(key);
  (await handoffOpen()).close(); // fail now, not after the export
  const active = URL.createObjectURL.handoff;
  if (active) { // a second call only changes the key
    active.key = key;
    return { key, capturing: true, alreadyActive: true };
  }
  const state = { key, blobs: new Map() };
  const createObjectURL = URL.createObjectURL;
  const click = HTMLAnchorElement.prototype.click;
  URL.createObjectURL = function (object) {
    const url = createObjectURL.call(this, object);
    if (object instanceof Blob) state.blobs.set(url, object);
    return url;
  };
  URL.createObjectURL.handoff = state;
  HTMLAnchorElement.prototype.click = function () {
    const blob = this.hasAttribute("download") && state.blobs.get(this.href);
    if (!blob) return click.call(this); // not a download of a blob: untouched
    state.blobs.delete(this.href);
    const { key } = state;
    const name = this.getAttribute("download") || key;
    handoffRun("readwrite", (store) => { store.put(blob, key); return store.put(name, key + "-name"); })
      .catch((error) => console.error("handoff: storing the archive failed", error));
  };
  return { key, capturing: true, alreadyActive: false };
}

async function stored(key) {
  const { blob, name } = await handoffRead(key);
  return { key, name, type: blob.type, size: blob.size };
}

async function deliver(key) {
  const { blob, name } = await handoffRead(key);
  const inputs = document.querySelectorAll('input[type="file"]');
  if (inputs.length !== 1) {
    throw new Error(inputs.length
      ? `handoff: ${inputs.length} file inputs on this page, expected only the one of the import block`
      : "handoff: no file input on this page yet; wait for the import block of the users page to load, then call deliver again");
  }
  const transfer = new DataTransfer();
  transfer.items.add(new File([blob], name, { type: blob.type }));
  inputs[0].files = transfer.files;
  inputs[0].dispatchEvent(new Event("change", { bubbles: true }));
  const file = inputs[0].files[0];
  return { key, name: file.name, type: file.type, size: file.size };
}
