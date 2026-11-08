import { beforeEach, afterEach, expect, it, vi } from "vitest";
import { webcrypto } from "node:crypto";
import { checksum, uploadFile, validateFile } from "./upload";
import { ApiError, session } from "./api";
const file = (
  name = "paper.pdf",
  type = "application/pdf",
  content = "%PDF-1.4\ntest",
) => {
  const value = new File([content], name, { type });
  Object.defineProperty(value, "arrayBuffer", {
    value: async () => new TextEncoder().encode(content).buffer,
  });
  return value;
};
const json = (value: unknown) => new Response(JSON.stringify(value));
beforeEach(() => {
  session.clear();
  vi.stubGlobal("crypto", webcrypto);
  vi.stubGlobal("fetch", vi.fn());
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});
it("computes the SHA256 digest in S3 base64 format", async () => {
  expect(await checksum(file("x.pdf", "application/pdf", "abc"))).toBe(
    "ungWv48Bz+pBQUDeXa4iI7ADYaOWF3qctBD/YfIAFa0=",
  );
});
it("accepts a PDF up to 25 MB, but not an empty or oversized file", () => {
  expect(() => validateFile(file())).not.toThrow();
  const empty = new File([], "empty.pdf", { type: "application/pdf" });
  expect(() => validateFile(empty)).toThrow("25 МБ");
  const large = file();
  Object.defineProperty(large, "size", {
    value: 25_000_001,
    configurable: true,
  });
  expect(() => validateFile(large)).toThrow("25 МБ");
  Object.defineProperty(large, "size", { value: 25_000_000 });
  expect(() => validateFile(large)).not.toThrow();
});
it.each(["image/jpeg", "image/png", "image/webp"])(
  "accepts avatar %s",
  (type) => {
    expect(() => validateFile(file("avatar", type), true)).not.toThrow();
  },
);
it("rejects a non-PDF, an invalid avatar, and an oversized avatar", () => {
  expect(() => validateFile(file("text.txt", "text/plain"))).toThrow("PDF");
  expect(() => validateFile(file(), true)).toThrow("JPEG");
  const large = file("photo.png", "image/png");
  Object.defineProperty(large, "size", { value: 5_000_001 });
  expect(() => validateFile(large, true)).toThrow("5 МБ");
});
it("uploads directly to S3 without an API bearer and binds only a ready file", async () => {
  const created = {
    upload_id: "upload",
    file_id: "file",
    upload_method: "PUT",
    upload_url: "https://s3.example.test/signed",
    required_headers: {
      "Content-Type": "application/pdf",
      "x-amz-checksum-sha256": "checksum",
    },
    reservation_id: "reservation",
  };
  vi.mocked(fetch)
    .mockResolvedValueOnce(json(created))
    .mockResolvedValueOnce(new Response(""))
    .mockResolvedValueOnce(json({}))
    .mockResolvedValueOnce(json({ status: "ready" }));
  const progress = vi.fn();
  expect(
    await uploadFile(
      file(),
      "paper_pdf",
      "paper",
      new AbortController().signal,
      progress,
      "old-file",
    ),
  ).toEqual(created);
  expect(vi.mocked(fetch).mock.calls[1]).toEqual([
    "https://s3.example.test/signed",
    expect.objectContaining({
      method: "PUT",
      headers: created.required_headers,
    }),
  ]);
  expect(vi.mocked(fetch).mock.calls[1][1]?.headers).not.toHaveProperty(
    "Authorization",
  );
  expect(
    JSON.parse(String(vi.mocked(fetch).mock.calls[0][1]?.body)),
  ).toMatchObject({ replaces_file_id: "old-file" });
  expect(progress).toHaveBeenLastCalledWith("Файл готов.");
});
it.each(["rejected", "expired", "cancelled"])(
  "releases the reservation when scanning ends with %s",
  async (status) => {
    vi.mocked(fetch)
      .mockResolvedValueOnce(
        json({
          upload_id: "u",
          upload_method: "PUT",
          upload_url: "/s3",
          required_headers: {},
        }),
      )
      .mockResolvedValueOnce(new Response(""))
      .mockResolvedValueOnce(json({}))
      .mockResolvedValueOnce(json({ status, rejection_code: "VIRUS_FOUND" }))
      .mockResolvedValueOnce(new Response(undefined, { status: 204 }));
    await expect(
      uploadFile(
        file(),
        "project_pdf",
        "p",
        new AbortController().signal,
        vi.fn(),
      ),
    ).rejects.toThrow(status === "rejected" ? "VIRUS_FOUND" : "истекла");
    expect(vi.mocked(fetch).mock.calls.at(-1)?.[1]?.method).toBe("DELETE");
  },
);
it("reports a failed S3 PUT and preserves the original error if cancellation fails", async () => {
  vi.mocked(fetch)
    .mockResolvedValueOnce(
      json({
        upload_id: "u",
        upload_method: "PUT",
        upload_url: "/s3",
        required_headers: {},
      }),
    )
    .mockResolvedValueOnce(new Response("", { status: 403 }))
    .mockRejectedValueOnce(new Error("cleanup unavailable"));
  await expect(
    uploadFile(file(), "avatar", "p", new AbortController().signal, vi.fn()),
  ).rejects.toThrow("JPEG");
  await expect(
    uploadFile(file(), "paper_pdf", "p", new AbortController().signal, vi.fn()),
  ).rejects.toBeInstanceOf(ApiError);
});
it("polls scanning and supports cancelling during the wait", async () => {
  vi.useFakeTimers();
  const controller = new AbortController();
  vi.mocked(fetch)
    .mockResolvedValueOnce(
      json({
        upload_id: "u",
        upload_method: "PUT",
        upload_url: "/s3",
        required_headers: {},
      }),
    )
    .mockResolvedValueOnce(new Response(""))
    .mockResolvedValueOnce(json({}))
    .mockResolvedValueOnce(json({ status: "scanning" }))
    .mockResolvedValueOnce(new Response(undefined, { status: 204 }));
  const promise = uploadFile(
    file(),
    "paper_pdf",
    "p",
    controller.signal,
    () => undefined,
  );
  const assertion = expect(promise).rejects.toMatchObject({
    name: "AbortError",
  });
  await vi.waitFor(() => expect(fetch).toHaveBeenCalledTimes(4));
  controller.abort();
  await assertion;
});
it("stops after the scan timeout instead of polling forever", async () => {
  vi.useFakeTimers();
  vi.mocked(fetch)
    .mockResolvedValueOnce(
      json({
        upload_id: "u",
        upload_method: "PUT",
        upload_url: "/s3",
        required_headers: {},
      }),
    )
    .mockResolvedValueOnce(new Response(""))
    .mockResolvedValueOnce(json({}))
    .mockImplementation(async (_path, options) =>
      options?.method === "DELETE"
        ? new Response(undefined, { status: 204 })
        : json({ status: "scanning" }),
    );
  const promise = uploadFile(
    file(),
    "paper_pdf",
    "p",
    new AbortController().signal,
    vi.fn(),
  );
  const assertion = expect(promise).rejects.toThrow("слишком много времени");
  await vi.waitFor(() => expect(fetch).toHaveBeenCalledTimes(4));
  await vi.advanceTimersByTimeAsync(185_000);
  await assertion;
});
it("aborts before creating a reservation", async () => {
  const controller = new AbortController();
  controller.abort();
  await expect(
    uploadFile(file(), "paper_pdf", "p", controller.signal, vi.fn()),
  ).rejects.toMatchObject({ name: "AbortError" });
  expect(fetch).not.toHaveBeenCalled();
});
it("provides a fallback for a rejection without a code", async () => {
  vi.mocked(fetch)
    .mockResolvedValueOnce(
      json({
        upload_id: "u",
        upload_method: "PUT",
        upload_url: "/s3",
        required_headers: {},
      }),
    )
    .mockResolvedValueOnce(new Response(""))
    .mockResolvedValueOnce(json({}))
    .mockResolvedValueOnce(json({ status: "rejected" }))
    .mockResolvedValueOnce(new Response(undefined, { status: 204 }));
  await expect(
    uploadFile(file(), "paper_pdf", "p", new AbortController().signal, vi.fn()),
  ).rejects.toThrow("неизвестная причина");
});
it("polls scanning until ready and removes the abort listener on each wait", async () => {
  vi.useFakeTimers();
  vi.mocked(fetch)
    .mockResolvedValueOnce(
      json({
        upload_id: "u",
        upload_method: "PUT",
        upload_url: "/s3",
        required_headers: {},
      }),
    )
    .mockResolvedValueOnce(new Response(""))
    .mockResolvedValueOnce(json({}))
    .mockResolvedValueOnce(json({ status: "scanning" }))
    .mockResolvedValueOnce(json({ status: "ready" }));
  const pending = uploadFile(
    file(),
    "paper_pdf",
    "p",
    new AbortController().signal,
    vi.fn(),
  );
  await vi.waitFor(() => expect(fetch).toHaveBeenCalledTimes(4));
  await vi.advanceTimersByTimeAsync(1500);
  expect(await pending).toMatchObject({ upload_id: "u" });
});
