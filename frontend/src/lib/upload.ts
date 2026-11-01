import { api, command, ApiError } from "./api";
import type { UploadCreated, UploadState } from "../types";

export function validateFile(file: File, avatar = false) {
  const limit = avatar ? 5_000_000 : 25_000_000;
  if (!file.size || file.size > limit)
    throw new Error(`Размер файла: от 1 байта до ${avatar ? 5 : 25} МБ.`);
  if (
    avatar
      ? !["image/jpeg", "image/png", "image/webp"].includes(file.type)
      : file.type !== "application/pdf"
  )
    throw new Error(
      avatar ? "Выберите JPEG, PNG или WebP." : "Выберите PDF-файл.",
    );
}
export async function checksum(file: File) {
  const digest = new Uint8Array(
    await crypto.subtle.digest("SHA-256", await file.arrayBuffer()),
  );
  return btoa(String.fromCharCode(...digest));
}
function pause(ms: number, signal: AbortSignal) {
  return new Promise<void>((resolve, reject) => {
    signal.throwIfAborted();
    const stop = () => {
      clearTimeout(timer);
      reject(new DOMException("Cancelled", "AbortError"));
    };
    const timer = setTimeout(() => {
      signal.removeEventListener("abort", stop);
      resolve();
    }, ms);
    signal.addEventListener("abort", stop, { once: true });
  });
}
export async function uploadFile(
  file: File,
  purpose: "paper_pdf" | "project_pdf" | "avatar",
  resourceId: string,
  signal: AbortSignal,
  progress: (text: string) => void,
  replacesFileId?: string | null,
) {
  validateFile(file, purpose === "avatar");
  progress("Подготовка файла…");
  const hash = await checksum(file);
  signal.throwIfAborted();
  const upload = await command<UploadCreated>("/media/uploads", {
    purpose,
    resource_id: resourceId,
    filename: file.name,
    content_type: file.type,
    size_bytes: file.size,
    checksum_sha256: hash,
    replaces_file_id: replacesFileId ?? null,
  });
  try {
    progress("Загрузка в хранилище…");
    const response = await fetch(upload.upload_url, {
      method: upload.upload_method,
      headers: upload.required_headers,
      body: file,
      signal,
    });
    if (!response.ok)
      throw new ApiError(
        response.status,
        "UPLOAD_FAILED",
        "Не удалось загрузить файл в хранилище.",
      );
    await command(`/media/uploads/${upload.upload_id}/complete`);
    progress("Проверка безопасности файла…");
    const end = Date.now() + 180_000;
    while (Date.now() < end) {
      signal.throwIfAborted();
      const state = await api<UploadState>(
        `/media/uploads/${upload.upload_id}`,
        { signal },
      );
      if (state.status === "ready") {
        progress("Файл готов.");
        return upload;
      }
      if (["rejected", "expired", "cancelled"].includes(state.status))
        throw new Error(
          state.status === "rejected"
            ? `Файл отклонён проверкой (${state.rejection_code ?? "неизвестная причина"}).`
            : "Загрузка истекла или отменена.",
        );
      await pause(1500, signal);
    }
    throw new Error(
      "Проверка файла заняла слишком много времени. Загрузите его повторно позже.",
    );
  } catch (error) {
    await command(
      `/media/uploads/${upload.upload_id}`,
      undefined,
      "DELETE",
    ).catch(() => undefined);
    throw error;
  }
}
