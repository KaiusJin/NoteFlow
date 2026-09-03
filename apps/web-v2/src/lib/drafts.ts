const DATABASE_NAME = "noteflow-offline";
const DATABASE_VERSION = 1;
const DRAFT_STORE = "drafts";

export interface LocalDraft {
  key: string;
  markdown: string;
  serverVersion: string | null;
  updatedAt: string;
}

function openDatabase(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DATABASE_NAME, DATABASE_VERSION);
    request.onupgradeneeded = () => {
      if (!request.result.objectStoreNames.contains(DRAFT_STORE)) {
        request.result.createObjectStore(DRAFT_STORE, { keyPath: "key" });
      }
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

export async function saveDraft(draft: LocalDraft): Promise<void> {
  const database = await openDatabase();
  await new Promise<void>((resolve, reject) => {
    const transaction = database.transaction(DRAFT_STORE, "readwrite");
    transaction.objectStore(DRAFT_STORE).put(draft);
    transaction.oncomplete = () => resolve();
    transaction.onerror = () => reject(transaction.error);
  });
  database.close();
}

export async function loadDraft(key: string): Promise<LocalDraft | null> {
  const database = await openDatabase();
  const draft = await new Promise<LocalDraft | null>((resolve, reject) => {
    const request = database.transaction(DRAFT_STORE).objectStore(DRAFT_STORE).get(key);
    request.onsuccess = () => resolve((request.result as LocalDraft | undefined) ?? null);
    request.onerror = () => reject(request.error);
  });
  database.close();
  return draft;
}

export async function deleteDraft(key: string): Promise<void> {
  const database = await openDatabase();
  await new Promise<void>((resolve, reject) => {
    const transaction = database.transaction(DRAFT_STORE, "readwrite");
    transaction.objectStore(DRAFT_STORE).delete(key);
    transaction.oncomplete = () => resolve();
    transaction.onerror = () => reject(transaction.error);
  });
  database.close();
}
