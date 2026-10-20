import { describe as group, expect, it, vi } from "vitest";
import { UploadError, insertReport, toRow, uploadPhoto } from "../src/upload";
import { sampleReport } from "./fixtures";

const cfg = { supabaseUrl: "https://abc.supabase.co", anonKey: "anon-key" };
const respond = (status: number, body = "") => vi.fn(async () => new Response(body, { status }));

group("uploadPhoto", () => {
  it("posts the JPEG to the bucket under the report id, without upsert", async () => {
    const f = respond(200, "{}");
    await uploadPhoto(cfg, "11111111-2222-4333-8444-555555555555", new Blob(["x"], { type: "image/jpeg" }), f);
    const [url, init] = f.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("https://abc.supabase.co/storage/v1/object/field-photos/11111111-2222-4333-8444-555555555555.jpg");
    expect((init.headers as Record<string, string>)["x-upsert"]).toBe("false");
    expect((init.headers as Record<string, string>).Authorization).toBe("Bearer anon-key");
  });

  it("treats 'already exists' as success, because a lost reply means the first attempt worked", async () => {
    await expect(uploadPhoto(cfg, "id", new Blob(["x"]), respond(409))).resolves.toBeUndefined();
    const storageStyle = respond(400, '{"statusCode":"409","error":"Duplicate","message":"The resource already exists"}');
    await expect(uploadPhoto(cfg, "id", new Blob(["x"]), storageStyle)).resolves.toBeUndefined();
  });

  it("classifies failures as retryable or permanent", async () => {
    const offline = vi.fn(async () => {
      throw new TypeError("Failed to fetch");
    });
    await expect(uploadPhoto(cfg, "id", new Blob(["x"]), offline)).rejects.toMatchObject({ retryable: true });
    await expect(uploadPhoto(cfg, "id", new Blob(["x"]), respond(503))).rejects.toMatchObject({ retryable: true, status: 503 });
    await expect(uploadPhoto(cfg, "id", new Blob(["x"]), respond(429))).rejects.toMatchObject({ retryable: true });
    await expect(uploadPhoto(cfg, "id", new Blob(["x"]), respond(413, "too large"))).rejects.toMatchObject({ retryable: false });
  });
});

group("insertReport", () => {
  it("sends the row with return=minimal, since devices cannot read rows back", async () => {
    const f = respond(201);
    await insertReport(cfg, sampleReport(), 1234, f);
    const [url, init] = f.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("https://abc.supabase.co/rest/v1/reports");
    expect((init.headers as Record<string, string>).Prefer).toBe("return=minimal");
    expect(JSON.parse(init.body as string)).toEqual(toRow(sampleReport(), 1234));
  });

  it("accepts a duplicate key and rejects a refused team code permanently", async () => {
    await expect(insertReport(cfg, sampleReport(), 1, respond(409))).resolves.toBeUndefined();
    const refused = insertReport(cfg, sampleReport(), 1, respond(401, '{"message":"new row violates row-level security policy"}'));
    await expect(refused).rejects.toBeInstanceOf(UploadError);
    await expect(insertReport(cfg, sampleReport(), 1, respond(403))).rejects.toMatchObject({ retryable: false, message: /team code/ });
  });
});

group("toRow", () => {
  it("maps every field to its column and names the photo after the id", () => {
    const row = toRow(sampleReport(), 900);
    expect(row).toMatchObject({
      id: sampleReport().id,
      team_code: "TEAM-2026-01",
      location_source: "device",
      photo_path: `${sampleReport().id}.jpg`,
      photo_bytes: 900,
      consent: true,
    });
    expect(Object.keys(row)).toHaveLength(16);
  });
});
