import fs from "node:fs";
import fsPromise from "node:fs/promises";
import path from "node:path";

export const storageFileNameList = [
  "boss-cookies.json",
  "boss-local-storage.json",
  "boss-login-status.json"
];

export const ensureRuntimeStorageExist = (storagePath) => {
  if (!fs.existsSync(storagePath)) {
    fs.mkdirSync(storagePath, { recursive: true });
  }
  for (const fileName of storageFileNameList) {
    const filePath = path.join(storagePath, fileName);
    if (!fs.existsSync(filePath)) {
      fs.writeFileSync(filePath, defaultStorageFileContent(fileName), "utf8");
    }
  }
};

export const readStorageFile = (storagePath, fileName, { isJson } = {}) => {
  isJson = isJson ?? true;
  const filePath = path.join(storagePath, fileName);
  if (!fs.existsSync(filePath)) {
    ensureRuntimeStorageExist(storagePath);
  }
  try {
    const content = fs.readFileSync(filePath, "utf8");
    return isJson ? JSON.parse(content) : content;
  } catch {
    fs.existsSync(filePath) && fs.unlinkSync(filePath);
    ensureRuntimeStorageExist(storagePath);
    return isJson ? JSON.parse(defaultStorageFileContent(fileName)) : defaultStorageFileContent(fileName);
  }
};

export const writeStorageFile = async (storagePath, fileName, content, { isJson } = {}) => {
  isJson = isJson ?? true;
  const filePath = path.join(storagePath, fileName);
  const fileContent = isJson ? JSON.stringify(content, null, 2) : content;
  return fsPromise.writeFile(filePath, fileContent, "utf8");
};

const defaultStorageFileContent = (fileName) => {
  if (fileName === "boss-login-status.json") {
    return JSON.stringify({
      status: "idle",
      message: "",
      updated_at: null
    });
  }
  if (fileName === "boss-local-storage.json") {
    return "{}";
  }
  return "[]";
};
