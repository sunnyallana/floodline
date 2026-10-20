import type { PhotoMeta } from "./location";

/** Long edge of the uploaded photo. Enough for SAM 3 and the VLM (both resize below this), ~300-600 KB as JPEG. */
export const UPLOAD_EDGE_PX = 1600;
export const THUMB_EDGE_PX = 240;
const UPLOAD_QUALITY = 0.82;
const THUMB_QUALITY = 0.7;

export interface PreparedPhoto {
  photo: Blob;
  thumb: Blob;
  meta: PhotoMeta;
}

/** GPS and capture time from EXIF. Many browsers strip GPS from camera captures; then everything is null. */
export async function readMeta(file: File): Promise<PhotoMeta> {
  let lat: number | null = null;
  let lon: number | null = null;
  let accuracyM: number | null = null;
  let takenAt: number | null = null;
  try {
    const { default: exifr } = await import("exifr/dist/lite.esm.mjs");
    const gps = (await exifr.gps(file).catch(() => undefined)) as { latitude?: number; longitude?: number } | undefined;
    if (gps && typeof gps.latitude === "number" && typeof gps.longitude === "number") {
      lat = gps.latitude;
      lon = gps.longitude;
    }
    const tags = (await exifr.parse(file, ["DateTimeOriginal", "GPSHPositioningError"])) as Record<string, unknown> | undefined;
    if (tags) {
      if (typeof tags.GPSHPositioningError === "number") accuracyM = tags.GPSHPositioningError;
      if (tags.DateTimeOriginal instanceof Date && !Number.isNaN(tags.DateTimeOriginal.getTime())) {
        takenAt = tags.DateTimeOriginal.getTime();
      }
    }
  } catch {
    // Not a JPEG/HEIC with EXIF, or a truncated file: fall through to file metadata.
  }
  return { lat, lon, accuracyM, takenAt: takenAt ?? (file.lastModified || null) };
}

export function fitWithin(width: number, height: number, edge: number): { width: number; height: number } {
  const scale = Math.min(1, edge / Math.max(width, height));
  return { width: Math.max(1, Math.round(width * scale)), height: Math.max(1, Math.round(height * scale)) };
}

async function encode(bitmap: ImageBitmap, edge: number, quality: number): Promise<Blob> {
  const { width, height } = fitWithin(bitmap.width, bitmap.height, edge);
  if (typeof OffscreenCanvas !== "undefined") {
    const canvas = new OffscreenCanvas(width, height);
    const ctx = canvas.getContext("2d");
    if (!ctx) throw new Error("Canvas is not available");
    ctx.drawImage(bitmap, 0, 0, width, height);
    return canvas.convertToBlob({ type: "image/jpeg", quality });
  }
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("Canvas is not available");
  ctx.drawImage(bitmap, 0, 0, width, height);
  return new Promise((resolve, reject) =>
    canvas.toBlob((b) => (b ? resolve(b) : reject(new Error("Could not encode the photo"))), "image/jpeg", quality),
  );
}

/** Reads EXIF first (re-encoding drops it), then writes an upright, downscaled JPEG and a thumbnail. */
export async function preparePhoto(file: File): Promise<PreparedPhoto> {
  const meta = await readMeta(file);
  const bitmap = await createImageBitmap(file, { imageOrientation: "from-image" });
  try {
    const [photo, thumb] = await Promise.all([
      encode(bitmap, UPLOAD_EDGE_PX, UPLOAD_QUALITY),
      encode(bitmap, THUMB_EDGE_PX, THUMB_QUALITY),
    ]);
    return { photo, thumb, meta };
  } finally {
    bitmap.close();
  }
}
